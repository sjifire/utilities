"""MCP tools for querying iSpyFire dispatch/call data.

Exposes recent calls, call details, open calls, and historical search
via MCP tools. Completed calls are cached in Cosmos DB for fast
retrieval and access beyond iSpyFire's 30-day window.

All blocking ISpyFireClient calls are wrapped with
``asyncio.to_thread()`` for async compatibility.
"""

import asyncio
import logging
import re
from dataclasses import asdict
from datetime import datetime

from sjifire.ispyfire.models import DispatchCall
from sjifire.mcp.auth import get_current_user
from sjifire.mcp.dispatch.models import DispatchCallDocument
from sjifire.mcp.dispatch.store import DispatchStore

logger = logging.getLogger(__name__)


def _call_to_dict(call: DispatchCall) -> dict:
    """Convert a DispatchCall dataclass to a JSON-serializable dict."""
    d = asdict(call)
    # Serialize datetime fields
    if isinstance(d.get("time_reported"), datetime):
        d["time_reported"] = d["time_reported"].isoformat()
    for rd in d.get("responder_details", []):
        if isinstance(rd.get("time_of_status_change"), datetime):
            rd["time_of_status_change"] = rd["time_of_status_change"].isoformat()
    return d


def _fetch_calls(days: int) -> list[DispatchCall]:
    """Fetch recent calls with full details (blocking). Returns raw dataclasses."""
    from sjifire.ispyfire.client import ISpyFireClient

    with ISpyFireClient() as client:
        summaries = client.get_calls(days=days)
        results = []
        for summary in summaries:
            detail = client.get_call_details(summary.id)
            if detail:
                results.append(detail)
    return results


def _fetch_call_details(call_id: str) -> DispatchCall | None:
    """Fetch details for a single call (blocking). Returns raw dataclass."""
    from sjifire.ispyfire.client import ISpyFireClient

    with ISpyFireClient() as client:
        return client.get_call_details(call_id)


def _fetch_open_calls() -> list[DispatchCall]:
    """Fetch currently open calls (blocking). Returns raw dataclasses."""
    from sjifire.ispyfire.client import ISpyFireClient

    with ISpyFireClient() as client:
        return client.get_open_calls()


async def _store_completed_calls(calls: list[DispatchCall]) -> int:
    """Store completed calls in Cosmos DB as a side effect.

    Errors are logged but never propagated to the caller.
    """
    try:
        async with DispatchStore() as store:
            return await store.store_completed(calls)
    except Exception:
        logger.warning("Failed to store completed calls", exc_info=True)
        return 0


async def list_dispatch_calls(days: int = 30) -> dict:
    """List recent dispatch calls with full details.

    Returns calls from the last 7 or 30 days, including nature,
    address, time reported, responding units, and status.
    Completed calls are stored in Cosmos DB as a side effect.

    Args:
        days: Number of days to look back (7 or 30). Defaults to 30.

    Returns:
        Dict with "calls" list and "count".
    """
    user = get_current_user()
    logger.info("Dispatch list (%d days) requested by %s", days, user.email)

    try:
        calls = await asyncio.to_thread(_fetch_calls, days)

        # Store completed calls as a side effect (fire and forget)
        stored = await _store_completed_calls(calls)
        if stored:
            logger.info("Stored %d completed calls from listing", stored)

        call_dicts = [_call_to_dict(c) for c in calls]
        logger.info("Returning %d dispatch calls", len(call_dicts))
        return {"calls": call_dicts, "count": len(call_dicts)}
    except Exception as e:
        logger.exception("Failed to list dispatch calls")
        return {"error": str(e)}


async def get_dispatch_call(call_id: str) -> dict:
    """Get full details for a specific dispatch call.

    Checks Cosmos DB first for fast retrieval, falls back to iSpyFire.
    Completed calls fetched from iSpyFire are stored for future lookups.
    Includes site history (previous calls at the same address) when
    available from the archive.

    Accepts either the internal UUID or the dispatch ID
    (e.g. "26-001678").

    Args:
        call_id: Call UUID or dispatch ID (e.g. "26-001678").

    Returns:
        Dict with all call fields: nature, address, responder
        timeline, CAD comments, geo location, and site_history.
    """
    user = get_current_user()
    logger.info("Dispatch call %s requested by %s", call_id, user.email)

    try:
        # Check Cosmos DB first
        doc = await _lookup_in_store(call_id)
        if doc:
            logger.info("Dispatch call %s found in store", call_id)
            result = doc.to_dict()
            address = doc.address
            exclude_id = doc.id
        else:
            # Fall back to iSpyFire
            call = await asyncio.to_thread(_fetch_call_details, call_id)
            if call is None:
                return {"error": f"Call not found: {call_id}"}

            # Store if completed
            if call.is_completed:
                await _store_single_call(call)

            result = _call_to_dict(call)
            address = call.address
            exclude_id = call.id

        # Include site history from archive
        if address:
            history = await _get_site_history(address, exclude_id)
            if history:
                result["site_history"] = history

        return result
    except Exception as e:
        logger.exception("Failed to get dispatch call details")
        return {"error": str(e)}


async def get_open_dispatch_calls() -> dict:
    """Get currently active/open dispatch calls.

    Returns any calls that have not yet been completed/closed.
    Always fetches live from iSpyFire (open calls are mutable).

    Returns:
        Dict with "calls" list (may be empty) and "count".
    """
    user = get_current_user()
    logger.info("Open dispatch calls requested by %s", user.email)

    try:
        calls = await asyncio.to_thread(_fetch_open_calls)
        call_dicts = [_call_to_dict(c) for c in calls]
        logger.info("Returning %d open dispatch calls", len(call_dicts))
        return {"calls": call_dicts, "count": len(call_dicts)}
    except Exception as e:
        logger.exception("Failed to get open dispatch calls")
        return {"error": str(e)}


async def search_dispatch_calls(
    dispatch_id: str = "",
    start_date: str = "",
    end_date: str = "",
) -> dict:
    """Search historical dispatch calls stored in the database.

    Searches the Cosmos DB archive of completed calls. Use this for
    looking up calls older than 30 days or searching by date range.

    At least one search parameter must be provided.

    Args:
        dispatch_id: Dispatch ID to search for (e.g. "26-001678").
        start_date: Start of date range (YYYY-MM-DD).
        end_date: End of date range (YYYY-MM-DD).

    Returns:
        Dict with "calls" list and "count", or "error" if no
        parameters provided.
    """
    user = get_current_user()
    logger.info(
        "Dispatch search by %s: dispatch_id=%s, dates=%s to %s",
        user.email,
        dispatch_id or "(none)",
        start_date or "(none)",
        end_date or "(none)",
    )

    if not dispatch_id and not start_date and not end_date:
        return {
            "error": "At least one search parameter required: dispatch_id, start_date, or end_date"
        }

    try:
        async with DispatchStore() as store:
            # Search by dispatch ID
            if dispatch_id:
                doc = await store.get_by_dispatch_id(dispatch_id)
                if doc:
                    return {"calls": [doc.to_dict()], "count": 1}
                return {"calls": [], "count": 0}

            # Search by date range
            if not start_date or not end_date:
                return {"error": "Both start_date and end_date are required for date range search"}

            docs = await store.list_by_date_range(start_date, end_date)
            return {
                "calls": [d.to_dict() for d in docs],
                "count": len(docs),
            }
    except Exception as e:
        logger.exception("Failed to search dispatch calls")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _lookup_in_store(call_id: str) -> DispatchCallDocument | None:
    """Look up a call in Cosmos DB by UUID or dispatch ID."""
    try:
        async with DispatchStore() as store:
            if re.match(r"\d{2}-\d+", call_id):
                # Dispatch ID (e.g. "26-001678")
                return await store.get_by_dispatch_id(call_id)
            else:
                # UUID — need the year for a point read.
                # Try current year and previous year as a heuristic.
                from datetime import UTC
                from datetime import datetime as dt_cls

                current_year = str(dt_cls.now(UTC).year)
                doc = await store.get(call_id, current_year)
                if doc:
                    return doc
                prev_year = str(int(current_year) - 1)
                return await store.get(call_id, prev_year)
    except Exception:
        logger.debug("Store lookup failed for %s", call_id, exc_info=True)
        return None


async def _store_single_call(call: DispatchCall) -> None:
    """Store a single completed call in Cosmos DB."""
    try:
        doc = DispatchCallDocument.from_dispatch_call(call)
        async with DispatchStore() as store:
            await store.upsert(doc)
        logger.info("Stored completed call %s (%s)", call.long_term_call_id, call.id)
    except Exception:
        logger.warning("Failed to store call %s", call.id, exc_info=True)


async def _get_site_history(address: str, exclude_id: str) -> list[dict]:
    """Get previous calls at the same address from the archive."""
    try:
        async with DispatchStore() as store:
            docs = await store.list_by_address(address, exclude_id=exclude_id)
        return [
            {
                "dispatch_id": d.long_term_call_id,
                "date": d.time_reported.isoformat() if d.time_reported else None,
                "nature": d.nature,
            }
            for d in docs
        ]
    except Exception:
        logger.debug("Failed to fetch site history for %s", address, exc_info=True)
        return []
