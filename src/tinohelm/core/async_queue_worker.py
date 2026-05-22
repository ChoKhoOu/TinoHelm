"""Shared primitives for async Redis-backed queue workers.

`research/worker.py` and `data/worker.py` are two independent implementations
of the same pattern — an infinite ``BRPOP`` loop that pops job IDs off a Redis
list, looks them up in PostgreSQL, executes the work, and publishes lifecycle
events. Both modules are ~220 lines each and diverged over time (different
progress-throttle heuristics, different recovery behaviour, subtly different
task-handle management). This module consolidates the parts that are the same
into testable primitives; the two workers delegate to these helpers and keep
only their job-specific logic.

The primitives are intentionally narrow — they cover queue fan-in, DB-level
``running → queued`` recovery, singleton asyncio task management, and the two
progress-persistence strategies in use. They deliberately do **not** abstract
the job body itself: the DB-schema-specific completion/failure writes differ
too much between the two workers to deduplicate without a leaky hook.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

import redis.asyncio as aioredis
from sqlalchemy import select, update

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job status tokens
# ---------------------------------------------------------------------------

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_PARTIAL_COMPLETED = "partial_completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Queue fan-in / recovery
# ---------------------------------------------------------------------------

async def enqueue_job(
    rds: aioredis.Redis,
    queue_key: str,
    job_id: str,
) -> None:
    """LPUSH ``job_id`` onto ``queue_key``.

    Kept as a helper (rather than an inline one-liner) so the queue key is
    always explicit at the call site and callers can be mocked uniformly.
    """
    await rds.lpush(queue_key, job_id)


async def requeue_running_jobs(
    session_factory: Callable[[], Any],
    model_cls: Any,
    rds: aioredis.Redis,
    queue_key: str,
    *,
    reset_queue: bool = False,
    recovery_message: str = "Recovered after restart",
) -> int:
    """Reset orphaned jobs and push their IDs back onto the Redis queue.

    Called once at worker startup. Runs the two-step recovery sequence:

    1. Update every row where ``status == "running"`` to ``status = "queued"``
       (they were mid-flight when the previous process died).
    2. Collect every ``status == "queued"`` job and LPUSH its ``job_id`` back
       onto the Redis queue.

    If ``reset_queue`` is True, the Redis list is cleared before the re-push
    — used by workers that want to guarantee no duplicates relative to
    whatever was already queued in Redis at restart time.

    Returns the number of rows flipped from running to queued.
    """
    recovered = 0
    async with session_factory() as db:
        stmt = (
            update(model_cls)
            .where(model_cls.status == STATUS_RUNNING)
            .values(
                status=STATUS_QUEUED,
                progress=0,
                message=recovery_message,
            )
        )
        result = await db.execute(stmt)
        recovered += result.rowcount or 0

        queued_ids = (
            await db.execute(
                select(model_cls.job_id).where(model_cls.status == STATUS_QUEUED)
            )
        ).scalars().all()

        if queued_ids:
            if reset_queue:
                await rds.delete(queue_key)
            for job_id in queued_ids:
                await rds.lpush(queue_key, job_id)

        await db.commit()

    if recovered:
        logger.info(
            "Recovered %d interrupted job(s) from %s",
            recovered,
            getattr(model_cls, "__tablename__", model_cls.__name__),
        )
    return recovered


# ---------------------------------------------------------------------------
# Consumer loop
# ---------------------------------------------------------------------------

async def consumer_loop(
    redis_url: str,
    queue_key: str,
    process_job: Callable[[str], Awaitable[None]],
    *,
    pop_timeout: float = 5.0,
    worker_label: str = "queue-worker",
) -> None:
    """Infinite BRPOP loop — the canonical queue consumer body.

    Cancels cleanly: a task running this coroutine can be cancelled via
    ``task.cancel()``; the ``CancelledError`` is caught, a shutdown log line
    is emitted, and the Redis connection is closed.
    """
    rds = aioredis.from_url(redis_url, decode_responses=True)
    logger.info("%s started (queue=%s)", worker_label, queue_key)

    try:
        while True:
            result = await rds.brpop(queue_key, timeout=pop_timeout)
            if result is None:
                continue
            _, job_id = result
            logger.info("%s picked up job %s", worker_label, job_id)
            await process_job(job_id)
    except asyncio.CancelledError:
        logger.info("%s shutting down", worker_label)
    finally:
        await rds.close()


# ---------------------------------------------------------------------------
# Task-handle
# ---------------------------------------------------------------------------

class WorkerHandle:
    """Singleton owner of a single long-running asyncio consumer task.

    The previous hand-written workers each used a module-level
    ``_worker_task: asyncio.Task | None`` variable plus a start/stop pair.
    This class captures the same semantics without leaking the global:

    - ``start(factory)`` creates a new task from ``factory()`` and remembers it
    - ``stop()`` cancels the task (no-op if already done or never started)
    - ``is_running()`` returns True only if the task exists and hasn't finished

    Callers that want module-level singletons can keep a single
    ``_handle = WorkerHandle(name="…")`` at module scope.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._task: asyncio.Task | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def task(self) -> asyncio.Task | None:
        return self._task

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(
        self,
        coro_factory: Callable[[], Awaitable[None]],
    ) -> asyncio.Task:
        """Create the asyncio task and take ownership.

        Raises ``RuntimeError`` if a task is already running — callers should
        ``stop()`` first. Tasks that finished (``done()``) are overwritten.
        """
        if self.is_running():
            raise RuntimeError(f"Worker {self._name!r} already running")
        self._task = asyncio.create_task(coro_factory(), name=self._name)
        return self._task

    def stop(self) -> None:
        """Cancel the task if running and keep ownership until cancellation settles."""
        if self._task is None:
            return
        if self._task.done():
            self._task = None
            return
        task = self._task

        def _clear_stopped_task(done_task: asyncio.Task) -> None:
            if self._task is done_task:
                self._task = None

        task.cancel()
        task.add_done_callback(_clear_stopped_task)


# ---------------------------------------------------------------------------
# Progress throttles
# ---------------------------------------------------------------------------

class PercentStepThrottle:
    """Write progress whenever ``pct`` lands on a step boundary.

    Emits True when ``pct == 0``, ``pct >= 100``, or ``pct % step == 0``.
    Stateless: safe to share across jobs.
    """

    def __init__(self, step: int = 10) -> None:
        if step <= 0:
            raise ValueError("step must be positive")
        self._step = step

    @property
    def step(self) -> int:
        return self._step

    def should_write(self, pct: int) -> bool:
        if pct <= 0:
            return True
        if pct >= 100:
            return True
        return pct % self._step == 0


class TimeThrottle:
    """Write progress when enough wall-clock time has elapsed.

    Always emits True at ``pct == 0`` and ``pct >= 100`` regardless of elapsed
    time, so boundary updates are never swallowed. Between those, returns
    True once every ``interval`` seconds (measured against ``now_fn``, which
    defaults to ``time.monotonic``).

    Stateful: the ``_last_write`` timestamp is advanced on every True.
    Construct one throttle per job; do not share across jobs.
    """

    def __init__(
        self,
        interval: float = 2.0,
        *,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self._interval = interval
        self._now_fn = now_fn or time.monotonic
        self._last_write = 0.0

    @property
    def interval(self) -> float:
        return self._interval

    def should_write(self, pct: int) -> bool:
        now = self._now_fn()
        if pct <= 0 or pct >= 100 or (now - self._last_write) >= self._interval:
            self._last_write = now
            return True
        return False


__all__ = [
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_CANCELLED",
    "enqueue_job",
    "requeue_running_jobs",
    "consumer_loop",
    "WorkerHandle",
    "PercentStepThrottle",
    "TimeThrottle",
]
