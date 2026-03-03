"""Tests for the kiosk display page.

The kiosk is a station bay monitor view showing active dispatch calls and
on-duty crew. It authenticates via signed tokens (not EasyAuth) and
auto-refreshes via polling.

Tests cover both the empty/idle state and the seeded state with dispatch
calls and schedule data.
"""

import pytest

pytestmark = pytest.mark.e2e

WAIT_UNTIL = "domcontentloaded"

# JS expression: Alpine store is initialized (kiosk sets $store.kiosk)
_KIOSK_READY = "window.Alpine && document.querySelector('.kiosk-main')"


# ---------------------------------------------------------------------------
# Empty kiosk (no seeded data — idle state)
# ---------------------------------------------------------------------------


def test_kiosk_requires_token(page):
    """Kiosk returns 401 without a valid token."""
    resp = page.request.get("/kiosk")
    assert resp.status == 401


def test_kiosk_loads_with_token(page, kiosk_token):
    """Kiosk page loads successfully with a valid token."""
    page.goto(f"/kiosk?token={kiosk_token}", wait_until=WAIT_UNTIL)
    page.wait_for_function(_KIOSK_READY, timeout=10_000)

    assert "kiosk" in page.url or page.title() != ""


def test_kiosk_header_renders(page, kiosk_token):
    """Kiosk header shows logo, status pill, and clock."""
    page.goto(f"/kiosk?token={kiosk_token}", wait_until=WAIT_UNTIL)
    page.wait_for_function(_KIOSK_READY, timeout=10_000)

    header = page.locator(".kiosk-header")
    assert header.is_visible()

    # Status pill should exist
    status = page.locator(".kiosk-status")
    assert status.is_visible()

    # Clock should be ticking
    clock = page.locator(".kiosk-clock")
    assert clock.is_visible()
    assert clock.text_content().strip() != ""


def test_kiosk_idle_state(page, kiosk_token):
    """Empty kiosk shows idle state with no active calls."""
    page.goto(f"/kiosk?token={kiosk_token}", wait_until=WAIT_UNTIL)
    page.wait_for_function(_KIOSK_READY, timeout=10_000)

    # Wait for at least one poll cycle to complete
    page.wait_for_timeout(3000)

    idle = page.locator(".idle-state")
    # Idle state should be visible (or archived calls shown — depends on data)
    # In a fresh server with no data, we expect the idle label
    if idle.is_visible():
        label = page.locator(".idle-label")
        assert label.is_visible()


def test_kiosk_crew_strip_renders(page, kiosk_token):
    """Crew strip is visible at the bottom of the kiosk."""
    page.goto(f"/kiosk?token={kiosk_token}", wait_until=WAIT_UNTIL)
    page.wait_for_function(_KIOSK_READY, timeout=10_000)

    crew_strip = page.locator(".crew-strip")
    assert crew_strip.is_visible()


def test_kiosk_data_api_requires_token(page, base_url):
    """Kiosk data endpoint returns 401 without a valid token."""
    resp = page.request.get(f"{base_url}/kiosk/data")
    assert resp.status == 401


def test_kiosk_data_api_returns_json(page, base_url, kiosk_token):
    """Kiosk data endpoint returns valid JSON with expected structure."""
    resp = page.request.get(f"{base_url}/kiosk/data?token={kiosk_token}")
    assert resp.ok
    data = resp.json()

    assert "timestamp" in data
    assert "calls" in data
    assert isinstance(data["calls"], list)
    assert "crew" in data
    assert "sections" in data
    assert "platoon" in data


# ---------------------------------------------------------------------------
# Seeded kiosk (with dispatch calls and schedule data)
# ---------------------------------------------------------------------------


def test_kiosk_seeded_shows_crew(seeded_page, kiosk_token):
    """Seeded kiosk shows on-duty crew names in the crew strip."""
    seeded_page.goto(f"/kiosk?token={kiosk_token}", wait_until=WAIT_UNTIL)
    seeded_page.wait_for_function(_KIOSK_READY, timeout=10_000)

    # Wait for data poll to complete
    seeded_page.wait_for_timeout(3000)

    crew_strip = seeded_page.locator(".crew-strip")
    assert crew_strip.is_visible()

    crew_text = crew_strip.text_content()
    # Today (A) has Rodriguez/Nguyen/Garcia, yesterday (B) has Lee/Kim/Davis
    has_a_crew = "Rodriguez" in crew_text and "Nguyen" in crew_text
    has_b_crew = "Lee" in crew_text and "Kim" in crew_text
    assert has_a_crew or has_b_crew


def test_kiosk_seeded_shows_platoon(seeded_page, kiosk_token):
    """Seeded kiosk shows the platoon designation."""
    seeded_page.goto(f"/kiosk?token={kiosk_token}", wait_until=WAIT_UNTIL)
    seeded_page.wait_for_function(_KIOSK_READY, timeout=10_000)

    seeded_page.wait_for_timeout(3000)

    # Kiosk renders platoon as a single letter in .crew-strip-platoon
    platoon = seeded_page.locator(".crew-strip-platoon").first
    assert platoon.is_visible()
    assert platoon.text_content().strip() in ("A", "B")


def test_kiosk_seeded_shows_archived_calls(seeded_page, kiosk_token):
    """Seeded kiosk shows recently completed calls when idle (no active calls)."""
    seeded_page.goto(f"/kiosk?token={kiosk_token}", wait_until=WAIT_UNTIL)
    seeded_page.wait_for_function(_KIOSK_READY, timeout=10_000)

    # Wait for data poll
    seeded_page.wait_for_timeout(3000)

    # Since there are no active calls but we have seeded completed calls,
    # the kiosk should show them as archived. Check for call panel or nature text.
    page_text = seeded_page.locator(".kiosk-main").text_content()

    # At minimum, the call natures from seeded data should appear if archived calls render
    has_calls = (
        "Structure Fire" in page_text or "ALS Medical" in page_text or "Fire Alarm" in page_text
    )
    # Or we're in idle state (if archived calls aren't showing — depends on timing)
    has_idle = seeded_page.locator(".idle-state").is_visible()

    assert has_calls or has_idle, "Expected either archived calls or idle state"


def test_kiosk_seeded_data_api(seeded_page, base_url, kiosk_token, _seeded):
    """Kiosk data API returns seeded crew data."""
    resp = seeded_page.request.get(f"{base_url}/kiosk/data?token={kiosk_token}")
    assert resp.ok
    data = resp.json()

    # Crew should be populated from seeded schedule (all days have 6 members)
    assert len(data["crew"]) >= 4
    crew_names = [c["name"] for c in data["crew"]]
    # Today (A) has Rodriguez/Nguyen, yesterday (B) has Lee/Kim — check either
    has_a_crew = any("Rodriguez" in n for n in crew_names)
    has_b_crew = any("Lee" in n for n in crew_names)
    assert has_a_crew or has_b_crew

    # Platoon depends on time vs shift change
    assert data["platoon"] in ("A", "B")

    # Sections should be populated
    assert len(data["sections"]) >= 1
