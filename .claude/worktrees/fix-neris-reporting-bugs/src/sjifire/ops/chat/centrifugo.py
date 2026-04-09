"""Centrifugo integration: WebSocket proxy, RPC proxy, auth endpoints, and publish helper.

Centrifugo runs as a sidecar container on localhost:8001 (client WS) and
localhost:9001 (internal HTTP API). Because ACA ingress only exposes one
port (8000 for FastAPI), we proxy the WebSocket path through FastAPI.

Centrifugo uses proxy mode for auth — it calls back to FastAPI endpoints
to validate connections and channel subscriptions. The RPC proxy handles
``namedRPC`` calls from the browser over the same WebSocket used for
receiving events, eliminating the dual-channel (HTTP POST + WS) split.
"""

import asyncio
import base64
import contextlib
import json
import logging
import os

import websockets
from cent import AsyncClient as CentClient
from cent import PublishRequest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.websockets import WebSocket, WebSocketDisconnect

from sjifire.ops.auth import UserContext, check_is_editor, get_easyauth_user, set_current_user

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

    Keepalive note: The ``websockets`` library sends WebSocket-level pings
    (control frames) every 20s by default and kills the connection if no
    pong arrives within 20s. These control frames are NOT relayed to the
    browser — each leg handles them independently. This is problematic
    because:

    1. The browser never sees upstream pings, so the browser→proxy leg
       has no WebSocket-level keepalive (only TCP keepalive).
    2. The upstream pong timeout can kill the proxy→Centrifugo leg during
       slow operations (long tool calls, GC pauses).

    We disable the library's keepalive and rely on Centrifugo's own
    protocol-level ping/pong (empty JSON ``{}`` text frames), which flow
    through the proxy as normal messages and keep both legs alive.
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
        async with websockets.connect(
            centrifugo_url,
            additional_headers=proxy_headers,
            ping_interval=None,
            ping_timeout=None,
            max_size=5 * 1024 * 1024,
        ) as upstream:
            logger.info("WS proxy: upstream connected to %s", centrifugo_url)

            async def client_to_upstream() -> None:
                try:
                    while True:
                        data = await ws.receive_text()
                        await upstream.send(data)
                except WebSocketDisconnect:
                    logger.debug("WS proxy: client disconnected")

            async def upstream_to_client() -> None:
                try:
                    async for message in upstream:
                        text = message if isinstance(message, str) else message.decode()
                        await ws.send_text(text)
                except websockets.exceptions.ConnectionClosed as e:
                    logger.info(
                        "WS proxy: upstream closed (code=%s reason=%s)",
                        e.code,
                        e.reason,
                    )

            # Run both directions; when either finishes, cancel the other
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            # Surface unexpected errors from completed relay tasks
            for task in done:
                exc = task.exception()
                if exc is not None:
                    logger.warning("WS proxy: relay task failed", exc_info=exc)

    except Exception:
        logger.warning("WS proxy: connection failed", exc_info=True)
    finally:
        with contextlib.suppress(Exception):
            await ws.close()


# ---------------------------------------------------------------------------
# Centrifugo proxy auth: connect + subscribe callbacks
# ---------------------------------------------------------------------------

_LOCALHOST = frozenset(("127.0.0.1", "::1"))


def _is_from_centrifugo(request: Request) -> bool:
    """Check that the request originated from the Centrifugo sidecar.

    In Azure Container Apps, sidecar containers share a network namespace
    with the main container.  Centrifugo's proxy calls hit localhost:8000,
    so ``request.client.host`` is ``127.0.0.1``.  External requests arrive
    through the ACA Envoy ingress and have a non-localhost peer address.

    NOTE: uvicorn must NOT be configured with ``--proxy-headers`` or
    ``ProxyHeadersMiddleware``, which would replace ``request.client``
    with the ``X-Forwarded-For`` value (spoofable).
    """
    client = request.client
    return client is not None and client.host in _LOCALHOST


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
    if not _is_from_centrifugo(request):
        logger.warning("Connect proxy: rejected non-localhost origin %s", request.client)
        return JSONResponse({"error": {"code": 403, "message": "Forbidden"}}, status_code=403)

    user = _get_user(request)
    if user is None:
        # Log which headers Centrifugo actually forwarded for debugging
        auth_headers = {
            k: v[:40]
            for k, v in request.headers.items()
            if "principal" in k.lower() or "cookie" in k.lower()
        }
        logger.warning("Connect proxy: user is None. Auth headers present: %s", auth_headers)
        return JSONResponse({"error": {"code": 401, "message": "Unauthorized"}})

    logger.info("Connect proxy: email=%s, user_id=%s", user.email, user.user_id or "(empty)")

    return JSONResponse(
        {
            "result": {
                "user": user.email,
                "data": {"name": user.name},
                # conn_info is attached to presence data and join/leave events.
                # Also carried to subscribe proxy as b64info for RBAC checks.
                "info": {"name": user.name, "email": user.email, "user_id": user.user_id},
            }
        }
    )


async def subscribe_proxy(request: Request) -> Response:
    """Centrifugo subscribe proxy — validate channel access.

    Centrifugo sends the authenticated user's email (set during connect)
    in the request body ``user`` field, and the connect ``info`` payload
    as ``b64info``. We use these instead of HTTP headers.

    Channel naming convention:
    - ``chat:incident:{incident_id}`` — requires editor role
    - ``chat:general:{user_email}`` — requires matching user email

    POST /centrifugo/subscribe
    """
    if not _is_from_centrifugo(request):
        logger.warning("Subscribe proxy: rejected non-localhost origin %s", request.client)
        return JSONResponse({"error": {"code": 403, "message": "Forbidden"}}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"code": 400, "message": "Bad request"}})

    # User email comes from the connect proxy result (body.user)
    user_email = body.get("user", "")
    if not user_email:
        return JSONResponse({"error": {"code": 401, "message": "Unauthorized"}})

    channel = body.get("channel", "")

    if channel.startswith("chat:incident:"):
        # Incident channels require editor role — decode b64info for user_id
        user_id = ""
        b64info = body.get("b64info", "")
        if b64info:
            with contextlib.suppress(Exception):
                info = json.loads(base64.b64decode(b64info))
                user_id = info.get("user_id", "")

        is_editor = await check_is_editor(user_id, email=user_email)
        if not is_editor:
            return JSONResponse({"error": {"code": 403, "message": "Editor role required"}})
        return JSONResponse({"result": {}})

    if channel.startswith("chat:general:"):
        # General channels are scoped per user
        channel_email = channel.removeprefix("chat:general:")
        if channel_email != user_email:
            return JSONResponse({"error": {"code": 403, "message": "Channel access denied"}})
        return JSONResponse({"result": {}})

    # Unknown channel namespace
    return JSONResponse({"error": {"code": 403, "message": "Unknown channel"}})


# ---------------------------------------------------------------------------
# RPC proxy: namedRPC calls from the browser WebSocket
# ---------------------------------------------------------------------------

# Hold references to background chat tasks so they aren't garbage-collected.
_background_tasks: set[asyncio.Task] = set()


def _decode_b64info(b64info: str) -> dict:
    """Decode Centrifugo b64info to a dict (user_id, name, email)."""
    if not b64info:
        return {}
    with contextlib.suppress(Exception):
        return json.loads(base64.b64decode(b64info))
    return {}


async def rpc_proxy(request: Request) -> Response:
    """Centrifugo RPC proxy — handle namedRPC calls from the browser.

    Centrifugo forwards ``namedRPC('method', data)`` calls here. The
    request body includes ``user`` (email from connect proxy), ``method``,
    ``data`` (the RPC payload), and ``b64info`` (connect info with
    user_id, name, email).

    POST /centrifugo/rpc
    """
    if not _is_from_centrifugo(request):
        logger.warning("RPC proxy: rejected non-localhost origin %s", request.client)
        return JSONResponse({"error": {"code": 403, "message": "Forbidden"}}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"code": 400, "message": "Bad request"}})

    method = body.get("method", "")
    raw_data = body.get("data", {})
    b64info = body.get("b64info", "")

    # Decode user identity from connect info
    info = _decode_b64info(b64info)
    user_email = info.get("email", "") or body.get("user", "")
    user_name = info.get("name", "")
    user_id = info.get("user_id", "")

    if not user_email:
        return JSONResponse({"error": {"code": 401, "message": "Unauthorized"}})

    logger.info(
        "RPC proxy user: email=%s, user_id=%s, method=%s",
        user_email,
        user_id or "(empty)",
        method,
    )

    user = UserContext(
        email=user_email,
        name=user_name,
        user_id=user_id,
        groups=frozenset(),
    )

    if method == "send_message":
        return await _handle_send_message(raw_data, user)
    if method == "send_general_message":
        return await _handle_send_general_message(raw_data, user)

    return JSONResponse({"error": {"code": 400, "message": f"Unknown method: {method}"}})


async def _handle_send_message(data: dict, user: UserContext) -> Response:
    """Handle incident chat RPC — replaces POST /reports/{id}/chat."""
    from sjifire.ops.chat.engine import run_chat
    from sjifire.ops.chat.turn_lock import TurnLockStore

    incident_id = data.get("incident_id", "").strip()
    message = data.get("message", "").strip()

    if not incident_id:
        return JSONResponse({"error": {"code": 400, "message": "incident_id is required"}})
    if not message:
        return JSONResponse({"error": {"code": 400, "message": "Message is required"}})
    if len(message) > 5000:
        return JSONResponse(
            {"error": {"code": 400, "message": "Message too long (max 5000 chars)"}}
        )

    # Editor check
    is_editor = await check_is_editor(user.user_id, email=user.email)
    if not is_editor:
        return JSONResponse({"error": {"code": 403, "message": "Editor role required"}})

    # Set user context so downstream tools (upload_attachment, etc.) can
    # access the authenticated user via get_current_user().
    from sjifire.ops.auth import _get_editor_group_id

    editor_group = _get_editor_group_id()
    user = UserContext(
        email=user.email,
        name=user.name,
        user_id=user.user_id,
        groups=frozenset({editor_group}) if editor_group else frozenset(),
    )
    set_current_user(user)

    # Parse optional image attachments
    images: list[dict] | None = None
    raw_images = data.get("images")
    if raw_images:
        allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
        if not isinstance(raw_images, list) or len(raw_images) > 3:
            return JSONResponse({"error": {"code": 400, "message": "Maximum 3 images allowed"}})
        images = []
        for img in raw_images:
            if not isinstance(img, dict):
                return JSONResponse({"error": {"code": 400, "message": "Invalid image format"}})
            media_type = img.get("media_type", "")
            img_data = img.get("data", "")
            if media_type not in allowed_types:
                return JSONResponse(
                    {"error": {"code": 400, "message": f"Unsupported image type: {media_type}"}}
                )
            if len(img_data) > 2_000_000:
                return JSONResponse(
                    {"error": {"code": 400, "message": "Image too large (max ~1.5MB)"}}
                )
            images.append({"media_type": media_type, "data": img_data})

    # Auto-save uploaded images as incident attachments
    saved_image_refs: list[dict] = []
    if images:
        from sjifire.ops.attachments.tools import upload_attachment

        for idx, img in enumerate(images, 1):
            suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(
                img["media_type"], ".jpg"
            )
            try:
                result = await upload_attachment(
                    incident_id=incident_id,
                    filename=f"chat-photo-{idx}{suffix}",
                    data_base64=img["data"],
                    content_type=img["media_type"],
                )
                if "error" not in result:
                    saved_image_refs.append(
                        {"attachment_id": result["id"], "content_type": img["media_type"]}
                    )
                else:
                    logger.warning("Auto-save attachment returned error: %s", result["error"])
            except Exception:
                logger.warning("Failed to auto-save chat image", exc_info=True)

    if saved_image_refs:
        logger.info(
            "Chat auto-saved %d image(s) as attachments: %s",
            len(saved_image_refs),
            saved_image_refs,
        )

    # Acquire distributed turn lock
    lock = None
    lock_infra_failed = False
    try:
        async with TurnLockStore() as lock_store:
            lock = await lock_store.acquire(incident_id, user.email, user.name)
    except Exception:
        logger.warning("Turn lock check failed for %s", incident_id, exc_info=True)
        lock_infra_failed = True

    if lock is None and not lock_infra_failed:
        existing = None
        try:
            async with TurnLockStore() as lock_store:
                existing = await lock_store.get(incident_id)
        except Exception:
            logger.debug("Failed to fetch turn lock holder for %s", incident_id, exc_info=True)
        holder = existing.holder_name if existing else "another user"
        holder_email = existing.holder_email if existing else ""
        error_data = json.dumps(
            {
                "holder_name": holder,
                "holder_email": holder_email,
                "retry_after": "done",
            }
        )
        return JSONResponse({"error": {"code": 409, "message": error_data}})

    channel = f"chat:incident:{incident_id}"
    task = asyncio.create_task(
        run_chat(
            incident_id,
            message,
            user,
            channel=channel,
            images=images,
            image_refs=saved_image_refs or None,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return JSONResponse({"result": {"data": {"status": "accepted"}}})


async def _handle_send_general_message(data: dict, user: UserContext) -> Response:
    """Handle dashboard chat RPC — replaces POST /chat/stream."""
    from sjifire.ops.chat.engine import run_general_chat

    message = data.get("message", "").strip()
    if not message:
        return JSONResponse({"error": {"code": 400, "message": "Message is required"}})
    if len(message) > 5000:
        return JSONResponse(
            {"error": {"code": 400, "message": "Message too long (max 5000 chars)"}}
        )

    context = data.get("context")
    channel = f"chat:general:{user.email}"
    task = asyncio.create_task(run_general_chat(message, user, channel=channel, context=context))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return JSONResponse({"result": {"data": {"status": "accepted"}}})
