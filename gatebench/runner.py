"""The gate x case matrix. STUB (2026-09-23).

Contract this must honour when it is written, recorded now so it is not
rediscovered later:

* **One gate at a time.** Deployed gates run in parallel and their conflicting
  decisions resolve by rules we have not measured (docs/protocol.md, Unverified
  #4). Benchmarking one at a time is a different thing from deployment, and the
  report says so.
* **Every row carries its n.** A non-deterministic (LLM) gate answers
  differently run to run; n=1 is not a measurement. Until the repeat count is
  decided (architecture.md S8 #4) the number must be printed, never assumed.
* **A gate that fails to launch is ERROR, not absent.** Already handled in
  SubprocessGate.run; the runner must not filter those rows out.
* **Public and held-out splits run in the same pass** and print side by side.
* **Case content is never interpolated into a command line.** The runner passes
  cases to a gate as stdin JSON only.
"""

from __future__ import annotations


def run_matrix(*args, **kwargs):
    raise NotImplementedError("runner not built (2026-09-23) - see architecture.md S9")
