"""Email processing logic for dispatch emails."""

import logging

from msgraph import GraphServiceClient
from msgraph.generated.models.message import Message

from sjifire.core.config import DispatchConfig

logger = logging.getLogger(__name__)


async def get_or_create_archive_folder(
    client: GraphServiceClient, user_id: str, folder_name: str
) -> str:
    """Get or create the archive folder in the mailbox.

    Args:
        client: Authenticated Graph client
        user_id: Mailbox user ID
        folder_name: Name of the archive folder

    Returns:
        Folder ID of the archive folder

    Raises:
        RuntimeError: If folder creation fails or folder ID is not available
    """
    mail_folders = await client.users.by_user_id(user_id).mail_folders.get()

    if mail_folders and mail_folders.value:
        for folder in mail_folders.value:
            if folder.display_name == folder_name and folder.id:
                logger.info(f"Found existing archive folder: {folder.id}")
                return folder.id

    from msgraph.generated.models.mail_folder import MailFolder

    new_folder = MailFolder(display_name=folder_name)
    created = await client.users.by_user_id(user_id).mail_folders.post(new_folder)

    if not created or not created.id:
        raise RuntimeError(f"Failed to create archive folder: {folder_name}")

    logger.info(f"Created archive folder: {created.id}")
    return created.id


async def mark_as_read(client: GraphServiceClient, user_id: str, message_id: str) -> None:
    """Mark an email as read.

    Args:
        client: Authenticated Graph client
        user_id: Mailbox user ID
        message_id: Message ID to mark as read
    """
    update = Message(is_read=True)
    await client.users.by_user_id(user_id).messages.by_message_id(message_id).patch(update)
    logger.info(f"Marked message {message_id} as read")


async def move_to_archive(
    client: GraphServiceClient, user_id: str, message_id: str, archive_folder_id: str
) -> None:
    """Move an email to the archive folder.

    Args:
        client: Authenticated Graph client
        user_id: Mailbox user ID
        message_id: Message ID to move
        archive_folder_id: Destination folder ID
    """
    from msgraph.generated.users.item.messages.item.move.move_post_request_body import (
        MovePostRequestBody,
    )

    body = MovePostRequestBody(destination_id=archive_folder_id)
    await client.users.by_user_id(user_id).messages.by_message_id(message_id).move.post(body)
    logger.info(f"Moved message {message_id} to archive")


async def process_email(
    client: GraphServiceClient, config: DispatchConfig, message_id: str
) -> dict:
    """Process a dispatch email: mark as read and move to archive.

    Args:
        client: Authenticated Graph client
        config: Dispatch configuration
        message_id: ID of the message to process

    Returns:
        Dict with processing result details
    """
    user_id = config.mailbox_user_id
    logger.info(f"Processing email {message_id} for user {user_id}")

    message = await client.users.by_user_id(user_id).messages.by_message_id(message_id).get()

    sender_email: str | None = None
    if message and message.from_ and message.from_.email_address:
        sender_email = message.from_.email_address.address

    subject = "(no subject)"
    if message and message.subject:
        subject = message.subject

    logger.info(f"Received email from {sender_email}: {subject}")

    archive_folder_id = await get_or_create_archive_folder(client, user_id, config.archive_folder)

    await mark_as_read(client, user_id, message_id)
    await move_to_archive(client, user_id, message_id, archive_folder_id)

    return {
        "status": "processed",
        "message_id": message_id,
        "sender": sender_email,
        "subject": subject,
    }
