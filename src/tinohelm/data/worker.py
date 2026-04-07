"""Async data-fetch worker — consumes jobs from Redis queue.

Runs inside the API process (data fetching is I/O-bound, no need for
subprocess isolation like backtest workers). On API startup the lifespan
handler calls ``start_data_worker()`` which spawns the consumer loop and
recovers any interrupted jobs from the DB.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

import redis.asyncio as aioredis
from sqlalchemy import select, update

from tinohelm.db.models import DataFetchJob
from tinohelm.db.session import get_session_factory

logger = logging.getLogger(__name__)

QUEUE_KEY = "tino:data:queue"
_worker_task: asyncio.Task | None = None


async def enqueue_job(rds: aioredis.Redis, job_id: str) -> None:
    """Push a job_id onto the Redis data-fetch queue."""
    await rds.lpush(QUEUE_KEY, job_id)


async def recover_interrupted_jobs(rds: aioredis.Redis) -> int:
    """Re-queue jobs that were running or queued when the API last stopped.

    Called once during startup. Returns the number of recovered jobs.
    """
    factory = get_session_factory()
    recovered = 0
    async with factory() as db:
        # Mark running → queued (they were interrupted mid-flight)
        stmt = (
            update(DataFetchJob)
            .where(DataFetchJob.status == "running")
            .values(status="queued", progress=0, message="Recovered after restart")
        )
        result = await db.execute(stmt)
        recovered += result.rowcount  # type: ignore[assignment]

        # Re-queue all queued jobs
        rows = (await db.execute(
            select(DataFetchJob.job_id).where(DataFetchJob.status == "queued")
        )).scalars().all()
        for job_id in rows:
            await rds.lpush(QUEUE_KEY, job_id)

        await db.commit()

    if recovered:
        logger.info("Recovered %d interrupted data-fetch job(s)", recovered)
    return recovered


async def _process_job(job_id: str, redis_url: str, catalog_path: str) -> None:
    """Execute a single data-fetch job."""
    factory = get_session_factory()
    rds = aioredis.from_url(redis_url, decode_responses=True)
    progress_channel = f"tino:data:progress:{job_id}"

    try:
        # Load job from DB
        async with factory() as db:
            job = (await db.execute(
                select(DataFetchJob).where(DataFetchJob.job_id == job_id)
            )).scalar_one_or_none()

            if not job:
                logger.warning("Data-fetch job %s not found in DB, skipping", job_id)
                return

            if job.status == "cancelled":
                logger.info("Data-fetch job %s was cancelled, skipping", job_id)
                return

            # Mark running
            job.status = "running"
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

        # Progress callback — updates DB + publishes to Redis
        async def _progress(pct: int, msg: str):
            payload = {
                "job_id": job_id, "symbol": symbol, "data_type": data_type,
                "progress": pct, "message": msg,
            }
            if interval:
                payload["interval"] = interval
            await rds.publish(progress_channel, json.dumps(payload))
            # Throttle DB writes: only on significant changes
            if pct % 10 == 0 or pct >= 100:
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
                    status="completed",
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

    except Exception as exc:
        logger.exception("Data-fetch job %s failed: %s", job_id, exc)
        try:
            async with factory() as db:
                await db.execute(
                    update(DataFetchJob)
                    .where(DataFetchJob.job_id == job_id)
                    .values(
                        status="failed",
                        error=str(exc)[:2000],
                        completed_at=datetime.utcnow(),
                    )
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to update job %s status to failed", job_id)
    finally:
        await rds.close()


async def _consumer_loop(redis_url: str, catalog_path: str) -> None:
    """Infinite loop: pop job_ids from Redis queue and process them."""
    rds = aioredis.from_url(redis_url, decode_responses=True)
    logger.info("Data-fetch worker started (queue=%s)", QUEUE_KEY)

    try:
        while True:
            # BRPOP blocks up to 5s, returns (key, value) or None
            result = await rds.brpop(QUEUE_KEY, timeout=5)
            if result is None:
                continue
            _, job_id = result
            logger.info("Data-fetch worker picked up job %s", job_id)
            await _process_job(job_id, redis_url, catalog_path)
    except asyncio.CancelledError:
        logger.info("Data-fetch worker shutting down")
    finally:
        await rds.close()


def start_data_worker(redis_url: str, catalog_path: str) -> asyncio.Task:
    """Start the data-fetch consumer as a background asyncio task."""
    global _worker_task
    _worker_task = asyncio.create_task(
        _consumer_loop(redis_url, catalog_path),
        name="data-fetch-worker",
    )
    return _worker_task


def stop_data_worker() -> None:
    """Cancel the data-fetch worker task."""
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        _worker_task = None
