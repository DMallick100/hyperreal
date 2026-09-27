"""Which of a vendor's ids can this gateway key actually reach? A refused call is free.

THE CATALOGUE SAYS WHAT THE GATEWAY SELLS, NOT WHAT THIS KEY MAY BUY, and the two
are different measurements. `provider_preflight.py --no-spend` reads the catalogue
with its prices; this file asks the entitlement question the catalogue cannot
answer. Written 2026-09-27 after the grok and qwen arms: both vendors' newest
flagship ids answer **403 `no_providers_available`** on this free-tier account while
older ids in the same family answer 200, so an id picked off the catalogue by price
or version is a smoke test away from being unusable.

It exists as a repo file rather than a scratch script because the bridge arm had to
write this sweep ad hoc once already
(`docs/results-2026-09-27-bridge-arm-blocked.md`), and because a doc whose
reproduction route is untracked scratch has put its method somewhere that gets
reaped.

**A REFUSED REQUEST BILLS NOTHING**, so the expensive half of this sweep is the ids
that work: one 16-token completion each. The grok+qwen sweep cost **$0.004526** for
41 ids. There is no excuse for guessing something this cheap to measure.

TEXT-ONLY, AND THE EXCLUSION IS PRINTED. Image, video, audio, tts/stt, voice,
vision and embedding ids are recorded `out_of_scope` **with no call made** — they
are not candidates for a Bash-tool arm, and a 400 from one of them would read as an
entitlement answer it is not (the "not installed / installed and quiet / broken"
confusion `docs/gates.md` has a rule about).

NOTHING BRANCHES ON THE PROVIDER'S PROSE. The status and the machine-readable
`error.type` are what get recorded as fields; the message is kept beside them as
**evidence**, because this gateway has already been measured answering `429
rate_limit_exceeded` over a message that said *no access*. Classifier input and
evidence are two jobs for the same bytes (`CLAUDE.md` 8.A).

    python3 measurements/gateway_access_sweep.py \
        --catalogue results/provider-preflight-2026-09-27-grok-qwen.json \
        --needle grok --needle qwen --out results/grok-qwen-access-sweep.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from measurements.provider_preflight import GATEWAY_BASE, _gateway_key  # noqa: E402
from measurements.shim_providers import SSL_CONTEXT, SSL_TRUST_STORE  # noqa: E402

# Substrings that mark a NON-text id. Matched on the id so the exclusion is visible
# in the output rather than being a judgement made off-screen.
NON_TEXT = ("imagine", "-tts", "-stt", "voice", "embedding", "-vl-", "omni",
            "image", "video", "audio", "rerank", "moderation")
PACE_SECONDS = 3.0
PROBE_MAX_TOKENS = 16
TIMEOUT_SECONDS = 120


def probe(model: str, key: str | None, base: str) -> dict:
    """One minimal completion. A 403 costs $0; a 200 costs a fraction of a cent."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "max_completion_tokens": PROBE_MAX_TOKENS,
    }).encode()
    request = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS,
                                    context=SSL_CONTEXT) as response:
            payload = json.loads(response.read().decode())
        usage = payload.get("usage") or {}
        return {
            "model": model,
            "status": response.status,
            "reachable": True,
            # The id the RESPONSE reports, never the one we asked for (spec N3 rule 1).
            "model_reported": payload.get("model"),
            "cost_usd": usage.get("cost"),
        }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            error = json.loads(raw).get("error") or {}
        except Exception:  # noqa: BLE001 - an undecodable body is still evidence
            error = {}
        return {
            "model": model,
            "status": exc.code,
            "reachable": False,
            "error_type": error.get("type"),
            "error_message": (error.get("message") or raw)[:200],
        }
    except Exception as exc:  # noqa: BLE001 - a transport failure is a finding too
        # `reachable: None` is neither yes nor no. A transport failure is OURS and
        # must not be counted as the provider refusing (spec N5.1 rung 1).
        return {"model": model, "status": None, "reachable": None,
                "error_type": type(exc).__name__, "error_message": str(exc)[:200]}


def ids_for(catalogue_path: str, needles: list[str]) -> list[str]:
    with open(catalogue_path) as handle:
        catalogue = json.load(handle)["catalogue"]
    found: list[str] = []
    for needle in needles:
        hits = catalogue["matching"].get(needle)
        if hits is None:
            raise SystemExit(
                f"needle {needle!r} is not in {catalogue_path}. Add it to "
                "provider_preflight's needle tuple and re-run `--no-spend` (free); "
                "do NOT write an id from memory (E3)."
            )
        for hit in hits:
            if hit["id"] not in found:
                found.append(hit["id"])
    return sorted(found)


def sweep(catalogue_path: str, needles: list[str], base: str, key: str | None,
          pace: float = PACE_SECONDS, verbose: bool = True) -> dict:
    ids = ids_for(catalogue_path, needles)
    rows, spend = [], 0.0
    for model in ids:
        marker = next((m for m in NON_TEXT if m in model), None)
        if marker:
            rows.append({"model": model, "status": None, "reachable": None,
                         "skipped": f"out_of_scope (matched {marker!r}: not a text "
                                    "completion id; no call made)"})
            continue
        row = probe(model, key, base)
        if row.get("cost_usd"):
            spend += float(row["cost_usd"])
        rows.append(row)
        if verbose:
            print("%-45s %s %s" % (model, row["status"], row.get("error_type") or ""))
        time.sleep(pace)
    return {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "what": "per-id entitlement sweep: which catalogue ids this key may actually buy",
        "needles": needles,
        "base": base,
        "trust_store": SSL_TRUST_STORE,
        "interpreter": sys.executable,
        "catalogue": catalogue_path,
        "non_text_markers": list(NON_TEXT),
        "pace_seconds": pace,
        "probe_max_tokens": PROBE_MAX_TOKENS,
        "ids_in_catalogue": len(ids),
        "probed": sum(1 for r in rows if "skipped" not in r),
        "reachable": sum(1 for r in rows if r.get("reachable") is True),
        "refused": sum(1 for r in rows if r.get("reachable") is False),
        # `unmeasured` is its own count: a socket failure is not a refusal, and
        # rounding it into one would put a harness error in a denominator.
        "unmeasured": sum(1 for r in rows
                          if r.get("reachable") is None and "skipped" not in r),
        "out_of_scope": sum(1 for r in rows if "skipped" in r),
        "spend_usd": round(spend, 6),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalogue", required=True,
                        help="a stored provider_preflight output")
    parser.add_argument("--needle", action="append", default=[], required=True,
                        help="a needle the catalogue was grepped for. Repeatable.")
    parser.add_argument("--out", default="", help="write the findings to this JSON path")
    parser.add_argument("--pace", type=float, default=PACE_SECONDS)
    args = parser.parse_args()

    findings = sweep(
        os.path.join(REPO, args.catalogue) if not os.path.isabs(args.catalogue)
        else args.catalogue,
        args.needle, GATEWAY_BASE, _gateway_key(), pace=args.pace,
    )
    print(json.dumps({k: v for k, v in findings.items() if k != "rows"}, indent=1))
    if args.out:
        path = args.out if os.path.isabs(args.out) else os.path.join(REPO, args.out)
        with open(path, "w") as handle:
            json.dump(findings, handle, indent=1)
        print("wrote", path, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
