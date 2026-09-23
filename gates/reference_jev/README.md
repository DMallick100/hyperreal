# reference gate

**Not built (2026-09-23).** Placeholder for the ~50-line reference gate.

Two jobs, and it is worth being clear they are different:

1. **A known-behaviour entrant** so the harness can be exercised against a gate
   whose answers we already know — the role `validate-bash.sh` is playing today
   in `tests/probe_shipped_gate.py`.
2. **A worked example** so "write your own gate" has something to copy.

It is scored publicly like every other entrant, **including where it loses**.
If the reference gate never appears in a losing column, the corpus is the thing
to distrust (`docs/architecture.md` §S2 #4).

## Constraint

Being written by the benchmark's author, it must not be tuned to the corpus. It
is written **before** the corpus and changed only for defects, with the change
noted. Any tuning of it after corpus results exist is recorded in this file.
