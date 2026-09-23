"""Pin the decoder against the two shipped gate idioms that disagree.

Run with no dependencies:  python3 tests/test_protocol.py
Or under pytest:           pytest tests/test_protocol.py

Every case here is built from a real on-disk reference implementation, not from
an imagined gate. The two that matter are cited in docs/protocol.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gatebench.protocol import Channel, Verdict, decode  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"  ok   {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL {name}  {detail}")


# --- the hookify idiom: JSON on stdout -------------------------------------

def test_stdout_deny() -> None:
    stdout = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
            },
            "systemMessage": "**[no-rm-rf]**\nDangerous",
        }
    )
    answer = decode(0, stdout, "")
    check("stdout deny -> DENY", answer.verdict is Verdict.DENY, answer.verdict)
    check("stdout deny -> STDOUT_JSON", answer.channel is Channel.STDOUT_JSON, answer.channel)
    check("stdout deny reason falls back to systemMessage", "no-rm-rf" in answer.reason, answer.reason)
    check(
        "exit 0 + deny is flagged as unverified behaviour",
        any("UNVERIFIED" in n for n in answer.conformance_notes),
        answer.conformance_notes,
    )


# --- the validate-bash.sh idiom: JSON on stderr, exit 2 --------------------

def test_stderr_deny_exit_2() -> None:
    stderr = json.dumps(
        {"hookSpecificOutput": {"permissionDecision": "deny"}, "systemMessage": "rm -rf"}
    )
    answer = decode(2, "", stderr)
    check("stderr deny -> DENY", answer.verdict is Verdict.DENY, answer.verdict)
    check("stderr deny -> STDERR_JSON", answer.channel is Channel.STDERR_JSON, answer.channel)
    check("stderr deny does not fail open", not answer.failed_open, answer.failed_open)


def test_ask_on_stderr_is_not_a_deny() -> None:
    """The single most important ordering test in the file.

    validate-bash.sh emits ASK on stderr *and* exits 2. A decoder that reads the
    exit code first publishes a false catch. The JSON must win.
    """
    stderr = json.dumps(
        {"hookSpecificOutput": {"permissionDecision": "ask"}, "systemMessage": "sudo"}
    )
    answer = decode(2, "", stderr)
    check("exit 2 + ask JSON -> ASK not DENY", answer.verdict is Verdict.ASK, answer.verdict)


# --- bare exit codes -------------------------------------------------------

def test_bare_exit_2_is_deny() -> None:
    answer = decode(2, "", "Blocked: writes outside the project root")
    check("bare exit 2 -> DENY", answer.verdict is Verdict.DENY, answer.verdict)
    check("bare exit 2 -> EXIT_CODE channel", answer.channel is Channel.EXIT_CODE, answer.channel)
    check("bare exit 2 keeps stderr as reason", "project root" in answer.reason, answer.reason)


def test_exit_0_silence_is_not_allow() -> None:
    answer = decode(0, "", "")
    check("exit 0 silence -> SILENT", answer.verdict is Verdict.SILENT, answer.verdict)
    check("SILENT is not ALLOW", answer.verdict is not Verdict.ALLOW, answer.verdict)


def test_continue_true_only_is_still_silent() -> None:
    answer = decode(0, json.dumps({"continue": True}), "")
    check("{'continue': true} -> SILENT", answer.verdict is Verdict.SILENT, answer.verdict)


def test_explicit_allow_is_distinct_from_silence() -> None:
    stdout = json.dumps({"hookSpecificOutput": {"permissionDecision": "allow"}})
    answer = decode(0, stdout, "")
    check("explicit allow -> ALLOW", answer.verdict is Verdict.ALLOW, answer.verdict)


# --- failing open ----------------------------------------------------------

def test_crash_fails_open_and_says_so() -> None:
    answer = decode(1, "", "Traceback (most recent call last): ...")
    check("exit 1 -> ERROR", answer.verdict is Verdict.ERROR, answer.verdict)
    check("exit 1 sets failed_open", answer.failed_open, answer.failed_open)


def test_timeout_fails_open() -> None:
    answer = decode(-9, "", "", timed_out=True)
    check("timeout -> ERROR", answer.verdict is Verdict.ERROR, answer.verdict)
    check("timeout sets failed_open", answer.failed_open, answer.failed_open)


def test_unparseable_decision_is_error_not_allow() -> None:
    stdout = json.dumps({"hookSpecificOutput": {"permissionDecision": "probably fine"}})
    answer = decode(0, stdout, "")
    check("garbage decision -> ERROR", answer.verdict is Verdict.ERROR, answer.verdict)
    check("garbage decision fails open visibly", answer.failed_open, answer.failed_open)


def test_garbage_stdout_on_exit_0_is_silent_not_error() -> None:
    answer = decode(0, "thinking about it...\n", "")
    check("chatty exit 0 -> SILENT", answer.verdict is Verdict.SILENT, answer.verdict)


def test_json_after_a_human_line_is_still_read() -> None:
    stdout = 'checking...\n{"hookSpecificOutput": {"permissionDecision": "deny"}}'
    answer = decode(0, stdout, "")
    check("prefixed JSON still decodes", answer.verdict is Verdict.DENY, answer.verdict)


# --- other channels --------------------------------------------------------

def test_continue_false_is_a_block_with_its_own_note() -> None:
    stdout = json.dumps({"continue": False, "stopReason": "policy"})
    answer = decode(0, stdout, "")
    check("continue:false -> DENY", answer.verdict is Verdict.DENY, answer.verdict)
    check("continue:false -> its own channel", answer.channel is Channel.CONTINUE_FALSE, answer.channel)
    check("continue:false is noted", bool(answer.conformance_notes), answer.conformance_notes)


def test_updated_input_without_a_decision_is_modify() -> None:
    stdout = json.dumps(
        {"hookSpecificOutput": {"updatedInput": {"command": "ls"}}}
    )
    answer = decode(0, stdout, "")
    check("updatedInput alone -> MODIFY", answer.verdict is Verdict.MODIFY, answer.verdict)
    check("MODIFY carries the rewrite", answer.updated_input == {"command": "ls"}, answer.updated_input)


def test_raw_channels_are_always_retained() -> None:
    answer = decode(2, "out", "err")
    check("raw stdout kept", answer.raw_stdout == "out", answer.raw_stdout)
    check("raw stderr kept", answer.raw_stderr == "err", answer.raw_stderr)


def test_answer_is_immutable() -> None:
    answer = decode(0, "", "")
    try:
        answer.verdict = Verdict.DENY  # type: ignore[misc]
    except Exception:
        check("GateAnswer is frozen", True)
        return
    check("GateAnswer is frozen", False, "assignment succeeded")


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        print(test.__name__)
        test()
    print()
    # Printed so the README and architecture doc quote a MEASURED count rather
    # than one someone counted by eye. The first draft of both said "17 tests /
    # 29 assertions"; the real numbers were different.
    summary = f"{len(tests)} tests, {CHECKS} assertions"
    if FAILURES:
        print(f"{summary} - {len(FAILURES)} FAILED")
        for failure in FAILURES:
            print("  -", failure)
        return 1
    print(f"{summary} - all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
