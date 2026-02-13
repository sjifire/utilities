"""MCP tools for querying iSpyFire dispatch/call data.

Exposes recent calls, call details, open calls, and call audit logs
via MCP tools. All blocking ISpyFireClient calls are wrapped with
``asyncio.to_thread()`` for async compatibility.
"""

import asyncio
import logging
from dataclasses import asdict

from sjifire.mcp.auth import get_current_user

logger = logging.getLogger(__name__)


def _call_to_dict(call) -> dict:
    """Convert a DispatchCall dataclass to a JSON-serializable dict."""
    d = asdict(call)
    # ispy_responders is already a dict; responder_details is a list of dicts via asdict
    return d


def _fetch_calls(days: int) -> list[dict]:
    """Fetch recent calls with full details (blocking)."""
    from sjifire.ispyfire.client import ISpyFireClient

    with ISpyFireClient() as client:
        summaries = client.get_calls(days=days)
        results = []
        for summary in summaries:
            detail = client.get_call_details(summary.id)
            if detail:
                results.append(_call_to_dict(detail))
    return results


def _fetch_call_details(call_id: str) -> dict | None:
    """Fetch details for a single call (blocking)."""
    from sjifire.ispyfire.client import ISpyFireClient

    with ISpyFireClient() as client:
        detail = client.get_call_details(call_id)
        if detail:
            return _call_to_dict(detail)
    return None


def _fetch_open_calls() -> list[dict]:
    """Fetch currently open calls (blocking)."""
    from sjifire.ispyfire.client import ISpyFireClient

    with ISpyFireClient() as client:
        calls = client.get_open_calls()
        return [_call_to_dict(c) for c in calls]


def _fetch_call_log(call_id: str) -> list[dict]:
    """Fetch audit log for a call (blocking)."""
    from sjifire.ispyfire.client import ISpyFireClient

    with ISpyFireClient() as client:
        return client.get_call_log(call_id)


async def list_dispatch_calls(days: int = 30) -> dict:
    """List recent dispatch calls with full details.

    Returns calls from the last 7 or 30 days, including nature,
    address, time reported, responding units, and status.

    Args:
        days: Number of days to look back (7 or 30). Defaults to 30.

    Returns:
        Dict with "calls" list and "count".
    """
    user = get_current_user()
    logger.info("Dispatch list (%d days) requested by %s", days, user.email)

    try:
        calls = await asyncio.to_thread(_fetch_calls, days)
        logger.info("Returning %d dispatch calls", len(calls))
        return {"calls": calls, "count": len(calls)}
    except Exception as e:
        logger.exception("Failed to list dispatch calls")
        return {"error": str(e)}


async def get_dispatch_call(call_id: str) -> dict:
    """Get full details for a specific dispatch call.

    Accepts either the internal UUID or the dispatch ID
    (e.g. "26-001678").

    Args:
        call_id: Call UUID or dispatch ID (e.g. "26-001678").

    Returns:
        Dict with all call fields: nature, address, responder
        timeline, comments, iSpy mobile responders, geo location.
    """
    user = get_current_user()
    logger.info("Dispatch call %s requested by %s", call_id, user.email)

    try:
        result = await asyncio.to_thread(_fetch_call_details, call_id)
        if result is None:
            return {"error": f"Call not found: {call_id}"}
        return result
    except Exception as e:
        logger.exception("Failed to get dispatch call details")
        return {"error": str(e)}


async def get_open_dispatch_calls() -> dict:
    """Get currently active/open dispatch calls.

    Returns any calls that have not yet been completed/closed.

    Returns:
        Dict with "calls" list (may be empty) and "count".
    """
    user = get_current_user()
    logger.info("Open dispatch calls requested by %s", user.email)

    try:
        calls = await asyncio.to_thread(_fetch_open_calls)
        logger.info("Returning %d open dispatch calls", len(calls))
        return {"calls": calls, "count": len(calls)}
    except Exception as e:
        logger.exception("Failed to get open dispatch calls")
        return {"error": str(e)}


async def get_dispatch_call_log(call_id: str) -> dict:
    """Get the audit log for a dispatch call.

    Shows who viewed the call and when.

    Args:
        call_id: Call UUID.

    Returns:
        Dict with "entries" list of {email, commenttype, timestamp}
        and "count".
    """
    user = get_current_user()
    logger.info("Dispatch call log %s requested by %s", call_id, user.email)

    try:
        entries = await asyncio.to_thread(_fetch_call_log, call_id)
        return {"entries": entries, "count": len(entries)}
    except Exception as e:
        logger.exception("Failed to get dispatch call log")
        return {"error": str(e)}
