"""Async research worker — consumes diagnosis jobs from Redis queue."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

import redis.asyncio as aioredis
from sqlalchemy import select, update

from tinohelm.db.models import ResearchJob
from tinohelm.db.session import get_session_factory

logger = logging.getLogger(__name__)

QUEUE_KEY = "tino:research:queue"
_worker_task: asyncio.Task | None = None


async def enqueue_job(rds: aioredis.Redis, job_id: str) -> None:
    """Push a job_id onto the Redis research queue."""
    await rds.lpush(QUEUE_KEY, job_id)


async def recover_interrupted_jobs(rds: aioredis.Redis) -> int:
    """Re-queue jobs that were running when the API last stopped."""
    factory = get_session_factory()
    recovered = 0
    async with factory() as db:
        stmt = (
            update(ResearchJob)
            .where(ResearchJob.status == "running")
            .values(status="queued", progress=0, message="Recovered after restart")
        )
        result = await db.execute(stmt)
        recovered += result.rowcount

        rows = (await db.execute(
            select(ResearchJob.job_id).where(ResearchJob.status == "queued")
        )).scalars().all()
        for job_id in rows:
            await rds.lpush(QUEUE_KEY, job_id)

        await db.commit()

    if recovered:
        logger.info("Recovered %d interrupted research job(s)", recovered)
    return recovered


async def _process_job(job_id: str, redis_url: str, catalog_path: str) -> None:
    """Execute a single research diagnosis job."""
    factory = get_session_factory()
    rds = aioredis.from_url(redis_url, decode_responses=True)
    progress_channel = f"tino:research:progress:{job_id}"
    symbol = factor_name = ""

    try:
        # Load job from DB
        async with factory() as db:
            job = (await db.execute(
                select(ResearchJob).where(ResearchJob.job_id == job_id)
            )).scalar_one_or_none()

            if not job:
                logger.warning("Research job %s not found, skipping", job_id)
                return

            if job.status == "cancelled":
                logger.info("Research job %s cancelled, skipping", job_id)
                return

            job.status = "running"
            job.progress = 0
            job.message = "Starting..."
            await db.commit()

            # Capture params
            symbol = job.symbol
            factor_name = job.factor_name
            data_type = job.data_type
            interval = job.interval
            start_date = str(job.start_date)
            end_date = str(job.end_date)
            params = job.parameters_json or {}

        # Progress callback
        async def _progress(pct: int, msg: str):
            await rds.publish(progress_channel, json.dumps({
                "job_id": job_id, "factor_name": factor_name, "symbol": symbol,
                "progress": pct, "message": msg,
            }))
            if pct % 10 == 0 or pct >= 100:
                async with factory() as db2:
                    await db2.execute(
                        update(ResearchJob)
                        .where(ResearchJob.job_id == job_id)
                        .values(progress=pct, message=msg)
                    )
                    await db2.commit()

        # Run report generation in thread (CPU-bound)
        from tinohelm.research.report import generate_report

        # Bridge async progress callback to sync for use in worker thread.
        # asyncio.run_coroutine_threadsafe() is the thread-safe way to
        # schedule a coroutine on the event loop from another thread.
        loop = asyncio.get_running_loop()

        def _sync_progress(pct: int, msg: str):
            asyncio.run_coroutine_threadsafe(_progress(pct, msg), loop)

        result = await asyncio.to_thread(
            generate_report,
            job_id=job_id,
            factor_name=factor_name,
            symbol=symbol,
            data_type=data_type,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            factor_params=params.get("factor_params", {}),
            forward_periods=params.get("forward_periods", [5, 15, 30]),
            n_quantiles=params.get("quantiles", 5),
            shuffle_iterations=params.get("shuffle_iterations", 1000),
            cross_symbols=params.get("cross_symbols"),
            param_scan_config=params.get("param_scan"),
            fee_rate=params.get("fee_rate", 0.0004),
            slippage_bps=params.get("slippage_bps", 1.0),
            catalog_path=catalog_path,
            progress_cb=_sync_progress,
        )

        # Mark completed
        from tinohelm.research.analysis import sanitize_for_json
        report = result.get("report", {})
        async with factory() as db:
            await db.execute(
                update(ResearchJob)
                .where(ResearchJob.job_id == job_id)
                .values(
                    status="completed",
                    progress=100,
                    message="Done",
                    result_path=result.get("path"),
                    rating=report.get("summary", {}).get("rating"),
                    verdict_json=sanitize_for_json(report.get("verdict")),
                    completed_at=datetime.utcnow(),
                )
            )
            await db.commit()

        logger.info("Research job %s completed: %s %s", job_id, factor_name, symbol)

        # Publish completion event → toast
        await rds.publish("tino:research:events", json.dumps({
            "type": "research.completed",
            "job_id": job_id,
            "factor_name": factor_name,
            "symbol": symbol,
            "rating": report.get("summary", {}).get("rating", 0),
            "verdict": report.get("verdict", {}),
        }))

    except Exception as exc:
        logger.exception("Research job %s failed: %s", job_id, exc)
        try:
            async with factory() as db:
                await db.execute(
                    update(ResearchJob)
                    .where(ResearchJob.job_id == job_id)
                    .values(
                        status="failed",
                        error=str(exc)[:2000],
                        completed_at=datetime.utcnow(),
                    )
                )
                await db.commit()
            await rds.publish("tino:research:events", json.dumps({
                "type": "research.failed",
                "job_id": job_id,
                "factor_name": factor_name,
                "symbol": symbol,
                "error": str(exc)[:200],
            }))
        except Exception:
            logger.exception("Failed to update research job %s to failed", job_id)
    finally:
        await rds.close()


async def _consumer_loop(redis_url: str, catalog_path: str) -> None:
    """Infinite loop: pop job_ids from Redis queue and process them."""
    rds = aioredis.from_url(redis_url, decode_responses=True)
    logger.info("Research worker started (queue=%s)", QUEUE_KEY)

    try:
        while True:
            result = await rds.brpop(QUEUE_KEY, timeout=5)
            if result is None:
                continue
            _, job_id = result
            logger.info("Research worker picked up job %s", job_id)
            await _process_job(job_id, redis_url, catalog_path)
    except asyncio.CancelledError:
        logger.info("Research worker shutting down")
    finally:
        await rds.close()


def start_research_worker(redis_url: str, catalog_path: str) -> asyncio.Task:
    """Start the research worker as a background asyncio task."""
    global _worker_task
    _worker_task = asyncio.create_task(
        _consumer_loop(redis_url, catalog_path),
        name="research-worker",
    )
    return _worker_task


def stop_research_worker() -> None:
    """Cancel the research worker task."""
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        _worker_task = None
