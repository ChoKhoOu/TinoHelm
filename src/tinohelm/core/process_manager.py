"""Process manager for TradingNode subprocesses and backtest workers."""
from __future__ import annotations

import json
import logging
import signal
import time
from dataclasses import dataclass, field
from multiprocessing import Process
from typing import Any

import redis

logger = logging.getLogger(__name__)

VALID_NODE_TYPES = ("sandbox", "live")


@dataclass
class NodeInfo:
    """Metadata for a managed TradingNode subprocess."""

    process: Process | None = None
    pid: int | None = None
    status: str = "stopped"  # running | stopped | error
    node_type: str = ""
    restart_count: int = 0
    max_restarts: int = 3
    config: dict[str, Any] = field(default_factory=dict)


class ProcessManager:
    """Manages TradingNode subprocesses (sandbox/live) and backtest workers.

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
        self._nodes: dict[str, NodeInfo] = {}
        self._backtest_workers: list[Process] = []

    # ------------------------------------------------------------------
    # Node lifecycle
    # ------------------------------------------------------------------

    def start_node(self, node_type: str, strategies: list[str]) -> None:
        """Spawn a TradingNode subprocess for *node_type* (sandbox or live).

        The subprocess target is resolved to the corresponding ``run_node``
        entry-point inside ``tinohelm.node.<node_type>``.  The node config
        dict is built via :func:`tinohelm.node.factory.build_trading_node_config`
        and passed as the sole argument.
        """
        if node_type not in VALID_NODE_TYPES:
            raise ValueError(f"Invalid node_type {node_type!r}; must be one of {VALID_NODE_TYPES}")

        if node_type in self._nodes and self._nodes[node_type].status == "running":
            logger.warning("Node %s is already running (pid=%s)", node_type, self._nodes[node_type].pid)
            return

        from tinohelm.core.config import get_settings
        from tinohelm.node.factory import build_trading_node_config

        settings = get_settings()
        config = build_trading_node_config(node_type, strategies, settings)

        # Resolve entry-point
        if node_type == "sandbox":
            from tinohelm.node.sandbox import run_node
        else:
            from tinohelm.node.live import run_node

        proc = Process(target=run_node, args=(config,), name=f"tino-{node_type}", daemon=False)
        proc.start()

        info = self._nodes.get(node_type, NodeInfo())
        info.restart_count = 0
        info.process = proc
        info.pid = proc.pid
        info.status = "running"
        info.node_type = node_type
        info.config = {"strategies": config.get("strategies", []), "node_type": node_type}
        self._nodes[node_type] = info

        logger.info("Started %s node (pid=%s, strategies=%s)", node_type, proc.pid, strategies)

    def stop_node(self, node_type: str) -> None:
        """Gracefully stop a TradingNode subprocess.

        1. Publish a ``shutdown`` command on the Redis commands channel.
        2. Wait up to 10 s for the process to exit.
        3. Send SIGTERM and wait another 5 s.
        4. Send SIGKILL as a last resort.
        """
        info = self._nodes.get(node_type)
        if info is None or info.status != "running":
            logger.info("Node %s is not running; nothing to stop", node_type)
            return

        channel = f"tino:{node_type}:commands"
        self._redis.publish(channel, json.dumps({"cmd": "shutdown"}))
        logger.info("Published shutdown command to %s", channel)

        proc = info.process
        if proc is None:
            info.status = "stopped"
            return

        # Phase 1: wait for graceful exit
        proc.join(timeout=10)
        if not proc.is_alive():
            info.status = "stopped"
            logger.info("Node %s exited gracefully", node_type)
            return

        # Phase 2: SIGTERM
        logger.warning("Node %s did not exit in 10 s; sending SIGTERM", node_type)
        try:
            proc.terminate()
        except OSError:
            pass
        proc.join(timeout=5)
        if not proc.is_alive():
            info.status = "stopped"
            return

        # Phase 3: SIGKILL
        logger.warning("Node %s still alive after SIGTERM; sending SIGKILL", node_type)
        try:
            proc.kill()
        except OSError:
            pass
        proc.join(timeout=3)
        info.status = "stopped"
        logger.info("Node %s killed", node_type)

    # ------------------------------------------------------------------
    # Kill switch
    # ------------------------------------------------------------------

    def kill_switch(
        self,
        level: int,
        node_type: str = "live",
        strategy_id: str | None = None,
    ) -> None:
        """Execute an emergency kill switch at the given severity *level*.

        Level 1 -- Pause a single strategy (requires *strategy_id*).
        Level 2 -- Flatten all positions immediately.
        Level 3 -- Full shutdown: publish shutdown, SIGTERM, SIGKILL.
        """
        channel = f"tino:{node_type}:commands"

        if level == 1:
            if strategy_id is None:
                raise ValueError("strategy_id is required for kill-switch level 1")
            payload = {"cmd": "pause", "strategy_id": strategy_id}
            self._redis.publish(channel, json.dumps(payload))
            logger.warning("Kill-switch L1: paused strategy %s on %s", strategy_id, node_type)

        elif level == 2:
            payload = {"cmd": "flatten"}
            self._redis.publish(channel, json.dumps(payload))
            logger.warning("Kill-switch L2: flatten all positions on %s", node_type)

        elif level == 3:
            payload = {"cmd": "shutdown"}
            self._redis.publish(channel, json.dumps(payload))
            logger.critical("Kill-switch L3: shutdown %s node", node_type)
            time.sleep(2)

            info = self._nodes.get(node_type)
            if info and info.process and info.process.is_alive():
                try:
                    info.process.terminate()
                except OSError:
                    pass
                info.process.join(timeout=5)

                if info.process.is_alive():
                    try:
                        info.process.kill()
                    except OSError:
                        pass
                    info.process.join(timeout=3)

                info.status = "stopped"
                logger.critical("Kill-switch L3: %s node terminated", node_type)
        else:
            raise ValueError(f"Invalid kill-switch level {level}; must be 1, 2, or 3")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return status for all managed nodes and backtest workers.

        Reads Redis heartbeat keys (``tino:heartbeat:<node_type>``) to enrich
        the response with last-seen timestamps.
        """
        result: dict[str, Any] = {"nodes": {}, "backtest_workers": []}

        for node_type in VALID_NODE_TYPES:
            info = self._nodes.get(node_type)
            heartbeat_raw = self._redis.get(f"tino:heartbeat:{node_type}")

            if info is None:
                result["nodes"][node_type] = {
                    "status": "stopped",
                    "pid": None,
                    "restart_count": 0,
                    "heartbeat": None,
                }
                continue

            # Refresh status from process liveness
            if info.process and not info.process.is_alive() and info.status == "running":
                info.status = "error"

            heartbeat: dict[str, Any] | None = None
            if heartbeat_raw:
                try:
                    heartbeat = json.loads(heartbeat_raw)
                except (json.JSONDecodeError, TypeError):
                    heartbeat = {"raw": heartbeat_raw}

            result["nodes"][node_type] = {
                "status": info.status,
                "pid": info.pid,
                "restart_count": info.restart_count,
                "strategies": info.config.get("strategies", []),
                "heartbeat": heartbeat,
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
        """Start *n* backtest worker processes."""
        from tinohelm.backtest.worker import run_worker

        worker_args = (self._redis_url, self._catalog_path, self._artifacts_path, self._db_url)
        for i in range(n):
            proc = Process(target=run_worker, args=worker_args, name=f"tino-bt-worker-{i}", daemon=True)
            proc.start()
            self._backtest_workers.append(proc)
            logger.info("Started backtest worker %d (pid=%s)", i, proc.pid)

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

    def get_node_info(self, node_type: str):
        return self._nodes.get(node_type)

    def get_backtest_workers(self):
        return list(self._backtest_workers)

    def replace_backtest_worker(self, index: int, new_proc):
        self._backtest_workers[index] = new_proc

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
        """Stop every managed node and worker."""
        for node_type in list(self._nodes.keys()):
            self.stop_node(node_type)
        self.stop_workers()
        self._redis.close()
        logger.info("ProcessManager: all processes shut down")
