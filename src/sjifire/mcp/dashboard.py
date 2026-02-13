"""MCP dashboard tool — session-start status board.

Returns on-duty crew, recent dispatch calls, and their incident report
status in a single call so Claude.ai can immediately orient the user.
"""

import asyncio
import logging
from datetime import UTC, datetime

from sjifire.mcp.auth import get_current_user
from sjifire.mcp.dispatch.store import DispatchStore
from sjifire.mcp.incidents.store import IncidentStore
from sjifire.mcp.schedule import tools as schedule_tools

logger = logging.getLogger(__name__)


async def get_dashboard() -> dict:
    """Get a status board with on-duty crew and recent calls.

    Returns who is on duty today, the most recent dispatch calls, and
    whether each call has an incident report started.  Designed to be
    called at the start of every Claude.ai session so users see an
    instant overview.

    Returns:
        Dict with ``user``, ``on_duty``, ``recent_calls``, and
        ``call_count`` keys.
    """
    user = get_current_user()

    # Fetch all three data sources in parallel.  return_exceptions=True
    # so a single failure doesn't block the others.
    calls_result, schedule_result, incidents_result = await asyncio.gather(
        _fetch_recent_calls(),
        _fetch_schedule(),
        _fetch_incidents(user.email, user.is_officer),
        return_exceptions=True,
    )

    result: dict = {
        "timestamp": datetime.now(UTC).isoformat(),
        "user": {
            "email": user.email,
            "name": user.name,
            "is_officer": user.is_officer,
        },
    }

    # --- Schedule section ---
    if isinstance(schedule_result, BaseException):
        logger.exception("Dashboard: schedule fetch failed", exc_info=schedule_result)
        result["on_duty"] = {"error": str(schedule_result)}
    else:
        result["on_duty"] = schedule_result

    # --- Build incident lookup ---
    incident_lookup: dict[str, dict] = {}
    if isinstance(incidents_result, BaseException):
        logger.exception("Dashboard: incidents fetch failed", exc_info=incidents_result)
    else:
        incident_lookup = incidents_result

    # --- Recent calls section ---
    if isinstance(calls_result, BaseException):
        logger.exception("Dashboard: dispatch fetch failed", exc_info=calls_result)
        result["recent_calls"] = {"error": str(calls_result)}
        result["call_count"] = 0
    else:
        recent_calls = []
        for call in calls_result:
            entry: dict = {
                "dispatch_id": call.long_term_call_id,
                "date": call.time_reported.isoformat() if call.time_reported else None,
                "nature": call.nature,
                "address": call.address,
            }
            # Cross-reference with incident reports
            incident_info = incident_lookup.get(call.long_term_call_id)
            entry["report"] = incident_info
            recent_calls.append(entry)

        result["recent_calls"] = recent_calls
        result["call_count"] = len(recent_calls)

    return result


async def _fetch_recent_calls():
    """Fetch recent dispatch calls from Cosmos DB."""
    async with DispatchStore() as store:
        return await store.list_recent(limit=15)


async def _fetch_schedule():
    """Fetch today's on-duty crew via the existing schedule tool."""
    return await schedule_tools.get_on_duty_crew()


async def _fetch_incidents(user_email: str, is_officer: bool) -> dict[str, dict]:
    """Fetch non-submitted incidents and build dispatch_id → report info lookup."""
    async with IncidentStore() as store:
        if is_officer:
            incidents = await store.list_by_status(exclude_status="submitted", max_items=50)
        else:
            incidents = await store.list_for_user(
                user_email, exclude_status="submitted", max_items=50
            )

    lookup: dict[str, dict] = {}
    for doc in incidents:
        lookup[doc.incident_number] = {
            "status": doc.status,
            "completeness": doc.completeness(),
            "incident_id": doc.id,
        }
    return lookup
