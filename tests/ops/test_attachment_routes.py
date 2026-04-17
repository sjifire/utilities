"""Tests for attachment HTTP routes."""

import base64
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sjifire.ops.attachments.models import AttachmentMeta
from sjifire.ops.attachments.store import AttachmentBlobStore
from sjifire.ops.auth import UserContext, set_current_user
from sjifire.ops.incidents.models import IncidentDocument

_TEST_USER = UserContext(
    email="ff@sjifire.org",
    name="Firefighter",
    user_id="user-1",
    groups=frozenset(),
)

_TEST_OFFICER = UserContext(
    email="chief@sjifire.org",
    name="Chief",
    user_id="user-2",
    groups=frozenset(["officer-group"]),
)


def _fake_get_user(_request):
    """Sync mock for chat/routes.py which still uses get_request_user."""
    set_current_user(_TEST_USER)
    return _TEST_USER


async def _fake_require_auth(_request, **_kwargs):
    set_current_user(_TEST_USER)
    return _TEST_USER


async def _fake_require_auth_unauth(_request, **_kwargs):
    from starlette.responses import JSONResponse

    return JSONResponse({"error": "Unauthorized"}, status_code=401)


@pytest.fixture(autouse=True)
def _clear_blob_memory():
    AttachmentBlobStore._memory.clear()
    yield
    AttachmentBlobStore._memory.clear()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("ENTRA_MCP_API_CLIENT_ID", raising=False)
    monkeypatch.setattr("sjifire.ops.attachments.routes.require_auth", _fake_require_auth)


class _FakeUploadFile:
    """Minimal stand-in for Starlette's UploadFile."""

    def __init__(self, filename: str, content: bytes, content_type: str):
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self):
        return self._content


class _FakeRequest:
    def __init__(self, *, path_params: dict | None = None, form_data: dict | None = None):
        self.path_params = path_params or {}
        self._form = form_data or {}

    async def form(self):
        return self._form


class TestUploadRoute:
    async def test_returns_401_when_unauthenticated(self, monkeypatch):
        monkeypatch.setattr(
            "sjifire.ops.attachments.routes.require_auth", _fake_require_auth_unauth
        )
        from sjifire.ops.attachments.routes import upload_attachment_route

        req = _FakeRequest(path_params={"incident_id": "inc-1"})
        resp = await upload_attachment_route(req)
        assert resp.status_code == 401

    async def test_returns_400_when_no_file(self):
        from sjifire.ops.attachments.routes import upload_attachment_route

        req = _FakeRequest(
            path_params={"incident_id": "inc-1"},
            form_data={},
        )
        resp = await upload_attachment_route(req)
        assert resp.status_code == 400
        body = json.loads(resp.body)
        assert "No file" in body["error"]

    async def test_rejects_bad_content_type(self):
        from sjifire.ops.attachments.routes import upload_attachment_route

        upload = _FakeUploadFile("test.bmp", b"data", "image/bmp")
        req = _FakeRequest(
            path_params={"incident_id": "inc-1"},
            form_data={"file": upload},
        )
        resp = await upload_attachment_route(req)
        assert resp.status_code == 400
        body = json.loads(resp.body)
        assert "not allowed" in body["error"]

    async def test_rejects_oversized_file(self):
        from sjifire.ops.attachments.routes import upload_attachment_route

        upload = _FakeUploadFile("big.jpg", b"x" * (21 * 1024 * 1024), "image/jpeg")
        req = _FakeRequest(
            path_params={"incident_id": "inc-1"},
            form_data={"file": upload},
        )
        resp = await upload_attachment_route(req)
        assert resp.status_code == 400
        body = json.loads(resp.body)
        assert "too large" in body["error"].lower()


class TestListRoute:
    async def test_returns_401_when_unauthenticated(self, monkeypatch):
        monkeypatch.setattr(
            "sjifire.ops.attachments.routes.require_auth", _fake_require_auth_unauth
        )
        from sjifire.ops.attachments.routes import list_attachments_route

        req = _FakeRequest(path_params={"incident_id": "inc-1"})
        resp = await list_attachments_route(req)
        assert resp.status_code == 401


class TestDownloadRoute:
    async def test_returns_401_when_unauthenticated(self, monkeypatch):
        monkeypatch.setattr(
            "sjifire.ops.attachments.routes.require_auth", _fake_require_auth_unauth
        )
        from sjifire.ops.attachments.routes import download_attachment_route

        req = _FakeRequest(
            path_params={"incident_id": "inc-1", "attachment_id": "att-1"},
        )
        resp = await download_attachment_route(req)
        assert resp.status_code == 401

    async def test_returns_404_for_missing_incident(self):
        from sjifire.ops.attachments.routes import download_attachment_route

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=None)
        cls = MagicMock()
        cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        cls.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("sjifire.ops.incidents.store.IncidentStore", cls):
            req = _FakeRequest(
                path_params={"incident_id": "nonexistent", "attachment_id": "att-1"},
            )
            resp = await download_attachment_route(req)
        assert resp.status_code == 404

    async def test_downloads_blob(self):
        from sjifire.ops.attachments.routes import download_attachment_route

        meta = AttachmentMeta(
            id="att-1",
            filename="scene.jpg",
            content_type="image/jpeg",
            uploaded_by="ff@sjifire.org",
            blob_path="incidents/2026/doc-1/att-1-scene.jpg",
        )
        doc = IncidentDocument(
            id="doc-1",
            incident_number="26-001",
            incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
            created_by="ff@sjifire.org",
            attachments=[meta],
        )

        mock_store = AsyncMock()
        mock_store.get_by_id = AsyncMock(return_value=doc)
        cls = MagicMock()
        cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        cls.return_value.__aexit__ = AsyncMock(return_value=None)

        # Put blob data in memory store
        AttachmentBlobStore._memory[meta.blob_path] = (b"jpeg bytes", "image/jpeg")

        with patch("sjifire.ops.incidents.store.IncidentStore", cls):
            req = _FakeRequest(
                path_params={"incident_id": "doc-1", "attachment_id": "att-1"},
            )
            resp = await download_attachment_route(req)

        assert resp.status_code == 200
        assert resp.body == b"jpeg bytes"
        assert resp.media_type == "image/jpeg"


class TestDeleteRoute:
    async def test_returns_401_when_unauthenticated(self, monkeypatch):
        monkeypatch.setattr(
            "sjifire.ops.attachments.routes.require_auth", _fake_require_auth_unauth
        )
        from sjifire.ops.attachments.routes import delete_attachment_route

        req = _FakeRequest(
            path_params={"incident_id": "inc-1", "attachment_id": "att-1"},
        )
        resp = await delete_attachment_route(req)
        assert resp.status_code == 401


# -- RPC proxy auto-save (moved from chat_stream to centrifugo.rpc_proxy) ----

_TEST_B64INFO = base64.b64encode(
    json.dumps(
        {"user_id": "test-uid", "name": "Test User", "email": "firefighter@sjifire.org"}
    ).encode()
).decode()


class _FakeClient:
    host = "127.0.0.1"
    port = 0


class _RpcRequest:
    """Minimal request stand-in for RPC proxy tests."""

    client = _FakeClient()

    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


def _rpc_req(images_data: dict) -> _RpcRequest:
    """Build a fake Centrifugo RPC proxy request with send_message method."""
    return _RpcRequest(
        {
            "method": "send_message",
            "data": {"incident_id": "inc-test", **images_data},
            "user": "firefighter@sjifire.org",
            "b64info": _TEST_B64INFO,
        }
    )


class TestChatImageAutoSave:
    """Test that images sent through RPC chat are auto-saved as attachments."""

    async def test_images_auto_saved_without_title(self):
        """Auto-saved chat images should have no title (LLM assigns later)."""
        from sjifire.ops.chat.centrifugo import rpc_proxy

        saved_calls = []

        async def mock_upload(**kwargs):
            saved_calls.append(kwargs)
            return {"id": "att-1", "filename": kwargs["filename"]}

        mock_lock = MagicMock()
        mock_lock.acquire = AsyncMock(return_value=True)
        mock_lock.get = AsyncMock(return_value=None)
        mock_lock.__aenter__ = AsyncMock(return_value=mock_lock)
        mock_lock.__aexit__ = AsyncMock(return_value=False)

        req = _rpc_req(
            {
                "message": "Check this run sheet",
                "images": [{"data": "abc123", "media_type": "image/jpeg"}],
            }
        )

        with (
            patch("sjifire.ops.chat.engine.run_chat", new_callable=AsyncMock),
            patch("sjifire.ops.attachments.tools.upload_attachment", mock_upload),
            patch("sjifire.ops.chat.centrifugo.check_is_editor", AsyncMock(return_value=True)),
            patch("sjifire.ops.chat.turn_lock.TurnLockStore", return_value=mock_lock),
        ):
            resp = await rpc_proxy(req)
            import asyncio

            await asyncio.sleep(0)

        body = json.loads(resp.body)
        assert body["result"]["data"]["status"] == "accepted"
        assert len(saved_calls) == 1
        assert saved_calls[0]["filename"] == "chat-photo-1.jpg"
        assert "title" not in saved_calls[0]  # No title kwarg
        assert saved_calls[0]["content_type"] == "image/jpeg"

    async def test_auto_save_failure_does_not_block_chat(self):
        """If auto-save fails, chat should still proceed."""
        from sjifire.ops.chat.centrifugo import rpc_proxy

        async def mock_upload_fail(**kwargs):
            raise RuntimeError("Blob storage unavailable")

        run_chat_mock = AsyncMock()
        mock_lock = MagicMock()
        mock_lock.acquire = AsyncMock(return_value=True)
        mock_lock.get = AsyncMock(return_value=None)
        mock_lock.__aenter__ = AsyncMock(return_value=mock_lock)
        mock_lock.__aexit__ = AsyncMock(return_value=False)

        req = _rpc_req(
            {
                "message": "Photo attached",
                "images": [{"data": "abc", "media_type": "image/png"}],
            }
        )

        with (
            patch("sjifire.ops.chat.engine.run_chat", run_chat_mock),
            patch("sjifire.ops.attachments.tools.upload_attachment", mock_upload_fail),
            patch("sjifire.ops.chat.centrifugo.check_is_editor", AsyncMock(return_value=True)),
            patch("sjifire.ops.chat.turn_lock.TurnLockStore", return_value=mock_lock),
        ):
            resp = await rpc_proxy(req)
            import asyncio

            await asyncio.sleep(0)

        body = json.loads(resp.body)
        assert body["result"]["data"]["status"] == "accepted"
        run_chat_mock.assert_called_once()

    async def test_multiple_images_get_numbered_filenames(self):
        """Multiple images get chat-photo-1, chat-photo-2, etc."""
        from sjifire.ops.chat.centrifugo import rpc_proxy

        saved_calls = []

        async def mock_upload(**kwargs):
            saved_calls.append(kwargs)
            return {"id": f"att-{len(saved_calls)}"}

        mock_lock = MagicMock()
        mock_lock.acquire = AsyncMock(return_value=True)
        mock_lock.get = AsyncMock(return_value=None)
        mock_lock.__aenter__ = AsyncMock(return_value=mock_lock)
        mock_lock.__aexit__ = AsyncMock(return_value=False)

        req = _rpc_req(
            {
                "message": "Multiple photos",
                "images": [
                    {"data": "a", "media_type": "image/jpeg"},
                    {"data": "b", "media_type": "image/png"},
                    {"data": "c", "media_type": "image/webp"},
                ],
            }
        )

        with (
            patch("sjifire.ops.chat.engine.run_chat", new_callable=AsyncMock),
            patch("sjifire.ops.attachments.tools.upload_attachment", mock_upload),
            patch("sjifire.ops.chat.centrifugo.check_is_editor", AsyncMock(return_value=True)),
            patch("sjifire.ops.chat.turn_lock.TurnLockStore", return_value=mock_lock),
        ):
            resp = await rpc_proxy(req)
            import asyncio

            await asyncio.sleep(0)

        body = json.loads(resp.body)
        assert body["result"]["data"]["status"] == "accepted"
        assert len(saved_calls) == 3
        assert saved_calls[0]["filename"] == "chat-photo-1.jpg"
        assert saved_calls[1]["filename"] == "chat-photo-2.png"
        assert saved_calls[2]["filename"] == "chat-photo-3.webp"

    async def test_image_refs_passed_to_run_chat(self):
        """Successful auto-save should pass image_refs to run_chat."""
        import asyncio

        from sjifire.ops.chat.centrifugo import rpc_proxy

        captured_kwargs = {}

        async def mock_upload(**kwargs):
            return {"id": "att-saved-1", "filename": kwargs["filename"]}

        async def fake_run_chat(*args, **kwargs):
            captured_kwargs.update(kwargs)

        mock_lock = MagicMock()
        mock_lock.acquire = AsyncMock(return_value=True)
        mock_lock.get = AsyncMock(return_value=None)
        mock_lock.__aenter__ = AsyncMock(return_value=mock_lock)
        mock_lock.__aexit__ = AsyncMock(return_value=False)

        req = _rpc_req(
            {
                "message": "Check this photo",
                "images": [{"data": "abc123", "media_type": "image/jpeg"}],
            }
        )

        with (
            patch("sjifire.ops.chat.engine.run_chat", side_effect=fake_run_chat),
            patch("sjifire.ops.attachments.tools.upload_attachment", mock_upload),
            patch("sjifire.ops.chat.centrifugo.check_is_editor", AsyncMock(return_value=True)),
            patch("sjifire.ops.chat.turn_lock.TurnLockStore", return_value=mock_lock),
        ):
            resp = await rpc_proxy(req)
            await asyncio.sleep(0)

        body = json.loads(resp.body)
        assert body["result"]["data"]["status"] == "accepted"
        assert captured_kwargs["image_refs"] == [
            {"attachment_id": "att-saved-1", "content_type": "image/jpeg"}
        ]

    async def test_failed_upload_passes_no_image_refs(self):
        """When auto-save fails, image_refs should be None."""
        import asyncio

        from sjifire.ops.chat.centrifugo import rpc_proxy

        captured_kwargs = {}

        async def mock_upload_fail(**kwargs):
            return {"error": "something went wrong"}

        async def fake_run_chat(*args, **kwargs):
            captured_kwargs.update(kwargs)

        mock_lock = MagicMock()
        mock_lock.acquire = AsyncMock(return_value=True)
        mock_lock.get = AsyncMock(return_value=None)
        mock_lock.__aenter__ = AsyncMock(return_value=mock_lock)
        mock_lock.__aexit__ = AsyncMock(return_value=False)

        req = _rpc_req(
            {
                "message": "Photo here",
                "images": [{"data": "abc", "media_type": "image/png"}],
            }
        )

        with (
            patch("sjifire.ops.chat.engine.run_chat", side_effect=fake_run_chat),
            patch("sjifire.ops.attachments.tools.upload_attachment", mock_upload_fail),
            patch("sjifire.ops.chat.centrifugo.check_is_editor", AsyncMock(return_value=True)),
            patch("sjifire.ops.chat.turn_lock.TurnLockStore", return_value=mock_lock),
        ):
            resp = await rpc_proxy(req)
            await asyncio.sleep(0)

        body = json.loads(resp.body)
        assert body["result"]["data"]["status"] == "accepted"
        assert captured_kwargs["image_refs"] is None


# -- Conversation history with images -----------------------------------------


class TestConversationHistoryImages:
    """Test that conversation history includes image URLs."""

    async def test_history_includes_image_urls(self):
        from sjifire.ops.chat.models import ConversationDocument, ConversationMessage
        from sjifire.ops.chat.routes import conversation_history

        conv = ConversationDocument(
            incident_id="inc-hist",
            user_email="ff@sjifire.org",
            messages=[
                ConversationMessage(
                    role="user",
                    content="Check this photo",
                    images=[{"attachment_id": "att-99", "content_type": "image/jpeg"}],
                ),
                ConversationMessage(role="assistant", content="I see the photo."),
            ],
        )

        mock_store = AsyncMock()
        mock_store.get_by_incident = AsyncMock(return_value=conv)
        cls = MagicMock()
        cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        cls.return_value.__aexit__ = AsyncMock(return_value=None)

        req = _FakeRequest(path_params={"incident_id": "inc-hist"})

        with (
            patch("sjifire.ops.chat.routes.ConversationStore", cls),
            patch("sjifire.ops.chat.routes.get_request_user", _fake_get_user),
            patch("sjifire.ops.chat.routes.check_is_editor", return_value=True),
        ):
            resp = await conversation_history(req)

        body = json.loads(resp.body)
        msgs = body["messages"]
        assert len(msgs) == 2

        # User message should have image URLs
        assert "images" in msgs[0]
        assert msgs[0]["images"] == ["/reports/inc-hist/attachments/att-99"]

        # Assistant message should not have images
        assert "images" not in msgs[1]

    async def test_history_without_images_has_no_images_key(self):
        from sjifire.ops.chat.models import ConversationDocument, ConversationMessage
        from sjifire.ops.chat.routes import conversation_history

        conv = ConversationDocument(
            incident_id="inc-no-img",
            user_email="ff@sjifire.org",
            messages=[
                ConversationMessage(role="user", content="Hello"),
                ConversationMessage(role="assistant", content="Hi there"),
            ],
        )

        mock_store = AsyncMock()
        mock_store.get_by_incident = AsyncMock(return_value=conv)
        cls = MagicMock()
        cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        cls.return_value.__aexit__ = AsyncMock(return_value=None)

        req = _FakeRequest(path_params={"incident_id": "inc-no-img"})

        with (
            patch("sjifire.ops.chat.routes.ConversationStore", cls),
            patch("sjifire.ops.chat.routes.get_request_user", _fake_get_user),
            patch("sjifire.ops.chat.routes.check_is_editor", return_value=True),
        ):
            resp = await conversation_history(req)

        body = json.loads(resp.body)
        for msg in body["messages"]:
            assert "images" not in msg
