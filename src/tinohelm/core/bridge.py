"""FastAPI-side event bridge — subscribes to Redis PubSub and relays to WebSocket clients.

Messages are relayed as type-tagged JSON with a ``type`` field using dot-notation
(e.g. ``backtest.progress``, ``node.heartbeat``, ``system.error``).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

import redis.asyncio as aioredis
from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Mapping from Redis channel prefix to event type prefix for messages
# that don't already carry a ``type`` field.
_CHANNEL_TYPE_MAP: dict[str, str] = {
    "tino:backtest:": "backtest.",
    "tino:heartbeat:": "node.heartbeat",
    "tino:sandbox:": "node.sandbox.",
    "tino:live:": "node.live.",
    "tino:data:": "data.",
    "tino:research:": "research.",
}


def _infer_type(channel: str) -> str | None:
    """Derive a dot-notation event type from a Redis channel name."""
    for prefix, event_type in _CHANNEL_TYPE_MAP.items():
        if channel.startswith(prefix):
            suffix = channel[len(prefix):].split(":")[0]
            if event_type.endswith("."):
                return f"{event_type}{suffix}"
            return event_type
    return None


class EventBridge:
    """Manages Redis PubSub subscriptions and WebSocket client relay.

    All messages sent to WebSocket clients are flat JSON dicts with a ``type``
    field (dot-notation).  If the Redis payload already contains ``type`` it is
    sent as-is; otherwise a type is inferred from the channel name.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None
        self._clients: dict[str, set[WebSocket]] = defaultdict(set)
        self._task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the event bridge — connect to Redis and begin listening."""
        self._redis = aioredis.from_url(self._redis_url)
        self._pubsub = self._redis.pubsub()
        await self._pubsub.psubscribe("tino:*")
        self._task = asyncio.create_task(self._listener())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_poller())
        logger.info("EventBridge started")

    async def stop(self) -> None:
        """Stop the event bridge."""
        for task in (self._task, self._heartbeat_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._pubsub:
            await self._pubsub.punsubscribe("tino:*")
            await self._pubsub.close()
        if self._redis:
            await self._redis.close()
        logger.info("EventBridge stopped")

    async def subscribe(self, websocket: WebSocket, channels: list[str] | None = None) -> None:
        """Register a WebSocket client for event delivery."""
        if channels:
            for ch in channels:
                self._clients[ch].add(websocket)
        else:
            self._clients["*"].add(websocket)

    def client_count(self) -> int:
        """Return the total number of unique connected WebSocket clients."""
        seen: set[int] = set()
        for clients in self._clients.values():
            for ws in clients:
                seen.add(id(ws))
        return len(seen)

    async def unsubscribe(self, websocket: WebSocket) -> None:
        """Remove a WebSocket client from all subscriptions and reap empty patterns.

        Patterns whose subscriber sets become empty are deleted from
        ``self._clients`` so short-lived channel subscriptions do not accumulate
        indefinitely. ``defaultdict`` semantics will re-create an entry on the
        next ``subscribe()`` call, so no external behaviour changes.
        """
        empty_patterns: list[str] = []
        for pattern, channel_clients in self._clients.items():
            channel_clients.discard(websocket)
            if not channel_clients:
                empty_patterns.append(pattern)
        for pattern in empty_patterns:
            del self._clients[pattern]

    async def _publish_to_subscribers(self, channel: str, payload: str) -> None:
        """Deliver ``payload`` to wildcard subscribers and to channel-prefix subscribers.

        A subscriber whose pattern is a prefix of ``channel`` receives the
        message. The special pattern ``"*"`` always matches.  Pulling this out
        of ``_listener`` and ``_heartbeat_poller`` keeps the fan-out semantics
        defined in a single place.
        """
        # Wildcard subscribers get every message
        await self._relay(payload, self._clients.get("*", set()))

        # Prefix-pattern subscribers
        for pattern, clients in list(self._clients.items()):
            if pattern == "*":
                continue
            if not channel.startswith(pattern):
                continue
            await self._relay(payload, clients)

    async def _listener(self) -> None:
        """Background task: listen to Redis PubSub and relay to WebSocket clients.

        Messages are sent as flat JSON with a ``type`` field.  If the Redis
        payload already carries ``type`` it is forwarded as-is; otherwise a
        type is inferred from the channel name.
        """
        while True:
            try:
                async for message in self._pubsub.listen():
                    if message["type"] != "pmessage":
                        continue

                    channel = message["channel"]
                    if isinstance(channel, bytes):
                        channel = channel.decode()

                    raw = message["data"]
                    if isinstance(raw, bytes):
                        raw = raw.decode()

                    try:
                        data: dict = json.loads(raw) if raw else {}
                    except (json.JSONDecodeError, TypeError):
                        logger.debug("Non-JSON message on %s, skipping", channel)
                        continue

                    # Ensure the payload has a ``type`` field
                    if "type" not in data:
                        inferred = _infer_type(channel)
                        if inferred:
                            data["type"] = inferred
                        else:
                            data["type"] = channel.replace(":", ".")

                    payload = json.dumps(data, default=str)
                    await self._publish_to_subscribers(channel, payload)

            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("EventBridge listener error; reconnecting in 5s")
                await asyncio.sleep(5)
                try:
                    await self._pubsub.punsubscribe("tino:*")
                    await self._pubsub.psubscribe("tino:*")
                except Exception:
                    logger.exception("EventBridge re-subscribe failed")

    async def _heartbeat_poller(self) -> None:
        """Periodically read ``tino:heartbeat:*`` keys and push ``node.heartbeat`` events."""
        while True:
            try:
                await asyncio.sleep(5)
                if not self._redis:
                    continue

                for node_type in ("sandbox", "live"):
                    raw = await self._redis.get(f"tino:heartbeat:{node_type}")
                    if raw is None:
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    try:
                        data = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    event = {
                        "type": "node.heartbeat",
                        "node_type": node_type,
                        "ts": data.get("ts"),
                        "strategies": data.get("strategies", 0),
                        "positions": data.get("positions", 0),
                    }
                    payload = json.dumps(event, default=str)
                    await self._publish_to_subscribers(
                        f"tino:heartbeat:{node_type}", payload
                    )

            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Heartbeat poller error")

    async def _relay(self, payload: str, clients: set[WebSocket]) -> None:
        """Send payload to a set of WebSocket clients, removing dead ones."""
        dead: list[WebSocket] = []
        for ws in list(clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)
