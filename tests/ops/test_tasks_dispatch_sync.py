"""Tests for dispatch sync, enrich, and reenrich background tasks."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.ops.dispatch.models import DispatchAnalysis
from sjifire.ops.tasks.dispatch_sync import (
    _prewarm_schedule,
    dispatch_enrich,
    dispatch_reenrich,
    dispatch_sync,
)
from tests.factories import DispatchAnalysisFactory, DispatchCallDocumentFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enriched(**kw):
    """Build a doc with populated analysis (IC + summary)."""
    return DispatchCallDocumentFactory.build(
        analysis=DispatchAnalysisFactory.build(), **kw
    )


def _unenriched(**kw):
    """Build a doc with empty analysis."""
    return DispatchCallDocumentFactory.build(analysis=DispatchAnalysis(), **kw)


def _mock_store(*, sync=0, enrich=None, recent=None):
    """Create a mock DispatchStore async context manager.

    Returns (store_cls_callable, store_instance).
    """
    s = AsyncMock()
    s.sync_recent = AsyncMock(return_value=sync)
    s.enrich_stored = AsyncMock(return_value=enrich or [])
    s.list_recent = AsyncMock(return_value=recent or [])
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=None)
    return (lambda: s), s


_DS = "sjifire.ops.dispatch.store.DispatchStore"
_PW = "sjifire.ops.tasks.dispatch_sync._prewarm_schedule"
_EC = "sjifire.ops.schedule.tools._ensure_cache"
_SS = "sjifire.ops.schedule.store.ScheduleStore"


# ---------------------------------------------------------------------------
# dispatch_sync
# ---------------------------------------------------------------------------


class TestDispatchSync:
    async def test_no_new_calls_no_unenriched(self):
        """Returns 0 when nothing new and nothing to re-enrich."""
        cls, s = _mock_store()
        with patch(_DS, cls):
            result = await dispatch_sync()
        assert result == 0
        s.sync_recent.assert_awaited_once_with(days=2)
        s.enrich_stored.assert_awaited_once()

    async def test_new_calls_and_reenriched(self):
        """Returns sum of new + successfully re-enriched."""
        cls, s = _mock_store(sync=3, enrich=[_enriched(), _enriched()])
        with patch(_DS, cls):
            result = await dispatch_sync()
        assert result == 5  # 3 new + 2 re-enriched

    async def test_new_calls_none_reenriched(self):
        """Returns only new count when enrich returns empty results."""
        cls, s = _mock_store(sync=4)
        with patch(_DS, cls):
            result = await dispatch_sync()
        assert result == 4

    async def test_enrich_returns_partial_success(self):
        """Only counts docs where analysis has IC or summary set."""
        cls, s = _mock_store(
            enrich=[_enriched(), _unenriched(), _enriched()]
        )
        with patch(_DS, cls):
            result = await dispatch_sync()
        assert result == 2  # only the 2 enriched docs


# ---------------------------------------------------------------------------
# dispatch_enrich
# ---------------------------------------------------------------------------


class TestDispatchEnrich:
    async def test_all_already_enriched(self):
        """Returns 0 when every stored call already has analysis."""
        cls, s = _mock_store(recent=[_enriched() for _ in range(5)])
        with patch(_DS, cls):
            result = await dispatch_enrich()
        assert result == 0
        # enrich_stored should NOT have been called (early return)
        s.enrich_stored.assert_not_awaited()

    async def test_some_unenriched(self):
        """Prewarms schedule, enriches, and returns count."""
        cls, s = _mock_store(
            recent=[_enriched(), _unenriched(), _unenriched()],
            enrich=[_enriched(), _enriched()],
        )
        with (
            patch(_DS, cls),
            patch(_PW, new_callable=AsyncMock) as mock_pw,
        ):
            result = await dispatch_enrich()
        assert result == 2
        mock_pw.assert_awaited_once()
        s.enrich_stored.assert_awaited_once_with(force=False, limit=9999)

    async def test_no_stored_calls(self):
        """Returns 0 when store is empty."""
        cls, s = _mock_store()
        with patch(_DS, cls):
            result = await dispatch_enrich()
        assert result == 0

    async def test_enrich_partial_success(self):
        """Only counts docs that gained analysis after enrichment."""
        cls, s = _mock_store(
            recent=[_unenriched()],
            enrich=[_unenriched()],  # LLM failure
        )
        with (
            patch(_DS, cls),
            patch(_PW, new_callable=AsyncMock),
        ):
            result = await dispatch_enrich()
        assert result == 0  # enrichment failed for all


# ---------------------------------------------------------------------------
# dispatch_reenrich
# ---------------------------------------------------------------------------


class TestDispatchReenrich:
    async def test_no_stored_calls(self):
        """Returns 0 when store is empty (enrich_stored still called with force=True)."""
        cls, s = _mock_store()
        with patch(_DS, cls):
            result = await dispatch_reenrich()
        assert result == 0
        s.enrich_stored.assert_awaited_once_with(force=True, limit=9999)

    async def test_force_reenriches_all(self):
        """Prewarms schedule and force re-enriches all stored calls."""
        docs = [_enriched() for _ in range(3)]
        enriched_results = [_enriched() for _ in range(3)]
        cls, s = _mock_store(recent=docs, enrich=enriched_results)
        with (
            patch(_DS, cls),
            patch(_PW, new_callable=AsyncMock) as mock_pw,
        ):
            result = await dispatch_reenrich()
        assert result == 3
        mock_pw.assert_awaited_once_with(docs)
        s.enrich_stored.assert_awaited_once_with(force=True, limit=9999)

    async def test_reenrich_partial_success(self):
        """Counts only docs with analysis after force re-enrichment."""
        docs = [_enriched() for _ in range(4)]
        results = [_enriched(), _unenriched(), _enriched(), _unenriched()]
        cls, s = _mock_store(recent=docs, enrich=results)
        with (
            patch(_DS, cls),
            patch(_PW, new_callable=AsyncMock),
        ):
            result = await dispatch_reenrich()
        assert result == 2

    async def test_prewarm_not_called_when_empty(self):
        """_prewarm_schedule is not called when there are no docs."""
        cls, s = _mock_store()
        with (
            patch(_DS, cls),
            patch(_PW, new_callable=AsyncMock) as mock_pw,
        ):
            await dispatch_reenrich()
        mock_pw.assert_not_awaited()


# ---------------------------------------------------------------------------
# _prewarm_schedule
# ---------------------------------------------------------------------------


class TestPrewarmSchedule:
    async def test_empty_docs(self):
        """Returns immediately with no schedule store interaction."""
        with patch(_EC, new_callable=AsyncMock) as mock_ec:
            await _prewarm_schedule([])
        mock_ec.assert_not_awaited()

    async def test_collects_dates_from_docs(self):
        """Collects unique dates (day-of and day-before) from time_reported."""
        docs = [
            DispatchCallDocumentFactory.build(
                time_reported=datetime(2026, 3, 10, 14, 30, tzinfo=UTC),
            ),
            DispatchCallDocumentFactory.build(
                time_reported=datetime(2026, 3, 10, 22, 0, tzinfo=UTC),
            ),
            DispatchCallDocumentFactory.build(
                time_reported=datetime(2026, 3, 12, 8, 0, tzinfo=UTC),
            ),
        ]
        mock_ss = AsyncMock()
        mock_ss.__aenter__ = AsyncMock(return_value=mock_ss)
        mock_ss.__aexit__ = AsyncMock(return_value=None)
        with (
            patch(_SS, return_value=mock_ss),
            patch(_EC, new_callable=AsyncMock) as mock_ec,
        ):
            await _prewarm_schedule(docs)
        mock_ec.assert_awaited_once()
        dates_arg = mock_ec.call_args[0][1]
        # Two calls on 3/10, one on 3/12 -> dates 3/9, 3/10, 3/11, 3/12
        assert set(dates_arg) == {
            "2026-03-09",
            "2026-03-10",
            "2026-03-11",
            "2026-03-12",
        }
        # Must be sorted
        assert dates_arg == sorted(dates_arg)

    async def test_skips_docs_without_time_reported(self):
        """Docs with time_reported=None don't contribute dates."""
        docs = [
            DispatchCallDocumentFactory.build(time_reported=None),
            DispatchCallDocumentFactory.build(
                time_reported=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
            ),
        ]
        mock_ss = AsyncMock()
        mock_ss.__aenter__ = AsyncMock(return_value=mock_ss)
        mock_ss.__aexit__ = AsyncMock(return_value=None)
        with (
            patch(_SS, return_value=mock_ss),
            patch(_EC, new_callable=AsyncMock) as mock_ec,
        ):
            await _prewarm_schedule(docs)
        # Only 2026-04-30 and 2026-05-01 from the second doc
        assert set(mock_ec.call_args[0][1]) == {"2026-04-30", "2026-05-01"}

    async def test_all_docs_missing_time_reported(self):
        """No dates to prewarm when all docs lack time_reported."""
        docs = [
            DispatchCallDocumentFactory.build(time_reported=None),
            DispatchCallDocumentFactory.build(time_reported=None),
        ]
        with patch(_EC, new_callable=AsyncMock) as mock_ec:
            await _prewarm_schedule(docs)
        mock_ec.assert_not_awaited()
