"""Tests for AttachmentBlobStore in-memory mode."""

import pytest

from sjifire.ops.attachments.store import AttachmentBlobStore


@pytest.fixture(autouse=True)
def _clear_memory():
    """Reset in-memory store between tests."""
    AttachmentBlobStore._memory.clear()
    yield
    AttachmentBlobStore._memory.clear()


@pytest.fixture(autouse=True)
def _no_azure(monkeypatch):
    """Ensure in-memory mode (no Azure env vars)."""
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_KEY", raising=False)


class TestUpload:
    async def test_upload_stores_bytes(self):
        data = b"fake image content"
        async with AttachmentBlobStore() as store:
            path = await store.upload("test/photo.jpg", data, "image/jpeg")
        assert path == "test/photo.jpg"
        assert AttachmentBlobStore._memory["test/photo.jpg"] == (data, "image/jpeg")

    async def test_upload_returns_path(self):
        async with AttachmentBlobStore() as store:
            result = await store.upload("a/b/c.png", b"png", "image/png")
        assert result == "a/b/c.png"


class TestDownload:
    async def test_download_returns_data_and_content_type(self):
        data = b"pdf content"
        async with AttachmentBlobStore() as store:
            await store.upload("doc.pdf", data, "application/pdf")
            downloaded, ct = await store.download("doc.pdf")
        assert downloaded == data
        assert ct == "application/pdf"

    async def test_download_not_found_raises(self):
        async with AttachmentBlobStore() as store:
            with pytest.raises(FileNotFoundError, match="Blob not found"):
                await store.download("nonexistent/path")


class TestDelete:
    async def test_delete_removes_blob(self):
        async with AttachmentBlobStore() as store:
            await store.upload("to-delete.jpg", b"data", "image/jpeg")
            await store.delete("to-delete.jpg")
        assert "to-delete.jpg" not in AttachmentBlobStore._memory

    async def test_delete_nonexistent_no_error(self):
        async with AttachmentBlobStore() as store:
            await store.delete("does-not-exist")  # Should not raise


class TestGenerateDownloadUrl:
    async def test_in_memory_url(self):
        async with AttachmentBlobStore() as store:
            await store.upload("test.jpg", b"data", "image/jpeg")
            url = await store.generate_download_url("test.jpg")
        assert url == "memory://test.jpg"


class TestContextManager:
    async def test_enters_in_memory_mode(self):
        store = AttachmentBlobStore()
        async with store as s:
            assert s._in_memory is True
            assert s._container_client is None
