"""Integration tests for the chat engine pipeline.

Exercise the full engine pipeline — run_chat → _run_loop → Claude streaming →
tool execution → conversation persistence — with only the Claude API and
external services stubbed.  Catches bugs that unit tests miss because they
mock _run_loop entirely (e.g. orphaned tool_results, reset race conditions).
"""

import base64
import json
import logging
from unittest.mock import patch

import pytest

from sjifire.ops.auth import UserContext
from sjifire.ops.chat.models import ConversationDocument, ConversationMessage
from sjifire.ops.chat.store import BudgetStore, ConversationStore
from sjifire.ops.chat.turn_lock import TurnLockStore
from sjifire.ops.dispatch.store import DispatchStore
from sjifire.ops.incidents.models import IncidentDocument
from sjifire.ops.incidents.store import IncidentStore
from sjifire.ops.schedule.store import ScheduleStore

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

TEST_USER = UserContext(
    email="firefighter@sjifire.org",
    name="Test User",
    user_id="test-uid",
    groups=frozenset(),
)

USER_B = UserContext(
    email="other@sjifire.org",
    name="Other User",
    user_id="other-uid",
    groups=frozenset(),
)


# ---------------------------------------------------------------------------
# Autouse fixture — mirrors test_chat_engine.py
# ---------------------------------------------------------------------------


async def _noop_container(name):
    return None


@pytest.fixture(autouse=True)
def _clear_memory_and_env(monkeypatch):
    """Reset all in-memory stores and patch Cosmos/external deps."""
    import sjifire.ops.chat.engine as engine_mod

    ConversationStore._memory.clear()
    BudgetStore._memory.clear()
    IncidentStore._memory.clear()
    DispatchStore._memory.clear()
    TurnLockStore._memory.clear()
    ScheduleStore._memory.clear()

    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)

    monkeypatch.setattr("sjifire.ops.chat.store.get_cosmos_container", _noop_container)
    monkeypatch.setattr("sjifire.ops.incidents.store.get_cosmos_container", _noop_container)
    monkeypatch.setattr("sjifire.ops.dispatch.store.get_cosmos_container", _noop_container)
    monkeypatch.setattr("sjifire.ops.chat.turn_lock.get_cosmos_container", _noop_container)
    monkeypatch.setattr("sjifire.ops.schedule.store.get_cosmos_container", _noop_container)

    # Minimal doc stubs so _load_doc doesn't hit the filesystem.
    # The incident prompt uses {company_name}, {user_name}, {user_email}.
    # The general prompt uses {company_name}, {today}, {time}, {timezone}.
    def _fake_load_doc(name):
        if "general" in name:
            return (
                "General assistant for {company_name}. TODAY: {today} TIME: {time} TZ: {timezone}"
            )
        return "RULES: be helpful\nWORKFLOW: do things\n{company_name}\n{user_name}\n{user_email}"

    monkeypatch.setattr("sjifire.ops.chat.engine._load_doc", _fake_load_doc)
    engine_mod._neris_incident_types_cache = ""

    yield

    ConversationStore._memory.clear()
    BudgetStore._memory.clear()
    IncidentStore._memory.clear()
    DispatchStore._memory.clear()
    TurnLockStore._memory.clear()
    ScheduleStore._memory.clear()


# ---------------------------------------------------------------------------
# FakeStream / FakeClient helpers
# ---------------------------------------------------------------------------


class FakeUsage:
    """Fake Anthropic usage object."""

    def __init__(self, input_tokens=1000, output_tokens=200):  # noqa: D107
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class FakeMessage:
    """Fake Anthropic message object."""

    def __init__(self, usage=None):  # noqa: D107
        self.usage = usage or FakeUsage()


class _ContentBlock:
    def __init__(self, block_type, block_id=None, name=None):
        self.type = block_type
        self.id = block_id
        self.name = name


class _BlockStart:
    def __init__(self, content_block):
        self.type = "content_block_start"
        self.content_block = content_block


class _TextDelta:
    def __init__(self, text):
        self.type = "content_block_delta"
        self.delta = type("D", (), {"text": text})()


class _InputDelta:
    def __init__(self, partial_json):
        self.type = "content_block_delta"
        self.delta = type("D", (), {"partial_json": partial_json})()


class _BlockStop:
    type = "content_block_stop"


def text_events(text: str) -> list:
    """Build streaming events for a plain text response."""
    return [_TextDelta(text)]


def tool_use_events(tool_id: str, name: str, input_dict: dict) -> list:
    """Build streaming events for a tool_use block."""
    return [
        _BlockStart(_ContentBlock("tool_use", tool_id, name)),
        _InputDelta(json.dumps(input_dict)),
        _BlockStop(),
    ]


class FakeStream:
    """Async context manager yielding streaming events."""

    def __init__(self, events, usage=None):  # noqa: D107
        self._events = events
        self._usage = usage or FakeUsage()

    async def __aenter__(self):  # noqa: D105
        return self

    async def __aexit__(self, *a):  # noqa: D105
        pass

    async def __aiter__(self):  # noqa: D105
        for e in self._events:
            yield e

    async def get_final_message(self):
        """Return fake final message with usage stats."""
        return FakeMessage(self._usage)


def make_fake_client(responses: list[FakeStream]):
    """Return a client whose messages.stream() returns successive FakeStreams."""
    call_count = 0

    def stream_factory(*args, **kwargs):
        nonlocal call_count
        idx = min(call_count, len(responses) - 1)
        call_count += 1
        return responses[idx]

    class FakeMessages:
        stream = staticmethod(stream_factory)

    class FakeClient:
        messages = FakeMessages()

    return FakeClient()


# ---------------------------------------------------------------------------
# Event capture helper
# ---------------------------------------------------------------------------


def make_event_capturer():
    """Return (fake_publish, events_list)."""
    events: list[tuple[str, str, dict]] = []

    async def fake_publish(channel, event_type, data):
        events.append((channel, event_type, data))

    return fake_publish, events


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def seed_incident(incident_id: str, **overrides) -> IncidentDocument:
    """Create a minimal IncidentDocument in the in-memory IncidentStore."""
    defaults = {
        "id": incident_id,
        "incident_number": "26-001234",
        "incident_datetime": "2026-02-15T14:30:00+00:00",
        "created_by": "firefighter@sjifire.org",
        "extras": {"station": "S31"},
    }
    defaults.update(overrides)
    doc = IncidentDocument(**defaults)
    async with IncidentStore() as store:
        await store.create(doc)
    return doc


# ---------------------------------------------------------------------------
# Common patches applied to every integration test
# ---------------------------------------------------------------------------


def _integration_patches(fake_client, fake_publish):
    """Return a combined context manager patching Claude client, publish, and external deps."""
    from contextlib import contextmanager

    @contextmanager
    def patches():
        with (
            patch("sjifire.ops.chat.engine.get_client", return_value=fake_client),
            patch("sjifire.ops.chat.engine.publish", side_effect=fake_publish),
            patch(
                "sjifire.ops.schedule.tools.get_on_duty_crew",
                return_value={"crew": [], "count": 0},
            ),
            patch("sjifire.ops.personnel.tools.get_operational_personnel", return_value=[]),
            patch("sjifire.ops.chat.engine._get_all_neris_incident_types", return_value=""),
            patch("sjifire.ops.auth.check_is_editor", return_value=True),
        ):
            yield

    return patches()


# ===========================================================================
# Test Cases
# ===========================================================================


class TestSendMessageTextResponseSavesConversation:
    """Full flow: run_chat → text response → conversation persisted."""

    async def test_basic_text_response(self):
        from sjifire.ops.chat.engine import run_chat

        await seed_incident("inc-text-1")

        fake_publish, events = make_event_capturer()
        client = make_fake_client([FakeStream(text_events("Summary of the incident."))])

        with _integration_patches(client, fake_publish):
            await run_chat("inc-text-1", "Tell me about this fire.", TEST_USER, channel="ch")

        event_types = [e[1] for e in events]
        assert "turn_start" in event_types
        assert "text" in event_types
        assert "user_message" in event_types
        assert "done" in event_types
        assert "error" not in event_types

        # Verify conversation was persisted
        async with ConversationStore() as store:
            conv = await store.get_by_incident("inc-text-1")
        assert conv is not None
        assert conv.turn_count == 1
        assert len(conv.messages) == 2  # user + assistant
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "Tell me about this fire."
        assert conv.messages[1].role == "assistant"
        assert conv.messages[1].content == "Summary of the incident."


class TestToolUseRoundTrip:
    """Claude calls get_incident, engine executes against in-memory store, Claude responds."""

    async def test_tool_use_and_text_response(self):
        from sjifire.ops.chat.engine import run_chat

        await seed_incident("inc-tool-1")

        fake_publish, events = make_event_capturer()
        client = make_fake_client(
            [
                FakeStream(
                    tool_use_events("toolu_1", "get_incident", {"incident_id": "inc-tool-1"})
                ),
                FakeStream(text_events("It's a fire call.")),
            ]
        )

        with _integration_patches(client, fake_publish):
            await run_chat("inc-tool-1", "What incident is this?", TEST_USER, channel="ch")

        event_types = [e[1] for e in events]
        assert "tool_call" in event_types
        assert "tool_result" in event_types
        assert "text" in event_types
        assert "error" not in event_types

        # Check tool_call event content
        tool_call_evts = [(t, d) for _, t, d in events if t == "tool_call"]
        assert tool_call_evts[0][1]["name"] == "get_incident"

        # Check tool_result contains incident number
        tool_result_evts = [(t, d) for _, t, d in events if t == "tool_result"]
        assert "26-001234" in tool_result_evts[0][1]["summary"]

        # Conversation: user, assistant+tool_use, user+tool_results, assistant+text
        async with ConversationStore() as store:
            conv = await store.get_by_incident("inc-tool-1")
        assert conv is not None
        assert len(conv.messages) == 4
        assert conv.messages[0].role == "user"
        assert conv.messages[1].role == "assistant"
        assert conv.messages[1].tool_use is not None
        assert conv.messages[2].role == "user"
        assert conv.messages[2].tool_results is not None
        assert conv.messages[3].role == "assistant"
        assert conv.messages[3].content == "It's a fire call."


class TestResetClearsHistoryAndContinues:
    """Regression test for the 'Something went wrong' production bug."""

    async def test_reset_clears_history(self):
        from sjifire.ops.chat.engine import run_chat

        inc = await seed_incident("inc-reset-1")

        # Pre-populate conversation with existing messages (simulating prior turns)
        pre_conv = ConversationDocument(
            incident_id="inc-reset-1",
            user_email="firefighter@sjifire.org",
            turn_count=3,
            messages=[
                ConversationMessage(role="user", content="old message 1"),
                ConversationMessage(role="assistant", content="old response 1"),
                ConversationMessage(role="user", content="old message 2"),
                ConversationMessage(role="assistant", content="old response 2"),
            ],
        )
        async with ConversationStore() as store:
            await store.create(pre_conv)

        fake_publish, events = make_event_capturer()

        # The reset_incident tool returns the incident data on success
        reset_result = inc.to_cosmos()
        reset_result["_reimport_available"] = False

        client = make_fake_client(
            [
                FakeStream(
                    tool_use_events("toolu_r1", "reset_incident", {"incident_id": "inc-reset-1"})
                ),
                FakeStream(text_events("Starting fresh.")),
            ]
        )

        with (
            _integration_patches(client, fake_publish),
            patch(
                "sjifire.ops.incidents.tools.reset_incident",
                return_value=reset_result,
            ),
        ):
            await run_chat("inc-reset-1", "Reset this report", TEST_USER, channel="ch")

        event_types = [e[1] for e in events]
        assert "error" not in event_types
        assert "done" in event_types

        # Conversation should be clean — pre-reset messages gone
        async with ConversationStore() as store:
            conv = await store.get_by_incident("inc-reset-1")
        assert conv is not None
        assert conv.turn_count == 1  # Reset to 0, then +1 for this turn

        # The assistant tool_use message should be preserved so tool_result has matching ID
        has_tool_use = any(m.tool_use for m in conv.messages if m.role == "assistant")
        assert has_tool_use

        # "Starting fresh." should be in the final assistant message
        last_assistant = [m for m in conv.messages if m.role == "assistant"][-1]
        assert last_assistant.content == "Starting fresh."


class TestTurnLock409ViaRpcProxy:
    """Second user blocked by turn lock."""

    async def test_409_when_locked(self):
        from starlette.requests import Request

        from sjifire.ops.chat.centrifugo import rpc_proxy

        await seed_incident("inc-lock-1")

        # Acquire lock for user A
        async with TurnLockStore() as lock_store:
            lock = await lock_store.acquire("inc-lock-1", TEST_USER.email, TEST_USER.name)
        assert lock is not None

        # Build a fake RPC request for user B
        b64info = base64.b64encode(
            json.dumps(
                {"email": USER_B.email, "name": USER_B.name, "user_id": USER_B.user_id}
            ).encode()
        ).decode()

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/centrifugo/rpc",
            "headers": [],
        }
        body = json.dumps(
            {
                "method": "send_message",
                "user": USER_B.email,
                "b64info": b64info,
                "data": {
                    "incident_id": "inc-lock-1",
                    "message": "I want to chat too",
                },
            }
        ).encode()

        async def receive():
            return {"type": "http.request", "body": body}

        request = Request(scope, receive)

        with patch("sjifire.ops.chat.centrifugo.check_is_editor", return_value=True):
            response = await rpc_proxy(request)

        assert response.status_code == 200  # Centrifugo RPC returns 200 with error in body
        response_data = json.loads(response.body.decode())
        assert "error" in response_data
        assert response_data["error"]["code"] == 409

        error_msg = json.loads(response_data["error"]["message"])
        assert error_msg["holder_name"] == TEST_USER.name
        assert error_msg["holder_email"] == TEST_USER.email
        assert error_msg["retry_after"] == "done"


class TestOrphanedToolResultsDropped:
    """Corrupted history recovery — orphaned tool_results are logged and dropped."""

    async def test_orphaned_results_recovered(self, caplog):
        from sjifire.ops.chat.engine import run_chat

        await seed_incident("inc-orphan-1")

        # Create a corrupted conversation with orphaned tool_results
        corrupted_conv = ConversationDocument(
            incident_id="inc-orphan-1",
            user_email="firefighter@sjifire.org",
            turn_count=1,
            messages=[
                ConversationMessage(role="user", content="first message"),
                ConversationMessage(role="assistant", content="first response"),
                # Orphaned: tool_result referencing a tool_use that doesn't exist
                ConversationMessage(
                    role="user",
                    content="",
                    tool_results=[
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_nonexistent",
                            "content": "some old result",
                        }
                    ],
                ),
            ],
        )
        async with ConversationStore() as store:
            await store.create(corrupted_conv)

        fake_publish, events = make_event_capturer()
        client = make_fake_client([FakeStream(text_events("Recovered fine."))])

        with (
            _integration_patches(client, fake_publish),
            caplog.at_level(logging.WARNING, logger="sjifire.ops.chat.engine"),
        ):
            await run_chat("inc-orphan-1", "Continue please", TEST_USER, channel="ch")

        event_types = [e[1] for e in events]
        assert "error" not in event_types
        assert "done" in event_types

        # Verify the orphaned tool_results warning was logged
        assert "Dropping orphaned tool_results" in caplog.text


class TestMultiTurnAccumulatesHistory:
    """Two sequential run_chat calls build correct history."""

    async def test_two_turns(self):
        from sjifire.ops.chat.engine import run_chat

        await seed_incident("inc-multi-1")

        fake_publish, events = make_event_capturer()

        # Turn 1
        client1 = make_fake_client([FakeStream(text_events("First response."))])
        with _integration_patches(client1, fake_publish):
            await run_chat("inc-multi-1", "First message", TEST_USER, channel="ch")

        async with ConversationStore() as store:
            conv1 = await store.get_by_incident("inc-multi-1")
        assert conv1 is not None
        assert conv1.turn_count == 1
        assert len(conv1.messages) == 2

        # Clear events for turn 2
        events.clear()

        # Release the turn lock from turn 1
        async with TurnLockStore() as lock_store:
            await lock_store.release("inc-multi-1", TEST_USER.email)

        # Turn 2
        client2 = make_fake_client([FakeStream(text_events("Second response."))])
        with _integration_patches(client2, fake_publish):
            await run_chat("inc-multi-1", "Second message", TEST_USER, channel="ch")

        async with ConversationStore() as store:
            conv2 = await store.get_by_incident("inc-multi-1")
        assert conv2 is not None
        assert conv2.turn_count == 2
        assert len(conv2.messages) == 4
        assert conv2.messages[2].content == "Second message"
        assert conv2.messages[3].content == "Second response."

        # Token totals should accumulate
        assert conv2.total_input_tokens > 0
        assert conv2.total_output_tokens > 0


class TestGeneralChatTextResponse:
    """run_general_chat path — text response saves to general:{email} conversation."""

    async def test_general_chat(self):
        from sjifire.ops.chat.engine import run_general_chat

        fake_publish, events = make_event_capturer()
        client = make_fake_client([FakeStream(text_events("Here are the recent calls."))])

        with (
            patch("sjifire.ops.chat.engine.get_client", return_value=client),
            patch("sjifire.ops.chat.engine.publish", side_effect=fake_publish),
            patch("sjifire.ops.chat.engine._get_all_neris_incident_types", return_value=""),
        ):
            await run_general_chat("What calls came in today?", TEST_USER, channel="gen-ch")

        event_types = [e[1] for e in events]
        assert "text" in event_types
        assert "done" in event_types
        assert "error" not in event_types

        # Conversation stored under general:{email}
        conv_key = f"general:{TEST_USER.email}"
        async with ConversationStore() as store:
            conv = await store.get_by_incident(conv_key)
        assert conv is not None
        assert conv.incident_id == conv_key
        assert conv.turn_count == 1
        assert len(conv.messages) == 2


class TestTurnEventsIncludeUserAttribution:
    """Events carry user_email/user_name so the client banner knows whose turn it is.

    The chat UI shows "Claude is responding to X" when another user's turn is
    active. If turn_start or done events lack user attribution, the banner
    either never shows (unsafe) or can't distinguish self vs other (annoying).
    """

    async def test_turn_start_and_done_carry_user_identity(self):
        from sjifire.ops.chat.engine import run_chat

        await seed_incident("inc-attr-1")

        fake_publish, events = make_event_capturer()
        client = make_fake_client([FakeStream(text_events("All good."))])

        with _integration_patches(client, fake_publish):
            await run_chat("inc-attr-1", "Check the report", TEST_USER, channel="ch")

        # turn_start must include user identity
        turn_starts = [d for _, t, d in events if t == "turn_start"]
        assert len(turn_starts) == 1
        assert turn_starts[0]["user_email"] == TEST_USER.email
        assert turn_starts[0]["user_name"] == TEST_USER.name

        # done must include user identity (so other clients clear the banner)
        dones = [d for _, t, d in events if t == "done"]
        assert len(dones) == 1
        assert dones[0]["user_email"] == TEST_USER.email
        assert dones[0]["user_name"] == TEST_USER.name

    async def test_user_message_broadcast_carries_attribution(self):
        from sjifire.ops.chat.engine import run_chat

        await seed_incident("inc-attr-2")

        fake_publish, events = make_event_capturer()
        client = make_fake_client([FakeStream(text_events("Got it."))])

        with _integration_patches(client, fake_publish):
            await run_chat("inc-attr-2", "Hello team", TEST_USER, channel="ch")

        # user_message event broadcasts the sender's identity
        user_msgs = [d for _, t, d in events if t == "user_message"]
        assert len(user_msgs) == 1
        assert user_msgs[0]["user_email"] == TEST_USER.email
        assert user_msgs[0]["user_name"] == TEST_USER.name
        assert user_msgs[0]["content"] == "Hello team"

    async def test_409_includes_holder_identity_for_banner(self):
        """Verify 409 error body includes holder name/email for the client banner."""
        from starlette.requests import Request

        from sjifire.ops.chat.centrifugo import rpc_proxy

        await seed_incident("inc-attr-3")

        # Lock held by user A
        async with TurnLockStore() as lock_store:
            await lock_store.acquire("inc-attr-3", TEST_USER.email, TEST_USER.name)

        # User B tries to send
        b64info = base64.b64encode(
            json.dumps(
                {"email": USER_B.email, "name": USER_B.name, "user_id": USER_B.user_id}
            ).encode()
        ).decode()

        scope = {"type": "http", "method": "POST", "path": "/centrifugo/rpc", "headers": []}
        body = json.dumps(
            {
                "method": "send_message",
                "user": USER_B.email,
                "b64info": b64info,
                "data": {"incident_id": "inc-attr-3", "message": "My turn?"},
            }
        ).encode()

        async def receive():
            return {"type": "http.request", "body": body}

        request = Request(scope, receive)

        with patch("sjifire.ops.chat.centrifugo.check_is_editor", return_value=True):
            response = await rpc_proxy(request)

        data = json.loads(response.body.decode())
        assert data["error"]["code"] == 409

        holder_info = json.loads(data["error"]["message"])
        assert holder_info["holder_name"] == TEST_USER.name
        assert holder_info["holder_email"] == TEST_USER.email
        # Client uses retry_after to know when to auto-retry
        assert holder_info["retry_after"] == "done"


class TestUpdateIncidentEmitsStatusUpdate:
    """update_incident tool triggers live status_update event."""

    async def test_status_update_event(self):
        from sjifire.ops.chat.engine import run_chat

        await seed_incident("inc-update-1", status="draft")

        fake_publish, events = make_event_capturer()
        client = make_fake_client(
            [
                FakeStream(
                    tool_use_events(
                        "toolu_u1",
                        "update_incident",
                        {
                            "incident_id": "inc-update-1",
                            "status": "in_progress",
                            "narrative": "Fire in single-story residential.",
                        },
                    )
                ),
                FakeStream(text_events("Updated.")),
            ]
        )

        with _integration_patches(client, fake_publish):
            await run_chat("inc-update-1", "Update the status", TEST_USER, channel="ch")

        event_types = [e[1] for e in events]
        assert "error" not in event_types
        assert "status_update" in event_types

        # Verify status_update event content
        status_evts = [d for _, t, d in events if t == "status_update"]
        assert len(status_evts) >= 1
        assert status_evts[0]["status"] == "in_progress"
        assert "completeness" in status_evts[0]
        assert "filled" in status_evts[0]["completeness"]

        # Verify the incident was actually updated in the store
        async with IncidentStore() as store:
            doc = await store.get_by_id("inc-update-1")
        assert doc is not None
        assert doc.status == "in_progress"
        assert doc.narrative == "Fire in single-story residential."
