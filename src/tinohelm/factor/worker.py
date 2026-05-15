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
import concurrent.futures
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


class FactorRunCancelled(Exception):
    """Raised internally when a factor run observes its Redis cancel flag."""


def _queue_payload_from_run(run: FactorRun) -> str:
    """Rebuild a full Redis payload from the persisted FactorRun snapshot."""
    config = dict(run.config or {})
    run_options = dict(config.get("_tino_run_options") or {})
    return json.dumps({
        "run_id": run.id,
        "factor_name": run.factor_name,
        "config": config,
        "params": config.get("params"),
        "full": bool(run_options.get("full", False)),
    })


def _run_id_from_queue_payload(raw: str) -> str | None:
    """Extract a run id from a JSON queue payload."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw or None
    if isinstance(payload, dict):
        run_id = payload.get("run_id")
        return str(run_id) if run_id else None
    if isinstance(payload, str):
        return payload or None
    return None


async def _queued_run_ids_in_redis(rds: aioredis.Redis) -> set[str]:
    """Best-effort snapshot of run ids already present in the live Redis list."""
    try:
        existing_payloads = await rds.lrange(QUEUE_KEY, 0, -1)
    except Exception:  # noqa: BLE001 - recovery must remain duplicate-safe
        logger.warning("Could not inspect factor queue before recovery replay", exc_info=True)
        return set()
    out: set[str] = set()
    for raw in existing_payloads or []:
        if isinstance(raw, bytes):
            raw = raw.decode()
        if not isinstance(raw, str):
            continue
        run_id = _run_id_from_queue_payload(raw)
        if run_id:
            out.add(run_id)
    return out


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
        interrupted_runs = (
            await db.execute(
                select(FactorRun)
                .where(FactorRun.status == STATUS_RUNNING)
                .order_by(FactorRun.created_at.asc())
            )
        ).scalars().all()
        interrupted_ids = [run.id for run in interrupted_runs]

        # Flip running → queued and persist to DB first.
        # DB commit happens before any Redis mutation so that a failed DB write
        # cannot enqueue rows whose durable status still says "running".
        if interrupted_ids:
            result = await db.execute(
                update(FactorRun)
                .where(FactorRun.id.in_(interrupted_ids), FactorRun.status == STATUS_RUNNING)
                .values(status="queued", progress=0)
            )
            recovered = result.rowcount or 0

        # Commit DB changes before touching Redis.  This is the critical ordering:
        # if Redis ops fail after this point, the DB already reflects "queued" and
        # the next startup will re-read the correct queued set.
        await db.commit()

        queued_runs = (
            await db.execute(
                select(FactorRun)
                .where(FactorRun.status == "queued")
                .order_by(FactorRun.created_at.asc())
            )
        ).scalars().all()

        existing_run_ids = await _queued_run_ids_in_redis(rds)

        # The live enqueue path uses LPUSH and consumers use BRPOP, so pushing
        # queued runs in created_at ASC order with LPUSH preserves FIFO within
        # the recovered snapshot: newest ends up at the head, oldest at the
        # tail, and BRPOP consumes oldest first.  Do not DELETE the live list:
        # API instances can enqueue between our DB snapshot and Redis replay.
        for run in queued_runs:
            if run.id in existing_run_ids:
                continue
            await rds.lpush(QUEUE_KEY, _queue_payload_from_run(run))
            existing_run_ids.add(run.id)

    if recovered:
        logger.info("Recovered %d interrupted factor run(s)", recovered)
    return recovered


async def _process_job(job_payload: str, redis_url: str) -> None:
    """Execute a single factor evaluation job.

    Parameters
    ----------
    job_payload:
        JSON string popped from ``tino:factor:queue``.  The full JSON
        payload described in the module docstring.  The worker loads the
        config from the DB ``FactorRun.config`` column.
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
        # 2. Prepare cancel helpers. Cancellation is applied only after the
        #    DB row is loaded so stale queue payloads cannot rewrite terminal
        #    runs.
        # ----------------------------------------------------------------
        cancel_key = f"tino:factor:cancel:{run_id}"

        async def _mark_cancelled() -> None:
            async with factory() as db_c:
                await db_c.execute(
                    update(FactorRun)
                    .where(
                        FactorRun.id == run_id,
                        FactorRun.status.in_(["queued", STATUS_RUNNING]),
                    )
                    .values(
                        status=STATUS_CANCELLED,
                        finished_at=datetime.now(UTC).replace(tzinfo=None),
                        progress_stage=None,
                    )
                )
                await db_c.commit()

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

            if run.status in {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED}:
                if await rds.exists(cancel_key):
                    await rds.delete(cancel_key)
                    logger.info("Cleared stale cancel key for terminal FactorRun %s", run_id)
                logger.info("FactorRun %s status is terminal %r; skipping", run_id, run.status)
                return
            if await rds.exists(cancel_key):
                logger.info("Factor run %s cancelled, skipping", run_id)
                await _mark_cancelled()
                return
            if run.status != "queued":
                logger.info(
                    "FactorRun %s status is %r, not queued; skipping",
                    run_id, run.status,
                )
                return

            # If caller didn't embed config in the payload, load from DB row.
            if config_dict is None:
                config_dict = run.config or {}
            config_run_options = dict((config_dict or {}).get("_tino_run_options") or {})
            if params is None:
                params = (config_dict or {}).get("params")
            if "full" not in payload:
                full = bool(config_run_options.get("full", False))
            if not factor_name:
                factor_name = run.factor_name

            # queued → running.  The status predicate makes recovery replay
            # duplicate-safe when the same queued snapshot is LPUSHed more
            # than once or a live queue entry races with startup recovery.
            claim_result = await db.execute(
                update(FactorRun)
                .where(FactorRun.id == run_id, FactorRun.status == "queued")
                .values(
                    status=STATUS_RUNNING,
                    started_at=datetime.now(UTC).replace(tzinfo=None),
                    progress=0,
                )
            )
            if getattr(claim_result, "rowcount", None) == 0:
                logger.info("FactorRun %s was already claimed; skipping", run_id)
                await db.rollback()
                return
            await db.commit()

        progress_channel = f"tino:factor:progress:{run_id}"

        # ----------------------------------------------------------------
        # 4. Progress callback — always publish to Redis; DB write throttled
        # ----------------------------------------------------------------
        throttle = PercentStepThrottle(step=PROGRESS_DB_STEP)

        async def _progress(pct: int, msg: str = "", *, stage: str | None = None) -> None:
            payload = json.dumps({
                "run_id": run_id,
                "factor_name": factor_name,
                "progress": pct,
                "message": msg,
                "stage": stage,
            })
            await rds.publish(progress_channel, payload)
            await rds.setex(progress_channel, 86400, str(pct))
            update_values: dict = {"progress": pct} if throttle.should_write(pct) else {}
            if stage is not None:
                update_values["progress_stage"] = stage
            if update_values:
                async with factory() as db2:
                    await db2.execute(
                        update(FactorRun)
                        .where(FactorRun.id == run_id)
                        .values(**update_values)
                    )
                    await db2.commit()

        async def _check_cancel() -> bool:
            return bool(await rds.exists(cancel_key))

        async def _progress_and_check_cancel(
            pct: int,
            msg: str = "",
            *,
            stage: str | None = None,
        ) -> None:
            await _progress(pct, msg, stage=stage)
            if await _check_cancel():
                raise FactorRunCancelled(run_id)

        # ----------------------------------------------------------------
        # 5. Build Orchestrator and run (CPU-bound → thread)
        # ----------------------------------------------------------------
        loop = asyncio.get_running_loop()
        progress_futures: list[concurrent.futures.Future[None]] = []

        def _sync_progress(pct: int, msg: str = "", *, stage: str | None = None) -> None:
            """Bridge progress from the worker thread and fail fast on cancel.

            Blocking on the scheduled coroutine is safe here: the event loop is
            awaiting ``asyncio.to_thread`` while this code runs in that worker
            thread.  This gives factor runs the same between-stage cancellation
            semantics as signal runs instead of only checking before start.
            """
            fut = asyncio.run_coroutine_threadsafe(
                _progress_and_check_cancel(pct, msg, stage=stage),
                loop,
            )
            progress_futures.append(fut)
            fut.result()

        eval_result = await asyncio.to_thread(
            _run_orchestrator,
            factor_name=factor_name,
            config_dict=config_dict,
            params=params,
            full=full,
            run_id=run_id,
            progress_cb=_sync_progress,
        )
        if progress_futures:
            await asyncio.gather(
                *(asyncio.wrap_future(fut) for fut in progress_futures),
                return_exceptions=False,
            )
        if await _check_cancel():
            raise FactorRunCancelled(run_id)

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
                    oos_ic_series=result_dict.get("oos_ic_series") or None,
                    neutralization_config=(
                        result_dict.get("neutralization_config") or None
                    ),
                    universe_id=config_dict.get("universe_id"),
                    segment_results=result_dict.get("segment_results") or None,
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

    except FactorRunCancelled:
        logger.info("FactorRun %s cancelled", run_id)
        try:
            await _mark_cancelled()
            await rds.publish(EVENTS_CHANNEL, json.dumps({
                "type": "factor.cancelled",
                "run_id": run_id,
                "factor_name": factor_name,
            }))
        except Exception:
            logger.exception("Failed to update FactorRun %s to cancelled", run_id)

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
    from tinohelm.factor.backend.polars_backend import PolarsBackend
    from tinohelm.factor.cache import FactorCache
    from tinohelm.factor.data_layer import DataLayer
    from tinohelm.factor.engine.orchestrator import Orchestrator
    from tinohelm.factor.evaluation.evaluator import Evaluator
    from tinohelm.factor.observer import Observer
    from tinohelm.factor.registry import Registry
    from tinohelm.factor.config import parse_eval_config
    from tinohelm.factor.universe import Universe
    from tinohelm.core.config import get_settings
    from tinohelm.data.storage import get_active_catalog_root
    import pathlib

    settings = get_settings()
    catalog_path = str(get_active_catalog_root(settings))

    # --- Build sub-systems -----------------------------------------------
    registry = Registry()
    registry.scan()

    # Build Universe from the symbol list embedded in config_dict
    universe_symbols: tuple[str, ...] = tuple(config_dict.get("universe", []))
    universe_obj = Universe.from_symbols(universe_symbols)

    data_layer = DataLayer(universe_obj, catalog_root=pathlib.Path(catalog_path))

    backend = PolarsBackend()
    evaluator = Evaluator()

    # Cache — use settings.paths.factor_cache (no hardcoded path).
    cache = FactorCache()

    # Observer with progress hook that calls back into the async loop.
    # The Observer's span mechanism drives coarse-grained step events.
    observer = Observer()

    # Emit 4-stage progress events via Observer span hook.
    # Maps observer span name → (pct, stage_label) on span entry.
    _STAGE_MAP: dict[str, tuple[int, str]] = {
        "data_load":   (10, "aligning"),
        "kernel_exec": (40, "computing"),
        "evaluate":    (70, "evaluating"),
    }

    original_start_span = observer.start_span

    import contextlib

    @contextlib.contextmanager
    def _instrumented_span(name: str, **tags):
        entry = _STAGE_MAP.get(name)
        if entry is not None:
            pct, stage = entry
            progress_cb(pct, name, stage=stage)
        with original_start_span(name, **tags) as ctx:
            yield ctx

    observer.start_span = _instrumented_span  # type: ignore[method-assign]

    # --- Reconstruct EvalConfig from dict --------------------------------
    config = parse_eval_config(config_dict, params=params)

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

    # Stage 4: persisting — emitted here so the async layer (step 6 in
    # _process_job) can write progress_stage="persisting" to DB before the
    # final completed update.
    progress_cb(95, "Persisting results", stage="persisting")
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
