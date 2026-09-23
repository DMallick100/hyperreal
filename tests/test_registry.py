#!/usr/bin/env python3
"""Tests for the gate registry: matcher scope, readiness, and echo screening.

Offline and deterministic. Nothing here needs hookify, ecc, or any other
third-party plugin to be installed - `tests/fixtures/fixture_gate.py` supplies
the known behaviour instead. A test that skips itself when a vendor plugin is
absent would report green on a machine where it checked nothing.

The runner prints its own counts. Do not quote a test count you counted by eye:
this repo has already shipped "17 tests / 29 assertions" in three files when
the real numbers were 16 and 31.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gatebench.gates.registry import (  # noqa: E402
    Applicability,
    GateRegistration,
    Readiness,
    from_plugin_hooks,
    screen_echoed_input,
)
from gatebench.protocol import Channel, Verdict, decode  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fixture_gate.py")

TESTS = []
ASSERTIONS = 0


def test(fn):
    TESTS.append(fn)
    return fn


def check(condition, message):
    global ASSERTIONS
    ASSERTIONS += 1
    if not condition:
        raise AssertionError(message)


def ready():
    return (Readiness.READY, "fixture")


def fixture_registration(mode, **kwargs):
    kwargs.setdefault("readiness_probe", ready)
    return GateRegistration(
        name=f"fixture-{mode}",
        argv=(sys.executable, FIXTURE, mode),
        source=FIXTURE,
        **kwargs,
    )


def hook_input(tool_name="Bash", tool_input=None):
    return {
        "session_id": "test",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input if tool_input is not None else {"command": "ls"},
    }


# -- matcher scope -----------------------------------------------------------


@test
def test_star_matcher_applies_to_every_tool():
    reg = fixture_registration("silent", matcher="*")
    check(reg.applies_to("Bash") is Applicability.APPLICABLE, "* should match Bash")
    check(reg.applies_to("WebFetch") is Applicability.APPLICABLE, "* should match WebFetch")


@test
def test_empty_matcher_applies_to_every_tool():
    reg = fixture_registration("silent", matcher="")
    check(reg.applies_to("Write") is Applicability.APPLICABLE, "empty matcher should match all")


@test
def test_bash_matcher_does_not_apply_to_write():
    """The measured false catch: ecc's Bash dispatcher DENIED a Write case."""
    reg = fixture_registration("silent", matcher="Bash")
    check(
        reg.applies_to("Write") is Applicability.NOT_APPLICABLE,
        "a Bash-matched hook is never shown a Write call",
    )


@test
def test_alternation_matcher_applies_to_each_branch():
    reg = fixture_registration("silent", matcher="Edit|Write|MultiEdit")
    for tool in ("Edit", "Write", "MultiEdit"):
        check(reg.applies_to(tool) is Applicability.APPLICABLE, f"{tool} should match")
    check(
        reg.applies_to("Bash") is Applicability.NOT_APPLICABLE,
        "Bash should not match Edit|Write|MultiEdit",
    )


@test
def test_matcher_is_anchored():
    """Our chosen semantics, recorded so a change to it is visible in a diff."""
    reg = fixture_registration("silent", matcher="Bash")
    check(
        reg.applies_to("BashOutput") is Applicability.NOT_APPLICABLE,
        "matcher is matched against the whole tool name",
    )


@test
def test_inapplicable_case_is_never_spawned():
    """A gate that cannot exist still returns cleanly when the case is out of
    scope, which proves no subprocess was attempted."""
    reg = GateRegistration(
        name="never-runs",
        argv=("/nonexistent/gatebench-should-not-run",),
        source="test",
        matcher="Bash",
        readiness_probe=ready,
    )
    run = reg.run(hook_input(tool_name="Write"))
    check(run.answer is None, "an out-of-scope case must not produce an answer")
    check(run.applicability is Applicability.NOT_APPLICABLE, "should be NOT_APPLICABLE")
    check(not run.is_scorable, "an out-of-scope row is neither a catch nor a miss")


# -- readiness ---------------------------------------------------------------


@test
def test_missing_probe_is_unknown_not_ready():
    reg = GateRegistration(name="no-probe", argv=(sys.executable, FIXTURE, "silent"), source="t")
    state, detail = reg.readiness()
    check(state is Readiness.UNKNOWN, f"expected UNKNOWN, got {state}")
    check("no readiness probe" in detail, "detail should say why")


@test
def test_unknown_readiness_is_not_scorable():
    reg = GateRegistration(name="no-probe", argv=(sys.executable, FIXTURE, "silent"), source="t")
    run = reg.run(hook_input())
    check(run.readiness is Readiness.UNKNOWN, "readiness should be UNKNOWN")
    check(not run.is_scorable, "an unchecked gate must not be scored")


@test
def test_missing_executable_is_not_installed():
    reg = GateRegistration(
        name="absent", argv=("/nonexistent/gatebench-absent",), source="t", readiness_probe=ready
    )
    state, detail = reg.readiness()
    check(state is Readiness.NOT_INSTALLED, f"expected NOT_INSTALLED, got {state}")
    check("does not exist" in detail, "detail should name the missing program")


@test
def test_unconfigured_gate_is_not_scored_as_a_miss():
    """hookify with no rule files answers `{}` on every case. That is not a miss."""
    reg = fixture_registration(
        "silent", readiness_probe=lambda: (Readiness.UNCONFIGURED, "no rules")
    )
    run = reg.run(hook_input())
    check(run.readiness is Readiness.UNCONFIGURED, "readiness should be UNCONFIGURED")
    check(run.answer is not None, "the gate still ran and its answer is still published")
    check(not run.is_scorable, "an unconfigured gate must not land in a denominator")


@test
def test_ready_applicable_answer_is_scorable():
    reg = fixture_registration("deny-stdout-exit0", matcher="Bash")
    run = reg.run(hook_input())
    check(run.is_scorable, "a ready, applicable, answering gate is scorable")
    check(run.answer.verdict is Verdict.DENY, f"expected DENY, got {run.answer.verdict}")


# -- echo screening ----------------------------------------------------------


POISON = {
    "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"}
}


@test
def test_whole_input_echo_yields_no_verdict():
    """A gate echoing the entire hook input - the ecc behaviour - buries a
    case's payload under `tool_input`, where the decoder never reads it. Safe by
    construction, pinned so a future decoder that walks nested keys fails here
    rather than in production."""
    reg = fixture_registration("echo", matcher="Bash")
    run = reg.run(hook_input(tool_input=POISON))
    check(run.answer.verdict is Verdict.SILENT, f"expected SILENT, got {run.answer.verdict}")


@test
def test_echoed_input_does_not_become_a_verdict():
    """The corpus must not be able to choose its own result on an echoing gate."""
    reg = fixture_registration("echo-tool-input", matcher="Bash")
    run = reg.run(hook_input(tool_input=POISON))
    check(
        run.answer.verdict is not Verdict.DENY,
        "a decision read out of the harness's own input is not the gate's decision",
    )
    check(run.answer.verdict is Verdict.SILENT, f"expected SILENT, got {run.answer.verdict}")


@test
def test_echo_screen_records_a_conformance_note():
    reg = fixture_registration("echo-tool-input", matcher="Bash")
    run = reg.run(hook_input(tool_input=POISON))
    check(run.answer.conformance_notes, "an echo must be recorded, not silently dropped")
    check(
        any("echoed" in note for note in run.answer.conformance_notes),
        f"note should name the echo: {run.answer.conformance_notes}",
    )


@test
def test_echo_screen_keeps_the_raw_channels():
    reg = fixture_registration("echo-tool-input", matcher="Bash")
    run = reg.run(hook_input(tool_input=POISON))
    check(
        "permissionDecision" in run.answer.raw_stdout,
        "raw stdout must survive so the row stays recheckable by hand",
    )


@test
def test_echo_screen_leaves_a_genuine_decision_alone():
    answer = decode(0, json.dumps(POISON), "")
    screened = screen_echoed_input(answer, hook_input())
    check(screened.verdict is Verdict.DENY, "a real deny must not be screened away")
    check(screened is answer, "an unscreened answer should be returned unchanged")


@test
def test_echo_screen_does_not_touch_an_exit_code_verdict():
    """An exit code cannot be echoed, so the screen must not reach it."""
    payload = hook_input()
    answer = decode(2, json.dumps(payload), "blocked")
    screened = screen_echoed_input(answer, payload)
    check(screened.verdict is Verdict.DENY, "exit-2 deny must survive the echo screen")
    check(screened.channel is Channel.EXIT_CODE, f"expected EXIT_CODE, got {screened.channel}")


@test
def test_echo_of_a_nested_value_is_also_screened():
    """A gate that echoes only `tool_input` is still echoing us."""
    payload = hook_input(tool_input=POISON)
    answer = decode(0, json.dumps(POISON), "")
    screened = screen_echoed_input(answer, payload)
    check(screened.verdict is Verdict.SILENT, "a nested echo must be screened too")


# -- session isolation -------------------------------------------------------


@test
def test_every_run_gets_a_fresh_session_by_default():
    """ecc's Bash dispatcher denied once per session and then went quiet. Under
    a shared session the case ORDER picks the winner."""
    reg = fixture_registration("silent", matcher="Bash")
    first = reg.run(hook_input())
    second = reg.run(hook_input())
    check(first.session_id != second.session_id, "two runs must not share a session")
    check(first.session_id.startswith("gatebench-"), f"unexpected id {first.session_id!r}")


@test
def test_an_explicit_session_is_honoured():
    """Measuring statefulness on purpose needs a shared session available."""
    reg = fixture_registration("silent", matcher="Bash")
    first = reg.run(hook_input(), session_id="pinned")
    second = reg.run(hook_input(), session_id="pinned")
    check(first.session_id == "pinned" == second.session_id, "explicit session must be used")


@test
def test_the_callers_hook_input_is_not_mutated():
    reg = fixture_registration("echo-tool-input", matcher="Bash")
    payload = hook_input()
    payload["session_id"] = "caller-owned"
    reg.run(payload)
    check(payload["session_id"] == "caller-owned", "the caller's mapping must be left alone")


@test
def test_the_gate_actually_receives_the_fresh_session():
    """Pinned end to end: the echo fixture hands back what it was sent."""
    reg = GateRegistration(
        name="echo-all",
        argv=(sys.executable, FIXTURE, "echo"),
        source=FIXTURE,
        matcher="Bash",
        readiness_probe=ready,
    )
    run = reg.run(hook_input())
    check(
        run.session_id in run.answer.raw_stdout,
        "the session the row reports must be the session the gate was given",
    )


@test
def test_an_inapplicable_run_still_reports_its_session():
    reg = fixture_registration("silent", matcher="Bash")
    run = reg.run(hook_input(tool_name="Write"))
    check(run.session_id, "every run reports the session it would have used")


# -- registration validation -------------------------------------------------


def expect_value_error(fn, fragment):
    try:
        fn()
    except ValueError as exc:
        check(fragment in str(exc), f"expected {fragment!r} in {exc!r}")
        return
    raise AssertionError(f"expected ValueError mentioning {fragment!r}")


@test
def test_empty_argv_is_refused():
    expect_value_error(lambda: GateRegistration(name="x", argv=(), source="t"), "no argv")


@test
def test_missing_source_is_refused():
    expect_value_error(
        lambda: GateRegistration(name="x", argv=("true",), source="  "), "no source"
    )


@test
def test_bad_matcher_is_refused():
    expect_value_error(
        lambda: GateRegistration(name="x", argv=("true",), source="t", matcher="Bash("),
        "is not a regex",
    )


@test
def test_model_without_date_is_refused():
    expect_value_error(
        lambda: GateRegistration(name="x", argv=("true",), source="t", model="some-llm"),
        "model and model_date go together",
    )


@test
def test_registration_is_immutable():
    reg = fixture_registration("silent")
    try:
        reg.name = "renamed"
    except Exception:
        check(True, "frozen registration refuses mutation")
        return
    raise AssertionError("a registration must be immutable")


# -- reading a plugin's own hook config --------------------------------------


SAMPLE_CONFIG = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/g.py"', "timeout": 7}
                ],
            },
            {"hooks": [{"type": "prompt", "prompt": "is this safe?"}]},
        ],
        "Stop": [{"hooks": [{"type": "command", "command": "true"}]}],
    }
}


def write_config(directory):
    path = os.path.join(directory, "hooks.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(SAMPLE_CONFIG, handle)
    return path


@test
def test_plugin_hooks_carry_the_matcher_and_expand_the_root():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_config(tmp)
        found = from_plugin_hooks(path, plugin_root=tmp)
        check(len(found) == 1, f"expected one command hook, got {len(found)}")
        reg = found[0]
        check(reg.matcher == "Bash", f"matcher should carry across, got {reg.matcher!r}")
        check(reg.argv == ("python3", os.path.join(tmp, "g.py")), f"argv was {reg.argv}")
        check(reg.timeout_seconds == 7.0, f"timeout should carry across, got {reg.timeout_seconds}")
        check(path in reg.source, "source must name the config it came from")


@test
def test_plugin_hooks_skip_prompt_type_hooks():
    """S8 #1 is undecided; a prompt hook is skipped, never simulated."""
    with tempfile.TemporaryDirectory() as tmp:
        found = from_plugin_hooks(write_config(tmp), plugin_root=tmp)
        check(all(reg.argv[0] != "prompt" for reg in found), "no prompt hook may be registered")
        check(len(found) == 1, "only the command hook is registered")


@test
def test_plugin_hooks_only_read_the_named_event():
    with tempfile.TemporaryDirectory() as tmp:
        found = from_plugin_hooks(write_config(tmp), plugin_root=tmp, event="Stop")
        check(len(found) == 1, "the Stop event has one command hook")
        check(found[0].matcher == "*", "a group with no matcher means every tool")


SHELL_CONFIG = {
    "hooks": {
        "PreToolUse": [
            {"hooks": [{"type": "command", "command": "gate.sh | tee /tmp/log"}]}
        ]
    }
}


@test
def test_a_command_needing_a_shell_is_refused():
    """`shlex.split` would hand `|` and `tee` to the gate as arguments, and we
    would publish the resulting nonsense as the gate's behaviour."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "hooks.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(SHELL_CONFIG, handle)
        try:
            from_plugin_hooks(path, plugin_root=tmp)
        except ValueError as exc:
            check("needs a shell" in str(exc), f"unexpected message: {exc}")
            check("|" in str(exc), "the message should name the operator found")
            return
        raise AssertionError("a shell-only hook command must be refused, not guessed at")


@test
def test_an_ordinary_command_is_not_mistaken_for_a_shell_one():
    """The guard must not refuse ordinary English - or ordinary argv. ecc's
    real hooks carry a 4KB `node -e` program full of punctuation."""
    with tempfile.TemporaryDirectory() as tmp:
        found = from_plugin_hooks(write_config(tmp), plugin_root=tmp)
        check(len(found) == 1, "a plain command must still register")


@test
def test_no_case_content_reaches_argv():
    """S6 constraint, pinned: argv is fixed at registration."""
    reg = fixture_registration("echo", matcher="Bash")
    before = reg.argv
    reg.run(hook_input(tool_input={"command": "rm -rf / ; echo pwned"}))
    check(reg.argv == before, "argv must not change with case content")
    check(
        not any("pwned" in token for token in reg.argv),
        "case content must never reach a command line",
    )


def main():
    failures = []
    for fn in TESTS:
        try:
            fn()
        except Exception as exc:  # broad on purpose: one test must never abort the run
            failures.append((fn.__name__, exc))
    for name, exc in failures:
        print(f"FAIL {name}: {exc}")
    print(f"{len(TESTS)} tests, {ASSERTIONS} assertions - ", end="")
    print("all checks passed" if not failures else f"{len(failures)} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
