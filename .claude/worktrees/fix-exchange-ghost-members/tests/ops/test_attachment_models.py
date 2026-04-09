"""Tests for attachment models and helpers."""

from sjifire.ops.attachments.models import (
    ALLOWED_CONTENT_TYPES,
    MAX_FILE_SIZE,
    AttachmentMeta,
    build_blob_path,
)


class TestBuildBlobPath:
    def test_standard_path(self):
        path = build_blob_path("2026", "inc-123", "att-456", "photo.jpg")
        assert path == "incidents/2026/inc-123/att-456-photo.jpg"

    def test_sanitizes_forward_slashes(self):
        path = build_blob_path("2026", "inc-1", "att-1", "sub/dir/file.png")
        assert path == "incidents/2026/inc-1/att-1-sub_dir_file.png"

    def test_sanitizes_backslashes(self):
        path = build_blob_path("2026", "inc-1", "att-1", "path\\to\\file.pdf")
        assert path == "incidents/2026/inc-1/att-1-path_to_file.pdf"


class TestAttachmentMeta:
    def test_defaults(self):
        meta = AttachmentMeta(
            filename="test.jpg",
            content_type="image/jpeg",
            uploaded_by="user@sjifire.org",
        )
        assert meta.title == ""
        assert meta.description == ""
        assert meta.size_bytes == 0
        assert meta.blob_path == ""
        assert meta.id  # UUID generated

    def test_email_normalized(self):
        meta = AttachmentMeta(
            filename="test.jpg",
            content_type="image/jpeg",
            uploaded_by="User@SJIFire.ORG",
        )
        assert meta.uploaded_by == "user@sjifire.org"

    def test_with_all_fields(self):
        meta = AttachmentMeta(
            filename="scene-photo.jpg",
            title="Front of structure",
            description="Heavy smoke from C side",
            content_type="image/jpeg",
            size_bytes=1024,
            blob_path="incidents/2026/inc-1/att-1-scene-photo.jpg",
            uploaded_by="ff@sjifire.org",
        )
        assert meta.title == "Front of structure"
        assert meta.description == "Heavy smoke from C side"
        assert meta.size_bytes == 1024


class TestConstants:
    def test_allowed_types_include_common_formats(self):
        assert "image/jpeg" in ALLOWED_CONTENT_TYPES
        assert "image/png" in ALLOWED_CONTENT_TYPES
        assert "image/webp" in ALLOWED_CONTENT_TYPES
        assert "application/pdf" in ALLOWED_CONTENT_TYPES

    def test_max_file_size_is_20mb(self):
        assert MAX_FILE_SIZE == 20 * 1024 * 1024
