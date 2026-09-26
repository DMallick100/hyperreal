"""Run every test file BOTH ways and total the assertions, because one stream lies.

Five of the eight files in `tests/` carry no `unittest.main()` - they print their
own results and exit 0 either way. A runner that reads only the unittest stream
reports a false green on those five, which is exactly what happened on
2026-09-25 (AeroTrace `CLAUDE.md` 8.0 #3: a dead gate reads as covered). So each
file is executed as a script AND loaded into a unittest runner, and a file that
yields no assertions by either route is reported as UNVERIFIED rather than
passing quietly.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The self-printed files end on a line naming a count; this reads it rather than
# trusting exit 0, which those files return regardless.
COUNT_RE = re.compile(r"(\d+)\s+(?:checks?|tests?|assertions?|cases?)", re.I)


def run_as_script(path: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, path],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def run_as_unittest(path: str) -> tuple[int, int]:
    """Return (tests_run, failures+errors) for the unittest cases in one file."""
    module = "tests." + os.path.basename(path)[:-3]
    loader = unittest.TestLoader()
    try:
        suite = loader.loadTestsFromName(module)
    except Exception:
        return 0, 0
    result = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0).run(suite)
    return result.testsRun, len(result.failures) + len(result.errors)


def main() -> int:
    sys.path.insert(0, REPO)
    total_unittest = 0
    total_scripted = 0
    bad: list[str] = []

    for path in sorted(glob.glob(os.path.join(REPO, "tests", "test_*.py"))):
        rel = os.path.relpath(path, REPO)
        rc, out = run_as_script(path)
        ran, failed = run_as_unittest(path)
        total_unittest += ran

        tail = out.splitlines()[-1] if out else ""
        scripted = 0
        if ran == 0:
            # No unittest cases: the file is self-printing, so read its own count.
            match = COUNT_RE.search(out)
            scripted = int(match.group(1)) if match else 0
            total_scripted += scripted

        verdict = "ok"
        if rc != 0 or failed:
            verdict = "FAIL"
            bad.append(rel)
        elif ran == 0 and scripted == 0:
            verdict = "UNVERIFIED (no assertions seen by either route)"
            bad.append(rel)

        print(f"{rel:38s} rc={rc} unittest={ran:3d} self={scripted:3d}  {verdict}")
        if verdict != "ok":
            print(f"    last line: {tail[:160]}")

    print(
        f"\nunittest assertions: {total_unittest}   self-printed: {total_scripted}   "
        f"total: {total_unittest + total_scripted}"
    )
    if bad:
        print("NOT GREEN: " + ", ".join(bad))
        return 1
    print("all files green by both routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
