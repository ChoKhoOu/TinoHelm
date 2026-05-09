"""Async data-fetch worker — consumes jobs from Redis queue.

Runs inside the API process (data fetching is I/O-bound, no need for
subprocess isolation like backtest workers). On API startup the lifespan
handler calls ``start_data_worker()`` which spawns the consumer loop and
recovers any interrupted jobs from the DB.

The generic queue-worker primitives live in
``tinohelm.core.async_queue_worker``; this module is only the data-fetch
specific job body plus the module-level singleton handle.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

import redis.asyncio as aioredis
from sqlalchemy import select, update

from tinohelm.core.async_queue_worker import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    TimeThrottle,
    WorkerHandle,
    consumer_loop,
    enqueue_job as _shared_enqueue_job,
    requeue_running_jobs,
)
from tinohelm.core.config import get_settings
from tinohelm.data import catalog_locks as _catalog_locking
from tinohelm.db.models import DataFetchJob
from tinohelm.db.session import get_session_factory

logger = logging.getLogger(__name__)

QUEUE_KEY = "tino:data:queue"
PROGRESS_THROTTLE_INTERVAL = 2.0
LOCK_BUSY_REQUEUE_DELAY = 1.0

_handle: WorkerHandle = WorkerHandle(name="data-fetch-worker")
_catalog_locks = _catalog_locking._catalog_locks
_catalog_lock_key = _catalog_locking.catalog_lock_key
_get_catalog_lock = _catalog_locking.get_catalog_lock
_catalog_lock_attempt_guard = asyncio.Lock()


async def enqueue_job(rds: aioredis.Redis, job_id: str) -> None:
    """Push a ``job_id`` onto the Redis data-fetch queue."""
    await _shared_enqueue_job(rds, QUEUE_KEY, job_id)


async def recover_interrupted_jobs(rds: aioredis.Redis) -> int:
    """Re-queue jobs that were running or queued when the API last stopped.

    Called once during startup. ``reset_queue=True`` clears Redis before the
    re-push so we can't double-enqueue anything still living in the list from
    before the restart. Returns the number of rows flipped running → queued.
    """
    restored = await _recover_pending_ingest_rollbacks_on_startup()
    if restored:
        logger.warning("Recovered %d pending ingest rollback object(s) before requeue", restored)
    return await requeue_running_jobs(
        get_session_factory(),
        DataFetchJob,
        rds,
        QUEUE_KEY,
        reset_queue=True,
    )


async def _recover_pending_ingest_rollbacks_on_startup() -> int:
    """Restore crash-stranded ingest rollback objects before consumers start."""
    from tinohelm.data.pipeline import recover_pending_ingest_rollbacks_async
    from tinohelm.data.storage import get_catalog_storage

    settings = get_settings()
    storage = get_catalog_storage(settings=settings, catalog_root=settings.paths.catalog)
    return await recover_pending_ingest_rollbacks_async(storage.catalog_root, storage=storage)


def _rowcount(result) -> int:
    value = getattr(result, "rowcount", None)
    return value if isinstance(value, int) else 0


def _clear_current_task_cancellation() -> int:
    current_task = asyncio.current_task()
    cleared = 0
    while current_task is not None and current_task.cancelling():
        current_task.uncancel()
        cleared += 1
    return cleared


async def _await_preserving_cancellation(awaitable):
    """Await a critical awaitable to completion without losing caller cancellation.

    The inner awaitable is shielded so durable terminal DB/event writes can
    finish during shutdown. Any cancellation request received while waiting is
    reported to the caller, which must re-raise after the critical section.
    If the critical awaitable itself fails after such a cancellation, shutdown
    still wins; log the inner failure and surface ``CancelledError``.
    """
    task = asyncio.ensure_future(awaitable)
    was_cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            return result, was_cancelled
        except asyncio.CancelledError:
            was_cancelled = True
            _clear_current_task_cancellation()
            if not task.done():
                continue
        except Exception as exc:
            if was_cancelled:
                logger.exception("Critical terminal update failed after cancellation")
                raise asyncio.CancelledError from exc
            raise
        try:
            return task.result(), was_cancelled
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if was_cancelled:
                logger.exception("Critical terminal update failed after cancellation")
                raise asyncio.CancelledError from exc
            raise


async def _load_queued_job(factory, job_id: str):
    async with factory() as db:
        return (await db.execute(
            select(DataFetchJob)
            .where(DataFetchJob.job_id == job_id)
            .where(DataFetchJob.status == STATUS_QUEUED)
        )).scalar_one_or_none()


async def _claim_queued_job(factory, job_id: str):
    async with factory() as db:
        result = await db.execute(
            update(DataFetchJob)
            .where(DataFetchJob.job_id == job_id)
            .where(DataFetchJob.status == STATUS_QUEUED)
            .values(
                status=STATUS_RUNNING,
                progress=0,
                message="Starting...",
                error=None,
                completed_at=None,
            )
        )
        await db.commit()
        if _rowcount(result) != 1:
            return None

    async with factory() as db:
        job = (await db.execute(
            select(DataFetchJob).where(DataFetchJob.job_id == job_id)
        )).scalar_one_or_none()
    if not job:
        return None
    if getattr(job, "status", STATUS_RUNNING) == STATUS_CANCELLED:
        return None
    return job


async def _try_acquire_catalog_lock(lock_key: str):
    """Acquire a catalog lock only if immediately available."""
    async with _catalog_lock_attempt_guard:
        lock = _get_catalog_lock(lock_key)
        if lock.locked():
            return None
        await lock.acquire()
        return lock


async def _guarded_terminal_update(factory, job_id: str, values: dict) -> bool:
    async with factory() as db:
        result = await db.execute(
            update(DataFetchJob)
            .where(DataFetchJob.job_id == job_id)
            .where(DataFetchJob.status == STATUS_RUNNING)
            .values(**values)
        )
        await db.commit()
    return _rowcount(result) == 1


async def _guarded_queued_failure_update(factory, job_id: str, values: dict) -> bool:
    async with factory() as db:
        result = await db.execute(
            update(DataFetchJob)
            .where(DataFetchJob.job_id == job_id)
            .where(DataFetchJob.status == STATUS_QUEUED)
            .values(**values)
        )
        await db.commit()
    return _rowcount(result) == 1


async def _requeue_queued_job_best_effort(rds: aioredis.Redis, job_id: str) -> None:
    try:
        await rds.lpush(QUEUE_KEY, job_id)
    except Exception:
        logger.warning("Failed to requeue pre-claim data-fetch job %s", job_id, exc_info=True)


async def _defer_locked_queued_job(rds: aioredis.Redis, job_id: str) -> None:
    if LOCK_BUSY_REQUEUE_DELAY > 0:
        await asyncio.sleep(LOCK_BUSY_REQUEUE_DELAY)
    await rds.lpush(QUEUE_KEY, job_id)


async def _job_is_still_running(factory, job_id: str) -> bool:
    async with factory() as db:
        job = (await db.execute(
            select(DataFetchJob.status).where(DataFetchJob.job_id == job_id)
        )).scalar_one_or_none()
    return job == STATUS_RUNNING


async def _persist_progress_if_running(factory, job_id: str, pct: int, msg: str) -> None:
    async with factory() as db:
        await db.execute(
            update(DataFetchJob)
            .where(DataFetchJob.job_id == job_id)
            .where(DataFetchJob.status == STATUS_RUNNING)
            .values(progress=pct, message=msg)
        )
        await db.commit()


async def _process_job(job_id: str, redis_url: str, catalog_path: str) -> None:
    """Execute a single data-fetch job."""
    factory = get_session_factory()
    rds = aioredis.from_url(redis_url, decode_responses=True)
    progress_channel = f"tino:data:progress:{job_id}"
    symbol = data_type = ""
    queued_loaded = False
    claimed = False

    try:
        queued_job = await _load_queued_job(factory, job_id)
        if queued_job is None:
            logger.info("Data-fetch job %s is not queued, skipping stale queue item", job_id)
            return

        queued_loaded = True
        symbol = queued_job.symbol
        data_type = queued_job.data_type

        lock_key = _catalog_lock_key(queued_job.symbol, queued_job.data_type, queued_job.interval)
        lock = await _try_acquire_catalog_lock(lock_key)
        if lock is None:
            logger.info(
                "Data-fetch job %s deferred because catalog lock %s is busy",
                job_id,
                lock_key,
            )
            await _defer_locked_queued_job(rds, job_id)
            return
        try:
            job = await _claim_queued_job(factory, job_id)
            if job is None:
                logger.info("Data-fetch job %s was cancelled before ingest, skipping catalog mutation", job_id)
                return
            claimed = True

            symbol = job.symbol
            data_type = job.data_type
            interval = job.interval
            start_date = job.start_date
            end_date = job.end_date
            asset_class = job.asset_class

            # Progress callback — publishes to Redis + throttled DB write
            throttle = TimeThrottle(interval=PROGRESS_THROTTLE_INTERVAL)

            async def _progress(pct: int, msg: str):
                payload = {
                    "type": "data.fetch.progress",
                    "job_id": job_id, "symbol": symbol, "data_type": data_type,
                    "progress": pct, "message": msg,
                }
                if interval:
                    payload["interval"] = interval
                await rds.publish(progress_channel, json.dumps(payload))
                if throttle.should_write(pct):
                    await _persist_progress_if_running(factory, job_id, pct, msg)

            # Run pipeline.  Same catalog targets are serialized across local
            # consumers so cleanup/write/update is one critical section per key.
            from tinohelm.data.pipeline import BinanceVisionPipeline

            pipeline = BinanceVisionPipeline(catalog_path=catalog_path)
            result = await pipeline.ingest(
                symbol=symbol,
                data_type=data_type,
                start=start_date,
                end=end_date,
                asset_class=asset_class,
                interval=interval,
                progress_cb=_progress,
            )
        finally:
            lock.release()

        async def _complete_success() -> bool:
            updated = await _guarded_terminal_update(
                factory,
                job_id,
                {
                    "status": STATUS_COMPLETED,
                    "progress": 100,
                    "message": f"Done: {result.objects_count} objects",
                    "completed_at": datetime.utcnow(),
                },
            )
            if not updated:
                return False

            logger.info(
                "Data-fetch job %s completed: %s %s — %d objects",
                job_id, symbol, data_type, result.objects_count,
            )

            # Publish completion event → EventBridge → WS → toast
            await rds.publish("tino:data:events", json.dumps({
                "type": "data.fetch.completed",
                "job_id": job_id,
                "symbol": symbol,
                "data_type": data_type,
                "objects_count": result.objects_count,
            }))
            return True

        updated, terminal_update_was_cancelled = await _await_preserving_cancellation(
            _complete_success()
        )
        if not updated:
            logger.info("Data-fetch job %s was no longer running at completion, skipping terminal event", job_id)
        if terminal_update_was_cancelled:
            raise asyncio.CancelledError
        if not updated:
            return

    except asyncio.CancelledError:
        if queued_loaded and not claimed:
            try:
                claimed = await _job_is_still_running(factory, job_id)
            except Exception:
                logger.warning("Failed to re-read job %s after pre-claim cancellation", job_id, exc_info=True)
            if not claimed:
                await _requeue_queued_job_best_effort(rds, job_id)
        raise
    except Exception as exc:
        failure_error = str(exc)
        logger.exception("Data-fetch job %s failed: %s", job_id, exc)
        try:
            if queued_loaded and not claimed:
                try:
                    claimed = await _job_is_still_running(factory, job_id)
                except Exception:
                    logger.warning("Failed to re-read job %s after pre-claim failure", job_id, exc_info=True)
            if queued_loaded and not claimed:
                updated = await _guarded_queued_failure_update(
                    factory,
                    job_id,
                    {
                        "status": STATUS_FAILED,
                        "error": failure_error[:2000],
                        "completed_at": datetime.utcnow(),
                    },
                )
                if updated:
                    await rds.publish("tino:data:events", json.dumps({
                        "type": "data.fetch.failed",
                        "job_id": job_id,
                        "symbol": symbol,
                        "data_type": data_type,
                        "error": failure_error[:200],
                    }))
                return

            async def _complete_failure() -> bool:
                updated = await _guarded_terminal_update(
                    factory,
                    job_id,
                    {
                        "status": STATUS_FAILED,
                        "error": failure_error[:2000],
                        "completed_at": datetime.utcnow(),
                    },
                )
                if updated:
                    # Publish failure event → EventBridge → WS → toast
                    await rds.publish("tino:data:events", json.dumps({
                        "type": "data.fetch.failed",
                        "job_id": job_id,
                        "symbol": symbol,
                        "data_type": data_type,
                        "error": failure_error[:200],
                    }))
                return updated

            _, failure_update_was_cancelled = await _await_preserving_cancellation(
                _complete_failure()
            )
            if failure_update_was_cancelled:
                raise asyncio.CancelledError
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to update job %s status to failed", job_id)
    finally:
        await rds.close()


def start_data_worker(redis_url: str, catalog_path: str) -> asyncio.Task:
    """Start the data-fetch consumer as a background asyncio task."""
    async def _process(job_id: str) -> None:
        await _process_job(job_id, redis_url, catalog_path)

    async def _run_consumer_forever(idx: int) -> None:
        backoff = 0.1
        while True:
            try:
                await consumer_loop(
                    redis_url,
                    QUEUE_KEY,
                    _process,
                    worker_label="Data-fetch worker",
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Data-fetch consumer %d crashed; restarting in %.1fs",
                    idx,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _run_consumers() -> None:
        concurrency = max(1, get_settings().data.job_concurrency)
        tasks = [
            asyncio.create_task(
                _run_consumer_forever(idx),
                name=f"data-fetch-consumer-{idx}",
            )
            for idx in range(concurrency)
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    return _handle.start(_run_consumers)


def stop_data_worker() -> None:
    """Cancel the data-fetch worker task."""
    _handle.stop()


async def stop_data_worker_and_wait(timeout: float = 30.0) -> None:
    """Cancel the data-fetch worker and wait for bounded shutdown cleanup."""
    task = _handle.task
    stop_data_worker()
    if task is None:
        return
    if task.done():
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.CancelledError:
        if task.done():
            return
        raise
    except TimeoutError:
        logger.warning(
            "Timed out waiting %.1fs for data-fetch worker shutdown; startup recovery will requeue leftovers",
            timeout,
        )
    except Exception:
        logger.exception("Data-fetch worker failed during shutdown")
