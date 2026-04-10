"""Tests for PII redaction in dispatch logs.

Covers redact_pii() text redaction and redact_dispatch_dict() structural
redaction for dispatch call dicts.
"""

import pytest

from sjifire.core.pii import redact_dispatch_dict, redact_pii

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
# redact_dispatch_dict — structural redaction
# ---------------------------------------------------------------------------


class TestRedactDispatchDict:
    def test_redacts_cad_comments(self):
        d = {"cad_comments": "13yo female injured, callback (360) 555-1234"}
        result = redact_dispatch_dict(d)
        assert result["cad_comments"] == "[patient] injured, callback [phone]"

    def test_redacts_responder_radio_log(self):
        d = {
            "responder_details": [
                {"unit_number": "E31", "status": "NOTE", "radio_log": "72yo male fell"},
                {"unit_number": "M31", "status": "ENRT", "radio_log": "M31 enroute"},
            ]
        }
        result = redact_dispatch_dict(d)
        assert result["responder_details"][0]["radio_log"] == "[patient] fell"
        assert result["responder_details"][1]["radio_log"] == "M31 enroute"

    def test_redacts_analysis_summary(self):
        d = {
            "analysis": {
                "summary": "72-year-old male fell from standing position",
                "short_dsc": "72yo male fall, transported",
                "key_events": [
                    "17:05 M31 — pt contact, 13yo female, conscious",
                    "17:10 BN31 — nothing showing, investigating",
                ],
            }
        }
        result = redact_dispatch_dict(d)
        assert "72-year-old" not in result["analysis"]["summary"]
        assert "[patient]" in result["analysis"]["summary"]
        assert "72yo" not in result["analysis"]["short_dsc"]
        assert "13yo" not in result["analysis"]["key_events"][0]
        # Operational content preserved
        assert "nothing showing" in result["analysis"]["key_events"][1]

    def test_handles_empty_fields(self):
        d = {
            "cad_comments": "",
            "responder_details": [],
            "analysis": {"summary": "", "short_dsc": "", "key_events": []},
        }
        result = redact_dispatch_dict(d)
        assert result["cad_comments"] == ""
        assert result["analysis"]["key_events"] == []

    def test_handles_missing_fields(self):
        d = {"nature": "Fire Alarm", "address": "100 First St"}
        result = redact_dispatch_dict(d)
        assert result == {"nature": "Fire Alarm", "address": "100 First St"}

    def test_handles_no_analysis(self):
        d = {"cad_comments": "test", "analysis": None}
        result = redact_dispatch_dict(d)
        assert result["analysis"] is None

    def test_modifies_in_place(self):
        d = {"cad_comments": "13yo female injured"}
        result = redact_dispatch_dict(d)
        assert result is d  # same object
        assert d["cad_comments"] == "[patient] injured"

    def test_full_dispatch_dict(self):
        """Test with a realistic full dispatch dict structure."""
        d = {
            "id": "call-uuid-123",
            "long_term_call_id": "26-001678",
            "nature": "Medical Aid",
            "address": "200 Spring St",
            "city": "Friday Harbor",
            "state": "WA",
            "time_reported": "2026-02-12T14:30:00",
            "geo_location": "48.5343,-123.0170",
            "cad_comments": "55 y/o male, chest pain, callback (360) 378-5141",
            "responding_units": "E31,M31",
            "responder_details": [
                {
                    "unit_number": "E31",
                    "status": "NOTE",
                    "radio_log": "pt contact, 55yo male, conscious and alert",
                    "time_of_status_change": "2026-02-12T14:38:00",
                },
                {
                    "unit_number": "M31",
                    "status": "ENRT",
                    "radio_log": "M31 enroute w/2",
                    "time_of_status_change": "2026-02-12T14:31:00",
                },
            ],
            "analysis": {
                "incident_commander": "BN31",
                "summary": "55 year old male with chest pain at 200 Spring St",
                "short_dsc": "55yo male chest pain, transported",
                "key_events": [
                    "14:38 E31 — pt contact, 55yo male, alert",
                    "14:42 M31 — BLS initiated",
                    "14:50 M31 — transporting to PeaceHealth",
                ],
                "patient_count": 1,
                "outcome": "transported",
            },
        }
        result = redact_dispatch_dict(d)

        # PII stripped
        assert "55 y/o" not in result["cad_comments"]
        assert "55yo" not in result["cad_comments"]
        assert "378-5141" not in result["cad_comments"]
        assert "55yo" not in result["responder_details"][0]["radio_log"]
        assert "55 year old" not in result["analysis"]["summary"]
        assert "55yo" not in result["analysis"]["short_dsc"]
        assert "55yo" not in result["analysis"]["key_events"][0]

        # Operational content preserved
        assert result["address"] == "200 Spring St"
        assert result["nature"] == "Medical Aid"
        assert "chest pain" in result["cad_comments"]
        assert "conscious and alert" in result["responder_details"][0]["radio_log"]
        assert result["responder_details"][1]["radio_log"] == "M31 enroute w/2"
        assert "BLS initiated" in result["analysis"]["key_events"][1]
        assert "transporting to PeaceHealth" in result["analysis"]["key_events"][2]
        assert result["analysis"]["patient_count"] == 1
