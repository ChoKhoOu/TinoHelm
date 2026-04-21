"""Tests for SnapshotActor's ``_RedisLogHandler``.

The handler routes python `logging` records through a :class:`TokenBucket` and
publishes an envelope to the ``tino:{node}:logs`` Redis PubSub channel. Under
a log storm (crash loops, noisy strategies) it must drop — never block or
leak — and it must gracefully swallow Redis failures so the runtime isn't
brought down by a broken sidecar.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

from tinohelm.node.actors.snapshot_actor import _RedisLogHandler


def _make_record(message: str = "hi", level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name="tinohelm.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


class TestRedisLogHandlerBasics:
    def test_publishes_to_correct_channel(self):
        redis_client = MagicMock()
        handler = _RedisLogHandler(redis_client, "sandbox", rate_limit=10)
        handler.emit(_make_record())
        assert redis_client.publish.called
        channel = redis_client.publish.call_args[0][0]
        assert channel == "tino:sandbox:logs"

    def test_live_node_type(self):
        redis_client = MagicMock()
        handler = _RedisLogHandler(redis_client, "live", rate_limit=10)
        handler.emit(_make_record())
        assert redis_client.publish.call_args[0][0] == "tino:live:logs"

    def test_payload_structure(self):
        redis_client = MagicMock()
        handler = _RedisLogHandler(redis_client, "sandbox", rate_limit=10)
        handler.emit(_make_record(message="hello world", level=logging.WARNING))
        payload = json.loads(redis_client.publish.call_args[0][1])
        assert payload["type"] == "log.entry"
        assert payload["node_type"] == "sandbox"
        assert payload["level"] == "WARNING"
        assert payload["message"] == "hello world"
        assert payload["logger_name"] == "tinohelm.test"
        # ISO-8601 timestamp
        assert "T" in payload["ts"]


class TestRedisLogHandlerRateLimiting:
    def test_burst_dropped_after_capacity(self):
        """After 10 emits in one tick, the 11th is dropped."""
        redis_client = MagicMock()
        handler = _RedisLogHandler(redis_client, "sandbox", rate_limit=10)
        for _ in range(15):
            handler.emit(_make_record())
        # With rate_limit=10 and all emits in one monotonic tick, at most 10
        # should have been published.
        assert redis_client.publish.call_count <= 10

    def test_redis_errors_silently_swallowed(self):
        """Redis downtime must not cascade into the logging pipeline."""
        redis_client = MagicMock()
        redis_client.publish.side_effect = ConnectionError("redis down")
        handler = _RedisLogHandler(redis_client, "sandbox", rate_limit=10)
        # Should NOT raise — handler must never kill the logger chain
        handler.emit(_make_record())
