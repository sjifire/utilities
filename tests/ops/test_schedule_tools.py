"""Tests for schedule tools — timezone-aware date handling.

Verifies that get_on_duty_crew uses the configured timezone (Pacific)
rather than the system clock (UTC on Azure) when determining "today".
This prevents a bug where after 4 PM Pacific (midnight UTC), the
function would use the wrong date.
"""

import os
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.core.config import get_timezone
from sjifire.ops.auth import UserContext, set_current_user
from sjifire.ops.schedule.models import DayScheduleCache, ScheduleEntryCache
from sjifire.ops.schedule.tools import get_on_duty_crew


def _make_day(date_str: str, platoon: str = "A") -> DayScheduleCache:
    """Build a DayScheduleCache with typical 18:00-18:00 crew."""
    return DayScheduleCache(
        id=date_str,
        date=date_str,
        platoon=platoon,
        entries=[
            ScheduleEntryCache(
                name="Alice Smith",
                position="Captain",
                section="Station 31",
                start_time="18:00",
                end_time="18:00",
            ),
            ScheduleEntryCache(
                name="Bob Jones",
                position="Firefighter",
                section="Station 31",
                start_time="18:00",
                end_time="18:00",
            ),
        ],
    )


@pytest.fixture(autouse=True)
def _dev_mode():
    """Ensure dev mode (no Entra config) so get_current_user() works."""
    with patch.dict(
        os.environ,
        {"ENTRA_MCP_API_CLIENT_ID": "", "COSMOS_ENDPOINT": "", "COSMOS_KEY": ""},
        clear=False,
    ):
        set_current_user(None)
        yield


@pytest.fixture
def auth_user():
    user = UserContext(email="ff@sjifire.org", name="Firefighter", user_id="user-1")
    set_current_user(user)
    return user


def _make_cache(dates: list[str]) -> dict[str, DayScheduleCache]:
    """Build a cache dict for the given dates, alternating platoons."""
    platoons = ["A", "B", "C"]
    return {d: _make_day(d, platoons[i % 3]) for i, d in enumerate(dates)}


class TestTimezoneAwareDateResolution:
    """Verify get_on_duty_crew uses local_now(), not system UTC."""

    @pytest.mark.asyncio
    async def test_uses_pacific_date_after_utc_midnight(self, auth_user):
        """At 4:30 PM Pacific (00:30 UTC next day), 'today' should be the Pacific date.

        Bug scenario: Azure Container Apps runs in UTC. After midnight UTC
        (4 PM Pacific), date.today() returns tomorrow's date. The function
        should use local_now() from the config helpers instead.

        At 16:30 Pacific on Feb 14 (before 18:00 shift change):
        - Pacific date = Feb 14, UTC date = Feb 15
        - Duty crew = Feb 13's crew (started 18:00 Feb 13, ends 18:00 Feb 14)
        """
        # 2026-02-15 00:30 UTC = 2026-02-14 16:30 Pacific
        fake_now = datetime(2026, 2, 14, 16, 30, tzinfo=get_timezone())
        cache = _make_cache(["2026-02-13", "2026-02-14", "2026-02-15"])

        with (
            patch("sjifire.ops.schedule.tools.local_now", return_value=fake_now),
            patch(
                "sjifire.ops.schedule.tools._ensure_cache",
                new_callable=AsyncMock,
                return_value=cache,
            ),
        ):
            result = await get_on_duty_crew()

        # Should resolve to Feb 13's crew (before 18:00 shift change on Feb 14)
        assert result["date"] == "2026-02-13"

    @pytest.mark.asyncio
    async def test_uses_pacific_date_before_utc_midnight(self, auth_user):
        """At 3:30 PM Pacific (23:30 UTC same day), both timezones agree.

        This is the 'normal' case — no timezone divergence.
        """
        fake_now = datetime(2026, 2, 14, 15, 30, tzinfo=get_timezone())
        cache = _make_cache(["2026-02-13", "2026-02-14", "2026-02-15"])

        with (
            patch("sjifire.ops.schedule.tools.local_now", return_value=fake_now),
            patch(
                "sjifire.ops.schedule.tools._ensure_cache",
                new_callable=AsyncMock,
                return_value=cache,
            ),
        ):
            result = await get_on_duty_crew()

        # Still before 18:00 shift change → Feb 13's crew
        assert result["date"] == "2026-02-13"

    @pytest.mark.asyncio
    async def test_after_shift_change_same_day(self, auth_user):
        """At 7 PM Pacific, the new crew (today's) is on duty."""
        fake_now = datetime(2026, 2, 14, 19, 0, tzinfo=get_timezone())
        cache = _make_cache(["2026-02-13", "2026-02-14", "2026-02-15"])

        with (
            patch("sjifire.ops.schedule.tools.local_now", return_value=fake_now),
            patch(
                "sjifire.ops.schedule.tools._ensure_cache",
                new_callable=AsyncMock,
                return_value=cache,
            ),
        ):
            result = await get_on_duty_crew()

        # After 18:00 shift change → Feb 14's crew is on duty
        assert result["date"] == "2026-02-14"

    @pytest.mark.asyncio
    async def test_explicit_target_date_bypasses_timezone(self, auth_user):
        """When target_date is provided, timezone doesn't matter."""
        cache = _make_cache(["2026-02-12", "2026-02-13", "2026-02-14"])

        with patch(
            "sjifire.ops.schedule.tools._ensure_cache",
            new_callable=AsyncMock,
            return_value=cache,
        ):
            result = await get_on_duty_crew(target_date="2026-02-13")

        # Explicit date, no hour → returns that date directly
        assert result["date"] == "2026-02-13"
