"""Async watchdog that monitors TradingNode health via Redis heartbeats."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

if TYPE_CHECKING:
    from tinohelm.core.process_manager import ProcessManager

logger = logging.getLogger(__name__)

CHECK_INTERVAL_S = 10
RESTART_DELAY_S = 5


class Watchdog:
    """Background asyncio task that monitors node heartbeats and auto-restarts.

    The watchdog inspects Redis heartbeat keys every ``CHECK_INTERVAL_S`` seconds.
    If a heartbeat key has expired (TTL-based) but the node was expected to be
    running, it verifies process liveness and triggers a restart when the restart
    budget has not been exhausted.
    """

    def __init__(self, process_manager: ProcessManager, redis_url: str = "redis://localhost:6379") -> None:
        self._pm = process_manager
        self._redis: aioredis.Redis = aioredis.from_url(redis_url, decode_responses=True)
        self._task: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> asyncio.Task:
        """Start the watchdog loop and return the ``asyncio.Task``."""
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="tino-watchdog")
        logger.info("Watchdog started")
        return self._task

    async def stop(self) -> None:
        """Cancel the watchdog task and wait for it to finish."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._redis.close()
        logger.info("Watchdog stopped")

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                await self._check_nodes()
                await self._check_backtest_workers()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Watchdog check failed")
            await asyncio.sleep(CHECK_INTERVAL_S)

    async def _check_nodes(self) -> None:
        """Check sandbox and live node heartbeats."""
        for node_type in ("sandbox", "live"):
            info = self._pm.get_node_info(node_type)
            if info is None or info.status != "running":
                continue

            heartbeat = await self._redis.get(f"tino:heartbeat:{node_type}")

            if heartbeat is not None:
                # Heartbeat present -- node is healthy.
                continue

            # Heartbeat expired -- check process liveness.
            proc = info.process
            if proc is not None and proc.is_alive():
                # Process alive but heartbeat missing (Redis hiccup?). Log and skip.
                logger.warning(
                    "Heartbeat missing for %s but process (pid=%s) is alive; skipping restart",
                    node_type,
                    info.pid,
                )
                continue

            # Process is dead.
            if info.restart_count >= info.max_restarts:
                info.status = "error"
                logger.error(
                    "Node %s died and restart budget exhausted (%d/%d)",
                    node_type,
                    info.restart_count,
                    info.max_restarts,
                )
                continue

            # Restart with delay.
            info.restart_count += 1
            logger.warning(
                "Node %s died; restarting (%d/%d) after %ds delay",
                node_type,
                info.restart_count,
                info.max_restarts,
                RESTART_DELAY_S,
            )
            await asyncio.sleep(RESTART_DELAY_S)

            strategies = info.config.get("strategies", [])
            try:
                # Run the blocking start_node in a thread so we don't block the loop.
                await asyncio.to_thread(self._pm.start_node, node_type, strategies)
                # Preserve the updated restart_count on the new NodeInfo.
                new_info = self._pm.get_node_info(node_type)
                if new_info is not None:
                    new_info.restart_count = info.restart_count
            except Exception:
                logger.exception("Failed to restart %s node", node_type)
                info.status = "error"

    async def _check_backtest_workers(self) -> None:
        """Restart any dead backtest worker processes."""
        for i, proc in enumerate(self._pm.get_backtest_workers()):
            if proc.is_alive():
                continue

            logger.warning("Backtest worker %d (pid=%s) is dead; restarting", i, proc.pid)
            try:
                from tinohelm.backtest.worker import run_worker
                from multiprocessing import Process as _Process

                worker_args = (
                    self._pm.redis_url,
                    self._pm.catalog_path,
                    self._pm.artifacts_path,
                    self._pm.db_url,
                )
                new_proc = _Process(target=run_worker, args=worker_args, name=f"tino-bt-worker-{i}", daemon=True)
                new_proc.start()
                self._pm.replace_backtest_worker(i, new_proc)
                logger.info("Backtest worker %d restarted (pid=%s)", i, new_proc.pid)
            except Exception:
                logger.exception("Failed to restart backtest worker %d", i)
