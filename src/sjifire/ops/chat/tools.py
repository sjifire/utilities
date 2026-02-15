"""Anthropic tool definitions and dispatcher for chat conversations.

Wraps the same async functions the MCP tools use, with user context
set before each call. Only a safe subset of tools is exposed to the
chat Claude — no create, delete, or submit operations.
"""

import json
import logging

from sjifire.ops.auth import UserContext, set_current_user

logger = logging.getLogger(__name__)

# Anthropic tool definitions — must match the wrapped function signatures
TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_incident",
        "description": (
            "Get the current incident report by ID. Returns all fields "
            "including crew, timestamps, narratives, and completeness."
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
                "crew": {
                    "type": "array",
                    "description": "Full crew list (replaces existing)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "email": {"type": "string"},
                            "rank": {"type": "string"},
                            "position": {"type": "string"},
                            "unit": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                },
                "outcome_narrative": {
                    "type": "string",
                    "description": "What happened (narrative)",
                },
                "actions_taken_narrative": {
                    "type": "string",
                    "description": "What actions were taken (narrative)",
                },
                "unit_responses": {
                    "type": "array",
                    "description": "NERIS apparatus/unit response data",
                    "items": {"type": "object"},
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
            },
            "required": ["incident_id"],
        },
    },
    {
        "name": "reset_incident",
        "description": (
            "Reset an incident report to a clean slate. Clears all content "
            "fields (type, crew, narratives) and re-populates address and "
            "timestamps from dispatch data. Use when the user wants to start over."
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
            "Get a list of ALL active personnel (names + emails). Use this "
            "only if you cannot match a name from the pre-loaded operational "
            "roster — e.g. for admin staff or volunteers not in the roster."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]

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
        return await incident_tools.update_incident(incident_id, **kwargs)

    if name == "reset_incident":
        return await incident_tools.reset_incident(tool_input["incident_id"])

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

        result = await personnel_tools.get_personnel()
        return {"personnel": result, "count": len(result)}

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
            "including crew, timestamps, narratives, and completeness."
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
