#!/usr/bin/env python3
"""Generate the NERIS value sets quick-reference markdown.

Auto-generates ``docs/neris/neris-value-sets-reference.md`` from the
``neris_api_client`` package enums, keeping only the curated value sets
most relevant to fire/EMS incident reporting.

Usage:
    uv run generate-neris-reference            # Write to default path
    uv run generate-neris-reference --check    # Check if file is up to date
"""

from __future__ import annotations

import argparse
import enum
import inspect
import re
import sys
from collections import defaultdict
from pathlib import Path

import neris_api_client.models as _neris_models

# ---------------------------------------------------------------------------
# Output path (relative to project root)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # src/sjifire/scripts → root
_OUTPUT_PATH = _PROJECT_ROOT / "docs" / "neris" / "neris-value-sets-reference.md"

# ---------------------------------------------------------------------------
# Curated value sets — (short_name, display_title)
#
# Order here controls the order in the output file.  Add or remove entries
# to change which value sets are included in the reference.
# ---------------------------------------------------------------------------

CURATED_VALUE_SETS: list[tuple[str, str]] = [
    # --- Incident classification ---
    ("incident", "Incident Types"),
    ("action_tactic", "Actions / Tactics Taken"),
    ("location_use", "Location Use Types"),
    ("noaction", "No Action Reason"),
    ("response_mode", "Response Mode"),
    ("special_modifier", "Special Incident Modifiers"),
    ("yes_no_unknown", "Yes / No / Unknown"),
    # --- Services & units ---
    ("unit", "Unit Types"),
    ("serv_fd", "FD Services"),
    ("serv_ems", "EMS Services"),
    ("duty", "Duty Status"),
    # --- Aid ---
    ("aid", "Aid Type"),
    ("aid_direction", "Aid Direction"),
    # --- Fire ---
    ("fire_condition_arrival", "Fire Condition on Arrival"),
    ("fire_cause_in", "Indoor Cause of Ignition"),
    ("fire_cause_out", "Outdoor Cause of Ignition"),
    ("fire_bldg_damage", "Fire Building Damage"),
    ("fire_invest", "Fire Investigation"),
    ("fire_invest_need", "Fire Investigation Need"),
    ("room", "Room of Origin"),
    ("water_supply", "Water Supply"),
    # --- Suppression ---
    ("suppress_appliance", "Suppression Appliances"),
    ("suppress_fire", "Fire Suppression Type"),
    ("suppress_operation", "Suppression Operation"),
    # --- Medical ---
    ("medical_patient_care", "Medical Patient Care Disposition"),
    ("medical_patient_status", "Medical Patient Status"),
    ("medical_transport", "Medical Transport Disposition"),
    # --- Casualties ---
    ("casualty_action", "Casualty Activity"),
    ("casualty_cause", "Casualty Cause"),
    ("casualty_ppe", "Casualty PPE"),
    ("casualty_timeline", "Casualty Timeline"),
    # --- Rescue ---
    ("rescue_action", "Rescue Actions"),
    ("rescue_elevation", "Rescue Elevation"),
    ("rescue_impediment", "Rescue Impediment"),
    ("rescue_mode", "Rescue Mode"),
]

# ---------------------------------------------------------------------------
# Build enum lookup: short_name → enum class
# ---------------------------------------------------------------------------

_VALUE_SETS: dict[str, type[enum.Enum]] = {}

for _name, _obj in inspect.getmembers(_neris_models):
    if (
        inspect.isclass(_obj)
        and issubclass(_obj, enum.Enum)
        and _name.startswith("Type")
        and _name.endswith("Value")
    ):
        short = _name.removeprefix("Type").removesuffix("Value")
        short = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", short).lower()
        _VALUE_SETS[short] = _obj


def _total_enum_count() -> tuple[int, int]:
    """Return (number of value sets, total values) across all enums."""
    return len(_VALUE_SETS), sum(len(cls) for cls in _VALUE_SETS.values())


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _humanize(raw: str) -> str:
    """Turn ``SOME_ENUM_VALUE`` into ``Some Enum Value``."""
    return raw.replace("_", " ").title()


def _format_flat_values(enum_cls: type[enum.Enum]) -> str:
    """Format a non-hierarchical value set as a simple bullet list."""
    lines: list[str] = []
    for member in enum_cls:
        val = str(member.value)
        lines.append(f"- `{val}` — {_humanize(val)}")
    return "\n".join(lines)


def _format_hierarchical_values(enum_cls: type[enum.Enum]) -> str:
    """Format a hierarchical value set (values containing ``||``) with grouped headers."""
    values = [str(m.value) for m in enum_cls]

    # Separate top-level flat values from hierarchical ones
    flat_values: list[str] = []
    hier_values: list[str] = []
    for v in values:
        if "||" in v:
            hier_values.append(v)
        else:
            flat_values.append(v)

    # Group hierarchical values by first segment
    groups: dict[str, list[str]] = defaultdict(list)
    for v in hier_values:
        first = v.split("||")[0]
        groups[first].append(v)

    lines: list[str] = []

    for group_name in sorted(groups):
        group_vals = groups[group_name]
        lines.append(f"\n## {_humanize(group_name)}")

        # Split into 2-level and 3+-level values
        two_level: list[str] = []
        deeper: dict[str, list[str]] = defaultdict(list)
        for v in sorted(group_vals):
            parts = v.split("||")
            has_sub = len(parts) > 2
            if has_sub:
                deeper[parts[1]].append(v)
            else:
                two_level.append(v)

        # List 2-level values with labels
        if two_level:
            lines.append("")
            lines.extend(f"- `{v}` — {_humanize(v.split('||')[-1])}" for v in two_level)

        # List 3+-level values grouped by second segment
        for sub_name in sorted(deeper):
            sub_vals = deeper[sub_name]
            lines.append(f"\n### {_humanize(sub_name)}\n")
            lines.extend(f"- `{v}`" for v in sorted(sub_vals))

    # Any top-level flat values (no ||) go at the end
    if flat_values:
        if hier_values:
            lines.append("")
        lines.extend(f"- `{v}` — {_humanize(v)}" for v in sorted(flat_values))

    return "\n".join(lines)


def _format_value_set(enum_cls: type[enum.Enum]) -> str:
    """Format a value set, auto-detecting flat vs. hierarchical."""
    has_hierarchy = any("||" in str(m.value) for m in enum_cls)
    if has_hierarchy:
        return _format_hierarchical_values(enum_cls)
    return _format_flat_values(enum_cls)


# ---------------------------------------------------------------------------
# Document generation
# ---------------------------------------------------------------------------


def generate() -> str:
    """Generate the complete markdown reference document."""
    total_sets, total_values = _total_enum_count()
    curated_count = len(CURATED_VALUE_SETS)
    curated_values = sum(len(_VALUE_SETS[name]) for name, _ in CURATED_VALUE_SETS)

    header = f"""\
# NERIS Value Sets — Quick Reference

> Auto-generated from `neris-api-client`. These are the {curated_count} most commonly used
> value sets ({curated_values} values) for incident reporting. The full package contains
> {total_sets} value sets with {total_values} total values — use `list_neris_value_sets`
> and `get_neris_values` tools for the complete set.

Reference data for the most commonly used NERIS value sets in incident reporting.
For other value sets, use the `get_neris_values` MCP tool."""

    sections: list[str] = [header]

    for short_name, display_title in CURATED_VALUE_SETS:
        enum_cls = _VALUE_SETS.get(short_name)
        if enum_cls is None:
            print(f"WARNING: Unknown value set {short_name!r}, skipping")
            continue

        # Derive the class name from the enum class
        class_name = enum_cls.__name__
        count = len(enum_cls)
        has_hierarchy = any("||" in str(m.value) for m in enum_cls)

        section_header = f"# {display_title}\n\nNERIS value set: `{class_name}` ({count} values)"
        if has_hierarchy:
            section_header += "\nValues use `||` as hierarchy separator."

        formatted = _format_value_set(enum_cls)
        sections.append(f"{section_header}\n{formatted}")

    return "\n\n---\n\n".join(sections) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate NERIS value sets quick-reference markdown.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_OUTPUT_PATH,
        help=f"Output file path (default: {_OUTPUT_PATH.relative_to(_PROJECT_ROOT)})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if the file is up to date without writing (exit 1 if stale).",
    )
    args = parser.parse_args()

    content = generate()

    if args.check:
        output: Path = args.output
        if not output.exists():
            print(f"STALE: {output} does not exist")
            sys.exit(1)
        existing = output.read_text()
        if existing == content:
            print(f"OK: {output} is up to date")
            sys.exit(0)
        else:
            print(f"STALE: {output} needs regeneration")
            sys.exit(1)

    output_path: Path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)

    curated_count = len(CURATED_VALUE_SETS)
    curated_values = sum(
        len(_VALUE_SETS[name]) for name, _ in CURATED_VALUE_SETS if name in _VALUE_SETS
    )
    print(f"Generated {output_path} ({curated_count} value sets, {curated_values} values)")


if __name__ == "__main__":
    main()
