"""Data models for calendar events."""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html import escape

from sjifire.aladtec.client import get_aladtec_credentials

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


def section_sort_key(section: str) -> tuple[int, str]:
    """Sort key for sections - stations first (numerically), then others alphabetically.

    Sorting priority:
    1. Station sections (S31, S32, S33, etc.) - sorted by number
    2. All other sections - sorted alphabetically
    """
    # Check if it's a station section (S followed by numbers)
    station_match = re.match(r"^S(\d+)$", section)
    if station_match:
        # Stations get priority 0, sorted by station number
        return (0, int(station_match.group(1)), section)

    # Everything else gets priority 1, sorted alphabetically
    return (1, 0, section)


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


@dataclass
class ShiftPeriod:
    """A time period within a shift where crew is constant."""

    start: datetime
    end: datetime
    crew: dict[str, list[CrewMember]]  # section -> list of CrewMember

    def format_crew_text(self, exclude_sections: list[str] | None = None) -> str:
        """Format crew list as text for calendar body."""
        exclude = set(exclude_sections or [])
        lines = []

        sorted_sections = sorted(self.crew.keys(), key=section_sort_key)

        for section in sorted_sections:
            if section in exclude:
                continue
            members = self.crew[section]
            if not members:
                continue

            lines.append(f"{section}:")
            sorted_members = sorted(members, key=lambda m: position_sort_key(m.position))
            lines.extend(f"  • {m.format_text()}" for m in sorted_members)
            lines.append("")

        return "\n".join(lines).strip()


@dataclass
class OnDutyEvent:
    """A calendar event representing on-duty crew for a shift period."""

    start: datetime
    end: datetime
    platoon: str
    crew: dict[str, list[CrewMember]]  # section -> list of CrewMember
    event_id: str | None = None  # M365 event ID if already created

    @property
    def subject(self) -> str:
        """Generate event subject/title."""
        if self.platoon:
            return f"On Duty: {self.platoon}"
        return "On Duty Crew"

    @property
    def body_html(self) -> str:
        """Generate event body as HTML with contact links."""
        lines = []

        sorted_sections = sorted(self.crew.keys(), key=section_sort_key)

        for section in sorted_sections:
            members = self.crew[section]
            if not members:
                continue

            lines.append(f"<p><b>{escape(section)}</b></p>")
            lines.append("<ul>")
            sorted_members = sorted(members, key=lambda m: position_sort_key(m.position))
            lines.extend(f"<li>{m.format_html()}</li>" for m in sorted_members)
            lines.append("</ul>")

        # Add Aladtec link at the bottom with spacing
        lines.append('<hr style="margin-top: 24px;">')
        lines.append(
            f'<p style="font-size: 0.9em; color: #666; margin-top: 12px;">'
            f'Schedule data from <a href="{get_aladtec_url()}">Aladtec</a>. '
            f"View your personal schedule and make changes there.</p>"
        )

        return "\n".join(lines)

    @property
    def body_text(self) -> str:
        """Generate event body as plain text (for comparison)."""
        lines = []

        sorted_sections = sorted(self.crew.keys(), key=section_sort_key)

        for section in sorted_sections:
            members = self.crew[section]
            if not members:
                continue

            lines.append(f"{section}:")
            sorted_members = sorted(members, key=lambda m: position_sort_key(m.position))
            lines.extend(f"  • {m.format_text()}" for m in sorted_members)
            lines.append("")

        return "\n".join(lines).strip()

    def matches(self, other: OnDutyEvent) -> bool:
        """Check if two events represent the same shift (for update detection)."""
        return self.start == other.start and self.end == other.end

    def content_matches(self, other: OnDutyEvent) -> bool:
        """Check if two events have the same content."""
        # Compare using plain text representation for simplicity
        return self.subject == other.subject and self.body_text == other.body_text


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


@dataclass
class AllDayDutyEvent:
    """An all-day calendar event showing crew for two time periods.

    For any calendar day, shows:
    - Until 1800: Crew from previous day's shift (ending at 1800)
    - From 1800: Crew starting today's shift (begins at 1800)
    """

    event_date: date
    until_1800_platoon: str
    until_1800_crew: dict[str, list[CrewMember]]  # section -> list of CrewMember
    from_1800_platoon: str
    from_1800_crew: dict[str, list[CrewMember]]  # section -> list of CrewMember
    event_id: str | None = None  # M365 event ID if already created

    @property
    def subject(self) -> str:
        """Generate event subject/title."""
        return "On Duty"

    @property
    def body_html(self) -> str:
        """Generate event body as HTML with two time period sections."""
        lines = []

        # Until 1800 section
        if self.until_1800_crew:
            platoon_note = f" ({self.until_1800_platoon})" if self.until_1800_platoon else ""
            lines.append(f'<h3 style="color: #1a5276;">Until 1800{platoon_note}</h3>')
            lines.extend(_format_crew_section_html(self.until_1800_crew))

        # From 1800 section
        if self.from_1800_crew:
            platoon_note = f" ({self.from_1800_platoon})" if self.from_1800_platoon else ""
            lines.append(f'<h3 style="color: #1a5276;">From 1800{platoon_note}</h3>')
            lines.extend(_format_crew_section_html(self.from_1800_crew))

        # Add Aladtec link at the bottom with spacing
        lines.append('<hr style="margin-top: 24px;">')
        lines.append(
            f'<p style="font-size: 0.9em; color: #666; margin-top: 12px;">'
            f'Schedule data from <a href="{get_aladtec_url()}">Aladtec</a>. '
            f"View your personal schedule and make changes there.</p>"
        )

        return "\n".join(lines)

    @property
    def body_text(self) -> str:
        """Generate event body as plain text (for comparison)."""
        lines = []

        # Until 1800 section
        if self.until_1800_crew:
            platoon_note = f" ({self.until_1800_platoon})" if self.until_1800_platoon else ""
            lines.append(f"Until 1800{platoon_note}")
            lines.append("-" * 20)
            lines.extend(_format_crew_section_text(self.until_1800_crew))

        # From 1800 section
        if self.from_1800_crew:
            platoon_note = f" ({self.from_1800_platoon})" if self.from_1800_platoon else ""
            lines.append(f"From 1800{platoon_note}")
            lines.append("-" * 20)
            lines.extend(_format_crew_section_text(self.from_1800_crew))

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
