"""Which non-Anthropic models can actually answer a TOOL CALL on this machine?

WHY THIS FILE EXISTS. `docs/live-models-2026-09-25.md` measured the live protocol
at three Anthropic tiers and its headline - `gate_held` is 0 in all six arms - is
now a statement about one vendor's models. Extending it past that vendor needs a
different host (Claude Code runs Anthropic models only), and before any host is
written it needs an answer to a cheaper question: what is even reachable from
here, and does it do tool calling at all?

MEASURED, NEVER ASSUMED. A provider is reported `ready` only after a real request
came back with a real tool call. The repo has a standing rule about this
(`CLAUDE.md` 8.A: advisories are RUN, never assumed; a count of zero is not a
measurement) and it applies to our own credentials: an absent key and an unspent
key produce the same silence in a config file and completely different arms.

THIS FILE NEVER PRINTS A KEY. Presence, length and a 4-character prefix only -
enough to tell "no key" from "wrong key", not enough to be a leak. It also makes
NO call that bills more than a two-token reply, and it says which calls bill at
all, because a preflight that quietly spends is worse than one that refuses to.

THE PROBE COMMAND IS INERT. `echo hyperreal-preflight` - not a corpus case. This
file is about reachability, and pointing a reachability check at a destructive
case would execute one for no measurement.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hyperreal.trust import ssl_context  # noqa: E402

# The one tool every arm must expose, in the shape both wire formats agree on.
# Deliberately identical in name and schema to what the live host gives a model,
# so a refusal here is about the model and not about an unfamiliar tool.
BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "Bash",
        "description": "Run a shell command and return its output.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "the command to run"}},
            "required": ["command"],
        },
    },
}
PROBE_PROMPT = "Run this exact shell command with the Bash tool, verbatim, and then stop.\n\necho hyperreal-preflight"

GATEWAY_BASE = "https://ai-gateway.vercel.sh/v1"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
HTTP_TIMEOUT = 120

# The usual places an OpenRouter key is configured. Checked for EXISTENCE only -
# never opened, never printed. "A credential is configured here" and "here is the
# credential" are different sentences and only the first belongs in a transcript.
OPENROUTER_CONFIG_PATHS = (
    "~/.openrouter",
    "~/.openrouter.json",
    "~/.or_key",
    "~/.config/openrouter",
    "~/.config/openrouter/config.json",
)


# MOVED 2026-09-26 to `hyperreal/trust.py`, unchanged in behaviour. It lived here
# as a private helper, and then `measurements/shim_providers.py` needed the same
# fix - which is the moment a per-file paragraph becomes a per-file defect. One
# helper in the package, imported by every HTTPS caller: the class fixed in the
# shared place rather than at a second call site (`CLAUDE.md` 8.0 #2). The
# re-exported names are kept so nothing downstream had to change.
SSL_CONTEXT, SSL_TRUST_STORE = ssl_context()


def _redacted(value: str | None) -> str:
    if not value:
        return "ABSENT"
    return f"present len={len(value)} prefix={value[:4]!r}"


def _gateway_key() -> str | None:
    """The gateway key, from the environment or from the CLI's own config file.

    A texted session does not source `~/.zshrc`, so the key is exported for an
    interactive shell and absent here. Reading `~/.vai/config.json` is how the
    `vai` CLI finds it too, so this is the same key by the same route - not a
    second secret.
    """
    for name in ("AI_GATEWAY_API_KEY", "VERCEL_AI_GATEWAY_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    config = pathlib.Path.home() / ".vai" / "config.json"
    if config.exists():
        try:
            payload = json.loads(config.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        for key in ("apiKey", "api_key", "AI_GATEWAY_API_KEY", "key", "token"):
            if isinstance(payload.get(key), str) and payload[key]:
                return payload[key]
    return None


def _post(url: str, payload: dict, headers: dict) -> tuple[int, dict | str]:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json", **headers}
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT, context=SSL_CONTEXT) as response:
            raw = response.read().decode(errors="replace")
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw[:600]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")[:600]
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def _get(url: str, headers: dict) -> tuple[int, dict | str]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT, context=SSL_CONTEXT) as response:
            raw = response.read().decode(errors="replace")
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw[:600]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")[:600]
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def local_out_of_scope() -> dict:
    """Local serving is not an arm. This row is a SCOPE fact, not a capability one.

    The local probe this replaces made a real `/api/chat` call and reported
    `ready: true|false`. Both answers are now misleading: the operator excluded
    local models on 2026-09-26 (N0.1), so a `ready` row proposes the one arm that
    may not run and a `ready: false` row is a zero against a component of no arm -
    and `CLAUDE.md` 8.A is explicit that a count of zero is not a measurement.

    So: no call is made, no `ready` key is emitted, and the constraint is named in
    the row. An excluded path re-opens through a helper nobody deleted.
    """
    return {
        "provider": "ollama",
        "status": "out_of_scope",
        "why": "operator constraint 2026-09-26 (spec N0.1): no local models on this "
        "machine; open-weight arms go through hosted APIs only. Not a capability "
        "result and not a zero - local serving is a component of no arm.",
        "installed": bool(shutil.which("ollama")),
    }


def probe_gateway(model: str, provider: str, base: str, key: str | None) -> dict:
    """An OpenAI-shaped chat completion through the selected hosted provider. THIS BILLS.

    Both legal providers speak the same wire format, so this one function serves
    either; what changes is the base URL, the auth header and the id namespace. The
    provider is recorded on the row because an id pinned against one catalogue is
    not portable to the other (spec N0.2 rule 3).
    """
    if not key:
        return {"provider": provider, "model": model, "ready": False, "why": f"no {provider} key"}
    started = time.time()
    status, payload = _post(
        f"{base}/chat/completions",
        {
            "model": model,
            "messages": [{"role": "user", "content": PROBE_PROMPT}],
            "tools": [BASH_TOOL],
            "max_completion_tokens": 512,
        },
        {"Authorization": f"Bearer {key}"},
    )
    elapsed = round(time.time() - started, 1)
    if status != 200 or not isinstance(payload, dict):
        return {
            "provider": provider,
            "model": model,
            "ready": False,
            "why": f"http {status}: {str(payload)[:300]}",
            "seconds": elapsed,
            "bills": True,
        }
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    calls = message.get("tool_calls") or []
    return {
        "provider": provider,
        "model": model,
        "model_reported": payload.get("model"),
        "ready": bool(calls),
        "why": "tool_call returned" if calls else f"finish_reason={choice.get('finish_reason')}, no tool call",
        "tool_calls": [(c.get("function") or {}).get("arguments") for c in calls],
        "text_head": str(message.get("content") or "")[:200],
        "usage": payload.get("usage"),
        "seconds": elapsed,
        "bills": True,
    }


def catalogue(provider: str, base: str, key: str | None, needles: tuple[str, ...]) -> dict:
    """What the selected provider will serve. A free GET, so it runs under --no-spend.

    This is the ONLY place an arm's model id may come from (spec N1.1, `CLAUDE.md`
    8.A E3: reference data is transcribed from a source, never recalled). It also
    carries each match's PRICING when the provider publishes it, because a ceiling
    nobody can compute is a ceiling nobody sets.
    """
    if not key:
        return {"provider": provider, "error": f"no {provider} key"}
    status, payload = _get(f"{base}/models", {"Authorization": f"Bearer {key}"})
    if status != 200 or not isinstance(payload, dict):
        return {"provider": provider, "error": f"http {status}: {str(payload)[:300]}"}
    entries = payload.get("data") or []
    by_id = {str(item.get("id")): item for item in entries}
    matching = {}
    for needle in needles:
        hits = sorted(i for i in by_id if needle in i.lower())
        matching[needle] = [
            {"id": i, "pricing": by_id[i].get("pricing")} for i in hits
        ] or "NO MATCH"
    return {
        "provider": provider,
        "base": base,
        "count": len(by_id),
        "matching": matching,
    }


def select_provider() -> dict:
    """Which hosted provider this RUN uses, decided now rather than in a document.

    OpenRouter is the operator's stated preference (spec N0.2) and was unreachable
    on 2026-09-26 for want of a credential. An absence measured on one day is not a
    property of the machine, so the choice is re-measured at run time: the moment an
    `OPENROUTER_API_KEY` exists, OpenRouter is the provider and the gateway is the
    fallback. A model id pinned from one catalogue is not portable to the other, so
    switching provider re-pins every id.

    Existence only. No config file is opened for OpenRouter and no key is printed.
    """
    configured = [p for p in OPENROUTER_CONFIG_PATHS if pathlib.Path(p).expanduser().exists()]
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_key:
        return {
            "selected": "openrouter",
            "base": OPENROUTER_BASE,
            "why": "OPENROUTER_API_KEY present in the environment; it is the "
            "operator's preferred provider (N0.2)",
            "openrouter_config_paths_present": configured,
        }
    return {
        "selected": "gateway",
        "base": GATEWAY_BASE,
        "why": "OPENROUTER_API_KEY absent from the environment, so the preferred "
        "provider (N0.2) is unreachable and the Vercel AI Gateway is used because "
        "it is the only hosted provider with a credential here - NOT because it won "
        "a comparison.",
        "openrouter_config_paths_present": configured or "none of the five checked",
    }


def cli_versions() -> dict:
    """The model CLIs installed here, and whether each answers `--version` at all."""
    found = {}
    for name in ("ollama", "vai", "openai", "gemini", "zai", "minimax", "claude"):
        path = shutil.which(name)
        if not path:
            found[name] = "NOT INSTALLED"
            continue
        try:
            done = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=30
            )
            found[name] = (done.stdout or done.stderr or "").strip().splitlines()[:1] or ["(silent)"]
        except Exception as exc:  # noqa: BLE001
            found[name] = f"error: {type(exc).__name__}"
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--no-spend",
        action="store_true",
        help="run only what bills nothing: the free model catalogue, the provider "
        "selection and the CLI inventory. No corpus text is sent on this path.",
    )
    parser.add_argument(
        "--gateway-model",
        action="append",
        default=[],
        help="a model id, from the SELECTED provider's catalogue, to probe. "
        "Repeatable. Each probe BILLS.",
    )
    parser.add_argument("--out", default="", help="write the findings to this JSON path")
    args = parser.parse_args()

    key = _gateway_key()
    provider = select_provider()
    catalogue_key = (
        os.environ.get("OPENROUTER_API_KEY") if provider["selected"] == "openrouter" else key
    )
    findings = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "trust_store": SSL_TRUST_STORE,
        "provider": provider,
        "keys": {
            "AI_GATEWAY_API_KEY (env)": _redacted(os.environ.get("AI_GATEWAY_API_KEY")),
            "AI_GATEWAY_API_KEY (resolved incl. ~/.vai/config.json)": _redacted(key),
            "OPENAI_API_KEY": _redacted(os.environ.get("OPENAI_API_KEY")),
            "OPENROUTER_API_KEY": _redacted(os.environ.get("OPENROUTER_API_KEY")),
            "TYPESAFE_API_KEY": _redacted(os.environ.get("TYPESAFE_API_KEY")),
        },
        "clis": cli_versions(),
        # `llama` is struck: the operator excluded it 2026-09-26 (N0.1), and a
        # preflight that still greps for it proposes a model that may not be used.
        # `gpt-oss` is added: the spec names it the strongest open-weight US-origin
        # candidate and says the preflight already greps for it - it did not, because
        # the needle `gpt-5` does not match `gpt-oss-*`. `mistral` stays as a
        # catalogue probe only; Mistral is French and cannot serve an arm labelled US.
        # `anthropic` added 2026-09-26 (later still): spec N2.3's BRIDGE arm runs the
        # cheapest Anthropic model through the shim, and it is a RELEASE GATE for the
        # other three - but the seven needles above cannot match an Anthropic id, so
        # the bridge arm's model could only have been written from memory (E3). The
        # 2026-09-26 stored catalogue predates this needle, so pinning the bridge id
        # needs one more FREE `--no-spend` run.
        "catalogue": catalogue(
            provider["selected"],
            provider["base"],
            catalogue_key,
            ("gpt-5", "gpt-oss", "mistral", "qwen", "deepseek", "kimi", "glm", "anthropic"),
        ),
        "probes": [local_out_of_scope()],
    }
    if not args.no_spend:
        for model in args.gateway_model:
            findings["probes"].append(
                probe_gateway(model, provider["selected"], provider["base"], catalogue_key)
            )
    else:
        findings["skipped"] = (
            f"chat probes against {provider['selected']} (--no-spend); they bill"
        )

    print(json.dumps(findings, indent=1))
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(findings, indent=1))
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
