"""Chat engine: Claude API streaming with tool-use loop.

Core function: ``stream_chat()`` takes a user message and yields SSE events
as an async generator. Handles tool calls, budget tracking, and conversation
persistence in Cosmos DB.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

from anthropic import AsyncAnthropic, RateLimitError

from sjifire.core.anthropic import MODEL, cached_system, get_client
from sjifire.core.config import get_org_config, local_now
from sjifire.ops.auth import UserContext
from sjifire.ops.chat.budget import check_budget, record_usage
from sjifire.ops.chat.models import MAX_TURNS, ConversationDocument, ConversationMessage
from sjifire.ops.chat.store import ConversationStore
from sjifire.ops.chat.tools import (
    GENERAL_TOOL_SCHEMAS,
    TOOL_SCHEMAS,
    execute_general_tool,
    execute_tool,
)

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


def _user_error(category: str, exc: Exception) -> str:
    """Build a user-friendly error message with a reference ID for debugging.

    Logs the full exception; returns only a short message + error ID.
    """
    error_id = uuid.uuid4().hex[:8]
    logger.error("Chat error [%s] %s: %s: %s", error_id, category, type(exc).__name__, exc)
    friendly = _ERROR_MESSAGES.get(category, "Something went wrong.")
    return f"{friendly} (ref: {error_id})"


def _get_instructions() -> str:
    """Load incident report instructions for the system prompt."""
    path = _DOCS_DIR / "neris" / "incident-report-instructions.md"
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        logger.warning("Incident report instructions not found: %s", path)
        return ""


def _get_neris_cheat_sheet() -> str:
    """Load the NERIS cheat sheet from docs."""
    path = _DOCS_DIR / "neris" / "neris-cheat-sheet.md"
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        logger.warning("NERIS cheat sheet not found: %s", path)
        return ""


def _build_system_prompt(
    incident_json: str,
    dispatch_json: str,
    crew_json: str,
    personnel_json: str,
    user_name: str,
    user_email: str,
) -> str:
    """Build the scoped system prompt for Claude."""
    org = get_org_config()
    instructions = _get_instructions()

    role = (
        f"You are an incident report assistant for {org.company_name}. "
        "Your ONLY purpose is to help firefighters complete NERIS "
        "incident reports accurately and efficiently."
    )

    rules = """\
RULES:
- You MUST stay focused on incident reporting. Do not engage in \
general conversation, answer trivia, write code, tell stories, \
or help with tasks unrelated to this incident report.
- If the user asks about something unrelated, briefly redirect: \
"I'm here to help with your incident report. Let's continue."
- Be concise. Ask one question at a time.
- Format data readably: use bullet points or line breaks for \
lists (crew members, units, timestamps). Never dump everything \
in a single paragraph.
- NERIS CODES: All 128 incident type codes are listed below — pick \
from that list directly, no tool call needed. Present the \
human-readable label (e.g. "Chimney Fire") with your reasoning. \
If the user wants a different option, show others from the same \
category. For OTHER NERIS fields (location_use, action_tactic, \
etc.), call get_neris_values. NEVER invent a NERIS code. Do not \
guess at addresses or timestamps.
- IMPORTANT: If you ask the user a question or present a choice \
for confirmation, WAIT for their response before saving. Do NOT \
call update_incident in the same turn as asking a question. \
Only save after the user confirms or provides their answer.
- For fields that are unambiguous from dispatch data (address, \
timestamps, responding units), save immediately without asking.
- After each save, confirm what was saved and move to the \
next section.
- The person writing this report is {user_name} ({user_email}). \
Cross-reference their name against the dispatch data \
(incident_commander_name, responding_units) and crew roster \
(position, section) to infer their likely role. If the dispatch \
data shows them as IC, say "It looks like you were IC on this \
call — is that right?" If the crew roster shows their position \
(e.g. Captain on E31), use that context. Only ask their role \
from scratch if you truly cannot determine it from the data.
- CREW NAME MATCHING: When dispatch data or user input contains \
last names only (e.g. "Stanger, See, Vos"), use the PERSONNEL \
ROSTER below to resolve each last name to a full name and email. \
Never ask the user for first names if you can match from the roster. \
If a name is not in the roster, call get_personnel for a wider search.
- DISPATCH LOG: iSpyFire calls it the "radio log" but it is the \
dispatch log. We do not have audio recordings. When the user asks \
for the "radio log", "dispatch log", or "CAD notes", show ALL \
entries from the dispatch data in chronological order: CAD comments, \
responder status changes (dispatched, enroute, on scene, clear), \
and any notes — everything with a timestamp. Use get_dispatch_call \
if the full details aren't in the context. Present it as a clean \
timeline. Don't say you lack access — this IS the dispatch log."""

    workflow = """\
WORKFLOW:
1. Review the dispatch data and crew roster provided below.
2. Walk through each section, pre-filling from dispatch data.
3. For unambiguous fields (address, timestamps), save immediately.
4. For fields requiring judgment (incident type, narratives), \
present your best guess and WAIT for the user to confirm \
before saving.
5. Save each confirmed section as you go.
6. When all required fields are complete, set status to \
"ready_review"."""

    sections = [
        role,
        rules.format(user_name=user_name, user_email=user_email),
        workflow,
        instructions,
        f"REPORT AUTHOR: {user_name} ({user_email})",
        f"CURRENT INCIDENT STATE:\n{incident_json}",
        f"DISPATCH DATA:\n{dispatch_json}",
        f"CREW ON DUTY:\n{crew_json}",
        "PERSONNEL ROSTER (use to match last names to full names + emails):\n" + personnel_json,
        _get_all_neris_incident_types(),
    ]
    return "\n\n".join(sections)


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
       Maps to ``update_incident(unit_responses=[...])``.

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

    # --- Per-unit times (→ update_incident unit_responses=[]) ---
    lines.append("")
    lines.append("UNIT RESPONSE TIMES (save via unit_responses=[...]):")
    lines.append("(-- = missing, needs to be filled in or confirmed N/A)")
    header = "Unit     | Dispatched | Enroute  | On Scene | Cleared  | In Quarters"
    divider = "---------|------------|----------|----------|----------|------------"
    lines.extend([header, divider])
    for ut in unit_times:
        unit = (ut.get("unit") or "?").ljust(8)
        paged = _time(ut.get("paged") or default_dispatched).ljust(10)
        enroute = _time(ut.get("enroute", "")).ljust(8)
        arrived = _time(ut.get("arrived", "")).ljust(8)
        completed = _time(ut.get("completed", "")).ljust(8)
        in_quarters = _time(ut.get("in_quarters", ""))
        lines.append(f"{unit} | {paged} | {enroute} | {arrived} | {completed} | {in_quarters}")

    return "\n".join(lines)


def _trim_messages(messages: list[dict]) -> list[dict]:
    """Keep only the last MAX_CONTEXT_MESSAGES turns to stay under token limits."""
    if len(messages) <= MAX_CONTEXT_MESSAGES * 2:
        return messages
    return messages[-(MAX_CONTEXT_MESSAGES * 2) :]


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


async def _fetch_context(incident_id: str, user: UserContext) -> tuple[str, str, str, str]:
    """Fetch incident, dispatch, crew, and personnel for the system prompt."""
    from sjifire.ops.auth import set_current_user

    set_current_user(user)

    from sjifire.ops.dispatch.store import DispatchStore
    from sjifire.ops.incidents.store import IncidentStore
    from sjifire.ops.personnel import tools as personnel_tools
    from sjifire.ops.schedule import tools as schedule_tools

    # Get incident (must be first — dispatch and crew depend on it)
    incident_json = "{}"
    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)
    if doc:
        incident_json = json.dumps(doc.model_dump(mode="json"), indent=2, default=str)

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
                    analysis_dict = analysis.model_dump(mode="json")
                    unit_times = analysis_dict.pop("unit_times", [])
                    slim["analysis"] = analysis_dict
                    # Format unit_times as a readable table for easy review
                    if unit_times:
                        tr = dispatch.time_reported.isoformat() if dispatch.time_reported else ""
                        at = analysis.alarm_time or ""
                        slim["unit_times_table"] = _format_unit_times_table(
                            unit_times, tr, alarm_time=at
                        )
                dispatch_json = json.dumps(slim, indent=2, default=str)
                if dispatch.time_reported:
                    incident_hour = dispatch.time_reported.hour
        except Exception:
            logger.warning("Failed to fetch dispatch for %s", doc.incident_number, exc_info=True)

        # Fetch crew using the incident hour for shift-change awareness
        crew_json = "[]"
        try:
            crew_data = await schedule_tools.get_on_duty_crew(
                target_date=doc.incident_date.isoformat(),
                target_hour=incident_hour,
            )
            crew_json = json.dumps(crew_data, indent=2, default=str)
        except Exception:
            logger.warning("Failed to fetch crew for %s", doc.incident_date, exc_info=True)
    else:
        dispatch_json = "{}"
        crew_json = "[]"

    # Fetch operational personnel for name matching (last name → full name + email)
    personnel_json = "[]"
    try:
        personnel = await personnel_tools.get_operational_personnel()
        personnel_json = json.dumps(personnel, indent=2, default=str)
    except Exception:
        logger.warning("Failed to fetch personnel", exc_info=True)

    return incident_json, dispatch_json, crew_json, personnel_json


async def stream_chat(
    incident_id: str,
    user_message: str,
    user: UserContext,
    *,
    images: list[dict] | None = None,
) -> AsyncGenerator[str]:
    r"""Stream a chat response as Server-Sent Events.

    Yields SSE-formatted strings (``event: type\ndata: json\n\n``).

    Event types:
    - ``text``: Partial assistant text (``{"content": "..."}``).
    - ``tool_call``: Tool invocation (``{"name": "...", "input": {...}}``).
    - ``tool_result``: Tool result summary (``{"name": "...", "summary": "..."}``).
    - ``done``: Conversation turn complete (``{"input_tokens": N, "output_tokens": N}``).
    - ``error``: Error message (``{"message": "..."}``).
    """
    # Budget check
    try:
        budget_status = await check_budget(user.email)
    except Exception as exc:
        yield _sse("error", {"message": _user_error("budget", exc)})
        return
    if not budget_status.allowed:
        yield _sse("error", {"message": budget_status.reason})
        return

    # Load or create conversation
    try:
        async with ConversationStore() as store:
            conversation = await store.get_by_incident(incident_id)
    except Exception as exc:
        yield _sse("error", {"message": _user_error("conversation", exc)})
        return

    is_new = conversation is None
    if is_new:
        conversation = ConversationDocument(
            incident_id=incident_id,
            user_email=user.email,
        )

    # Turn limit
    if conversation.turn_count >= MAX_TURNS:
        yield _sse(
            "error",
            {
                "message": "This conversation has reached its limit. "
                "Please start a new session to continue."
            },
        )
        return

    # Build system prompt with context
    try:
        incident_json, dispatch_json, crew_json, personnel_json = await _fetch_context(
            incident_id, user
        )
    except Exception as exc:
        yield _sse("error", {"message": _user_error("context", exc)})
        return
    system_prompt = _build_system_prompt(
        incident_json, dispatch_json, crew_json, personnel_json, user.name, user.email
    )

    # Build messages for Claude API
    api_messages = []
    for msg in conversation.messages:
        entry: dict = {"role": msg.role, "content": []}
        if msg.content:
            entry["content"].append({"type": "text", "text": msg.content})
        if msg.tool_use:
            entry["content"].extend(msg.tool_use)
        if msg.tool_results:
            entry["role"] = "user"
            entry["content"] = msg.tool_results
        if not entry["content"]:
            entry["content"] = msg.content or ""
        api_messages.append(entry)

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
        content_blocks.append({"type": "text", "text": user_message})
        api_messages.append({"role": "user", "content": content_blocks})
    else:
        api_messages.append({"role": "user", "content": user_message})
    api_messages = _trim_messages(api_messages)

    # Record user message (text only — images are one-shot, not stored)
    conversation.messages.append(ConversationMessage(role="user", content=user_message))

    # Streaming loop (handles tool calls)
    total_input = 0
    total_output = 0

    client = get_client()

    try:
        async for event_str in _stream_loop(
            client, system_prompt, api_messages, conversation, user
        ):
            # Parse the event to track tokens
            yield event_str

    except Exception as exc:
        yield _sse("error", {"message": _user_error("stream", exc)})
        return

    # Calculate total tokens from this turn's messages
    turn_messages = [
        m
        for m in conversation.messages
        if m.role == "assistant" and (m.input_tokens > 0 or m.output_tokens > 0)
    ]
    for m in turn_messages[-5:]:  # last few are from this turn
        total_input += m.input_tokens
        total_output += m.output_tokens

    # Persist conversation
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
        yield _sse("error", {"message": _user_error("save", exc)})
        return

    # Record budget usage
    try:
        if total_input > 0 or total_output > 0:
            await record_usage(user.email, total_input, total_output)
    except Exception as exc:
        logger.warning("Failed to record budget usage: %s", exc)

    yield _sse("done", {"input_tokens": total_input, "output_tokens": total_output})


async def _stream_loop(
    client: AsyncAnthropic,
    system_prompt: str,
    api_messages: list[dict],
    conversation: ConversationDocument,
    user: UserContext,
) -> AsyncGenerator[str]:
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
                                yield _sse("text", {"content": event.delta.text})
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
                    yield _sse("text", {"content": msg})
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
            yield _sse("tool_call", {"name": tc["name"], "input": tc["input"]})

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
            evt = {"name": tc["name"], "summary": summary, "is_error": is_error}
            yield _sse("tool_result", evt)

            # After update_incident, emit live status update for the client
            if tc["name"] == "update_incident":
                try:
                    result_data_raw = json.loads(result_str)
                    if "error" not in result_data_raw:
                        from sjifire.ops.incidents.models import IncidentDocument

                        doc = IncidentDocument.from_cosmos(result_data_raw)
                        yield _sse(
                            "status_update",
                            {
                                "status": doc.status,
                                "completeness": doc.completeness(),
                            },
                        )
                except Exception:
                    logger.debug("Failed to emit status_update", exc_info=True)

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

    # If we exhausted tool rounds
    logger.warning("Chat hit max tool rounds for incident %s", conversation.incident_id)


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

    return json.dumps(data, default=str)[:200]


def _sse(event: str, data: dict) -> str:
    """Format a Server-Sent Event string."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# ---------------------------------------------------------------------------
# General chat assistant (not tied to a specific incident)
# ---------------------------------------------------------------------------

_GENERAL_CONVERSATION_PREFIX = "general:"


def _build_general_system_prompt(context: dict | None = None) -> str:
    """Build the system prompt for the general operations assistant."""
    org = get_org_config()
    now = local_now()

    parts = [
        f"You are an operations assistant for {org.company_name}. "
        "You help firefighters look up dispatch calls, crew schedules, "
        "NERIS reporting codes, and incident report status.\n\n"
        f"TODAY: {now.strftime('%A, %B %d, %Y')}  "
        f"TIME: {now.strftime('%H:%M')} ({org.timezone})\n\n"
        "RULES:\n"
        "- Be concise and helpful.\n"
        "- Use tools to look up data — don't guess.\n"
        "- If someone wants to edit an incident report, tell them to "
        'click "Edit Report" on the reports table for that call.\n'
        "- You can answer questions about schedules, call history, "
        "NERIS codes, and report status.\n"
        "- Format responses using markdown for readability.\n"
        "- When the user asks about calls visible on the page, use the "
        "PAGE CONTEXT below before calling tools.",
    ]

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


async def stream_general_chat(
    user_message: str,
    user: UserContext,
    context: dict | None = None,
) -> AsyncGenerator[str]:
    r"""Stream a general chat response as Server-Sent Events.

    Like ``stream_chat()`` but not scoped to a specific incident.
    Uses a ``general:{email}`` conversation key for persistence.
    """
    budget_status = await check_budget(user.email)
    if not budget_status.allowed:
        yield _sse("error", {"message": budget_status.reason})
        return

    conversation_key = f"{_GENERAL_CONVERSATION_PREFIX}{user.email}"

    async with ConversationStore() as store:
        conversation = await store.get_by_incident(conversation_key)

    is_new = conversation is None
    if is_new:
        conversation = ConversationDocument(
            incident_id=conversation_key,
            user_email=user.email,
        )

    if conversation.turn_count >= MAX_TURNS:
        yield _sse(
            "error",
            {
                "message": "This conversation has reached its limit. "
                "Please refresh to start a new session."
            },
        )
        return

    system_prompt = _build_general_system_prompt(context)

    # Build messages for Claude API
    api_messages = []
    for msg in conversation.messages:
        entry: dict = {"role": msg.role, "content": []}
        if msg.content:
            entry["content"].append({"type": "text", "text": msg.content})
        if msg.tool_use:
            entry["content"].extend(msg.tool_use)
        if msg.tool_results:
            entry["role"] = "user"
            entry["content"] = msg.tool_results
        if not entry["content"]:
            entry["content"] = msg.content or ""
        api_messages.append(entry)

    api_messages.append({"role": "user", "content": user_message})
    api_messages = _trim_messages(api_messages)

    conversation.messages.append(ConversationMessage(role="user", content=user_message))

    total_input = 0
    total_output = 0

    client = get_client()

    try:
        async for event_str in _stream_general_loop(
            client, system_prompt, api_messages, conversation, user
        ):
            yield event_str

    except Exception as exc:
        yield _sse("error", {"message": _user_error("stream", exc)})
        return

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

    async with ConversationStore() as store:
        if is_new:
            await store.create(conversation)
        else:
            await store.update(conversation)

    if total_input > 0 or total_output > 0:
        await record_usage(user.email, total_input, total_output)

    yield _sse("done", {"input_tokens": total_input, "output_tokens": total_output})


async def _stream_general_loop(
    client: AsyncAnthropic,
    system_prompt: str,
    api_messages: list[dict],
    conversation: ConversationDocument,
    user: UserContext,
) -> AsyncGenerator[str]:
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
                                yield _sse("text", {"content": event.delta.text})
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
                    yield _sse("text", {"content": msg})
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
            yield _sse("tool_call", {"name": tc["name"], "input": tc["input"]})

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
            yield _sse("tool_result", evt)

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


def _log_cache_stats(usage: object) -> None:
    """Log prompt cache performance metrics if available."""
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
    if cache_read or cache_create:
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        logger.info(
            "Cache: %d read, %d created, %d uncached (of %d total input)",
            cache_read,
            cache_create,
            input_tokens - cache_read - cache_create,
            input_tokens,
        )
