"""Anthropic tool definitions and dispatcher for chat conversations.

Wraps the same async functions the MCP tools use, with user context
set before each call. Only a safe subset of tools is exposed to the
chat Claude — no create, delete, submit, or reset operations.
"""

import json
import logging

from sjifire.mcp.auth import UserContext, set_current_user

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
            "Get the crew on duty for a specific date. Returns names, "
            "positions, sections, and shift times."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format (defaults to today)",
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
    from sjifire.mcp.dispatch import tools as dispatch_tools
    from sjifire.mcp.incidents import tools as incident_tools
    from sjifire.mcp.neris import tools as neris_tools
    from sjifire.mcp.schedule import tools as schedule_tools

    if name == "get_incident":
        return await incident_tools.get_incident(tool_input["incident_id"])

    if name == "update_incident":
        incident_id = tool_input.pop("incident_id")
        return await incident_tools.update_incident(incident_id, **tool_input)

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
        )

    if name == "get_neris_values":
        return await neris_tools.get_neris_values(
            value_set=tool_input["value_set"],
            prefix=tool_input.get("prefix"),
            search=tool_input.get("search"),
        )

    return {"error": f"Unknown tool: {name}"}
