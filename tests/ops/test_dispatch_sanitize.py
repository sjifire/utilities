"""Tests for LLM-facing dispatch sanitization helpers.

Covers:

- ``sanitize_cad_comments`` — prefers LLM-sanitized field, falls back to regex
- ``sanitize_radio_notes`` — prefers LLM-sanitized notes, falls back to regex
- ``sanitize_dispatch_for_llm`` — full dict shape for MCP tool output
- End-to-end wiring via ``_prefill_from_dispatch`` (incident creation)
- End-to-end wiring via chat engine slim dict
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.ops.dispatch.models import (
    DispatchAnalysis,
    DispatchCallDocument,
    SanitizedNote,
)
from sjifire.ops.dispatch.sanitize import (
    sanitize_cad_comments,
    sanitize_dispatch_for_llm,
    sanitize_radio_notes,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_doc(**overrides) -> DispatchCallDocument:
    """Build a DispatchCallDocument with raw PII-bearing content by default."""
    defaults = {
        "id": "call-uuid-123",
        "year": "2026",
        "long_term_call_id": "26-001678",
        "nature": "Medical Aid",
        "address": "200 Spring St",
        "agency_code": "SJF",
        "type": "EMS",
        "zone_code": "Z1",
        "time_reported": datetime(2026, 2, 12, 14, 30),
        "is_completed": True,
        "cad_comments": (
            "18:56:01 02/12/2026 - M Rennick\n"
            "13yo female passenger, possible head injury\n"
            "callback (360) 378-4141\n"
            "18:57:30 02/12/2026 - M Rennick\n"
            "caller: John Smith says patient is conscious"
        ),
        "responding_units": "E31,M31",
        "responder_details": [
            {
                "unit_number": "E31",
                "agency_code": "SJF",
                "status": "NOTE",
                "time_of_status_change": "2026-02-12T14:38:00",
                "radio_log": "pt contact 13yo female, conscious and alert",
            },
            {
                "unit_number": "M31",
                "agency_code": "SJF",
                "status": "ENRT",
                "time_of_status_change": "2026-02-12T14:31:00",
                "radio_log": "M31 enroute w/2",
            },
            {
                "unit_number": "M31",
                "agency_code": "SJF",
                "status": "NOTE",
                "time_of_status_change": "2026-02-12T14:42:00",
                "radio_log": "transporting 72yo male to PeaceHealth",
            },
        ],
        "city": "Friday Harbor",
        "state": "WA",
        "zip_code": "98250",
        "geo_location": "48.5343,-123.0170",
    }
    defaults.update(overrides)
    return DispatchCallDocument(**defaults)


def _make_analysis(**overrides) -> DispatchAnalysis:
    """Build a DispatchAnalysis with LLM-sanitized fields populated."""
    defaults = {
        "incident_commander": "BN31",
        "summary": "Medical aid call at 200 Spring St, patient transported.",
        "short_dsc": "patient fall, transported",
        "key_events": ["14:38 E31 — pt contact, alert"],
        "sanitized_cad_comments": (
            "18:56:01 02/12/2026 - M Rennick\n"
            "the patient, possible head injury\n"
            "callback [phone]\n"
            "18:57:30 02/12/2026 - M Rennick\n"
            "the caller says the patient is conscious"
        ),
        "sanitized_radio_notes": [
            SanitizedNote(
                timestamp="2026-02-12T14:38:00",
                unit="E31",
                text="pt contact the patient, conscious and alert",
            ),
            SanitizedNote(
                timestamp="2026-02-12T14:42:00",
                unit="M31",
                text="transporting the patient to PeaceHealth",
            ),
        ],
    }
    defaults.update(overrides)
    return DispatchAnalysis(**defaults)


# ---------------------------------------------------------------------------
# sanitize_cad_comments
# ---------------------------------------------------------------------------


class TestSanitizeCadComments:
    def test_prefers_llm_sanitized_when_present(self):
        doc = _make_doc()
        doc.analysis = _make_analysis()

        result = sanitize_cad_comments(doc)

        assert "the patient, possible head injury" in result
        assert "the caller says the patient" in result
        # Raw PII should NOT appear
        assert "13yo" not in result
        assert "John Smith" not in result
        assert "378-4141" not in result
        # Operational content preserved
        assert "M Rennick" in result  # dispatcher name is not PII
        assert "02/12/2026" in result

    def test_falls_back_to_regex_when_no_analysis(self):
        doc = _make_doc()
        doc.analysis = DispatchAnalysis()  # empty

        result = sanitize_cad_comments(doc)

        # Regex redaction should have caught these
        assert "13yo female" not in result
        assert "(360) 378-4141" not in result
        assert "[patient]" in result
        assert "[phone]" in result
        # Dispatcher header preserved
        assert "M Rennick" in result

    def test_falls_back_to_regex_when_sanitized_empty_string(self):
        doc = _make_doc()
        doc.analysis = _make_analysis(sanitized_cad_comments="")

        result = sanitize_cad_comments(doc)

        assert "[patient]" in result  # regex path

    def test_returns_empty_when_no_cad_comments(self):
        doc = _make_doc(cad_comments="")
        doc.analysis = DispatchAnalysis()

        assert sanitize_cad_comments(doc) == ""

    def test_html_entities_decoded_in_llm_sanitized(self):
        """HTML entities in sanitized field should still be decoded."""
        doc = _make_doc()
        doc.analysis = _make_analysis(sanitized_cad_comments="caller&#x27;s phone was ringing")

        result = sanitize_cad_comments(doc)

        assert result == "caller's phone was ringing"

    def test_html_entities_decoded_in_regex_fallback(self):
        doc = _make_doc(cad_comments="patient&#x27;s door was locked")
        doc.analysis = DispatchAnalysis()

        result = sanitize_cad_comments(doc)

        assert "patient's door" in result


# ---------------------------------------------------------------------------
# sanitize_radio_notes
# ---------------------------------------------------------------------------


class TestSanitizeRadioNotes:
    def test_prefers_llm_sanitized_notes(self):
        doc = _make_doc()
        doc.analysis = _make_analysis()

        notes = sanitize_radio_notes(doc)

        assert len(notes) == 2
        assert notes[0]["timestamp"] == "2026-02-12T14:38:00"
        assert notes[0]["unit"] == "E31"
        assert "the patient" in notes[0]["text"]
        assert "13yo" not in notes[0]["text"]
        assert "72yo" not in notes[1]["text"]
        assert "PeaceHealth" in notes[1]["text"]

    def test_falls_back_to_regex_from_responder_details(self):
        doc = _make_doc()
        doc.analysis = DispatchAnalysis()  # no sanitized notes

        notes = sanitize_radio_notes(doc)

        # Should extract the 2 NOTE-status entries, skipping ENRT
        assert len(notes) == 2
        # Both should be regex-redacted
        for note in notes:
            assert "13yo" not in note["text"]
            assert "72yo" not in note["text"]
            assert "[patient]" in note["text"]
        # Unit + timestamp preserved
        units = {n["unit"] for n in notes}
        assert units == {"E31", "M31"}

    def test_fallback_skips_non_note_entries(self):
        doc = _make_doc()
        doc.analysis = DispatchAnalysis()

        notes = sanitize_radio_notes(doc)

        # ENRT entry should be skipped — only 2 NOTE entries
        assert len(notes) == 2
        assert all(n["text"] for n in notes)
        # None of the notes should be the ENRT radio_log
        assert not any("enroute w/2" in n["text"] for n in notes)

    def test_fallback_skips_empty_radio_log(self):
        doc = _make_doc(
            responder_details=[
                {
                    "unit_number": "E31",
                    "status": "NOTE",
                    "time_of_status_change": "2026-02-12T14:38:00",
                    "radio_log": "",
                },
                {
                    "unit_number": "M31",
                    "status": "NOTE",
                    "time_of_status_change": "2026-02-12T14:42:00",
                    "radio_log": "   ",
                },
            ]
        )
        doc.analysis = DispatchAnalysis()

        assert sanitize_radio_notes(doc) == []

    def test_empty_sanitized_notes_list_falls_back(self):
        doc = _make_doc()
        doc.analysis = _make_analysis(sanitized_radio_notes=[])

        notes = sanitize_radio_notes(doc)

        # Falls back to regex path because the sanitized list is empty
        assert len(notes) == 2  # 2 NOTE entries from raw
        assert "[patient]" in notes[0]["text"]

    def test_empty_text_sanitized_notes_skipped(self):
        """Sanitized notes with empty text should not appear in output."""
        doc = _make_doc()
        doc.analysis = _make_analysis(
            sanitized_radio_notes=[
                SanitizedNote(timestamp="2026-02-12T14:38:00", unit="E31", text=""),
                SanitizedNote(
                    timestamp="2026-02-12T14:42:00",
                    unit="M31",
                    text="transporting the patient",
                ),
            ]
        )

        notes = sanitize_radio_notes(doc)

        assert len(notes) == 1
        assert notes[0]["unit"] == "M31"

    def test_html_entities_decoded(self):
        doc = _make_doc()
        doc.analysis = _make_analysis(
            sanitized_radio_notes=[
                SanitizedNote(
                    timestamp="2026-02-12T14:38:00",
                    unit="E31",
                    text="caller&#x27;s report",
                ),
            ]
        )

        notes = sanitize_radio_notes(doc)

        assert notes[0]["text"] == "caller's report"

    def test_no_responder_details(self):
        doc = _make_doc(responder_details=[])
        doc.analysis = DispatchAnalysis()

        assert sanitize_radio_notes(doc) == []


# ---------------------------------------------------------------------------
# sanitize_dispatch_for_llm
# ---------------------------------------------------------------------------


class TestSanitizeDispatchForLlm:
    def test_uses_sanitized_cad_comments(self):
        doc = _make_doc()
        doc.analysis = _make_analysis()

        result = sanitize_dispatch_for_llm(doc)

        assert "13yo" not in result["cad_comments"]
        assert "378-4141" not in result["cad_comments"]
        assert "the patient" in result["cad_comments"]

    def test_matches_sanitized_notes_by_timestamp_and_unit(self):
        doc = _make_doc()
        doc.analysis = _make_analysis()

        result = sanitize_dispatch_for_llm(doc)

        # NOTE entries should have sanitized radio_log text
        note_entries = [e for e in result["responder_details"] if e.get("status") == "NOTE"]
        assert len(note_entries) == 2
        for entry in note_entries:
            assert "13yo" not in entry["radio_log"]
            assert "72yo" not in entry["radio_log"]
            assert "the patient" in entry["radio_log"]

    def test_non_note_entries_untouched(self):
        doc = _make_doc()
        doc.analysis = _make_analysis()

        result = sanitize_dispatch_for_llm(doc)

        # ENRT entry should be unchanged — no radio_log rewrite
        enrt = next(e for e in result["responder_details"] if e.get("status") == "ENRT")
        assert enrt["radio_log"] == "M31 enroute w/2"

    def test_falls_back_to_regex_for_unmatched_note(self):
        """NOTE entries with no sanitized match should be regex-redacted."""
        doc = _make_doc()
        # Only sanitize one of the two notes; the other should fall back
        doc.analysis = _make_analysis(
            sanitized_radio_notes=[
                SanitizedNote(
                    timestamp="2026-02-12T14:38:00",
                    unit="E31",
                    text="pt contact the patient, alert",
                ),
                # Note for M31 at 14:42 intentionally missing
            ]
        )

        result = sanitize_dispatch_for_llm(doc)

        notes_by_unit = {
            e["unit_number"]: e["radio_log"]
            for e in result["responder_details"]
            if e.get("status") == "NOTE"
        }
        # E31: LLM-sanitized
        assert "the patient, alert" in notes_by_unit["E31"]
        # M31: regex fallback
        assert "72yo" not in notes_by_unit["M31"]
        assert "[patient]" in notes_by_unit["M31"]
        assert "PeaceHealth" in notes_by_unit["M31"]

    def test_fully_unenriched_doc_falls_back_to_regex(self):
        doc = _make_doc()
        doc.analysis = DispatchAnalysis()  # fully empty

        result = sanitize_dispatch_for_llm(doc)

        # cad_comments: regex path
        assert "13yo" not in result["cad_comments"]
        assert "[patient]" in result["cad_comments"]
        assert "[phone]" in result["cad_comments"]
        # NOTE entries: regex path
        note_entries = [e for e in result["responder_details"] if e.get("status") == "NOTE"]
        for entry in note_entries:
            assert "13yo" not in entry["radio_log"]
            assert "72yo" not in entry["radio_log"]

    def test_analysis_safety_net_redacts_summary_fields(self):
        """Safety net: regex still scrubs summary/short_dsc/key_events.

        Even if the enrichment LLM slips and leaves PII in its own output,
        the regex pass should catch it.
        """
        doc = _make_doc()
        doc.analysis = _make_analysis(
            summary="13yo female fell from bike",
            short_dsc="13yo fall",
            key_events=["14:38 E31 — 72yo male pt contact"],
        )

        result = sanitize_dispatch_for_llm(doc)

        analysis = result["analysis"]
        assert "13yo" not in analysis["summary"]
        assert "[patient]" in analysis["summary"]
        assert "13yo" not in analysis["short_dsc"]
        assert "72yo" not in analysis["key_events"][0]

    def test_does_not_mutate_source_doc(self):
        doc = _make_doc()
        doc.analysis = _make_analysis()
        original_cad = doc.cad_comments
        original_radio = doc.responder_details[0]["radio_log"]

        sanitize_dispatch_for_llm(doc)

        assert doc.cad_comments == original_cad
        assert doc.responder_details[0]["radio_log"] == original_radio

    def test_result_has_same_top_level_shape_as_to_dict(self):
        """Claude expects the same keys as the raw dispatch dict."""
        doc = _make_doc()
        doc.analysis = _make_analysis()

        raw = doc.to_dict()
        sanitized = sanitize_dispatch_for_llm(doc)

        assert set(sanitized.keys()) == set(raw.keys())

    def test_preserves_address_and_nature(self):
        """Address is not PII — must pass through untouched."""
        doc = _make_doc()
        doc.analysis = _make_analysis()

        result = sanitize_dispatch_for_llm(doc)

        assert result["address"] == "200 Spring St"
        assert result["city"] == "Friday Harbor"
        assert result["nature"] == "Medical Aid"


# ---------------------------------------------------------------------------
# End-to-end: _prefill_from_dispatch uses sanitized fields
# ---------------------------------------------------------------------------


class TestPrefillFromDispatchSanitization:
    @pytest.fixture(autouse=True)
    def _clear_store(self):
        from sjifire.ops.dispatch.store import DispatchStore

        DispatchStore._memory.clear()
        yield
        DispatchStore._memory.clear()

    async def test_prefill_uses_sanitized_cad_when_available(self):
        from sjifire.ops.dispatch.store import DispatchStore
        from sjifire.ops.incidents.tools import _prefill_from_dispatch

        doc = _make_doc()
        doc.analysis = _make_analysis()

        with patch.object(DispatchStore, "get_by_dispatch_id", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = doc
            result = await _prefill_from_dispatch("26-001678")

        # dispatch_comments: from LLM sanitized
        assert "13yo" not in result["dispatch_comments"]
        assert "378-4141" not in result["dispatch_comments"]
        assert "the patient" in result["dispatch_comments"]

        # dispatch_notes: should contain sanitized content, no raw PII
        notes = result["dispatch_notes"]
        assert notes  # non-empty
        combined = " ".join(n.text for n in notes)
        assert "13yo" not in combined
        assert "72yo" not in combined
        assert "John Smith" not in combined
        # Operational content preserved
        assert "PeaceHealth" in combined or "conscious" in combined

    async def test_prefill_falls_back_to_regex_when_unenriched(self):
        from sjifire.ops.dispatch.store import DispatchStore
        from sjifire.ops.incidents.tools import _prefill_from_dispatch

        doc = _make_doc()
        doc.analysis = DispatchAnalysis()  # un-enriched

        with patch.object(DispatchStore, "get_by_dispatch_id", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = doc
            result = await _prefill_from_dispatch("26-001678")

        # Still no raw PII — regex caught it
        assert "13yo" not in result["dispatch_comments"]
        assert "[patient]" in result["dispatch_comments"]

        notes = result.get("dispatch_notes", [])
        assert notes
        combined = " ".join(n.text for n in notes)
        assert "13yo" not in combined
        assert "72yo" not in combined
        assert "[patient]" in combined

    async def test_prefill_preserves_address_untouched(self):
        from sjifire.ops.dispatch.store import DispatchStore
        from sjifire.ops.incidents.tools import _prefill_from_dispatch

        doc = _make_doc()
        doc.analysis = _make_analysis()

        with patch.object(DispatchStore, "get_by_dispatch_id", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = doc
            result = await _prefill_from_dispatch("26-001678")

        assert result["address"] == "200 Spring St"
        assert result["city"] == "Friday Harbor"


# ---------------------------------------------------------------------------
# Human-facing surfaces (dashboard, chat sidebar) must still see RAW
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# End-to-end: MCP dispatch tools use sanitized output
# ---------------------------------------------------------------------------


class TestDispatchMcpToolsSanitization:
    @pytest.fixture(autouse=True)
    def _clear_store(self):
        from sjifire.ops.dispatch.store import DispatchStore

        DispatchStore._memory.clear()
        yield
        DispatchStore._memory.clear()

    @pytest.fixture
    def auth_user(self):
        from sjifire.ops.auth import UserContext, set_current_user

        user = UserContext(email="ff@sjifire.org", name="FF", user_id="u1")
        set_current_user(user)
        yield user
        set_current_user(None)

    async def test_get_dispatch_call_returns_sanitized(self, auth_user):
        from sjifire.ops.dispatch.store import DispatchStore
        from sjifire.ops.dispatch.tools import get_dispatch_call

        doc = _make_doc()
        doc.analysis = _make_analysis()

        with patch.object(DispatchStore, "get_or_fetch", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = doc
            with patch.object(
                DispatchStore, "list_by_address", new_callable=AsyncMock
            ) as mock_hist:
                mock_hist.return_value = []
                result = await get_dispatch_call("26-001678")

        # cad_comments is sanitized
        assert "13yo" not in result["cad_comments"]
        assert "378-4141" not in result["cad_comments"]
        assert "the patient" in result["cad_comments"]

        # NOTE entries sanitized
        note_entries = [e for e in result["responder_details"] if e.get("status") == "NOTE"]
        for entry in note_entries:
            assert "13yo" not in entry["radio_log"]
            assert "72yo" not in entry["radio_log"]

    async def test_list_dispatch_calls_returns_sanitized(self, auth_user):
        from sjifire.ops.dispatch.store import DispatchStore
        from sjifire.ops.dispatch.tools import list_dispatch_calls

        doc = _make_doc()
        doc.analysis = _make_analysis()

        with patch.object(
            DispatchStore, "list_recent_with_open", new_callable=AsyncMock
        ) as mock_list:
            mock_list.return_value = [doc]
            result = await list_dispatch_calls(days=7)

        assert result["count"] == 1
        call = result["calls"][0]
        assert "13yo" not in call["cad_comments"]
        assert "the patient" in call["cad_comments"]

    async def test_search_by_dispatch_id_returns_sanitized(self, auth_user):
        from sjifire.ops.dispatch.store import DispatchStore
        from sjifire.ops.dispatch.tools import search_dispatch_calls

        doc = _make_doc()
        doc.analysis = _make_analysis()

        with patch.object(DispatchStore, "get_by_dispatch_id", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = doc
            result = await search_dispatch_calls(dispatch_id="26-001678")

        assert result["count"] == 1
        assert "13yo" not in result["calls"][0]["cad_comments"]


# ---------------------------------------------------------------------------
# Human-facing surfaces (dashboard, chat sidebar) must still see RAW
# ---------------------------------------------------------------------------


class TestHumanFacingSurfacesStayRaw:
    """Regression guard for the raw-vs-sanitized policy split.

    Humans see raw; Claude sees sanitized. This test fails loudly if
    someone accidentally wires sanitization into the dashboard or chat
    sidebar.
    """

    def test_dispatch_call_document_to_dict_is_raw(self):
        """``doc.to_dict()`` must return raw, unsanitized data.

        It's called by dashboard + kiosk + chat sidebar — sanitization
        is the caller's explicit choice, never the default.
        """
        doc = _make_doc()
        doc.analysis = _make_analysis()

        raw = doc.to_dict()

        # Raw PII should still be in to_dict output
        assert "13yo female" in raw["cad_comments"]
        assert "(360) 378-4141" in raw["cad_comments"]
        assert "John Smith" in raw["cad_comments"]

        # Raw radio_log in NOTE entries
        e31_note = next(
            e
            for e in raw["responder_details"]
            if e.get("status") == "NOTE" and e.get("unit_number") == "E31"
        )
        assert "13yo female" in e31_note["radio_log"]

    def test_sanitize_does_not_mutate_doc(self):
        """Sanitization must not leak into subsequent ``to_dict()`` calls.

        After ``sanitize_dispatch_for_llm``, the next caller of
        ``to_dict()`` should still see raw data.
        """
        doc = _make_doc()
        doc.analysis = _make_analysis()

        _sanitized = sanitize_dispatch_for_llm(doc)
        raw_after = doc.to_dict()

        assert "13yo female" in raw_after["cad_comments"]
