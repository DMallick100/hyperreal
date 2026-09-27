"""The merge's refusals, and that the output cap reaches the wire. No network, $0.

Every assertion here is the cheap kind the shim build already leans on: a runner is
real when its refusals can be exercised for nothing. The two things under test are
the ones that could quietly falsify a published arm - a merge that overwrote a
measured row, and an output cap that a flag claims to set and a request does not
carry.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from measurements.live_shim_merge_rows import merge  # noqa: E402
from measurements.shim_providers import (  # noqa: E402
    DEFAULT_MAX_OUTPUT_TOKENS,
    chat_openai_shaped,
)

HOST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "measurements", "live_shim_host.py")


def row(case_id, outcome, *, cost=0.01, cap=1024, stage="", detail=""):
    return {
        "case_id": case_id,
        "family": "destructive",
        "outcome": outcome,
        "error_stage": stage,
        "error_detail": detail,
        "cost_usd": cost,
        "max_output_tokens": cap,
        "host": "hyperreal-shim@aaaaaaa",
    }


def arm(rows, **overrides):
    payload = {
        "pass": "fresh",
        "provider": "gateway",
        "served_by": "gateway",
        "model_requested": "openai/gpt-5",
        "model_origin": "us",
        "corpus": "/corpus",
        "tag": "r1",
        "host": "hyperreal-shim@aaaaaaa",
        "when": "2026-09-26T02:47:21",
        "max_output_tokens": 1024,
        "max_turns": 8,
        "case_budget_usd": 0.25,
        "min_call_interval_seconds": 4.0,
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "rows": rows,
    }
    payload.update(overrides)
    return payload


def write(payload):
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(payload, handle)
    handle.close()
    return handle.name


def merged(base_rows, patch_rows, **patch_overrides):
    base = write(arm(base_rows))
    patch = write(arm(patch_rows, tag="r2", **patch_overrides))
    return merge(base, patch)


class OnlyAHoleMayBeFilled(unittest.TestCase):
    def test_a_harness_error_row_is_superseded(self):
        out = merged(
            [row("a", "blocked"), row("b", "harness_error", stage="unknown", detail="finish=length")],
            [row("b", "ran", cap=8192, cost=0.05)],
        )
        by_id = {r["case_id"]: r for r in out["rows"]}
        self.assertEqual(by_id["b"]["outcome"], "ran")
        self.assertEqual(by_id["a"]["outcome"], "blocked")
        self.assertEqual(out["measured"], 2)
        self.assertTrue(out["complete"])
        self.assertIn("COMPLETE (2 of 2 measured)", out["completeness"])

    def test_a_MEASURED_row_may_never_be_overwritten(self):
        # Including when the re-run looks better. A `blocked` row is a result, and a
        # second pass choosing which result to publish is the defect.
        for outcome in ("blocked", "ran", "mutated", "model_refused", "undetermined"):
            with self.assertRaises(SystemExit) as caught:
                merged([row("a", outcome)], [row("a", "ran", cap=8192)])
            self.assertIn("MEASURED", str(caught.exception))

    def test_a_case_the_base_arm_never_sent_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            merged([row("a", "harness_error", stage="unknown")], [row("zz", "ran")])
        self.assertIn("not in the base arm", str(caught.exception))

    def test_two_spellings_of_ONE_corpus_directory_are_one_corpus(self):
        # Measured on the real files: the gpt-5 arm stored an absolute `--corpus` and
        # the re-run stored the relative default resolving to the same directory.
        # Refusing that merge would be refusing for a false reason.
        here = os.path.dirname(os.path.abspath(__file__))
        base = write(arm([row("a", "harness_error", stage="unknown")], corpus=here))
        patch = write(
            arm(
                [row("a", "ran")],
                tag="r2",
                corpus=os.path.join(here, os.pardir, os.path.basename(here)),
            )
        )
        out = merge(base, patch)
        self.assertEqual(out["measured"], 1)
        self.assertTrue(
            any("same directory, different spelling" in k for k in out["merge_parameter_differences"]),
            "the judgement that two spellings are one directory is printed, not absorbed",
        )

    def test_a_DIFFERENT_corpus_directory_is_still_refused(self):
        with self.assertRaises(SystemExit) as caught:
            merged(
                [row("a", "harness_error", stage="unknown")],
                [row("a", "ran")],
                corpus="/somewhere-else-entirely",
            )
        self.assertIn("not the same arm", str(caught.exception))

    def test_a_different_arm_is_refused_field_by_field(self):
        for field, value in (
            ("provider", "openrouter"),
            ("model_requested", "openai/gpt-oss-120b"),
            ("model_origin", "china"),
            ("pass", "shared"),
            ("corpus", "/somewhere-else"),
        ):
            with self.assertRaises(SystemExit) as caught:
                merged(
                    [row("a", "harness_error", stage="unknown")],
                    [row("a", "ran")],
                    **{field: value},
                )
            self.assertIn("not the same arm", str(caught.exception))
            self.assertIn(field, str(caught.exception))


class NothingIsDestroyedAndEveryRowSaysWhereItCameFrom(unittest.TestCase):
    def setUp(self):
        self.out = merged(
            [row("a", "blocked"), row("b", "harness_error", stage="unknown", detail="finish=length")],
            [row("b", "ran", cap=8192, cost=0.05)],
            max_output_tokens=8192,
            min_call_interval_seconds=15.0,
        )
        self.by_id = {r["case_id"]: r for r in self.out["rows"]}

    def test_provenance_is_on_every_row_not_only_the_new_ones(self):
        for case_id in ("a", "b"):
            self.assertIn("provenance", self.by_id[case_id], case_id)
        self.assertEqual(self.by_id["a"]["provenance"]["tag"], "r1")
        self.assertEqual(self.by_id["b"]["provenance"]["tag"], "r2")

    def test_the_replaced_row_records_what_it_replaced(self):
        supersedes = self.by_id["b"]["provenance"]["supersedes"]
        self.assertEqual(supersedes["outcome"], "harness_error")
        self.assertEqual(supersedes["error_stage"], "unknown")
        self.assertEqual(supersedes["max_output_tokens"], 1024)
        self.assertIsNone(self.by_id["a"]["provenance"]["supersedes"])

    def test_each_row_carries_the_cap_it_actually_ran_at(self):
        self.assertEqual(self.by_id["a"]["provenance"]["max_output_tokens"], 1024)
        self.assertEqual(self.by_id["b"]["provenance"]["max_output_tokens"], 8192)

    def test_the_superseded_row_is_kept_whole(self):
        self.assertEqual([r["case_id"] for r in self.out["superseded_rows"]], ["b"])
        self.assertEqual(self.out["superseded_rows"][0]["outcome"], "harness_error")
        self.assertEqual(self.out["superseded_rows"][0]["superseded_by"]["tag"], "r2")

    def test_three_cost_numbers_because_one_would_be_wrong(self):
        self.assertAlmostEqual(self.out["total_cost_usd"], 0.06)
        self.assertAlmostEqual(self.out["superseded_cost_usd"], 0.01)
        self.assertAlmostEqual(self.out["billed_cost_usd_all_attempts"], 0.07)

    def test_billed_comes_from_the_PASS_ACCUMULATORS_not_the_row_sums(self):
        # A retried case billed attempts no row keeps, and only each pass's own
        # `total_cost_usd` counted them. Summing rows would under-report the bill.
        base = write(dict(arm([row("a", "harness_error", stage="unknown", cost=0.01)]), total_cost_usd=0.09))
        patch = write(dict(arm([row("a", "ran", cost=0.02)], tag="r2"), total_cost_usd=0.05))
        out = merge(base, patch)
        self.assertAlmostEqual(out["total_cost_usd"], 0.02, msg="the rows reported")
        self.assertAlmostEqual(out["billed_cost_usd_all_attempts"], 0.14, msg="0.09 + 0.05")
        self.assertEqual(sorted(out["billed_cost_by_pass_usd"].values()), [0.05, 0.09])

    def test_a_parameter_that_changed_becomes_a_PRINTED_caveat(self):
        self.assertTrue(self.out["comparability_caveats"])
        self.assertTrue(any("max_output_tokens" in c for c in self.out["comparability_caveats"]))
        self.assertIn("max_output_tokens", self.out["merge_parameter_differences"])

    def test_the_heading_names_BOTH_shim_commits_when_the_rows_span_two(self):
        # Spec N6: `host` is mandatory in the heading too. Inheriting the base's sha
        # would claim the whole arm ran at a commit seven of its rows did not.
        out = merged(
            [row("a", "blocked"), row("b", "harness_error", stage="unknown")],
            [dict(row("b", "ran"), host="hyperreal-shim@bbbbbbb")],
        )
        self.assertIn("aaaaaaa", out["host"])
        self.assertIn("bbbbbbb", out["host"])
        self.assertEqual(len(out["hosts"]), 2)

    def test_transport_parameters_are_RECORDED_and_not_caveated(self):
        # The pacing differs between these two passes (4.0s vs 15.0s) and is recorded.
        # It is not a comparability caveat: the repo already ruled pacing transport,
        # and a caveat list carrying true-but-irrelevant lines is one readers skim.
        self.assertIn("min_call_interval_seconds", self.out["merge_parameter_differences"])
        self.assertFalse(
            [c for c in self.out["comparability_caveats"] if "min_call_interval" in c]
        )

    def test_a_field_the_base_pass_never_wrote_reads_UNRECORDED_not_as_a_value(self):
        # The four published arms predate `max_output_tokens`. Their cap is knowable
        # (1024 was hardcoded then) and is still not written into their rows: an
        # inferred number in an evidence file cannot be told from a measured one.
        base = write(arm([row("a", "harness_error", stage="unknown")]))
        json_rows = json.load(open(base))
        del json_rows["max_output_tokens"]
        for r in json_rows["rows"]:
            del r["max_output_tokens"]
        with open(base, "w", encoding="utf-8") as handle:
            json.dump(json_rows, handle)
        out = merge(base, write(arm([row("a", "ran", cap=8192)], tag="r2", max_output_tokens=8192)))
        caveat = next(c for c in out["comparability_caveats"] if "max_output_tokens" in c)
        self.assertIn("unrecorded", caveat)
        self.assertNotIn("1024", caveat)
        self.assertIsNone(out["rows"][0]["provenance"]["supersedes"]["max_output_tokens"])

    def test_the_caveat_list_exists_and_is_EMPTY_when_the_parameters_agree(self):
        # Printed even when empty: a field that disappears when it has nothing to say
        # is a field a reader cannot tell was checked.
        out = merged(
            [row("a", "harness_error", stage="unknown")], [row("a", "ran")]
        )
        self.assertEqual(out["comparability_caveats"], [])


class TheOutputCapReachesTheWire(unittest.TestCase):
    def test_the_published_default_has_not_moved(self):
        # Four arms are published at 1024. Raising this default would make every
        # future arm incomparable with them without anybody choosing that.
        self.assertEqual(DEFAULT_MAX_OUTPUT_TOKENS, 1024)

    def test_the_request_body_carries_the_cap_it_was_given(self):
        sent = {}

        class FakeResponse:
            status = 200

            def read(self):
                return json.dumps(
                    {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}
                ).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None, context=None):
            sent["body"] = json.loads(request.data.decode())
            return FakeResponse()

        real = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            reply = chat_openai_shaped(
                base="https://example.invalid/v1",
                key="k",
                model="openai/gpt-5",
                messages=[{"role": "user", "content": "x"}],
                tools=[],
                max_completion_tokens=8192,
            )
        finally:
            urllib.request.urlopen = real
        self.assertEqual(sent["body"]["max_completion_tokens"], 8192)
        self.assertEqual(reply.status, 200)

    def test_the_host_passes_its_cap_into_the_provider_call(self):
        # A source-level tripwire, because the flag being wired to nothing is
        # invisible from outside and would publish a re-run that never raised the cap.
        source = open(HOST, encoding="utf-8").read()
        self.assertIn("max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS", source)
        self.assertIn("max_completion_tokens=max_output_tokens", source)


if __name__ == "__main__":
    unittest.main()
