#!/usr/bin/env python3
"""Ops admin CLI for local incident management.

Commands:
    reopen-incident - Unlock a submitted/approved incident back to draft
    reset-incident  - Reset a draft incident (bypasses cooldown)
    re-enrich       - Re-run LLM enrichment on a dispatch call
    update-neris    - Push local corrections to a NERIS record

Usage:
    uv run ops-admin reopen-incident <incident-id>
    uv run ops-admin reset-incident <incident-id>
    uv run ops-admin reset-incident <incident-id> --email user@sjifire.org
    uv run ops-admin re-enrich <dispatch-id>
    uv run ops-admin update-neris <incident-id> [--fields narrative timestamps]
"""

import argparse
import asyncio
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Silence noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)


def _setup_officer_context(email: str, name: str) -> None:
    """Set up an editor-privileged UserContext for local admin use."""
    fake_group = "local-admin-officer"
    os.environ["ENTRA_REPORT_EDITORS_GROUP_ID"] = fake_group

    from sjifire.ops.auth import UserContext, set_current_user

    set_current_user(
        UserContext(
            email=email,
            name=name,
            user_id="local-admin",
            groups=frozenset({fake_group}),
        )
    )


async def _reopen_incident(incident_id: str, email: str | None) -> int:
    """Reopen a submitted/approved incident back to draft."""
    from sjifire.ops.incidents.store import IncidentStore

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

    if doc is None:
        print(f"Error: Incident {incident_id} not found")
        return 1

    user_email = email or doc.created_by
    user_name = next(
        (e.editor_name for e in doc.edit_history if e.editor_email == user_email),
        user_email.split("@")[0].title(),
    )

    _setup_officer_context(user_email, user_name)

    from sjifire.ops.incidents.tools import reopen_incident

    result = await reopen_incident(incident_id)

    if "error" in result:
        print(f"Error: {result['error']}")
        return 1

    print(f"Reopened incident {result.get('incident_number', incident_id)}")
    print(f"  Status: {result['previous_status']} → {result['status']}")
    print(f"  {result.get('message', '')}")
    return 0


async def _reset_incident(incident_id: str, email: str | None) -> int:
    """Reset a draft incident, bypassing the 24hr cooldown."""
    from sjifire.ops.incidents.store import IncidentStore

    # Look up the incident to find the creator
    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

    if doc is None:
        print(f"Error: Incident {incident_id} not found")
        return 1

    # Use provided email or fall back to incident creator
    user_email = email or doc.created_by
    # Look up name from edit history, fall back to email prefix
    user_name = next(
        (e.editor_name for e in doc.edit_history if e.editor_email == user_email),
        user_email.split("@")[0].title(),
    )

    _setup_officer_context(user_email, user_name)

    # If locked, reopen first
    if doc.status in ("submitted", "approved"):
        from sjifire.ops.incidents.tools import reopen_incident

        reopen_result = await reopen_incident(incident_id)
        if "error" in reopen_result:
            print(f"Error reopening: {reopen_result['error']}")
            return 1
        print(f"Reopened ({doc.status} → draft)")

    from sjifire.ops.auth import get_current_user
    from sjifire.ops.token_store import get_token_store

    user = get_current_user()

    # Clear cooldown so reset_incident() doesn't reject us
    token_store = await get_token_store()
    await token_store.delete("incident_reset_cooldown", user.email)

    from sjifire.ops.incidents.tools import reset_incident

    result = await reset_incident(incident_id)

    if "error" in result:
        print(f"Error: {result['error']}")
        return 1

    print(f"Reset incident {result.get('incident_number', incident_id)}")
    print(f"  Status: {result.get('status')}")
    print(f"  Address: {result.get('address', '—')}")
    if result.get("timestamps"):
        print(f"  Timestamps: {result['timestamps']}")
    return 0


async def _re_enrich(dispatch_id: str) -> int:
    """Re-run enrichment on a dispatch call and save results."""
    from sjifire.ops.dispatch.enrich import enrich_dispatch
    from sjifire.ops.dispatch.store import DispatchStore

    async with DispatchStore() as store:
        doc = await store.get_by_dispatch_id(dispatch_id)
        if doc is None:
            print(f"Error: Dispatch call {dispatch_id} not found")
            return 1

        print(f"Re-enriching {dispatch_id} ({doc.nature} — {doc.address})...")
        analysis = await enrich_dispatch(doc)
        doc.analysis = analysis
        await store.upsert(doc)

    print(f"  Summary:   {analysis.summary}")
    print(f"  Short dsc: {analysis.short_dsc}")
    print(f"  Outcome:   {analysis.outcome}")
    print(f"  IC:        {analysis.incident_commander} → {analysis.incident_commander_name}")
    print(f"  Actions:   {analysis.actions_taken}")
    return 0


def cmd_reopen_incident(args: argparse.Namespace) -> int:
    """Reopen a submitted/approved incident."""
    return asyncio.run(_reopen_incident(args.incident_id, getattr(args, "email", None)))


def cmd_reset_incident(args: argparse.Namespace) -> int:
    """Reset a draft incident."""
    return asyncio.run(_reset_incident(args.incident_id, args.email))


async def _update_neris(incident_id: str, email: str | None, fields: list[str] | None) -> int:
    """Push local corrections to a NERIS record."""
    from sjifire.ops.incidents.store import IncidentStore

    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)

    if doc is None:
        print(f"Error: Incident {incident_id} not found")
        return 1

    user_email = email or doc.created_by
    user_name = next(
        (e.editor_name for e in doc.edit_history if e.editor_email == user_email),
        user_email.split("@")[0].title(),
    )

    _setup_officer_context(user_email, user_name)

    from sjifire.ops.incidents.tools import update_neris_incident

    result = await update_neris_incident(incident_id, fields=fields)

    if "error" in result:
        print(f"Error: {result['error']}")
        return 1

    status = result.get("status", "")
    if status == "no_changes":
        print(result.get("message", "No changes needed."))
        return 0

    print(f"Updated NERIS record: {result.get('neris_id')}")
    print(f"  Fields: {', '.join(result.get('fields_updated', []))}")
    print(f"  Snapshot: {result.get('snapshot_id')}")
    return 0


def cmd_re_enrich(args: argparse.Namespace) -> int:
    """Re-run enrichment on a dispatch call."""
    return asyncio.run(_re_enrich(args.dispatch_id))


def cmd_update_neris(args: argparse.Namespace) -> int:
    """Push local corrections to NERIS."""
    fields = args.fields if args.fields else None
    return asyncio.run(_update_neris(args.incident_id, getattr(args, "email", None), fields))


def main() -> None:
    """CLI entry point for ops admin commands."""
    parser = argparse.ArgumentParser(description="Ops admin CLI")
    sub = parser.add_subparsers(dest="command")

    reopen_p = sub.add_parser("reopen-incident", help="Unlock submitted/approved → draft")
    reopen_p.add_argument("incident_id", help="Incident document ID")
    reopen_p.add_argument("--email", help="Override user email (default: incident creator)")

    reset_p = sub.add_parser("reset-incident", help="Reset a draft incident")
    reset_p.add_argument("incident_id", help="Incident document ID")
    reset_p.add_argument(
        "--email",
        help="Override user email (default: incident creator)",
    )

    enrich_p = sub.add_parser("re-enrich", help="Re-run enrichment on a dispatch call")
    enrich_p.add_argument("dispatch_id", help="Dispatch ID (e.g. 26-002210)")

    neris_p = sub.add_parser("update-neris", help="Push local corrections to NERIS")
    neris_p.add_argument("incident_id", help="Incident document ID")
    neris_p.add_argument("--email", help="Override user email (default: incident creator)")
    neris_p.add_argument(
        "--fields",
        nargs="+",
        help="Specific fields to update (e.g. narrative timestamps units)",
    )

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "reopen-incident": cmd_reopen_incident,
        "reset-incident": cmd_reset_incident,
        "re-enrich": cmd_re_enrich,
        "update-neris": cmd_update_neris,
    }
    sys.exit(commands[args.command](args))
