"""Setup script for dispatch email subscriptions and function deployment."""

import argparse
import asyncio
import logging
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta

from msgraph.generated.models.subscription import Subscription

from sjifire.core.config import load_dispatch_config
from sjifire.core.graph_client import get_graph_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def get_function_app_name() -> str | None:
    """Get the function app name from config."""
    import json

    from sjifire.core.config import get_project_root

    config_path = get_project_root() / "config" / "email_dispatch.json"
    with open(config_path) as f:
        config = json.load(f)
    return config.get("azure", {}).get("function_app")


def check_func_cli() -> bool:
    """Check if Azure Functions Core Tools is installed."""
    if not shutil.which("func"):
        logger.error("Azure Functions Core Tools (func) not found.")
        logger.error("Install from: https://aka.ms/azure-functions-core-tools")
        return False
    return True


def deploy_function_app(function_app: str) -> bool:
    """Deploy the function app code using func CLI."""
    from sjifire.core.config import get_project_root

    functions_dir = get_project_root() / "functions"

    if not functions_dir.exists():
        logger.error(f"Functions directory not found: {functions_dir}")
        return False

    logger.info(f"Deploying to {function_app}...")

    result = subprocess.run(
        ["func", "azure", "functionapp", "publish", function_app, "--python"],
        cwd=functions_dir,
    )

    return result.returncode == 0


def get_function_url(function_app: str) -> str:
    """Get the webhook URL for the email_webhook function."""
    return f"https://{function_app}.azurewebsites.net/api/email_webhook"


async def validate_mailbox(client, user_id: str) -> bool:
    """Validate that the mailbox exists and is accessible."""
    try:
        user = await client.users.by_user_id(user_id).get()
        if user:
            logger.info(f"Mailbox validated: {user.display_name} ({user.mail})")
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to validate mailbox {user_id}: {e}")
        return False


async def list_subscriptions(client, user_id: str) -> list:
    """List existing Graph subscriptions for the mailbox."""
    try:
        subscriptions = await client.subscriptions.get()
        mail_subs = []
        if subscriptions and subscriptions.value:
            for sub in subscriptions.value:
                if f"/users/{user_id}/messages" in (sub.resource or ""):
                    mail_subs.append(sub)
                    logger.info(f"Found subscription: {sub.id}")
                    logger.info(f"  URL: {sub.notification_url}")
                    logger.info(f"  Expires: {sub.expiration_date_time}")
        if not mail_subs:
            logger.info("No existing subscriptions found")
        return mail_subs
    except Exception as e:
        logger.error(f"Failed to list subscriptions: {e}")
        return []


async def delete_subscription(client, subscription_id: str) -> bool:
    """Delete a Graph subscription."""
    try:
        await client.subscriptions.by_subscription_id(subscription_id).delete()
        logger.info(f"Deleted subscription: {subscription_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete subscription {subscription_id}: {e}")
        return False


async def create_subscription(client, user_id: str, webhook_url: str) -> Subscription | None:
    """Create a new Graph subscription for mail notifications."""
    expiration = datetime.now(UTC) + timedelta(days=2)

    subscription = Subscription(
        change_type="created",
        notification_url=webhook_url,
        resource=f"/users/{user_id}/messages",
        expiration_date_time=expiration,
        client_state="sjifire-dispatch-secret",
    )

    try:
        created = await client.subscriptions.post(subscription)
        logger.info(f"Created subscription: {created.id}")
        logger.info(f"Expires: {created.expiration_date_time}")
        return created
    except Exception as e:
        logger.error(f"Failed to create subscription: {e}")
        return None


async def run_setup(
    list_only: bool = False,
    deploy: bool = False,
    subscribe: bool = False,
) -> int:
    """Run the setup process.

    Args:
        list_only: If True, only list existing subscriptions
        deploy: If True, deploy function app code
        subscribe: If True, create/update MS Graph subscription

    Returns:
        Exit code (0 for success)
    """
    logger.info("=" * 50)
    logger.info("SJI Fire Dispatch Setup")
    logger.info("=" * 50)

    function_app = get_function_app_name()
    if not function_app:
        logger.error("No function_app configured in config/email_dispatch.json")
        return 1

    # List-only mode
    if list_only:
        try:
            config = load_dispatch_config(require_mailbox=True)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return 1
        client = get_graph_client()
        await list_subscriptions(client, config.mailbox_user_id)
        return 0

    # Deploy function app
    if deploy:
        if not check_func_cli():
            return 1
        logger.info("")
        if not deploy_function_app(function_app):
            logger.error("Deployment failed")
            return 1
        logger.info("Deployment successful")

    # Set up MS Graph subscription
    if subscribe:
        try:
            config = load_dispatch_config(require_mailbox=True)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return 1

        logger.info("")
        logger.info("Setting up MS Graph subscription...")

        client = get_graph_client()

        if not await validate_mailbox(client, config.mailbox_user_id):
            return 1

        existing = await list_subscriptions(client, config.mailbox_user_id)
        if existing:
            logger.info("Removing existing subscriptions...")
            for sub in existing:
                if sub.id:
                    await delete_subscription(client, sub.id)

        webhook_url = get_function_url(function_app)
        logger.info(f"Webhook URL: {webhook_url}")

        subscription = await create_subscription(client, config.mailbox_user_id, webhook_url)
        if not subscription:
            return 1

    if not deploy and not subscribe and not list_only:
        logger.info("")
        logger.info("Usage:")
        logger.info("  --deploy      Deploy function app to Azure")
        logger.info("  --subscribe   Create MS Graph webhook subscription")
        logger.info("  --list        List existing subscriptions")
        logger.info("")
        logger.info("Example: uv run dispatch-setup --deploy --subscribe")
        return 0

    logger.info("")
    logger.info("Done!")
    return 0


def main():
    """CLI entry point for dispatch-setup."""
    parser = argparse.ArgumentParser(
        description="Deploy dispatch function and manage MS Graph subscriptions"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_only",
        help="List existing MS Graph subscriptions",
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Deploy function app code to Azure",
    )
    parser.add_argument(
        "--subscribe",
        action="store_true",
        help="Create/update MS Graph webhook subscription",
    )

    args = parser.parse_args()
    exit_code = asyncio.run(run_setup(
        list_only=args.list_only,
        deploy=args.deploy,
        subscribe=args.subscribe,
    ))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
