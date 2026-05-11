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
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import Integer, and_, func, select, update

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
# Opaque sentinel pushed onto QUEUE_KEY solely to wake a BRPOP'ing consumer.
# After #164, the DB is the scheduling source of truth — Redis never carries
# job_ids any more, just "there might be new work, come drain". Any value the
# worker pops off the queue triggers a DB drain loop; we use a stable constant
# so that logs / monitoring are readable and stale tokens are obvious.
WAKE_TOKEN = "wake"
PROGRESS_THROTTLE_INTERVAL = 2.0
LOCK_BUSY_REQUEUE_DELAY = 1.0

_handle: WorkerHandle = WorkerHandle(name="data-fetch-worker")
_catalog_locks = _catalog_locking._catalog_locks
_catalog_lock_key = _catalog_locking.catalog_lock_key
_get_catalog_lock = _catalog_locking.get_catalog_lock
_catalog_lock_attempt_guard = asyncio.Lock()


async def enqueue_job(rds: aioredis.Redis, job_id: str) -> None:
    """Wake the data-fetch worker to drain queued jobs from the DB.

    The ``job_id`` argument is kept for call-site compatibility (and for
    logging), but it is *not* pushed onto the Redis list — the job is already
    queued in the DB, and the worker picks runnable jobs from persistent
    state. What lands on Redis is a coarse wake sentinel (``WAKE_TOKEN``) so
    any BRPOP'ing consumer returns and enters a DB drain loop.
    """
    await _shared_enqueue_job(rds, QUEUE_KEY, WAKE_TOKEN)


async def _flip_running_to_queued(factory, model_cls) -> int:
    """Flip every ``status='running'`` row back to ``queued``.

    Returns the number of rows touched. Used only during startup recovery —
    once the worker is live, scheduling runs via ``claim_next_queued_job``.
    """
    async with factory() as db:
        result = await db.execute(
            update(model_cls)
            .where(model_cls.status == STATUS_RUNNING)
            .values(
                status=STATUS_QUEUED,
                progress=0,
                message="Recovered after restart",
            )
        )
        await db.commit()
    return result.rowcount or 0


async def _count_queued_jobs(factory, model_cls) -> int:
    async with factory() as db:
        result = await db.execute(
            select(func.count())
            .select_from(model_cls)
            .where(model_cls.status == STATUS_QUEUED)
        )
        count = result.scalar_one()
    return int(count or 0)


async def backfill_legacy_batch_ids(factory) -> int:
    """Assign ``batch_id`` to every pre-#163 ``DataFetchJob`` row.

    Legacy rows written before the ``batch_id`` column existed arrive with
    ``batch_id IS NULL``. The new scheduler (#164/#165) tolerates that via
    ``COALESCE(batch_id, job_id)`` but loses the "these were one submission"
    hint — every legacy row becomes its own FetchBatch and original grouping
    is gone.

    PRD #162 decisions #23–#24 resolve that by grouping legacy rows under a
    rule-based, deterministic key: rows sharing ``created_at`` (the closest
    proxy for "same submission" after-the-fact, since a fetch-batch insert
    commits all rows under one server-side ``now()``) receive one shared
    batch_id. Rows whose ``created_at`` is unique still get a batch_id —
    they become single-job FetchBatches, matching how the new scheduler
    already treats backtest-triggered standalone fetches.

    Returns the number of rows touched so callers can log rollout coverage.
    Running it a second time is a no-op.
    """
    from uuid import uuid4 as _uuid4

    async with factory() as db:
        result = await db.execute(
            select(DataFetchJob.created_at, func.count(DataFetchJob.id))
            .where(DataFetchJob.batch_id.is_(None))
            .group_by(DataFetchJob.created_at)
            .order_by(DataFetchJob.created_at.asc())
        )
        groups = result.all()
        if not groups:
            return 0

        # One UPDATE per distinct created_at — typically G ≪ N for legacy
        # backlog, since a fetch-batch submission shares one server-side
        # ``now()``. The ``batch_id IS NULL`` guard keeps the rewrite
        # idempotent and leaves any post-#163 row untouched.
        touched = 0
        for created_at, group_size in groups:
            batch_id = str(_uuid4())
            await db.execute(
                update(DataFetchJob)
                .where(DataFetchJob.batch_id.is_(None))
                .where(DataFetchJob.created_at == created_at)
                .values(batch_id=batch_id)
            )
            touched += int(group_size or 0)
        await db.commit()
    return touched


async def recover_interrupted_jobs(rds: aioredis.Redis) -> int:
    """Startup recovery for the DB-driven fetch scheduler.

    After #164 Redis no longer holds job_ids, so there is nothing to
    "re-enqueue" per job. Recovery does three things:

    1. Flip every ``status='running'`` row back to ``queued`` — whoever
       was in flight when the process died re-enters the runnable pool.
    2. Clear the legacy Redis list so any stale job_id tokens left over
       from the pre-#164 scheduler are discarded.
    3. If any queued work exists, push a single ``WAKE_TOKEN`` so the
       first idle consumer immediately drains the backlog. No wake is
       needed when the DB is empty — new fetch requests will push their
       own wake tokens as they arrive.

    Returns the number of rows flipped running → queued.
    """
    restored = await _recover_pending_ingest_rollbacks_on_startup()
    if restored:
        logger.warning("Recovered %d pending ingest rollback object(s) before requeue", restored)

    factory = get_session_factory()
    recovered = await _flip_running_to_queued(factory, DataFetchJob)

    # #166: adopt any legacy pre-#163 backlog into the new scheduler model
    # by backfilling ``batch_id`` on rows still carrying NULL. We do this
    # *before* clearing the legacy Redis list so a mid-migration crash
    # leaves the wake signal in place — the next startup will retry
    # backfill and re-assert scheduler ownership rather than silently
    # losing both.
    try:
        adopted = await backfill_legacy_batch_ids(factory)
    except Exception:
        logger.exception(
            "Legacy batch_id backfill failed; continuing with startup. "
            "Untouched NULL batch_id rows still schedule as single-job batches."
        )
        adopted = 0
    if adopted:
        logger.info(
            "Adopted %d legacy DataFetchJob row(s) into the new scheduler "
            "by grouping on shared created_at",
            adopted,
        )

    queued_count = await _count_queued_jobs(factory, DataFetchJob)

    # Purge any legacy Redis backlog — scheduling truth is now the DB.
    try:
        await rds.delete(QUEUE_KEY)
    except Exception:
        logger.warning("Failed to clear legacy %s list on startup", QUEUE_KEY, exc_info=True)

    if queued_count:
        await _shared_enqueue_job(rds, QUEUE_KEY, WAKE_TOKEN)
        logger.info(
            "Recovery: %d queued job(s) pending; pushed one wake token",
            queued_count,
        )

    if recovered:
        logger.info(
            "Recovered %d interrupted DataFetchJob(s) on startup",
            recovered,
        )
    return recovered


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


_BUCKET_STARTED_STATUSES = (
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
)
_BUCKET_RUNNING_STATUSES = (STATUS_RUNNING,)


def _next_queued_job_id_subquery():
    """Pick the next runnable DataFetchJob per #165 fairness + #166 soft FIFO.

    Ordering keys, in priority:
      1. ``bucket_running_count > 0`` ascending — #166 soft FIFO. If the
         older batch's only queued rows sit in buckets that already have
         a running job (catalog lock busy), let a newer batch's idle
         bucket fill the worker slot rather than sleeping. Strict FIFO
         within the same "bucket is idle" class below preserves order
         when older still has idle capacity of its own.
      2. ``batch_created_at`` ascending — cross-batch FIFO (older batch first).
      3. Per-FetchBucket ``started_count`` ascending inside that batch —
         least-progressed bucket first, where bucket =
         ``(symbol, data_type, interval)`` and started_count counts
         ``running + completed + failed + cancelled`` (PRD #162 decision).
      4. ``start_date`` ascending inside a bucket — preserve chronological
         catalog-consistent ingest order (#165 AC).
      5. ``created_at, id`` as a deterministic tiebreaker.

    ``batch_created_at`` is derived as the earliest ``created_at`` of any job
    sharing the same ``batch_id``; jobs with NULL batch_id fall back to their
    own ``created_at`` so legacy pre-#163 rows still participate.
    """
    batch_col = func.coalesce(DataFetchJob.batch_id, DataFetchJob.job_id)

    batch_created_subq = (
        select(
            batch_col.label("batch_key"),
            func.min(DataFetchJob.created_at).label("batch_created_at"),
        )
        .group_by(batch_col)
        .subquery()
    )

    started_case = func.coalesce(
        func.sum(
            func.cast(
                DataFetchJob.status.in_(_BUCKET_STARTED_STATUSES),
                Integer,  # Portable across SQLite + Postgres.
            )
        ),
        0,
    )
    running_case = func.coalesce(
        func.sum(
            func.cast(
                DataFetchJob.status.in_(_BUCKET_RUNNING_STATUSES),
                Integer,
            )
        ),
        0,
    )

    bucket_started_subq = (
        select(
            batch_col.label("batch_key"),
            DataFetchJob.symbol.label("bucket_symbol"),
            DataFetchJob.data_type.label("bucket_data_type"),
            func.coalesce(DataFetchJob.interval, "").label("bucket_interval"),
            started_case.label("started_count"),
            running_case.label("running_count"),
        )
        .group_by(
            batch_col,
            DataFetchJob.symbol,
            DataFetchJob.data_type,
            func.coalesce(DataFetchJob.interval, ""),
        )
        .subquery()
    )

    # 0 = bucket is idle (can start immediately); 1 = bucket already has
    # in-flight work. Ordering this ASC before ``batch_created_at`` means an
    # idle bucket anywhere in the queue beats a non-idle bucket from the
    # oldest batch — that is the soft-FIFO relaxation from #166.
    bucket_has_running = (bucket_started_subq.c.running_count > 0)

    return (
        select(DataFetchJob.job_id)
        .join(
            batch_created_subq,
            batch_created_subq.c.batch_key == batch_col,
        )
        .join(
            bucket_started_subq,
            and_(
                bucket_started_subq.c.batch_key == batch_col,
                bucket_started_subq.c.bucket_symbol == DataFetchJob.symbol,
                bucket_started_subq.c.bucket_data_type == DataFetchJob.data_type,
                bucket_started_subq.c.bucket_interval
                == func.coalesce(DataFetchJob.interval, ""),
            ),
        )
        .where(DataFetchJob.status == STATUS_QUEUED)
        .order_by(
            bucket_has_running.asc(),
            batch_created_subq.c.batch_created_at.asc(),
            bucket_started_subq.c.started_count.asc(),
            DataFetchJob.symbol.asc(),
            DataFetchJob.data_type.asc(),
            func.coalesce(DataFetchJob.interval, "").asc(),
            DataFetchJob.start_date.asc(),
            DataFetchJob.created_at.asc(),
            DataFetchJob.id.asc(),
        )
        .limit(1)
    )


async def claim_next_queued_job(factory):
    """Atomically claim the next runnable DataFetchJob and return the row.

    Scheduling truth lives in the DB: the consumer never reads a job_id off
    Redis. Instead it asks this function for "the next runnable job", which
    flips exactly one row from ``queued`` → ``running`` and returns it.

    The target is picked by :func:`_next_queued_job_id_subquery`, which
    encodes cross-batch soft FIFO + per-FetchBucket fairness + same-bucket
    chronological order (PRD #162 / issue #165). If two consumers race, the
    guarded ``UPDATE ... WHERE status='queued'`` still admits at most one
    ``rowcount == 1``; the loser returns ``None``.
    """
    async with factory() as db:
        oldest_queued = _next_queued_job_id_subquery().scalar_subquery()
        stmt = (
            update(DataFetchJob)
            .where(DataFetchJob.status == STATUS_QUEUED)
            .where(DataFetchJob.job_id == oldest_queued)
            .values(
                status=STATUS_RUNNING,
                progress=0,
                message="Starting...",
                error=None,
                completed_at=None,
            )
            .returning(DataFetchJob.job_id)
        )
        result = await db.execute(stmt)
        claimed_job_id = result.scalar_one_or_none()
        await db.commit()
        if claimed_job_id is None:
            return None

    async with factory() as db:
        job = (
            await db.execute(
                select(DataFetchJob).where(DataFetchJob.job_id == claimed_job_id)
            )
        ).scalar_one_or_none()
    if job is None:
        return None
    if getattr(job, "status", STATUS_RUNNING) == STATUS_CANCELLED:
        return None
    return job


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
    """No-op under the DB-driven scheduler.

    Pre-claim cancellation leaves ``status='queued'`` in the DB, which is
    all the next drain needs. Keeping the function as a named seam so
    call-sites can evolve (e.g. push a wake token if a consumer was
    blocked on BRPOP when the cancellation happened) without touching
    ``_process_job``'s cancellation branch.
    """
    return None


async def _defer_locked_queued_job(rds: aioredis.Redis, job_id: str) -> None:
    """Pure back-off: wait, then let the drain pick the job up again.

    Before #164 this path LPUSH'd the job_id back onto Redis. Now the row
    is already ``queued`` and the DB-driven drain will naturally retry it.
    We still sleep so a consumer that just lost the lock race doesn't hot-
    spin on the same bucket.
    """
    if LOCK_BUSY_REQUEUE_DELAY > 0:
        await asyncio.sleep(LOCK_BUSY_REQUEUE_DELAY)


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
                    "completed_at": datetime.now(timezone.utc),
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
                        "completed_at": datetime.now(timezone.utc),
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
                        "completed_at": datetime.now(timezone.utc),
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


async def _revert_claimed_to_queued(factory, job_id: str) -> None:
    """Flip a just-claimed row back to ``queued`` without overwriting a
    cancellation or a late-failing terminal that raced us."""
    async with factory() as db:
        await db.execute(
            update(DataFetchJob)
            .where(DataFetchJob.job_id == job_id)
            .where(DataFetchJob.status == STATUS_RUNNING)
            .values(status=STATUS_QUEUED, progress=0, message="Deferred: catalog lock busy")
        )
        await db.commit()


async def _process_claimed_job(job, redis_url: str, catalog_path: str) -> bool:
    """Run pipeline + terminal update for a job already flipped to ``running``.

    ``claim_next_queued_job`` has already atomically marked the row, so this
    path does NOT call ``_load_queued_job`` (the row's status is ``running``,
    that filter would return ``None`` and silently drop the job — the bug
    fixed by #166 follow-up). ``job`` is the ORM row returned by
    ``claim_next_queued_job``; we read its fields directly.

    Catalog-lock serialization per ``(symbol, data_type, interval)`` is
    preserved. If the lock is busy we revert the row to ``queued`` so a
    later drain picks it up — a pre-claimed row must never be left
    stranded in ``running``.

    Returns ``True`` if the job reached a terminal state (completed/failed)
    this call, ``False`` if the row was reverted to ``queued`` due to a
    busy catalog lock. ``drain_once`` uses the return value to stop this
    tick instead of hot-spinning on the same lock-busy bucket.
    """
    factory = get_session_factory()
    rds = aioredis.from_url(redis_url, decode_responses=True)
    job_id = job.job_id
    symbol = job.symbol
    data_type = job.data_type
    interval = job.interval
    progress_channel = f"tino:data:progress:{job_id}"

    try:
        lock_key = _catalog_lock_key(symbol, data_type, interval)
        lock = await _try_acquire_catalog_lock(lock_key)
        if lock is None:
            logger.info(
                "Data-fetch job %s deferred because catalog lock %s is busy; "
                "reverting to queued",
                job_id, lock_key,
            )
            await _revert_claimed_to_queued(factory, job_id)
            if LOCK_BUSY_REQUEUE_DELAY > 0:
                await asyncio.sleep(LOCK_BUSY_REQUEUE_DELAY)
            return False

        try:
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

            from tinohelm.data.pipeline import BinanceVisionPipeline

            pipeline = BinanceVisionPipeline(catalog_path=catalog_path)
            result = await pipeline.ingest(
                symbol=symbol,
                data_type=data_type,
                start=job.start_date,
                end=job.end_date,
                asset_class=job.asset_class,
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
                    "completed_at": datetime.now(timezone.utc),
                },
            )
            if not updated:
                return False
            logger.info(
                "Data-fetch job %s completed: %s %s — %d objects",
                job_id, symbol, data_type, result.objects_count,
            )
            await rds.publish("tino:data:events", json.dumps({
                "type": "data.fetch.completed",
                "job_id": job_id,
                "symbol": symbol,
                "data_type": data_type,
                "objects_count": result.objects_count,
            }))
            return True

        updated, terminal_cancelled = await _await_preserving_cancellation(
            _complete_success()
        )
        if not updated:
            logger.info(
                "Data-fetch job %s was no longer running at completion, "
                "skipping terminal event",
                job_id,
            )
        if terminal_cancelled:
            raise asyncio.CancelledError
        return True

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        failure_error = str(exc)
        logger.exception("Data-fetch job %s failed: %s", job_id, exc)
        try:
            async def _complete_failure() -> bool:
                updated = await _guarded_terminal_update(
                    factory,
                    job_id,
                    {
                        "status": STATUS_FAILED,
                        "error": failure_error[:2000],
                        "completed_at": datetime.now(timezone.utc),
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
                return updated

            _, failure_cancelled = await _await_preserving_cancellation(
                _complete_failure()
            )
            if failure_cancelled:
                raise asyncio.CancelledError
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to update job %s status to failed", job_id)
        return True
    finally:
        await rds.close()


async def drain_once(*, redis_url: str, catalog_path: str) -> int:
    """Drain all currently-runnable DataFetchJob rows from the DB.

    Called by each consumer after BRPOP returns a wake token. Loops over
    ``claim_next_queued_job`` until the DB has no more runnable rows, so
    one wake signal is enough to clear an arbitrarily deep backlog.

    Exceptions from the per-job body are logged but do **not** abort the
    drain — sibling jobs in the same FetchBatch must keep progressing
    (PRD #162 best-effort batch semantics).
    """
    factory = get_session_factory()
    processed = 0
    while True:
        try:
            job = await claim_next_queued_job(factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("claim_next_queued_job failed; ending drain early")
            return processed
        if job is None:
            return processed
        processed += 1
        try:
            advanced = await _process_claimed_job(job, redis_url, catalog_path)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Data-fetch job %s raised in drain; continuing with sibling work",
                job.job_id,
            )
            advanced = True  # Exception is terminal for this row; don't hot-spin
        if not advanced:
            # Row was reverted to queued (catalog lock busy). Stop this
            # drain pass so the next wake signal — or a sibling consumer —
            # retries. Continuing would just re-claim the same row and
            # defer it again, burning CPU.
            return processed


def start_data_worker(redis_url: str, catalog_path: str) -> asyncio.Task:
    """Start the data-fetch consumer as a background asyncio task."""
    async def _process(_wake_token: str) -> None:
        # The payload popped off Redis is always a wake sentinel (see
        # WAKE_TOKEN); actual scheduling happens from the DB.
        await drain_once(redis_url=redis_url, catalog_path=catalog_path)

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
