"""Tests for chat route handlers: RPC proxy image validation, print report."""

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.ops.auth import UserContext
from sjifire.ops.chat.centrifugo import rpc_proxy
from sjifire.ops.chat.routes import create_report, print_report
from sjifire.ops.incidents.models import (
    EditEntry,
    IncidentDocument,
    PersonnelAssignment,
    UnitAssignment,
)

_TEST_USER = UserContext(
    email="firefighter@sjifire.org",
    name="Test User",
    user_id="test-uid",
    groups=frozenset(),
)

_B64INFO = base64.b64encode(
    json.dumps(
        {"user_id": _TEST_USER.user_id, "name": _TEST_USER.name, "email": _TEST_USER.email}
    ).encode()
).decode()


class _FakeClient:
    """Simulate localhost origin (Centrifugo sidecar)."""

    host = "127.0.0.1"
    port = 0


class _FakeRequest:
    """Minimal Starlette Request stand-in for testing routes."""

    client = _FakeClient()

    def __init__(self, body: dict, *, method: str = "POST"):
        self._body = body
        self.method = method
        self.path_params = {"incident_id": "inc-test"}

    async def json(self):
        return self._body


def _rpc_request(data: dict) -> _FakeRequest:
    """Build a fake Centrifugo RPC proxy request for send_message."""
    return _FakeRequest(
        {
            "method": "send_message",
            "data": {"incident_id": "inc-test", **data},
            "user": _TEST_USER.email,
            "b64info": _B64INFO,
        }
    )


def _fake_get_user(_request):
    """Fake _get_user that also sets the context var (like the real one)."""
    from sjifire.ops.auth import set_current_user

    set_current_user(_TEST_USER)
    return _TEST_USER


@pytest.fixture(autouse=True)
def _patch_auth(monkeypatch):
    """Bypass auth checks and reset turn lock for route tests."""
    from sjifire.ops.chat.turn_lock import TurnLockStore

    monkeypatch.delenv("ENTRA_MCP_API_CLIENT_ID", raising=False)
    monkeypatch.setattr("sjifire.ops.chat.routes._get_user", _fake_get_user)
    # Bypass editor check for RPC tests
    monkeypatch.setattr("sjifire.ops.chat.centrifugo.check_is_editor", AsyncMock(return_value=True))
    TurnLockStore._memory.clear()
    yield
    TurnLockStore._memory.clear()


class TestImageValidation:
    """Test image validation in the RPC proxy send_message handler."""

    async def test_rejects_more_than_3_images(self):
        req = _rpc_request(
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
        resp = await rpc_proxy(req)
        body = json.loads(resp.body)
        assert body["error"]["code"] == 400
        assert "3 images" in body["error"]["message"]

    async def test_rejects_non_list_images(self):
        req = _rpc_request(
            {
                "message": "test",
                "images": "not-a-list",
            }
        )
        resp = await rpc_proxy(req)
        body = json.loads(resp.body)
        assert body["error"]["code"] == 400
        assert "3 images" in body["error"]["message"]

    async def test_rejects_unsupported_media_type(self):
        req = _rpc_request(
            {
                "message": "test",
                "images": [{"data": "abc", "media_type": "image/bmp"}],
            }
        )
        resp = await rpc_proxy(req)
        body = json.loads(resp.body)
        assert body["error"]["code"] == 400
        assert "Unsupported" in body["error"]["message"]

    async def test_rejects_oversized_image(self):
        req = _rpc_request(
            {
                "message": "test",
                "images": [{"data": "x" * 2_500_000, "media_type": "image/jpeg"}],
            }
        )
        resp = await rpc_proxy(req)
        body = json.loads(resp.body)
        assert body["error"]["code"] == 400
        assert "too large" in body["error"]["message"]

    async def test_rejects_invalid_image_format(self):
        req = _rpc_request(
            {
                "message": "test",
                "images": ["not-a-dict"],
            }
        )
        resp = await rpc_proxy(req)
        body = json.loads(resp.body)
        assert body["error"]["code"] == 400
        assert "Invalid" in body["error"]["message"]

    async def test_valid_images_passed_to_run_chat(self):
        """Valid images should reach run_chat with images kwarg."""
        mock_run_chat = AsyncMock()

        with patch("sjifire.ops.chat.engine.run_chat", mock_run_chat):
            req = _rpc_request(
                {
                    "message": "Check this photo",
                    "images": [
                        {"data": "abc123", "media_type": "image/jpeg"},
                        {"data": "def456", "media_type": "image/png"},
                    ],
                }
            )
            resp = await rpc_proxy(req)
            # Let the background task run
            await asyncio.sleep(0)

        body = json.loads(resp.body)
        assert body["result"]["data"]["status"] == "accepted"
        mock_run_chat.assert_called_once()
        _, kwargs = mock_run_chat.call_args
        images = kwargs["images"]
        assert images is not None
        assert len(images) == 2
        assert images[0]["media_type"] == "image/jpeg"
        assert images[1]["media_type"] == "image/png"

    async def test_no_images_passes_none(self):
        """When no images in request, images kwarg should be None."""
        mock_run_chat = AsyncMock()

        with patch("sjifire.ops.chat.engine.run_chat", mock_run_chat):
            req = _rpc_request({"message": "Just text"})
            resp = await rpc_proxy(req)
            await asyncio.sleep(0)

        body = json.loads(resp.body)
        assert body["result"]["data"]["status"] == "accepted"
        mock_run_chat.assert_called_once()
        _, kwargs = mock_run_chat.call_args
        assert kwargs["images"] is None


# ---------------------------------------------------------------------------
# Print report route tests
# ---------------------------------------------------------------------------


def _make_incident(**overrides) -> IncidentDocument:
    """Build a minimal IncidentDocument for testing."""
    defaults = {
        "id": "inc-print-test",
        "incident_number": "26-001678",
        "incident_datetime": datetime(2026, 2, 2, tzinfo=UTC),
        "created_by": "firefighter@sjifire.org",
        "station": "S31",
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
            units=[
                UnitAssignment(
                    unit_id="BN31",
                    personnel=[
                        PersonnelAssignment(name="Kyle Dodd", rank="Batt Chief", position="BC"),
                    ],
                ),
            ],
            narrative="False alarm. Investigated.",
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

    async def test_units_table(self):
        doc = _make_incident(
            units=[
                UnitAssignment(
                    unit_id="E31",
                    dispatch="18:53",
                    enroute="18:55",
                    on_scene="",
                    cleared="18:58",
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


# ---------------------------------------------------------------------------
# Create report route tests
# ---------------------------------------------------------------------------


class _FakeFormRequest(_FakeRequest):
    """Fake request that supports form() for POST /reports/new."""

    def __init__(self, form_data: dict, **kwargs):
        super().__init__({}, method="POST", **kwargs)
        self._form = form_data

    async def form(self):
        return self._form


class TestCreateReport:
    """Tests for the POST /reports/new route."""

    async def test_get_redirects_to_reports_list(self):
        """GET /reports/new should redirect to /reports, not 500."""
        req = _FakeRequest({}, method="GET")
        resp = await create_report(req)

        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard#reports"

    async def test_post_with_dispatch_cad_comments(self):
        """POST /reports/new succeeds when dispatch has cad_comments string.

        Regression test: cad_comments is a plain string from iSpyFire, not
        a list of dicts. The old code iterated over it as list[dict] and
        called .get() on each character, causing AttributeError.
        """
        from sjifire.ops.dispatch.models import DispatchCallDocument

        dispatch = DispatchCallDocument(
            id="uuid-test",
            year="2026",
            long_term_call_id="26-002210",
            nature="Medical Aid",
            address="100 Spring St",
            agency_code="SJF",
            cad_comments="18:51 Dispatched\n18:55 Enroute",
        )

        mock_dispatch_store = AsyncMock()
        mock_dispatch_store.get_by_dispatch_id = AsyncMock(return_value=dispatch)

        mock_incident_store = AsyncMock()
        mock_incident_store.get_by_number = AsyncMock(return_value=None)
        mock_incident_store.create = AsyncMock(side_effect=lambda doc: doc)

        with (
            patch(
                "sjifire.ops.incidents.tools.IncidentStore",
            ) as mock_inc_cls,
            patch(
                "sjifire.ops.dispatch.store.DispatchStore",
            ) as mock_disp_cls,
        ):
            mock_inc_cls.return_value.__aenter__ = AsyncMock(return_value=mock_incident_store)
            mock_inc_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_disp_cls.return_value.__aenter__ = AsyncMock(return_value=mock_dispatch_store)
            mock_disp_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            req = _FakeFormRequest(
                {
                    "incident_number": "26-002210",
                    "incident_date": "2026-02-16",
                    "station": "S31",
                }
            )
            resp = await create_report(req)

        # Should redirect to the new report, not 500
        assert resp.status_code == 303
        assert "/reports/" in resp.headers["location"]
