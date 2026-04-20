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
    STATUS_RUNNING,
    TimeThrottle,
    WorkerHandle,
    consumer_loop,
    enqueue_job as _shared_enqueue_job,
    requeue_running_jobs,
)
from tinohelm.db.models import DataFetchJob
from tinohelm.db.session import get_session_factory

logger = logging.getLogger(__name__)

QUEUE_KEY = "tino:data:queue"
PROGRESS_THROTTLE_INTERVAL = 2.0

_handle: WorkerHandle = WorkerHandle(name="data-fetch-worker")


async def enqueue_job(rds: aioredis.Redis, job_id: str) -> None:
    """Push a ``job_id`` onto the Redis data-fetch queue."""
    await _shared_enqueue_job(rds, QUEUE_KEY, job_id)


async def recover_interrupted_jobs(rds: aioredis.Redis) -> int:
    """Re-queue jobs that were running or queued when the API last stopped.

    Called once during startup. ``reset_queue=True`` clears Redis before the
    re-push so we can't double-enqueue anything still living in the list from
    before the restart. Returns the number of rows flipped running → queued.
    """
    return await requeue_running_jobs(
        get_session_factory(),
        DataFetchJob,
        rds,
        QUEUE_KEY,
        reset_queue=True,
    )


async def _process_job(job_id: str, redis_url: str, catalog_path: str) -> None:
    """Execute a single data-fetch job."""
    factory = get_session_factory()
    rds = aioredis.from_url(redis_url, decode_responses=True)
    progress_channel = f"tino:data:progress:{job_id}"
    symbol = data_type = ""

    try:
        # Load job from DB
        async with factory() as db:
            job = (await db.execute(
                select(DataFetchJob).where(DataFetchJob.job_id == job_id)
            )).scalar_one_or_none()

            if not job:
                logger.warning("Data-fetch job %s not found in DB, skipping", job_id)
                return

            if job.status == STATUS_CANCELLED:
                logger.info("Data-fetch job %s was cancelled, skipping", job_id)
                return

            # Mark running
            job.status = STATUS_RUNNING
            job.progress = 0
            job.message = "Starting..."
            await db.commit()

            # Capture params before session closes
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
                async with factory() as db2:
                    await db2.execute(
                        update(DataFetchJob)
                        .where(DataFetchJob.job_id == job_id)
                        .values(progress=pct, message=msg)
                    )
                    await db2.commit()

        # Run pipeline
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

        # Mark completed
        async with factory() as db:
            await db.execute(
                update(DataFetchJob)
                .where(DataFetchJob.job_id == job_id)
                .values(
                    status=STATUS_COMPLETED,
                    progress=100,
                    message=f"Done: {result.objects_count} objects",
                    completed_at=datetime.utcnow(),
                )
            )
            await db.commit()

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

    except Exception as exc:
        logger.exception("Data-fetch job %s failed: %s", job_id, exc)
        try:
            async with factory() as db:
                await db.execute(
                    update(DataFetchJob)
                    .where(DataFetchJob.job_id == job_id)
                    .values(
                        status=STATUS_FAILED,
                        error=str(exc)[:2000],
                        completed_at=datetime.utcnow(),
                    )
                )
                await db.commit()
            # Publish failure event → EventBridge → WS → toast
            await rds.publish("tino:data:events", json.dumps({
                "type": "data.fetch.failed",
                "job_id": job_id,
                "symbol": symbol,
                "data_type": data_type,
                "error": str(exc)[:200],
            }))
        except Exception:
            logger.exception("Failed to update job %s status to failed", job_id)
    finally:
        await rds.close()


def start_data_worker(redis_url: str, catalog_path: str) -> asyncio.Task:
    """Start the data-fetch consumer as a background asyncio task."""
    async def _process(job_id: str) -> None:
        await _process_job(job_id, redis_url, catalog_path)

    return _handle.start(
        lambda: consumer_loop(
            redis_url,
            QUEUE_KEY,
            _process,
            worker_label="Data-fetch worker",
        )
    )


def stop_data_worker() -> None:
    """Cancel the data-fetch worker task."""
    _handle.stop()
