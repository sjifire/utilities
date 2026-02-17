"""Tests for attachment HTTP routes."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

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
    set_current_user(_TEST_USER)
    return _TEST_USER


def _fake_get_user_none(_request):
    return None


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
    monkeypatch.setattr("sjifire.ops.attachments.routes._get_user", _fake_get_user)


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
        monkeypatch.setattr("sjifire.ops.attachments.routes._get_user", _fake_get_user_none)
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
        monkeypatch.setattr("sjifire.ops.attachments.routes._get_user", _fake_get_user_none)
        from sjifire.ops.attachments.routes import list_attachments_route

        req = _FakeRequest(path_params={"incident_id": "inc-1"})
        resp = await list_attachments_route(req)
        assert resp.status_code == 401


class TestDownloadRoute:
    async def test_returns_401_when_unauthenticated(self, monkeypatch):
        monkeypatch.setattr("sjifire.ops.attachments.routes._get_user", _fake_get_user_none)
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
        cls = AsyncMock()
        cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        cls.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("sjifire.ops.attachments.routes.IncidentStore", cls):
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
        cls = AsyncMock()
        cls.return_value.__aenter__ = AsyncMock(return_value=mock_store)
        cls.return_value.__aexit__ = AsyncMock(return_value=None)

        # Put blob data in memory store
        AttachmentBlobStore._memory[meta.blob_path] = (b"jpeg bytes", "image/jpeg")

        with patch("sjifire.ops.attachments.routes.IncidentStore", cls):
            req = _FakeRequest(
                path_params={"incident_id": "doc-1", "attachment_id": "att-1"},
            )
            resp = await download_attachment_route(req)

        assert resp.status_code == 200
        assert resp.body == b"jpeg bytes"
        assert resp.media_type == "image/jpeg"


class TestDeleteRoute:
    async def test_returns_401_when_unauthenticated(self, monkeypatch):
        monkeypatch.setattr("sjifire.ops.attachments.routes._get_user", _fake_get_user_none)
        from sjifire.ops.attachments.routes import delete_attachment_route

        req = _FakeRequest(
            path_params={"incident_id": "inc-1", "attachment_id": "att-1"},
        )
        resp = await delete_attachment_route(req)
        assert resp.status_code == 401


# -- Chat route auto-save ---------------------------------------------------


class _ChatRequest:
    """Minimal request for chat_stream tests with path_params and json body."""

    def __init__(self, body: dict):
        self.path_params = {"incident_id": "inc-test"}
        self._body = body

    async def json(self):
        return self._body


class TestChatImageAutoSave:
    """Test that images sent through chat are auto-saved as attachments."""

    async def test_images_auto_saved_without_title(self):
        """Auto-saved chat images should have no title (LLM assigns later)."""
        from sjifire.ops.chat.routes import chat_stream

        saved_calls = []

        async def mock_upload(**kwargs):
            saved_calls.append(kwargs)
            return {"id": "att-1", "filename": kwargs["filename"]}

        async def fake_stream(*args, **kwargs):
            yield "event: done\ndata: {}\n\n"

        req = _ChatRequest(
            {
                "message": "Check this run sheet",
                "images": [{"data": "abc123", "media_type": "image/jpeg"}],
            }
        )

        with (
            patch("sjifire.ops.chat.routes.stream_chat", side_effect=fake_stream),
            patch("sjifire.ops.attachments.tools.upload_attachment", mock_upload),
            patch("sjifire.ops.chat.routes._get_user", _fake_get_user),
        ):
            resp = await chat_stream(req)
            async for _ in resp.body_iterator:
                pass

        assert len(saved_calls) == 1
        assert saved_calls[0]["filename"] == "chat-photo-1.jpg"
        assert "title" not in saved_calls[0]  # No title kwarg
        assert saved_calls[0]["content_type"] == "image/jpeg"

    async def test_auto_save_failure_does_not_block_chat(self):
        """If auto-save fails, chat should still proceed."""
        from sjifire.ops.chat.routes import chat_stream

        async def mock_upload_fail(**kwargs):
            raise RuntimeError("Blob storage unavailable")

        stream_called = False

        async def fake_stream(*args, **kwargs):
            nonlocal stream_called
            stream_called = True
            yield "event: done\ndata: {}\n\n"

        req = _ChatRequest(
            {
                "message": "Photo attached",
                "images": [{"data": "abc", "media_type": "image/png"}],
            }
        )

        with (
            patch("sjifire.ops.chat.routes.stream_chat", side_effect=fake_stream),
            patch("sjifire.ops.attachments.tools.upload_attachment", mock_upload_fail),
            patch("sjifire.ops.chat.routes._get_user", _fake_get_user),
        ):
            resp = await chat_stream(req)
            async for _ in resp.body_iterator:
                pass

        assert stream_called  # Chat proceeded despite upload failure

    async def test_multiple_images_get_numbered_filenames(self):
        """Multiple images get chat-photo-1, chat-photo-2, etc."""
        from sjifire.ops.chat.routes import chat_stream

        saved_calls = []

        async def mock_upload(**kwargs):
            saved_calls.append(kwargs)
            return {"id": f"att-{len(saved_calls)}"}

        async def fake_stream(*args, **kwargs):
            yield "event: done\ndata: {}\n\n"

        req = _ChatRequest(
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
            patch("sjifire.ops.chat.routes.stream_chat", side_effect=fake_stream),
            patch("sjifire.ops.attachments.tools.upload_attachment", mock_upload),
            patch("sjifire.ops.chat.routes._get_user", _fake_get_user),
        ):
            resp = await chat_stream(req)
            async for _ in resp.body_iterator:
                pass

        assert len(saved_calls) == 3
        assert saved_calls[0]["filename"] == "chat-photo-1.jpg"
        assert saved_calls[1]["filename"] == "chat-photo-2.png"
        assert saved_calls[2]["filename"] == "chat-photo-3.webp"
