"""Async watchdog that monitors node heartbeats and auto-scales backtest workers."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

if TYPE_CHECKING:
    from tinohelm.core.process_manager import ProcessManager

logger = logging.getLogger(__name__)

CHECK_INTERVAL_S = 10


class Watchdog:
    """Background asyncio task that monitors node heartbeats and backtest workers.

    The watchdog inspects Redis heartbeat keys every ``CHECK_INTERVAL_S`` seconds.
    Trading node lifecycle is managed by Docker — the watchdog only logs warnings
    when heartbeats are missing.  Backtest worker auto-scaling is handled via
    ``ProcessManager.ensure_capacity()``.
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
        """Check sandbox and live node heartbeats (Redis-only)."""
        for node_type in ("sandbox", "live"):
            state_raw = await self._redis.get(f"tino:node:state:{node_type}")
            if not state_raw:
                continue  # No state = node not configured

            heartbeat = await self._redis.get(f"tino:heartbeat:{node_type}")
            if heartbeat is not None:
                continue  # Healthy

            # Heartbeat missing — Docker's restart policy handles restart.
            logger.warning(
                "Heartbeat missing for %s node — Docker restart policy should handle recovery",
                node_type,
            )

    async def _check_backtest_workers(self) -> None:
        """Auto-scale backtest worker pool.

        Calls ``ensure_capacity`` which handles:
        - Pruning dead ephemeral workers
        - Maintaining minimum keep-alive workers
        - Spawning ephemeral workers when queue has pending jobs
        """
        try:
            await asyncio.to_thread(self._pm.ensure_capacity)
        except Exception:
            logger.exception("Failed to ensure worker capacity")
