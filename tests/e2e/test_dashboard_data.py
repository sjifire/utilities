"""Tests that verify the dashboard renders seeded fixture data correctly.

These tests use the ``seeded_page`` fixture which ensures the server's
in-memory stores contain representative dispatch calls and crew schedule
data before loading the dashboard.
"""

import pytest

pytestmark = pytest.mark.e2e

WAIT_UNTIL = "domcontentloaded"

# JS expression that resolves once Alpine has fetched /dashboard/data
_DATA_LOADED = (
    "document.querySelector('.stat-value') && "
    "document.querySelector('.stat-value').textContent.trim() !== ''"
)


def _goto_tab(page, tab_id: str):
    """Navigate to the dashboard, wait for data, then click a tab."""
    page.goto("/dashboard", wait_until=WAIT_UNTIL)
    page.wait_for_function(_DATA_LOADED, timeout=15_000)
    if tab_id != "overview":
        page.locator(f"button.nav-tab:text('{_TAB_LABELS[tab_id]}')").click()
        page.wait_for_timeout(300)  # allow Alpine x-show transition


_TAB_LABELS = {
    "calls": "Recent Calls",
    "crew": "On Duty",
    "overview": "Overview",
}


# ---------------------------------------------------------------------------
# Overview tab — stat cards
# ---------------------------------------------------------------------------


def test_overview_stat_cards_populated(seeded_page):
    """Stat cards show non-zero counts from seeded data."""
    _goto_tab(seeded_page, "overview")

    cards = seeded_page.locator(".stat-card")
    assert cards.count() >= 3

    # Recent Calls stat should show "3" (we seeded 3 calls)
    recent_calls_stat = cards.nth(2).locator(".stat-value")
    recent_calls_stat.wait_for(state="visible")
    assert recent_calls_stat.text_content().strip() == "3"

    # On Duty count should show "6" (we seeded 6 crew members)
    on_duty_stat = cards.nth(1).locator(".stat-value")
    on_duty_stat.wait_for(state="visible")
    assert on_duty_stat.text_content().strip() == "6"


# ---------------------------------------------------------------------------
# Recent Calls tab — table rows
# ---------------------------------------------------------------------------


def test_recent_calls_table_populated(seeded_page):
    """Recent Calls tab shows rows for seeded dispatch calls."""
    _goto_tab(seeded_page, "calls")

    rows = seeded_page.locator(".data-table:visible tbody tr")
    assert rows.count() == 3


def test_recent_calls_shows_nature(seeded_page):
    """Call nature text appears in the table."""
    _goto_tab(seeded_page, "calls")

    page_text = seeded_page.locator(".data-table:visible").text_content()
    assert "Structure Fire" in page_text
    assert "ALS Medical" in page_text
    assert "Fire Alarm" in page_text


def test_recent_calls_shows_address(seeded_page):
    """Call addresses appear in the table."""
    _goto_tab(seeded_page, "calls")

    page_text = seeded_page.locator(".data-table:visible").text_content()
    assert "123 Main St" in page_text
    assert "456 Spring St" in page_text


def test_recent_calls_severity_dots(seeded_page):
    """Severity dots render with correct classes."""
    _goto_tab(seeded_page, "calls")

    dots = seeded_page.locator(".data-table:visible .severity-dot")
    assert dots.count() == 3

    # Collect severity classes
    classes = [dots.nth(i).get_attribute("class") or "" for i in range(dots.count())]
    severity_values = []
    for cls in classes:
        if "high" in cls:
            severity_values.append("high")
        elif "medium" in cls:
            severity_values.append("medium")
        else:
            severity_values.append("low")

    # Structure Fire → medium, ALS Medical → high, Fire Alarm → low
    assert "high" in severity_values
    assert "medium" in severity_values
    assert "low" in severity_values


# ---------------------------------------------------------------------------
# On Duty tab — crew grid
# ---------------------------------------------------------------------------


def test_crew_grid_populated(seeded_page):
    """On Duty tab shows crew member names from seeded schedule."""
    _goto_tab(seeded_page, "crew")

    crew_names = seeded_page.locator(".crew-row-name:visible")
    assert crew_names.count() >= 4

    all_text = seeded_page.locator(".crew-grid:visible").first.text_content()
    assert "Capt Rodriguez" in all_text
    assert "Lt Nguyen" in all_text
    assert "FF Garcia" in all_text


def test_crew_sections_grouped(seeded_page):
    """Crew members are grouped by section with headers."""
    _goto_tab(seeded_page, "crew")

    section_headers = seeded_page.locator(".crew-sec-hdr:visible")
    header_texts = [
        section_headers.nth(i).text_content().strip() for i in range(section_headers.count())
    ]
    # We seeded Chief Officer, S31, and S32 sections
    assert any("31" in h for h in header_texts)


def test_crew_position_badges(seeded_page):
    """Position badges render for crew members."""
    _goto_tab(seeded_page, "crew")

    badges = seeded_page.locator(".crew-row:visible .badge")
    assert badges.count() >= 4

    badge_texts = [badges.nth(i).text_content().strip() for i in range(badges.count())]
    assert "Captain" in badge_texts
    assert "Lieutenant" in badge_texts
    assert "Firefighter" in badge_texts


def test_platoon_displayed(seeded_page):
    """Platoon letter is displayed in the crew header."""
    _goto_tab(seeded_page, "crew")

    crew_text = seeded_page.locator(".crew-grid:visible").first.text_content()
    # Platoon "A" should appear somewhere in the crew panel
    assert " A" in crew_text or "Platoon A" in crew_text or "(A)" in crew_text


# ---------------------------------------------------------------------------
# Dashboard data API — direct JSON verification
# ---------------------------------------------------------------------------


def test_dashboard_data_api(seeded_page, base_url, _seeded):
    """The /dashboard/data API returns seeded data in the expected structure."""
    resp = seeded_page.request.get(f"{base_url}/dashboard/data")
    assert resp.ok
    data = resp.json()

    # Recent calls present
    assert len(data["recent_calls"]) == 3

    # Crew present
    assert data["unique_crew_count"] == 6
    assert data["platoon"] == "A"

    # Sections are populated
    assert len(data["sections"]) >= 2
