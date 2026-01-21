"""ESO Suite web scraper using Playwright."""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, async_playwright

from ..utils.config import Settings, get_settings
from .models import Personnel


class ESOScraper:
    """Scraper for ESO Suite web application."""

    ESO_LOGIN_URL = "https://www.esosuite.net/login"
    ESO_NFIRS_SEARCH_URL = "https://www.esosuite.net/nfirs/#/search?template=2"

    DEFAULT_TIMEOUT = 60000
    NAVIGATION_TIMEOUT = 120000

    def __init__(self, settings: Optional[Settings] = None):
        """Initialize scraper with settings."""
        self.settings = settings or get_settings()
        self._browser = None
        self._context = None
        self._page: Optional[Page] = None

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def start(self, headless: bool = True):
        """Start browser and create page."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=headless)
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            accept_downloads=True,
        )
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self.DEFAULT_TIMEOUT)
        self._page.set_default_navigation_timeout(self.NAVIGATION_TIMEOUT)

    async def close(self):
        """Close browser and cleanup."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def screenshot(self, name: str) -> Optional[Path]:
        """Take a screenshot and save it."""
        if not self._page:
            return None

        self.settings.screenshots_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{name}_{timestamp}.png"
        filepath = self.settings.screenshots_dir / filename
        await self._page.screenshot(path=str(filepath), full_page=True)
        print(f"Screenshot saved: {filename}")
        return filepath

    async def login(self) -> bool:
        """Log in to ESO Suite."""
        if not self._page:
            raise RuntimeError("Browser not started. Call start() first.")

        if not self.settings.has_eso_credentials:
            raise ValueError("ESO credentials not configured")

        print("Navigating to login page...")
        await self._page.goto(self.ESO_LOGIN_URL)

        # Step 1: Enter organization code
        print("Entering organization code...")
        await self._page.fill("#OrganizationCode", self.settings.eso_agency)
        await self._page.click('button[type="button"].eso-button-primary')

        # Wait for login form
        await self._page.wait_for_selector(
            'input[name="username-input"]', state="visible"
        )

        # Step 2: Enter credentials
        print("Entering credentials...")
        await self._page.fill('input[name="username-input"]', self.settings.eso_username)
        await self._page.fill('input[type="password"]', self.settings.eso_password)
        await self._page.click("#login-button")

        # Wait for dashboard
        await self._page.wait_for_load_state("networkidle")
        print("Login successful")
        return True

    async def navigate_to_incident(self, incident_number: str) -> bool:
        """Navigate to a specific incident."""
        if not self._page:
            raise RuntimeError("Browser not started")

        print(f"Navigating to incident {incident_number}...")
        await self._page.goto(self.ESO_NFIRS_SEARCH_URL)
        await self._page.wait_for_load_state("networkidle")
        await self._page.wait_for_selector("table tbody tr", state="visible")

        # Find and click the incident
        incident_link = await self._page.query_selector(f'a:has-text("{incident_number}")')
        if incident_link:
            await incident_link.click()
            await self._page.wait_for_load_state("networkidle")
            print(f"Opened incident {incident_number}")
            return True

        return False

    async def scrape_personnel_from_incidents(
        self, max_incidents: int = 30
    ) -> list[Personnel]:
        """Scrape personnel from multiple incidents."""
        if not self._page:
            raise RuntimeError("Browser not started")

        personnel_map: dict[str, Personnel] = {}

        print(f"\nScraping personnel from up to {max_incidents} incidents...")
        await self._page.goto(self.ESO_NFIRS_SEARCH_URL)
        await self._page.wait_for_load_state("networkidle")
        await self._page.wait_for_selector("table tbody tr", state="visible")

        rows = await self._page.query_selector_all("table tbody tr")
        incidents_to_scrape = min(len(rows), max_incidents)
        print(f"Found {len(rows)} incidents, scraping {incidents_to_scrape}...")

        for i in range(incidents_to_scrape):
            try:
                # Re-navigate each time (DOM changes after navigation)
                await self._page.goto(self.ESO_NFIRS_SEARCH_URL)
                await self._page.wait_for_load_state("networkidle")
                await self._page.wait_for_selector("table tbody tr", state="visible")

                incident_rows = await self._page.query_selector_all("table tbody tr")
                if i >= len(incident_rows):
                    break

                row = incident_rows[i]
                link = await row.query_selector("a")
                if not link:
                    continue

                incident_text = await link.text_content()
                print(f"  [{i + 1}/{incidents_to_scrape}] {incident_text.strip() if incident_text else 'Unknown'}")

                await link.click()
                await self._page.wait_for_load_state("networkidle")
                await self._page.wait_for_timeout(1000)

                # Navigate to Unit Reports tab
                unit_reports_tab = await self._page.query_selector("text=UNIT REPORTS")
                if unit_reports_tab:
                    await unit_reports_tab.click()
                    await self._page.wait_for_load_state("networkidle")
                    await self._page.wait_for_timeout(1000)

                # Click on Personnel sub-tab
                clicked = await self._page.evaluate("""() => {
                    const elements = Array.from(document.querySelectorAll('*'));
                    for (const el of elements) {
                        const text = el.textContent?.trim();
                        if (text === 'Personnel' && el.tagName !== 'SCRIPT' && el.children.length === 0) {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }""")

                if clicked:
                    await self._page.wait_for_load_state("networkidle")
                    await self._page.wait_for_timeout(1500)

                    if i == 0:
                        await self.screenshot("personnel_tab")

                    # Extract personnel
                    page_text = await self._page.evaluate("() => document.body.innerText")
                    new_personnel = self._parse_personnel_from_text(page_text)

                    for person in new_personnel:
                        if person.eso_id not in personnel_map:
                            personnel_map[person.eso_id] = person
                            print(f"    + {person.full_name} ({person.eso_id})")

            except Exception as e:
                print(f"    Error: {e}")

        return sorted(personnel_map.values(), key=lambda p: p.last_name)

    def _parse_personnel_from_text(self, text: str) -> list[Personnel]:
        """Parse personnel from page text."""
        personnel = []
        seen_ids: set[str] = set()

        # Pattern: "LASTNAME, FIRSTNAME - ID" or "LastName, FirstName - ID"
        pattern = r"([A-Za-z'-]+),\s*([A-Za-z\"'\s-]+)\s*[-–]\s*(\d{2,5})"

        for match in re.finditer(pattern, text, re.IGNORECASE):
            last_name = match.group(1).strip()
            # Remove quotes from nicknames and get first part
            first_name = match.group(2).strip().replace('"', "").replace("'", "").split()[0]
            eso_id = match.group(3).strip()

            # Skip duplicates and invalid entries
            if eso_id in seen_ids:
                continue
            if not last_name or not first_name or not eso_id:
                continue
            if last_name[0].isdigit() or first_name[0].isdigit():
                continue
            if last_name.lower() in ("unit", "incident"):
                continue

            seen_ids.add(eso_id)
            personnel.append(Personnel.from_parsed(last_name, first_name, eso_id))

        return personnel
