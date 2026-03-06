"""FastAPI-side event bridge — subscribes to Redis PubSub and relays to WebSocket clients."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

import redis.asyncio as aioredis
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class EventBridge:
    """Manages Redis PubSub subscriptions and WebSocket client relay."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None
        self._clients: dict[str, set[WebSocket]] = defaultdict(set)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the event bridge — connect to Redis and begin listening."""
        self._redis = aioredis.from_url(self._redis_url)
        self._pubsub = self._redis.pubsub()
        await self._pubsub.psubscribe("tino:*")
        self._task = asyncio.create_task(self._listener())
        logger.info("EventBridge started")

    async def stop(self) -> None:
        """Stop the event bridge."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
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
        """Return the total number of connected WebSocket clients."""
        return len(self._clients)

    async def unsubscribe(self, websocket: WebSocket) -> None:
        """Remove a WebSocket client from all subscriptions."""
        for channel_clients in self._clients.values():
            channel_clients.discard(websocket)

    async def _listener(self) -> None:
        """Background task: listen to Redis PubSub and relay to WebSocket clients."""
        while True:
            try:
                async for message in self._pubsub.listen():
                    if message["type"] != "pmessage":
                        continue

                    channel = message["channel"]
                    if isinstance(channel, bytes):
                        channel = channel.decode()

                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode()

                    # Build relay payload
                    payload = json.dumps({
                        "channel": channel,
                        "data": json.loads(data) if data else {},
                    })

                    # Send to wildcard subscribers
                    await self._relay(payload, self._clients.get("*", set()))

                    # Send to channel-specific subscribers
                    for pattern, clients in self._clients.items():
                        if pattern != "*" and not channel.startswith(pattern):
                            continue
                        if pattern != "*":
                            await self._relay(payload, clients)

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

    async def _relay(self, payload: str, clients: set[WebSocket]) -> None:
        """Send payload to a set of WebSocket clients, removing dead ones."""
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)
