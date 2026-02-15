"""Tests for chat route image validation."""

import json
from unittest.mock import patch

import pytest

from sjifire.ops.auth import UserContext
from sjifire.ops.chat.routes import chat_stream

_TEST_USER = UserContext(
    email="firefighter@sjifire.org",
    name="Test User",
    user_id="test-uid",
    groups=frozenset(),
)


class _FakeRequest:
    """Minimal Starlette Request stand-in for testing chat_stream."""

    def __init__(self, body: dict):
        self._body = body
        self.path_params = {"incident_id": "inc-test"}

    async def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _patch_auth(monkeypatch):
    """Bypass auth checks for route tests."""
    monkeypatch.delenv("ENTRA_MCP_API_CLIENT_ID", raising=False)
    monkeypatch.setattr("sjifire.ops.chat.routes._get_user", lambda r: _TEST_USER)


class TestImageValidation:
    async def test_rejects_more_than_3_images(self):
        req = _FakeRequest(
            {
                "message": "test",
                "images": [
                    {"data": "a", "media_type": "image/jpeg"},
                    {"data": "b", "media_type": "image/jpeg"},
                    {"data": "c", "media_type": "image/jpeg"},
                    {"data": "d", "media_type": "image/jpeg"},
                ],
            }
        )
        resp = await chat_stream(req)
        assert resp.status_code == 400
        body = json.loads(resp.body)
        assert "3 images" in body["error"]

    async def test_rejects_non_list_images(self):
        req = _FakeRequest(
            {
                "message": "test",
                "images": "not-a-list",
            }
        )
        resp = await chat_stream(req)
        assert resp.status_code == 400
        body = json.loads(resp.body)
        assert "3 images" in body["error"]

    async def test_rejects_unsupported_media_type(self):
        req = _FakeRequest(
            {
                "message": "test",
                "images": [{"data": "abc", "media_type": "image/bmp"}],
            }
        )
        resp = await chat_stream(req)
        assert resp.status_code == 400
        body = json.loads(resp.body)
        assert "Unsupported" in body["error"]

    async def test_rejects_oversized_image(self):
        req = _FakeRequest(
            {
                "message": "test",
                "images": [{"data": "x" * 2_500_000, "media_type": "image/jpeg"}],
            }
        )
        resp = await chat_stream(req)
        assert resp.status_code == 400
        body = json.loads(resp.body)
        assert "too large" in body["error"]

    async def test_rejects_invalid_image_format(self):
        req = _FakeRequest(
            {
                "message": "test",
                "images": ["not-a-dict"],
            }
        )
        resp = await chat_stream(req)
        assert resp.status_code == 400
        body = json.loads(resp.body)
        assert "Invalid" in body["error"]

    async def test_valid_images_passed_to_stream(self):
        """Valid images should reach stream_chat with images kwarg."""
        captured = {}

        async def fake_stream(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            yield "event: done\ndata: {}\n\n"

        with patch("sjifire.ops.chat.routes.stream_chat", side_effect=fake_stream):
            req = _FakeRequest(
                {
                    "message": "Check this photo",
                    "images": [
                        {"data": "abc123", "media_type": "image/jpeg"},
                        {"data": "def456", "media_type": "image/png"},
                    ],
                }
            )
            resp = await chat_stream(req)
            # Consume the streaming response to trigger the generator
            async for _ in resp.body_iterator:
                pass

        images = captured["kwargs"]["images"]
        assert images is not None
        assert len(images) == 2
        assert images[0]["media_type"] == "image/jpeg"
        assert images[1]["media_type"] == "image/png"

    async def test_no_images_passes_none(self):
        """When no images in request, images kwarg should be None."""
        captured = {}

        async def fake_stream(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            yield "event: done\ndata: {}\n\n"

        with patch("sjifire.ops.chat.routes.stream_chat", side_effect=fake_stream):
            req = _FakeRequest({"message": "Just text"})
            resp = await chat_stream(req)
            async for _ in resp.body_iterator:
                pass

        assert captured["kwargs"]["images"] is None
