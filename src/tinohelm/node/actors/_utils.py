"""Shared utilities for node actors."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import redis

logger = logging.getLogger(__name__)


def ts_ns_to_iso(ts_ns: int) -> str:
    """Convert nanosecond timestamp to ISO-8601 string."""
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat()


def redis_publish(
    redis_client: redis.Redis | None,
    node_type: str,
    channel_suffix: str,
    data: dict,
) -> None:
    """Publish event data to Redis PubSub channel."""
    if redis_client:
        channel = f"tino:{node_type}:{channel_suffix}"
        try:
            redis_client.publish(channel, json.dumps(data, default=str))
        except Exception as e:
            logger.error("Redis publish error on %s: %s", channel, e)
