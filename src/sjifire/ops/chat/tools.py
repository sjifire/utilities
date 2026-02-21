"""Anthropic tool definitions and dispatcher for chat conversations.

Wraps the same async functions the MCP tools use, with user context
set before each call. Only a safe subset of tools is exposed to the
chat Claude — no create, delete, or submit operations.
"""

import json
import logging

import httpx

from sjifire.ops.auth import UserContext, set_current_user

logger = logging.getLogger(__name__)

# Anthropic tool definitions — must match the wrapped function signatures
TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_incident",
        "description": (
            "Get the current incident report by ID. Returns all fields "
            "including units, personnel, timestamps, narrative, and completeness."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "The incident document ID (UUID)",
                },
            },
            "required": ["incident_id"],
        },
    },
    {
        "name": "update_incident",
        "description": (
            "Update fields on the incident report. Only provide fields you want to change. "
            "Save frequently — don't batch updates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "The incident document ID",
                },
                "status": {
                    "type": "string",
                    "enum": ["draft", "in_progress", "ready_review"],
                    "description": "Report status",
                },
                "incident_type": {
                    "type": "string",
                    "description": "NERIS incident type code (e.g. FIRE||STRUCTURE_FIRE)",
                },
                "address": {"type": "string", "description": "Incident address"},
                "city": {"type": "string", "description": "City name"},
                "latitude": {"type": "number", "description": "GPS latitude"},
                "longitude": {"type": "number", "description": "GPS longitude"},
                "units": {
                    "type": "array",
                    "description": "Unit assignments with nested personnel (replaces existing)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "unit_id": {
                                "type": "string",
                                "description": "Unit ID (E31, BN31, M31, POV)",
                            },
                            "response_mode": {
                                "type": "string",
                                "description": "EMERGENT or NON_EMERGENT",
                            },
                            "personnel": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "email": {"type": "string"},
                                        "rank": {"type": "string"},
                                        "position": {"type": "string"},
                                    },
                                    "required": ["name"],
                                },
                            },
                            "dispatch": {"type": "string", "description": "Dispatch timestamp"},
                            "enroute": {"type": "string", "description": "Enroute timestamp"},
                            "on_scene": {"type": "string", "description": "On scene timestamp"},
                            "cleared": {"type": "string", "description": "Cleared timestamp"},
                            "canceled": {"type": "string", "description": "Canceled timestamp"},
                            "in_quarters": {
                                "type": "string",
                                "description": "In quarters timestamp",
                            },
                        },
                        "required": ["unit_id"],
                    },
                },
                "narrative": {
                    "type": "string",
                    "description": "Combined incident narrative (what happened and actions taken)",
                },
                "action_taken": {
                    "type": "string",
                    "enum": ["ACTION", "NOACTION"],
                    "description": "Was action taken? ACTION = yes, NOACTION = no",
                },
                "noaction_reason": {
                    "type": "string",
                    "enum": ["CANCELLED", "STAGED_STANDBY", "NO_INCIDENT_FOUND"],
                    "description": "Why no action (required when action_taken=NOACTION)",
                },
                "action_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "NERIS action codes (required when action_taken=ACTION)",
                },
                "timestamps": {
                    "type": "object",
                    "description": (
                        "Event timestamps (keys: psap_answer, first_unit_dispatched, etc.)"
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "internal_notes": {
                    "type": "string",
                    "description": "Internal notes (not sent to NERIS)",
                },
                "arrival_conditions": {
                    "type": "string",
                    "enum": [
                        "NO_SMOKE_FIRE_SHOWING",
                        "SMOKE_SHOWING",
                        "SMOKE_FIRE_SHOWING",
                        "STRUCTURE_INVOLVED",
                        "FIRE_SPREAD_BEYOND_STRUCTURE",
                        "FIRE_OUT_UPON_ARRIVAL",
                    ],
                    "description": "Fire condition on arrival (fire incidents only)",
                },
                "outside_fire_cause": {
                    "type": "string",
                    "description": "Cause of outside fire (NERIS fire_cause_out code)",
                },
                "outside_fire_acres": {
                    "type": "number",
                    "description": "Estimated acres burned (outside fire only)",
                },
                "additional_incident_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Up to 2 additional NERIS incident type codes",
                },
                "automatic_alarm": {
                    "type": "boolean",
                    "description": "Was this call initiated by an automatic alarm?",
                },
                "people_present": {
                    "type": "boolean",
                    "description": "Were people present at the incident location?",
                },
                "displaced_count": {
                    "type": "integer",
                    "description": "Number of people displaced",
                },
                "extras": {
                    "type": "object",
                    "description": (
                        "Additional NERIS fields merged into existing extras. Use snake_case keys."
                    ),
                },
            },
            "required": ["incident_id"],
        },
    },
    {
        "name": "reset_incident",
        "description": (
            "Reset an incident report to a clean slate. Clears all content "
            "fields (type, units, personnel, narrative) and re-populates address "
            "and timestamps from dispatch data. Use when the user wants to start over."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "The incident document ID (UUID)",
                },
            },
            "required": ["incident_id"],
        },
    },
    {
        "name": "import_from_neris",
        "description": (
            "Import or re-import data from a NERIS record into this incident report. "
            "Overwrites incident type, narrative, location, units, and timestamps "
            "with NERIS values."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "The incident document ID",
                },
                "neris_id": {
                    "type": "string",
                    "description": "NERIS compound ID (optional if already set on incident)",
                },
            },
            "required": ["incident_id"],
        },
    },
    {
        "name": "finalize_incident",
        "description": (
            "Lock an incident report after NERIS review. Fetches the current NERIS "
            "status and sets the local report to 'approved' (if NERIS approved) or "
            "'submitted'. The incident must have a NERIS ID and be in an editable status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "The incident document ID (UUID)",
                },
            },
            "required": ["incident_id"],
        },
    },
    {
        "name": "get_dispatch_call",
        "description": (
            "Get full details for a dispatch call including nature, address, "
            "responder timeline, CAD comments, and geo location."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "call_id": {
                    "type": "string",
                    "description": 'Dispatch ID (e.g. "26-001678")',
                },
            },
            "required": ["call_id"],
        },
    },
    {
        "name": "search_dispatch_calls",
        "description": "Search historical dispatch calls by ID or date range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "dispatch_id": {
                    "type": "string",
                    "description": 'Dispatch ID to search for (e.g. "26-001678")',
                },
                "start_date": {
                    "type": "string",
                    "description": "Start of date range (YYYY-MM-DD)",
                },
                "end_date": {
                    "type": "string",
                    "description": "End of date range (YYYY-MM-DD)",
                },
            },
        },
    },
    {
        "name": "get_on_duty_crew",
        "description": (
            "Get the crew on duty for a specific date and time. Uses shift-change "
            "logic: if target_hour is before the shift change (e.g. 18:00), returns "
            "the previous day's crew who were still on duty."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format (defaults to today)",
                },
                "target_hour": {
                    "type": "integer",
                    "description": "Hour of day (0-23) for shift-change-aware lookup",
                },
            },
        },
    },
    {
        "name": "get_neris_values",
        "description": (
            "Look up valid NERIS values for a field. Use prefix to filter by "
            "category (e.g. prefix='FIRE||') or search for keywords."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "value_set": {
                    "type": "string",
                    "description": (
                        'NERIS value set name (e.g. "incident", "action_tactic", "location_use")'
                    ),
                },
                "prefix": {
                    "type": "string",
                    "description": "Filter values starting with this prefix",
                },
                "search": {
                    "type": "string",
                    "description": "Case-insensitive keyword search",
                },
            },
            "required": ["value_set"],
        },
    },
    {
        "name": "get_personnel",
        "description": (
            "Search active personnel by name or email. Use this when you "
            "cannot match a name from the pre-loaded operational roster — "
            "e.g. for admin staff, volunteers, or when the user gives a "
            "nickname, shorthand, or last name only. Always pass a search "
            "term for targeted lookups (e.g. search='Vos'). If zero results, "
            "the user may have used a nickname — try the formal name "
            "(Mike→Michael, Dick→Richard, Bill→William, etc.) before "
            "asking the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Case-insensitive substring search on name or email. "
                    "Use for last-name lookups (e.g. 'Vos', 'Smith'). "
                    "Omit to get the full list.",
                },
            },
        },
    },
    {
        "name": "lookup_location",
        "description": (
            "Look up address details, cross streets, and property type from GPS coordinates. "
            "Use this during the location step to find cross streets and property type instead "
            "of asking the user. Returns the verified address, nearby road names, and a "
            "property_type from OpenStreetMap (e.g. 'building/house', 'building/apartments'). "
            "Map the property_type to the correct NERIS location_use code from the cheat sheet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "latitude": {"type": "number", "description": "GPS latitude"},
                "longitude": {"type": "number", "description": "GPS longitude"},
            },
            "required": ["latitude", "longitude"],
        },
    },
    {
        "name": "list_attachments",
        "description": (
            "List all attachments (photos, documents) on this incident report. "
            "Returns metadata for each: ID, filename, title, description, "
            "content type, size. Use after a reset to see what survived."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "The incident document ID (UUID)",
                },
            },
            "required": ["incident_id"],
        },
    },
    {
        "name": "get_attachment",
        "description": (
            "Fetch a single attachment by ID. Returns metadata and the image data "
            "for vision analysis. Use this to view or re-analyze a photo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "The incident document ID (UUID)",
                },
                "attachment_id": {
                    "type": "string",
                    "description": "The attachment ID (from list_attachments or context)",
                },
            },
            "required": ["incident_id", "attachment_id"],
        },
    },
    {
        "name": "update_attachment",
        "description": (
            "Update the title and/or description on an attachment. Call this "
            "after analyzing a photo to label it (e.g., 'E31 accountability board', "
            "'Scene photo — front of structure'). The title shows in the chat UI."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "The incident document ID (UUID)",
                },
                "attachment_id": {
                    "type": "string",
                    "description": "The attachment ID to update",
                },
                "title": {
                    "type": "string",
                    "description": "Short descriptive title (e.g., 'E31 accountability board')",
                },
                "description": {
                    "type": "string",
                    "description": "Longer description of what the photo shows",
                },
            },
            "required": ["incident_id", "attachment_id"],
        },
    },
    {
        "name": "delete_attachment",
        "description": (
            "Delete an attachment from the incident report. Removes the file "
            "from storage and the metadata from the report."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "The incident document ID (UUID)",
                },
                "attachment_id": {
                    "type": "string",
                    "description": "The attachment ID to delete",
                },
            },
            "required": ["incident_id", "attachment_id"],
        },
    },
]

# Mark last tool with cache_control so entire tool definition block is cached
TOOL_SCHEMAS[-1]["cache_control"] = {"type": "ephemeral"}

# Set of allowed tool names for validation
_ALLOWED_TOOLS = {t["name"] for t in TOOL_SCHEMAS}


async def execute_tool(name: str, tool_input: dict, user: UserContext) -> str:
    """Route a tool call to the corresponding async function.

    Sets the user context before calling so auth checks work correctly.
    Returns a JSON string with the tool result.
    """
    if name not in _ALLOWED_TOOLS:
        logger.warning("Chat attempted disallowed tool: %s", name)
        return json.dumps({"error": f"Tool '{name}' is not available"})

    set_current_user(user)

    try:
        result = await _dispatch(name, tool_input)
        return json.dumps(result, default=str)
    except Exception:
        logger.exception("Tool execution failed: %s", name)
        return json.dumps({"error": f"Tool '{name}' failed. Please try again."})


async def _dispatch(name: str, tool_input: dict) -> dict:
    """Call the underlying tool function."""
    from sjifire.ops.dispatch import tools as dispatch_tools
    from sjifire.ops.incidents import tools as incident_tools
    from sjifire.ops.neris import tools as neris_tools
    from sjifire.ops.schedule import tools as schedule_tools

    if name == "get_incident":
        return await incident_tools.get_incident(tool_input["incident_id"])

    if name == "update_incident":
        incident_id = tool_input["incident_id"]
        kwargs = {k: v for k, v in tool_input.items() if k != "incident_id"}
        # Map chat schema "units" to the function's "unit_responses" param
        if "units" in kwargs:
            kwargs["unit_responses"] = kwargs.pop("units")
        return await incident_tools.update_incident(incident_id, **kwargs)

    if name == "reset_incident":
        return await incident_tools.reset_incident(tool_input["incident_id"])

    if name == "import_from_neris":
        return await incident_tools.import_from_neris(
            tool_input["incident_id"],
            neris_id=tool_input.get("neris_id"),
        )

    if name == "finalize_incident":
        return await incident_tools.finalize_incident(tool_input["incident_id"])

    if name == "get_dispatch_call":
        return await dispatch_tools.get_dispatch_call(tool_input["call_id"])

    if name == "search_dispatch_calls":
        return await dispatch_tools.search_dispatch_calls(
            dispatch_id=tool_input.get("dispatch_id", ""),
            start_date=tool_input.get("start_date", ""),
            end_date=tool_input.get("end_date", ""),
        )

    if name == "get_on_duty_crew":
        return await schedule_tools.get_on_duty_crew(
            target_date=tool_input.get("target_date"),
            target_hour=tool_input.get("target_hour"),
        )

    if name == "get_neris_values":
        return await neris_tools.get_neris_values(
            value_set=tool_input["value_set"],
            prefix=tool_input.get("prefix"),
            search=tool_input.get("search"),
        )

    if name == "get_personnel":
        from sjifire.ops.personnel import tools as personnel_tools

        search = tool_input.get("search")
        result = await personnel_tools.get_personnel(search=search)
        if result:
            return {"personnel": result, "count": len(result)}

        # No exact match — return names-only list for Claude to reason about
        # (nicknames, shorthand, spelling variants). Compact format so
        # Claude can scan ~56 names without JSON noise.
        if search:
            all_personnel = await personnel_tools.get_personnel()
            return {
                "personnel": [],
                "count": 0,
                "search": search,
                "hint": f"No exact match for '{search}'. Full roster names below — "
                "check for nicknames (Mike→Michael, Dick→Richard, etc.), "
                "spelling variants, or ask the user.",
                "all_names": [p["name"] for p in all_personnel],
            }

        return {"personnel": [], "count": 0}

    if name == "lookup_location":
        return await _lookup_location(tool_input["latitude"], tool_input["longitude"])

    if name == "list_attachments":
        from sjifire.ops.attachments import tools as attachment_tools

        return await attachment_tools.list_attachments(tool_input["incident_id"])

    if name == "get_attachment":
        from sjifire.ops.attachments import tools as attachment_tools

        return await attachment_tools.get_attachment(
            tool_input["incident_id"], tool_input["attachment_id"], include_data=True
        )

    if name == "update_attachment":
        from sjifire.ops.attachments import tools as attachment_tools

        return await attachment_tools.update_attachment(
            tool_input["incident_id"],
            tool_input["attachment_id"],
            title=tool_input.get("title"),
            description=tool_input.get("description"),
        )

    if name == "delete_attachment":
        from sjifire.ops.attachments import tools as attachment_tools

        return await attachment_tools.delete_attachment(
            tool_input["incident_id"], tool_input["attachment_id"]
        )

    return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# General assistant tools (read-only, no update_incident)
# ---------------------------------------------------------------------------

GENERAL_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_dispatch_call",
        "description": (
            "Get full details for a dispatch call including nature, address, "
            "responder timeline, CAD comments, and geo location."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "call_id": {
                    "type": "string",
                    "description": 'Dispatch ID (e.g. "26-001678")',
                },
            },
            "required": ["call_id"],
        },
    },
    {
        "name": "list_dispatch_calls",
        "description": "List recent dispatch calls from the last 7 or 30 days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back (7 or 30, default 30)",
                },
            },
        },
    },
    {
        "name": "search_dispatch_calls",
        "description": "Search historical dispatch calls by ID or date range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "dispatch_id": {
                    "type": "string",
                    "description": 'Dispatch ID to search for (e.g. "26-001678")',
                },
                "start_date": {
                    "type": "string",
                    "description": "Start of date range (YYYY-MM-DD)",
                },
                "end_date": {
                    "type": "string",
                    "description": "End of date range (YYYY-MM-DD)",
                },
            },
        },
    },
    {
        "name": "get_on_duty_crew",
        "description": (
            "Get the crew on duty for a specific date and time. Uses shift-change "
            "logic: if target_hour is before the shift change (e.g. 18:00), returns "
            "the previous day's crew who were still on duty."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format (defaults to today)",
                },
                "target_hour": {
                    "type": "integer",
                    "description": "Hour of day (0-23) for shift-change-aware lookup",
                },
            },
        },
    },
    {
        "name": "get_neris_values",
        "description": (
            "Look up valid NERIS values for a field. Use prefix to filter by "
            "category (e.g. prefix='FIRE||') or search for keywords."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "value_set": {
                    "type": "string",
                    "description": (
                        'NERIS value set name (e.g. "incident", "action_tactic", "location_use")'
                    ),
                },
                "prefix": {
                    "type": "string",
                    "description": "Filter values starting with this prefix",
                },
                "search": {
                    "type": "string",
                    "description": "Case-insensitive keyword search",
                },
            },
            "required": ["value_set"],
        },
    },
    {
        "name": "list_incidents",
        "description": (
            "List incident reports. Shows draft, in-progress, and ready-for-review "
            "reports by default. Pass status='submitted' to see submitted ones."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Filter by status: draft, in_progress, ready_review, or submitted"
                    ),
                },
            },
        },
    },
    {
        "name": "get_incident",
        "description": (
            "Get an incident report by ID. Returns all fields "
            "including units, personnel, timestamps, narrative, and completeness."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "The incident document ID (UUID)",
                },
            },
            "required": ["incident_id"],
        },
    },
]

# Mark last general tool with cache_control so tool definitions are cached
GENERAL_TOOL_SCHEMAS[-1]["cache_control"] = {"type": "ephemeral"}

_ALLOWED_GENERAL_TOOLS = {t["name"] for t in GENERAL_TOOL_SCHEMAS}


async def execute_general_tool(name: str, tool_input: dict, user: UserContext) -> str:
    """Route a general tool call to the corresponding async function.

    Same pattern as ``execute_tool`` but uses the general (read-only) tool set.
    """
    if name not in _ALLOWED_GENERAL_TOOLS:
        logger.warning("General chat attempted disallowed tool: %s", name)
        return json.dumps({"error": f"Tool '{name}' is not available"})

    set_current_user(user)

    try:
        result = await _dispatch_general(name, tool_input)
        return json.dumps(result, default=str)
    except Exception:
        logger.exception("General tool execution failed: %s", name)
        return json.dumps({"error": f"Tool '{name}' failed. Please try again."})


async def _dispatch_general(name: str, tool_input: dict) -> dict:
    """Call the underlying tool function for general assistant tools."""
    from sjifire.ops.dispatch import tools as dispatch_tools
    from sjifire.ops.incidents import tools as incident_tools
    from sjifire.ops.neris import tools as neris_tools
    from sjifire.ops.schedule import tools as schedule_tools

    if name == "get_dispatch_call":
        return await dispatch_tools.get_dispatch_call(tool_input["call_id"])

    if name == "list_dispatch_calls":
        return await dispatch_tools.list_dispatch_calls(
            days=tool_input.get("days", 30),
        )

    if name == "search_dispatch_calls":
        return await dispatch_tools.search_dispatch_calls(
            dispatch_id=tool_input.get("dispatch_id", ""),
            start_date=tool_input.get("start_date", ""),
            end_date=tool_input.get("end_date", ""),
        )

    if name == "get_on_duty_crew":
        return await schedule_tools.get_on_duty_crew(
            target_date=tool_input.get("target_date"),
            target_hour=tool_input.get("target_hour"),
        )

    if name == "get_neris_values":
        return await neris_tools.get_neris_values(
            value_set=tool_input["value_set"],
            prefix=tool_input.get("prefix"),
            search=tool_input.get("search"),
        )

    if name == "list_incidents":
        return await incident_tools.list_incidents(
            status=tool_input.get("status"),
        )

    if name == "get_incident":
        return await incident_tools.get_incident(tool_input["incident_id"])

    return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# Location lookup (Azure Maps preferred, OSM Nominatim + Overpass fallback)
# ---------------------------------------------------------------------------

_OSM_HEADERS = {"User-Agent": "SJIFire-Ops/1.0 (incident-reporting)"}
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"


async def _lookup_location(lat: float, lon: float) -> dict:
    """Reverse geocode and find nearby cross streets.

    Uses Azure Maps when ``AZURE_MAPS_KEY`` is set (production),
    falls back to OSM Nominatim + Overpass (dev/free tier).
    """
    from sjifire.ops.geo import get_azure_maps_key, reverse_geocode

    if get_azure_maps_key():
        return await reverse_geocode(lat, lon)

    return await _lookup_location_osm(lat, lon)


async def _lookup_location_osm(lat: float, lon: float) -> dict:
    """Reverse geocode via OpenStreetMap (fallback for dev without Azure Maps)."""
    async with httpx.AsyncClient(timeout=10, headers=_OSM_HEADERS) as client:
        # Reverse geocode
        resp = await client.get(
            _NOMINATIM_URL,
            params={"lat": lat, "lon": lon, "format": "json", "addressdetails": "1"},
        )
        resp.raise_for_status()
        geo = resp.json()
        address = geo.get("address", {})
        main_road = address.get("road", "")

        # Find nearby named roads via Overpass
        query = f"[out:json][timeout:5];way(around:200,{lat},{lon})[highway][name];out tags;"
        try:
            resp2 = await client.post(_OVERPASS_URL, data={"data": query})
            resp2.raise_for_status()
            elements = resp2.json().get("elements", [])
        except Exception:
            logger.warning("Overpass query failed for %.6f,%.6f", lat, lon)
            elements = []

    # Rank roads by importance so main arteries sort first
    road_rank = {
        "motorway": 0,
        "trunk": 1,
        "primary": 2,
        "secondary": 3,
        "tertiary": 4,
        "residential": 5,
        "unclassified": 6,
        "service": 7,
        "track": 8,
        "path": 9,
    }

    nearby: list[dict] = []
    seen: set[str] = set()
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name", "")
        if not name or name.lower() == main_road.lower() or name in seen:
            continue
        seen.add(name)
        hw = tags.get("highway", "")
        nearby.append(
            {
                "name": name,
                "type": hw,
                "rank": road_rank.get(hw, 99),
            }
        )

    nearby.sort(key=lambda r: r["rank"])

    # Include OSM property classification so the AI can map to NERIS location_use
    osm_category = geo.get("category", "")
    osm_type = geo.get("type", "")

    return {
        "road": main_road,
        "cross_streets": [{"name": r["name"], "type": r["type"]} for r in nearby],
        "city": address.get("city") or address.get("town") or address.get("village", ""),
        "county": address.get("county", ""),
        "state": address.get("state", ""),
        "display_address": geo.get("display_name", ""),
        "property_type": f"{osm_category}/{osm_type}" if osm_category else "",
    }
