"""WebSocket event hub — relays Redis PubSub events to connected clients."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from tinohelm.api.deps import get_event_bridge

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

MAX_WS_CONNECTIONS = 50


@router.websocket("/ws/events")
async def ws_events(
    websocket: WebSocket,
    channels: str | None = Query(default=None),
) -> None:
    """Accept a WebSocket connection and relay events from the EventBridge.

    Optional query param ``channels`` is a comma-separated list of channel
    patterns to subscribe to (e.g. ``?channels=tino:sandbox,tino:live``).
    If omitted the client receives all events.
    """
    bridge = get_event_bridge()

    if bridge.client_count() >= MAX_WS_CONNECTIONS:
        await websocket.close(code=1013, reason="Too many connections")
        return

    await websocket.accept()

    channel_list: list[str] | None = None
    if channels:
        channel_list = [ch.strip() for ch in channels.split(",") if ch.strip()]

    await bridge.subscribe(websocket, channel_list)
    logger.info("WebSocket client connected (channels=%s)", channel_list or "*")

    try:
        # Keep the connection alive by reading (client may send pings / text)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as exc:
        logger.warning("WebSocket error: %s", exc)
    finally:
        await bridge.unsubscribe(websocket)


@router.websocket("/ws/equity")
async def ws_equity(websocket: WebSocket) -> None:
    """Stream equity curve snapshots via the ``tino:equity`` Redis channel."""
    bridge = get_event_bridge()

    if bridge.client_count() >= MAX_WS_CONNECTIONS:
        await websocket.close(code=1013, reason="Too many connections")
        return

    await websocket.accept()

    await bridge.subscribe(websocket, ["tino:equity"])
    logger.info("WebSocket equity client connected")

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info("WebSocket equity client disconnected")
    except Exception as exc:
        logger.warning("WebSocket equity error: %s", exc)
    finally:
        await bridge.unsubscribe(websocket)
