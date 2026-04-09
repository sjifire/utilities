"""Tests for the events module — models, store, and routes."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.ops.attachments.store import AttachmentBlobStore
from sjifire.ops.auth import UserContext, set_current_user
from sjifire.ops.events.models import (
    AttendeeRecord,
    EventAttachmentMeta,
    EventRecord,
    build_event_blob_path,
)
from sjifire.ops.events.store import EventStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TEST_USER = UserContext(
    email="test@sjifire.org",
    name="Test User",
    user_id="uid-test",
    groups=frozenset(["editor-group"]),
)


def _fake_get_user(_request):
    set_current_user(_TEST_USER)
    return _TEST_USER


def _fake_get_user_none(_request):
    return None


@pytest.fixture(autouse=True)
def _clear_stores(monkeypatch):
    EventStore._memory.clear()
    AttachmentBlobStore._memory.clear()
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.setattr("sjifire.ops.cosmos.get_cosmos_container", _noop_container)
    yield
    EventStore._memory.clear()
    AttachmentBlobStore._memory.clear()


async def _noop_container(name):
    return None


def _make_record(**overrides) -> EventRecord:
    defaults = {
        "subject": "Ladder Training",
        "event_date": datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        "created_by": "test@sjifire.org",
    }
    defaults.update(overrides)
    return EventRecord(**defaults)


# ===========================================================================
# Model tests
# ===========================================================================


class TestEventRecord:
    def test_year_derived_from_event_date(self):
        rec = _make_record(event_date=datetime(2026, 7, 4, tzinfo=UTC))
        assert rec.year == "2026"

    def test_to_cosmos_roundtrip(self):
        rec = _make_record()
        data = rec.to_cosmos()
        restored = EventRecord.from_cosmos(data)
        assert restored.id == rec.id
        assert restored.subject == rec.subject
        assert restored.year == rec.year

    def test_from_cosmos_legacy_training_date(self):
        """Legacy docs with training_date should map to event_date."""
        data = {
            "id": "old-1",
            "subject": "Old Training",
            "training_date": "2025-06-15T09:00:00+00:00",
            "created_by": "test@sjifire.org",
        }
        rec = EventRecord.from_cosmos(data)
        assert rec.event_date.year == 2025
        assert rec.year == "2025"

    def test_attendee_email_normalized(self):
        att = AttendeeRecord(name="John", email="JOHN@SJIFIRE.ORG")
        assert att.email == "john@sjifire.org"

    def test_attachment_uploaded_by_normalized(self):
        meta = EventAttachmentMeta(
            filename="test.jpg",
            content_type="image/jpeg",
            uploaded_by="Test@sjifire.org",
        )
        assert meta.uploaded_by == "test@sjifire.org"


class TestBuildEventBlobPath:
    def test_basic_path(self):
        path = build_event_blob_path("2026", "rec-1", "att-1", "photo.jpg")
        assert path == "events/2026/rec-1/att-1-photo.jpg"

    def test_sanitizes_slashes(self):
        path = build_event_blob_path("2026", "rec-1", "att-1", "path/to\\file.pdf")
        assert "/" not in path.split("-", 3)[-1] or path.count("/") == 3
        assert "\\" not in path


# ===========================================================================
# Store tests
# ===========================================================================


class TestEventStore:
    async def test_upsert_and_get_by_id(self):
        rec = _make_record()
        async with EventStore() as store:
            saved = await store.upsert(rec)
            assert saved.id == rec.id
            assert saved.updated_at is not None

            fetched = await store.get_by_id(rec.id)
            assert fetched is not None
            assert fetched.subject == "Ladder Training"

    async def test_get_by_id_not_found(self):
        async with EventStore() as store:
            result = await store.get_by_id("nonexistent")
            assert result is None

    async def test_get_by_calendar_event_id(self):
        rec = _make_record(calendar_event_id="cal-123")
        async with EventStore() as store:
            await store.upsert(rec)
            found = await store.get_by_calendar_event_id("cal-123")
            assert found is not None
            assert found.id == rec.id

    async def test_get_by_calendar_event_id_not_found(self):
        async with EventStore() as store:
            result = await store.get_by_calendar_event_id("missing")
            assert result is None

    async def test_list_by_year(self):
        r1 = _make_record(
            subject="Jan Event",
            event_date=datetime(2026, 1, 15, tzinfo=UTC),
        )
        r2 = _make_record(
            subject="Mar Event",
            event_date=datetime(2026, 3, 10, tzinfo=UTC),
        )
        r3 = _make_record(
            subject="Different Year",
            event_date=datetime(2025, 6, 1, tzinfo=UTC),
        )
        async with EventStore() as store:
            await store.upsert(r1)
            await store.upsert(r2)
            await store.upsert(r3)

            results = await store.list_by_year("2026")
            assert len(results) == 2
            # Should be sorted by event_date ASC
            assert results[0].subject == "Jan Event"
            assert results[1].subject == "Mar Event"

    async def test_list_recent(self):
        for i in range(5):
            rec = _make_record(
                subject=f"Event {i}",
                event_date=datetime(2026, 1, i + 1, tzinfo=UTC),
            )
            async with EventStore() as store:
                await store.upsert(rec)

        async with EventStore() as store:
            results = await store.list_recent(max_items=3)
            assert len(results) == 3
            # Newest first
            assert results[0].event_date > results[1].event_date

    async def test_delete(self):
        rec = _make_record()
        async with EventStore() as store:
            await store.upsert(rec)
            assert await store.get_by_id(rec.id) is not None

            await store.delete(rec.id, rec.year)
            assert await store.get_by_id(rec.id) is None

    async def test_upsert_updates_existing(self):
        rec = _make_record()
        async with EventStore() as store:
            await store.upsert(rec)
            rec.subject = "Updated Subject"
            await store.upsert(rec)

            fetched = await store.get_by_id(rec.id)
            assert fetched.subject == "Updated Subject"


# ===========================================================================
# Route tests
# ===========================================================================


class TestEventRoutes:
    """Test event HTTP route handlers."""

    @pytest.fixture(autouse=True)
    def _patch_auth(self, monkeypatch):
        monkeypatch.setattr("sjifire.ops.events.routes.get_request_user", _fake_get_user)
        monkeypatch.setattr("sjifire.ops.events.routes._is_manager", AsyncMock(return_value=True))
        monkeypatch.setattr("sjifire.ops.events.routes._get_event_managers_group_id", lambda: "grp")

    async def test_create_record(self):
        from sjifire.ops.events.routes import create_record

        request = _make_request(
            json={
                "subject": "Hose Training",
                "start": "2026-04-01T09:00:00Z",
            }
        )
        resp = await create_record(request)
        assert resp.status_code == 201

    async def test_create_record_duplicate_calendar_event_returns_existing(self):
        """Duplicate calendar_event_id returns existing record (idempotent)."""
        from sjifire.ops.events.routes import create_record

        rec = _make_record(calendar_event_id="cal-dup")
        async with EventStore() as store:
            await store.upsert(rec)

        request = _make_request(
            json={
                "calendar_event_id": "cal-dup",
                "subject": "Dup",
                "start": "2026-04-01T09:00:00Z",
            }
        )
        resp = await create_record(request)
        assert resp.status_code == 200  # Returns existing, not 201

    async def test_get_record(self):
        from sjifire.ops.events.routes import get_record

        rec = _make_record()
        async with EventStore() as store:
            await store.upsert(rec)

        request = _make_request(path_params={"record_id": rec.id})
        resp = await get_record(request)
        assert resp.status_code == 200

    async def test_get_record_not_found(self):
        from sjifire.ops.events.routes import get_record

        request = _make_request(path_params={"record_id": "nonexistent"})
        resp = await get_record(request)
        assert resp.status_code == 404

    async def test_update_record_attendees(self):
        from sjifire.ops.events.routes import update_record

        rec = _make_record()
        async with EventStore() as store:
            await store.upsert(rec)

        request = _make_request(
            path_params={"record_id": rec.id},
            json={"attendees": [{"name": "Jane Doe", "email": "jdoe@sjifire.org"}]},
        )
        resp = await update_record(request)
        assert resp.status_code == 200

        async with EventStore() as store:
            updated = await store.get_by_id(rec.id)
            assert len(updated.attendees) == 1
            assert updated.attendees[0].name == "Jane Doe"

    async def test_update_record_notes(self):
        from sjifire.ops.events.routes import update_record

        rec = _make_record()
        async with EventStore() as store:
            await store.upsert(rec)

        request = _make_request(
            path_params={"record_id": rec.id},
            json={"notes": "Good session"},
        )
        resp = await update_record(request)
        assert resp.status_code == 200

        async with EventStore() as store:
            updated = await store.get_by_id(rec.id)
            assert updated.notes == "Good session"

    async def test_upload_file(self):
        from sjifire.ops.events.routes import upload_file

        rec = _make_record()
        async with EventStore() as store:
            await store.upsert(rec)

        request = _make_form_request(
            path_params={"record_id": rec.id},
            filename="sign-in.jpg",
            content=b"\xff\xd8\xff\xe0" + b"\x00" * 100,
            content_type="image/jpeg",
        )
        resp = await upload_file(request)
        assert resp.status_code == 201

        async with EventStore() as store:
            updated = await store.get_by_id(rec.id)
            assert len(updated.attachments) == 1
            assert updated.attachments[0].filename == "sign-in.jpg"

    async def test_upload_file_invalid_type(self):
        from sjifire.ops.events.routes import upload_file

        rec = _make_record()
        async with EventStore() as store:
            await store.upsert(rec)

        request = _make_form_request(
            path_params={"record_id": rec.id},
            filename="data.csv",
            content=b"a,b,c",
            content_type="text/csv",
        )
        resp = await upload_file(request)
        assert resp.status_code == 400

    async def test_download_attachment(self):
        from sjifire.ops.events.routes import download_attachment

        rec = _make_record()
        att = EventAttachmentMeta(
            filename="photo.jpg",
            content_type="image/jpeg",
            size_bytes=100,
            blob_path="events/2026/rec/att-photo.jpg",
            uploaded_by="test@sjifire.org",
        )
        rec.attachments.append(att)

        async with EventStore() as store:
            await store.upsert(rec)

        # Seed blob
        AttachmentBlobStore._memory[att.blob_path] = (b"\xff" * 100, "image/jpeg")

        request = _make_request(path_params={"record_id": rec.id, "att_id": att.id})
        resp = await download_attachment(request)
        assert resp.status_code == 200
        assert resp.media_type == "image/jpeg"

    async def test_download_attachment_not_found(self):
        from sjifire.ops.events.routes import download_attachment

        rec = _make_record()
        async with EventStore() as store:
            await store.upsert(rec)

        request = _make_request(path_params={"record_id": rec.id, "att_id": "missing"})
        resp = await download_attachment(request)
        assert resp.status_code == 404

    async def test_delete_attachment(self):
        from sjifire.ops.events.routes import delete_attachment

        rec = _make_record()
        att = EventAttachmentMeta(
            filename="old.pdf",
            content_type="application/pdf",
            size_bytes=50,
            blob_path="events/2026/rec/att-old.pdf",
            uploaded_by="test@sjifire.org",
        )
        rec.attachments.append(att)

        async with EventStore() as store:
            await store.upsert(rec)

        AttachmentBlobStore._memory[att.blob_path] = (b"\x00" * 50, "application/pdf")

        request = _make_request(path_params={"record_id": rec.id, "att_id": att.id})
        resp = await delete_attachment(request)
        assert resp.status_code == 200

        async with EventStore() as store:
            updated = await store.get_by_id(rec.id)
            assert len(updated.attachments) == 0

    async def test_unauthenticated_returns_401(self, monkeypatch):
        from sjifire.ops.events.routes import create_record

        monkeypatch.setattr("sjifire.ops.events.routes.get_request_user", _fake_get_user_none)
        request = _make_request(json={"subject": "X", "start": "2026-04-01T09:00:00Z"})
        resp = await create_record(request)
        assert resp.status_code == 401

    async def test_non_manager_returns_403(self, monkeypatch):
        from sjifire.ops.events.routes import create_record

        monkeypatch.setattr("sjifire.ops.events.routes._is_manager", AsyncMock(return_value=False))
        request = _make_request(json={"subject": "X", "start": "2026-04-01T09:00:00Z"})
        resp = await create_record(request)
        assert resp.status_code == 403


# ===========================================================================
# Parser tests
# ===========================================================================


class TestParser:
    async def test_parse_json_response_strips_fences(self):
        from sjifire.ops.events.parser import _parse_json_response

        raw = '```json\n[{"name": "John Smith"}]\n```'
        result = _parse_json_response(raw)
        assert len(result) == 1
        assert result[0]["name"] == "John Smith"

    async def test_parse_json_response_plain(self):
        from sjifire.ops.events.parser import _parse_json_response

        raw = '[{"name": "Jane Doe"}]'
        result = _parse_json_response(raw)
        assert len(result) == 1

    async def test_parse_json_response_invalid(self):
        from sjifire.ops.events.parser import _parse_json_response

        result = _parse_json_response("not json at all")
        assert result == []

    async def test_normalize_name(self):
        from sjifire.ops.events.parser import _normalize_name

        assert _normalize_name("Chief John Smith") == "john smith"
        assert _normalize_name("FF Jane Doe") == "jane doe"
        assert _normalize_name("  Bob Jones  ") == "bob jones"

    async def test_match_against_roster(self):
        from sjifire.ops.events.parser import _match_against_roster

        roster = [
            {"name": "John Smith", "email": "jsmith@sjifire.org"},
            {"name": "Jane Doe", "email": "jdoe@sjifire.org"},
        ]
        parsed = [{"name": "John Smith"}, {"name": "Unknown Person"}]

        with patch("sjifire.ops.personnel.tools.get_personnel", AsyncMock(return_value=roster)):
            results = await _match_against_roster(parsed)

        matched = [r for r in results if r.get("email")]
        unmatched = [r for r in results if not r.get("email")]
        assert len(matched) == 1
        assert matched[0]["email"] == "jsmith@sjifire.org"
        assert len(unmatched) == 1


# ===========================================================================
# Helpers for fake requests
# ===========================================================================


class _FakeRequest:
    """Minimal Starlette Request stand-in for route tests."""

    def __init__(self, path_params=None, json_data=None, form_data=None, headers=None):
        self.path_params = path_params or {}
        self._json = json_data
        self._form = form_data
        self.headers = headers or {}

    async def json(self):
        return self._json or {}

    async def form(self):
        return self._form or {}


class _FakeUploadFile:
    def __init__(self, filename, content, content_type):
        self.filename = filename
        self._content = content
        self.content_type = content_type

    async def read(self):
        return self._content


def _make_request(*, path_params=None, json=None, headers=None):
    return _FakeRequest(path_params=path_params, json_data=json, headers=headers)


def _make_form_request(
    *, path_params=None, filename="file.jpg", content=b"", content_type="image/jpeg"
):
    upload = _FakeUploadFile(filename, content, content_type)
    return _FakeRequest(path_params=path_params, form_data={"file": upload})
