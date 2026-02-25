"""Tests for the NERIS report sync task."""

import os
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from sjifire.ops.incidents.models import IncidentDocument
from sjifire.ops.incidents.store import IncidentStore
from sjifire.ops.neris.store import NerisReportStore
from sjifire.ops.tasks.neris_sync import (
    _sync_neris_to_local,
    fetch_neris_summaries,
    neris_sync,
    refresh_neris_report_cache,
)


@pytest.fixture(autouse=True)
def _env():
    """Ensure in-memory mode for Cosmos."""
    with patch.dict(os.environ, {"COSMOS_ENDPOINT": "", "COSMOS_KEY": ""}, clear=False):
        yield
    NerisReportStore._memory.clear()
    IncidentStore._memory.clear()


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

    @patch("sjifire.neris.client.NerisClient")
    def test_full_fetch_without_since(self, mock_client_cls):
        """Without since, calls get_all_incidents for full fetch."""
        mock_client = MagicMock()
        mock_client.get_all_incidents.return_value = []
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        fetch_neris_summaries()

        mock_client.get_all_incidents.assert_called_once_with()

    @patch("sjifire.neris.client.NerisClient")
    def test_incremental_fetch_with_since(self, mock_client_cls):
        """With since, pages by last_modified descending and filters client-side."""
        mock_client = MagicMock()
        mock_client.list_incidents.return_value = {
            "incidents": [
                {**_SAMPLE_INCIDENTS[0], "last_modified": "2026-02-20T00:00:00+00:00"},
                {**_SAMPLE_INCIDENTS[1], "last_modified": "2026-02-10T00:00:00+00:00"},
            ],
            "next_cursor": None,
        }
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        # Only the first incident is newer than the cutoff
        summaries = fetch_neris_summaries(since="2026-02-15T00:00:00+00:00")

        assert len(summaries) == 1
        assert summaries[0]["neris_id"] == "FD53055879|26001980|123"
        mock_client.list_incidents.assert_called_once_with(
            page_size=100,
            cursor=None,
            sort_by="last_modified",
            sort_direction="DESCENDING",
        )

    @patch("sjifire.neris.client.NerisClient")
    def test_incremental_stops_on_old_page(self, mock_client_cls):
        """Stops paginating when an entire page is older than the checkpoint."""
        mock_client = MagicMock()
        mock_client.list_incidents.side_effect = [
            {
                "incidents": [
                    {**_SAMPLE_INCIDENTS[0], "last_modified": "2026-02-20T00:00:00+00:00"},
                ],
                "next_cursor": "page2",
            },
            {
                "incidents": [
                    {**_SAMPLE_INCIDENTS[1], "last_modified": "2026-02-01T00:00:00+00:00"},
                ],
                "next_cursor": "page3",
            },
        ]
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        summaries = fetch_neris_summaries(since="2026-02-15T00:00:00+00:00")

        # Got 1 from first page, stopped at second (all old)
        assert len(summaries) == 1
        # Should NOT have fetched page 3
        assert mock_client.list_incidents.call_count == 2


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


class TestSyncNerisToLocal:
    """Tests for _sync_neris_to_local status transitions."""

    async def test_transitions_submitted_to_approved(self):
        """Submitted + NERIS APPROVED → local approved."""
        incident = IncidentDocument(
            id="inc-1",
            incident_number="26-001980",
            incident_datetime=datetime(2026, 2, 9, tzinfo=UTC),
            created_by="ff@sjifire.org",
            status="submitted",
            neris_incident_id="FD|26001980|123",
            station="S31",
        )
        async with IncidentStore() as store:
            await store.create(incident)

        summaries = [
            {
                "neris_id": "FD|26001980|123",
                "status": "APPROVED",
                "incident_number": "26-001980",
            }
        ]
        count = await _sync_neris_to_local(summaries)

        assert count == 1
        async with IncidentStore() as store:
            updated = await store.get_by_id("inc-1")
        assert updated.status == "approved"
        assert updated.edit_history[-1].editor_email == "system@sjifire.org"
        assert updated.edit_history[-1].editor_name == "NERIS Sync"
        assert "status:approved" in updated.edit_history[-1].fields_changed

    async def test_skips_draft_incidents(self):
        """Draft incidents are not transitioned even if NERIS is APPROVED."""
        incident = IncidentDocument(
            id="inc-draft",
            incident_number="26-002000",
            incident_datetime=datetime(2026, 2, 10, tzinfo=UTC),
            created_by="ff@sjifire.org",
            status="draft",
            neris_incident_id="FD|26002000|999",
            station="S31",
        )
        async with IncidentStore() as store:
            await store.create(incident)

        summaries = [
            {
                "neris_id": "FD|26002000|999",
                "status": "APPROVED",
                "incident_number": "26-002000",
            }
        ]
        count = await _sync_neris_to_local(summaries)

        assert count == 0
        async with IncidentStore() as store:
            doc = await store.get_by_id("inc-draft")
        assert doc.status == "draft"

    async def test_skips_already_approved(self):
        """Already-approved incidents are not re-transitioned."""
        incident = IncidentDocument(
            id="inc-appr",
            incident_number="26-003000",
            incident_datetime=datetime(2026, 2, 11, tzinfo=UTC),
            created_by="ff@sjifire.org",
            status="approved",
            neris_incident_id="FD|26003000|111",
            station="S31",
        )
        async with IncidentStore() as store:
            await store.create(incident)

        summaries = [
            {
                "neris_id": "FD|26003000|111",
                "status": "APPROVED",
                "incident_number": "26-003000",
            }
        ]
        count = await _sync_neris_to_local(summaries)

        assert count == 0

    async def test_skips_pending_neris_status(self):
        """Submitted + NERIS PENDING_APPROVAL → no change."""
        incident = IncidentDocument(
            id="inc-pending",
            incident_number="26-004000",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            status="submitted",
            neris_incident_id="FD|26004000|222",
            station="S31",
        )
        async with IncidentStore() as store:
            await store.create(incident)

        summaries = [
            {
                "neris_id": "FD|26004000|222",
                "status": "PENDING_APPROVAL",
                "incident_number": "26-004000",
            }
        ]
        count = await _sync_neris_to_local(summaries)

        assert count == 0
        async with IncidentStore() as store:
            doc = await store.get_by_id("inc-pending")
        assert doc.status == "submitted"

    async def test_no_matching_local_incident(self):
        """NERIS summary with no local match is silently skipped."""
        summaries = [
            {
                "neris_id": "FD|26005000|333",
                "status": "APPROVED",
                "incident_number": "26-005000",
            }
        ]
        count = await _sync_neris_to_local(summaries)
        assert count == 0


class TestCheckpoint:
    """Tests for sync checkpoint (high-water mark)."""

    async def test_get_checkpoint_returns_none_initially(self):
        async with NerisReportStore() as store:
            checkpoint = await store.get_sync_checkpoint()
        assert checkpoint is None

    async def test_set_and_get_checkpoint(self):
        ts = "2026-02-15T10:30:00+00:00"
        async with NerisReportStore() as store:
            await store.set_sync_checkpoint(ts)
        async with NerisReportStore() as store:
            checkpoint = await store.get_sync_checkpoint()
        assert checkpoint == ts

    async def test_checkpoint_overwrite(self):
        async with NerisReportStore() as store:
            await store.set_sync_checkpoint("2026-02-14T00:00:00+00:00")
            await store.set_sync_checkpoint("2026-02-15T00:00:00+00:00")
            checkpoint = await store.get_sync_checkpoint()
        assert checkpoint == "2026-02-15T00:00:00+00:00"


class TestNerisSync:
    @patch("sjifire.ops.tasks.neris_sync.fetch_neris_summaries")
    async def test_orchestrates_fetch_and_sync(self, mock_fetch):
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

        count = await neris_sync()

        assert count == 1
        mock_fetch.assert_called_once()

        # Verify store was populated
        async with NerisReportStore() as store:
            result = await store.list_as_lookup()
        assert len(result["reports"]) == 1

    @patch("sjifire.ops.tasks.neris_sync.fetch_neris_summaries")
    async def test_first_sync_full_fetch(self, mock_fetch):
        """First sync (no checkpoint) passes since=None."""
        mock_fetch.return_value = []

        await neris_sync()

        mock_fetch.assert_called_once_with(since=None)

    @patch("sjifire.ops.tasks.neris_sync.fetch_neris_summaries")
    async def test_incremental_sync_passes_checkpoint(self, mock_fetch):
        """Subsequent sync passes stored checkpoint as since."""
        mock_fetch.return_value = []

        async with NerisReportStore() as store:
            await store.set_sync_checkpoint("2026-02-15T10:30:00+00:00")

        await neris_sync()

        mock_fetch.assert_called_once_with(since="2026-02-15T10:30:00+00:00")

    @patch("sjifire.ops.tasks.neris_sync.fetch_neris_summaries")
    async def test_checkpoint_stored_after_sync(self, mock_fetch):
        """A new checkpoint is stored after successful sync."""
        mock_fetch.return_value = []

        await neris_sync()

        async with NerisReportStore() as store:
            checkpoint = await store.get_sync_checkpoint()
        assert checkpoint is not None
        parsed = datetime.fromisoformat(checkpoint)
        assert parsed.tzinfo is not None

    @patch("sjifire.ops.tasks.neris_sync.fetch_neris_summaries")
    async def test_sync_transitions_submitted_to_approved(self, mock_fetch):
        """neris_sync transitions local submitted incidents when NERIS is APPROVED."""
        incident = IncidentDocument(
            id="inc-sync",
            incident_number="26-001980",
            incident_datetime=datetime(2026, 2, 9, tzinfo=UTC),
            created_by="ff@sjifire.org",
            status="submitted",
            neris_incident_id="FD|26001980|123",
            station="S31",
        )
        async with IncidentStore() as store:
            await store.create(incident)

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

        await neris_sync()

        async with IncidentStore() as store:
            doc = await store.get_by_id("inc-sync")
        assert doc.status == "approved"
