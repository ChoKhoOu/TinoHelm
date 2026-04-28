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
from types import SimpleNamespace
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
from tinohelm.factor.engine.planner import _infer_source
from tinohelm.signal.types import SignalSpec
from tinohelm.signal.utils import (
    signal_spec_from_dict,
    validate_supported_signal_execution,
)

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

        queued_runs = (
            await db.execute(
                select(SignalRun.id, SignalRun.signal_name, SignalRun.config)
                .where(SignalRun.status == "queued")
                .order_by(SignalRun.created_at.asc())
            )
        ).all()

        if queued_runs:
            for run_id, signal_name, config in queued_runs:
                payload = json.dumps({
                    "run_id": run_id,
                    "signal_name": signal_name,
                    "config": config or {},
                })
                # Queue contract is LPUSH producers + BRPOP consumer.  With
                # rows selected created_at ASC, LPUSHing in that same order
                # leaves the oldest job at the right side of the list, so
                # BRPOP resumes queued runs chronologically.  Do not delete
                # the shared queue here: rolling startups can overlap with
                # fresh API enqueues.  Duplicate payloads are harmless because
                # _process_job claims rows by DB status before execution.
                await rds.lpush(QUEUE_KEY, payload)

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
            now = datetime.now(UTC).replace(tzinfo=None)
            async with factory() as db:
                await db.execute(
                    update(SignalRun)
                    .where(SignalRun.id == run_id)
                    .values(
                        status=STATUS_CANCELLED,
                        finished_at=now,
                        progress_stage=None,
                    )
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
            if run.status != "queued":
                logger.info(
                    "SignalRun %s status is %r, not queued; skipping",
                    run_id, run.status,
                )
                return

            if config_dict is None:
                config_dict = run.config or {}
            if not signal_name:
                signal_name = run.signal_name

            claim_result = await db.execute(
                update(SignalRun)
                .where(SignalRun.id == run_id, SignalRun.status == "queued")
                .values(
                    status=STATUS_RUNNING,
                    started_at=datetime.now(UTC).replace(tzinfo=None),
                    progress=0,
                )
            )
            if getattr(claim_result, "rowcount", None) == 0:
                logger.info("SignalRun %s was already claimed; skipping", run_id)
                await db.rollback()
                return
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
            now = datetime.now(UTC).replace(tzinfo=None)
            async with factory() as db_c:
                await db_c.execute(
                    update(SignalRun)
                    .where(SignalRun.id == run_id)
                    .values(
                        status=STATUS_CANCELLED,
                        finished_at=now,
                        progress_stage=None,
                    )
                )
                await db_c.commit()

        # ----------------------------------------------------------------
        # 5. Build SignalSpec for kernel + evaluator dispatch
        # ----------------------------------------------------------------
        spec = signal_spec_from_dict(signal_name, config_dict)
        validate_supported_signal_execution(spec)
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

    Production implementation
    -------------------------
    1. Resolve the factor kernel + :class:`FactorSpec` from
       :class:`tinohelm.factor.registry.Registry`.
    2. Rebuild :class:`tinohelm.factor.universe.Universe` from the persisted
       ``config["universe_pit_rules"]`` boundary snapshot, while using
       ``config["universe_symbols"]`` as the anchor-time loading subset.  This
       keeps historical PIT masking intact across listing/delisting windows.
    3. Build a :class:`DataLayer` against ``catalog_root`` and issue
       :class:`DataRequest` entries for every ``input_spec`` the factor
       declares, clipped to the ``[start, end]`` window from ``config``.
    4. Call the factor kernel with the resulting panels — the kernel
       signature matches :mod:`tinohelm.factor.builtins` (panels as
       keyword arguments + a ``params`` kwarg).
    5. Compute ``future_returns`` as a per-symbol 1-period shift of close:
       ``r_t = close_{t+1} / close_t - 1``.  This matches what the
       :class:`SignalEvaluator` expects (``inner-join on ts``; NaN tails
       are safely dropped inside the evaluator).

    The frequency for data loading is derived from
    :attr:`SignalSpec.rebalance_freq` via the shared ``INTERVAL_MAP``
    contract — same mapping used by :mod:`tinohelm.strategy.loader_helpers`
    so research + live + signal stays consistent.

    Parameters
    ----------
    spec:
        The reconstructed :class:`SignalSpec` (drives ``factor_ref``,
        ``rebalance_freq``, ``universe_ref``).
    config:
        The raw ``signal_runs.config`` dict (carries ``start`` / ``end``,
        ``universe_pit_rules`` / ``universe_symbols`` / ``instrument_ids`` /
        ``periods_per_year``).

    Returns
    -------
    tuple[pl.DataFrame, pl.DataFrame]
        ``(factor_panel, future_returns)`` — both with a ``"ts"`` column
        plus N symbol columns.  Symbols in ``future_returns`` are always
        the TinoHelm short form (``"BTCUSDT-PERP"``) so the
        :class:`SignalEvaluator` inner-joins them against the factor
        panel cleanly.

    Raises
    ------
    ValueError
        The config is missing the universe_symbols list (legacy rows)
        or the factor is not in the registry.  Callers translate this
        into a ``status="failed"`` write.
    """
    # Imports kept function-local so the module stays importable in
    # environments where only the stub is needed (unit tests patch this
    # function before calling ``_process_job``).
    from pathlib import Path

    from tinohelm.core.paths import paths as _paths
    from tinohelm.factor.data_layer import DataLayer
    from tinohelm.factor.registry import Registry as FactorRegistry
    from tinohelm.factor.types import DataRequest
    from tinohelm.factor.universe import Universe
    from tinohelm.signal._run_helpers import normalize_rebalance_freq

    # ----------------------------------------------------------------
    # 1. Resolve factor kernel + FactorSpec from the registry
    # ----------------------------------------------------------------
    factor_name = (spec.factor_ref or "").split("@", 1)[0]
    if not factor_name:
        raise ValueError(
            "SignalSpec.factor_ref is empty; cannot resolve a factor kernel"
        )
    registry = FactorRegistry()
    registry.scan()
    factor_kernel = registry.get_kernel(factor_name)
    factor_spec = registry.get_spec(factor_name)
    if factor_kernel is None or factor_spec is None:
        raise ValueError(
            f"Factor {factor_name!r} not found in registry — signal "
            "config points at a factor that was removed or never scanned."
        )
    needs_backend = getattr(factor_spec, "needs_backend", False)
    if type(needs_backend).__module__ == "unittest.mock":
        needs_backend = False
    if needs_backend:
        raise ValueError(
            f"Signal worker does not support backend-first factor "
            f"{factor_name!r} yet; choose a non-backend factor or run it "
            "through the factor scheduler first."
        )

    # ----------------------------------------------------------------
    # 2. Build Universe from the persisted PIT boundary snapshot
    # ----------------------------------------------------------------
    pit_symbols = config.get("universe_symbols") or []
    if not pit_symbols:
        # Defensive: the /run endpoint always persists this list.  A
        # missing list means the run predates universe resolution —
        # the worker refuses to silently fall back to CSV scanning
        # because that would mask the upstream contract violation.
        raise ValueError(
            "signal_runs.config is missing 'universe_symbols'; the run "
            "was created before /api/signal/run enforced universe "
            "resolution.  Re-create the run via POST /api/signal/run."
        )
    pit_rules_snapshot = config.get("universe_pit_rules") or {}
    if pit_rules_snapshot:
        universe = Universe.from_db_row(
            SimpleNamespace(
                name=str(config.get("universe_ref") or "signal_run_universe"),
                pit_rules_json=pit_rules_snapshot,
            )
        )
    else:
        # Legacy rows created before ``universe_pit_rules`` was persisted can
        # only provide the anchor-time load subset.  Keep them runnable, but
        # new /run rows preserve listing/delisting boundaries so historical
        # DataLayer PIT masking is not downgraded to a permanent universe.
        logger.warning(
            "_load_aligned_panels: signal run config missing "
            "'universe_pit_rules'; falling back to permanent anchor-time "
            "universe for legacy compatibility"
        )
        universe = Universe.from_symbols(tuple(pit_symbols))

    # ----------------------------------------------------------------
    # 3. Frequency derivation — rebalance_freq → INTERVAL_MAP key
    # ----------------------------------------------------------------
    # Keep research DataLayer cadence, /export rebalance gate, and live bar
    # subscription template on one strict parser; invalid strings fail loudly
    # instead of falling back to inconsistent cadences.
    freq = normalize_rebalance_freq(spec.rebalance_freq)

    # ----------------------------------------------------------------
    # 4. Build DataLayer + issue per-input DataRequests
    # ----------------------------------------------------------------
    catalog_root = Path(config.get("catalog_path") or str(_paths.get("catalog")))
    data_layer = DataLayer(universe=universe, catalog_root=catalog_root)

    start = config.get("start")
    end = config.get("end")

    # When input_specs is empty (user fixture skipping the auto-detection)
    # fall back to ``close`` — matches ``compute_latest_factor_panel``.
    input_fields = (
        [s.field_name for s in factor_spec.input_specs]
        if factor_spec.input_specs
        else ["close"]
    )
    # DataLayer groups by (field, frequency, source) so listing every
    # symbol per field is fine — it fans out the ThreadPoolExecutor.
    merged_params: dict = dict(factor_spec.params or {})
    factor_param_overrides = dict(config.get("factor_params") or {})
    factor_param_overrides.update(dict(spec.factor_params or {}))
    merged_params.update(factor_param_overrides)
    effective_lookback = max(
        int(factor_spec.lookback),
        int(merged_params.get("lookback", factor_spec.lookback)),
    )
    requests: list[DataRequest] = []
    for field_name in input_fields:
        for sym in pit_symbols:
            source = _infer_source(field_name)
            if source is None:
                raise ValueError(
                    f"_load_aligned_panels: cannot infer DataLayer source "
                    f"for field {field_name!r} — add it to "
                    f"tinohelm.factor.engine.planner._infer_source()"
                )
            requests.append(
                DataRequest(
                    symbol=sym,
                    field_name=field_name,
                    frequency=freq,
                    lookback=effective_lookback,
                    source=source,
                )
            )
    panels = data_layer.load(requests, start=start, end=end)

    # ----------------------------------------------------------------
    # 5. Run the factor kernel
    # ----------------------------------------------------------------
    # Kernel signature: ``kernel(**panels_by_field, params=...)`` — same
    # convention as :class:`tinohelm.factor.engine.scheduler.Scheduler`.
    factor_panel = factor_kernel(**panels, params=merged_params)

    # ----------------------------------------------------------------
    # 6. Future returns — close_{t+1} / close_t - 1
    # ----------------------------------------------------------------
    # Always reload close independently so future_returns are defined
    # even when the factor itself doesn't need close (e.g. funding-rate
    # factors).  Reuses the same DataLayer + window to stay PIT-aligned.
    close_requests = [
        DataRequest(
            symbol=sym,
            field_name="close",
            frequency=freq,
            lookback=0,  # no warmup for the eval panel
            source="bar",
        )
        for sym in pit_symbols
    ]
    close_panel = data_layer.load(close_requests, start=start, end=end).get(
        "close"
    )
    if close_panel is None or close_panel.is_empty():
        raise ValueError(
            "DataLayer returned an empty close panel for the signal run "
            "window; cannot compute future returns"
        )
    symbol_cols = [c for c in close_panel.columns if c != "ts"]
    # Shift each symbol column by -1 (future close) and divide by current.
    future_returns = close_panel.with_columns(
        [
            (
                (pl.col(c).shift(-1) / pl.col(c)) - 1.0
            ).alias(c)
            for c in symbol_cols
        ]
    )

    return factor_panel, future_returns


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
