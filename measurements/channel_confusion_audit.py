"""When a gate speaks on more than one channel, does it say the same thing?

WHY THIS AUDIT EXISTS
---------------------
`protocol.decode` reads four channels in a fixed precedence: stdout JSON, then
stderr JSON, then exit code 2, then `continue: false`. That precedence exists
for a good measured reason - `validate-bash.sh` emits `permissionDecision: ask`
on stderr *while exiting 2*, and reading the exit code first would publish an
`ask` as a `deny`, a false catch.

But precedence is a **resolution**, and a resolution hides a conflict. The
decoder attaches a conformance note for exactly two shapes (exit 2 + non-deny
JSON; exit 0 + deny JSON). Everything else it resolves in silence:

* stdout JSON says one thing and stderr JSON says another - the loser is never
  reported;
* a non-zero, non-2 exit code alongside a clean stdout decision - the exit code
  is dropped entirely, and a gate that crashed *after* deciding looks healthy;
* `continue: false` sitting inside a payload that also names a
  `permissionDecision` - only the decision is read, and the harder block is
  discarded;
* a decision on a channel the gate also used for diagnostics, so the payload
  the decoder parsed was the *last* `{...}` in a stream of them.

This probe re-reads the raw channels of every call and reports each conflict it
finds, WITHOUT changing how any of them is resolved. It is a measurement of how
often the decoder's precedence is load-bearing, and of whether any gate here is
currently being read on a channel that contradicts another.

It also re-checks Guard 2 (echoed input): a gate that copies our stdin to its
stdout hands the decoder a payload the corpus author wrote. Guard 2 downgrades
that to SILENT with a note. This audit counts how many calls it fired on,
because a guard that never fires and a guard that is dead look identical from
outside (`CLAUDE.md` 8.0 #3).

Usage:  python3 measurements/channel_confusion_audit.py [--repeats N]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hyperreal.corpus import load  # noqa: E402
from hyperreal.gates.installed import discover  # noqa: E402
from hyperreal.gates.registry import Applicability, Readiness  # noqa: E402
from hyperreal.protocol import (  # noqa: E402
    BLOCKING_EXIT_CODE,
    Channel,
    Verdict,
    _parse_json_object,
    _verdict_from_payload,
)
from hyperreal.runner import hook_input_for  # noqa: E402


def _decisions_on_channels(stdout: str, stderr: str) -> dict[str, tuple[str, str]]:
    """Every channel that independently carries a permission decision.

    Uses the decoder's own payload reader and verdict reader, so this audit
    cannot drift from what `decode` would see - it only declines to apply the
    precedence.
    """
    found: dict[str, tuple[str, str]] = {}
    for text, channel in ((stdout, Channel.STDOUT_JSON), (stderr, Channel.STDERR_JSON)):
        payload = _parse_json_object(text)
        if payload is None:
            continue
        decision = _verdict_from_payload(payload)
        if decision is None:
            continue
        verdict, reason = decision
        found[channel.value] = (verdict.value, reason)
    return found


def _count_top_level_objects(text: str) -> int:
    """How many JSON values sit end-to-end on this stream.

    `json.JSONDecoder.raw_decode` is the only reader that answers this without
    guessing at whitespace or separators. Returns 0 for a stream that is not
    JSON at all, which is not a finding - plenty of gates print prose.
    """
    decoder = json.JSONDecoder()
    index, found = 0, 0
    body = text.strip()
    while index < len(body):
        try:
            _, end = decoder.raw_decode(body, index)
        except ValueError:
            return found
        found += 1
        index = end
        while index < len(body) and body[index] in " \t\r\n":
            index += 1
    return found


def _continue_false_on(stdout: str, stderr: str) -> list[str]:
    channels = []
    for text, channel in ((stdout, Channel.STDOUT_JSON), (stderr, Channel.STDERR_JSON)):
        payload = _parse_json_object(text)
        if payload is not None and payload.get("continue") is False:
            channels.append(channel.value)
    return channels


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()

    gates = discover()
    cases = load(ROOT / "corpus")

    print("Hyperreal channel-confusion audit")
    print(f"  {len(gates)} gate(s) x {len(cases)} case(s) x n={args.repeats}")
    print("  the decoder's precedence is APPLIED as shipped and REPORTED, not changed\n")

    calls = 0
    skipped = Counter()
    channel_used = Counter()
    # The conflicts, each a list of human-readable lines.
    two_decisions: list[str] = []
    exit_vs_json: list[str] = []
    dropped_exit: list[str] = []
    discarded_continue: list[str] = []
    multi_object: list[str] = []
    echo_screened: list[str] = []
    both_streams: Counter = Counter()
    # Guard 2 fires only when a DECISION payload equals something we sent. A gate
    # can echo our input without that being true - it can print the input beside
    # its own decision. Counted separately, because `gates/registry.py`'s Guard 2
    # docstring reports echoing as measured on "every ecc hook", and whether that
    # still holds for the hook we actually score is a checkable claim.
    echoed_anywhere: Counter = Counter()
    echo_free: Counter = Counter()

    for gate in gates:
        readiness, detail = gate.readiness()
        for case in cases:
            # Skips are counted in CASES, calls in CALLS - they are different
            # denominators and printing both under one word would make 32 skipped
            # look comparable to 64 made.
            if gate.applies_to(case.tool_name) is Applicability.NOT_APPLICABLE:
                skipped[f"{gate.name}: not applicable"] += 1
                continue
            if readiness is Readiness.NOT_INSTALLED:
                skipped[f"{gate.name}: {readiness.value}"] += 1
                continue
            for index in range(args.repeats):
                run = gate.run(hook_input_for(case))
                answer = run.answer
                if answer is None:
                    skipped[f"{gate.name}: no answer"] += 1
                    continue
                calls += 1
                channel_used[f"{gate.name}/{answer.channel.value}"] += 1
                where = f"{gate.name} / {case.case_id} / repeat {index}"

                stdout, stderr = answer.raw_stdout, answer.raw_stderr
                exit_code = answer.exit_code

                if stdout.strip() and stderr.strip():
                    both_streams[gate.name] += 1

                # A fingerprint only our envelope carries: the session id Guard 3
                # minted for this one call. Structural, not a substring of prose -
                # a gate cannot produce this string except by copying our input.
                if run.session_id and run.session_id in (stdout + stderr):
                    echoed_anywhere[gate.name] += 1
                else:
                    echo_free[gate.name] += 1

                decisions = _decisions_on_channels(stdout, stderr)
                if len(decisions) > 1:
                    shape = "; ".join(
                        f"{channel}={verdict}" for channel, (verdict, _) in sorted(decisions.items())
                    )
                    agreed = len({verdict for verdict, _ in decisions.values()}) == 1
                    two_decisions.append(
                        f"{where}: {shape} ({'agree' if agreed else 'CONTRADICT'}); "
                        f"decoder took {answer.channel.value}"
                    )

                # An exit code the decoder did not use, that is not 0 and not 2.
                if decisions and exit_code not in (0, BLOCKING_EXIT_CODE):
                    dropped_exit.append(
                        f"{where}: decided on {answer.channel.value} but exited "
                        f"{exit_code}; the exit code was not read"
                    )

                # The two shapes the decoder DOES note - counted, to confirm the
                # note actually fires rather than being prose in a docstring.
                # Each class is checked independently: a `break` on the first
                # match would undercount the second whenever both fire, which is
                # exactly the call worth seeing.
                for note in answer.conformance_notes:
                    if "exit 0 (non-blocking)" in note or f"exit {BLOCKING_EXIT_CODE}" in note:
                        exit_vs_json.append(f"{where}: {note.splitlines()[0][:110]}")
                for note in answer.conformance_notes:
                    if "echoed the harness's own input" in note:
                        echo_screened.append(where)

                # A continue:false the decoder never reached, because a
                # permissionDecision on the same or an earlier channel won.
                stops = _continue_false_on(stdout, stderr)
                if stops and answer.channel is not Channel.CONTINUE_FALSE:
                    discarded_continue.append(
                        f"{where}: continue:false present on {','.join(stops)} but the "
                        f"verdict was read from {answer.channel.value}"
                    )

                # More than one JSON object on a stream means `_parse_json_object`
                # took a brace-span across them, or took only one of them. Counted
                # by actually decoding successive values - a substring test for
                # `}\n{` misses space-separated objects, and a check that only
                # fires on one formatting is a check the next gate walks past.
                for text, label in ((stdout, "stdout"), (stderr, "stderr")):
                    found = _count_top_level_objects(text)
                    if found > 1:
                        multi_object.append(
                            f"{where}: {label} carried {found} top-level JSON objects; "
                            f"the decoder reads one"
                        )

    print(f"{calls} scorable call(s) made.")
    if skipped:
        for label, count in sorted(skipped.items()):
            print(f"  skipped {count:>3} case(s)  {label}")
    print()

    print("CHANNEL EACH VERDICT WAS READ FROM")
    for label, count in sorted(channel_used.items()):
        print(f"  {count:>4}  {label}")
    print()

    def _section(title: str, lines: list[str], clean: str) -> None:
        print(title)
        if not lines:
            print(f"  {clean}")
        else:
            print(f"  {len(lines)} occurrence(s):")
            for line in lines[:12]:
                print(f"    {line}")
            if len(lines) > 12:
                print(f"    ... and {len(lines) - 12} more")
        print()

    _section(
        "C1. TWO CHANNELS EACH CARRYING A PERMISSION DECISION",
        two_decisions,
        "no call decided on more than one channel; the stdout-before-stderr "
        "precedence was never load-bearing in this run",
    )
    _section(
        "C2. A DECISION ALONGSIDE AN EXIT CODE THE DECODER DOES NOT READ",
        dropped_exit,
        "every deciding call exited 0 or 2; no exit code was silently dropped",
    )
    _section(
        "C3. THE TWO DISAGREEMENTS THE DECODER DOES NOTE (exit vs JSON)",
        exit_vs_json,
        "no call produced exit-code/JSON disagreement",
    )
    _section(
        "C4. A continue:false THAT LOST TO A permissionDecision",
        discarded_continue,
        "no call carried a continue:false that was discarded",
    )
    _section(
        "C5. MORE THAN ONE JSON OBJECT ON ONE STREAM",
        multi_object,
        "no stream carried multiple top-level JSON objects",
    )
    _section(
        "C6. GUARD 2 (echoed input) FIRING",
        echo_screened,
        "guard 2 did not fire in this run - which is NOT evidence it works; "
        "tests/test_registry.py is what holds it up",
    )

    print("C7. DOES THE GATE REPEAT OUR INPUT BACK AT ALL?")
    print("    Detected by looking for the per-call session id Guard 3 minted -")
    print("    a string no gate can produce except by copying our envelope.")
    if not echoed_anywhere and not echo_free:
        print("    no calls to check")
    for name in sorted(set(echoed_anywhere) | set(echo_free)):
        hit, miss = echoed_anywhere[name], echo_free[name]
        verdict = "echoes our input" if hit else "does NOT echo our input"
        print(f"    {name}: {verdict} ({hit} of {hit + miss} call(s))")
    if not echoed_anywhere:
        print("    FINDING: no registered gate echoed our envelope in this run, so")
        print("    Guard 2 has no live exercise on this corpus. `gates/registry.py`'s")
        print("    Guard 2 docstring reports echoing as measured on 'every ecc hook")
        print("    invoked through run-with-flags.js' - the hook this harness scores")
        print("    is not one of them, or no longer behaves that way. Recorded as a")
        print("    finding; the guard and the docstring are NOT changed here.")
    print()

    print("BOTH STREAMS NON-EMPTY ON THE SAME CALL")
    if not both_streams:
        print("  no gate wrote to stdout and stderr on the same call")
    for name, count in sorted(both_streams.items()):
        print(f"  {count:>4}  {name} - diagnostics on one stream and a decision on the")
        print("        other is legal, and it is also how a decision gets missed")
    print()
    print("WHAT THIS DOES NOT ESTABLISH: which channel a running Claude Code reads,")
    print("or in what order. docs/protocol.md's Unverified section still stands, and")
    print("nothing here is a reason to change the decoder's precedence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
