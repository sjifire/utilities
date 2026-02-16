"""Tools for looking up NERIS value sets.

Reads enum definitions from the ``neris_api_client`` package so Claude
can present valid options when guiding users through incident reports.
"""

import enum
import inspect
import logging
import re

import neris_api_client.models as _neris_models

from sjifire.ops.auth import get_current_user

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Build the lookup once at import time — maps short name → enum class.
# E.g. "incident" → TypeIncidentValue, "action_tactic" → TypeActionTacticValue
_VALUE_SETS: dict[str, type[enum.Enum]] = {}

for _name, _obj in inspect.getmembers(_neris_models):
    if (
        inspect.isclass(_obj)
        and issubclass(_obj, enum.Enum)
        and _name.startswith("Type")
        and _name.endswith("Value")
    ):
        # TypeIncidentValue → incident
        # TypeActionTacticValue → action_tactic
        short = _name.removeprefix("Type").removesuffix("Value")
        short = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", short).lower()
        _VALUE_SETS[short] = _obj


def _humanize(raw: str) -> str:
    """Turn ``FIRE||STRUCTURE_FIRE||CHIMNEY_FIRE`` into readable text."""
    return raw.replace("||", " > ").replace("_", " ").title()


def _enum_to_list(
    cls: type[enum.Enum],
    *,
    prefix: str | None = None,
    search: str | None = None,
) -> list[dict[str, str]]:
    """Return filtered enum members as dicts with value + label."""
    results = []
    for member in cls:
        value = str(member.value)
        if prefix and not value.startswith(prefix):
            continue
        if search and search not in value.lower():
            continue
        results.append({"value": value, "label": _humanize(value)})
    return results


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def list_neris_value_sets() -> dict:
    """List all available NERIS value sets and how many values each contains.

    Use this to discover which value sets exist before looking up
    specific values. Returns short names you can pass to
    ``get_neris_values``.

    Returns:
        Dict with "value_sets" list of {name, count} and "total".
    """
    get_current_user()

    sets = [{"name": name, "count": len(cls)} for name, cls in sorted(_VALUE_SETS.items())]
    return {"value_sets": sets, "total": len(sets)}


async def get_neris_values(
    value_set: str,
    *,
    prefix: str | None = None,
    search: str | None = None,
) -> dict:
    """Get valid values for a NERIS field.

    Use ``list_neris_value_sets`` first to see available set names.
    Values use ``||`` as a hierarchy separator
    (e.g. ``FIRE||STRUCTURE_FIRE||CHIMNEY_FIRE``).

    Examples:
        - ``get_neris_values("incident")`` → all 128 incident types
        - ``get_neris_values("incident", prefix="FIRE||")`` → fire types only
        - ``get_neris_values("incident", search="boat")`` → find boat-related
        - ``get_neris_values("action_tactic", prefix="SUPPRESSION||")``
        - ``get_neris_values("location_use", search="residential")``

    Args:
        value_set: Short name from ``list_neris_value_sets``
            (e.g. "incident", "action_tactic", "location_use").
        prefix: Filter to values starting with this prefix.
            Use ``||`` hierarchy separators
            (e.g. "FIRE||STRUCTURE_FIRE||").
        search: Case-insensitive keyword search across all values.

    Returns:
        Dict with "value_set" name, "values" list of
        {value, label}, and "count". Or "error" if set not found.
    """
    get_current_user()

    cls = _VALUE_SETS.get(value_set.lower())
    if cls is None:
        available = sorted(_VALUE_SETS.keys())
        return {
            "error": f"Unknown value set: {value_set!r}",
            "available": available,
        }

    search_lower = search.lower() if search else None
    values = _enum_to_list(cls, prefix=prefix, search=search_lower)

    if not values:
        total = len(list(cls))
        logger.warning(
            "get_neris_values(%r, prefix=%r, search=%r) returned 0/%d values",
            value_set,
            prefix,
            search,
            total,
        )

    return {
        "value_set": value_set,
        "values": values,
        "count": len(values),
    }
