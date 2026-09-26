"""Capture the REAL host's PreToolUse envelope, once, and store it as a fixture.

Spec N8 gate 3 (`docs/non-anthropic-arms-spec.md`) and
`measurements/fixtures/README.md`. `live_shim_host.load_host_envelope()` refuses
to run any arm until `measurements/fixtures/host_pretooluse_envelope.json`
exists, and refuses to substitute a handwritten one - so this is the one ~2c
`claude -p` session that unblocks all four arms.

METHOD. Launch one `claude -p` in a throwaway /tmp workspace with a benign
command, `--include-hook-events`, and the launcher env scrubbed exactly as
`live_session_probe.SCRUBBED_ENV` scrubs it. Every ecc PreToolUse hook behind
`run-with-flags.js` echoes its stdin to stdout, so the envelope the host handed
its hooks is IN the event stream. Find it STRUCTURALLY - by the envelope keys
only the host can write (`live_session_probe._is_echo`'s test) - never by
matching prose, and never by reconstructing it from the flags we passed.

WHAT IS STORED. The envelope verbatim, including any field this repo has not
thought about. `live_shim_host.envelope_for()` templates exactly five fields and
carries everything else through; `tests/test_shim_host.py` asserts both halves.

WHAT THIS DOES NOT SETTLE. Which hook wins when two answer. That stays
`combination_rule: "first_deny_wins (shim assumption, unverified)"` until the
bridge arm (spec N2.3) measures it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from live_session_probe import SCRUBBED_ENV, _child_env, _events, _is_echo  # noqa: E402
from live_shim_host import HOST_ENVELOPE_FIXTURE, TEMPLATED_ENVELOPE_FIELDS  # noqa: E402

PROMPT = (
    "Run this exact shell command with the Bash tool, verbatim, and then stop.\n\n"
    "echo hyperreal-envelope-capture"
)
# The capture must not touch the corpus fixture: this is a benign echo in a
# directory of its own, so a capture run can never be confused with an arm.
WORKSPACE = "/tmp/hyperreal-envelope-capture"
TIMEOUT_SECONDS = 180


def _argv(session: str, model: str, budget: str) -> list[str]:
    return [
        "claude",
        "-p",
        PROMPT,
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-hook-events",
        "--tools",
        "Bash",
        "--allowedTools",
        "Bash",
        "--strict-mcp-config",
        "--max-budget-usd",
        budget,
        "--permission-mode",
        "acceptEdits",
        "--session-id",
        session,
    ]


def _envelopes(events: list[dict]) -> list[dict]:
    """Every PreToolUse envelope echoed back by a hook, in stream order."""
    found = []
    for event in events:
        if event.get("type") != "system" or event.get("subtype") != "hook_response":
            continue
        if event.get("hook_event") != "PreToolUse":
            continue
        for channel in ("stdout", "stderr"):
            text = (event.get(channel) or "").strip()
            if not _is_echo(text):
                continue
            payload = json.loads(text)
            if payload.get("hook_event_name") != "PreToolUse":
                continue
            found.append({"hook_id": event.get("hook_id"), "channel": channel, "envelope": payload})
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="haiku", help="cheapest tier; the envelope is the host's")
    parser.add_argument("--budget", default="0.20")
    parser.add_argument("--out", default=HOST_ENVELOPE_FIXTURE)
    parser.add_argument("--force", action="store_true", help="overwrite an existing fixture")
    parser.add_argument("--raw", default="", help="also write the full event stream here")
    args = parser.parse_args()

    if os.path.exists(args.out) and not args.force:
        raise SystemExit(f"{args.out} already exists; pass --force to recapture")

    if os.path.isdir(WORKSPACE):
        shutil.rmtree(WORKSPACE)
    os.makedirs(WORKSPACE, exist_ok=True)

    env, scrubbed = _child_env(True)
    session = str(uuid.uuid4())
    argv = _argv(session, args.model, args.budget)
    print(f"launching claude -p in {WORKSPACE}", file=sys.stderr)
    print(f"scrubbed from the child env: {scrubbed or 'nothing'} (of {list(SCRUBBED_ENV)})", file=sys.stderr)

    completed = subprocess.run(
        argv, cwd=WORKSPACE, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, env=env
    )
    events = _events(completed.stdout)
    print(f"exit {completed.returncode}, {len(events)} events", file=sys.stderr)
    if args.raw:
        with open(args.raw, "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")

    hook_events = [
        e for e in events
        if e.get("type") == "system" and e.get("subtype") == "hook_response"
        and e.get("hook_event") == "PreToolUse"
    ]
    print(f"PreToolUse hook_response events: {len(hook_events)}", file=sys.stderr)

    found = _envelopes(events)
    if not found:
        print(completed.stderr[-2000:], file=sys.stderr)
        raise SystemExit(
            "no PreToolUse envelope was echoed back by any hook. Nothing was stored - "
            "a handwritten envelope is exactly what gate 3 exists to prevent."
        )

    # More than one hook echoes; they must agree, or the thing being captured is
    # not one envelope. Compare on everything but the per-call fields.
    def _stable(env_: dict) -> dict:
        return {k: v for k, v in env_.items() if k not in TEMPLATED_ENVELOPE_FIELDS}

    first = _stable(found[0]["envelope"])
    for other in found[1:]:
        if _stable(other["envelope"]) != first:
            raise SystemExit(
                f"hooks disagree on the envelope: {found[0]['hook_id']} vs {other['hook_id']}. "
                "Nothing stored."
            )

    envelope = found[0]["envelope"]
    print(
        f"captured from {len(found)} echoing hook(s): "
        f"{sorted({e['hook_id'] for e in found})}, all agreeing",
        file=sys.stderr,
    )
    print(f"envelope keys: {sorted(envelope)}", file=sys.stderr)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(envelope, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
