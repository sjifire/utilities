"""Azure Functions app for email dispatch processing."""

import json
import logging

import azure.functions as func

from sjifire.core.config import load_dispatch_config
from sjifire.core.graph_client import get_graph_client
from sjifire.dispatch.cleanup import cleanup_old_emails
from sjifire.dispatch.processor import process_email

app = func.FunctionApp()

VALIDATION_TOKEN_PARAM = "validationToken"
CLIENT_STATE = "sjifire-dispatch-secret"


@app.function_name(name="email_webhook")
@app.route(route="email_webhook", methods=["POST", "GET"])
async def email_webhook(req: func.HttpRequest) -> func.HttpResponse:
    """HTTP trigger for MS Graph webhook notifications.

    Handles:
    - Subscription validation (GET with validationToken)
    - New email notifications (POST with change notifications)
    """
    logging.info("Email webhook triggered")

    validation_token = req.params.get(VALIDATION_TOKEN_PARAM)
    if validation_token:
        logging.info("Subscription validation request received")
        return func.HttpResponse(validation_token, status_code=200, mimetype="text/plain")

    try:
        body = req.get_json()
    except ValueError:
        logging.error("Invalid JSON in request body")
        return func.HttpResponse("Invalid JSON", status_code=400)

    if "value" not in body:
        logging.warning("No notifications in request body")
        return func.HttpResponse("OK", status_code=200)

    config = load_dispatch_config()
    client = get_graph_client()

    for notification in body["value"]:
        client_state = notification.get("clientState")
        if client_state != CLIENT_STATE:
            logging.warning(f"Invalid client state: {client_state}")
            continue

        resource = notification.get("resource", "")
        if "/messages/" not in resource:
            logging.info(f"Ignoring non-message notification: {resource}")
            continue

        message_id = resource.split("/messages/")[-1]
        logging.info(f"Processing notification for message: {message_id}")

        try:
            result = await process_email(client, config, message_id)
            logging.info(f"Email processed: {json.dumps(result)}")
        except Exception as e:
            logging.error(f"Failed to process email {message_id}: {e}")

    return func.HttpResponse("OK", status_code=200)


@app.function_name(name="weekly_cleanup")
@app.timer_trigger(schedule="0 0 0 * * 0", arg_name="timer", run_on_startup=False)
async def weekly_cleanup(timer: func.TimerRequest) -> None:
    """Timer trigger for weekly cleanup of old emails.

    Runs every Sunday at midnight UTC.
    CRON: 0 0 0 * * 0
    """
    logging.info("Weekly cleanup triggered")

    if timer.past_due:
        logging.info("Timer is past due, running cleanup anyway")

    config = load_dispatch_config()
    client = get_graph_client()

    try:
        result = await cleanup_old_emails(client, config)
        logging.info(f"Cleanup complete: {json.dumps(result)}")
    except Exception as e:
        logging.error(f"Cleanup failed: {e}")
        raise
