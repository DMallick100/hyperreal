#!/usr/bin/env python3
"""A gate with known behaviour, for testing the harness rather than a vendor.

The registry's tests must not depend on hookify or ecc being installed: a test
that silently skips when a third-party plugin is absent is a dead gate
(`CLAUDE.md` 8.0 #3). This fixture answers exactly what its `--mode` says, on
exactly the channel its mode names, so every branch of the registry can be
exercised offline and deterministically.

It is NOT the reference gate promised in `README.md` item 7 - that is still a
declared stub. This is a test instrument.
"""

import json
import sys

MODES = (
    "silent",
    "deny-stdout-exit0",
    "ask-stderr-exit2",
    "echo",
    "echo-tool-input",
    "crash",
)


def main(argv):
    mode = argv[1] if len(argv) > 1 else "silent"
    if mode not in MODES:
        print(f"unknown mode {mode!r}; expected one of {MODES}", file=sys.stderr)
        return 2
    raw = sys.stdin.read()

    if mode == "silent":
        return 0

    if mode == "crash":
        print("fixture gate crashed on purpose", file=sys.stderr)
        return 3

    if mode == "echo":
        # Copies the harness's own stdin straight back. This is the behaviour
        # measured on every ecc hook invoked through run-with-flags.js, and the
        # reason `screen_echoed_input` exists.
        sys.stdout.write(raw)
        return 0

    if mode == "echo-tool-input":
        # The dangerous half of the echo family. Echoing the whole hook input
        # leaves a case's payload one level down, where the decoder never looks;
        # echoing `tool_input` alone puts whatever the case author wrote exactly
        # where the decoder reads a permission decision.
        try:
            sys.stdout.write(json.dumps(json.loads(raw).get("tool_input", {})))
        except (ValueError, AttributeError):
            sys.stdout.write(raw)
        return 0

    if mode == "deny-stdout-exit0":
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "fixture denies",
            }
        }
        print(json.dumps(payload))
        return 0

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "fixture asks",
        }
    }
    print(json.dumps(payload), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
