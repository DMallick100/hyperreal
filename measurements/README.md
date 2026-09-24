# measurements/

The measurement passes that produced the four protocol findings recorded in
`docs/architecture.md` — channel disagreement, matcher scope, echoed input, and
session statefulness. They invoke the gates installed on *this* machine, so
they are evidence of how those gates behaved when they were read, not a
portable test suite: `tests/` is the suite, and it is what must stay green.

They were written outside this repo during the kickoff and moved in on
2026-09-23, with their `gatebench` imports repointed at the renamed package.

    python3 measurements/pass1_channels.py
    python3 measurements/pass2_invocation.py
    python3 measurements/pass3_session_state.py
    python3 measurements/survey_installed.py
    python3 measurements/dump_candidate_sources.py

Run them from the repo root. A gate that is not installed here simply will not
answer; that is a fact about this machine, never a result about the gate.
