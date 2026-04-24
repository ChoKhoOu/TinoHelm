"""Node lifecycle command publisher (Redis-based).

Trading nodes (sandbox/live) run as independent Docker containers.
This class writes config to Redis and publishes lifecycle commands —
it does NOT spawn or manage node processes directly.

Note: backtest worker pool management was removed in the 2026-04-24
refactor; see ``backtest/consumer.py`` for the replacement.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import redis

logger = logging.getLogger(__name__)

VALID_NODE_TYPES = ("sandbox", "live")


class NodeController:
    """Publishes trading-node lifecycle commands and reports node status.

    Trading nodes run as independent Docker containers.  This class owns only
    the Redis client used for publishing commands and reading heartbeat/state
    keys.  Backtest worker orchestration has moved to
    ``tinohelm.backtest.consumer``.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        catalog_path: str = "data/catalog",
        artifacts_path: str = "data/artifacts",
        db_url: str = "",
    ) -> None:
        self._redis: redis.Redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._redis_url = redis_url
        self._catalog_path = catalog_path
        self._artifacts_path = artifacts_path
        self._db_url = db_url

    # ------------------------------------------------------------------
    # Lifecycle commands
    # ------------------------------------------------------------------

    def lifecycle_command(
        self,
        action: str,
        node_type: str = "live",
        strategy_id: str | None = None,
        strategy_name: str | None = None,
    ) -> None:
        """Publish a lifecycle command to the node via Redis PubSub.

        Actions: pause, resume, flatten, halt, unhalt, shutdown,
                 start_strategy, pause_strategy, resume_strategy,
                 flatten_stop_strategy.
        """
        if node_type not in VALID_NODE_TYPES:
            raise ValueError(
                f"Invalid node_type {node_type!r}; must be one of {VALID_NODE_TYPES}"
            )
        channel = f"tino:{node_type}:commands"
        cmd: dict[str, Any] = {"cmd": action}
        if strategy_id:
            cmd["strategy_id"] = strategy_id
        if strategy_name:
            cmd["strategy_name"] = strategy_name

        self._redis.publish(channel, json.dumps(cmd))
        logger.warning("Lifecycle command: %s on %s (strategy=%s)", action, node_type, strategy_id)

        # For shutdown, poll heartbeat for confirmation
        if action == "shutdown":
            for _ in range(15):
                if not self._redis.exists(f"tino:heartbeat:{node_type}"):
                    logger.info("Shutdown confirmed: %s node heartbeat gone", node_type)
                    break
                time.sleep(1)

    # ------------------------------------------------------------------
    # Kill switch (backward compat — delegates to lifecycle_command)
    # ------------------------------------------------------------------

    _LEVEL_TO_ACTION = {1: "pause", 2: "flatten", 3: "shutdown"}

    def kill_switch(
        self,
        level: int,
        node_type: str = "live",
        strategy_id: str | None = None,
    ) -> None:
        """Execute an emergency kill switch at the given severity *level*.

        Level 1 -- Pause a single strategy (requires *strategy_id*).
        Level 2 -- Flatten all positions immediately.
        Level 3 -- Full shutdown (maps to L4 in new 4-level model).
        """
        if level not in self._LEVEL_TO_ACTION:
            raise ValueError(f"Invalid kill-switch level {level}; must be 1, 2, or 3")
        if level == 1 and strategy_id is None:
            raise ValueError("strategy_id is required for kill-switch level 1")

        action = self._LEVEL_TO_ACTION[level]
        self.lifecycle_command(action, node_type, strategy_id)

    # ------------------------------------------------------------------
    # Status (Redis-only; backtest worker pool removed)
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return status for all trading nodes (sandbox + live).

        Node status is determined purely from Redis heartbeat and state keys.
        The historical ``backtest_workers`` key is no longer included — backtest
        execution is handled by the async consumer pool (see
        ``tinohelm.backtest.consumer``).
        """
        result: dict[str, Any] = {"nodes": {}}

        for node_type in VALID_NODE_TYPES:
            heartbeat_raw = self._redis.get(f"tino:heartbeat:{node_type}")
            state_raw = self._redis.get(f"tino:node:state:{node_type}")

            heartbeat: dict[str, Any] | None = None
            if heartbeat_raw:
                try:
                    heartbeat = json.loads(heartbeat_raw)
                except (json.JSONDecodeError, TypeError):
                    heartbeat = {"raw": heartbeat_raw}

            state: dict[str, Any] = {}
            if state_raw:
                try:
                    state = json.loads(state_raw)
                except (json.JSONDecodeError, TypeError):
                    pass

            if heartbeat:
                status = "running"
            elif state.get("status") == "config_ready":
                status = "config_ready"
            else:
                status = "stopped"

            result["nodes"][node_type] = {
                "status": status,
                "heartbeat": heartbeat,
                "config_version": state.get("config_version"),
            }

        return result

    # ------------------------------------------------------------------
    # Public accessors (used by route handlers / tests)
    # ------------------------------------------------------------------

    @property
    def redis_url(self) -> str:
        return self._redis_url

    @property
    def catalog_path(self) -> str:
        return self._catalog_path

    @property
    def artifacts_path(self) -> str:
        return self._artifacts_path

    @property
    def db_url(self) -> str:
        return self._db_url

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Close the Redis connection.

        Trading nodes are independent Docker containers and shut down via
        ``docker compose stop`` / their own lifecycle; this controller only
        owns the Redis client used to publish commands and read status.
        """
        try:
            self._redis.close()
        except Exception:  # pragma: no cover — best-effort cleanup
            logger.exception("NodeController: error closing Redis client")
        logger.info("NodeController: shut down")
