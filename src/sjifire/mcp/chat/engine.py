"""Chat engine: Claude API streaming with tool-use loop.

Core function: ``stream_chat()`` takes a user message and yields SSE events
as an async generator. Handles tool calls, budget tracking, and conversation
persistence in Cosmos DB.
"""

import json
import logging
import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

from anthropic import AsyncAnthropic

from sjifire.core.config import get_org_config
from sjifire.mcp.auth import UserContext
from sjifire.mcp.chat.budget import check_budget, record_usage
from sjifire.mcp.chat.models import MAX_TURNS, ConversationDocument, ConversationMessage
from sjifire.mcp.chat.store import ConversationStore
from sjifire.mcp.chat.tools import TOOL_SCHEMAS, execute_tool

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-20250514"
MAX_RESPONSE_TOKENS = 1024
MAX_CONTEXT_MESSAGES = 20  # Keep last N turns to stay under token limits

# Path to incident report instructions (system prompt context)
_SRC_DOCS = Path(__file__).resolve().parents[4] / "docs"
_APP_DOCS = Path("/app/docs")
_DOCS_DIR = _SRC_DOCS if _SRC_DOCS.is_dir() else _APP_DOCS


def _get_instructions() -> str:
    """Load incident report instructions for the system prompt."""
    path = _DOCS_DIR / "neris" / "incident-report-instructions.md"
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        logger.warning("Incident report instructions not found: %s", path)
        return ""


def _build_system_prompt(
    incident_json: str,
    dispatch_json: str,
    crew_json: str,
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
- Be concise. Ask one question at a time. Present what you \
already know before asking.
- Use the tools to look up data — never guess at NERIS codes, \
addresses, or timestamps.
- When you have enough information for a field, save it \
immediately with update_incident. Don't batch updates.
- After each save, confirm what was saved and move to the \
next section."""

    workflow = """\
WORKFLOW:
1. Review the dispatch data and crew roster provided below.
2. Walk through each section, pre-filling from dispatch data.
3. Ask the user only for information that can't be auto-filled.
4. Save each section as you go.
5. When all required fields are complete, set status to \
"ready_review"."""

    return "\n\n".join(
        [
            role,
            rules,
            workflow,
            instructions,
            f"CURRENT INCIDENT STATE:\n{incident_json}",
            f"DISPATCH DATA:\n{dispatch_json}",
            f"CREW ON DUTY:\n{crew_json}",
        ]
    )


def _trim_messages(messages: list[dict]) -> list[dict]:
    """Keep only the last MAX_CONTEXT_MESSAGES turns to stay under token limits."""
    if len(messages) <= MAX_CONTEXT_MESSAGES * 2:
        return messages
    return messages[-(MAX_CONTEXT_MESSAGES * 2) :]


async def _fetch_context(incident_id: str, user: UserContext) -> tuple[str, str, str]:
    """Fetch incident, dispatch, and crew data for the system prompt."""
    from sjifire.mcp.auth import set_current_user

    set_current_user(user)

    from sjifire.mcp.dispatch.store import DispatchStore
    from sjifire.mcp.incidents.store import IncidentStore
    from sjifire.mcp.schedule import tools as schedule_tools

    # Get incident
    incident_json = "{}"
    async with IncidentStore() as store:
        doc = await store.get_by_id(incident_id)
    if doc:
        incident_json = json.dumps(doc.model_dump(mode="json"), indent=2, default=str)

        # Get dispatch data using the incident number
        dispatch_json = "{}"
        try:
            async with DispatchStore() as store:
                dispatch = await store.get_by_dispatch_id(doc.incident_number)
            if dispatch:
                dispatch_json = json.dumps(dispatch.to_dict(), indent=2, default=str)
        except Exception:
            logger.warning("Failed to fetch dispatch for %s", doc.incident_number, exc_info=True)

        # Get crew for incident date
        crew_json = "[]"
        try:
            crew_data = await schedule_tools.get_on_duty_crew(
                target_date=doc.incident_date.isoformat()
            )
            crew_json = json.dumps(crew_data, indent=2, default=str)
        except Exception:
            logger.warning("Failed to fetch crew for %s", doc.incident_date, exc_info=True)
    else:
        dispatch_json = "{}"
        crew_json = "[]"

    return incident_json, dispatch_json, crew_json


async def stream_chat(
    incident_id: str,
    user_message: str,
    user: UserContext,
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
    budget_status = await check_budget(user.email)
    if not budget_status.allowed:
        yield _sse("error", {"message": budget_status.reason})
        return

    # Load or create conversation
    async with ConversationStore() as store:
        conversation = await store.get_by_incident(incident_id)

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
    incident_json, dispatch_json, crew_json = await _fetch_context(incident_id, user)
    system_prompt = _build_system_prompt(incident_json, dispatch_json, crew_json)

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

    # Add the new user message
    api_messages.append({"role": "user", "content": user_message})
    api_messages = _trim_messages(api_messages)

    # Record user message
    conversation.messages.append(ConversationMessage(role="user", content=user_message))

    # Streaming loop (handles tool calls)
    total_input = 0
    total_output = 0

    client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    try:
        async for event_str in _stream_loop(
            client, system_prompt, api_messages, conversation, user
        ):
            # Parse the event to track tokens
            yield event_str

    except Exception:
        logger.exception("Chat streaming error for incident %s", incident_id)
        yield _sse("error", {"message": "An error occurred. Please try again."})
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

    async with ConversationStore() as store:
        if is_new:
            await store.create(conversation)
        else:
            await store.update(conversation)

    # Record budget usage
    if total_input > 0 or total_output > 0:
        await record_usage(user.email, total_input, total_output)

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

        async with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_RESPONSE_TOKENS,
            system=system_prompt,
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
            final_message = stream.get_final_message()
            if final_message.usage:
                input_tokens = final_message.usage.input_tokens
                output_tokens = final_message.usage.output_tokens

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

        # Execute tool calls and build results
        tool_results: list[dict] = []
        for tc in tool_calls:
            tool_name = tc["name"]
            tool_input = tc["input"]

            yield _sse("tool_call", {"name": tool_name, "input": tool_input})

            result_str = await execute_tool(tool_name, tool_input, user)

            # Create a short summary for the UI
            try:
                result_data = json.loads(result_str)
                summary = _summarize_tool_result(tool_name, result_data)
            except json.JSONDecodeError:
                summary = result_str[:200]

            yield _sse("tool_result", {"name": tool_name, "summary": summary})

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result_str,
                }
            )

        # Record tool results as a message
        conversation.messages.append(
            ConversationMessage(
                role="user",
                content="",
                tool_results=tool_results,
            )
        )

        # Add to API messages for next round
        # Assistant message with tool use blocks
        assistant_content: list[dict] = []
        if assistant_text:
            assistant_content.append({"type": "text", "text": assistant_text})
        assistant_content.extend(tool_calls)
        api_messages.append({"role": "assistant", "content": assistant_content})

        # Tool results as user message
        api_messages.append({"role": "user", "content": tool_results})

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

    return json.dumps(data, default=str)[:200]


def _sse(event: str, data: dict) -> str:
    """Format a Server-Sent Event string."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
