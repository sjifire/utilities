"""Tests for attachment MCP tools with access control."""

import base64
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from sjifire.ops.attachments.store import AttachmentBlobStore
from sjifire.ops.attachments.tools import (
    _check_edit_access,
    delete_attachment,
    get_attachment,
    list_attachments,
    upload_attachment,
)
from sjifire.ops.auth import UserContext, set_current_user
from sjifire.ops.incidents.models import (
    IncidentDocument,
    PersonnelAssignment,
    UnitAssignment,
)

# -- Fixtures ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _editor_group_env():
    """Set the editor group ID for all tests."""
    import sjifire.ops.auth

    sjifire.ops.auth._EDITOR_GROUP_ID = None
    with patch.dict(os.environ, {"ENTRA_REPORT_EDITORS_GROUP_ID": "officer-group"}):
        yield
    sjifire.ops.auth._EDITOR_GROUP_ID = None


@pytest.fixture(autouse=True)
def _clear_blob_memory():
    AttachmentBlobStore._memory.clear()
    yield
    AttachmentBlobStore._memory.clear()


@pytest.fixture(autouse=True)
def _no_azure(monkeypatch):
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_KEY", raising=False)
    monkeypatch.delenv("COSMOS_ENDPOINT", raising=False)
    monkeypatch.delenv("COSMOS_KEY", raising=False)


@pytest.fixture
def regular_user():
    user = UserContext(
        email="ff@sjifire.org", name="Firefighter", user_id="user-1", groups=frozenset()
    )
    set_current_user(user)
    return user


@pytest.fixture
def officer_user():
    user = UserContext(
        email="chief@sjifire.org",
        name="Chief",
        user_id="user-2",
        groups=frozenset(["officer-group"]),
    )
    set_current_user(user)
    return user


@pytest.fixture
def sample_doc():
    return IncidentDocument(
        id="doc-123",
        incident_number="26-000944",
        incident_datetime=datetime(2026, 2, 12, tzinfo=UTC),
        created_by="ff@sjifire.org",
        extras={"station": "S31"},
        units=[
            UnitAssignment(
                unit_id="E31",
                personnel=[
                    PersonnelAssignment(name="Crew 1", email="crew1@sjifire.org", position="FF")
                ],
            ),
        ],
    )


SMALL_JPEG_B64 = base64.b64encode(b"fake jpeg data").decode()


def _mock_store(doc):
    """Build a mocked IncidentStore context manager."""
    mock = AsyncMock()
    mock.get_by_id = AsyncMock(return_value=doc)
    mock.update = AsyncMock(side_effect=lambda d: d)

    cls = AsyncMock()
    cls.return_value.__aenter__ = AsyncMock(return_value=mock)
    cls.return_value.__aexit__ = AsyncMock(return_value=None)
    return cls, mock


# -- Edit access helper ------------------------------------------------------


class TestCheckEditAccess:
    def test_creator_can_edit(self, sample_doc):
        assert _check_edit_access(sample_doc, "ff@sjifire.org", is_editor=False)

    def test_editor_can_edit(self, sample_doc):
        assert _check_edit_access(sample_doc, "random@sjifire.org", is_editor=True)

    def test_crew_cannot_edit(self, sample_doc):
        assert not _check_edit_access(sample_doc, "crew1@sjifire.org", is_editor=False)

    def test_stranger_cannot_edit(self, sample_doc):
        assert not _check_edit_access(sample_doc, "stranger@sjifire.org", is_editor=False)


# -- Upload ------------------------------------------------------------------


class TestUploadAttachment:
    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_upload_success(self, mock_store_cls, regular_user, sample_doc):
        mock_store_cls, _mock = _mock_store(sample_doc)

        with patch("sjifire.ops.attachments.tools.IncidentStore", mock_store_cls):
            result = await upload_attachment(
                incident_id="doc-123",
                filename="scene.jpg",
                data_base64=SMALL_JPEG_B64,
                content_type="image/jpeg",
                title="Front of structure",
            )

        assert "error" not in result
        assert result["filename"] == "scene.jpg"
        assert result["title"] == "Front of structure"
        assert result["content_type"] == "image/jpeg"
        assert result["attachment_count"] == 1
        # Blob was stored
        assert len(AttachmentBlobStore._memory) == 1

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_upload_without_title(self, mock_store_cls, regular_user, sample_doc):
        mock_store_cls, _ = _mock_store(sample_doc)

        with patch("sjifire.ops.attachments.tools.IncidentStore", mock_store_cls):
            result = await upload_attachment(
                incident_id="doc-123",
                filename="chat-photo-1.jpg",
                data_base64=SMALL_JPEG_B64,
            )

        assert "error" not in result
        assert result["title"] == ""

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_upload_for_parsing_returns_image_data(
        self, mock_store_cls, regular_user, sample_doc
    ):
        mock_store_cls, _ = _mock_store(sample_doc)

        with patch("sjifire.ops.attachments.tools.IncidentStore", mock_store_cls):
            result = await upload_attachment(
                incident_id="doc-123",
                filename="run-sheet.jpg",
                data_base64=SMALL_JPEG_B64,
                for_parsing=True,
            )

        assert "error" not in result
        assert "image_data" in result
        assert result["image_data"]["base64"] == SMALL_JPEG_B64
        assert result["image_data"]["media_type"] == "image/jpeg"

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_for_parsing_pdf_no_image_data(self, mock_store_cls, regular_user, sample_doc):
        """PDFs don't get image_data even with for_parsing=True."""
        mock_store_cls, _ = _mock_store(sample_doc)
        pdf_b64 = base64.b64encode(b"fake pdf").decode()

        with patch("sjifire.ops.attachments.tools.IncidentStore", mock_store_cls):
            result = await upload_attachment(
                incident_id="doc-123",
                filename="report.pdf",
                data_base64=pdf_b64,
                content_type="application/pdf",
                for_parsing=True,
            )

        assert "error" not in result
        assert "image_data" not in result

    async def test_rejects_invalid_content_type(self, regular_user):
        result = await upload_attachment(
            incident_id="doc-123",
            filename="test.bmp",
            data_base64=SMALL_JPEG_B64,
            content_type="image/bmp",
        )
        assert "error" in result
        assert "not allowed" in result["error"]

    async def test_rejects_invalid_base64(self, regular_user):
        result = await upload_attachment(
            incident_id="doc-123",
            filename="test.jpg",
            data_base64="not-valid-base64!!!",
            content_type="image/jpeg",
        )
        assert "error" in result
        assert "base64" in result["error"].lower()

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_rejects_oversized_file(self, mock_store_cls, regular_user, sample_doc):
        mock_store_cls, _ = _mock_store(sample_doc)
        # 21 MB of data
        big_data = base64.b64encode(b"x" * (21 * 1024 * 1024)).decode()

        with patch("sjifire.ops.attachments.tools.IncidentStore", mock_store_cls):
            result = await upload_attachment(
                incident_id="doc-123",
                filename="huge.jpg",
                data_base64=big_data,
                content_type="image/jpeg",
            )

        assert "error" in result
        assert "too large" in result["error"].lower()

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_rejects_not_found_incident(self, mock_store_cls, regular_user):
        cls, _ = _mock_store(None)
        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await upload_attachment(
                incident_id="nonexistent",
                filename="test.jpg",
                data_base64=SMALL_JPEG_B64,
            )

        assert "error" in result
        assert "not found" in result["error"].lower()

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_rejects_submitted_incident(self, mock_store_cls, regular_user, sample_doc):
        sample_doc.status = "submitted"
        cls, _ = _mock_store(sample_doc)

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await upload_attachment(
                incident_id="doc-123",
                filename="test.jpg",
                data_base64=SMALL_JPEG_B64,
            )

        assert "error" in result
        assert "submitted" in result["error"].lower()

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_stranger_cannot_upload(self, mock_store_cls, sample_doc):
        stranger = UserContext(email="stranger@sjifire.org", name="X", user_id="x")
        set_current_user(stranger)

        cls, _ = _mock_store(sample_doc)
        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await upload_attachment(
                incident_id="doc-123",
                filename="test.jpg",
                data_base64=SMALL_JPEG_B64,
            )

        assert "error" in result
        assert "permission" in result["error"].lower()

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_officer_can_upload_to_others_incident(
        self, mock_store_cls, officer_user, sample_doc
    ):
        cls, _ = _mock_store(sample_doc)

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await upload_attachment(
                incident_id="doc-123",
                filename="test.jpg",
                data_base64=SMALL_JPEG_B64,
            )

        assert "error" not in result

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_rejects_when_max_attachments_reached(
        self, mock_store_cls, regular_user, sample_doc
    ):
        from sjifire.ops.attachments.models import MAX_ATTACHMENTS, AttachmentMeta

        sample_doc.attachments = [
            AttachmentMeta(
                filename=f"file-{i}.jpg",
                content_type="image/jpeg",
                uploaded_by="ff@sjifire.org",
            )
            for i in range(MAX_ATTACHMENTS)
        ]
        cls, _ = _mock_store(sample_doc)

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await upload_attachment(
                incident_id="doc-123",
                filename="one-too-many.jpg",
                data_base64=SMALL_JPEG_B64,
            )

        assert "error" in result
        assert "Maximum" in result["error"]


# -- List --------------------------------------------------------------------


class TestListAttachments:
    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_creator_lists(self, mock_store_cls, regular_user, sample_doc):
        from sjifire.ops.attachments.models import AttachmentMeta

        sample_doc.attachments = [
            AttachmentMeta(
                filename="photo.jpg", content_type="image/jpeg", uploaded_by="ff@sjifire.org"
            ),
        ]
        cls, _ = _mock_store(sample_doc)

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await list_attachments("doc-123")

        assert result["count"] == 1
        assert result["attachments"][0]["filename"] == "photo.jpg"

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_crew_can_list(self, mock_store_cls, sample_doc):
        crew = UserContext(email="crew1@sjifire.org", name="Crew", user_id="c1")
        set_current_user(crew)
        cls, _ = _mock_store(sample_doc)

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await list_attachments("doc-123")

        assert "error" not in result

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_stranger_denied(self, mock_store_cls, sample_doc):
        stranger = UserContext(email="stranger@sjifire.org", name="X", user_id="x")
        set_current_user(stranger)
        cls, _ = _mock_store(sample_doc)

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await list_attachments("doc-123")

        assert "error" in result
        assert "access" in result["error"].lower()

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_empty_list(self, mock_store_cls, regular_user, sample_doc):
        cls, _ = _mock_store(sample_doc)

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await list_attachments("doc-123")

        assert result["count"] == 0
        assert result["attachments"] == []

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_not_found(self, mock_store_cls, regular_user):
        cls, _ = _mock_store(None)
        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await list_attachments("nonexistent")

        assert "error" in result
        assert "not found" in result["error"].lower()


# -- Get ---------------------------------------------------------------------


class TestGetAttachment:
    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_get_metadata(self, mock_store_cls, regular_user, sample_doc):
        from sjifire.ops.attachments.models import AttachmentMeta

        meta = AttachmentMeta(
            filename="photo.jpg",
            content_type="image/jpeg",
            uploaded_by="ff@sjifire.org",
            blob_path="incidents/2026/doc-123/att-1-photo.jpg",
        )
        sample_doc.attachments = [meta]
        cls, _ = _mock_store(sample_doc)

        # Put data in blob store for download URL
        AttachmentBlobStore._memory[meta.blob_path] = (b"data", "image/jpeg")

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await get_attachment("doc-123", meta.id)

        assert "error" not in result
        assert result["filename"] == "photo.jpg"
        assert "download_url" in result
        assert "image_data" not in result  # include_data defaults to False

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_get_with_image_data(self, mock_store_cls, regular_user, sample_doc):
        from sjifire.ops.attachments.models import AttachmentMeta

        meta = AttachmentMeta(
            filename="photo.jpg",
            content_type="image/jpeg",
            uploaded_by="ff@sjifire.org",
            blob_path="incidents/2026/doc-123/att-1-photo.jpg",
        )
        sample_doc.attachments = [meta]
        cls, _ = _mock_store(sample_doc)

        AttachmentBlobStore._memory[meta.blob_path] = (b"jpeg data", "image/jpeg")

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await get_attachment("doc-123", meta.id, include_data=True)

        assert "image_data" in result
        assert result["image_data"]["media_type"] == "image/jpeg"

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_attachment_not_found(self, mock_store_cls, regular_user, sample_doc):
        cls, _ = _mock_store(sample_doc)

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await get_attachment("doc-123", "nonexistent-att-id")

        assert "error" in result
        assert "not found" in result["error"].lower()

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_stranger_denied(self, mock_store_cls, sample_doc):
        stranger = UserContext(email="stranger@sjifire.org", name="X", user_id="x")
        set_current_user(stranger)
        cls, _ = _mock_store(sample_doc)

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await get_attachment("doc-123", "any-id")

        assert "error" in result
        assert "access" in result["error"].lower()


# -- Delete ------------------------------------------------------------------


class TestDeleteAttachment:
    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_creator_deletes(self, mock_store_cls, regular_user, sample_doc):
        from sjifire.ops.attachments.models import AttachmentMeta

        meta = AttachmentMeta(
            filename="photo.jpg",
            content_type="image/jpeg",
            uploaded_by="ff@sjifire.org",
            blob_path="incidents/2026/doc-123/att-1-photo.jpg",
        )
        sample_doc.attachments = [meta]
        cls, _ = _mock_store(sample_doc)

        # Put data in blob store
        AttachmentBlobStore._memory[meta.blob_path] = (b"data", "image/jpeg")

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await delete_attachment("doc-123", meta.id)

        assert "error" not in result
        assert result["deleted"] == meta.id
        assert result["filename"] == "photo.jpg"
        assert result["attachment_count"] == 0
        # Blob deleted
        assert meta.blob_path not in AttachmentBlobStore._memory

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_officer_deletes_others(self, mock_store_cls, officer_user, sample_doc):
        from sjifire.ops.attachments.models import AttachmentMeta

        meta = AttachmentMeta(
            filename="photo.jpg",
            content_type="image/jpeg",
            uploaded_by="ff@sjifire.org",
            blob_path="incidents/2026/doc-123/att-1-photo.jpg",
        )
        sample_doc.attachments = [meta]
        cls, _ = _mock_store(sample_doc)

        AttachmentBlobStore._memory[meta.blob_path] = (b"data", "image/jpeg")

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await delete_attachment("doc-123", meta.id)

        assert "error" not in result

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_stranger_cannot_delete(self, mock_store_cls, sample_doc):
        from sjifire.ops.attachments.models import AttachmentMeta

        stranger = UserContext(email="stranger@sjifire.org", name="X", user_id="x")
        set_current_user(stranger)

        meta = AttachmentMeta(
            filename="photo.jpg",
            content_type="image/jpeg",
            uploaded_by="ff@sjifire.org",
        )
        sample_doc.attachments = [meta]
        cls, _ = _mock_store(sample_doc)

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await delete_attachment("doc-123", meta.id)

        assert "error" in result
        assert "permission" in result["error"].lower()

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_cannot_delete_from_submitted(self, mock_store_cls, regular_user, sample_doc):
        from sjifire.ops.attachments.models import AttachmentMeta

        sample_doc.status = "submitted"
        meta = AttachmentMeta(
            filename="photo.jpg",
            content_type="image/jpeg",
            uploaded_by="ff@sjifire.org",
        )
        sample_doc.attachments = [meta]
        cls, _ = _mock_store(sample_doc)

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await delete_attachment("doc-123", meta.id)

        assert "error" in result
        assert "submitted" in result["error"].lower()

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_attachment_not_found(self, mock_store_cls, regular_user, sample_doc):
        cls, _ = _mock_store(sample_doc)

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await delete_attachment("doc-123", "nonexistent-att")

        assert "error" in result
        assert "not found" in result["error"].lower()

    @patch("sjifire.ops.attachments.tools.IncidentStore")
    async def test_incident_not_found(self, mock_store_cls, regular_user):
        cls, _ = _mock_store(None)

        with patch("sjifire.ops.attachments.tools.IncidentStore", cls):
            result = await delete_attachment("nonexistent", "att-1")

        assert "error" in result
        assert "not found" in result["error"].lower()
