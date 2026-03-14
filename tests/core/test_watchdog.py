"""Tests for tinohelm.core.watchdog.Watchdog."""
from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_watchdog(mock_redis: AsyncMock | None = None, pm: MagicMock | None = None):
    """Build a Watchdog with async Redis and ProcessManager fully mocked.

    Bypasses __init__ to avoid real aioredis connection attempts.
    """
    from tinohelm.core.watchdog import Watchdog

    if mock_redis is None:
        mock_redis = AsyncMock()
    if pm is None:
        pm = MagicMock()

    wd = Watchdog.__new__(Watchdog)
    wd._redis = mock_redis
    wd._pm = pm
    wd._task = None
    wd._running = True
    return wd, mock_redis, pm


# ---------------------------------------------------------------------------
# _check_nodes tests
# ---------------------------------------------------------------------------

class TestCheckNodes:

    def test_check_nodes_healthy_when_heartbeat_exists(self, caplog):
        """No warning is logged when heartbeat key exists for a configured node."""
        mock_redis = AsyncMock()

        async def redis_get(key):
            if "state:" in key:
                return json.dumps({"status": "config_ready"})
            if "heartbeat:" in key:
                return json.dumps({"ts": "2026-01-01"})
            return None

        mock_redis.get.side_effect = redis_get
        wd, _, _ = _make_watchdog(mock_redis)

        with caplog.at_level(logging.WARNING):
            asyncio.run(wd._check_nodes())

        # No warnings should appear
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == [], f"Unexpected warnings: {[r.message for r in warnings]}"

    def test_check_nodes_warns_when_heartbeat_missing(self, caplog):
        """A warning is logged when state key exists but heartbeat is absent."""
        mock_redis = AsyncMock()

        async def redis_get(key):
            if "state:" in key:
                return json.dumps({"status": "config_ready"})
            # heartbeat keys return None
            return None

        mock_redis.get.side_effect = redis_get
        wd, _, _ = _make_watchdog(mock_redis)

        with caplog.at_level(logging.WARNING):
            asyncio.run(wd._check_nodes())

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) >= 1, "Expected at least one warning for missing heartbeat"
        assert any("Heartbeat missing" in r.message for r in warnings)

    def test_check_nodes_skips_unconfigured_nodes(self, caplog):
        """No warning when state key is absent (node not configured)."""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None  # All keys absent

        wd, _, _ = _make_watchdog(mock_redis)

        with caplog.at_level(logging.WARNING):
            asyncio.run(wd._check_nodes())

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == [], \
            f"Unexpected warnings for unconfigured node: {[r.message for r in warnings]}"

    def test_check_nodes_does_not_restart(self):
        """Watchdog does NOT call start_node — that is Docker's responsibility."""
        mock_redis = AsyncMock()

        async def redis_get(key):
            if "state:" in key:
                return json.dumps({"status": "config_ready"})
            return None  # No heartbeat

        mock_redis.get.side_effect = redis_get
        pm = MagicMock(spec=[])  # spec=[] means NO attributes allowed
        wd, _, _ = _make_watchdog(mock_redis, pm)

        # Should not raise AttributeError — start_node is not called
        asyncio.run(wd._check_nodes())

        # Confirm pm had no method calls
        assert not pm.method_calls, \
            f"Watchdog called pm methods unexpectedly: {pm.method_calls}"
