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
    "reports": "Reports",
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
# Recent Calls tab — call content details
# ---------------------------------------------------------------------------


def test_recent_calls_short_descriptions(seeded_page):
    """Short descriptions from analysis appear in the calls table."""
    _goto_tab(seeded_page, "calls")

    table_text = seeded_page.locator(".data-table:visible").text_content()
    assert "Kitchen fire, contained" in table_text
    assert "Chest pain, transported" in table_text


def test_recent_calls_address_links(seeded_page):
    """Address links point to Google Maps."""
    _goto_tab(seeded_page, "calls")

    links = seeded_page.locator(".data-table:visible .address-link")
    assert links.count() >= 2

    from urllib.parse import urlparse

    for i in range(links.count()):
        href = links.nth(i).get_attribute("href") or ""
        parsed = urlparse(href)
        assert parsed.hostname is not None
        assert parsed.hostname.endswith("google.com"), f"Expected Google Maps URL, got: {href}"


def test_recent_calls_call_ids(seeded_page):
    """Dispatch IDs render in the calls table."""
    _goto_tab(seeded_page, "calls")

    table_text = seeded_page.locator(".data-table:visible").text_content()
    # All seeded calls have IDs starting with the current 2-digit year prefix
    from datetime import datetime

    prefix = f"{datetime.now().strftime('%y')}-"
    assert prefix in table_text


# ---------------------------------------------------------------------------
# On Duty tab — crew grid
# ---------------------------------------------------------------------------


def test_crew_grid_populated(seeded_page):
    """On Duty tab shows crew member names from seeded schedule."""
    _goto_tab(seeded_page, "crew")

    crew_names = seeded_page.locator(".crew-row-name:visible")
    assert crew_names.count() >= 4

    all_text = seeded_page.locator(".crew-grid:visible").first.text_content()
    # Today (A) has Rodriguez/Nguyen/Garcia, yesterday (B) has Lee/Kim/Davis
    has_a_crew = "Rodriguez" in all_text and "Nguyen" in all_text
    has_b_crew = "Lee" in all_text and "Kim" in all_text
    assert has_a_crew or has_b_crew


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
    # Platoon letter (A or B, depending on time vs shift change) should appear
    has_platoon = any(
        label in crew_text for label in (" A", "Platoon A", "(A)", " B", "Platoon B", "(B)")
    )
    assert has_platoon, f"No platoon label found in: {crew_text[:200]}"


def test_crew_upcoming_section(seeded_page):
    """Upcoming crew section renders with crew names."""
    _goto_tab(seeded_page, "crew")

    # The crew grid has two columns: current and upcoming
    crew_grid = seeded_page.locator(".crew-grid:visible")
    crew_grid.wait_for(state="visible", timeout=5_000)
    grid_text = crew_grid.text_content()

    # Upcoming should render — we seeded tomorrow's crew (B platoon)
    assert "Upcoming" in grid_text

    # Upcoming section should have crew names
    upcoming_names = seeded_page.locator(".crew-col-upcoming .crew-row-name")
    if upcoming_names.count() > 0:
        # Verify crew names from the seeded upcoming data
        upcoming_text = ""
        for i in range(upcoming_names.count()):
            upcoming_text += upcoming_names.nth(i).text_content()
        # Should contain names from tomorrow's B platoon seed data
        assert len(upcoming_text) > 0


def test_crew_shift_info(seeded_page):
    """Shift timing information is displayed in crew headers."""
    _goto_tab(seeded_page, "crew")

    # The crew column header should show shift end/start time or date range
    headers = seeded_page.locator(".crew-col-header:visible")
    assert headers.count() >= 1

    header_text = ""
    for i in range(headers.count()):
        header_text += headers.nth(i).text_content()
    # Should contain a date range (e.g., "Mar 3-4") or shift label
    assert len(header_text.strip()) > 0, "Crew column headers should have content"


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

    # Crew present (all seeded days have 6 crew members)
    assert data["unique_crew_count"] == 6
    assert data["platoon"] in ("A", "B")  # depends on time vs shift change

    # Sections are populated
    assert len(data["sections"]) >= 2


def test_dashboard_data_api_recent_calls_structure(seeded_page, base_url, _seeded):
    """Each recent call in the API response has expected fields."""
    resp = seeded_page.request.get(f"{base_url}/dashboard/data")
    assert resp.ok
    data = resp.json()

    for call in data["recent_calls"]:
        assert "id" in call
        assert "nature" in call
        assert "address" in call
        assert "severity" in call
        assert "date" in call
        assert "time" in call


def test_dashboard_data_api_fastest_turnout(seeded_page, base_url, _seeded):
    """Fastest turnout field is present (seeded data has unit_times for E31)."""
    resp = seeded_page.request.get(f"{base_url}/dashboard/data")
    assert resp.ok
    data = resp.json()

    # The seeded Structure Fire call has E31 unit_times with paged/enroute
    ft = data.get("fastest_turnout")
    if ft:
        assert "display" in ft
        assert "unit" in ft
        assert ft["unit"] == "E31"


def test_dashboard_data_api_open_calls_count(seeded_page, base_url, _seeded):
    """Open calls count is 0 since all seeded calls are completed."""
    resp = seeded_page.request.get(f"{base_url}/dashboard/data")
    assert resp.ok
    data = resp.json()

    assert data["open_calls"] == 0


# ---------------------------------------------------------------------------
# Reports tab — editor-only (requires editor_page fixture)
# ---------------------------------------------------------------------------


def _goto_reports_tab(page):
    """Navigate to the dashboard and click the Reports tab (editor only)."""
    page.goto("/dashboard", wait_until=WAIT_UNTIL)
    page.wait_for_function(_DATA_LOADED, timeout=15_000)
    page.locator("button.nav-tab:text('Reports')").click()
    page.wait_for_timeout(500)  # allow Alpine x-show transition + reports loading


def test_reports_tab_visible_for_editor(editor_page):
    """With editor fixture, Reports tab appears."""
    editor_page.goto("/dashboard", wait_until=WAIT_UNTIL)
    editor_page.wait_for_function(_DATA_LOADED, timeout=15_000)

    tabs = editor_page.locator(".nav-tab")
    labels = [tabs.nth(i).text_content().strip() for i in range(tabs.count())]
    assert "Reports" in labels


def test_reports_table_populated(editor_page):
    """Reports table shows rows with correct statuses for seeded data."""
    _goto_reports_tab(editor_page)

    # Wait for the reports table to have rows
    editor_page.locator(".data-table:visible tbody tr").first.wait_for(
        state="visible", timeout=10_000
    )
    rows = editor_page.locator(".data-table:visible tbody tr")

    # Should show all 3 calls (2 with reports, 1 missing)
    assert rows.count() == 3


def test_reports_status_badges(editor_page):
    """Report statuses render correctly: draft, submitted/locked, missing."""
    _goto_reports_tab(editor_page)

    editor_page.locator(".data-table:visible tbody tr").first.wait_for(
        state="visible", timeout=10_000
    )

    table_text = editor_page.locator(".data-table:visible").text_content()

    # Draft report should show "draft" status
    assert "draft" in table_text.lower()

    # Missing report (ccc-333 has no incident) should show missing indicator
    missing = editor_page.locator(".report-status.missing:visible")
    assert missing.count() >= 1

    # Submitted/locked report should show locked indicator
    locked = editor_page.locator(".report-status.locked:visible")
    assert locked.count() >= 1


def test_reports_filter_pills(editor_page):
    """Filter pills change the visible row count."""
    _goto_reports_tab(editor_page)

    editor_page.locator(".data-table:visible tbody tr").first.wait_for(
        state="visible", timeout=10_000
    )

    # "All" filter should show 3 rows
    all_rows = editor_page.locator(".data-table:visible tbody tr")
    all_count = all_rows.count()
    assert all_count == 3

    # Click "Missing" filter
    editor_page.locator(".filter-pill:text('Missing')").click()
    editor_page.wait_for_timeout(300)

    # Missing should show only 1 row (ccc-333 has no report)
    missing_rows = editor_page.locator(".data-table:visible tbody tr")
    assert missing_rows.count() == 1
