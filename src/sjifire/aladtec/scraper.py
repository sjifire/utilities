"""Aladtec web scraper for member data via CSV export."""

import csv
import io
import logging
import re
from typing import Self

import httpx
from bs4 import BeautifulSoup

from sjifire.aladtec.models import Member
from sjifire.core.config import get_aladtec_credentials
from sjifire.core.normalize import format_phone, validate_email

logger = logging.getLogger(__name__)


def clean_title(title: str | None) -> str | None:
    """Clean up title field - handle newlines and duplicates.

    Args:
        title: Raw title string (may contain newlines and duplicates)

    Returns:
        Cleaned title (first unique value)
    """
    if not title:
        return None

    # Split on newlines and take first non-empty line
    lines = [line.strip() for line in title.replace("\r", "").split("\n")]
    for line in lines:
        if line:
            return line

    return None


class AladtecScraper:
    """Scraper for Aladtec member database using CSV export."""

    def __init__(self) -> None:
        """Initialize the scraper with credentials from environment."""
        self.base_url, self.username, self.password = get_aladtec_credentials()
        self.client: httpx.Client | None = None

    def __enter__(self) -> Self:
        """Enter context manager - create HTTP client."""
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=30.0,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager - close HTTP client."""
        if self.client:
            self.client.close()
            self.client = None

    def login(self) -> bool:
        """Log in to Aladtec.

        Returns:
            True if login successful, False otherwise
        """
        if not self.client:
            raise RuntimeError("Scraper must be used as context manager")

        logger.info(f"Logging in to {self.base_url}")

        # Get the login page first to establish session
        response = self.client.get(f"{self.base_url}/")

        if response.status_code != 200:
            logger.error(f"Failed to load login page: {response.status_code}")
            return False

        form_data = {
            "username": self.username,
            "password": self.password,
        }

        # Submit login to the correct endpoint
        login_url = f"{self.base_url}/index.php?action=login"
        response = self.client.post(login_url, data=form_data)

        # Check if login succeeded - look for dashboard elements or schedule
        if "schedule" in response.text.lower() or "dashboard" in response.text.lower():
            logger.info("Login successful")
            return True

        # Check for error messages
        if "invalid" in response.text.lower() or "incorrect" in response.text.lower():
            logger.error("Login failed - invalid credentials")
            return False

        # Check URL - successful login usually goes to schedule or home
        if "action=login" not in str(response.url):
            logger.info("Login successful")
            return True

        logger.error("Login failed - still on login page")
        return False

    def get_members(self, layout: str = "g_all", include_inactive: bool = False) -> list[Member]:
        """Fetch all members via CSV export.

        Args:
            layout: Layout preset to use. Options include:
                - "g_all": All Items - shows all columns (default)
                - "g_0": Member Information view
                - "g_1": General Information view
            include_inactive: If True, also fetch inactive members.
                Note: Inactive members are fetched from a separate layout (g_inactive)
                which has limited fields (name and status only - no email, phone, etc.)

        Returns:
            List of Member objects. If include_inactive=True, includes both active
            members (with full details) and inactive members (with limited details).
        """
        if not self.client:
            raise RuntimeError("Scraper must be used as context manager")

        logger.info(f"Fetching member CSV export (layout={layout})")

        member_list_url = f"{self.base_url}/index.php"

        # First load the page with the specified layout to set the view columns
        # load_layout controls which columns are shown (not load_filter_set)
        layout_params = {
            "action": "manage_members_view_member_list",
            "load_layout": layout,
            "pager": "100",
        }

        response = self.client.get(member_list_url, params=layout_params)
        if response.status_code != 200:
            logger.error(f"Failed to load member list: {response.status_code}")
            return []

        logger.debug(f"Loaded member list page with layout {layout}")

        # Now request the CSV export
        export_params = {
            "action": "manage_members_view_member_list",
            "export_mode": "1",
        }

        response = self.client.get(member_list_url, params=export_params)

        if response.status_code != 200:
            logger.error(f"Failed to get CSV export: {response.status_code}")
            return self._scrape_members_html()

        # Check if we got CSV content (should have commas and likely "Name" header)
        content = response.text
        if "," not in content or len(content) < 50:
            logger.warning("Export response doesn't look like CSV, trying HTML scrape")
            return self._scrape_members_html()

        logger.debug(f"Got CSV export ({len(content)} bytes)")
        members = self._parse_csv(content)

        # Always enrich with full position lists
        members = self.enrich_with_positions(members)

        # Fetch inactive members if requested
        if include_inactive:
            inactive_members = self._get_inactive_members()
            members.extend(inactive_members)
            logger.info(f"Total members (active + inactive): {len(members)}")

        return members

    def _get_inactive_members(self) -> list[Member]:
        """Fetch inactive members from Aladtec.

        Uses custom layout 307 which filters for inactive members and includes email.

        Returns:
            List of Member objects with status='Inactive'
        """
        if not self.client:
            return []

        logger.info("Fetching inactive members")

        member_list_url = f"{self.base_url}/index.php"

        # Load page with custom inactive layout that includes email
        # Layout 307 = Inactive members with Email column
        layout_params = {
            "action": "manage_members_view_member_list",
            "load_layout": "307",
            "pager": "100",
        }

        response = self.client.get(member_list_url, params=layout_params)
        if response.status_code != 200:
            logger.error(f"Failed to load inactive member list: {response.status_code}")
            return []

        # Export CSV
        export_params = {
            "action": "manage_members_view_member_list",
            "export_mode": "1",
        }

        response = self.client.get(member_list_url, params=export_params)
        if response.status_code != 200:
            logger.error(f"Failed to export inactive members: {response.status_code}")
            return []

        content = response.text
        if "," not in content or len(content) < 50:
            logger.warning("Inactive export doesn't look like CSV")
            return []

        # Parse the limited CSV format
        return self._parse_inactive_csv(content)

    def _parse_inactive_csv(self, csv_content: str) -> list[Member]:
        """Parse inactive members CSV.

        Uses custom layout 307 which includes:
        Member, Email, Work Group, Access Level, Member Status, Pay Period, Pay Profile, etc.

        Args:
            csv_content: Raw CSV string from inactive export

        Returns:
            List of Member objects with status='Inactive'
        """
        lines = csv_content.strip().split("\n")

        # Skip title and filter lines, find actual header row
        # Header row starts with 'Member,' (the column name, not "Member List" or "Member Filter")
        header_idx = 0
        for i, line in enumerate(lines):
            # Look for line that starts with Member and has Email or Work Group column
            if line.startswith("Member,") or line.startswith('"Member",'):
                header_idx = i
                break

        csv_data = "\n".join(lines[header_idx:])

        members = []
        reader = csv.DictReader(io.StringIO(csv_data))

        for row in reader:
            # Parse name from "Member" column (format: "Last, First")
            member_name = row.get("Member", "").strip()
            if not member_name:
                continue

            if "," in member_name:
                parts = member_name.split(",", 1)
                last_name = parts[0].strip()
                first_name = parts[1].strip() if len(parts) > 1 else ""
            else:
                parts = member_name.split(None, 1)
                first_name = parts[0] if parts else ""
                last_name = parts[1] if len(parts) > 1 else ""

            if not first_name or not last_name:
                continue

            # Get email - parse business and personal
            email_raw = row.get("Email", "").strip()
            email = None
            personal_email = None
            member_context = f"{first_name} {last_name}"
            if email_raw:
                emails = [e.strip() for e in email_raw.split(",") if e.strip()]
                for e in emails:
                    if e.endswith("@sjifire.org"):
                        if not email:
                            email = e
                    else:
                        if not personal_email:
                            # Validate personal email
                            validated = validate_email(e, member_context)
                            if validated:
                                personal_email = validated

            # Create member
            member_id = f"{first_name.lower()}.{last_name.lower()}"
            status = row.get("Member Status", "Inactive").strip()
            work_group = row.get("Work Group", "").strip() or None
            pay_profile = row.get("Pay Profile", "").strip() or None

            member = Member(
                id=member_id,
                first_name=first_name,
                last_name=last_name,
                email=email,
                personal_email=personal_email,
                status=status,
                work_group=work_group,
                pay_profile=pay_profile,
                positions=[],
            )
            members.append(member)

        logger.info(f"Parsed {len(members)} inactive members")
        return members

    def _parse_csv(self, csv_content: str) -> list[Member]:
        """Parse CSV content into Member objects.

        Aladtec CSV export has 2 header rows before the actual column headers:
        - Row 1: "Member List"
        - Row 2: "Member Filter: ..."
        - Row 3: Actual column headers

        Args:
            csv_content: Raw CSV string

        Returns:
            List of Member objects
        """
        lines = csv_content.strip().split("\n")

        # Skip the title and filter rows (first 2 lines)
        # Find the actual header row (contains "First Name" or similar)
        header_idx = 0
        for i, line in enumerate(lines):
            if "first name" in line.lower() or "email" in line.lower():
                header_idx = i
                break

        # Rejoin from the header row
        csv_data = "\n".join(lines[header_idx:])

        members = []
        reader = csv.DictReader(io.StringIO(csv_data))

        # Log available columns for debugging
        if reader.fieldnames:
            logger.debug(f"CSV columns: {list(reader.fieldnames)}")

        for row in reader:
            member = self._parse_csv_row(row)
            if member:
                members.append(member)

        logger.info(f"Parsed {len(members)} members from CSV")
        return members

    def _parse_csv_row(self, row: dict) -> Member | None:
        """Parse a CSV row into a Member.

        Handles various column naming conventions.

        Args:
            row: Dict from csv.DictReader

        Returns:
            Member object or None if parsing fails
        """
        # Normalize column names (lowercase, strip whitespace)
        normalized = {k.lower().strip(): v for k, v in row.items()}

        # Helper to get and strip a value
        def get_field(*keys: str) -> str | None:
            for key in keys:
                val = normalized.get(key)
                if val:
                    val = val.strip()
                    if val:
                        return val
            return None

        # Try to find first name
        first_name = get_field("first name", "firstname", "first", "given name") or ""

        # Try to find last name
        last_name = get_field("last name", "lastname", "last", "surname") or ""

        # Handle "Name" column that might be "Last, First" or "First Last"
        if not first_name or not last_name:
            name = normalized.get("name", "").strip()
            if "," in name:
                parts = name.split(",", 1)
                last_name = parts[0].strip()
                first_name = parts[1].strip() if len(parts) > 1 else ""
            elif name:
                parts = name.split(None, 1)
                first_name = parts[0]
                last_name = parts[1] if len(parts) > 1 else ""

        if not first_name or not last_name:
            return None

        # Email - parse business (@sjifire.org) and personal emails
        email_raw = get_field("email", "e-mail", "email address", "emails")
        email = None
        personal_email = None
        member_context = f"{first_name} {last_name}"
        if email_raw:
            # Split multiple emails (comma-separated in Aladtec)
            emails = [e.strip() for e in email_raw.split(",") if e.strip()]
            for e in emails:
                if e.endswith("@sjifire.org"):
                    if not email:  # Take first business email
                        email = e
                else:
                    if not personal_email:  # Take first personal email
                        # Validate personal email
                        validated = validate_email(e, member_context)
                        if validated:
                            personal_email = validated

        # Phone (cell/mobile) - format to standard
        phone_raw = get_field(
            "mobile phone", "phone", "phone number", "mobile", "cell", "cell phone"
        )
        phone = format_phone(phone_raw)

        # Home phone - format to standard
        home_phone_raw = get_field("home phone", "home", "landline")
        home_phone = format_phone(home_phone_raw)

        # Employee Type (may be multiple, comma-separated in CSV)
        employee_type_raw = get_field("employee type", "position", "positions", "rank", "role")
        employee_type = employee_type_raw
        positions: list[str] = []
        if employee_type_raw:
            positions = [p.strip() for p in employee_type_raw.split(",") if p.strip()]

        # Title - clean up newlines and duplicates (only from Title column)
        title_raw = get_field("title")
        title = clean_title(title_raw)

        # Status
        status = get_field("member status", "status")

        # Work group
        work_group = get_field("work group", "workgroup", "group")

        # Pay profile
        pay_profile = get_field("pay profile", "payroll profile", "payprofile", "pay")

        # Employee ID (remove commas from formatted numbers like "2,512")
        employee_id = get_field("employee id", "employeeid", "emp id", "id")
        if employee_id:
            employee_id = employee_id.replace(",", "")

        # Station assignment
        station_assignment = get_field(
            "station assignment", "station", "assignment", "assigned station"
        )

        # EVIP
        evip = get_field("evip", "e-vip")

        # Date hired
        date_hired = get_field("date hired", "hire date", "hired", "start date")

        # Generate member ID from employee_id or name
        member_id = employee_id or f"{first_name.lower()}.{last_name.lower()}"

        return Member(
            id=str(member_id).strip(),
            first_name=first_name,
            last_name=last_name,
            email=email,
            personal_email=personal_email,
            phone=phone,
            home_phone=home_phone,
            employee_type=employee_type,
            positions=positions,
            title=title,
            status=status,
            work_group=work_group,
            pay_profile=pay_profile,
            employee_id=employee_id,
            station_assignment=station_assignment,
            evip=evip,
            date_hired=date_hired,
        )

    def get_user_id_map(self) -> dict[str, str]:
        """Get mapping of member names to Aladtec user IDs.

        Uses the roster endpoint which returns all members in a single request.

        Returns:
            Dict mapping "Last, First" names to user IDs
        """
        if not self.client:
            return {}

        import json

        response = self.client.get(
            f"{self.base_url}/index.php",
            params={
                "action": "manage_member_roster_qactions",
                "qact": "7",
                "att_id": "POS-QUALI",
            },
        )

        if response.status_code != 200:
            return {}

        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            return {}

        user_map = {}
        for row in data.get("rows", []):
            row_id = row.get("id", "")
            user_id = row_id.replace("stfrow_", "")

            cell = row.get("cell", [])
            if len(cell) >= 3:
                name_match = re.search(r">([^<]+)<", cell[2])
                if name_match:
                    name = name_match.group(1)
                    user_map[name] = user_id

        logger.info(f"Got {len(user_map)} user IDs from roster")
        return user_map

    def get_member_positions(self, user_id: str) -> list[str]:
        """Get list of position names for a member.

        Args:
            user_id: Aladtec user ID

        Returns:
            List of position names
        """
        if not self.client:
            return []

        response = self.client.get(
            f"{self.base_url}/index.php",
            params={
                "action": "manage_members_view_member_information",
                "target_user_id": user_id,
            },
        )

        if response.status_code != 200:
            return []

        soup = BeautifulSoup(response.text, "html.parser")

        # Find the Positions section
        positions_header = soup.find(string=lambda t: t and "Positions:" in str(t) if t else False)
        if not positions_header:
            return []

        parent = positions_header.find_parent("td")
        if not parent:
            return []

        next_td = parent.find_next_sibling("td")
        if not next_td:
            return []

        # Try to get positions from list items first (view mode)
        positions = []
        for li in next_td.find_all("li"):
            text = li.get_text(strip=True)
            if text:
                positions.append(text)

        # If no list items found, try checked checkboxes (edit mode)
        if not positions:
            for cb in next_td.find_all("input", {"type": "checkbox"}):
                if cb.has_attr("checked"):
                    cb_id = cb.get("id", "")
                    label = next_td.find("label", {"for": cb_id})
                    if label:
                        positions.append(label.get_text(strip=True))

        return positions

    def enrich_with_positions(self, members: list[Member]) -> list[Member]:
        """Enrich members with their full position lists.

        Fetches position data from each member's detail page.

        Args:
            members: List of members from get_members()

        Returns:
            Same list with positions field populated
        """
        if not self.client:
            return members

        logger.info(f"Enriching {len(members)} members with position data")

        # Get user ID mapping
        user_map = self.get_user_id_map()

        # Match members to user IDs by name
        for member in members:
            # Try "Last, First" format
            name_key = f"{member.last_name}, {member.first_name}"
            user_id = user_map.get(name_key)

            if not user_id:
                continue

            positions = self.get_member_positions(user_id)
            # Always set positions (even if empty) to clear any incorrect initial value
            member.positions = positions
            if positions:
                logger.debug(f"{member.display_name}: {len(positions)} positions")

        logger.info("Position enrichment complete")
        return members

    def _scrape_members_html(self) -> list[Member]:
        """Fallback: scrape members from HTML if CSV export not available.

        Returns:
            List of Member objects
        """
        if not self.client:
            return []

        logger.info("Attempting HTML scrape fallback")

        # Try to find and click "All Items" filter, then scrape
        members_url = f"{self.base_url}/members"
        response = self.client.get(members_url, params={"filter": "all"})

        if response.status_code != 200:
            logger.error("Failed to load members page")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        members = []

        # Look for table rows
        table = soup.find("table")
        if table:
            rows = table.find_all("tr")[1:]  # Skip header
            for row in rows:
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2:
                    member = self._parse_html_row(cells)
                    if member:
                        members.append(member)

        logger.info(f"Scraped {len(members)} members from HTML")
        return members

    def _parse_html_row(self, cells) -> Member | None:
        """Parse an HTML table row into a Member.

        Args:
            cells: List of BeautifulSoup cell elements

        Returns:
            Member object or None
        """
        try:
            text_values = [cell.get_text(strip=True) for cell in cells]

            # Assume first column is name
            name_text = text_values[0] if text_values else ""
            if "," in name_text:
                parts = name_text.split(",", 1)
                last_name = parts[0].strip()
                first_name = parts[1].strip()
            else:
                parts = name_text.split(None, 1)
                first_name = parts[0] if parts else ""
                last_name = parts[1] if len(parts) > 1 else ""

            if not first_name or not last_name:
                return None

            # Look for emails in any cell - separate business and personal
            email = None
            personal_email = None
            for cell in cells:
                link = cell.find("a", href=lambda h: h and "mailto:" in h)
                if link:
                    found_email = link.get("href", "").replace("mailto:", "")
                    if found_email.endswith("@sjifire.org"):
                        if not email:
                            email = found_email
                    elif not personal_email:
                        personal_email = found_email
                text = cell.get_text(strip=True)
                if "@" in text:
                    if text.endswith("@sjifire.org"):
                        if not email:
                            email = text
                    elif not personal_email:
                        personal_email = text

            member_id = f"{first_name.lower()}.{last_name.lower()}"

            return Member(
                id=member_id,
                first_name=first_name,
                last_name=last_name,
                email=email,
                personal_email=personal_email,
            )
        except (IndexError, AttributeError):
            return None
