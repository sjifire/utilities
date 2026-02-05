"""Manage calendar subscriptions in M365.

This module provides functionality to add external iCal calendar subscriptions
to users' M365 Outlook calendars.

Note: Microsoft Graph API does not directly support adding iCal calendar subscriptions.
Users must manually add subscriptions via Outlook, or we can use Exchange Web Services (EWS).
This module provides utilities to:
1. Generate subscription instructions for users
2. Store subscription URLs for reference
3. (Future) Use EWS or Outlook add-ins for programmatic subscription
"""

import asyncio
import logging
from dataclasses import dataclass

from msgraph import GraphServiceClient

from sjifire.core.msgraph_client import get_graph_client

logger = logging.getLogger(__name__)


@dataclass
class CalendarSubscription:
    """Represents a calendar subscription to add to M365."""

    user_email: str
    subscription_url: str
    calendar_name: str = "Aladtec Schedule"
    color: str = "auto"  # Calendar color in Outlook


@dataclass
class SubscriptionResult:
    """Result of attempting to add a calendar subscription."""

    user_email: str
    success: bool
    message: str
    subscription_url: str | None = None


class CalendarSubscriptionManager:
    """Manage iCal calendar subscriptions for M365 users.

    Since Microsoft Graph API doesn't support adding iCal calendar subscriptions
    programmatically, this class provides utilities to:
    1. Validate that users exist in M365
    2. Generate subscription URLs and instructions
    3. Track which users need subscriptions added

    For programmatic subscription management, consider:
    - Exchange Web Services (EWS) via PowerShell
    - Outlook Add-in deployment
    - User self-service with generated instructions
    """

    def __init__(self) -> None:
        """Initialize the subscription manager with Graph credentials."""
        self.client: GraphServiceClient = get_graph_client()

    async def validate_user(self, email: str) -> bool:
        """Check if a user exists in M365.

        Args:
            email: User's email address

        Returns:
            True if user exists and has a mailbox
        """
        try:
            user = await self.client.users.by_user_id(email).get()
            return user is not None and user.mail is not None
        except Exception as e:
            logger.debug(f"User validation failed for {email}: {e}")
            return False

    async def get_user_calendars(self, email: str) -> list[dict]:
        """Get list of calendars for a user.

        Args:
            email: User's email address

        Returns:
            List of calendar info dicts with id, name, and color
        """
        try:
            result = await self.client.users.by_user_id(email).calendars.get()
            if result and result.value:
                return [
                    {
                        "id": cal.id,
                        "name": cal.name,
                        "color": cal.color.value if cal.color else None,
                        "is_default": cal.is_default_calendar,
                        "can_edit": cal.can_edit,
                    }
                    for cal in result.value
                ]
            return []
        except Exception as e:
            logger.error(f"Failed to get calendars for {email}: {e}")
            return []

    async def check_existing_subscription(
        self,
        email: str,
        calendar_name: str = "Aladtec",
    ) -> bool:
        """Check if user already has an Aladtec calendar subscription.

        Looks for a calendar containing the specified name.

        Args:
            email: User's email address
            calendar_name: Name pattern to search for

        Returns:
            True if a matching calendar exists
        """
        calendars = await self.get_user_calendars(email)
        name_lower = calendar_name.lower()
        for cal in calendars:
            if cal.get("name") and name_lower in cal["name"].lower():
                logger.info(f"Found existing '{cal['name']}' calendar for {email}")
                return True
        return False

    def generate_subscription_instructions(
        self,
        subscription: CalendarSubscription,
    ) -> str:
        """Generate instructions for manually adding a calendar subscription.

        Since Graph API doesn't support programmatic iCal subscriptions,
        this generates user-friendly instructions.

        Args:
            subscription: Calendar subscription details

        Returns:
            HTML-formatted instructions
        """
        # Convert webcal:// to https:// for copying
        url_for_copy = subscription.subscription_url
        if url_for_copy.startswith("webcal://"):
            url_for_copy = url_for_copy.replace("webcal://", "https://", 1)

        return f"""
<h2>Add Your Aladtec Calendar to Outlook</h2>

<p>Follow these steps to add your personal Aladtec schedule to your Outlook calendar:</p>

<h3>Option 1: Outlook Desktop (Windows/Mac)</h3>
<ol>
    <li>Open Outlook and go to the Calendar view</li>
    <li>Right-click on "My Calendars" in the left panel</li>
    <li>Select "Add Calendar" → "From Internet..."</li>
    <li>Paste this URL and click OK:</li>
</ol>
<p style="font-family: monospace; background: #f0f0f0; padding: 10px;">
{url_for_copy}
</p>

<h3>Option 2: Outlook Web (outlook.office.com)</h3>
<ol>
    <li>Go to <a href="https://outlook.office.com/calendar">outlook.office.com/calendar</a></li>
    <li>Click "Add calendar" in the left sidebar</li>
    <li>Select "Subscribe from web"</li>
    <li>Paste the URL above and give it a name like "{subscription.calendar_name}"</li>
    <li>Click "Import"</li>
</ol>

<p><strong>Note:</strong> Calendar updates may take up to 24 hours to sync depending
on your calendar provider settings.</p>
"""

    def sync_subscriptions(
        self,
        subscriptions: list[CalendarSubscription],
        dry_run: bool = False,
    ) -> list[SubscriptionResult]:
        """Validate users and prepare subscriptions.

        Since Graph API doesn't support adding iCal subscriptions directly,
        this method validates users and generates instructions.

        Args:
            subscriptions: List of subscriptions to process
            dry_run: If True, only validate without generating output

        Returns:
            List of results for each subscription
        """

        async def _async_sync() -> list[SubscriptionResult]:
            results = []
            for sub in subscriptions:
                # Validate user exists
                exists = await self.validate_user(sub.user_email)
                if not exists:
                    results.append(
                        SubscriptionResult(
                            user_email=sub.user_email,
                            success=False,
                            message="User not found in M365",
                        )
                    )
                    continue

                # Check for existing subscription
                has_existing = await self.check_existing_subscription(sub.user_email, "Aladtec")

                if has_existing:
                    results.append(
                        SubscriptionResult(
                            user_email=sub.user_email,
                            success=True,
                            message="Calendar subscription already exists",
                            subscription_url=sub.subscription_url,
                        )
                    )
                    continue

                if dry_run:
                    results.append(
                        SubscriptionResult(
                            user_email=sub.user_email,
                            success=True,
                            message="Would generate subscription instructions",
                            subscription_url=sub.subscription_url,
                        )
                    )
                else:
                    # Generate instructions (could also email them to user)
                    # The instructions are available via generate_subscription_instructions()
                    logger.info(f"Generated subscription instructions for {sub.user_email}")
                    results.append(
                        SubscriptionResult(
                            user_email=sub.user_email,
                            success=True,
                            message="Instructions generated - manual subscription required",
                            subscription_url=sub.subscription_url,
                        )
                    )

            return results

        return asyncio.run(_async_sync())
