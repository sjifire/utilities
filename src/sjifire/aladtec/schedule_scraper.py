"""Aladtec schedule scraper for on-duty crew data."""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from sjifire.aladtec.client import AladtecClient

logger = logging.getLogger(__name__)


def _parse_time(time_str: str) -> datetime:
    """Parse a time string (HH:MM) into a datetime.time object.

    Args:
        time_str: Time in HH:MM format

    Returns:
        datetime with time set (date is 1900-01-01)
    """
    return datetime.strptime(time_str, "%H:%M")


@dataclass
class ScheduleEntry:
    """A scheduled person for a specific time period."""

    date: date
    section: str
    position: str
    name: str
    start_time: str  # HH:MM format
    end_time: str  # HH:MM format
    platoon: str = ""

    @property
    def is_full_shift(self) -> bool:
        """Check if this is a full 24-hour shift (1800-1800)."""
        return self.start_time == "18:00" and self.end_time == "18:00"

    @property
    def start_datetime(self) -> datetime:
        """Get full datetime for shift start."""
        time_obj = _parse_time(self.start_time)
        return datetime.combine(self.date, time_obj.time())

    @property
    def end_datetime(self) -> datetime:
        """Get full datetime for shift end."""
        time_obj = _parse_time(self.end_time)
        end_dt = datetime.combine(self.date, time_obj.time())
        # If end time is <= start time, it's the next day
        if self.end_time <= self.start_time:
            end_dt += timedelta(days=1)
        return end_dt


@dataclass
class DaySchedule:
    """All schedule entries for a single day."""

    date: date
    platoon: str
    entries: list[ScheduleEntry] = field(default_factory=list)

    def get_entries_by_section(self) -> dict[str, list[ScheduleEntry]]:
        """Group entries by section."""
        sections: dict[str, list[ScheduleEntry]] = {}
        for entry in self.entries:
            if entry.section not in sections:
                sections[entry.section] = []
            sections[entry.section].append(entry)
        return sections

    def get_filled_positions(
        self, exclude_sections: list[str] | None = None
    ) -> list[ScheduleEntry]:
        """Get all filled positions, optionally excluding certain sections.

        Args:
            exclude_sections: Section names to exclude (e.g., ["Administration"])

        Returns:
            List of entries with names assigned
        """
        exclude = set(exclude_sections or [])
        return [e for e in self.entries if e.name and e.section not in exclude]


class AladtecScheduleScraper(AladtecClient):
    """Scraper for Aladtec schedule data using AJAX endpoints."""

    def __init__(self) -> None:
        """Initialize the scraper with credentials from environment."""
        super().__init__(timeout=60.0)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _fetch_ajax_schedule(self, start_date: date) -> dict[str, str]:
        """Fetch schedule via AJAX for a specific start date.

        Args:
            start_date: Date to use as AJAX start date

        Returns:
            Dict mapping date strings (YYYY-MM-DD) to HTML content
        """
        if not self.client:
            raise RuntimeError("Scraper must be used as context manager")

        date_str = start_date.strftime("%Y-%m-%d")
        nav_date = start_date.strftime("%b %Y")  # e.g., "Feb 2026"

        # POST to navigate to the month (required before each fetch for consistency)
        self.client.post(
            f"{self.base_url}/index.php",
            params={"action": "manage_work_view_ajax"},
            data={
                "nav_date": nav_date,
                "schedule_view": "monthly_calendar",
                "qnav": "1",
                "version": "1.1",
            },
        )

        # Now fetch the AJAX data
        response = self.client.get(
            f"{self.base_url}/index.php",
            params={
                "action": "manage_work_view_ajax",
                "schedule_view": "monthly_calendar",
                "ajax_start_date": date_str,
                "mode": "ajax",
                "version": "1.1",
            },
        )

        if response.status_code != 200:
            logger.error(f"Failed to fetch schedule: {response.status_code}")
            return {}

        try:
            data = json.loads(response.text)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response: {e}")
            return {}

        return_data = data.get("return_data", "")

        if return_data.startswith("{"):
            try:
                return json.loads(return_data)
            except json.JSONDecodeError:
                pass

        return {}

    def fetch_month_schedule(self, month_start: date) -> dict[str, str]:
        """Fetch a full month's schedule via chained AJAX requests.

        The AJAX endpoint returns a variable-sized window of data (typically 10-30 days).
        We chain requests by using the day after the last fetched date as the next
        start date, continuing until the full month is covered.

        Args:
            month_start: First day of the month to fetch (day is ignored, uses 1st)

        Returns:
            Dict mapping date strings (YYYY-MM-DD) to HTML content for each day
        """
        if not self.client:
            raise RuntimeError("Scraper must be used as context manager")

        from calendar import monthrange

        # Normalize to first of month
        first_of_month = month_start.replace(day=1)
        month_str = first_of_month.strftime("%Y-%m")
        last_day = monthrange(first_of_month.year, first_of_month.month)[1]
        month_end = first_of_month.replace(day=last_day)

        all_data: dict[str, str] = {}
        current_start = first_of_month
        max_fetches = 10  # Safety limit to prevent infinite loops
        fetch_count = 0

        while current_start <= month_end and fetch_count < max_fetches:
            fetch_count += 1
            data = self._fetch_ajax_schedule(current_start)

            if not data:
                # No data returned, advance by 5 days and retry
                logger.debug(f"No data from {current_start}, advancing by 5 days")
                current_start += timedelta(days=5)
                continue

            # Collect dates for this month
            all_data.update(
                {
                    date_str: html
                    for date_str, html in data.items()
                    if date_str.startswith(month_str)
                }
            )

            # Find the last date returned and move to the day after
            last_fetched_str = max(data.keys())
            last_fetched = datetime.strptime(last_fetched_str, "%Y-%m-%d").date()
            next_start = last_fetched + timedelta(days=1)

            if next_start <= current_start:
                # No progress made, force advance to avoid infinite loop
                logger.debug(f"No progress from {current_start}, forcing advance")
                current_start += timedelta(days=5)
            else:
                current_start = next_start

        logger.debug(f"Fetched {len(all_data)} days for {month_str} in {fetch_count} requests")
        return all_data

    def parse_day_html(self, date_str: str, html: str) -> DaySchedule:
        """Parse a single day's HTML to extract schedule entries.

        Args:
            date_str: Date in YYYY-MM-DD format
            html: HTML content for the day

        Returns:
            DaySchedule with all entries for that day
        """
        day_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        soup = BeautifulSoup(html, "html.parser")
        entries: list[ScheduleEntry] = []

        # Get platoon from the shift-label
        platoon = ""
        platoon_elem = soup.find(class_="shift-label-display")
        if platoon_elem:
            platoon = platoon_elem.get_text(strip=True)

        # Find all schedule sections
        current_section = ""

        for div in soup.find_all("div", class_="sch_entry"):
            # Get section header
            header = div.find(class_="calendar-event-header")
            if header:
                current_section = header.get_text(strip=True)

            # Find all scheduled entries (rows with title containing schedule info)
            for row in div.find_all("tr", class_="calendar-event"):
                title = row.get("title", "")
                if not title:
                    continue

                # Parse the title format:
                # "Name<br/><p>Section / Position<br/>Date Start - Date End</p>"

                name_match = re.match(r"^([^<]+)", title)
                if not name_match:
                    continue
                name = name_match.group(1).strip()

                # Get position from the title
                pos_match = re.search(r"/ ([^<]+)<br/>", title)
                position = pos_match.group(1).strip() if pos_match else ""

                # Get times - handles both "19:00 - 20:00" and "20:00 - Tue, Feb 3 10:00"
                time_match = re.search(r"(\d{2}:\d{2})\s*-\s*[^>]*?(\d{2}:\d{2})", title)
                start_time = time_match.group(1) if time_match else "18:00"
                end_time = time_match.group(2) if time_match else "18:00"

                entries.append(
                    ScheduleEntry(
                        date=day_date,
                        section=current_section,
                        position=position,
                        name=name,
                        start_time=start_time,
                        end_time=end_time,
                        platoon=platoon,
                    )
                )

        return DaySchedule(date=day_date, platoon=platoon, entries=entries)

    def get_schedule_range(
        self,
        start_date: date,
        end_date: date,
    ) -> list[DaySchedule]:
        """Fetch schedule for a date range.

        Args:
            start_date: First date to include
            end_date: Last date to include

        Returns:
            List of DaySchedule objects, one per day with data
        """
        if not self.client:
            raise RuntimeError("Scraper must be used as context manager")

        schedules: list[DaySchedule] = []
        all_day_data: dict[str, str] = {}

        # Fetch by month
        current = start_date.replace(day=1)
        end_month = end_date.replace(day=1)

        while current <= end_month:
            logger.info(f"Fetching {current.strftime('%B %Y')}...")
            month_data = self.fetch_month_schedule(current)

            if month_data:
                all_day_data.update(month_data)

            # Move to next month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

        # Parse all days within our date range
        for date_str, html in sorted(all_day_data.items()):
            try:
                day_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            if start_date <= day_date <= end_date:
                day_schedule = self.parse_day_html(date_str, html)
                if day_schedule.entries:
                    schedules.append(day_schedule)

        logger.info(f"Fetched {len(schedules)} days with schedule data")
        return schedules

    def get_schedule_months_ahead(self, months: int = 6) -> list[DaySchedule]:
        """Fetch schedule from today through N months ahead.

        Args:
            months: Number of months to fetch (default 6)

        Returns:
            List of DaySchedule objects
        """
        today = date.today()
        # Calculate end date - go to the last day of the target month
        end_month = today.month + months
        end_year = today.year
        while end_month > 12:
            end_month -= 12
            end_year += 1

        # Get last day of end month
        if end_month == 12:
            end_date = date(end_year, 12, 31)
        else:
            end_date = date(end_year, end_month + 1, 1) - timedelta(days=1)

        return self.get_schedule_range(today, end_date)


def save_schedules(schedules: list[DaySchedule], path: str | Path) -> None:
    """Save schedule data to a JSON file for caching.

    Args:
        schedules: List of DaySchedule objects to save
        path: Path to write JSON file
    """
    data = []
    for day in schedules:
        day_dict = {
            "date": day.date.isoformat(),
            "platoon": day.platoon,
            "entries": [
                {
                    "date": entry.date.isoformat(),
                    "section": entry.section,
                    "position": entry.position,
                    "name": entry.name,
                    "start_time": entry.start_time,
                    "end_time": entry.end_time,
                    "platoon": entry.platoon,
                }
                for entry in day.entries
            ],
        }
        data.append(day_dict)

    Path(path).write_text(json.dumps(data, indent=2))
    logger.info(f"Saved {len(schedules)} days of schedule data to {path}")


def load_schedules(path: str | Path) -> list[DaySchedule]:
    """Load schedule data from a JSON cache file.

    Args:
        path: Path to JSON file

    Returns:
        List of DaySchedule objects

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If file is invalid JSON
    """
    content = Path(path).read_text()
    data = json.loads(content)

    schedules = []
    for day_dict in data:
        entries = [
            ScheduleEntry(
                date=datetime.strptime(e["date"], "%Y-%m-%d").date(),
                section=e["section"],
                position=e["position"],
                name=e["name"],
                start_time=e["start_time"],
                end_time=e["end_time"],
                platoon=e.get("platoon", ""),
            )
            for e in day_dict["entries"]
        ]
        day = DaySchedule(
            date=datetime.strptime(day_dict["date"], "%Y-%m-%d").date(),
            platoon=day_dict["platoon"],
            entries=entries,
        )
        schedules.append(day)

    logger.info(f"Loaded {len(schedules)} days of schedule data from {path}")
    return schedules
