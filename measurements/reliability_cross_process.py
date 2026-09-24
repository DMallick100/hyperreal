"""Does a gate answer the same way in a DIFFERENT harness process, later?

WHAT IS ALREADY MEASURED, AND WHAT IS NOT
-----------------------------------------
`runner.run_matrix` repeats every (gate, case) `n` times and folds a
disagreement into `Stability.UNSTABLE`. That is real, and it is narrow: all `n`
repeats happen back to back, inside one Python process, within a second or two
of each other. It catches a gate that flips call to call. It cannot catch:

* a gate whose answer depends on state that outlives one harness process -
  a cache file, a lockfile, a daemon it started, a `~/.cache` counter;
* a gate that is fast on a warm filesystem and times out on a cold one;
* a gate that answers differently at a different wall-clock moment.

Those are exactly the failure modes that make a published number irreproducible
for the reader who reruns it tomorrow. `docs/architecture.md` S8 #4 is still
open on the right `n`; this probe measures the axis that raising `n` inside one
process cannot reach.

WHAT IT DOES
------------
Runs `hyperreal run` as a FULL, SEPARATE OS PROCESS, `--passes` times, each
writing its own evidence file, then diffs the per-(gate, case) verdicts across
those files. Separate processes mean separate interpreter state, separate
subprocess trees, separate session ids, and a gap in wall-clock time.

It changes nothing. It shells out to the shipped CLI with the shipped defaults
and reads the evidence the CLI already writes.

HOW TO READ THE OUTPUT
----------------------
* `verdict agreement` - a (gate, case) whose verdict is identical in every pass.
  Disagreement here is the finding, and the case ids are printed.
* `channel agreement` - same verdict reached on a DIFFERENT channel in different
  passes is still a conformance finding even when the verdict is stable.
* `latency spread` - p50/p95 per pass, side by side. This repo has already seen
  the same row move 71 ms -> 507 ms -> 1533 ms -> 30158 ms between runs
  (`docs/results-2026-09-23.md` R2, R4). A spread printed per pass is the honest
  way to say a single latency figure does not travel.

A gate that agrees across passes is NOT thereby deterministic - it is a gate
that did not vary over the passes we ran, on this machine, today. Absence is not
permission (`CLAUDE.md` 8.0 #4).

Usage:  python3 measurements/reliability_cross_process.py [--passes N] [--repeats N]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Three is the floor here for the same reason two is the floor inside a run: two
# passes that disagree tell you there is variance and nothing about its shape.
DEFAULT_PASSES = 3
MINIMUM_PASSES = 2


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. No interpolation, no mean - see S7."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _run_one_pass(index: int, evidence_path: Path, repeats: int) -> list[dict]:
    argv = [
        sys.executable,
        "-m",
        "hyperreal.cli",
        "run",
        "--rank-by",
        "name",
        "--repeats",
        str(repeats),
        "--evidence",
        str(evidence_path),
        "--quiet",
    ]
    completed = subprocess.run(
        argv, cwd=str(ROOT), capture_output=True, text=True, timeout=1800
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"pass {index} exited {completed.returncode}; a partial pass is not a "
            f"pass and is not averaged in.\nstderr:\n{completed.stderr[-2000:]}"
        )
    records = []
    for line in evidence_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("record") == "run":
            records.append(record)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--passes", type=int, default=DEFAULT_PASSES)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()

    if args.passes < MINIMUM_PASSES:
        raise SystemExit(
            f"--passes {args.passes} is below the floor of {MINIMUM_PASSES}: one "
            "pass cannot disagree with anything"
        )

    print(f"Hyperreal cross-process reliability: {args.passes} separate harness")
    print(f"processes, n={args.repeats} per gate per case inside each.\n")

    # verdicts[(gate, case)][pass_index] = the set of verdicts that pass saw
    verdicts: dict[tuple[str, str], dict[int, set[str]]] = defaultdict(dict)
    channels: dict[tuple[str, str], dict[int, set[str]]] = defaultdict(dict)
    latencies: dict[tuple[str, int], list[float]] = defaultdict(list)
    scorable_gates: set[str] = set()

    with tempfile.TemporaryDirectory(prefix="hyperreal-reliability-") as tmp:
        for index in range(args.passes):
            path = Path(tmp) / f"pass-{index}.jsonl"
            records = _run_one_pass(index, path, args.repeats)
            per_pass: dict[tuple[str, str], set[str]] = defaultdict(set)
            per_pass_channel: dict[tuple[str, str], set[str]] = defaultdict(set)
            for record in records:
                key = (record["gate"], record["case_id"])
                per_pass[key].add(str(record.get("verdict")))
                per_pass_channel[key].add(str(record.get("channel")))
                if record.get("verdict") is not None:
                    scorable_gates.add(record["gate"])
                    latencies[(record["gate"], index)].append(float(record["wall_ms"]))
            for key, seen in per_pass.items():
                verdicts[key][index] = seen
            for key, seen in per_pass_channel.items():
                channels[key][index] = seen
            print(f"  pass {index}: {len(records)} run records")

    print()

    # -- verdict agreement ---------------------------------------------------
    unstable_within: list[str] = []
    unstable_across: list[str] = []
    for (gate, case_id), by_pass in sorted(verdicts.items()):
        per_pass_answers = []
        for index in sorted(by_pass):
            seen = by_pass[index]
            if len(seen) > 1:
                unstable_within.append(
                    f"{gate} / {case_id}: pass {index} disagreed with itself: "
                    f"{sorted(seen)}"
                )
            per_pass_answers.append(tuple(sorted(seen)))
        if len(set(per_pass_answers)) > 1:
            shape = " | ".join(
                f"pass {index}={','.join(sorted(by_pass[index]))}" for index in sorted(by_pass)
            )
            unstable_across.append(f"{gate} / {case_id}: {shape}")

    total = len(verdicts)
    print(f"VERDICT AGREEMENT across {args.passes} processes")
    print(f"  {total - len(unstable_across)} of {total} (gate, case) pairs answered identically in every pass")
    if unstable_across:
        print(f"  {len(unstable_across)} pair(s) DID NOT. This is the finding:")
        for line in unstable_across:
            print(f"    {line}")
    else:
        print("  0 pairs varied. Not proof of determinism - proof of no variance observed.")
    if unstable_within:
        print(f"\n  {len(unstable_within)} pair(s) also varied WITHIN a single pass:")
        for line in unstable_within:
            print(f"    {line}")

    # -- channel agreement ---------------------------------------------------
    channel_moves: list[str] = []
    for (gate, case_id), by_pass in sorted(channels.items()):
        shapes = {tuple(sorted(by_pass[index])) for index in by_pass}
        if len(shapes) > 1:
            shape = " | ".join(
                f"pass {index}={','.join(sorted(by_pass[index]))}" for index in sorted(by_pass)
            )
            channel_moves.append(f"{gate} / {case_id}: {shape}")

    print(f"\nCHANNEL AGREEMENT across {args.passes} processes")
    if channel_moves:
        print(f"  {len(channel_moves)} pair(s) answered on a different channel between passes:")
        for line in channel_moves:
            print(f"    {line}")
    else:
        print("  every pair used the same channel in every pass")

    # -- latency spread ------------------------------------------------------
    print(f"\nLATENCY SPREAD per pass (ms; p50 and p95, never a mean)")
    if not scorable_gates:
        print("  no gate produced a scorable answer, so there is no latency to report")
    for gate in sorted(scorable_gates):
        cells = []
        for index in range(args.passes):
            samples = latencies[(gate, index)]
            if not samples:
                cells.append(f"pass {index}: -")
                continue
            cells.append(
                f"pass {index}: p50 {_percentile(samples, 0.50):.0f} / "
                f"p95 {_percentile(samples, 0.95):.0f} (n={len(samples)})"
            )
        print(f"  {gate}")
        for cell in cells:
            print(f"    {cell}")

    print(
        "\nWHAT THIS DOES NOT ESTABLISH: one machine, one day, consecutive passes."
        "\nA gate with a daily or a per-boot behaviour change is invisible here, and"
        "\nso is anything that depends on a state this harness never created."
    )
    return 1 if (unstable_across or unstable_within) else 0


if __name__ == "__main__":
    raise SystemExit(main())
