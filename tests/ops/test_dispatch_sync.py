"""Tests for background dispatch sync loop."""

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.ops.dispatch.store import DispatchStore
from sjifire.ops.dispatch.sync import dispatch_sync_loop


@pytest.fixture(autouse=True)
def _no_cosmos():
    """Ensure in-memory mode."""
    with patch.dict(os.environ, {"COSMOS_ENDPOINT": "", "COSMOS_KEY": ""}, clear=False):
        yield
    DispatchStore._memory.clear()


class TestDispatchSyncLoop:
    async def test_runs_one_iteration(self):
        """Loop calls sync_recent and enrich_stored, then sleeps."""
        mock_sync = AsyncMock(return_value=3)
        mock_enrich = AsyncMock(return_value=[])

        with (
            patch.object(DispatchStore, "sync_recent", mock_sync),
            patch.object(DispatchStore, "enrich_stored", mock_enrich),
            patch("sjifire.ops.dispatch.sync.SYNC_INTERVAL", 0),
            patch("sjifire.ops.dispatch.sync.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            # Initial delay sleep returns immediately, loop sleep cancels
            call_count = 0

            async def counted_sleep(seconds):
                nonlocal call_count
                call_count += 1
                if call_count > 1:
                    raise asyncio.CancelledError

            mock_sleep.side_effect = counted_sleep

            with pytest.raises(asyncio.CancelledError):
                await dispatch_sync_loop()

        mock_sync.assert_called_once_with(days=2)
        mock_enrich.assert_called_once_with(limit=10)

    async def test_handles_exceptions_gracefully(self):
        """Loop continues after an exception in sync_recent."""
        call_count = 0

        async def failing_sync(days=2):
            raise RuntimeError("iSpyFire down")

        async def counted_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError

        with (
            patch.object(DispatchStore, "sync_recent", side_effect=failing_sync),
            patch("sjifire.ops.dispatch.sync.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_sleep.side_effect = counted_sleep

            with pytest.raises(asyncio.CancelledError):
                await dispatch_sync_loop()

        # Loop ran, hit the exception, slept, then was cancelled
        assert call_count == 2  # initial delay + loop sleep
