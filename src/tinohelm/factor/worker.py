"""Async factor worker — consumes factor evaluation jobs from Redis queue.

Runs inside the API process. The generic queue-worker primitives live in
``tinohelm.core.async_queue_worker``; this module is only the
factor-specific job body plus the module-level singleton handle.

Redis key conventions (mirrors CLAUDE.md § Redis Key Patterns):
- ``tino:factor:queue``               — job queue (LPUSH/BRPOP)
- ``tino:factor:cancel:{run_id}``     — cancel flag (set to "1" to skip)
- ``tino:factor:progress:{run_id}``   — progress PubSub (JSON messages)
- ``tino:factor:events``              — completion/failure event PubSub

Payload schema consumed from the queue::

    {
        "run_id":      "<UUID>",
        "factor_name": "ret_N",
        "config":      { <EvalConfig dict — see types.EvalConfig> },
        "params":      { <optional param overrides> } | null,
        "full":        true | false
    }
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import traceback
from datetime import datetime, UTC

import redis.asyncio as aioredis
from sqlalchemy import select, update

from tinohelm.core.async_queue_worker import (
    PercentStepThrottle,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    WorkerHandle,
    consumer_loop,
)
from tinohelm.db.models import FactorRun
from tinohelm.db.session import get_session_factory

logger = logging.getLogger(__name__)

QUEUE_KEY = "tino:factor:queue"
EVENTS_CHANNEL = "tino:factor:events"
PROGRESS_DB_STEP = 10

_handle: WorkerHandle = WorkerHandle(name="factor-worker")


async def recover_interrupted_jobs(rds: aioredis.Redis) -> int:
    """Reset FactorRun rows stuck in 'running' back to 'queued' and re-enqueue.

    Called once during API startup. ``FactorRun`` uses ``id`` (not ``job_id``)
    so this is a custom implementation rather than the generic
    ``requeue_running_jobs`` helper (which references ``model_cls.job_id``).
    Returns the number of rows flipped from running → queued.
    """
    factory = get_session_factory()
    recovered = 0
    async with factory() as db:
        # Flip running → queued and persist to DB first.
        # DB commit happens before any Redis mutation so that a partial failure
        # (commit error after Redis delete) cannot produce double-enqueue on the
        # next startup — the DB would still show the rows as "running" and the
        # recover would simply retry cleanly.
        result = await db.execute(
            update(FactorRun)
            .where(FactorRun.status == STATUS_RUNNING)
            .values(status="queued", progress=0)
        )
        recovered = result.rowcount or 0

        # Commit DB changes before touching Redis.  This is the critical ordering:
        # if Redis ops fail after this point, the DB already reflects "queued" and
        # the next startup will re-read the correct queued set.
        await db.commit()

        # After a successful commit, rebuild the Redis queue from the DB's
        # authoritative "queued" state (includes both newly-recovered rows and
        # any pre-existing queued rows).
        queued_ids = (
            await db.execute(
                select(FactorRun.id).where(FactorRun.status == "queued")
            )
        ).scalars().all()

        # Clear the old queue and re-populate atomically to avoid duplicates.
        if queued_ids:
            await rds.delete(QUEUE_KEY)
            for run_id in queued_ids:
                await rds.lpush(QUEUE_KEY, run_id)

    if recovered:
        logger.info("Recovered %d interrupted factor run(s)", recovered)
    return recovered


async def _process_job(job_payload: str, redis_url: str) -> None:
    """Execute a single factor evaluation job.

    Parameters
    ----------
    job_payload:
        JSON string popped from ``tino:factor:queue``.  May be the
        ``run_id`` UUID directly (legacy path) or the full JSON payload
        described in the module docstring.  When it is a plain UUID, the
        worker loads the config from the DB ``FactorRun.config`` column.
    redis_url:
        Redis connection URL forwarded from ``start_factor_worker()``.
    """
    factory = get_session_factory()
    rds = aioredis.from_url(redis_url, decode_responses=True)
    run_id = "<unknown>"
    factor_name = ""

    try:
        # ----------------------------------------------------------------
        # 1. Decode payload
        # ----------------------------------------------------------------
        try:
            payload = json.loads(job_payload)
        except json.JSONDecodeError:
            # Plain UUID string enqueued directly (e.g. from API route).
            payload = {"run_id": job_payload}

        run_id = payload.get("run_id", "<unknown>")
        factor_name = payload.get("factor_name", "")
        config_dict: dict | None = payload.get("config")
        params: dict | None = payload.get("params")
        full: bool = bool(payload.get("full", False))

        # ----------------------------------------------------------------
        # 2. Check cancel flag early (before DB round-trip)
        # ----------------------------------------------------------------
        cancel_key = f"tino:factor:cancel:{run_id}"
        if await rds.exists(cancel_key):
            logger.info("Factor run %s cancelled (pre-load), skipping", run_id)
            async with factory() as db:
                await db.execute(
                    update(FactorRun)
                    .where(FactorRun.id == run_id)
                    .values(status=STATUS_CANCELLED)
                )
                await db.commit()
            return

        # ----------------------------------------------------------------
        # 3. Load FactorRun from DB; resolve config if not in payload
        # ----------------------------------------------------------------
        async with factory() as db:
            run = (await db.execute(
                select(FactorRun).where(FactorRun.id == run_id)
            )).scalar_one_or_none()

            if run is None:
                logger.warning("FactorRun %s not found in DB, skipping", run_id)
                return

            if run.status == STATUS_CANCELLED:
                logger.info("FactorRun %s already cancelled, skipping", run_id)
                return

            # If caller didn't embed config in the payload, load from DB row.
            if config_dict is None:
                config_dict = run.config or {}
            if not factor_name:
                factor_name = run.factor_name

            # queued → running
            await db.execute(
                update(FactorRun)
                .where(FactorRun.id == run_id)
                .values(
                    status=STATUS_RUNNING,
                    started_at=datetime.now(UTC).replace(tzinfo=None),
                    progress=0,
                )
            )
            await db.commit()

        progress_channel = f"tino:factor:progress:{run_id}"

        # ----------------------------------------------------------------
        # 4. Progress callback — always publish to Redis; DB write throttled
        # ----------------------------------------------------------------
        throttle = PercentStepThrottle(step=PROGRESS_DB_STEP)

        async def _progress(pct: int, msg: str = "") -> None:
            payload = json.dumps({
                "run_id": run_id,
                "factor_name": factor_name,
                "progress": pct,
                "message": msg,
            })
            await rds.publish(progress_channel, payload)
            await rds.setex(progress_channel, 86400, str(pct))
            if throttle.should_write(pct):
                async with factory() as db2:
                    await db2.execute(
                        update(FactorRun)
                        .where(FactorRun.id == run_id)
                        .values(progress=pct)
                    )
                    await db2.commit()

        # ----------------------------------------------------------------
        # 5. Build Orchestrator and run (CPU-bound → thread)
        # ----------------------------------------------------------------
        loop = asyncio.get_running_loop()

        def _sync_progress(pct: int, msg: str = "") -> None:
            """Bridge: schedule async progress update from a worker thread."""
            asyncio.run_coroutine_threadsafe(_progress(pct, msg), loop)

        eval_result = await asyncio.to_thread(
            _run_orchestrator,
            factor_name=factor_name,
            config_dict=config_dict,
            params=params,
            full=full,
            run_id=run_id,
            progress_cb=_sync_progress,
        )

        # ----------------------------------------------------------------
        # 6. Mark completed
        # ----------------------------------------------------------------
        result_dict = dataclasses.asdict(eval_result)
        async with factory() as db:
            await db.execute(
                update(FactorRun)
                .where(FactorRun.id == run_id)
                .values(
                    status=STATUS_COMPLETED,
                    progress=100,
                    result=result_dict,
                    finished_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            await db.commit()

        logger.info("FactorRun %s completed: %s", run_id, factor_name)

        await rds.publish(EVENTS_CHANNEL, json.dumps({
            "type": "factor.completed",
            "run_id": run_id,
            "factor_name": factor_name,
            "rating": eval_result.rating,
        }))

    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("FactorRun %s failed: %s", run_id, exc)
        try:
            async with factory() as db:
                await db.execute(
                    update(FactorRun)
                    .where(FactorRun.id == run_id)
                    .values(
                        status=STATUS_FAILED,
                        error=tb[:2000],
                        finished_at=datetime.now(UTC).replace(tzinfo=None),
                    )
                )
                await db.commit()
            await rds.publish(EVENTS_CHANNEL, json.dumps({
                "type": "factor.failed",
                "run_id": run_id,
                "factor_name": factor_name,
                "error": str(exc)[:200],
            }))
        except Exception:
            logger.exception(
                "Failed to update FactorRun %s to failed", run_id
            )
    finally:
        await rds.close()


def _run_orchestrator(
    *,
    factor_name: str,
    config_dict: dict,
    params: dict | None,
    full: bool,
    run_id: str,
    progress_cb,
) -> "EvalResult":  # noqa: F821 — avoid circular import at top level
    """Synchronous helper executed inside ``asyncio.to_thread``.

    Constructs the full Orchestrator stack from project defaults, then
    calls :meth:`Orchestrator.run`.  Imported lazily to keep worker module
    importable even when heavy deps (pandas, numpy) are absent in test env.
    """
    from tinohelm.factor.backend.pandas_backend import PandasBackend
    from tinohelm.factor.cache import FactorCache
    from tinohelm.factor.data_layer import DataLayer
    from tinohelm.factor.engine.orchestrator import Orchestrator
    from tinohelm.factor.evaluation.evaluator import Evaluator
    from tinohelm.factor.observer import Observer
    from tinohelm.factor.registry import Registry
    from tinohelm.factor.types import EvalConfig
    from tinohelm.factor.universe import Universe
    from tinohelm.core.config import get_settings
    import pathlib

    settings = get_settings()
    catalog_path = str(settings.paths.catalog)

    # --- Build sub-systems -----------------------------------------------
    registry = Registry()
    registry.scan()

    # Build Universe from the symbol list embedded in config_dict
    universe_symbols: tuple[str, ...] = tuple(config_dict.get("universe", []))
    universe_obj = Universe.from_symbols(universe_symbols)

    data_layer = DataLayer(universe_obj, catalog_root=pathlib.Path(catalog_path))

    backend = PandasBackend()
    evaluator = Evaluator()

    # Cache stored alongside catalog
    cache_dir = pathlib.Path(catalog_path) / ".factor_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = FactorCache(cache_root=str(cache_dir))

    # Observer with progress hook that calls back into the async loop.
    # The Observer's span mechanism drives coarse-grained step events.
    observer = Observer()

    # Emit progress at major pipeline stages via Observer hook.
    # stage_pct maps span name → approximate percent on entry.
    _STAGE_PCT: dict[str, int] = {
        "data_load": 10,
        "kernel_exec": 40,
        "evaluate": 70,
    }

    original_start_span = observer.start_span

    import contextlib

    @contextlib.contextmanager
    def _instrumented_span(name: str, **tags):
        pct = _STAGE_PCT.get(name)
        if pct is not None:
            progress_cb(pct, name)
        with original_start_span(name, **tags) as ctx:
            yield ctx

    observer.start_span = _instrumented_span  # type: ignore[method-assign]

    # --- Reconstruct EvalConfig from dict --------------------------------
    config = EvalConfig(
        universe=tuple(config_dict.get("universe", [])),
        start=config_dict["start"],
        end=config_dict["end"],
        forward_period=config_dict.get("forward_period", 5),
        quantiles=config_dict.get("quantiles", 5),
        cost_bps=config_dict.get("cost_bps", 4.0),
        ic_freq=config_dict.get("ic_freq", "D"),
        log_ret=config_dict.get("log_ret", False),
        params=params or config_dict.get("params", {}),
    )

    orchestrator = Orchestrator(
        registry=registry,
        data_layer=data_layer,
        backend=backend,
        evaluator=evaluator,
        cache=cache,
        observer=observer,
    )

    result = orchestrator.run(
        factor_name,
        config,
        params=params,
        run_id=run_id,
        full=full,
    )

    progress_cb(100, "Done")
    return result


def start_factor_worker(redis_url: str) -> asyncio.Task:
    """Start the factor worker as a background asyncio task.

    Called from the FastAPI ``startup`` event hook.  Returns the task so
    the caller can await it if needed (e.g. for testing).

    Parameters
    ----------
    redis_url:
        Redis connection URL (e.g. ``"redis://localhost:6379/0"``).
    """

    async def _process(job_payload: str) -> None:
        await _process_job(job_payload, redis_url)

    return _handle.start(
        lambda: consumer_loop(
            redis_url,
            QUEUE_KEY,
            _process,
            worker_label="Factor worker",
        )
    )


def stop_factor_worker() -> None:
    """Cancel the factor worker task.

    Called from the FastAPI ``shutdown`` event hook.
    """
    _handle.stop()
