"""Tests for dispatch call document models."""

from datetime import datetime

from sjifire.ispyfire.models import DispatchCall, UnitResponse
from sjifire.mcp.dispatch.models import (
    DispatchCallDocument,
    _extract_year,
    year_from_dispatch_id,
)


class TestExtractYear:
    def test_from_time_reported(self):
        assert _extract_year(datetime(2026, 2, 12, 14, 30), "26-001678") == "2026"

    def test_from_dispatch_id_prefix(self):
        assert _extract_year(None, "26-001678") == "2026"

    def test_dispatch_id_prefix_25(self):
        assert _extract_year(None, "25-000001") == "2025"

    def test_fallback_to_current_year(self):
        result = _extract_year(None, "")
        assert len(result) == 4
        assert result.isdigit()

    def test_time_reported_takes_precedence(self):
        assert _extract_year(datetime(2025, 12, 31, 23, 59), "26-000001") == "2025"


class TestYearFromDispatchId:
    def test_valid_dispatch_id(self):
        assert year_from_dispatch_id("26-001678") == "2026"

    def test_year_25(self):
        assert year_from_dispatch_id("25-000100") == "2025"

    def test_invalid_no_dash(self):
        assert year_from_dispatch_id("some-uuid-string") is None

    def test_invalid_prefix_too_long(self):
        assert year_from_dispatch_id("2026-001678") is None

    def test_empty_string(self):
        assert year_from_dispatch_id("") is None

    def test_none_like(self):
        assert year_from_dispatch_id("") is None


def _make_dispatch_call(**overrides):
    """Helper to create a DispatchCall with sensible defaults."""
    defaults = {
        "id": "call-uuid-123",
        "long_term_call_id": "26-001678",
        "nature": "Medical Aid",
        "address": "200 Spring St",
        "agency_code": "SJF",
        "type": "EMS",
        "zone_code": "Z1",
        "time_reported": datetime(2026, 2, 12, 14, 30),
        "is_completed": True,
        "cad_comments": "Patient fall",
        "responding_units": "E31,M31",
        "responder_details": [
            UnitResponse(
                unit_number="E31",
                agency_code="SJF",
                status="Dispatched",
                time_of_status_change=datetime(2026, 2, 12, 14, 30, 15),
            ),
        ],
        "city": "Friday Harbor",
        "state": "WA",
        "zip_code": "98250",
        "geo_location": "48.5343,-123.0170",
        "created_timestamp": 1739388600,
    }
    defaults.update(overrides)
    return DispatchCall(**defaults)


class TestFromDispatchCall:
    def test_basic_conversion(self):
        call = _make_dispatch_call()
        doc = DispatchCallDocument.from_dispatch_call(call)

        assert doc.id == "call-uuid-123"
        assert doc.long_term_call_id == "26-001678"
        assert doc.year == "2026"
        assert doc.nature == "Medical Aid"
        assert doc.address == "200 Spring St"
        assert doc.is_completed is True

    def test_year_extracted_from_time_reported(self):
        call = _make_dispatch_call(time_reported=datetime(2025, 6, 15, 10, 0))
        doc = DispatchCallDocument.from_dispatch_call(call)
        assert doc.year == "2025"

    def test_responder_details_as_dicts(self):
        call = _make_dispatch_call()
        doc = DispatchCallDocument.from_dispatch_call(call)
        assert isinstance(doc.responder_details[0], dict)
        assert doc.responder_details[0]["unit_number"] == "E31"
        # datetime should be serialized to ISO string in nested dicts
        assert isinstance(doc.responder_details[0]["time_of_status_change"], str)

    def test_stored_at_set(self):
        call = _make_dispatch_call()
        doc = DispatchCallDocument.from_dispatch_call(call)
        assert doc.stored_at is not None

    def test_renamed_fields(self):
        call = _make_dispatch_call()
        doc = DispatchCallDocument.from_dispatch_call(call)
        assert doc.cad_comments == "Patient fall"
        assert doc.responding_units == "E31,M31"


class TestCosmosRoundtrip:
    def test_roundtrip(self):
        call = _make_dispatch_call()
        doc = DispatchCallDocument.from_dispatch_call(call)

        cosmos_data = doc.to_cosmos()
        restored = DispatchCallDocument.from_cosmos(cosmos_data)

        assert restored.id == doc.id
        assert restored.year == doc.year
        assert restored.long_term_call_id == doc.long_term_call_id
        assert restored.nature == doc.nature
        assert restored.is_completed == doc.is_completed
        assert restored.time_reported == doc.time_reported
        assert len(restored.responder_details) == 1

    def test_to_cosmos_includes_partition_key(self):
        call = _make_dispatch_call()
        doc = DispatchCallDocument.from_dispatch_call(call)
        cosmos = doc.to_cosmos()
        assert cosmos["year"] == "2026"
        assert cosmos["id"] == "call-uuid-123"

    def test_time_reported_serialized_as_iso(self):
        call = _make_dispatch_call()
        doc = DispatchCallDocument.from_dispatch_call(call)
        cosmos = doc.to_cosmos()
        # Pydantic model_dump(mode="json") serializes datetime as ISO string
        assert isinstance(cosmos["time_reported"], str)
        assert cosmos["time_reported"].startswith("2026-02-12")


class TestToDict:
    def test_strips_cosmos_fields(self):
        call = _make_dispatch_call()
        doc = DispatchCallDocument.from_dispatch_call(call)

        d = doc.to_dict()

        # Should not have Cosmos-only fields
        assert "year" not in d
        assert "stored_at" not in d

        # Should have all original DispatchCall fields
        assert d["id"] == "call-uuid-123"
        assert d["long_term_call_id"] == "26-001678"
        assert d["nature"] == "Medical Aid"
        assert d["is_completed"] is True

    def test_preserves_all_dispatch_fields(self):
        call = _make_dispatch_call()
        doc = DispatchCallDocument.from_dispatch_call(call)
        d = doc.to_dict()

        assert d["city"] == "Friday Harbor"
        assert d["state"] == "WA"
        assert d["zip_code"] == "98250"
        assert d["geo_location"] == "48.5343,-123.0170"
        assert d["type"] == "EMS"
        assert d["zone_code"] == "Z1"
        assert d["cad_comments"] == "Patient fall"
        assert d["responding_units"] == "E31,M31"
