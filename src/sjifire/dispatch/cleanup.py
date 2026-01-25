"""Archive cleanup logic for deleting old emails."""

import logging
from datetime import UTC, datetime, timedelta

from msgraph import GraphServiceClient
from msgraph.generated.users.item.mail_folders.item.messages.messages_request_builder import (
    MessagesRequestBuilder,
)

from sjifire.core.config import DispatchConfig
from sjifire.dispatch.processor import get_or_create_archive_folder

logger = logging.getLogger(__name__)


async def cleanup_old_emails(client: GraphServiceClient, config: DispatchConfig) -> dict:
    """Delete emails older than retention period from archive folder.

    Args:
        client: Authenticated Graph client
        config: Dispatch configuration with retention_days

    Returns:
        Dict with cleanup result details
    """
    user_id = config.mailbox_user_id
    retention_days = config.retention_days

    cutoff_date = datetime.now(UTC) - timedelta(days=retention_days)
    cutoff_str = cutoff_date.strftime("%Y-%m-%dT%H:%M:%SZ")

    logger.info(f"Starting cleanup for emails older than {cutoff_str} ({retention_days} days)")

    archive_folder_id = await get_or_create_archive_folder(client, user_id, config.archive_folder)

    query_params = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(
        filter=f"receivedDateTime lt {cutoff_str}",
        select=["id", "subject", "receivedDateTime"],
        top=100,
    )
    request_config = MessagesRequestBuilder.MessagesRequestBuilderGetRequestConfiguration(
        query_parameters=query_params,
    )

    messages = (
        await client.users.by_user_id(user_id)
        .mail_folders.by_mail_folder_id(archive_folder_id)
        .messages.get(request_configuration=request_config)
    )

    deleted_count = 0
    deleted_ids = []

    if messages and messages.value:
        for message in messages.value:
            if not message.id:
                logger.warning("Skipping message with no ID")
                continue

            try:
                await client.users.by_user_id(user_id).messages.by_message_id(message.id).delete()
                deleted_count += 1
                deleted_ids.append(message.id)
                logger.info(f"Deleted message {message.id}: {message.subject}")
            except Exception as e:
                logger.error(f"Failed to delete message {message.id}: {e}")

    logger.info(f"Cleanup complete: deleted {deleted_count} emails")

    return {
        "status": "completed",
        "deleted_count": deleted_count,
        "deleted_ids": deleted_ids,
        "cutoff_date": cutoff_str,
    }
