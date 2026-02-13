"""Tests for ispyfire-dispatch archive command."""

import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from sjifire.ispyfire.models import CallSummary, DispatchCall, UnitResponse
from sjifire.mcp.dispatch.models import DispatchCallDocument
from sjifire.mcp.dispatch.store import DispatchStore
from sjifire.scripts.ispyfire_dispatch import (
    _archive_to_cosmos,
    _get_existing_ids,
    cmd_archive,
    main,
)


@pytest.fixture(autouse=True)
def _no_cosmos():
    """Ensure in-memory mode by clearing COSMOS_ENDPOINT."""
    with patch.dict(os.environ, {"COSMOS_ENDPOINT": "", "COSMOS_KEY": ""}, clear=False):
        yield
    DispatchStore._memory.clear()


def _make_call(**overrides) -> DispatchCall:
    defaults = {
        "id": "call-uuid-1",
        "long_term_call_id": "26-001678",
        "nature": "Medical Aid",
        "address": "200 Spring St",
        "agency_code": "SJF",
        "type": "EMS",
        "zone_code": "Z1",
        "time_reported": datetime(2026, 2, 12, 14, 30),
        "is_completed": True,
        "cad_comments": "Patient fall",
        "responding_units": "E31",
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
    }
    defaults.update(overrides)
    return DispatchCall(**defaults)


def _make_summary(call_id: str) -> CallSummary:
    return CallSummary(id=call_id, ispy_timestamp="1739388600")


class TestArchiveToCosmos:
    async def test_stores_completed_calls(self):
        completed = _make_call(id="uuid-done", is_completed=True)
        open_call = _make_call(id="uuid-open", is_completed=False)

        count = await _archive_to_cosmos([completed, open_call])
        assert count == 1

        async with DispatchStore() as store:
            doc = await store.get("uuid-done", "2026")
            assert doc is not None
            assert doc.nature == "Medical Aid"

            doc = await store.get("uuid-open", "2026")
            assert doc is None

    async def test_empty_list(self):
        count = await _archive_to_cosmos([])
        assert count == 0

    async def test_multiple_completed_calls(self):
        calls = [
            _make_call(id=f"uuid-{i}", long_term_call_id=f"26-00{i:04d}", is_completed=True)
            for i in range(1, 4)
        ]

        count = await _archive_to_cosmos(calls)
        assert count == 3

        async with DispatchStore() as store:
            for i in range(1, 4):
                doc = await store.get(f"uuid-{i}", "2026")
                assert doc is not None


class TestGetExistingIds:
    async def test_returns_stored_ids(self):
        call = _make_call(id="uuid-existing")
        doc = DispatchCallDocument.from_dispatch_call(call)
        async with DispatchStore() as store:
            await store.upsert(doc)

        result = await _get_existing_ids(["uuid-existing", "uuid-new"])
        assert result == {"uuid-existing"}

    async def test_empty_list(self):
        result = await _get_existing_ids([])
        assert result == set()

    async def test_none_exist(self):
        result = await _get_existing_ids(["uuid-a", "uuid-b"])
        assert result == set()


class TestCmdArchive:
    def test_archives_new_completed_calls(self, capsys):
        completed = _make_call(id="uuid-1", is_completed=True)
        open_call = _make_call(id="uuid-2", is_completed=False)
        summaries = [_make_summary("uuid-1"), _make_summary("uuid-2")]

        mock_client = MagicMock()
        mock_client.get_calls.return_value = summaries
        mock_client.get_call_details.side_effect = [completed, open_call]
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        args = MagicMock(days=7, dry_run=False)

        with patch("sjifire.scripts.ispyfire_dispatch.ISpyFireClient", return_value=mock_client):
            result = cmd_archive(args)

        assert result == 0
        output = capsys.readouterr().out
        assert "Archived 1 completed calls" in output

    def test_skips_already_archived(self, capsys):
        """Calls already in Cosmos should not have details fetched."""
        completed = _make_call(id="uuid-old", is_completed=True)
        doc = DispatchCallDocument.from_dispatch_call(completed)
        # Pre-populate the in-memory store
        DispatchStore._memory[doc.id] = doc.to_cosmos()

        summaries = [_make_summary("uuid-old"), _make_summary("uuid-new")]
        new_call = _make_call(id="uuid-new", is_completed=True, long_term_call_id="26-001679")

        mock_client = MagicMock()
        mock_client.get_calls.return_value = summaries
        # Only uuid-new should have details fetched
        mock_client.get_call_details.return_value = new_call
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        args = MagicMock(days=7, dry_run=False)

        with patch("sjifire.scripts.ispyfire_dispatch.ISpyFireClient", return_value=mock_client):
            result = cmd_archive(args)

        assert result == 0
        output = capsys.readouterr().out
        assert "1 already archived" in output
        assert "1 new" in output
        # Only fetched details for the new call
        mock_client.get_call_details.assert_called_once_with("uuid-new")

    def test_all_already_archived(self, capsys):
        """When everything is already archived, no details are fetched."""
        completed = _make_call(id="uuid-1", is_completed=True)
        doc = DispatchCallDocument.from_dispatch_call(completed)
        DispatchStore._memory[doc.id] = doc.to_cosmos()

        summaries = [_make_summary("uuid-1")]

        mock_client = MagicMock()
        mock_client.get_calls.return_value = summaries
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        args = MagicMock(days=7, dry_run=False)

        with patch("sjifire.scripts.ispyfire_dispatch.ISpyFireClient", return_value=mock_client):
            result = cmd_archive(args)

        assert result == 0
        output = capsys.readouterr().out
        assert "All calls already archived" in output
        mock_client.get_call_details.assert_not_called()

    def test_dry_run_does_not_write(self, capsys):
        completed = _make_call(id="uuid-1", is_completed=True)
        summaries = [_make_summary("uuid-1")]

        mock_client = MagicMock()
        mock_client.get_calls.return_value = summaries
        mock_client.get_call_details.return_value = completed
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        args = MagicMock(days=7, dry_run=True)

        with patch("sjifire.scripts.ispyfire_dispatch.ISpyFireClient", return_value=mock_client):
            result = cmd_archive(args)

        assert result == 0
        output = capsys.readouterr().out
        assert "[DRY RUN]" in output
        assert "26-001678" in output

    def test_no_calls_found(self, capsys):
        mock_client = MagicMock()
        mock_client.get_calls.return_value = []
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        args = MagicMock(days=7, dry_run=False)

        with patch("sjifire.scripts.ispyfire_dispatch.ISpyFireClient", return_value=mock_client):
            result = cmd_archive(args)

        assert result == 0
        output = capsys.readouterr().out
        assert "No calls found" in output

    def test_no_completed_calls(self, capsys):
        open_call = _make_call(id="uuid-1", is_completed=False)
        summaries = [_make_summary("uuid-1")]

        mock_client = MagicMock()
        mock_client.get_calls.return_value = summaries
        mock_client.get_call_details.return_value = open_call
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        args = MagicMock(days=7, dry_run=False)

        with patch("sjifire.scripts.ispyfire_dispatch.ISpyFireClient", return_value=mock_client):
            result = cmd_archive(args)

        assert result == 0
        output = capsys.readouterr().out
        assert "No new completed calls to archive" in output

    def test_skips_calls_with_no_details(self, capsys):
        summaries = [_make_summary("uuid-1"), _make_summary("uuid-2")]

        mock_client = MagicMock()
        mock_client.get_calls.return_value = summaries
        mock_client.get_call_details.return_value = None
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        args = MagicMock(days=7, dry_run=False)

        with patch("sjifire.scripts.ispyfire_dispatch.ISpyFireClient", return_value=mock_client):
            result = cmd_archive(args)

        assert result == 0
        output = capsys.readouterr().out
        assert "No new completed calls to archive" in output

    def test_shows_open_calls_message(self, capsys):
        """New calls that are still open should show an info message."""
        completed = _make_call(id="uuid-1", is_completed=True)
        open1 = _make_call(id="uuid-2", is_completed=False)
        open2 = _make_call(id="uuid-3", is_completed=False)
        summaries = [_make_summary("uuid-1"), _make_summary("uuid-2"), _make_summary("uuid-3")]

        mock_client = MagicMock()
        mock_client.get_calls.return_value = summaries
        mock_client.get_call_details.side_effect = [completed, open1, open2]
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        args = MagicMock(days=7, dry_run=False)

        with patch("sjifire.scripts.ispyfire_dispatch.ISpyFireClient", return_value=mock_client):
            result = cmd_archive(args)

        assert result == 0
        output = capsys.readouterr().out
        assert "2 still open (will archive when completed)" in output
        assert "Archived 1 completed calls" in output

    def test_end_to_end_data_in_store(self, capsys):
        """Verify archived data actually lands in the store."""
        call = _make_call(id="uuid-e2e", long_term_call_id="26-009999", nature="Structure Fire")
        summaries = [_make_summary("uuid-e2e")]

        mock_client = MagicMock()
        mock_client.get_calls.return_value = summaries
        mock_client.get_call_details.return_value = call
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        args = MagicMock(days=7, dry_run=False)

        with patch("sjifire.scripts.ispyfire_dispatch.ISpyFireClient", return_value=mock_client):
            cmd_archive(args)

        # Verify data landed in the in-memory store
        data = DispatchStore._memory.get("uuid-e2e")
        assert data is not None
        doc = DispatchCallDocument.from_cosmos(data)
        assert doc.long_term_call_id == "26-009999"
        assert doc.nature == "Structure Fire"
        assert doc.year == "2026"

    def test_cosmos_failure_on_existing_check_propagates(self, capsys):
        """If Cosmos is down during gap check, the error propagates."""
        summaries = [_make_summary("uuid-1")]

        mock_client = MagicMock()
        mock_client.get_calls.return_value = summaries
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("sjifire.scripts.ispyfire_dispatch.ISpyFireClient", return_value=mock_client),
            patch("sjifire.scripts.ispyfire_dispatch._get_existing_ids", side_effect=RuntimeError("Cosmos unavailable")),
            pytest.raises(RuntimeError, match="Cosmos unavailable"),
        ):
            cmd_archive(MagicMock(days=7, dry_run=False))


class TestCLIArgParsing:
    """Test CLI argument parsing via main()."""

    def test_archive_defaults_to_7_days(self, capsys):
        mock_client = MagicMock()
        mock_client.get_calls.return_value = []
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("sjifire.scripts.ispyfire_dispatch.ISpyFireClient", return_value=mock_client),
            patch.object(sys, "argv", ["ispyfire-dispatch", "archive"]),
        ):
            result = main()

        assert result == 0
        mock_client.get_calls.assert_called_once_with(days=7)

    def test_archive_days_30(self, capsys):
        mock_client = MagicMock()
        mock_client.get_calls.return_value = []
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("sjifire.scripts.ispyfire_dispatch.ISpyFireClient", return_value=mock_client),
            patch.object(sys, "argv", ["ispyfire-dispatch", "archive", "--days", "30"]),
        ):
            result = main()

        assert result == 0
        mock_client.get_calls.assert_called_once_with(days=30)

    def test_archive_dry_run_flag(self, capsys):
        completed = _make_call(id="uuid-1", is_completed=True)
        summaries = [_make_summary("uuid-1")]

        mock_client = MagicMock()
        mock_client.get_calls.return_value = summaries
        mock_client.get_call_details.return_value = completed
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("sjifire.scripts.ispyfire_dispatch.ISpyFireClient", return_value=mock_client),
            patch.object(sys, "argv", ["ispyfire-dispatch", "archive", "--dry-run"]),
        ):
            result = main()

        assert result == 0
        output = capsys.readouterr().out
        assert "[DRY RUN]" in output

    def test_no_command_shows_help(self, capsys):
        with patch.object(sys, "argv", ["ispyfire-dispatch"]):
            result = main()
        assert result == 1
