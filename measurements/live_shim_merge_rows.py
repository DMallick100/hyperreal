"""Merge a re-run of some cases into an arm, with per-row provenance. Free.

WHY THIS IS NOT A COPY-AND-PASTE JOB. An arm file is the published evidence for a
row in a table, and "30 of 30 measured" is the strongest sentence one can carry. A
merge that quietly replaced rows would let a re-run under *different parameters*
wear the original arm's heading, which is the unverified-model-of-a-host defect one
level down. So three things are structural here rather than remembered:

1. **A re-run may only supersede a row the arm never measured.** The base row's
   outcome must be `harness_error` (spec N5: harness errors are never in a
   denominator, and they are the only rows a re-run is entitled to replace). A
   `blocked`, `ran`, `mutated`, `model_refused` or `undetermined` row is a RESULT,
   and this refuses to overwrite one - including when the re-run looks better.
2. **Nothing is destroyed.** Superseded rows are kept whole under
   `superseded_rows`, the same reason the open-US arm's failed first pass is
   committed beside its second.
3. **Every row says where it came from and what it ran at.** `provenance` is on
   EVERY row, retained ones included: a merged file where only the new rows are
   annotated invites the reader to assume the rest are the new parameters too.

WHAT IT REFUSES TO MERGE AT ALL. Two passes are not one arm if they differ on the
provider, the model requested, the model's origin, the pass kind or the corpus
(spec N0.2 rule 4). Anything else that differs - the host sha, the output cap, the
call pacing, the catalogue - is RECORDED in `merge_parameter_differences` and, when
it is a parameter that changes what was measured, restated as a comparability
caveat. A difference a reader cannot see is a difference that becomes a false
comparison.

    python3 measurements/live_shim_merge_rows.py --base results/<arm>.json \
        --patch results/<rerun>.json --out results/<merged>.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HARNESS_ERROR = "harness_error"

# Differ on one of these and the two files are not the same arm. Refuse.
IDENTITY_FIELDS = ("pass", "provider", "model_requested", "model_origin", "corpus", "served_by")

# ...except that `corpus` is a PATH, and two spellings of one directory are one
# corpus. Measured 2026-09-26: the gpt-5 arm's first pass stored
# `/Users/dhruvmallick/hyperreal/corpus` (it was given an absolute `--corpus`) and
# the re-run stored `corpus` (the default, resolved against the same cwd). A string
# compare would refuse a legitimate merge, and refusing for a false reason is how
# the check gets loosened wholesale next time. Compared by `realpath`; when the
# strings differ and the directory does not, the pair is RECORDED rather than
# silently normalised.
PATH_IDENTITY_FIELDS = ("corpus",)

# Differ on one of these and the merged arm's rows are not comparable to each other
# on the axis the field names, so the caveat is written into the payload. These
# change what a case was ASKED or how much of an answer it was allowed to give.
MEASUREMENT_PARAMETERS = ("max_output_tokens", "max_turns", "case_budget_usd")

# Recorded, not caveated: these move with a commit or a clock, or they decide only
# whether an answer arrived at all. `min_call_interval_seconds` and
# `http_timeout_seconds` are OUR transport - the repo already ruled pacing a
# transport parameter when it fixed the open-US arm's 429s, on the grounds that it
# alters no prompt, no gate, no corpus and no cap. Calling them comparability
# caveats would put a true-but-irrelevant line beside the one that matters, and a
# caveat list a reader learns to skim guards nothing.
RECORDED_ONLY = (
    "host",
    "tag",
    "when",
    "catalogue",
    "arm_budget_usd",
    "combination_rule",
    "isolate_cwd",
    "min_call_interval_seconds",
    "http_timeout_seconds",
)


def _render(value) -> str:
    """A field a pass never wrote reads `unrecorded`, never as a value.

    `None` here means the pass predates the field, which is NOT the same claim as a
    number (`CLAUDE.md` 8.0 #4 - a blank is *not known*, never *verified*). The value
    is knowable for the published arms (1024 was hardcoded at that commit) and is
    deliberately not backfilled into their rows: an inferred number in an evidence
    file is indistinguishable from a measured one.
    """
    return "unrecorded (the field postdates that pass)" if value is None else str(value)


def _provenance(payload: dict, row: dict, path: str, supersedes: dict | None) -> dict:
    """Where this row came from, at what settings, and what (if anything) it replaced."""
    return {
        "source_file": os.path.basename(path),
        "tag": payload.get("tag", ""),
        "host": row.get("host", payload.get("host", "")),
        "when": payload.get("when", ""),
        # Read from the ROW first: a row written before this field existed has no
        # value, and reporting the run's setting for it would be inventing one.
        "max_output_tokens": row.get("max_output_tokens", payload.get("max_output_tokens")),
        "min_call_interval_seconds": payload.get("min_call_interval_seconds"),
        "supersedes": supersedes,
    }


def merge(base_path: str, patch_path: str) -> dict:
    with open(base_path, encoding="utf-8") as handle:
        base = json.load(handle)
    with open(patch_path, encoding="utf-8") as handle:
        patch = json.load(handle)

    def _same(field: str) -> bool:
        first, second = base.get(field), patch.get(field)
        if field in PATH_IDENTITY_FIELDS and isinstance(first, str) and isinstance(second, str):
            return os.path.realpath(first) == os.path.realpath(second)
        return first == second

    mismatched = {
        field: {"base": base.get(field), "patch": patch.get(field)}
        for field in IDENTITY_FIELDS
        if not _same(field)
    }
    if mismatched:
        raise SystemExit(
            "refusing to merge: these are not the same arm.\n"
            + json.dumps(mismatched, indent=1)
            + "\nSpec N0.2 rule 4 - an arm run half under one set of identities and "
            "half under another is not one arm."
        )

    base_rows = {row["case_id"]: row for row in base["rows"]}
    patch_rows = {row["case_id"]: row for row in patch["rows"]}

    unknown = sorted(set(patch_rows) - set(base_rows))
    if unknown:
        raise SystemExit(
            f"refusing to merge: {unknown} are in the re-run and not in the base arm. "
            "A merge fills holes in an arm; it does not extend one."
        )

    protected = sorted(
        case_id
        for case_id in patch_rows
        if base_rows[case_id]["outcome"] != HARNESS_ERROR
    )
    if protected:
        raise SystemExit(
            "refusing to merge: the base arm MEASURED these cases, so a re-run may "
            f"not replace them: {[(c, base_rows[c]['outcome']) for c in protected]}\n"
            "Only a `harness_error` row is a hole. Overwriting a measured row would "
            "let a second run silently choose the result (spec N5)."
        )

    differences = {
        field: {"base": base.get(field), "patch": patch.get(field)}
        for field in MEASUREMENT_PARAMETERS + RECORDED_ONLY
        if base.get(field) != patch.get(field)
    }
    # Two spellings of the same directory passed the identity check above. That is a
    # judgement this file made, so it is printed rather than absorbed.
    differences.update(
        {
            f"{field} (same directory, different spelling)": {
                "base": base.get(field),
                "patch": patch.get(field),
                "realpath": os.path.realpath(str(base.get(field))),
            }
            for field in PATH_IDENTITY_FIELDS
            if base.get(field) != patch.get(field)
        }
    )
    caveats = [
        f"{field}: {_render(base.get(field))} on the retained rows, "
        f"{_render(patch.get(field))} on the re-run rows"
        for field in MEASUREMENT_PARAMETERS
        if base.get(field) != patch.get(field)
    ]

    rows: list[dict] = []
    superseded: list[dict] = []
    for row in base["rows"]:
        replacement = patch_rows.get(row["case_id"])
        if replacement is None:
            merged_row = dict(row)
            merged_row["provenance"] = _provenance(base, row, base_path, None)
            rows.append(merged_row)
            continue
        merged_row = dict(replacement)
        merged_row["provenance"] = _provenance(
            patch,
            replacement,
            patch_path,
            {
                "outcome": row["outcome"],
                "error_stage": row.get("error_stage", ""),
                "error_detail": row.get("error_detail", "")[:200],
                "tag": base.get("tag", ""),
                "max_output_tokens": row.get("max_output_tokens", base.get("max_output_tokens")),
                "cost_usd": row.get("cost_usd", 0.0),
            },
        )
        rows.append(merged_row)
        kept = dict(row)
        kept["superseded_by"] = {"source_file": os.path.basename(patch_path), "tag": patch.get("tag", "")}
        superseded.append(kept)

    measured = [r for r in rows if r["outcome"] != HARNESS_ERROR]
    errors = [r for r in rows if r["outcome"] == HARNESS_ERROR]
    reported_cost = round(sum(float(r.get("cost_usd") or 0.0) for r in rows), 6)
    superseded_cost = round(sum(float(r.get("cost_usd") or 0.0) for r in superseded), 6)
    # WHAT WAS BILLED IS NOT THE SUM OF THE ROWS. A row keeps the cost of the attempt
    # that produced it; a case retried through a 429 or a timeout billed its earlier
    # attempts too, and only each pass's own accumulator (`total_cost_usd`) counted
    # them. Measured here: the row sums come to 0.4992 and the two accumulators to
    # 0.5023, the difference being the gpt-5 arm's discarded retry attempts. Take the
    # accumulators - the same "sum what the pass measured, do not re-derive it"
    # discipline the $21.46 leaf total is on record for.
    billed_cost = round(
        float(base.get("total_cost_usd") or 0.0) + float(patch.get("total_cost_usd") or 0.0), 6
    )

    # Spec N6 makes `host` mandatory on every row AND in every table heading. A merged
    # arm spans two shim commits, so the heading names both rather than inheriting the
    # base's and quietly claiming the whole arm ran at that sha. Each row still carries
    # its own.
    hosts = sorted({str(r.get("host", "")) for r in rows if r.get("host")})
    payload = dict(base)
    payload.update(
        {
            "host": hosts[0] if len(hosts) == 1 else " + ".join(hosts),
            "hosts": hosts,
            "merged_from": [os.path.basename(base_path), os.path.basename(patch_path)],
            "tag": f"{base.get('tag', '')}+{patch.get('tag', '')}-merged",
            "merge_rule": "a re-run may only supersede a base row whose outcome was "
            "harness_error; superseded rows are kept, not deleted",
            "merge_parameter_differences": differences,
            # Printed even when empty, so a reader can tell "the caps agreed" from
            # "nobody checked" (`CLAUDE.md` 8.A: a column that disappears when empty
            # is a column a reader cannot tell was measured).
            "comparability_caveats": caveats,
            "superseded_case_ids": sorted(patch_rows),
            "superseded_rows": superseded,
            "cases": len(rows),
            "measured": len(measured),
            "harness_errors": [
                {
                    "case_id": r["case_id"],
                    "stage": r.get("error_stage", ""),
                    "detail": (r.get("error_detail") or "")[:200],
                }
                for r in errors
            ],
            "complete": not errors,
            "completeness": f"{'COMPLETE' if not errors else 'INCOMPLETE'} "
            f"({len(measured)} of {len(rows)} measured"
            + (
                "; " + ", ".join(f"{r['case_id']}/{r.get('error_stage', '')}" for r in errors) + ")"
                if errors
                else ")"
            ),
            # THREE cost numbers, because one would be wrong either way. The rows
            # reported cost `total_cost_usd`; the arm BILLED that plus what the
            # superseded attempts cost, and money spent is never rounded away
            # (`CLAUDE.md` 8.A: print the numerator and the denominator).
            "total_cost_usd": reported_cost,
            "superseded_cost_usd": superseded_cost,
            "billed_cost_usd_all_attempts": billed_cost,
            "billed_cost_by_pass_usd": {
                os.path.basename(base_path): base.get("total_cost_usd"),
                os.path.basename(patch_path): patch.get("total_cost_usd"),
            },
            "rows": rows,
        }
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", required=True)
    parser.add_argument("--patch", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    payload = merge(args.base, args.patch)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
    print(f"wrote {args.out}")
    print(json.dumps({k: v for k, v in payload.items() if k not in ("rows", "superseded_rows")}, indent=1))
    # A merged arm still carrying a harness error is INCOMPLETE, and the exit status
    # says so - the same rule that made the runner exit 1 on 7 of 30.
    return 0 if payload["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
