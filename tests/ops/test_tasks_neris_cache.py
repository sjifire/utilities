"""Tests for the NERIS cache refresh task."""

import os
from unittest.mock import MagicMock, patch

import pytest

from sjifire.ops.neris.store import NerisReportStore
from sjifire.ops.tasks.neris_cache import (
    fetch_neris_summaries,
    neris_cache_refresh,
    refresh_neris_report_cache,
)


@pytest.fixture(autouse=True)
def _env():
    """Ensure in-memory mode for Cosmos."""
    with patch.dict(os.environ, {"COSMOS_ENDPOINT": "", "COSMOS_KEY": ""}, clear=False):
        yield
    NerisReportStore._memory.clear()


# Sample NERIS API response matching the real shape
_SAMPLE_INCIDENTS = [
    {
        "neris_id": "FD53055879|26001980|123",
        "dispatch": {
            "incident_number": "26-001980",
            "determinant_code": None,
            "call_create": "2026-02-09T06:07:17+00:00",
        },
        "incident_types": [{"type": "MEDICAL||INJURY||MOTOR_VEHICLE_COLLISION"}],
        "incident_status": {"status": "PENDING_APPROVAL"},
    },
    {
        "neris_id": "FD53055879|26SJ0020|456",
        "dispatch": {
            "incident_number": "26SJ0020",
            "determinant_code": "26002059",
            "call_create": "2026-02-07T09:45:54+00:00",
        },
        "incident_types": [{"type": "PUBSERV||ALARMS_NONMED||FIRE_ALARM"}],
        "incident_status": {"status": "APPROVED"},
    },
    {
        "neris_id": "FD53055879|26000039|789",
        "dispatch": {
            "incident_number": "26-000039",
            "determinant_code": None,
            "call_create": "2026-01-02T01:12:41+00:00",
        },
        "incident_types": [],
        "incident_status": {"status": "PENDING_APPROVAL"},
    },
]


class TestFetchNerisSummaries:
    @patch("sjifire.neris.client.NerisClient")
    def test_extracts_summaries(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.get_all_incidents.return_value = _SAMPLE_INCIDENTS
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        summaries = fetch_neris_summaries()

        assert len(summaries) == 3

        # First incident
        s0 = summaries[0]
        assert s0["neris_id"] == "FD53055879|26001980|123"
        assert s0["incident_number"] == "26-001980"
        assert s0["determinant_code"] == ""
        assert s0["status"] == "PENDING_APPROVAL"
        assert s0["incident_type"] == "MEDICAL||INJURY||MOTOR_VEHICLE_COLLISION"
        assert s0["source"] == "neris"

        # Second with determinant_code
        s1 = summaries[1]
        assert s1["determinant_code"] == "26002059"
        assert s1["status"] == "APPROVED"

        # Third with empty incident_types
        s2 = summaries[2]
        assert s2["incident_type"] == ""

    @patch("sjifire.neris.client.NerisClient")
    def test_empty_api_response(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.get_all_incidents.return_value = []
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        summaries = fetch_neris_summaries()
        assert summaries == []


class TestRefreshNerisReportCache:
    async def test_writes_to_store(self):
        summaries = [
            {
                "source": "neris",
                "neris_id": "FD|26001980|123",
                "incident_number": "26-001980",
                "determinant_code": "",
                "status": "APPROVED",
                "incident_type": "MEDICAL",
                "call_create": "2026-02-09T06:07:17+00:00",
            },
            {
                "source": "neris",
                "neris_id": "FD|26SJ0020|456",
                "incident_number": "26SJ0020",
                "determinant_code": "",
                "status": "PENDING_APPROVAL",
                "incident_type": "FIRE",
                "call_create": "2026-02-07T09:45:54+00:00",
            },
        ]

        count = await refresh_neris_report_cache(summaries)
        assert count == 2

        # Verify data in store
        async with NerisReportStore() as store:
            result = await store.list_as_lookup()

        assert len(result["reports"]) == 2
        assert "26001980" in result["lookup"]

    async def test_empty_summaries(self):
        count = await refresh_neris_report_cache([])
        assert count == 0


class TestNerisCacheRefresh:
    @patch("sjifire.ops.tasks.neris_cache.fetch_neris_summaries")
    async def test_orchestrates_fetch_and_cache(self, mock_fetch):
        mock_fetch.return_value = [
            {
                "source": "neris",
                "neris_id": "FD|26001980|123",
                "incident_number": "26-001980",
                "determinant_code": "",
                "status": "APPROVED",
                "incident_type": "MEDICAL",
                "call_create": "2026-02-09T06:07:17+00:00",
            },
        ]

        count = await neris_cache_refresh()

        assert count == 1
        mock_fetch.assert_called_once()

        # Verify cache was populated
        async with NerisReportStore() as store:
            result = await store.list_as_lookup()
        assert len(result["reports"]) == 1
