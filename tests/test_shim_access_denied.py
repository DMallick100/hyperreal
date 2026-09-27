"""A refused MODEL must be legible, and must not be re-asked eighty seconds running.

WHY THIS FILE EXISTS. The N2.3 bridge arm was launched on 2026-09-27 and could not
run: every `anthropic/*` id on this machine's only provider answers "no access" on
a free-tier account. The arm's own evidence for that was the string `http 403` - in
the row, and in a 135-byte per-case transcript carrying `status: 403` and no body -
so the cause had to be recovered by a SECOND script that re-posted to the provider,
even though `ProviderReply.raw` held the provider's sentence at the moment of
failure. Three defects, each pinned below:

1. **A non-200's detail must carry the provider's machine code and its own
   sentence.** `http 403` is a status, not a finding.
2. **The per-case transcript must carry it too**, because that log is the artefact a
   reader is pointed at.
3. **A 403 is not retryable.** `provider_http` is a transport bucket that holds a
   429 and a 403 alike, so an entitlement answer bought three attempts and 80s of
   backoff per case - 90 attempts across a 30-case arm, all of them asking a
   question already answered.

WHAT IS DELIBERATELY NOT HERE. A 429 whose body reads "No access to this model at
this time." - which two ids on this gateway genuinely return - is NOT reclassified.
Every machine-readable field on it says throttle, and `CLAUDE.md` 8.A forbids
detecting state by substring-matching prose. It keeps its retries; what changed is
that the sentence now reaches the record, so a human can see what the status hid.
That asymmetry is the point of the file and has a test of its own.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from measurements.live_shim_probe import (  # noqa: E402
    NON_RETRYABLE_STATUSES,
    RETRYABLE_STAGES,
)
from measurements.shim_providers import _error_code_of, _error_message_of  # noqa: E402

# The gateway's real 403 body, trimmed. Stored rather than paraphrased: a fixture
# that agrees with our model of the shape instead of with the wire is the defect
# `measurements/capture_host_envelope.py` exists to prevent.
RESTRICTED_403 = {
    "error": {
        "message": "Free tier users do not have access to this model. Upgrade to paid "
        "credits at https://vercel.com/d?to=... for unrestricted access.",
        "type": "no_providers_available",
        "param": {"statusCode": 403, "name": "RestrictedModelsError"},
    },
    "providerMetadata": {"gateway": {"routing": {"originalModelId": "anthropic/claude-haiku-4.5"}}},
}

# The one that lies. Status 429, type `rate_limit_exceeded`, and a message that is
# an access denial. Both ids answering this way did so on three consecutive attempts
# at 20s spacing, so it is not a transient the pacing would have cleared.
ACCESS_DENIAL_WEARING_A_429 = {
    "error": {"message": "No access to this model at this time.", "type": "rate_limit_exceeded"}
}


class ANon200CarriesTheProvidersOwnWords(unittest.TestCase):
    def test_the_machine_code_is_extracted(self):
        self.assertEqual(_error_code_of(RESTRICTED_403), "no_providers_available")

    def test_the_message_is_extracted_for_the_RECORD(self):
        message = _error_message_of(RESTRICTED_403)
        self.assertIn("Free tier users do not have access", message)

    def test_the_message_is_bounded_so_one_body_cannot_flood_a_row(self):
        flood = {"error": {"message": "x" * 5000, "type": "t"}}
        self.assertLessEqual(len(_error_message_of(flood)), 300)

    def test_a_body_with_no_message_yields_EMPTY_and_never_a_guess(self):
        self.assertEqual(_error_message_of({"error": {"type": "only_a_type"}}), "")
        self.assertEqual(_error_message_of({}), "")
        self.assertEqual(_error_message_of("not a mapping"), "")

    def test_the_429_that_lies_gives_up_its_sentence_even_though_nothing_branches_on_it(self):
        # The whole value of recording it: the code and the message DISAGREE, and a
        # reader can only see that if both are written down.
        self.assertEqual(_error_code_of(ACCESS_DENIAL_WEARING_A_429), "rate_limit_exceeded")
        self.assertEqual(
            _error_message_of(ACCESS_DENIAL_WEARING_A_429), "No access to this model at this time."
        )


class AnAuthorizationAnswerIsNotWeather(unittest.TestCase):
    def test_403_401_and_404_are_non_retryable(self):
        for status in (401, 403, 404):
            self.assertIn(status, NON_RETRYABLE_STATUSES)

    def test_429_is_STILL_retryable_because_a_throttle_really_does_clear(self):
        self.assertNotIn(429, NON_RETRYABLE_STATUSES)

    def test_500_is_STILL_retryable(self):
        # `anthropic/claude-3-haiku` answered 500 three times on 2026-09-27 and is
        # recorded UNMEASURED rather than restricted. A 500 is the provider's
        # weather; calling it an access denial would be inventing a measurement.
        self.assertNotIn(500, NON_RETRYABLE_STATUSES)

    def test_the_STAGE_is_still_retryable_so_the_status_is_doing_the_work(self):
        # If someone deletes the status check, this test still passes and the one
        # above it still passes - so the guard is pinned where it acts, in the
        # probe's retry predicate, by the integration test below.
        self.assertIn("provider_http", RETRYABLE_STAGES)


class TheRetryPredicateHonoursIt(unittest.TestCase):
    """The rule where it ACTS, not only where it is declared.

    `CLAUDE.md` 8.A: a gate enforced at the declaration was defeated by a caller
    that declared nothing. So this reproduces the probe's own predicate over rows
    rather than trusting the constant to be consulted.
    """

    @staticmethod
    def retryable(row: dict, attempts: list[dict]) -> bool:
        from measurements.live_shim_probe import HARNESS_ERROR

        return (
            row["outcome"] == HARNESS_ERROR
            and row["error_stage"] in RETRYABLE_STAGES
            and not any(a.get("executed") for a in attempts)
            and row.get("provider_status") not in NON_RETRYABLE_STATUSES
        )

    def test_a_403_is_not_retried(self):
        row = {"outcome": "harness_error", "error_stage": "provider_http", "provider_status": 403}
        self.assertFalse(self.retryable(row, []))

    def test_a_429_is_retried(self):
        row = {"outcome": "harness_error", "error_stage": "provider_http", "provider_status": 429}
        self.assertTrue(self.retryable(row, []))

    def test_a_403_whose_command_ALREADY_RAN_is_still_not_retried(self):
        row = {"outcome": "harness_error", "error_stage": "provider_http", "provider_status": 403}
        self.assertFalse(self.retryable(row, [{"executed": True}]))

    def test_a_timeout_with_no_status_is_retried(self):
        # status 0 means we never got an answer; that is exactly the retryable case.
        row = {"outcome": "harness_error", "error_stage": "provider_timeout", "provider_status": 0}
        self.assertTrue(self.retryable(row, []))


class TheTranscriptRecordsTheFailure(unittest.TestCase):
    def test_the_turn_entry_declares_the_two_new_keys(self):
        # A filesystem walk over the writer, because the defect was an ABSENT key and
        # a unit test on a happy turn would never have noticed one missing.
        source = (ROOT / "measurements" / "live_shim_host.py").read_text(encoding="utf-8")
        self.assertIn('"transport_detail": reply.transport_detail', source)
        self.assertIn('"provider_error_code": reply.error_code', source)


class TheArmRecordsWhatItVerifiedAgainst(unittest.TestCase):
    def test_the_payload_carries_its_trust_store_and_interpreter(self):
        # The bridge smoke's first failure was CERTIFICATE_VERIFY_FAILED under an
        # interpreter with no `certifi`, and the arm file could not say which store
        # it used - `hyperreal/trust.py`'s own docstring says a caller that cannot
        # name its store cannot defend a reachability result in either direction.
        source = (ROOT / "measurements" / "live_shim_probe.py").read_text(encoding="utf-8")
        self.assertIn('"trust_store": SSL_TRUST_STORE', source)
        self.assertIn('"interpreter": sys.executable', source)


class TheSweepCanExpressTheCapItsArmsRanAt(unittest.TestCase):
    def test_the_cap_and_the_timeout_are_passed_through_not_defaulted(self):
        source = (ROOT / "measurements" / "live_shim_sweep.py").read_text(encoding="utf-8")
        self.assertIn('"--max-output-tokens"', source)
        self.assertIn('"--http-timeout"', source)

    def test_the_sweeps_defaults_are_IMPORTED_from_the_probe_s_module(self):
        # Two copies of one default is how a wrapper comes to disagree with what it
        # wraps about which cap an arm published at.
        source = (ROOT / "measurements" / "live_shim_sweep.py").read_text(encoding="utf-8")
        self.assertIn("DEFAULT_MAX_OUTPUT_TOKENS", source)
        self.assertIn("DEFAULT_HTTP_TIMEOUT", source)
        self.assertNotIn("default=1024", source)

    def test_the_probe_no_longer_retypes_the_timeout_literal(self):
        source = (ROOT / "measurements" / "live_shim_probe.py").read_text(encoding="utf-8")
        self.assertNotIn("default=120,", source)


if __name__ == "__main__":
    unittest.main()
