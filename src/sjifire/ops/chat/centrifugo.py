"""Centrifugo integration: WebSocket proxy, auth endpoints, and publish helper.

Centrifugo runs as a sidecar container on localhost:8001 (client WS) and
localhost:9001 (internal HTTP API). Because ACA ingress only exposes one
port (8000 for FastAPI), we proxy the WebSocket path through FastAPI.

Centrifugo uses proxy mode for auth — it calls back to FastAPI endpoints
to validate connections and channel subscriptions.
"""

import asyncio
import contextlib
import logging
import os

import websockets
from cent import AsyncClient as CentClient
from cent import PublishRequest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.websockets import WebSocket, WebSocketDisconnect

from sjifire.ops.auth import check_is_editor, get_easyauth_user

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Centrifugo HTTP API client (lazy singleton)
# ---------------------------------------------------------------------------

_cent_client: CentClient | None = None


def _get_cent_client() -> CentClient:
    """Return a lazily-initialized Centrifugo HTTP API client."""
    global _cent_client
    if _cent_client is None:
        api_key = os.getenv("CENTRIFUGO_API_KEY", "")
        api_url = os.getenv("CENTRIFUGO_API_URL", "http://localhost:9001/api")
        _cent_client = CentClient(api_url, api_key=api_key)
    return _cent_client


async def publish(channel: str, event: str, data: dict) -> None:
    """Publish a chat event to a Centrifugo channel.

    Args:
        channel: Centrifugo channel name (e.g. ``chat:incident:{id}``).
        event: Event type (text, tool_call, tool_result, done, error, etc.).
        data: Event payload dict.
    """
    try:
        client = _get_cent_client()
        req = PublishRequest(channel=channel, data={"event": event, **data})
        await client.publish(req)
    except Exception:
        logger.error(
            "Centrifugo publish failed (channel=%s, event=%s)", channel, event, exc_info=True
        )


# ---------------------------------------------------------------------------
# WebSocket proxy: /connection/websocket → localhost:8001
# ---------------------------------------------------------------------------


async def websocket_proxy(ws: WebSocket) -> None:
    """Proxy a WebSocket connection from the client to Centrifugo.

    The browser connects to ``wss://ops.sjifire.org/connection/websocket``
    which ACA routes to FastAPI port 8000. We forward each frame to
    Centrifugo on ``ws://localhost:8001/connection/websocket``.
    """
    await ws.accept()

    centrifugo_port = os.getenv("CENTRIFUGO_PORT", "8001")
    centrifugo_url = f"ws://localhost:{centrifugo_port}/connection/websocket"

    # Forward EasyAuth headers so Centrifugo's connect proxy can identify the user.
    # Centrifugo forwards these via CENTRIFUGO_CLIENT_PROXY_CONNECT_HTTP_HEADERS.
    proxy_headers = {}
    forward = (
        "cookie",
        "x-ms-client-principal",
        "x-ms-client-principal-id",
        "x-ms-client-principal-name",
    )
    for hdr in forward:
        val = ws.headers.get(hdr)
        if val:
            proxy_headers[hdr] = val

    try:
        async with websockets.connect(centrifugo_url, additional_headers=proxy_headers) as upstream:
            # Forward frames in both directions concurrently
            async def client_to_upstream() -> None:
                try:
                    while True:
                        data = await ws.receive_text()
                        await upstream.send(data)
                except WebSocketDisconnect:
                    pass

            async def upstream_to_client() -> None:
                try:
                    async for message in upstream:
                        text = message if isinstance(message, str) else message.decode()
                        await ws.send_text(text)
                except websockets.exceptions.ConnectionClosed:
                    pass

            # Run both directions; when either finishes, cancel the other
            _done, pending = await asyncio.wait(
                [
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except Exception:
        logger.debug("WebSocket proxy connection closed", exc_info=True)
    finally:
        with contextlib.suppress(Exception):
            await ws.close()


# ---------------------------------------------------------------------------
# Centrifugo proxy auth: connect + subscribe callbacks
# ---------------------------------------------------------------------------


def _get_user(request: Request):
    """Extract user from EasyAuth headers, falling back to dev user."""
    user = get_easyauth_user(request)
    if user is None:
        # Dev mode: use the synthetic dev user set by _DevAuthMiddleware
        from sjifire.ops.auth import _current_user

        user = _current_user.get()
    return user


async def connect_proxy(request: Request) -> Response:
    """Centrifugo connect proxy — validate user via EasyAuth headers.

    Centrifugo forwards the client's original HTTP headers (Cookie,
    X-Ms-Client-Principal-Id, etc.) in the proxy request. We extract
    the EasyAuth user and return their identity.

    POST /centrifugo/connect
    """
    user = _get_user(request)
    if user is None:
        return JSONResponse({"error": {"code": 401, "message": "Unauthorized"}})

    return JSONResponse(
        {
            "result": {
                "user": user.email,
                "data": {"name": user.name},
                # conn_info is attached to presence data and join/leave events
                "info": {"name": user.name, "email": user.email},
            }
        }
    )


async def subscribe_proxy(request: Request) -> Response:
    """Centrifugo subscribe proxy — validate channel access.

    Channel naming convention:
    - ``chat:incident:{incident_id}`` — requires editor role
    - ``chat:general:{user_email}`` — requires matching user email

    POST /centrifugo/subscribe
    """
    user = _get_user(request)
    if user is None:
        return JSONResponse({"error": {"code": 401, "message": "Unauthorized"}})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"code": 400, "message": "Bad request"}})

    channel = body.get("channel", "")

    if channel.startswith("chat:incident:"):
        # Incident channels require editor role
        is_editor = await check_is_editor(user.user_id, fallback=user.is_editor)
        if not is_editor:
            return JSONResponse({"error": {"code": 403, "message": "Editor role required"}})
        return JSONResponse({"result": {}})

    if channel.startswith("chat:general:"):
        # General channels are scoped per user
        channel_email = channel.removeprefix("chat:general:")
        if channel_email != user.email:
            return JSONResponse({"error": {"code": 403, "message": "Channel access denied"}})
        return JSONResponse({"result": {}})

    # Unknown channel namespace
    return JSONResponse({"error": {"code": 403, "message": "Unknown channel"}})
