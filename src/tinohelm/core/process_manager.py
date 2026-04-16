"""Process manager for backtest workers and trading node config (Redis-based)."""
from __future__ import annotations

import json
import logging
import time
from multiprocessing import Process
from typing import Any

import redis

logger = logging.getLogger(__name__)

VALID_NODE_TYPES = ("sandbox", "live")


class ProcessManager:
    """Manages backtest worker processes and trading node configuration.

    Trading nodes run as independent Docker containers. This class writes
    config to Redis and publishes lifecycle commands — it does NOT spawn
    or manage node processes directly.

    Uses sync ``redis.Redis`` for pubsub publishing because this object lives
    in the main FastAPI process and publishing is a quick fire-and-forget call.
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
        self._backtest_workers: list[Process] = []
        self._min_workers: int = 1
        self._max_workers: int = 4
        self._ephemeral_idle_timeout: int = 60

    # ------------------------------------------------------------------
    # Lifecycle commands
    # ------------------------------------------------------------------

    def lifecycle_command(
        self,
        action: str,
        node_type: str = "live",
        strategy_id: str | None = None,
        strategy_name: str | None = None,
        symbols: list[str] | None = None,
        interval: str | None = None,
    ) -> None:
        """Publish a lifecycle command to the node via Redis PubSub.

        Actions: pause, resume, flatten, halt, unhalt, shutdown,
                 start_strategy, pause_strategy, resume_strategy,
                 flatten_stop_strategy.
        """
        if node_type not in VALID_NODE_TYPES:
            raise ValueError(f"Invalid node_type {node_type!r}; must be one of {VALID_NODE_TYPES}")
        channel = f"tino:{node_type}:commands"
        cmd: dict[str, Any] = {"cmd": action}
        if strategy_id:
            cmd["strategy_id"] = strategy_id
        if strategy_name:
            cmd["strategy_name"] = strategy_name
        if symbols:
            cmd["symbols"] = symbols
        if interval:
            cmd["interval"] = interval

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
    # Status (Redis-only)
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return status for all nodes and backtest workers.

        Node status is determined purely from Redis heartbeat and state keys.
        """
        result: dict[str, Any] = {"nodes": {}, "backtest_workers": []}

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

        for i, w in enumerate(self._backtest_workers):
            result["backtest_workers"].append({
                "index": i,
                "pid": w.pid,
                "alive": w.is_alive(),
            })

        return result

    # ------------------------------------------------------------------
    # Backtest workers
    # ------------------------------------------------------------------

    def start_workers(self, n: int) -> None:
        """Start backtest worker pool with dynamic scaling.

        Starts ``min(n, min_workers)`` keep-alive workers that never
        auto-exit, plus any extra as ephemeral workers that self-terminate
        after ``ephemeral_idle_timeout`` seconds of idleness.
        """
        self._min_workers = max(1, min(n, self._min_workers))
        self._max_workers = max(n, self._max_workers)

        # Start keep-alive workers (idle_timeout=0)
        for i in range(self._min_workers):
            self._spawn_worker(idle_timeout=0, label=f"tino-bt-worker-{i}")

        # Start additional ephemeral workers if n > min
        for i in range(self._min_workers, n):
            self._spawn_worker(
                idle_timeout=self._ephemeral_idle_timeout,
                label=f"tino-bt-ephemeral-{i}",
            )

    def _spawn_worker(self, idle_timeout: int = 0, label: str = "tino-bt-worker") -> Process:
        """Spawn a single backtest worker process."""
        from tinohelm.backtest.worker import backtest_worker

        worker_args = (
            self._redis_url,
            self._catalog_path,
            self._artifacts_path,
            self._db_url,
            idle_timeout,
        )
        proc = Process(target=backtest_worker, args=worker_args, name=label, daemon=True)
        proc.start()
        self._backtest_workers.append(proc)
        kind = "keep-alive" if idle_timeout == 0 else f"ephemeral({idle_timeout}s)"
        logger.info("Started %s backtest worker (pid=%s, %s)", label, proc.pid, kind)
        return proc

    def ensure_capacity(self) -> None:
        """Auto-scale workers based on queue depth.

        Called periodically by the Watchdog.  Spawns ephemeral workers
        when the queue has pending jobs and current alive count is below
        ``max_workers``.  Dead ephemeral workers are cleaned up automatically.
        """
        # Prune dead workers from the list
        alive_before = len(self._backtest_workers)
        self._backtest_workers = [w for w in self._backtest_workers if w.is_alive()]
        pruned = alive_before - len(self._backtest_workers)
        if pruned > 0:
            logger.info("Pruned %d dead worker(s), %d alive", pruned, len(self._backtest_workers))

        # Ensure minimum keep-alive workers
        alive = len(self._backtest_workers)
        if alive < self._min_workers:
            for _ in range(self._min_workers - alive):
                self._spawn_worker(idle_timeout=0, label="tino-bt-worker-ka")

        # Scale up if queue has pending jobs
        try:
            queue_len = self._redis.llen("tino:backtest:queue")
        except Exception:
            return

        if queue_len > 0:
            alive = len(self._backtest_workers)
            if alive < self._max_workers:
                needed = min(queue_len, self._max_workers - alive)
                for i in range(needed):
                    self._spawn_worker(
                        idle_timeout=self._ephemeral_idle_timeout,
                        label=f"tino-bt-ephemeral-auto-{alive + i}",
                    )
                logger.info(
                    "Auto-scaled: +%d worker(s) for %d queued job(s) (total: %d)",
                    needed, queue_len, len(self._backtest_workers),
                )

    def stop_workers(self) -> None:
        """Stop all running backtest worker processes."""
        for proc in self._backtest_workers:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)
                if proc.is_alive():
                    proc.kill()
                    proc.join(timeout=3)
        self._backtest_workers.clear()
        logger.info("All backtest workers stopped")

    # ------------------------------------------------------------------
    # Public accessors (used by Watchdog)
    # ------------------------------------------------------------------

    def get_backtest_workers(self):
        return list(self._backtest_workers)

    @property
    def redis_url(self): return self._redis_url

    @property
    def catalog_path(self): return self._catalog_path

    @property
    def artifacts_path(self): return self._artifacts_path

    @property
    def db_url(self): return self._db_url

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def shutdown_all(self) -> None:
        """Stop backtest workers only. Trading nodes are independent containers."""
        self.stop_workers()
        self._redis.close()
        logger.info("ProcessManager: backtest workers shut down")
