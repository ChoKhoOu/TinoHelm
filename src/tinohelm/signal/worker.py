"""Async signal worker — consumes signal evaluation jobs from Redis queue.

Mirrors :mod:`tinohelm.factor.worker` exactly: it runs inside the API process,
dequeues jobs from ``tino:signal:queue``, executes the kernel + evaluator
pipeline, and persists ``SignalEvalResult`` to PostgreSQL.

The shared queue-worker primitives (BRPOP loop, ``WorkerHandle``, recovery)
live in :mod:`tinohelm.core.async_queue_worker`; this module is only the
signal-specific job body plus the module-level singleton handle.

Redis key conventions (mirrors CLAUDE.md § Redis Key Patterns)::

    tino:signal:queue                 — job queue (LPUSH/BRPOP)
    tino:signal:cancel:{run_id}       — cancel flag (set to "1" to skip)
    tino:signal:progress:{run_id}     — progress PubSub (JSON messages)
    tino:signal:events                — completion/failure event PubSub

Payload schema consumed from the queue::

    {
        "run_id":      "<UUID>",
        "signal_name": "momentum_top3_long_short",
        "config":      { <SignalRun.config snapshot> } | null,
    }

Four-stage progress contract
----------------------------
Each job emits **exactly four** progress events with a populated ``stage``
field and a corresponding DB ``progress_stage`` UPDATE:

    1. ``"aligning"``   — load factor + future-returns panels
    2. ``"computing"``  — apply the kernel (top_k_long_short, etc.)
    3. ``"evaluating"`` — run :class:`SignalEvaluator` to compute metrics
    4. ``"persisting"`` — write the result back to ``signal_runs.result``

The cancel flag is checked between every stage so a SET right after stage k
guarantees stage k+1 (and onwards) does not run.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import traceback
from datetime import datetime, UTC
from typing import Any, Callable

import polars as pl
import redis.asyncio as aioredis
from sqlalchemy import select, update

from tinohelm.core.async_queue_worker import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    WorkerHandle,
    consumer_loop,
)
from tinohelm.db.models import SignalRun
from tinohelm.db.session import get_session_factory
from tinohelm.signal.evaluator import SignalEvalResult, SignalEvaluator
from tinohelm.signal.kernels import (
    quantile_long_short,
    rank_to_weight,
    threshold_signed,
    top_k_long_short,
    zscore_clip,
)
from tinohelm.signal.types import SignalSpec
from tinohelm.signal.utils import signal_spec_from_dict

logger = logging.getLogger(__name__)

QUEUE_KEY = "tino:signal:queue"
EVENTS_CHANNEL = "tino:signal:events"

# Four-stage progress markers — mirror signal_runs.progress_stage column values.
STAGE_ALIGNING = "aligning"
STAGE_COMPUTING = "computing"
STAGE_EVALUATING = "evaluating"
STAGE_PERSISTING = "persisting"

# Default annualisation factor — hourly crypto bars.  Callers may override
# via job payload key ``"periods_per_year"``.
_DEFAULT_PERIODS_PER_YEAR = 365 * 24

# Map kernel slug → callable.  Kept module-level so tests can monkey-patch.
_KERNEL_DISPATCH: dict[str, Callable] = {  # type: ignore[type-arg]
    "top_k_long_short": top_k_long_short,
    "quantile_long_short": quantile_long_short,
    "threshold_signed": threshold_signed,
    "zscore_clip": zscore_clip,
    "rank_to_weight": rank_to_weight,
}

_handle: WorkerHandle = WorkerHandle(name="signal-worker")


# ---------------------------------------------------------------------------
# Recovery — mirror tinohelm.factor.worker.recover_interrupted_jobs.
# ---------------------------------------------------------------------------

async def recover_interrupted_jobs(rds: aioredis.Redis) -> int:
    """Reset SignalRun rows stuck in 'running' back to 'queued' and re-enqueue.

    Called once during API startup.  ``SignalRun`` uses ``id`` (a UUID
    string) as primary key — same shape as :class:`FactorRun` — so we
    duplicate the factor recovery routine here rather than calling the
    generic ``requeue_running_jobs`` helper (which expects ``model_cls.job_id``).

    Returns the number of rows flipped from running → queued.
    """
    factory = get_session_factory()
    recovered = 0
    async with factory() as db:
        # Flip running → queued and persist to DB first.  Same ordering as
        # ``factor.worker.recover_interrupted_jobs``: DB commit precedes any
        # Redis mutation so a partial failure cannot produce double-enqueue
        # on the next startup.
        result = await db.execute(
            update(SignalRun)
            .where(SignalRun.status == STATUS_RUNNING)
            .values(status="queued", progress=0, progress_stage=None)
        )
        recovered = result.rowcount or 0
        await db.commit()

        queued_ids = (
            await db.execute(
                select(SignalRun.id)
                .where(SignalRun.status == "queued")
                .order_by(SignalRun.created_at.asc())
            )
        ).scalars().all()

        if queued_ids:
            await rds.delete(QUEUE_KEY)
            for run_id in queued_ids:
                await rds.rpush(QUEUE_KEY, run_id)

    if recovered:
        logger.info("Recovered %d interrupted signal run(s)", recovered)
    return recovered


# ---------------------------------------------------------------------------
# Core job processing
# ---------------------------------------------------------------------------

async def _process_job(job_payload: str, redis_url: str) -> None:
    """Execute a single signal evaluation job with 4 progress stages.

    Parameters
    ----------
    job_payload:
        JSON string popped from ``tino:signal:queue``.  May be the
        ``run_id`` UUID directly (legacy / test path) or the full JSON
        payload described in the module docstring.
    redis_url:
        Redis connection URL forwarded from ``start_signal_worker()``.
    """
    factory = get_session_factory()
    rds = aioredis.from_url(redis_url, decode_responses=True)
    run_id = "<unknown>"
    signal_name = ""

    try:
        # ----------------------------------------------------------------
        # 1. Decode payload
        # ----------------------------------------------------------------
        try:
            payload = json.loads(job_payload)
        except json.JSONDecodeError:
            payload = {"run_id": job_payload}

        run_id = payload.get("run_id", "<unknown>")
        signal_name = payload.get("signal_name", "")
        config_dict: dict | None = payload.get("config")

        # ----------------------------------------------------------------
        # 2. Cancel-flag fast path (before DB round-trip)
        # ----------------------------------------------------------------
        cancel_key = f"tino:signal:cancel:{run_id}"
        if await rds.exists(cancel_key):
            logger.info("Signal run %s cancelled (pre-load), skipping", run_id)
            async with factory() as db:
                await db.execute(
                    update(SignalRun)
                    .where(SignalRun.id == run_id)
                    .values(status=STATUS_CANCELLED)
                )
                await db.commit()
            return

        # ----------------------------------------------------------------
        # 3. Load SignalRun, transition queued → running, fill in defaults
        # ----------------------------------------------------------------
        async with factory() as db:
            run = (await db.execute(
                select(SignalRun).where(SignalRun.id == run_id)
            )).scalar_one_or_none()

            if run is None:
                logger.warning("SignalRun %s not found in DB, skipping", run_id)
                return

            if run.status == STATUS_CANCELLED:
                logger.info("SignalRun %s already cancelled, skipping", run_id)
                return

            if config_dict is None:
                config_dict = run.config or {}
            if not signal_name:
                signal_name = run.signal_name

            await db.execute(
                update(SignalRun)
                .where(SignalRun.id == run_id)
                .values(
                    status=STATUS_RUNNING,
                    started_at=datetime.now(UTC).replace(tzinfo=None),
                    progress=0,
                )
            )
            await db.commit()

        progress_channel = f"tino:signal:progress:{run_id}"

        # ----------------------------------------------------------------
        # 4. Helper: emit progress event + persist progress_stage
        # ----------------------------------------------------------------
        async def _progress(pct: int, msg: str, *, stage: str | None = None) -> None:
            """Publish progress event and (when ``stage`` is set) persist it.

            Every call publishes a JSON payload on
            ``tino:signal:progress:{run_id}``.  When ``stage`` is non-None,
            the value is also UPDATEd into ``signal_runs.progress_stage``
            so external observers (CLI poll, frontend) can recover from a
            missed PubSub event.
            """
            event = {
                "run_id": run_id,
                "signal_name": signal_name,
                "progress": pct,
                "message": msg,
                "stage": stage,
            }
            await rds.publish(progress_channel, json.dumps(event))
            await rds.setex(progress_channel, 86400, str(pct))
            if stage is not None:
                async with factory() as db_p:
                    await db_p.execute(
                        update(SignalRun)
                        .where(SignalRun.id == run_id)
                        .values(progress=pct, progress_stage=stage)
                    )
                    await db_p.commit()

        async def _check_cancel() -> bool:
            """Return True if the cancel flag was set since the last check."""
            return bool(await rds.exists(cancel_key))

        async def _mark_cancelled() -> None:
            async with factory() as db_c:
                await db_c.execute(
                    update(SignalRun)
                    .where(SignalRun.id == run_id)
                    .values(status=STATUS_CANCELLED)
                )
                await db_c.commit()

        # ----------------------------------------------------------------
        # 5. Build SignalSpec for kernel + evaluator dispatch
        # ----------------------------------------------------------------
        spec = signal_spec_from_dict(signal_name, config_dict)
        periods_per_year = int(
            config_dict.get("periods_per_year", _DEFAULT_PERIODS_PER_YEAR)
        )

        # ----------------------------------------------------------------
        # 6. Stage 1: ALIGNING — load factor panel + future returns
        # ----------------------------------------------------------------
        await _progress(10, "Aligning factor data", stage=STAGE_ALIGNING)
        if await _check_cancel():
            await _mark_cancelled()
            return

        factor_panel, future_returns = await asyncio.to_thread(
            _load_aligned_panels, spec, config_dict
        )

        # ----------------------------------------------------------------
        # 7. Stage 2: COMPUTING — apply kernel
        # ----------------------------------------------------------------
        await _progress(40, "Computing weights", stage=STAGE_COMPUTING)
        if await _check_cancel():
            await _mark_cancelled()
            return

        kernel = _resolve_kernel(spec.method)
        constraints = {
            "gross_exposure": spec.gross_exposure,
            "net_exposure": spec.net_exposure,
            "max_position": spec.max_position,
        }
        weight_panel = await asyncio.to_thread(
            kernel, factor_panel, dict(spec.method_params), constraints
        )

        # ----------------------------------------------------------------
        # 8. Stage 3: EVALUATING — compute SignalEvalResult
        # ----------------------------------------------------------------
        await _progress(70, "Evaluating signal", stage=STAGE_EVALUATING)
        if await _check_cancel():
            await _mark_cancelled()
            return

        evaluator = SignalEvaluator(periods_per_year=periods_per_year)
        eval_result: SignalEvalResult = await asyncio.to_thread(
            evaluator.evaluate, weight_panel, future_returns, spec.cost_model
        )

        # ----------------------------------------------------------------
        # 9. Stage 4: PERSISTING — write result back to DB
        # ----------------------------------------------------------------
        await _progress(95, "Persisting results", stage=STAGE_PERSISTING)
        if await _check_cancel():
            await _mark_cancelled()
            return

        result_dict = dataclasses.asdict(eval_result)
        async with factory() as db:
            await db.execute(
                update(SignalRun)
                .where(SignalRun.id == run_id)
                .values(
                    status=STATUS_COMPLETED,
                    progress=100,
                    result=result_dict,
                    finished_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            await db.commit()

        logger.info("SignalRun %s completed: %s", run_id, signal_name)

        await rds.publish(EVENTS_CHANNEL, json.dumps({
            "type": "signal.completed",
            "run_id": run_id,
            "signal_name": signal_name,
            "sharpe": eval_result.sharpe,
            "mdd": eval_result.mdd,
        }))

    except Exception as exc:
        tb = traceback.format_exc()
        logger.exception("SignalRun %s failed: %s", run_id, exc)
        try:
            async with factory() as db:
                await db.execute(
                    update(SignalRun)
                    .where(SignalRun.id == run_id)
                    .values(
                        status=STATUS_FAILED,
                        error=tb[:2000],
                        finished_at=datetime.now(UTC).replace(tzinfo=None),
                    )
                )
                await db.commit()
            await rds.publish(EVENTS_CHANNEL, json.dumps({
                "type": "signal.failed",
                "run_id": run_id,
                "signal_name": signal_name,
                "error": str(exc)[:200],
            }))
        except Exception:
            logger.exception(
                "Failed to update SignalRun %s to failed", run_id
            )
    finally:
        await rds.close()


# ---------------------------------------------------------------------------
# Helpers — kernel resolution, spec rebuild, panel loading
# ---------------------------------------------------------------------------

def _resolve_kernel(method: str) -> Callable:  # type: ignore[type-arg]
    """Map a kernel slug to its callable.

    Kept as a function rather than ``_KERNEL_DISPATCH[method]`` directly so
    tests can monkey-patch this single seam (e.g. patch the dispatch dict
    or replace the function entirely).
    """
    try:
        return _KERNEL_DISPATCH[method]
    except KeyError:
        raise ValueError(
            f"Unknown signal kernel method {method!r}; expected one of "
            f"{sorted(_KERNEL_DISPATCH)}"
        )



def _load_aligned_panels(
    spec: SignalSpec,
    config: dict,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Load factor + future-returns panels for the signal pipeline.

    This module deliberately does **not** own the factor-data loading
    logic.  It is overridden in tests via patching, and in production it
    will be wired into :class:`tinohelm.factor.engine.orchestrator.Orchestrator`
    once the signal-orchestrator integration task lands (see acceptance
    criteria 3-4 of s16).  For now it raises NotImplementedError so a
    misconfigured production deployment fails fast rather than silently
    producing zero-weight panels.

    Parameters
    ----------
    spec:
        The reconstructed :class:`SignalSpec` (drives ``factor_ref``,
        ``rebalance_freq``, ``universe_ref``).
    config:
        The raw ``signal_runs.config`` dict (carries ``start`` / ``end``
        and ``periods_per_year`` knobs).

    Returns
    -------
    tuple[pl.DataFrame, pl.DataFrame]
        ``(factor_panel, future_returns)`` — both with a ``"ts"`` column
        plus N symbol columns.

    Raises
    ------
    NotImplementedError
        Always raised in production until the orchestrator integration is
        complete.  Tests patch this function with a fixture that returns
        synthetic panels.
    """
    raise NotImplementedError(
        "signal worker factor-panel loading is not yet wired to the "
        "factor orchestrator; integration is tracked separately. Tests "
        "monkey-patch tinohelm.signal.worker._load_aligned_panels."
    )


# ---------------------------------------------------------------------------
# Public start/stop API — mirrors tinohelm.factor.worker
# ---------------------------------------------------------------------------

def start_signal_worker(redis_url: str) -> asyncio.Task:
    """Start the signal worker as a background asyncio task.

    Called from the FastAPI ``startup`` lifecycle.  Returns the task so
    the caller can await it if needed (e.g. for testing).
    """

    async def _process(job_payload: str) -> None:
        await _process_job(job_payload, redis_url)

    return _handle.start(
        lambda: consumer_loop(
            redis_url,
            QUEUE_KEY,
            _process,
            worker_label="Signal worker",
        )
    )


def stop_signal_worker() -> None:
    """Cancel the signal worker task.  Called during FastAPI shutdown."""
    _handle.stop()


__all__ = [
    "QUEUE_KEY",
    "EVENTS_CHANNEL",
    "STAGE_ALIGNING",
    "STAGE_COMPUTING",
    "STAGE_EVALUATING",
    "STAGE_PERSISTING",
    "recover_interrupted_jobs",
    "start_signal_worker",
    "stop_signal_worker",
]
