"""Tests for chat route handlers: image validation, print report."""

import json
from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import patch

import pytest

from sjifire.ops.auth import UserContext
from sjifire.ops.chat.routes import chat_stream, print_report
from sjifire.ops.incidents.models import (
    CrewAssignment,
    EditEntry,
    IncidentDocument,
    Narratives,
)

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


# ---------------------------------------------------------------------------
# Print report route tests
# ---------------------------------------------------------------------------


def _make_incident(**overrides) -> IncidentDocument:
    """Build a minimal IncidentDocument for testing."""
    defaults = {
        "id": "inc-print-test",
        "station": "S31",
        "incident_number": "26-001678",
        "incident_date": date(2026, 2, 2),
        "created_by": "firefighter@sjifire.org",
    }
    defaults.update(overrides)
    return IncidentDocument(**defaults)


@asynccontextmanager
async def _fake_store(doc: IncidentDocument | None):
    """Async context manager that mimics IncidentStore."""

    class _Store:
        async def get_by_id(self, _id: str):
            return doc

    yield _Store()


class TestPrintReport:
    """Tests for the GET /reports/{id}/print route."""

    async def test_returns_html_with_incident_data(self):
        doc = _make_incident(
            incident_type="FIRE||STRUCTURE_FIRE||CHIMNEY_FIRE",
            address="241 WARBASS WAY",
            crew=[
                CrewAssignment(name="Kyle Dodd", rank="Batt Chief", unit="BN31", position="BC"),
            ],
            narratives=Narratives(outcome="False alarm.", actions_taken="Investigated."),
            timestamps={"psap_answer_time": "18:51:21"},
        )
        with patch(
            "sjifire.ops.chat.routes.IncidentStore",
            return_value=_fake_store(doc),
        ):
            req = _FakeRequest({})
            req.path_params = {"incident_id": "inc-print-test"}
            resp = await print_report(req)

        assert resp.status_code == 200
        assert resp.media_type == "text/html"
        body = resp.body.decode()
        assert "San Juan Island Fire" in body
        assert "26-001678" in body
        assert "Fire &gt; Structure Fire &gt; Chimney Fire" in body
        assert "241 WARBASS WAY" in body
        assert "Kyle Dodd" in body
        assert "False alarm." in body
        assert "Investigated." in body
        assert "18:51:21" in body

    async def test_returns_404_for_missing_incident(self):
        with patch(
            "sjifire.ops.chat.routes.IncidentStore",
            return_value=_fake_store(None),
        ):
            req = _FakeRequest({})
            req.path_params = {"incident_id": "nonexistent"}
            resp = await print_report(req)

        assert resp.status_code == 404
        body = json.loads(resp.body)
        assert "not found" in body["error"].lower()

    async def test_handles_minimal_incident(self):
        """A bare-bones incident (no crew, no narrative, no timestamps) renders."""
        doc = _make_incident()
        with patch(
            "sjifire.ops.chat.routes.IncidentStore",
            return_value=_fake_store(doc),
        ):
            req = _FakeRequest({})
            req.path_params = {"incident_id": "inc-print-test"}
            resp = await print_report(req)

        assert resp.status_code == 200
        body = resp.body.decode()
        assert "26-001678" in body
        assert "Not set" in body  # incident_type empty → "Not set"

    async def test_neris_id_shown_when_submitted(self):
        doc = _make_incident(
            status="submitted",
            neris_incident_id="FD53055879|26SJ0020|1770457554",
        )
        with patch(
            "sjifire.ops.chat.routes.IncidentStore",
            return_value=_fake_store(doc),
        ):
            req = _FakeRequest({})
            req.path_params = {"incident_id": "inc-print-test"}
            resp = await print_report(req)

        assert resp.status_code == 200
        body = resp.body.decode()
        assert "FD53055879" in body
        assert "submitted" in body.lower()

    async def test_edit_history_rendered(self):
        doc = _make_incident(
            edit_history=[
                EditEntry(
                    editor_email="chief@sjifire.org",
                    editor_name="Chief Smith",
                    fields_changed=["incident_type", "crew"],
                ),
            ],
        )
        with patch(
            "sjifire.ops.chat.routes.IncidentStore",
            return_value=_fake_store(doc),
        ):
            req = _FakeRequest({})
            req.path_params = {"incident_id": "inc-print-test"}
            resp = await print_report(req)

        assert resp.status_code == 200
        body = resp.body.decode()
        assert "Chief Smith" in body
        assert "incident_type" in body

    async def test_unit_responses_table(self):
        doc = _make_incident(
            unit_responses=[
                {
                    "unit_designator": "E31",
                    "dispatched": "18:53",
                    "enroute": "18:55",
                    "on_scene": "",
                    "cleared": "18:58",
                },
            ],
        )
        with patch(
            "sjifire.ops.chat.routes.IncidentStore",
            return_value=_fake_store(doc),
        ):
            req = _FakeRequest({})
            req.path_params = {"incident_id": "inc-print-test"}
            resp = await print_report(req)

        assert resp.status_code == 200
        body = resp.body.decode()
        assert "E31" in body
        assert "Apparatus Response" in body

    async def test_gps_coordinates_shown(self):
        doc = _make_incident(latitude=48.5234, longitude=-123.0156)
        with patch(
            "sjifire.ops.chat.routes.IncidentStore",
            return_value=_fake_store(doc),
        ):
            req = _FakeRequest({})
            req.path_params = {"incident_id": "inc-print-test"}
            resp = await print_report(req)

        assert resp.status_code == 200
        body = resp.body.decode()
        assert "48.5234" in body
        assert "-123.0156" in body
