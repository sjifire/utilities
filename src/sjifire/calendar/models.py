"""Data models for calendar events."""

import re
from dataclasses import dataclass, field
from datetime import date
from html import escape

from sjifire.aladtec.client import get_aladtec_credentials
from sjifire.core.config import get_org_config

# Position ordering: officers first, then AO, then firefighters
POSITION_ORDER = {
    "Chief": 0,
    "Captain": 1,
    "Lieutenant": 2,
    "Apparatus Operator": 3,
    "Firefighter": 4,
    "EMT": 5,
    "Support": 6,
    "Marine Pilot": 7,
    "Marine Mate": 8,
    "Backup Duty Officer": 9,
}


def get_aladtec_url() -> str:
    """Get Aladtec URL from credentials."""
    url, _, _ = get_aladtec_credentials()
    return url


def section_sort_key(section: str) -> tuple[int, int, str]:
    """Sort key for sections with custom priority order.

    Sorting priority (soft matching, case-insensitive):
    1. Chief (matches "chief", "chief officer", "chief on call", etc.)
    2. S31 (the primary station)
    3. Backup (matches "backup", "backup duty officer", etc.)
    4. Support (matches "support")
    5. Other stations (S32, S33, etc.) - sorted by number
    6. Everything else - sorted alphabetically
    """
    section_lower = section.lower()

    # Priority 0: Chief (soft match)
    if "chief" in section_lower:
        return (0, 0, section)

    # Priority 1: S31 specifically
    if section_lower == "s31" or section_lower == "station 31":
        return (1, 0, section)

    # Priority 2: Backup (soft match)
    if "backup" in section_lower:
        return (2, 0, section)

    # Priority 3: Support (soft match)
    if "support" in section_lower:
        return (3, 0, section)

    # Priority 4: Other stations (sorted by number)
    station_match = re.match(r"^S(\d+)$", section, re.IGNORECASE)
    if station_match:
        return (4, int(station_match.group(1)), section)

    # Priority 5: Everything else alphabetically
    return (5, 0, section)


def position_sort_key(position: str) -> int:
    """Sort key for positions within a section."""
    cleaned = clean_position(position)
    for key, order in POSITION_ORDER.items():
        if key in cleaned:
            return order
    return 99


def clean_position(position: str) -> str:
    """Clean up position title - remove colons."""
    return position.replace(":", "").strip()


@dataclass
class CrewMember:
    """A crew member with contact info."""

    name: str
    position: str
    email: str | None = None
    phone: str | None = None

    def format_html(self) -> str:
        """Format as HTML with contact links."""
        pos = clean_position(self.position)
        parts = [f"<b>{escape(pos)}:</b> {escape(self.name)}"]

        contact_links = []
        if self.email:
            contact_links.append(f'<a href="mailto:{escape(self.email)}">email</a>')
        if self.phone:
            # Format phone for tel: link (digits only)
            phone_digits = "".join(c for c in self.phone if c.isdigit())
            if len(phone_digits) == 10:
                phone_digits = "1" + phone_digits
            contact_links.append(f'<a href="tel:+{phone_digits}">{escape(self.phone)}</a>')

        if contact_links:
            parts.append(" | ".join(contact_links))

        return " - ".join(parts)

    def format_text(self) -> str:
        """Format as plain text."""
        pos = clean_position(self.position)
        return f"{pos}: {self.name}"


def _format_crew_section_html(
    crew: dict[str, list[CrewMember]],
) -> list[str]:
    """Format a crew dict as single HTML table with section headers."""
    lines = []
    sorted_sections = sorted(crew.keys(), key=section_sort_key)

    # Single table for all sections with consistent column widths
    lines.append('<table style="border-collapse: collapse; width: 100%;">')
    lines.append("<colgroup>")
    lines.append('<col style="width: 30%;">')
    lines.append('<col style="width: 25%;">')
    lines.append('<col style="width: 45%;">')
    lines.append("</colgroup>")

    for section in sorted_sections:
        members = crew[section]
        if not members:
            continue

        # Section header row
        lines.append(
            f'<tr><td colspan="3" style="padding: 12px 0 6px 0; font-weight: bold; '
            f'border-bottom: 2px solid #1a5276;">{escape(section)}</td></tr>'
        )

        sorted_members = sorted(members, key=lambda m: position_sort_key(m.position))
        for member in sorted_members:
            pos = clean_position(member.position)

            # Build contact links
            contact_parts = []
            if member.email:
                contact_parts.append(f'<a href="mailto:{escape(member.email)}">email</a>')
            if member.phone:
                phone_digits = "".join(c for c in member.phone if c.isdigit())
                if len(phone_digits) == 10:
                    phone_digits = "1" + phone_digits
                contact_parts.append(
                    f'<a href="tel:+{phone_digits}" style="white-space: nowrap;">'
                    f"{escape(member.phone)}</a>"
                )
            contact = " | ".join(contact_parts) if contact_parts else ""

            lines.append("<tr>")
            lines.append(f'<td style="padding: 4px 8px 4px 10px;">{escape(member.name)}</td>')
            lines.append(f'<td style="padding: 4px 8px;">{escape(pos)}</td>')
            lines.append(f'<td style="padding: 4px 8px;">{contact}</td>')
            lines.append("</tr>")

    lines.append("</table>")

    return lines


def _format_crew_section_text(
    crew: dict[str, list[CrewMember]],
) -> list[str]:
    """Format a crew dict as plain text lines."""
    lines = []
    sorted_sections = sorted(crew.keys(), key=section_sort_key)

    for section in sorted_sections:
        members = crew[section]
        if not members:
            continue

        lines.append(f"{section}:")
        sorted_members = sorted(members, key=lambda m: position_sort_key(m.position))
        lines.extend(f"  • {m.format_text()}" for m in sorted_members)
        lines.append("")

    return lines


# HTML template for calendar event body
# Uses {placeholders} for dynamic content
EVENT_BODY_TEMPLATE = """\
{until_section}
{from_section}
<hr style="margin-top: 24px;">
<p style="font-size: 0.9em; color: #666; margin-top: 12px;">
Schedule data from <a href="{aladtec_url}">Aladtec</a>.
View your personal schedule and make changes there.
</p>
"""

SECTION_HEADER_TEMPLATE = '<h3 style="color: #1a5276;">{label}{platoon}</h3>'


@dataclass
class AllDayDutyEvent:
    """An all-day calendar event showing crew for two time periods.

    For any calendar day, shows:
    - Until shift change: Crew from previous day's shift (ending at shift change)
    - From shift change: Crew starting today's shift (begins at shift change)
    """

    event_date: date
    until_crew: dict[str, list[CrewMember]]  # section -> list of CrewMember
    from_crew: dict[str, list[CrewMember]]  # section -> list of CrewMember
    until_platoon: str = ""
    from_platoon: str = ""
    shift_change_hour: int = 18  # Hour when shifts change (e.g., 18 = 6 PM)
    event_id: str | None = None  # M365 event ID if already created

    @property
    def _shift_time_display(self) -> str:
        """Format shift change hour for display (e.g., '1800')."""
        return f"{self.shift_change_hour:02d}00"

    @property
    def subject(self) -> str:
        """Generate event subject/title."""
        return get_org_config().duty_event_subject

    @property
    def body_html(self) -> str:
        """Generate event body as HTML with two time period sections."""
        shift_time = self._shift_time_display

        # Build "Until" section
        until_section = ""
        if self.until_crew:
            platoon = f" ({self.until_platoon})" if self.until_platoon else ""
            header = SECTION_HEADER_TEMPLATE.format(
                label=f"Until {shift_time}",
                platoon=platoon,
            )
            crew_html = "\n".join(_format_crew_section_html(self.until_crew))
            until_section = f"{header}\n{crew_html}"

        # Build "From" section
        from_section = ""
        if self.from_crew:
            platoon = f" ({self.from_platoon})" if self.from_platoon else ""
            header = SECTION_HEADER_TEMPLATE.format(
                label=f"From {shift_time}",
                platoon=platoon,
            )
            crew_html = "\n".join(_format_crew_section_html(self.from_crew))
            from_section = f"{header}\n{crew_html}"

        return EVENT_BODY_TEMPLATE.format(
            until_section=until_section,
            from_section=from_section,
            aladtec_url=get_aladtec_url(),
        )

    @property
    def body_text(self) -> str:
        """Generate event body as plain text (for comparison)."""
        shift_time = self._shift_time_display
        lines = []

        # Until section
        if self.until_crew:
            platoon = f" ({self.until_platoon})" if self.until_platoon else ""
            lines.append(f"Until {shift_time}{platoon}")
            lines.append("-" * 20)
            lines.extend(_format_crew_section_text(self.until_crew))

        # From section
        if self.from_crew:
            platoon = f" ({self.from_platoon})" if self.from_platoon else ""
            lines.append(f"From {shift_time}{platoon}")
            lines.append("-" * 20)
            lines.extend(_format_crew_section_text(self.from_crew))

        return "\n".join(lines).strip()


@dataclass
class SyncResult:
    """Result of a calendar sync operation."""

    events_created: int = 0
    events_updated: int = 0
    events_deleted: int = 0
    events_unchanged: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        """Total events processed."""
        return (
            self.events_created + self.events_updated + self.events_deleted + self.events_unchanged
        )

    def __str__(self) -> str:
        """Human-readable summary."""
        parts = []
        if self.events_created:
            parts.append(f"{self.events_created} created")
        if self.events_updated:
            parts.append(f"{self.events_updated} updated")
        if self.events_deleted:
            parts.append(f"{self.events_deleted} deleted")
        if self.events_unchanged:
            parts.append(f"{self.events_unchanged} unchanged")
        if self.errors:
            parts.append(f"{len(self.errors)} errors")

        return ", ".join(parts) if parts else "No changes"
