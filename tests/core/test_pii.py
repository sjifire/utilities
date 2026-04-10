"""Tests for PII redaction helpers in ``sjifire.core.pii``.

Covers ``redact_pii`` text redaction patterns (age+gender, standalone
age, phone numbers, caller names) plus preservation of operational
content.  The higher-level LLM-preferred sanitization helpers live in
``sjifire.ops.dispatch.sanitize`` and are tested in
``tests/ops/test_dispatch_sanitize.py``.
"""

import typing

import pytest

from sjifire.core.pii import redact_pii

# ---------------------------------------------------------------------------
# redact_pii — age + gender patterns
# ---------------------------------------------------------------------------


class TestRedactAgeGender:
    """Age + gender combinations should be replaced with [patient]."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            # Standard "yo" forms
            (
                "13yo female injured in rear passenger seat",
                "[patient] injured in rear passenger seat",
            ),
            ("9yo male stuck under car", "[patient] stuck under car"),
            ("72yo male fell from standing position", "[patient] fell from standing position"),
            # Spaced "y/o" forms
            ("55 y/o female, conscious and breathing", "[patient], conscious and breathing"),
            ("3 y/o male having seizure", "[patient] having seizure"),
            # "year old" forms
            ("72 year old male who had fallen", "[patient] who had fallen"),
            ("13-year-old female unresponsive", "[patient] unresponsive"),
            ("5 year old boy choking", "[patient] choking"),
            ("88 year old woman, difficulty breathing", "[patient], difficulty breathing"),
            # "yr old" forms
            ("45 yr old man with chest pain", "[patient] with chest pain"),
            ("6-yr-old girl fell off bike", "[patient] fell off bike"),
            # Abbreviated gender (m/f)
            ("55yo m with chest pain", "[patient] with chest pain"),
            ("23yo f unresponsive", "[patient] unresponsive"),
            # En-dash separator
            ("72\u2013year\u2013old male", "[patient]"),
        ],
    )
    def test_age_gender_redacted(self, text: str, expected: str):
        assert redact_pii(text) == expected

    def test_multiple_patients_in_one_line(self):
        text = "13yo female and 9yo male both injured in vehicle"
        result = redact_pii(text)
        assert result == "[patient] and [patient] both injured in vehicle"

    def test_age_gender_case_insensitive(self):
        assert redact_pii("72YO MALE FELL") == "[patient] FELL"
        assert redact_pii("13Yo Female injured") == "[patient] injured"


# ---------------------------------------------------------------------------
# redact_pii — standalone age patterns
# ---------------------------------------------------------------------------


class TestRedactStandaloneAge:
    """Age descriptors without gender should also be replaced."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("72yo fell from standing", "[patient] fell from standing"),
            ("13 y/o having seizure", "[patient] having seizure"),
            ("5 year old choking", "[patient] choking"),
            ("88-year-old, difficulty breathing", "[patient], difficulty breathing"),
            ("45 yr old with chest pain", "[patient] with chest pain"),
        ],
    )
    def test_standalone_age_redacted(self, text: str, expected: str):
        assert redact_pii(text) == expected


# ---------------------------------------------------------------------------
# redact_pii — phone numbers
# ---------------------------------------------------------------------------


class TestRedactPhoneNumbers:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("callback (360) 555-1234", "callback [phone]"),
            ("call 360-555-1234 for info", "call [phone] for info"),
            ("phone: 360.555.1234", "phone: [phone]"),
            ("contact 360 555-1234", "contact [phone]"),
        ],
    )
    def test_phone_redacted(self, text: str, expected: str):
        assert redact_pii(text) == expected


# ---------------------------------------------------------------------------
# redact_pii — caller/RP names
# ---------------------------------------------------------------------------


class TestRedactCallerNames:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("caller: John Smith on scene", "[caller] on scene"),
            ("caller John Smith advises false alarm", "[caller] advises false alarm"),
            ("RP: Jane Doe reports smoke", "[caller] reports smoke"),
            ("RP Jane Doe called back", "[caller] called back"),
            ("reporting party: Bob Jones saw flames", "[caller] saw flames"),
        ],
    )
    def test_caller_redacted(self, text: str, expected: str):
        assert redact_pii(text) == expected


# ---------------------------------------------------------------------------
# redact_pii — preservation of operational content
# ---------------------------------------------------------------------------


class TestPreserveOperational:
    """Operational dispatch content must NOT be redacted."""

    @pytest.mark.parametrize(
        "text",
        [
            # Unit codes and station references
            "E31 dispatched to 200 Spring St",
            "BN31 has command, est Farm Rd Command",
            "T33 staging on Cattle Point Rd",
            "L31 clear, returning to quarters",
            # Addresses
            "respond to 589 Old Farm Rd, Friday Harbor",
            "cross streets: Cattle Point Rd and Pear Point Rd",
            # Times and timestamps
            "dispatched at 14:30:15",
            "on scene at 14:38:22",
            # Status codes
            "disp:AMB, oc:MED",
            "cancel additional resources",
            # Fire-specific
            "nothing showing on arrival",
            "smoke from eaves, fire in chimney",
            "fire extinguished, overhaul in progress",
            # General dispatch language
            "2 calls from on site, advise false alarm",
            "good codes from on site per alarm company",
            "key holder en route",
            # Street numbers (should not be confused with ages)
            "respond to 123 Main St",
            "accident at mile marker 42",
        ],
    )
    def test_operational_text_preserved(self, text: str):
        assert redact_pii(text) == text


# ---------------------------------------------------------------------------
# redact_pii — edge cases
# ---------------------------------------------------------------------------


class TestRedactEdgeCases:
    def test_empty_string(self):
        assert redact_pii("") == ""

    def test_none_returns_none(self):
        assert redact_pii(None) is None  # type: ignore[arg-type]

    def test_no_pii(self):
        text = "Engine 31 responded to fire alarm at 100 First St"
        assert redact_pii(text) == text

    def test_mixed_pii_and_operational(self):
        text = (
            "E31 on scene. 13yo female injured in rear passenger seat. "
            "Callback (360) 555-1234. BN31 has command."
        )
        expected = (
            "E31 on scene. [patient] injured in rear passenger seat. "
            "Callback [phone]. BN31 has command."
        )
        assert redact_pii(text) == expected

    def test_multiline_text(self):
        text = "13yo male stuck under car\nE31 responding\ncaller: John Smith on scene"
        expected = "[patient] stuck under car\nE31 responding\n[caller] on scene"
        assert redact_pii(text) == expected

    def test_html_decoded_apostrophes(self):
        """PII in text with decoded HTML entities."""
        text = "72yo male at patient's residence, can't walk"
        expected = "[patient] at patient's residence, can't walk"
        assert redact_pii(text) == expected

    def test_real_cad_comment_block(self):
        """Realistic multi-line CAD comment block."""
        text = (
            "18:56:01 02/02/2026 - M Rennick\n"
            "13yo female passenger, possible head injury\n"
            "18:57:30 02/02/2026 - M Rennick\n"
            "caller: John Smith says patient is conscious\n"
            "callback (360) 378-4141\n"
            "19:01:00 02/02/2026 - Dispatch\n"
            "E31 on scene, BN31 has command"
        )
        result = redact_pii(text)
        # Age+gender redacted
        assert "13yo" not in result
        assert "female" not in result
        # Caller name redacted
        assert "John Smith" not in result
        # Phone redacted
        assert "378-4141" not in result
        # Operational content preserved
        assert "M Rennick" in result  # dispatcher name (not caller/patient)
        assert "E31 on scene" in result
        assert "BN31 has command" in result
        assert "possible head injury" in result  # medical condition preserved
        assert "patient is conscious" in result


# ---------------------------------------------------------------------------
# ReDoS hardening: empirical linear-time proof + size guard
# ---------------------------------------------------------------------------


class TestRedactPiiRedos:
    """Guard against catastrophic backtracking on pathological input.

    Our regexes don't have nested quantifiers that would cause true
    ReDoS, but we keep these tests as empirical proof — if someone
    refactors the patterns in a way that introduces backtracking, the
    timing assertion will fail loudly.
    """

    _PATHOLOGICAL_INPUTS: typing.ClassVar[dict[str, str]] = {
        # Long runs of the ambiguous separator characters
        "long-whitespace-run": "13" + " " * 10_000 + "-" + " " * 10_000 + "year old male",
        "long-dash-run": "13" + "-" * 1_000 + "year" + "-" * 1_000 + "old male",
        # Long input with no match at all (worst case for naive backtracking)
        "no-match-long": "a" * 50_000,
        # Long input where the regex must fail near the end of each span
        "near-miss-year": "yea" * 10_000,
        "near-miss-yr": "yr " * 10_000,
        # Lots of real matches interleaved with noise
        "many-matches": ("13yo male " + "E31 responding " * 3) * 1_000,
        # Long phone-like string that ultimately doesn't match
        "phone-near-miss": "123-456-78" * 5_000,
        # Long caller-like prefix with no capitalized name follow-up
        "caller-near-miss": "caller: " * 5_000,
    }

    @pytest.mark.parametrize("label", list(_PATHOLOGICAL_INPUTS.keys()))
    def test_pathological_input_completes_quickly(self, label: str):
        """Every redact_pii call should be well under a second on ~50KB."""
        import time

        text = self._PATHOLOGICAL_INPUTS[label]
        start = time.perf_counter()
        result = redact_pii(text)
        elapsed = time.perf_counter() - start

        # Generous budget — real-world dispatch CAD blobs are <10KB and
        # complete in ~1ms. On 50KB pathological input we should still
        # be well under a second.  If this fails it means someone
        # introduced backtracking into a pattern.
        assert elapsed < 1.0, (
            f"redact_pii too slow on '{label}' ({elapsed:.3f}s) — "
            f"possible regex backtracking introduced"
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_very_large_input_logs_warning(self, caplog):
        """Inputs above the soft cap trigger a canary warning log."""
        import logging

        text = "a" * 200_001  # one byte over _MAX_INPUT_BYTES

        with caplog.at_level(logging.WARNING, logger="sjifire.core.pii"):
            result = redact_pii(text)

        assert any("oversized input" in rec.message for rec in caplog.records), (
            f"expected oversized-input warning, got: {[r.message for r in caplog.records]}"
        )
        # Still processed the input — no silent skip
        assert isinstance(result, str)
        assert len(result) == len(text)

    def test_input_at_cap_does_not_warn(self, caplog):
        """Exactly at the cap should not emit a warning."""
        import logging

        text = "a" * 200_000  # exactly at cap

        with caplog.at_level(logging.WARNING, logger="sjifire.core.pii"):
            redact_pii(text)

        assert not any("oversized input" in rec.message for rec in caplog.records)
