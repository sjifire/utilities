"""Smoke tests for the ops dashboard.

These tests verify the dashboard loads, Alpine.js initializes, and tab
navigation works correctly in a real browser.
"""

import pytest

pytestmark = pytest.mark.e2e

# The dashboard fetches /dashboard/data which calls iSpyFire (30s timeout in
# dev mode with no credentials).  Use domcontentloaded so Playwright doesn't
# block on that background fetch.
WAIT_UNTIL = "domcontentloaded"


def test_dashboard_loads(page):
    """Page loads with the expected title."""
    page.goto("/dashboard", wait_until=WAIT_UNTIL)
    assert "SJIF&R Dashboard" in page.title()


def test_dashboard_alpine_initializes(page):
    """Alpine.js initializes and renders tab buttons."""
    page.goto("/dashboard", wait_until=WAIT_UNTIL)
    # Alpine renders .nav-tab buttons from the tabs() getter
    page.wait_for_selector(".nav-tab", state="visible", timeout=10_000)
    assert page.locator(".nav-tab").count() >= 3


def test_tabs_visible(page):
    """Nav bar shows the expected core tabs."""
    page.goto("/dashboard", wait_until=WAIT_UNTIL)
    page.wait_for_selector(".nav-tab", state="visible", timeout=10_000)
    tabs = page.locator(".nav-tab")
    labels = [tabs.nth(i).text_content().strip() for i in range(tabs.count())]
    assert "Overview" in labels
    assert "Recent Calls" in labels
    assert "On Duty" in labels
    # Reports tab visibility depends on editor role — tested separately
    # in test_dashboard_data.py::test_reports_tab_visible_for_editor


def test_tab_navigation(page):
    """Clicking a tab updates the URL hash and shows the correct panel."""
    page.goto("/dashboard", wait_until=WAIT_UNTIL)
    page.wait_for_selector(".nav-tab", state="visible", timeout=10_000)

    # Click "Recent Calls" tab
    page.locator(".nav-tab", has_text="Recent Calls").click()
    page.wait_for_url("**/dashboard#calls")

    # Click "On Duty" tab
    page.locator(".nav-tab", has_text="On Duty").click()
    page.wait_for_url("**/dashboard#crew")

    # Click back to "Overview"
    page.locator(".nav-tab", has_text="Overview").click()
    page.wait_for_url("**/dashboard#overview")


def test_overview_default(page):
    """Overview tab is active on initial load (no hash)."""
    page.goto("/dashboard", wait_until=WAIT_UNTIL)
    page.wait_for_selector(".nav-tab", state="visible", timeout=10_000)
    overview_tab = page.locator(".nav-tab", has_text="Overview")
    assert "active" in (overview_tab.get_attribute("class") or "")


def test_health_endpoint(page, base_url):
    """Health endpoint returns expected JSON."""
    resp = page.request.get(f"{base_url}/health")
    assert resp.ok
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "sjifire-ops"


# ---------------------------------------------------------------------------
# Nav bar
# ---------------------------------------------------------------------------

# JS expression that resolves once Alpine has fetched /dashboard/data
_DATA_LOADED = (
    "document.querySelector('.stat-value') && "
    "document.querySelector('.stat-value').textContent.trim() !== ''"
)


def test_nav_bar_status_pill(seeded_page):
    """Open calls status pill renders in the nav bar."""
    seeded_page.goto("/dashboard", wait_until=WAIT_UNTIL)
    seeded_page.wait_for_function(_DATA_LOADED, timeout=15_000)

    pill = seeded_page.locator(".status-pill")
    pill.wait_for(state="visible", timeout=5_000)
    pill_text = pill.text_content()
    # All seeded calls are completed, so no active calls
    assert "No Active Calls" in pill_text or "0" in pill_text


def test_nav_bar_date_display(seeded_page):
    """Date is displayed in the nav bar right section."""
    seeded_page.goto("/dashboard", wait_until=WAIT_UNTIL)
    seeded_page.wait_for_function(_DATA_LOADED, timeout=15_000)

    date_el = seeded_page.locator(".nav-status-date")
    date_el.wait_for(state="visible", timeout=5_000)
    date_text = date_el.text_content().strip()
    # Should contain a day name (e.g., "Tuesday") and a year
    from datetime import datetime

    now = datetime.now()
    assert str(now.year) in date_text
    # Should contain a weekday name
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    assert any(day in date_text for day in weekdays), f"No weekday found in: {date_text}"
