"""One-off: stamp every public case with its canary and its split. v1.1.

Run once, 2026-09-24, to take `corpus/` from version 2026-09-23.1 to
2026-09-24.1. Kept in the repo rather than thrown away because a reader who
wants to check that the canary was added mechanically - the same string, once
per case, derived from the case id and from nothing else - can read this
instead of taking the diff on trust.

Idempotent: rerunning it rewrites the same bytes. It never touches
`tool_input`, which is what keeps v1.0 and v1.1 comparable at the gate.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hyperreal.corpus import FAMILIES, PUBLIC_SPLIT, canary_for, load  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "corpus"
NEW_VERSION = "2026-09-24.1"

# The published field order, so the diff is an insertion and not a reshuffle.
ORDER = ("case_id", "family", "tool_name", "tool_input", "expected", "rationale",
         "provenance", "corpus_version", "canary", "split", "added_after_failure_of", "tags")


def main() -> int:
    touched = 0
    for family in FAMILIES:
        path = ROOT / f"{family}.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        out = []
        for row in rows:
            row = dict(row)
            row["corpus_version"] = NEW_VERSION
            row["canary"] = canary_for(row["case_id"])
            row["split"] = PUBLIC_SPLIT
            out.append({key: row[key] for key in ORDER if key in row})
            touched += 1
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    cases = load(ROOT, expect_split=PUBLIC_SPLIT)
    print(f"{touched} cases stamped; {len(cases)} reload clean at {cases[0].corpus_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
