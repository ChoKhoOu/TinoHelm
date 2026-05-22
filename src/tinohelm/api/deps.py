"""Dependency injection helpers for FastAPI routes."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.core.bridge import EventBridge
from tinohelm.core.config import Settings, get_settings
from tinohelm.core.node_controller import NodeController
from tinohelm.db.session import get_db as _get_db

# ---- singletons set during lifespan ----
_redis_client: aioredis.Redis | None = None
_node_controller: NodeController | None = None
_event_bridge: EventBridge | None = None


def set_redis(client: aioredis.Redis) -> None:
    """Store the Redis client singleton (called during app lifespan)."""
    global _redis_client
    _redis_client = client


def set_node_controller(nc: NodeController) -> None:
    """Store the NodeController singleton (called during app lifespan)."""
    global _node_controller
    _node_controller = nc


def set_event_bridge(bridge: EventBridge) -> None:
    """Store the EventBridge singleton (called during app lifespan)."""
    global _event_bridge
    _event_bridge = bridge


# ---- FastAPI Depends callables ----

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session."""
    async for session in _get_db():
        yield session


async def get_redis() -> aioredis.Redis:
    """Return the shared async Redis client."""
    if _redis_client is None:
        raise RuntimeError("Redis client not initialised")
    return _redis_client


def get_node_controller() -> NodeController:
    """Return the NodeController singleton."""
    if _node_controller is None:
        raise RuntimeError("NodeController not initialised")
    return _node_controller


def get_settings_dep() -> Settings:
    """Return the cached Settings singleton."""
    return get_settings()


def get_event_bridge() -> EventBridge:
    """Return the EventBridge singleton."""
    if _event_bridge is None:
        raise RuntimeError("EventBridge not initialised")
    return _event_bridge


# ---- startup time ----
_startup_time: float = 0.0


def set_startup_time(t: float) -> None:
    """Record the application startup timestamp."""
    global _startup_time
    _startup_time = t


def get_startup_time() -> float:
    """Return the application startup timestamp."""
    return _startup_time
