"""Azure Blob Storage operations for incident attachments.

Uploads, downloads, and deletes attachment blobs. Falls back to
in-memory storage when ``AZURE_STORAGE_ACCOUNT_URL`` is not set,
so ``mcp dev`` works without Azure infrastructure.

Blob container: ``attachments``
Blob path layout: ``incidents/{year}/{incident_id}/{attachment_id}-{filename}``
"""

import logging
import os
from typing import ClassVar, Self

logger = logging.getLogger(__name__)

CONTAINER_NAME = "attachments"


class AttachmentBlobStore:
    """Async blob operations for incident attachments.

    Falls back to in-memory storage when Azure Storage is not configured.

    Usage::

        async with AttachmentBlobStore() as store:
            await store.upload(blob_path, data, content_type)
            data = await store.download(blob_path)
    """

    _memory: ClassVar[dict[str, tuple[bytes, str]]] = {}

    def __init__(self) -> None:
        """Initialize store. Call ``__aenter__`` to connect."""
        self._container_client = None
        self._in_memory = False

    async def __aenter__(self) -> Self:
        """Get a container client for the attachments container."""
        account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL")

        if not account_url:
            self._in_memory = True
            logger.debug("No AZURE_STORAGE_ACCOUNT_URL — using in-memory blob store")
            return self

        account_key = os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
        if account_key:
            from azure.storage.blob.aio import BlobServiceClient

            service = BlobServiceClient(account_url, credential=account_key)
        else:
            from azure.identity.aio import DefaultAzureCredential
            from azure.storage.blob.aio import BlobServiceClient

            service = BlobServiceClient(account_url, credential=DefaultAzureCredential())

        self._container_client = service.get_container_client(CONTAINER_NAME)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Close the container client if open."""
        if self._container_client is not None:
            await self._container_client.close()
            self._container_client = None

    async def upload(self, blob_path: str, data: bytes, content_type: str) -> str:
        """Upload a blob and return its path.

        Args:
            blob_path: Target path within the container
            data: Raw file bytes
            content_type: MIME type (e.g. ``image/jpeg``)

        Returns:
            The blob_path (same as input, for convenience)
        """
        if self._in_memory:
            self._memory[blob_path] = (data, content_type)
            logger.info("Uploaded blob %s (in-memory, %d bytes)", blob_path, len(data))
            return blob_path

        from azure.storage.blob import ContentSettings

        blob = self._container_client.get_blob_client(blob_path)
        await blob.upload_blob(
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
        logger.info("Uploaded blob %s (%d bytes)", blob_path, len(data))
        return blob_path

    async def download(self, blob_path: str) -> tuple[bytes, str]:
        """Download a blob's content and content type.

        Args:
            blob_path: Blob path within the container

        Returns:
            Tuple of (data_bytes, content_type)

        Raises:
            FileNotFoundError: If the blob does not exist
        """
        if self._in_memory:
            entry = self._memory.get(blob_path)
            if entry is None:
                raise FileNotFoundError(f"Blob not found: {blob_path}")
            return entry

        blob = self._container_client.get_blob_client(blob_path)
        try:
            stream = await blob.download_blob()
            data = await stream.readall()
            props = await blob.get_blob_properties()
            ct = props.content_settings.content_type or "application/octet-stream"
            return data, ct
        except Exception as exc:
            if "BlobNotFound" in str(exc):
                raise FileNotFoundError(f"Blob not found: {blob_path}") from exc
            raise

    async def delete(self, blob_path: str) -> None:
        """Delete a blob.

        Args:
            blob_path: Blob path within the container

        No error is raised if the blob doesn't exist.
        """
        if self._in_memory:
            self._memory.pop(blob_path, None)
            logger.info("Deleted blob %s (in-memory)", blob_path)
            return

        blob = self._container_client.get_blob_client(blob_path)
        await blob.delete_blob(delete_snapshots="include")
        logger.info("Deleted blob %s", blob_path)

    async def generate_download_url(self, blob_path: str, expires_hours: int = 1) -> str:
        """Generate a time-limited SAS download URL for a blob.

        Args:
            blob_path: Blob path within the container
            expires_hours: Hours until the URL expires (default 1)

        Returns:
            A full URL with SAS token for direct download
        """
        if self._in_memory:
            return f"memory://{blob_path}"

        from datetime import UTC, datetime, timedelta

        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        blob = self._container_client.get_blob_client(blob_path)
        account_name = self._container_client.account_name

        # Use account key if available, otherwise use user delegation key
        account_key = os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
        if account_key:
            sas_token = generate_blob_sas(
                account_name=account_name,
                container_name=CONTAINER_NAME,
                blob_name=blob_path,
                account_key=account_key,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.now(UTC) + timedelta(hours=expires_hours),
            )
        else:
            from azure.identity.aio import DefaultAzureCredential

            credential = DefaultAzureCredential()
            from azure.storage.blob.aio import BlobServiceClient

            async with BlobServiceClient(
                f"https://{account_name}.blob.core.windows.net",
                credential=credential,
            ) as svc:
                start = datetime.now(UTC)
                expiry = start + timedelta(hours=expires_hours)
                delegation_key = await svc.get_user_delegation_key(start, expiry)
                sas_token = generate_blob_sas(
                    account_name=account_name,
                    container_name=CONTAINER_NAME,
                    blob_name=blob_path,
                    user_delegation_key=delegation_key,
                    permission=BlobSasPermissions(read=True),
                    expiry=expiry,
                )

        return f"{blob.url}?{sas_token}"
