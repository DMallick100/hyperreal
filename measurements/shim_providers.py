"""One wire format, normalised into facts. No classification lives in this file.

WHY ONLY ONE ADAPTER (spec N4.1). Both legal hosted providers - the Vercel AI
Gateway and OpenRouter - speak the OpenAI chat-completions shape, so one function
serves either and what changes is the base URL, the auth header and the model-id
namespace. `chat_ollama(...)` is deliberately **not built**: the operator excluded
local serving on 2026-09-26 (N0.1), so there is no second wire format to
normalise, and an unused adapter is an invitation to the excluded arm. That is the
same reasoning that deleted `probe_ollama()` from `provider_preflight.py` rather
than putting it behind a flag.

WHAT "NO CLASSIFICATION" MEANS HERE, AND WHY IT IS STRICT. This file may say *what
came back* - the HTTP status, the machine-readable error code, the finish reason,
whether a tool call's arguments parsed. It may never say what that MEANS. A 429, a
content-policy refusal and a model politely declining all arrive as "no tool
call", and deciding between them is the N5 ladder's job in
`live_shim_probe.py`, where it is pinned by tests. A normaliser that also
classified would put that decision in two places, and the two would drift.

WHAT IT READS AND WHAT IT REFUSES TO READ. Error classification downstream keys on
the `code`/`type` FIELDS of the provider's error object, never on the human
`message` string - `CLAUDE.md` 8.A: detect state structurally, never by
substring-matching prose. Reword a vendor's error text and a prose-matching check
reports a clean run.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hyperreal.trust import ssl_context  # noqa: E402

SSL_CONTEXT, SSL_TRUST_STORE = ssl_context()

GATEWAY_BASE = "https://ai-gateway.vercel.sh/v1"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_HTTP_TIMEOUT = 120

# The closed set of `error_stage` values this module can produce. The rest of the
# vocabulary (`gate_invocation`, `fixture_rebuild`, `executor_spawn`, `turn_limit`)
# belongs to the host, which is where those failures happen. Closed so a failure is
# counted by cause rather than read as prose (spec N5.1).
PROVIDER_HTTP = "provider_http"
PROVIDER_TIMEOUT = "provider_timeout"
RESPONSE_DECODE = "response_decode"
TOOL_ARG_PARSE = "tool_arg_parse"

# The machine-readable codes a provider uses to say "a platform safeguard above
# the model refused this", read off the error object's `code`/`type` field. Closed
# and PARTIAL, and the partiality is the point: an unrecognised code becomes a
# loud `harness_error/unknown` downstream rather than being quietly filed as a
# model refusal (spec N5.1, last rule).
POLICY_ERROR_CODES = frozenset(
    {
        "content_policy_violation",
        "content_filter",
        "content_policy",
        "invalid_prompt",
        "moderation",
        "policy_violation",
    }
)

# `finish_reason` values that mean the same thing one level up in the response.
POLICY_FINISH_REASONS = frozenset({"content_filter", "content_policy"})


@dataclass(frozen=True)
class ToolCallProposal:
    """One tool call the model proposed, with its arguments parsed or NOT.

    `parse_error` carries `tool_arg_parse` when `arguments` was not valid JSON or
    named no `command`. It is recorded rather than raised because a malformed
    tool call is a row about this case, not a reason to abandon the arm - and
    because the ladder, not this file, decides what it means.
    """

    call_id: str
    name: str
    arguments_raw: str
    command: str = ""
    parse_error: str = ""


@dataclass(frozen=True)
class ProviderReply:
    """What came back from one chat completion. Facts only.

    `raw` is kept whole so a published row can be rechecked by someone who does
    not trust this normaliser - the same decomposability rule every other row in
    this repo carries (`docs/architecture.md` S7).
    """

    status: int
    tool_calls: tuple[ToolCallProposal, ...] = ()
    text: str = ""
    finish: str = ""
    usage: Mapping[str, Any] = field(default_factory=dict)
    raw: Any = None
    # "" when the transport and the body were both fine. Otherwise one of this
    # module's four `error_stage` values.
    transport_error: str = ""
    transport_detail: str = ""
    # The id the RESPONSE reports, never the id we asked for (spec N3 rule 1: the
    # alias is not the model). Empty when the provider did not say.
    model_reported: str = ""
    # Where the provider says it routed. See `upstream_of` - `unknown` is the
    # honest and expected value, and it is never collapsed into `served_by`.
    upstream: str = "unknown"
    # The provider's machine-readable error code, for the ladder's rung 2. Never
    # the human message.
    error_code: str = ""
    wall_seconds: float = 0.0

    @property
    def parse_errors(self) -> tuple[str, ...]:
        return tuple(call.parse_error for call in self.tool_calls if call.parse_error)


# The field a provider might name its upstream in. UNVERIFIED against either
# provider's documentation - nothing in this repo has measured that either of them
# reports it at all, so `unknown` is the value to expect and the one a row will
# print. Recorded this way rather than guessed at because where the corpus text
# (destructive commands, injection payloads) physically went is a claim about
# somebody else's infrastructure, and spec N3 rule 2 says we do not make it.
UPSTREAM_FIELDS = ("provider", "upstream", "served_by")


def upstream_of(payload: Any) -> str:
    """Which upstream the provider says it routed to, or the string `unknown`."""
    if not isinstance(payload, Mapping):
        return "unknown"
    for name in UPSTREAM_FIELDS:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _error_code_of(payload: Any) -> str:
    """The provider's machine-readable error code. The `message` is never read."""
    if not isinstance(payload, Mapping):
        return ""
    error = payload.get("error")
    if isinstance(error, Mapping):
        for name in ("code", "type"):
            value = error.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    for name in ("code", "type"):
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _proposals(message: Mapping[str, Any]) -> tuple[ToolCallProposal, ...]:
    """Normalise `tool_calls`, recording a parse failure instead of raising."""
    found = []
    for index, call in enumerate(message.get("tool_calls") or []):
        if not isinstance(call, Mapping):
            found.append(
                ToolCallProposal(
                    call_id=f"shim-malformed-{index}",
                    name="",
                    arguments_raw=repr(call)[:400],
                    parse_error=TOOL_ARG_PARSE,
                )
            )
            continue
        function = call.get("function") if isinstance(call.get("function"), Mapping) else {}
        raw = function.get("arguments")
        raw_text = raw if isinstance(raw, str) else json.dumps(raw) if raw is not None else ""
        call_id = str(call.get("id") or f"shim-call-{index}")
        name = str(function.get("name") or "")
        try:
            parsed = json.loads(raw_text) if raw_text else None
        except (ValueError, TypeError):
            found.append(
                ToolCallProposal(
                    call_id=call_id,
                    name=name,
                    arguments_raw=raw_text[:2000],
                    parse_error=TOOL_ARG_PARSE,
                )
            )
            continue
        command = parsed.get("command") if isinstance(parsed, Mapping) else None
        if not isinstance(command, str) or not command.strip():
            # A tool call with no `command` is not a command the gate can be shown.
            # It is OUR failure to get a usable proposal, not the model declining.
            found.append(
                ToolCallProposal(
                    call_id=call_id,
                    name=name,
                    arguments_raw=raw_text[:2000],
                    parse_error=TOOL_ARG_PARSE,
                )
            )
            continue
        found.append(
            ToolCallProposal(
                call_id=call_id, name=name, arguments_raw=raw_text[:2000], command=command
            )
        )
    return tuple(found)


def chat_openai_shaped(
    *,
    base: str,
    key: str,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    timeout: int = DEFAULT_HTTP_TIMEOUT,
    max_completion_tokens: int = 1024,
) -> ProviderReply:
    """One chat completion against an OpenAI-shaped hosted provider. THIS BILLS.

    Every failure path returns a `ProviderReply` carrying a `transport_error` from
    this module's closed set. Nothing here raises for a provider-side problem: an
    exception would abort the arm, and one case can never be allowed to end a run
    (the same fail-open-at-the-loop rule the Jev triage loop learned).
    """
    body = json.dumps(
        {
            "model": model,
            "messages": list(messages),
            "tools": list(tools),
            "tool_choice": "auto",
            "max_completion_tokens": max_completion_tokens,
        }
    ).encode()
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
            raw_text = response.read().decode(errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw_text = exc.read().decode(errors="replace")
        status = exc.code
    except (socket.timeout, TimeoutError) as exc:
        return ProviderReply(
            status=0,
            transport_error=PROVIDER_TIMEOUT,
            transport_detail=f"{type(exc).__name__}: {exc}",
            wall_seconds=round(time.time() - started, 2),
        )
    except (urllib.error.URLError, OSError) as exc:
        # A URLError wrapping a timeout is a timeout; anything else is transport.
        reason = getattr(exc, "reason", None)
        timed_out = isinstance(reason, (socket.timeout, TimeoutError)) or isinstance(
            exc, (socket.timeout, TimeoutError)
        )
        return ProviderReply(
            status=0,
            transport_error=PROVIDER_TIMEOUT if timed_out else PROVIDER_HTTP,
            transport_detail=f"{type(exc).__name__}: {exc}",
            wall_seconds=round(time.time() - started, 2),
        )
    elapsed = round(time.time() - started, 2)

    try:
        payload = json.loads(raw_text)
    except (ValueError, TypeError):
        return ProviderReply(
            status=status,
            transport_error=RESPONSE_DECODE,
            transport_detail=f"undecodable body ({len(raw_text)} bytes): {raw_text[:300]}",
            raw=raw_text[:4000],
            wall_seconds=elapsed,
        )

    if status != 200 or not isinstance(payload, Mapping):
        # NOT classified here. A 400 carrying a policy code and a 429 are both
        # "non-200 with a code attached"; which of them is a platform refusal and
        # which is our problem is the ladder's call.
        return ProviderReply(
            status=status,
            transport_error=PROVIDER_HTTP,
            transport_detail=f"http {status}",
            raw=payload if isinstance(payload, Mapping) else str(payload)[:4000],
            error_code=_error_code_of(payload),
            upstream=upstream_of(payload),
            wall_seconds=elapsed,
        )

    choice = (payload.get("choices") or [{}])[0]
    choice = choice if isinstance(choice, Mapping) else {}
    message = choice.get("message") if isinstance(choice.get("message"), Mapping) else {}
    return ProviderReply(
        status=status,
        tool_calls=_proposals(message),
        text=str(message.get("content") or ""),
        finish=str(choice.get("finish_reason") or ""),
        usage=payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {},
        raw=payload,
        model_reported=str(payload.get("model") or ""),
        upstream=upstream_of(payload),
        # An Anthropic-shaped `stop_reason: "refusal"` can arrive through a
        # gateway on a 200. M5 caught one of these wearing `subtype: "success"`,
        # so it is read off the body rather than trusted to show up as a status.
        error_code=str(payload.get("stop_reason") or "").strip().lower(),
        wall_seconds=elapsed,
    )


# -- pricing -----------------------------------------------------------------
#
# A ceiling nobody can compute is a ceiling nobody sets (spec N7). Prices come
# from the STORED catalogue output, never from memory: `CLAUDE.md` 8.A E3 -
# reference data is transcribed from a source. That is also why this refuses a
# missing id rather than defaulting to zero; a cost of $0.00 on a billed call is
# a spend guard that reports nothing.

# Repointed 2026-09-26 (eighth delivery) from `provider-preflight-2026-09-26.json`
# to the `-26b` re-run, which is the first catalogue carrying the `anthropic`
# needle and therefore the first that can price the N2.3 BRIDGE arm. Verified a
# strict superset before repointing, because a price that moved underneath a
# published id would rewrite a ceiling silently: 136 ids vs 118, **zero** price
# disagreements, **zero** ids dropped, and `openai/gpt-5` unchanged at
# $1.25/$10 per Mtok. Re-run the check before repointing this again - "the newer
# file is a superset" is an assertion, not a property of being newer.
#
# ANCHORED to the repo root, not joined against the process cwd. Measured
# 2026-09-26: as a bare relative path this raised FileNotFoundError the moment it
# was imported from anywhere but `~/hyperreal` - the same defect `live_shim_sweep`
# already anchors RESULTS against and that `live_model_state_check.py` is on
# record for. On the PRICING path it is the worse one to leave: every arm's spend
# ceiling comes through here, and a ceiling that depends on which directory you
# typed the command in is not a ceiling.
PINNED_CATALOGUE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "results",
    "provider-preflight-2026-09-26b.json",
)


def load_prices(catalogue_path: str = PINNED_CATALOGUE) -> dict[str, dict[str, float]]:
    """Per-Mtok input/output prices, keyed by the id the catalogue published.

    Reads the file `provider_preflight.py --no-spend` wrote. A model id absent
    from it has no price here, and the caller must refuse to run that arm rather
    than bill against an unknown number.
    """
    with open(catalogue_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    matching = ((payload.get("catalogue") or {}).get("matching")) or {}
    prices: dict[str, dict[str, float]] = {}
    for hits in matching.values():
        if not isinstance(hits, list):
            continue
        for hit in hits:
            if not isinstance(hit, Mapping):
                continue
            pricing = hit.get("pricing")
            if not isinstance(pricing, Mapping):
                continue
            try:
                prices[str(hit.get("id"))] = {
                    "input": float(pricing["input"]),
                    "output": float(pricing["output"]),
                }
            except (KeyError, TypeError, ValueError):
                continue
    return prices


def cost_of(usage: Mapping[str, Any], price: Mapping[str, float]) -> float:
    """Dollars for one call, from the provider's own token counts and prices.

    The catalogue quotes dollars per TOKEN, not per Mtok - `openai/gpt-5` is
    `"input": "0.00000125"`, i.e. $1.25/Mtok. Multiplying by a million here would
    overstate a ceiling by 10^6 and stop the arm on its first case, which is the
    kind of arithmetic a spend guard cannot afford to get wrong quietly.
    """
    prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    try:
        return float(prompt) * price["input"] + float(completion) * price["output"]
    except (KeyError, TypeError, ValueError):
        return 0.0
