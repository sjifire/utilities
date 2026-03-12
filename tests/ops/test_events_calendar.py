"""Tests for the events calendar module — utility functions and fetch orchestration."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from sjifire.ops.events.calendar import (
    _cache_key,
    _strip_html,
    fetch_events,
)

# ---------------------------------------------------------------------------
# _strip_html
# ---------------------------------------------------------------------------


class TestStripHtml:
    def test_empty_string(self):
        assert _strip_html("") == ""

    def test_none_returns_empty(self):
        assert _strip_html(None) == ""

    def test_plain_text_passthrough(self):
        assert _strip_html("Hello world") == "Hello world"

    def test_strips_simple_tags(self):
        assert _strip_html("<p>Hello</p>") == "Hello"

    def test_strips_nested_tags(self):
        result = _strip_html("<div><p>Hello</p><p>World</p></div>")
        assert "Hello" in result
        assert "World" in result

    def test_preserves_text_from_complex_html(self):
        html = '<b>Bold</b> and <i>italic</i> and <a href="url">link</a>'
        result = _strip_html(html)
        assert "Bold" in result
        assert "italic" in result
        assert "link" in result
        assert "<b>" not in result
        assert "<a" not in result

    def test_strips_whitespace(self):
        result = _strip_html("<p>  spaced  </p>")
        assert result == "spaced"


# ---------------------------------------------------------------------------
# _cache_key
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_basic_format(self):
        start = date(2026, 1, 1)
        end = date(2026, 3, 31)
        assert _cache_key("Training", start, end) == "cal:Training:2026-01-01:2026-03-31"

    def test_different_labels_produce_different_keys(self):
        start = date(2026, 1, 1)
        end = date(2026, 3, 31)
        assert _cache_key("A", start, end) != _cache_key("B", start, end)

    def test_different_dates_produce_different_keys(self):
        assert _cache_key("X", date(2026, 1, 1), date(2026, 3, 31)) != _cache_key(
            "X", date(2026, 4, 1), date(2026, 6, 30)
        )


# ---------------------------------------------------------------------------
# fetch_events — orchestration tests
# ---------------------------------------------------------------------------


def _make_org_config(event_calendars: list[dict]):
    """Build a fake OrgConfig with the given event_calendars."""
    cfg = MagicMock()
    cfg.event_calendars = event_calendars
    return cfg


class TestFetchEvents:
    async def test_no_calendar_sources_returns_empty(self):
        with patch(
            "sjifire.ops.events.calendar.load_org_config",
            return_value=_make_org_config([]),
        ):
            result = await fetch_events(date(2026, 1, 1), date(2026, 3, 31))
        assert result == []

    async def test_single_source_returns_events(self):
        sources = [{"mailbox": "cal@sjifire.org", "label": "Training"}]
        events = [
            {"event_id": "1", "subject": "Ladder Drill", "start": "2026-02-01T09:00:00"},
        ]

        with (
            patch(
                "sjifire.ops.events.calendar.load_org_config",
                return_value=_make_org_config(sources),
            ),
            patch(
                "sjifire.ops.events.calendar._fetch_cached",
                AsyncMock(return_value=events),
            ) as mock_fetch,
        ):
            result = await fetch_events(date(2026, 1, 1), date(2026, 3, 31))

        assert len(result) == 1
        assert result[0]["subject"] == "Ladder Drill"
        mock_fetch.assert_awaited_once_with(
            "cal@sjifire.org", "Training", date(2026, 1, 1), date(2026, 3, 31), ""
        )

    async def test_multiple_sources_merged_and_sorted(self):
        sources = [
            {"mailbox": "a@sjifire.org", "label": "A"},
            {"mailbox": "b@sjifire.org", "label": "B"},
        ]
        events_a = [
            {"event_id": "a2", "subject": "Later", "start": "2026-02-15T10:00:00"},
        ]
        events_b = [
            {"event_id": "b1", "subject": "Earlier", "start": "2026-01-10T08:00:00"},
        ]

        async def fake_fetch(mailbox, label, start, end, calendar_name=""):
            if label == "A":
                return events_a
            return events_b

        with (
            patch(
                "sjifire.ops.events.calendar.load_org_config",
                return_value=_make_org_config(sources),
            ),
            patch(
                "sjifire.ops.events.calendar._fetch_cached",
                AsyncMock(side_effect=fake_fetch),
            ),
        ):
            result = await fetch_events(date(2026, 1, 1), date(2026, 3, 31))

        assert len(result) == 2
        assert result[0]["subject"] == "Earlier"
        assert result[1]["subject"] == "Later"

    async def test_failed_source_skipped_others_returned(self):
        sources = [
            {"mailbox": "ok@sjifire.org", "label": "OK"},
            {"mailbox": "bad@sjifire.org", "label": "Bad"},
        ]
        ok_events = [{"event_id": "1", "subject": "Good Event", "start": "2026-02-01T09:00:00"}]

        async def fake_fetch(mailbox, label, start, end, calendar_name=""):
            if label == "Bad":
                raise RuntimeError("Graph API unavailable")
            return ok_events

        with (
            patch(
                "sjifire.ops.events.calendar.load_org_config",
                return_value=_make_org_config(sources),
            ),
            patch(
                "sjifire.ops.events.calendar._fetch_cached",
                AsyncMock(side_effect=fake_fetch),
            ),
        ):
            result = await fetch_events(date(2026, 1, 1), date(2026, 3, 31))

        assert len(result) == 1
        assert result[0]["subject"] == "Good Event"

    async def test_all_sources_fail_returns_empty(self):
        sources = [
            {"mailbox": "bad1@sjifire.org", "label": "Bad1"},
            {"mailbox": "bad2@sjifire.org", "label": "Bad2"},
        ]

        with (
            patch(
                "sjifire.ops.events.calendar.load_org_config",
                return_value=_make_org_config(sources),
            ),
            patch(
                "sjifire.ops.events.calendar._fetch_cached",
                AsyncMock(side_effect=RuntimeError("fail")),
            ),
        ):
            result = await fetch_events(date(2026, 1, 1), date(2026, 3, 31))

        assert result == []

    async def test_calendar_name_passed_through(self):
        sources = [
            {
                "mailbox": "room@sjifire.org",
                "label": "Room",
                "calendar_name": "Training Room",
            },
        ]

        with (
            patch(
                "sjifire.ops.events.calendar.load_org_config",
                return_value=_make_org_config(sources),
            ),
            patch(
                "sjifire.ops.events.calendar._fetch_cached",
                AsyncMock(return_value=[]),
            ) as mock_fetch,
        ):
            await fetch_events(date(2026, 1, 1), date(2026, 3, 31))

        mock_fetch.assert_awaited_once_with(
            "room@sjifire.org",
            "Room",
            date(2026, 1, 1),
            date(2026, 3, 31),
            "Training Room",
        )

    async def test_events_sorted_by_start_field(self):
        sources = [{"mailbox": "cal@sjifire.org", "label": "Cal"}]
        unsorted = [
            {"event_id": "3", "subject": "C", "start": "2026-03-01T09:00:00"},
            {"event_id": "1", "subject": "A", "start": "2026-01-01T09:00:00"},
            {"event_id": "2", "subject": "B", "start": "2026-02-01T09:00:00"},
        ]

        with (
            patch(
                "sjifire.ops.events.calendar.load_org_config",
                return_value=_make_org_config(sources),
            ),
            patch(
                "sjifire.ops.events.calendar._fetch_cached",
                AsyncMock(return_value=unsorted),
            ),
        ):
            result = await fetch_events(date(2026, 1, 1), date(2026, 3, 31))

        subjects = [e["subject"] for e in result]
        assert subjects == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# _fetch_cached — caching logic tests
# ---------------------------------------------------------------------------


class TestFetchCached:
    async def test_cache_hit_returns_cached_data(self):
        from sjifire.ops.events.calendar import _fetch_cached

        cached_events = [
            {"event_id": "cached", "subject": "From Cache", "start": "2026-01-01T09:00:00"}
        ]

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=cached_events)
        mock_cache.set = AsyncMock()

        with (
            patch("sjifire.ops.cache.cosmos_cache", mock_cache),
            patch(
                "sjifire.ops.events.calendar._fetch_one_calendar",
                AsyncMock(),
            ) as mock_fetch_one,
        ):
            result = await _fetch_cached(
                "cal@sjifire.org", "Training", date(2026, 1, 1), date(2026, 3, 31)
            )

        assert result == cached_events
        mock_fetch_one.assert_not_awaited()

    async def test_cache_miss_fetches_and_caches(self):
        from sjifire.ops.events.calendar import _fetch_cached

        fresh_events = [{"event_id": "fresh", "subject": "Fresh", "start": "2026-02-01T10:00:00"}]

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()

        with (
            patch("sjifire.ops.cache.cosmos_cache", mock_cache),
            patch(
                "sjifire.ops.events.calendar._fetch_one_calendar",
                AsyncMock(return_value=fresh_events),
            ) as mock_fetch_one,
        ):
            result = await _fetch_cached(
                "cal@sjifire.org", "Training", date(2026, 1, 1), date(2026, 3, 31)
            )

        assert result == fresh_events
        mock_fetch_one.assert_awaited_once()
        mock_cache.set.assert_awaited_once()
        call_args = mock_cache.set.call_args
        assert call_args[0][0] == "cal:Training:2026-01-01:2026-03-31"
        assert call_args[0][1] == fresh_events
        assert call_args[1]["ttl"] == 10800
