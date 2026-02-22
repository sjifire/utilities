"""Tests for turn lock integration in chat routes.

These tests import Pydantic-based modules transitively via ``chat_stream``.
On Python 3.14rc2 with current Pydantic, collection fails with a typing
error.  Guarded with a try/except + module-level skip.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

_SKIP = False
_SKIP_REASON = ""
try:
    from sjifire.ops.auth import UserContext
    from sjifire.ops.chat.routes import chat_stream
    from sjifire.ops.chat.turn_lock import TurnLockStore
except Exception as _exc:
    _SKIP = True
    _SKIP_REASON = f"Import failed: {_exc}"

pytestmark = pytest.mark.skipif(_SKIP, reason=_SKIP_REASON)


def _make_alice():
    return UserContext(
        email="alice@sjifire.org",
        name="Alice Smith",
        user_id="alice-uid",
        groups=frozenset(),
    )


def _make_bob():
    return UserContext(
        email="bob@sjifire.org",
        name="Bob Jones",
        user_id="bob-uid",
        groups=frozenset(),
    )


class _FakeRequest:
    """Minimal Starlette Request stand-in."""

    def __init__(self, body: dict):
        self._body = body
        self.path_params = {"incident_id": "inc-test"}

    async def json(self):
        return self._body


async def _noop_container(name):
    return None


@pytest.fixture(autouse=True)
def _clear_memory(monkeypatch):
    """Reset in-memory store."""
    TurnLockStore._memory.clear()
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    monkeypatch.delenv("ENTRA_MCP_API_CLIENT_ID", raising=False)
    monkeypatch.setattr("sjifire.ops.chat.turn_lock.get_cosmos_container", _noop_container)
    yield
    TurnLockStore._memory.clear()


def _patch_user(user):
    """Patch _get_user to return the given user."""

    def _fake_get_user(_request):
        from sjifire.ops.auth import set_current_user

        set_current_user(user)
        return user

    return patch("sjifire.ops.chat.routes._get_user", _fake_get_user)


class TestTurnLockInRoutes:
    async def test_first_user_acquires_lock_and_gets_202(self):
        """First user to send a message gets 202 (lock acquired)."""
        alice = _make_alice()
        mock_run_chat = AsyncMock()

        with _patch_user(alice), patch("sjifire.ops.chat.routes.run_chat", mock_run_chat):
            req = _FakeRequest({"message": "Begin report"})
            resp = await chat_stream(req)
            await asyncio.sleep(0)

        assert resp.status_code == 202
        mock_run_chat.assert_called_once()

    async def test_second_user_gets_409_when_locked(self):
        """Second user gets 409 with holder info when lock is held."""
        bob = _make_bob()
        async with TurnLockStore() as store:
            await store.acquire("inc-test", "alice@sjifire.org", "Alice Smith")

        with _patch_user(bob):
            req = _FakeRequest({"message": "My turn?"})
            resp = await chat_stream(req)

        assert resp.status_code == 409
        body = json.loads(resp.body)
        assert "Alice Smith" in body["error"]
        assert body["holder_name"] == "Alice Smith"
        assert body["holder_email"] == "alice@sjifire.org"
        assert body["retry_after"] == "done"

    async def test_same_user_gets_409_when_turn_active(self):
        """Same user gets 409 when their own turn is still active.

        Prevents concurrent engine tasks. The client queues the message
        and auto-retries after the current turn's ``done`` event.
        """
        alice = _make_alice()

        async with TurnLockStore() as store:
            await store.acquire("inc-test", "alice@sjifire.org", "Alice Smith")

        with _patch_user(alice):
            req = _FakeRequest({"message": "Continue"})
            resp = await chat_stream(req)

        assert resp.status_code == 409
        body = json.loads(resp.body)
        assert body["holder_name"] == "Alice Smith"

    async def test_user_can_send_after_lock_released(self):
        """After lock is released, another user can send."""
        bob = _make_bob()
        mock_run_chat = AsyncMock()

        async with TurnLockStore() as store:
            await store.acquire("inc-test", "alice@sjifire.org", "Alice Smith")
            await store.release("inc-test", "alice@sjifire.org")

        with _patch_user(bob), patch("sjifire.ops.chat.routes.run_chat", mock_run_chat):
            req = _FakeRequest({"message": "My turn now"})
            resp = await chat_stream(req)
            await asyncio.sleep(0)

        assert resp.status_code == 202
        mock_run_chat.assert_called_once()

    async def test_409_response_has_holder_info_fields(self):
        """409 response includes structured holder info for client use."""
        bob = _make_bob()
        async with TurnLockStore() as store:
            await store.acquire("inc-test", "alice@sjifire.org", "Alice Smith")

        with _patch_user(bob):
            req = _FakeRequest({"message": "test"})
            resp = await chat_stream(req)

        body = json.loads(resp.body)
        assert "holder_name" in body
        assert "holder_email" in body
        assert "retry_after" in body
