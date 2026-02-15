"""Tools for querying iSpyFire dispatch/call data.

Thin interface layer. All data operations (fetching, enrichment,
storage) are delegated to ``DispatchStore``.
"""

import logging

from sjifire.ops.auth import get_current_user
from sjifire.ops.dispatch.store import DispatchStore

logger = logging.getLogger(__name__)


_MAX_DISPATCH_DAYS = 90


async def list_dispatch_calls(days: int = 30) -> dict:
    """List recent dispatch calls with full details.

    Returns calls from the last 7 or 30 days, including nature,
    address, time reported, responding units, and status.
    Completed calls are stored in Cosmos DB by background sync.

    Args:
        days: Number of days to look back (7 or 30). Defaults to 30.

    Returns:
        Dict with "calls" list and "count".
    """
    user = get_current_user()
    days = max(1, min(days, _MAX_DISPATCH_DAYS))
    logger.info("Dispatch list (%d days) requested by %s", days, user.email)

    try:
        async with DispatchStore() as store:
            docs = await store.list_recent_with_open()

        logger.info("Returning %d dispatch calls", len(docs))
        return {"calls": [d.to_dict() for d in docs], "count": len(docs)}
    except Exception:
        logger.exception("Failed to list dispatch calls")
        return {"error": "Unable to retrieve dispatch calls. Please try again."}


async def get_dispatch_call(call_id: str) -> dict:
    """Get full details for a specific dispatch call.

    Checks Cosmos DB first for fast retrieval, falls back to iSpyFire.
    Completed calls fetched from iSpyFire are enriched and stored.
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
        async with DispatchStore() as store:
            doc = await store.get_or_fetch(call_id)
            if doc is None:
                return {"error": f"Call not found: {call_id}"}

            result = doc.to_dict()

            # Include site history from archive
            if doc.address:
                history = await store.list_by_address(doc.address, exclude_id=doc.id)
                if history:
                    result["site_history"] = [
                        {
                            "dispatch_id": d.long_term_call_id,
                            "date": d.time_reported.isoformat() if d.time_reported else None,
                            "nature": d.nature,
                        }
                        for d in history
                    ]

            return result
    except Exception:
        logger.exception("Failed to get dispatch call details")
        return {"error": "Unable to retrieve call details. Please try again."}


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
        async with DispatchStore() as store:
            docs = await store.fetch_open()

        logger.info("Returning %d open dispatch calls", len(docs))
        return {"calls": [d.to_dict() for d in docs], "count": len(docs)}
    except Exception:
        logger.exception("Failed to get open dispatch calls")
        return {"error": "Unable to retrieve open calls. Please try again."}


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
    except Exception:
        logger.exception("Failed to search dispatch calls")
        return {"error": "Unable to search dispatch calls. Please try again."}
