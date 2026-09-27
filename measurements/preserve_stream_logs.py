"""Copy a real-host pass's per-case stream logs out of /tmp before the OS reaps them.

WHY THIS EXISTS. `live_session_fixture.SANDBOX` is `/tmp/hyperreal-live-2026-09-25`,
so every `claude -p` session's stream log — the only place a pass's resolved model id
and its post-execution hook events are written — lands somewhere the OS deletes. Three
findings in three days have run into that same missing file:

  * "the alias is not the model" (2026-09-25) says read `claude-haiku-4-5-…` out of
    each session's own `system/init` event. For eight of nine published real-host arms
    that event is gone, so those tables can never name what they measured.
  * the bridge arm can establish "did the command run?" and can never claim model
    parity with the baseline, for the same reason
    (`docs/results-2026-09-27-bridge-arm-blocked.md` §5).
  * `blocked_verdict_audit.py` answers "did this case's command execute?" from
    `PostToolUse`/`PostToolUseFailure` events in the log. Against a reaped pass it
    returns **30 UNSCOREABLE of 30** — which is why the `is_error` defect
    (`docs/results-2026-09-27-claude-repeat-pass.md` §3) has an unknown blast radius
    rather than a measured one.

`docs/results-2026-09-27-claude-repeat-pass.md` §5 names this as the cheapest
outstanding task in the repo. This is it.

WHAT IT DOES NOT DO. It does not move `SANDBOX`. Relocating the log root would change
where every future run writes as a side effect of a read-only concern, which is the
2026-09-25 rule ("do not change a control because a probe embarrassed it") pointed at
our own harness. Copying afterwards leaves every published path in every published row
still true.

WHAT IT REFUSES. It will not overwrite an existing preserved copy — a second pass at
the same tag is a second pass, not a correction — and it records a sha256 per file, so
"the preserved copy is the log the row names" is checkable rather than assumed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# `measurements/` is not a package (no `__init__.py`), so a sibling import needs this
# directory on the path — the same shape `live_session_probe.py` uses.
sys.path.insert(0, HERE)

from live_session_fixture import LOGS  # noqa: E402

# Anchored to the repo, not the process cwd. `live_model_state_check.py` shipped the
# relative version of this and reported nine populated arms as missing when run from
# ~/AeroTrace; a free checker whose failure mode is "everything is gone" is one people
# learn to disbelieve.
DEST_ROOT = os.path.join(ROOT, "results", "stream-logs")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def logs_for(tag: str, source: str = LOGS) -> list[str]:
    """The per-case logs of one pass.

    `live_session_probe` names a log `{kind}-{model}-{tag}-{case_id}.jsonl`, so a tag
    identifies a pass across every case. Matched on the tag delimited by hyphens rather
    than by `in`, because `iso-r2-2026-09-27` is a substring of nothing here today and
    that is a property of today's tags, not of the naming scheme.
    """
    if not os.path.isdir(source):
        return []
    needle = f"-{tag}-"
    return sorted(
        os.path.join(source, name)
        for name in os.listdir(source)
        if name.endswith(".jsonl") and needle in name
    )


def preserve(tag: str, *, source: str = LOGS, dest_root: str = DEST_ROOT) -> dict:
    found = logs_for(tag, source)
    dest = os.path.join(dest_root, tag)
    if os.path.isdir(dest) and os.listdir(dest):
        raise SystemExit(
            f"refusing to overwrite {dest}: it already holds "
            f"{len(os.listdir(dest))} file(s). A second pass at one tag is a second "
            f"pass, not a correction — give it its own tag."
        )
    os.makedirs(dest, exist_ok=True)
    manifest = []
    for path in found:
        name = os.path.basename(path)
        target = os.path.join(dest, name)
        shutil.copy2(path, target)
        manifest.append(
            {
                "name": name,
                "source": path,
                "bytes": os.path.getsize(target),
                "sha256": _sha256(target),
            }
        )
    payload = {
        "tag": tag,
        "source_dir": source,
        "dest_dir": dest,
        "files": len(manifest),
        "bytes": sum(row["bytes"] for row in manifest),
        "manifest": manifest,
    }
    with open(os.path.join(dest, "MANIFEST.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("tags", nargs="+", help="pass tags, e.g. iso-r2-2026-09-27")
    args = parser.parse_args()
    worst = 0
    for tag in args.tags:
        payload = preserve(tag)
        print(
            f"{tag}: {payload['files']} log(s), {payload['bytes']} bytes -> "
            f"{payload['dest_dir']}"
        )
        # Zero files is not success. A pass whose logs were already reaped must not
        # report a tidy copy of nothing (CLAUDE.md 8.0 #4).
        if payload["files"] == 0:
            print(f"  NOTHING FOUND for {tag} — reaped, or the tag is wrong", file=sys.stderr)
            worst = 1
    return worst


if __name__ == "__main__":
    sys.exit(main())
