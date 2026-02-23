"""Chat engine: Claude API streaming with tool-use loop.

Core function: ``run_chat()`` takes a user message and publishes events
to a Centrifugo channel. Handles tool calls, budget tracking, and
conversation persistence in Cosmos DB.
"""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from anthropic import AsyncAnthropic, RateLimitError

from sjifire.core.anthropic import MODEL, cached_system, get_client
from sjifire.core.config import get_org_config, local_now
from sjifire.ops.auth import UserContext
from sjifire.ops.chat.budget import check_budget, record_usage
from sjifire.ops.chat.centrifugo import publish
from sjifire.ops.chat.models import (
    MAX_TURNS,
    ContextSnapshot,
    ConversationDocument,
    ConversationMessage,
)
from sjifire.ops.chat.store import ConversationStore
from sjifire.ops.chat.tools import (
    GENERAL_TOOL_SCHEMAS,
    TOOL_SCHEMAS,
    execute_general_tool,
    execute_tool,
)
from sjifire.ops.chat.turn_lock import TurnLockStore

# Set of fire-and-forget tasks — prevents GC from collecting running tasks.
_background_tasks: set[asyncio.Task] = set()

logger = logging.getLogger(__name__)
MAX_RESPONSE_TOKENS = 4096
MAX_CONTEXT_MESSAGES = 20  # Keep last N turns to stay under token limits
RATE_LIMIT_MAX_RETRIES = 3
RATE_LIMIT_BASE_DELAY = 15  # seconds — generous for token-per-minute limits

# Path to incident report instructions (system prompt context)
_SRC_DOCS = Path(__file__).resolve().parents[4] / "docs"
_APP_DOCS = Path("/app/docs")
_DOCS_DIR = _SRC_DOCS if _SRC_DOCS.is_dir() else _APP_DOCS


_ERROR_MESSAGES = {
    "budget": "Unable to check usage limits. Please try again.",
    "conversation": "Unable to load conversation. Please try again.",
    "context": "Unable to load incident data. Please try again.",
    "stream": "Something went wrong. Please try again.",
    "save": "Unable to save conversation. Your message was processed but may not appear on reload.",
}


async def _release_turn_lock(incident_id: str, email: str) -> None:
    """Release the distributed turn lock, swallowing errors."""
    try:
        async with TurnLockStore() as store:
            await store.release(incident_id, email)
    except Exception:
        logger.warning("Failed to release turn lock for %s", incident_id, exc_info=True)


def _user_error(category: str, exc: Exception) -> str:
    """Build a user-friendly error message with a reference ID for debugging.

    Logs the full exception; returns only a short message + error ID.
    """
    error_id = uuid.uuid4().hex[:8]
    logger.error("Chat error [%s] %s: %s: %s", error_id, category, type(exc).__name__, exc)
    friendly = _ERROR_MESSAGES.get(category, "Something went wrong.")
    return f"{friendly} (ref: {error_id})"


def _load_doc(name: str) -> str:
    """Load a doc file relative to _DOCS_DIR.

    Examples: ``_load_doc("incident-chat-prompt.md")``,
    ``_load_doc("neris/neris-cheat-sheet.md")``.
    """
    path = _DOCS_DIR / name
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        logger.warning("Doc file not found: %s", path)
        return ""


def _build_system_prompt(
    user_name: str,
    user_email: str,
    dispatch_json: str = "{}",
    crew_json: str = "[]",
    personnel_json: str = "[]",
) -> str:
    """Build the system prompt for Claude.

    Layout (order matters for Anthropic prefix caching):
    1. Static content first (persona, instructions, NERIS codes, cheat sheet)
       — cached across ALL conversations.
    2. Per-incident stable data last (dispatch, crew, personnel)
       — cached within one conversation after turn 1.

    Incident state and attachments go in the messages array via
    ``_build_context_message`` because they change after tool calls.
    """
    org = get_org_config()
    prompt = _load_doc("incident-chat-prompt.md").format(
        company_name=org.company_name,
        user_name=user_name,
        user_email=user_email,
    )
    instructions = _load_doc("neris/incident-report-instructions.md")
    cheat_sheet = _load_doc("neris/neris-cheat-sheet.md")

    sections = [
        # --- Static (cached across all conversations) ---
        prompt,
        instructions,
        f"REPORT AUTHOR: {user_name} ({user_email})",
        _get_all_neris_incident_types(),
        cheat_sheet,
        # --- Per-incident stable (cached within conversation after turn 1) ---
        f"DISPATCH DATA:\n{dispatch_json}",
        f"CREW ON DUTY:\n{crew_json}",
        f"PERSONNEL ROSTER (use to match last names to full names + emails):\n{personnel_json}",
    ]
    return "\n\n".join(sections)


def _build_context_message(
    incident_json: str,
    attachments_summary: str = "",
) -> str:
    """Build the context preamble injected into the user message.

    Only includes data that changes between turns (incident state after
    tool calls, attachments after uploads). Stable data (dispatch, crew,
    personnel) lives in the system prompt where Anthropic caches it.
    """
    parts = [f"CURRENT INCIDENT STATE:\n{incident_json}"]
    if attachments_summary:
        parts.append(f"ATTACHMENTS ON FILE:\n{attachments_summary}")
    return "\n\n".join(parts)


def _format_unit_times_table(
    unit_times: list[dict],
    time_reported: str = "",
    alarm_time: str = "",
) -> str:
    """Format unit response times as a readable table for the system prompt.

    Two sections:
    1. **Incident timestamps** — derived from earliest unit times plus
       time_reported. Maps to ``update_incident(timestamps={...})``.
    2. **Per-unit times** — each apparatus with key timestamps for review.
       Maps to ``update_incident(units=[...])``.

    The ``alarm_time`` (SJF3 PAGED) is used as the default dispatch time
    for all units — the page goes out once and all units respond from it.
    Falls back to ``time_reported`` if alarm_time is empty.
    """
    # Default dispatch time: alarm (page) time, or call received time
    default_dispatched = alarm_time or time_reported

    def _time(iso: str) -> str:
        """Extract HH:MM:SS from an ISO timestamp, or '--' if empty."""
        if not iso:
            return "--"
        if "T" in iso:
            iso = iso.split("T", 1)[1]
        for sep in ("+", "Z"):
            if sep in iso:
                iso = iso.split(sep, 1)[0]
        return iso[:8]

    def _earliest(field: str) -> str:
        """Find the earliest non-empty value for a field across all units."""
        values = [ut.get(field, "") for ut in unit_times if ut.get(field)]
        return min(values) if values else ""

    def _latest(field: str) -> str:
        """Find the latest non-empty value for a field across all units."""
        values = [ut.get(field, "") for ut in unit_times if ut.get(field)]
        return max(values) if values else ""

    # --- Incident-level timestamps (→ update_incident timestamps={}) ---
    dispatched = _earliest("paged") or default_dispatched
    lines = [
        "INCIDENT TIMESTAMPS (save via timestamps={...}):",
        f"  Call Received (psap_answer):       {_time(time_reported)}",
        f"  First Dispatched (first_unit_dispatched): {_time(dispatched)}",
        f"  First Enroute (first_unit_enroute):    {_time(_earliest('enroute'))}",
        f"  First On Scene (first_unit_arrived):   {_time(_earliest('arrived'))}",
        f"  Last Unit Cleared (last_unit_cleared):  {_time(_latest('completed'))}",
        f"  Last In Quarters (last_unit_in_quarters): {_time(_latest('in_quarters'))}",
    ]

    # --- Per-unit times (→ update_incident units=[]) ---
    lines.append("")
    lines.append("UNIT RESPONSE TIMES (save via units=[...]):")
    lines.append("(-- = missing, needs to be filled in or confirmed N/A)")
    lines.append("(Staged = ARSTN/ARRNL, unit waiting at a location but NOT on scene)")
    lines.append("(Use these EXACT timestamps — do NOT round or estimate)")
    header = "Unit     | Dispatched | Enroute  | Staged   | On Scene | Cleared  | In Quarters"
    divider = "---------|------------|----------|----------|----------|----------|------------"
    lines.extend([header, divider])
    for ut in unit_times:
        unit = (ut.get("unit") or "?").ljust(8)
        paged = _time(ut.get("paged") or default_dispatched).ljust(10)
        enroute = _time(ut.get("enroute", "")).ljust(8)
        staged = _time(ut.get("staged", "")).ljust(8)
        arrived = _time(ut.get("arrived", "")).ljust(8)
        completed = _time(ut.get("completed", "")).ljust(8)
        in_quarters = _time(ut.get("in_quarters", ""))
        row = f"{unit} | {paged} | {enroute} | {staged} | {arrived} | {completed} | {in_quarters}"
        lines.append(row)

    return "\n".join(lines)


def _conversation_to_api_messages(
    messages: list[ConversationMessage],
) -> list[dict]:
    """Convert stored conversation messages to Claude API format.

    Validates tool_use/tool_result pairing: if a tool_result message references
    tool_use IDs that don't exist in the preceding assistant message, it is
    dropped. This repairs conversations corrupted by pre-fix reset bugs.
    """
    api_messages: list[dict] = []
    for msg in messages:
        entry: dict = {"role": msg.role, "content": []}
        if msg.content:
            entry["content"].append({"type": "text", "text": msg.content})
        if msg.tool_use:
            entry["content"].extend(msg.tool_use)
        if msg.tool_results:
            # Validate: preceding message must have matching tool_use blocks
            prev_tool_ids: set[str] = set()
            if api_messages:
                prev = api_messages[-1]
                if prev.get("role") == "assistant" and isinstance(prev.get("content"), list):
                    prev_tool_ids = {
                        b["id"]
                        for b in prev["content"]
                        if isinstance(b, dict) and b.get("type") == "tool_use"
                    }
            result_ids = {
                r["tool_use_id"]
                for r in msg.tool_results
                if isinstance(r, dict) and "tool_use_id" in r
            }
            if result_ids and not result_ids.issubset(prev_tool_ids):
                logger.warning(
                    "Dropping orphaned tool_results (ids=%s)",
                    result_ids - prev_tool_ids,
                )
                continue
            entry["role"] = "user"
            entry["content"] = msg.tool_results
        if not entry["content"]:
            entry["content"] = msg.content or ""
        api_messages.append(entry)
    return api_messages


def _trim_messages(messages: list[dict]) -> list[dict]:
    """Keep only the last MAX_CONTEXT_MESSAGES turns to stay under token limits.

    Ensures tool_result blocks always have their matching tool_use in the
    preceding message.  If trimming would orphan a tool_result, we back up
    one more message to include the tool_use.
    """
    limit = MAX_CONTEXT_MESSAGES * 2
    if len(messages) <= limit:
        return messages

    trimmed = messages[-limit:]

    # If the first kept message is a user message containing tool_result
    # blocks, the preceding assistant message with the tool_use was trimmed.
    # Back up to include it.
    if trimmed and trimmed[0].get("role") == "user":
        content = trimmed[0].get("content")
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            start = len(messages) - limit
            if start > 0:
                trimmed = [messages[start - 1], *trimmed]

    # Final safety check: drop any leading tool_result messages that still
    # lack a preceding tool_use (e.g. corrupted conversation history).
    while trimmed and trimmed[0].get("role") == "user":
        content = trimmed[0].get("content")
        if isinstance(content, list) and all(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            logger.warning("Dropping orphaned tool_result message from history")
            trimmed = trimmed[1:]
        else:
            break

    return trimmed


_neris_incident_types_cache: str = ""


def _get_all_neris_incident_types() -> str:
    """Return all 128 NERIS incident type codes, grouped by category.

    Cached after first call. Included in the system prompt so the agent
    can pick the right code without any tool calls.
    """
    global _neris_incident_types_cache
    if _neris_incident_types_cache:
        return _neris_incident_types_cache

    from sjifire.ops.neris.tools import _VALUE_SETS, _humanize

    incident_enum = _VALUE_SETS.get("incident")
    if not incident_enum:
        return ""

    # Group by top-level category
    categories: dict[str, list[tuple[str, str]]] = {}
    for member in incident_enum:
        value = str(member.value)
        parts = value.split("||")
        cat = parts[0]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append((value, _humanize(value)))

    lines = [
        "ALL NERIS INCIDENT TYPES (128 codes):",
        "Pick from this list. No tool call needed for incident type.",
    ]
    for cat, codes in categories.items():
        lines.append(f"\n{_humanize(cat)}:")
        lines.extend(f"  - {label}  ({value})" for value, label in codes)

    _neris_incident_types_cache = "\n".join(lines)
    return _neris_incident_types_cache


async def _fetch_context(
    incident_id: str,
    user: UserContext,
    *,
    snapshot: ContextSnapshot | None = None,
) -> tuple[str, str, str, str, str]:
    """Fetch incident, dispatch, crew, and personnel for the system prompt.

    When *snapshot* is provided, dispatch/crew/personnel are read from the
    cached strings instead of making external API calls.  The incident and
    attachments are always fetched fresh (they change when editors update).
    """
    from sjifire.ops.auth import set_current_user

    set_current_user(user)

    from sjifire.ops.incidents.store import IncidentStore

    # Get incident — ALWAYS fresh (changes after tool calls)
    incident_json = "{}"
    doc = None
    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)
    if doc:
        incident_json = json.dumps(doc.model_dump(mode="json"), default=str)

    # Dispatch, crew, personnel — use snapshot if available
    if snapshot is not None:
        dispatch_json = snapshot.dispatch_json
        crew_json = snapshot.crew_json
        personnel_json = snapshot.personnel_json
    else:
        from sjifire.ops.dispatch.store import DispatchStore
        from sjifire.ops.personnel import tools as personnel_tools
        from sjifire.ops.schedule import tools as schedule_tools

        if doc:
            # Fetch dispatch first (crew lookup needs the incident time)
            dispatch_json = "{}"
            incident_hour: int | None = None
            try:
                async with DispatchStore() as dstore:
                    dispatch = await dstore.get_by_dispatch_id(doc.incident_number)
                if dispatch:
                    # Slim dispatch: drop raw radio log (~8K chars). The enriched
                    # analysis.key_events has the condensed narrative instead.
                    # The agent has get_dispatch_call if it needs full details.
                    slim: dict = {
                        "id": dispatch.id,
                        "nature": dispatch.nature,
                        "address": dispatch.address,
                        "time_reported": dispatch.time_reported,
                        "geo_location": dispatch.geo_location,
                        "cad_comments": dispatch.cad_comments,
                    }
                    if dispatch.analysis:
                        analysis = dispatch.analysis
                        # Include analysis fields minus unit_times (formatted separately)
                        # and on_duty_crew (already in CREW_ON_DUTY section)
                        analysis_dict = analysis.model_dump(mode="json")
                        unit_times = analysis_dict.pop("unit_times", [])
                        analysis_dict.pop("on_duty_crew", None)
                        slim["analysis"] = analysis_dict
                        # Format unit_times as a readable table for easy review
                        if unit_times:
                            reported = dispatch.time_reported
                            tr = reported.isoformat() if reported else ""
                            at = analysis.alarm_time or ""
                            slim["unit_times_table"] = _format_unit_times_table(
                                unit_times, tr, alarm_time=at
                            )
                    dispatch_json = json.dumps(slim, default=str)
                    if dispatch.time_reported:
                        incident_hour = dispatch.time_reported.hour
            except Exception:
                logger.warning(
                    "Failed to fetch dispatch for %s", doc.incident_number, exc_info=True
                )

            # Fetch crew using the incident hour for shift-change awareness
            crew_json = "[]"
            try:
                crew_data = await schedule_tools.get_on_duty_crew(
                    target_date=doc.incident_datetime.date().isoformat(),
                    target_hour=incident_hour,
                )
                crew_json = json.dumps(crew_data, default=str)
            except Exception:
                logger.warning("Failed to fetch crew for %s", doc.incident_datetime, exc_info=True)
        else:
            dispatch_json = "{}"
            crew_json = "[]"

        # Fetch operational personnel for name matching (last name → full name + email)
        personnel_json = "[]"
        try:
            personnel = await personnel_tools.get_operational_personnel()
            personnel_json = json.dumps(personnel, default=str)
        except Exception:
            logger.warning("Failed to fetch personnel", exc_info=True)

    # Build a concise summary of attachments on file — ALWAYS fresh
    attachments_summary = ""
    if doc and doc.attachments:
        lines = []
        for a in doc.attachments:
            label = a.title or a.filename
            size_kb = a.size_bytes // 1024
            desc = f" — {a.description}" if a.description else ""
            lines.append(f"- {label} (id: {a.id}, {a.content_type}, {size_kb}KB){desc}")
        attachments_summary = "\n".join(lines)

    return incident_json, dispatch_json, crew_json, personnel_json, attachments_summary


async def run_chat(
    incident_id: str,
    user_message: str,
    user: UserContext,
    *,
    channel: str,
    images: list[dict] | None = None,
    image_refs: list[dict] | None = None,
) -> None:
    """Run a chat turn, publishing events to a Centrifugo channel.

    Event types published:
    - ``turn_start``: Turn begins (``{"user_email": "...", "user_name": "..."}``).
    - ``text``: Partial assistant text (``{"content": "..."}``).
    - ``tool_call``: Tool invocation (``{"name": "...", "input": {...}}``).
    - ``tool_result``: Tool result summary (``{"name": "...", "summary": "..."}``).
    - ``status_update``: Incident status change (``{"status": "...", ...}``).
    - ``done``: Conversation turn complete (``{"input_tokens": N, ..., "user_email": "..."}``).
    - ``error``: Error message (``{"message": "..."}``).
    """
    # Notify all subscribers that a turn is starting (multi-user awareness)
    await publish(
        channel,
        "turn_start",
        {"user_email": user.email, "user_name": user.name},
    )

    # Phase 1: budget + conversation (both fast Cosmos reads, ~50ms)
    async def _load_conversation():
        async with ConversationStore() as store:
            return await store.get_by_incident(incident_id)

    budget_result, conv_result = await asyncio.gather(
        check_budget(user.email),
        _load_conversation(),
        return_exceptions=True,
    )

    # Check phase-1 results for errors
    if isinstance(budget_result, Exception):
        await _release_turn_lock(incident_id, user.email)
        await publish(channel, "error", {"message": _user_error("budget", budget_result)})
        return
    if not budget_result.allowed:
        await _release_turn_lock(incident_id, user.email)
        await publish(channel, "error", {"message": budget_result.reason})
        return
    if isinstance(conv_result, Exception):
        await _release_turn_lock(incident_id, user.email)
        await publish(channel, "error", {"message": _user_error("conversation", conv_result)})
        return

    conversation = conv_result
    snapshot = conversation.context_snapshot if conversation else None
    logger.info(
        "Context snapshot: %s (conversation=%s)",
        "HIT" if snapshot else "MISS",
        "existing" if conversation else "new",
    )

    # Phase 2: context (fast if snapshot exists, full fetch otherwise)
    try:
        ctx_result = await _fetch_context(incident_id, user, snapshot=snapshot)
    except Exception as exc:
        ctx_result = exc

    if isinstance(ctx_result, Exception):
        await _release_turn_lock(incident_id, user.email)
        await publish(channel, "error", {"message": _user_error("context", ctx_result)})
        return

    (
        incident_json,
        dispatch_json,
        crew_json,
        personnel_json,
        attachments_summary,
    ) = ctx_result

    # Save snapshot after first full fetch (reused on subsequent turns)
    if snapshot is None:
        snapshot = ContextSnapshot(
            dispatch_json=dispatch_json,
            crew_json=crew_json,
            personnel_json=personnel_json,
        )

    # Everything below must release the turn lock on exit
    try:
        is_new = conversation is None
        if is_new:
            conversation = ConversationDocument(
                incident_id=incident_id,
                user_email=user.email,
            )

        # Attach snapshot so it's persisted with the conversation
        conversation.context_snapshot = snapshot

        # Turn limit
        if conversation.turn_count >= MAX_TURNS:
            await publish(
                channel,
                "error",
                {
                    "message": "This conversation has reached its limit. "
                    "Please start a new session to continue."
                },
            )
            return

        # System prompt: static + per-incident stable data (cached by Anthropic)
        system_prompt = _build_system_prompt(
            user.name, user.email, dispatch_json, crew_json, personnel_json
        )
        # Context preamble: only data that changes between turns
        context_preamble = _build_context_message(incident_json, attachments_summary)

        # Log context sizes for debugging token usage
        logger.info(
            "Context sizes (chars): system=%d (dispatch=%d crew=%d personnel=%d) "
            "preamble=%d (incident=%d attachments=%d)",
            len(system_prompt),
            len(dispatch_json),
            len(crew_json),
            len(personnel_json),
            len(context_preamble),
            len(incident_json),
            len(attachments_summary),
        )

        # Build messages for Claude API
        api_messages = _conversation_to_api_messages(conversation.messages)

        # Prepend fresh context to the user message so Claude always sees
        # the latest incident state without polluting the stable system prompt.
        prefixed_message = f"{context_preamble}\n\n---\n\n{user_message}"

        # Add the new user message (with optional image content blocks)
        if images:
            content_blocks: list[dict] = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img["media_type"],
                        "data": img["data"],
                    },
                }
                for img in images
            ]
            content_blocks.append({"type": "text", "text": prefixed_message})
            api_messages.append({"role": "user", "content": content_blocks})
        else:
            api_messages.append({"role": "user", "content": prefixed_message})
        api_messages = _trim_messages(api_messages)

        # Record user message with image references (attachment IDs for blob-backed display)
        conversation.messages.append(
            ConversationMessage(role="user", content=user_message, images=image_refs)
        )

        # Broadcast user message to other subscribers (multi-user awareness).
        # The sender already has this message locally — clients filter by email.
        await publish(
            channel,
            "user_message",
            {"content": user_message, "user_email": user.email, "user_name": user.name},
        )

        # Persist user message — must await create (so subsequent saves are
        # updates, not conflicts), but updates can run in background.
        async def _persist_user_msg():
            try:
                async with ConversationStore() as s:
                    if is_new:
                        await s.create(conversation)
                    else:
                        await s.update(conversation)
            except Exception as exc:
                logger.warning("Failed to persist user message: %s", exc)

        if is_new:
            await _persist_user_msg()
            is_new = False
        else:
            task = asyncio.create_task(_persist_user_msg())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        # Streaming loop (handles tool calls)
        total_input = 0
        total_output = 0

        client = get_client()

        stream_error = False
        try:
            await _run_loop(
                client, system_prompt, api_messages, conversation, user, channel=channel
            )

        except Exception as exc:
            await publish(channel, "error", {"message": _user_error("stream", exc)})
            stream_error = True

        # Calculate total tokens from this turn's messages
        turn_messages = [
            m
            for m in conversation.messages
            if m.role == "assistant" and (m.input_tokens > 0 or m.output_tokens > 0)
        ]
        for m in turn_messages[-5:]:  # last few are from this turn
            total_input += m.input_tokens
            total_output += m.output_tokens

        # Persist full turn (assistant response + token counts).
        # Runs even after stream errors so partial responses aren't lost.
        conversation.turn_count += 1
        conversation.total_input_tokens += total_input
        conversation.total_output_tokens += total_output
        conversation.updated_at = datetime.now(UTC)

        try:
            async with ConversationStore() as store:
                if is_new:
                    await store.create(conversation)
                else:
                    await store.update(conversation)
        except Exception as exc:
            await publish(channel, "error", {"message": _user_error("save", exc)})
            return

        if stream_error:
            return

        # Record budget usage
        try:
            if total_input > 0 or total_output > 0:
                await record_usage(user.email, total_input, total_output)
        except Exception as exc:
            logger.warning("Failed to record budget usage: %s", exc)

        await publish(
            channel,
            "done",
            {
                "input_tokens": total_input,
                "output_tokens": total_output,
                "user_email": user.email,
                "user_name": user.name,
            },
        )
    finally:
        # Always release the turn lock so other users can proceed
        await _release_turn_lock(incident_id, user.email)


async def _run_loop(
    client: AsyncAnthropic,
    system_prompt: str,
    api_messages: list[dict],
    conversation: ConversationDocument,
    user: UserContext,
    *,
    channel: str,
) -> None:
    """Run the Claude streaming loop, handling tool calls recursively."""
    max_tool_rounds = 10  # Safety limit on tool call loops

    for _ in range(max_tool_rounds):
        assistant_text = ""
        tool_calls: list[dict] = []
        input_tokens = 0
        output_tokens = 0

        # Retry loop for rate limit errors
        for attempt in range(RATE_LIMIT_MAX_RETRIES):
            try:
                async with client.messages.stream(
                    model=MODEL,
                    max_tokens=MAX_RESPONSE_TOKENS,
                    system=cached_system(system_prompt),
                    messages=api_messages,
                    tools=TOOL_SCHEMAS,
                ) as stream:
                    async for event in stream:
                        if (
                            event.type == "content_block_start"
                            and hasattr(event.content_block, "type")
                            and event.content_block.type == "tool_use"
                        ):
                            tool_calls.append(
                                {
                                    "type": "tool_use",
                                    "id": event.content_block.id,
                                    "name": event.content_block.name,
                                    "input": {},
                                }
                            )

                        elif event.type == "content_block_delta":
                            if hasattr(event.delta, "text"):
                                assistant_text += event.delta.text
                                await publish(channel, "text", {"content": event.delta.text})
                            elif hasattr(event.delta, "partial_json") and tool_calls:
                                tc = tool_calls[-1]
                                tc.setdefault("_partial", "")
                                tc["_partial"] += event.delta.partial_json

                        elif (
                            event.type == "content_block_stop"
                            and tool_calls
                            and "_partial" in tool_calls[-1]
                        ):
                            tc = tool_calls[-1]
                            try:
                                tc["input"] = json.loads(tc.pop("_partial"))
                            except json.JSONDecodeError:
                                tc.pop("_partial", None)

                    # Get usage from the final message
                    final_message = await stream.get_final_message()
                    if final_message.usage:
                        input_tokens = final_message.usage.input_tokens
                        output_tokens = final_message.usage.output_tokens
                        _log_cache_stats(final_message.usage)
                break  # Success — exit retry loop
            except RateLimitError:
                if attempt < RATE_LIMIT_MAX_RETRIES - 1:
                    delay = RATE_LIMIT_BASE_DELAY * (attempt + 1)
                    logger.warning(
                        "Rate limited (attempt %d/%d), waiting %ds",
                        attempt + 1,
                        RATE_LIMIT_MAX_RETRIES,
                        delay,
                    )
                    msg = f"\n\n*Rate limited — retrying in {delay}s...*\n\n"
                    await publish(channel, "text", {"content": msg})
                    await asyncio.sleep(delay)
                    # Reset state for retry
                    assistant_text = ""
                    tool_calls = []
                else:
                    raise  # Final attempt — let caller handle

        # Record assistant message
        conversation.messages.append(
            ConversationMessage(
                role="assistant",
                content=assistant_text,
                tool_use=tool_calls if tool_calls else None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )

        # If no tool calls, we're done
        if not tool_calls:
            return

        # Execute tool calls in parallel
        for tc in tool_calls:
            await publish(channel, "tool_call", {"name": tc["name"], "input": tc["input"]})

        result_strs = await asyncio.gather(
            *(execute_tool(tc["name"], tc["input"], user) for tc in tool_calls)
        )

        # Build full results for current API round and summaries for history
        full_tool_results: list[dict] = []
        summary_tool_results: list[dict] = []
        for tc, result_str in zip(tool_calls, result_strs, strict=True):
            try:
                result_data = json.loads(result_str)
                summary = _summarize_tool_result(tc["name"], result_data)
            except json.JSONDecodeError:
                summary = result_str[:200]

            is_error = summary.startswith("Error")
            evt: dict = {"name": tc["name"], "summary": summary, "is_error": is_error}
            # Include image URL and title so the chat UI can render inline thumbnails
            if tc["name"] == "get_attachment" and not is_error:
                aid = tc["input"].get("attachment_id", "")
                if aid:
                    evt["image_url"] = f"/reports/{conversation.incident_id}/attachments/{aid}"
                try:
                    rd = json.loads(result_str)
                    if rd.get("title"):
                        evt["image_title"] = rd["title"]
                    if rd.get("description"):
                        evt["image_desc"] = rd["description"]
                except (json.JSONDecodeError, KeyError):
                    pass  # Title/desc are optional UI enhancements; skip if unparseable
            await publish(channel, "tool_result", evt)

            # After reset_incident, clear pre-reset conversation history.
            # The reset deletes the Cosmos document; when the engine saves
            # via upsert it re-creates it.  By truncating here we ensure
            # only the post-reset exchange is persisted, giving a clean
            # slate on page reload while keeping current-turn context.
            # Re-add the current assistant message so subsequent tool_result
            # messages still have a matching tool_use block in the history.
            if tc["name"] == "reset_incident":
                try:
                    rd = json.loads(result_str)
                    if isinstance(rd, dict) and "error" not in rd:
                        # Snapshot the current assistant message before clearing
                        current_assistant_msg = (
                            conversation.messages[-1] if conversation.messages else None
                        )
                        conversation.messages.clear()
                        conversation.context_snapshot = None
                        conversation.turn_count = 0
                        conversation.total_input_tokens = 0
                        conversation.total_output_tokens = 0
                        # Re-add so tool_results have a matching tool_use.
                        # Strip text content — it was already streamed to the
                        # client and would appear twice on history reload.
                        if current_assistant_msg and current_assistant_msg.role == "assistant":
                            stripped = current_assistant_msg.model_copy(update={"content": ""})
                            conversation.messages.append(stripped)
                        logger.info("Cleared conversation history after reset_incident")
                except (json.JSONDecodeError, KeyError):
                    pass

            # After update_incident, emit live status update for the client
            if tc["name"] == "update_incident":
                try:
                    result_data_raw = json.loads(result_str)
                    if "error" not in result_data_raw:
                        from sjifire.ops.incidents.models import IncidentDocument

                        doc = IncidentDocument.from_cosmos(result_data_raw)
                        await publish(
                            channel,
                            "status_update",
                            {
                                "status": doc.status,
                                "completeness": doc.completeness(),
                            },
                        )
                except Exception:
                    logger.debug("Failed to emit status_update", exc_info=True)

            # Build the full tool result for the current API round.
            # For get_attachment with image_data, use a multi-block content
            # array so Claude can see the image via vision.
            tool_result_content: str | list[dict] = result_str
            try:
                result_parsed = json.loads(result_str)
                image_info = result_parsed.get("image_data")
                if image_info and isinstance(image_info, dict):
                    slim = {k: v for k, v in result_parsed.items() if k != "image_data"}
                    tool_result_content = [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": image_info["media_type"],
                                "data": image_info["base64"],
                            },
                        },
                        {"type": "text", "text": json.dumps(slim, default=str)},
                    ]
            except (json.JSONDecodeError, KeyError):
                pass  # Non-image tool result — use raw string as-is

            full_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": tool_result_content,
                }
            )
            summary_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": summary,
                }
            )

        # Store summaries in conversation history (future turns get slim results)
        conversation.messages.append(
            ConversationMessage(
                role="user",
                content="",
                tool_results=summary_tool_results,
            )
        )

        # Add to API messages for next round
        # Assistant message with tool use blocks (strip internal keys)
        assistant_content: list[dict] = []
        if assistant_text:
            assistant_content.append({"type": "text", "text": assistant_text})
        assistant_content.extend(
            {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]}
            for tc in tool_calls
        )
        api_messages.append({"role": "assistant", "content": assistant_content})

        # Current turn uses full results for accurate tool-use reasoning
        api_messages.append({"role": "user", "content": full_tool_results})

    # If we exhausted tool rounds, notify the user
    logger.warning("Chat hit max tool rounds for incident %s", conversation.incident_id)
    await publish(
        channel,
        "text",
        {"content": "\n\n*Tool call limit reached. Send another message to continue.*\n\n"},
    )


def _summarize_tool_result(name: str, data: dict) -> str:
    """Create a brief summary of a tool result for the chat UI."""
    if "error" in data:
        return f"Error: {data['error']}"

    if name == "get_incident":
        num = data.get("incident_number", "")
        status = data.get("status", "")
        return f"Incident {num} ({status})"

    if name == "update_incident":
        num = data.get("incident_number", "")
        return f"Updated incident {num}"

    if name == "reset_incident":
        num = data.get("incident_number", "")
        return f"Reset incident {num}"

    if name == "get_dispatch_call":
        nature = data.get("nature", "")
        addr = data.get("address", "")
        return f"{nature} at {addr}"

    if name == "search_dispatch_calls":
        count = data.get("count", 0)
        return f"Found {count} call(s)"

    if name == "get_on_duty_crew":
        count = data.get("count", 0)
        platoon = data.get("platoon", "")
        return f"{count} crew on duty ({platoon})"

    if name == "get_neris_values":
        count = data.get("count", 0)
        vs = data.get("value_set", "")
        return f"{count} values for {vs}"

    if name == "list_dispatch_calls":
        count = data.get("count", 0)
        return f"{count} dispatch call(s)"

    if name == "get_personnel":
        count = data.get("count", 0)
        return f"{count} personnel"

    if name == "list_incidents":
        incidents = data if isinstance(data, list) else data.get("incidents", [])
        return f"{len(incidents)} incident(s)"

    if name == "lookup_location":
        road = data.get("road", "")
        cross = data.get("cross_streets", [])
        if cross:
            names = [c["name"] if isinstance(c, dict) else c for c in cross[:3]]
            return f"{road} near {', '.join(names)}"
        return f"{road} (no cross streets found)"

    if name == "list_attachments":
        count = data.get("count", 0)
        return f"{count} attachment(s)"

    if name == "get_attachment":
        title = data.get("title") or data.get("filename", "")
        return f"Attachment: {title}"

    if name == "update_attachment":
        title = data.get("title", "")
        return f"Labeled: {title}" if title else "Attachment updated"

    if name == "delete_attachment":
        fname = data.get("filename", "")
        return f"Deleted: {fname}"

    return json.dumps(data, default=str)[:200]


# ---------------------------------------------------------------------------
# General chat assistant (not tied to a specific incident)
# ---------------------------------------------------------------------------

_GENERAL_CONVERSATION_PREFIX = "general:"


def _build_general_system_prompt(context: dict | None = None) -> str:
    """Build the system prompt for the general operations assistant."""
    org = get_org_config()
    now = local_now()

    prompt = _load_doc("general-chat-prompt.md").format(
        company_name=org.company_name,
        today=now.strftime("%A, %B %d, %Y"),
        time=now.strftime("%H:%M"),
        timezone=org.timezone,
    )
    parts = [prompt]

    if context and context.get("calls"):
        calls = context["calls"]
        rows = ["ID | Date | Time | Nature | Address | IC | Report"]
        rows.append("---|------|------|--------|---------|----|---------")
        for c in calls:
            status = c.get("report_status") or c.get("report_source") or "--"
            rows.append(
                f"{c.get('id', '')} | {c.get('date', '')} | {c.get('time', '')} | "
                f"{c.get('nature', '')} | {c.get('address', '')} | "
                f"{c.get('ic', '')} | {status}"
            )
        parts.append("\nPAGE CONTEXT — Dispatch calls visible to the user:\n" + "\n".join(rows))

    return "\n".join(parts)


async def run_general_chat(
    user_message: str,
    user: UserContext,
    *,
    channel: str,
    context: dict | None = None,
) -> None:
    """Run a general chat turn, publishing events to a Centrifugo channel.

    Like ``run_chat()`` but not scoped to a specific incident.
    Uses a ``general:{email}`` conversation key for persistence.
    """
    conversation_key = f"{_GENERAL_CONVERSATION_PREFIX}{user.email}"

    # Run budget check and conversation load in parallel
    async def _load_conv():
        async with ConversationStore() as store:
            return await store.get_by_incident(conversation_key)

    budget_result, conv_result = await asyncio.gather(
        check_budget(user.email),
        _load_conv(),
        return_exceptions=True,
    )

    if isinstance(budget_result, Exception):
        await publish(channel, "error", {"message": _user_error("budget", budget_result)})
        return
    if not budget_result.allowed:
        await publish(channel, "error", {"message": budget_result.reason})
        return
    if isinstance(conv_result, Exception):
        await publish(channel, "error", {"message": _user_error("conversation", conv_result)})
        return

    conversation = conv_result

    is_new = conversation is None
    if is_new:
        conversation = ConversationDocument(
            incident_id=conversation_key,
            user_email=user.email,
        )

    if conversation.turn_count >= MAX_TURNS:
        await publish(
            channel,
            "error",
            {
                "message": "This conversation has reached its limit. "
                "Please refresh to start a new session."
            },
        )
        return

    system_prompt = _build_general_system_prompt(context)

    # Build messages for Claude API
    api_messages = _conversation_to_api_messages(conversation.messages)

    api_messages.append({"role": "user", "content": user_message})
    api_messages = _trim_messages(api_messages)

    conversation.messages.append(ConversationMessage(role="user", content=user_message))

    # Persist user message — must await create (so subsequent saves are
    # updates, not conflicts), but updates can run in background.
    async def _persist_user_msg():
        try:
            async with ConversationStore() as s:
                if is_new:
                    await s.create(conversation)
                else:
                    await s.update(conversation)
        except Exception as exc:
            logger.warning("Failed to persist user message: %s", exc)

    if is_new:
        await _persist_user_msg()
        is_new = False
    else:
        task = asyncio.create_task(_persist_user_msg())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    total_input = 0
    total_output = 0

    client = get_client()

    stream_error = False
    try:
        await _run_general_loop(
            client, system_prompt, api_messages, conversation, user, channel=channel
        )

    except Exception as exc:
        await publish(channel, "error", {"message": _user_error("stream", exc)})
        stream_error = True

    turn_messages = [
        m
        for m in conversation.messages
        if m.role == "assistant" and (m.input_tokens > 0 or m.output_tokens > 0)
    ]
    for m in turn_messages[-5:]:
        total_input += m.input_tokens
        total_output += m.output_tokens

    conversation.turn_count += 1
    conversation.total_input_tokens += total_input
    conversation.total_output_tokens += total_output
    conversation.updated_at = datetime.now(UTC)

    try:
        async with ConversationStore() as store:
            if is_new:
                await store.create(conversation)
            else:
                await store.update(conversation)
    except Exception as exc:
        await publish(channel, "error", {"message": _user_error("save", exc)})
        return

    if stream_error:
        return

    if total_input > 0 or total_output > 0:
        await record_usage(user.email, total_input, total_output)

    await publish(channel, "done", {"input_tokens": total_input, "output_tokens": total_output})


async def _run_general_loop(
    client: AsyncAnthropic,
    system_prompt: str,
    api_messages: list[dict],
    conversation: ConversationDocument,
    user: UserContext,
    *,
    channel: str,
) -> None:
    """Run the Claude streaming loop for general assistant, handling tool calls."""
    max_tool_rounds = 10

    for _ in range(max_tool_rounds):
        assistant_text = ""
        tool_calls: list[dict] = []
        input_tokens = 0
        output_tokens = 0

        # Retry loop for rate limit errors
        for attempt in range(RATE_LIMIT_MAX_RETRIES):
            try:
                async with client.messages.stream(
                    model=MODEL,
                    max_tokens=MAX_RESPONSE_TOKENS,
                    system=cached_system(system_prompt),
                    messages=api_messages,
                    tools=GENERAL_TOOL_SCHEMAS,
                ) as stream:
                    async for event in stream:
                        if (
                            event.type == "content_block_start"
                            and hasattr(event.content_block, "type")
                            and event.content_block.type == "tool_use"
                        ):
                            tool_calls.append(
                                {
                                    "type": "tool_use",
                                    "id": event.content_block.id,
                                    "name": event.content_block.name,
                                    "input": {},
                                }
                            )

                        elif event.type == "content_block_delta":
                            if hasattr(event.delta, "text"):
                                assistant_text += event.delta.text
                                await publish(channel, "text", {"content": event.delta.text})
                            elif hasattr(event.delta, "partial_json") and tool_calls:
                                tc = tool_calls[-1]
                                tc.setdefault("_partial", "")
                                tc["_partial"] += event.delta.partial_json

                        elif (
                            event.type == "content_block_stop"
                            and tool_calls
                            and "_partial" in tool_calls[-1]
                        ):
                            tc = tool_calls[-1]
                            try:
                                tc["input"] = json.loads(tc.pop("_partial"))
                            except json.JSONDecodeError:
                                tc.pop("_partial", None)

                    final_message = await stream.get_final_message()
                    if final_message.usage:
                        input_tokens = final_message.usage.input_tokens
                        output_tokens = final_message.usage.output_tokens
                        _log_cache_stats(final_message.usage)
                break  # Success — exit retry loop
            except RateLimitError:
                if attempt < RATE_LIMIT_MAX_RETRIES - 1:
                    delay = RATE_LIMIT_BASE_DELAY * (attempt + 1)
                    logger.warning(
                        "General chat rate limited (attempt %d/%d), waiting %ds",
                        attempt + 1,
                        RATE_LIMIT_MAX_RETRIES,
                        delay,
                    )
                    msg = f"\n\n*Rate limited — retrying in {delay}s...*\n\n"
                    await publish(channel, "text", {"content": msg})
                    await asyncio.sleep(delay)
                    assistant_text = ""
                    tool_calls = []
                else:
                    raise

        conversation.messages.append(
            ConversationMessage(
                role="assistant",
                content=assistant_text,
                tool_use=tool_calls if tool_calls else None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )

        if not tool_calls:
            return

        for tc in tool_calls:
            await publish(channel, "tool_call", {"name": tc["name"], "input": tc["input"]})

        result_strs = await asyncio.gather(
            *(execute_general_tool(tc["name"], tc["input"], user) for tc in tool_calls)
        )

        full_tool_results: list[dict] = []
        summary_tool_results: list[dict] = []
        for tc, result_str in zip(tool_calls, result_strs, strict=True):
            try:
                result_data = json.loads(result_str)
                summary = _summarize_tool_result(tc["name"], result_data)
            except json.JSONDecodeError:
                summary = result_str[:200]

            is_error = summary.startswith("Error")
            evt = {"name": tc["name"], "summary": summary, "is_error": is_error}
            await publish(channel, "tool_result", evt)

            full_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result_str,
                }
            )
            summary_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": summary,
                }
            )

        # Store summaries in conversation history (future turns get slim results)
        conversation.messages.append(
            ConversationMessage(
                role="user",
                content="",
                tool_results=summary_tool_results,
            )
        )

        assistant_content: list[dict] = []
        if assistant_text:
            assistant_content.append({"type": "text", "text": assistant_text})
        assistant_content.extend(
            {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]}
            for tc in tool_calls
        )
        api_messages.append({"role": "assistant", "content": assistant_content})
        # Current turn uses full results for accurate tool-use reasoning
        api_messages.append({"role": "user", "content": full_tool_results})

    logger.warning("General chat hit max tool rounds for %s", user.email)
    await publish(
        channel,
        "text",
        {"content": "\n\n*Tool call limit reached. Send another message to continue.*\n\n"},
    )


def _log_cache_stats(usage: object) -> None:
    """Log prompt cache performance metrics if available."""
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
    uncached = input_tokens - cache_read - cache_create
    # Always log token counts so we can see context growth
    logger.info(
        "Tokens: %d in (%d cached, %d created, %d uncached), %d out",
        input_tokens,
        cache_read,
        cache_create,
        uncached,
        output_tokens,
    )
