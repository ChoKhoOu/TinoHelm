"""Signal framework API routes — 7 endpoints for the declarative signal system.

Mirrors the pattern established in :mod:`tinohelm.api.routes.factor`:

- Router registered in :mod:`tinohelm.api.app` with ``_auth_deps``
- Pydantic v2 request/response models (``model_dump`` not ``.dict()``)
- Lazy imports for heavy deps
- ``get_db`` + ``get_redis`` dependency injection

Endpoints
---------
1. ``GET  /api/signal/list``           — discover registered signals
2. ``POST /api/signal/run``            — enqueue an evaluation job
3. ``GET  /api/signal/runs``           — paginated list of runs (status filter)
4. ``GET  /api/signal/report/{run_id}``— full ``SignalEvalResult`` for a run
5. ``POST /api/signal/cancel/{run_id}``— set the cancel flag in Redis
6. ``POST /api/signal/compare``        — compare 2+ runs by Sharpe / MDD / Turnover
7. ``GET  /api/signal/export/{run_id}``— JSON export of the eval result
"""
from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.api.deps import get_db, get_redis
from tinohelm.db.models import SignalRun
from tinohelm.signal._run_helpers import rebalance_freq_to_ns

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signal", tags=["signal"])

QUEUE_KEY = "tino:signal:queue"
CANCEL_KEY_PREFIX = "tino:signal:cancel:"

# Cancel flag TTL — long enough for any reasonable in-flight job to observe it
# but short enough that a stale flag does not poison a fresh re-enqueue with
# the same run_id (which never happens in practice — run_id is a UUID4).
_CANCEL_TTL_SECONDS = 3600

# Metrics surfaced by /api/signal/compare.  Sharpe / MDD / Turnover are the
# Phase-2 signal scorecard columns called out in the design doc; the rest
# are kept available for callers that want a richer comparison.
_DEFAULT_COMPARE_METRICS: tuple[str, ...] = (
    "sharpe",
    "mdd",
    "turnover_annualized",
)
_ALLOWED_COMPARE_METRICS: frozenset[str] = frozenset({
    "sharpe",
    "mdd",
    "turnover_annualized",
    "capacity_score",
    "tail_loss_p99",
    "total_return",
    "cost_drag",
})

# ``POST /api/signal/run`` persists a server-owned config snapshot used by the
# worker, audit/replay, and the NT export path.  Callers may only supply
# evaluation-tuning extras here; PIT universe resolution, factor identity,
# constraints, bar template, and cost model must stay anchored to the
# registered ``SignalSpec`` plus server-side universe lookup.
_ALLOWED_RUN_CONFIG_EXTRA_KEYS: frozenset[str] = frozenset({
    "periods_per_year",
})

_ALLOWED_METHOD_PARAMS_BY_METHOD: dict[str, frozenset[str]] = {
    "top_k_long_short": frozenset({"k"}),
    "quantile_long_short": frozenset({"quantiles", "long_q", "short_q"}),
    "threshold_signed": frozenset({
        "upper",
        "lower",
        "long_weight",
        "short_weight",
    }),
    "zscore_clip": frozenset({"clip"}),
    "rank_to_weight": frozenset({"power"}),
}


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class RunSignalRequest(BaseModel):
    """Request body for ``POST /api/signal/run``."""

    signal_name: str
    universe_id: int | None = None
    start: str | None = None  # ISO date — optional for backwards-compat tests
    end: str | None = None
    force: bool = False  # ignore cache (forwarded into config)
    config: dict | None = None  # caller-supplied overrides merged on top of spec defaults


class CompareSignalsRequest(BaseModel):
    """Request body for ``POST /api/signal/compare``."""

    run_ids: list[str] = Field(..., min_length=2)
    metrics: list[str] | None = None  # default → _DEFAULT_COMPARE_METRICS


class CompareCellValue(BaseModel):
    """One cell of the compare matrix — a metric value with its rank."""

    value: float | None
    rank: int  # 1-based; ties broken by insertion order; None values rank last


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _spec_to_dict(spec) -> dict:
    """Convert a :class:`SignalSpec` to a JSON-serialisable dict."""
    d = dataclasses.asdict(spec)
    # ``method_params`` is intentionally compare=False/hash=False on the spec,
    # but it's a plain dict and serialises fine.  Strip nothing.
    return d


def _spec_to_list_item(spec) -> dict:
    """Slim summary card for ``GET /list``.

    Includes only the fields that drive the signal browser UI; the full
    spec is not surfaced (callers can always fetch it via the compare /
    report endpoints).
    """
    return {
        "name": spec.name,
        "version": spec.version,
        "method": spec.method,
        "weighting": spec.weighting,
        "factor_ref": spec.factor_ref,
        "universe_ref": spec.universe_ref,
        "rebalance_freq": spec.rebalance_freq,
        "gross_exposure": spec.gross_exposure,
        "net_exposure": spec.net_exposure,
        "max_position": spec.max_position,
        "extra_warmup_bars": spec.extra_warmup_bars,
        "description": spec.description,
        "deprecated": spec.deprecated,
    }


def _resolve_metrics(metrics: list[str] | None) -> list[str]:
    """Validate metrics filter; return the canonical list to compare on."""
    if not metrics:
        return list(_DEFAULT_COMPARE_METRICS)
    invalid = [m for m in metrics if m not in _ALLOWED_COMPARE_METRICS]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid compare metrics {invalid!r}; allowed: "
                f"{sorted(_ALLOWED_COMPARE_METRICS)}"
            ),
        )
    return list(metrics)


def _merge_allowed_run_config(
    config_payload: dict,
    user_config: Mapping[str, object] | None,
) -> None:
    """Merge caller-supplied ``/run`` config without clobbering server facts.

    The run config is a durable snapshot consumed by the worker and export
    endpoint.  Blindly applying ``dict.update`` would let a caller replace
    PIT-resolved ``universe_symbols`` / ``instrument_ids`` or the registered
    ``factor_ref`` / ``cost_model``.  Keep the surface deliberately small:

    * top-level allowlist for harmless evaluation extras;
    * nested merge for upstream factor ``factor_params``;
    * nested merge for signal-kernel ``method_params`` only.
    """
    if not user_config:
        return

    for key in _ALLOWED_RUN_CONFIG_EXTRA_KEYS:
        if key in user_config:
            config_payload[key] = user_config[key]

    if "factor_params" in user_config:
        raw_factor_params = user_config["factor_params"]
        if raw_factor_params is not None:
            if not isinstance(raw_factor_params, Mapping):
                raise HTTPException(
                    status_code=422,
                    detail="config.factor_params must be an object when provided",
                )
            config_payload["factor_params"] = {
                **dict(config_payload.get("factor_params") or {}),
                **dict(raw_factor_params),
            }

    if "method_params" not in user_config:
        return
    raw_method_params = user_config["method_params"]
    if raw_method_params is None:
        return
    if not isinstance(raw_method_params, Mapping):
        raise HTTPException(
            status_code=422,
            detail="config.method_params must be an object when provided",
        )
    method = str(config_payload.get("method") or "")
    allowed = _ALLOWED_METHOD_PARAMS_BY_METHOD.get(method, frozenset())
    invalid = sorted(set(raw_method_params) - allowed)
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=(
                f"config.method_params contains unsupported key(s) for "
                f"{method!r}: {invalid}. Put upstream factor overrides "
                "under config.factor_params instead."
            ),
        )

    config_payload["method_params"] = {
        **dict(config_payload.get("method_params") or {}),
        **dict(raw_method_params),
    }


def _build_compare_table(
    runs: list[SignalRun],
    metrics: list[str],
) -> dict:
    """Build the comparison table for ``POST /compare``.

    Returns the same shape as ``compare_multi.ranking_heatmap`` from the
    factor framework so the frontend can reuse the existing scorecard
    component:

        {
          "factors": [<run_label>, ...],
          "metrics": [<metric>, ...],
          "values": [[<float | None>, ...], ...],   # F × M
          "rankings": [[<int>, ...], ...],          # F × M (1-based)
        }

    ``rankings`` are 1-based per metric column.  Higher value → rank 1,
    except for ``mdd``, ``tail_loss_p99``, and ``cost_drag`` where lower
    (closer to 0) is better — those flip the sort.  ``None`` values rank
    last regardless.
    """
    F, M = len(runs), len(metrics)

    # Use signal_name + short suffix when duplicates exist.
    labels: list[str] = []
    seen: dict[str, int] = {}
    for r in runs:
        base = r.signal_name or r.id
        if base in seen:
            seen[base] += 1
            labels.append(f"{base}:{r.id[:8]}")
        else:
            seen[base] = 1
            labels.append(base)

    # F × M matrix of metric values (None when missing / non-finite).
    values: list[list[float | None]] = []
    for r in runs:
        row: list[float | None] = []
        result = r.result or {}
        for m in metrics:
            raw = result.get(m)
            if raw is None:
                row.append(None)
                continue
            try:
                v = float(raw)
            except (TypeError, ValueError):
                row.append(None)
                continue
            if v != v:  # NaN
                row.append(None)
                continue
            row.append(v)
        values.append(row)

    # Per-metric 1-based ranking.  ``tail_loss_p99`` is usually negative
    # (e.g. -5% is worse than -1%), so it must rank by closeness to zero
    # rather than raw ascending value.
    _LOWER_IS_BETTER = {"mdd", "cost_drag"}
    rankings: list[list[int]] = [[0] * M for _ in range(F)]
    for m_idx, m in enumerate(metrics):
        col = [(values[i][m_idx], i) for i in range(F)]
        valid = [(v, i) for v, i in col if v is not None]
        none_idx = [i for v, i in col if v is None]
        if m == "tail_loss_p99":
            valid.sort(key=lambda t: abs(t[0]))
        elif m in _LOWER_IS_BETTER:
            valid.sort(key=lambda t: t[0])
        else:
            valid.sort(key=lambda t: -t[0])
        for rank, (_, fi) in enumerate(valid, start=1):
            rankings[fi][m_idx] = rank
        last_rank = len(valid) + 1
        for fi in none_idx:
            rankings[fi][m_idx] = last_rank

    return {
        "factors": labels,
        "metrics": metrics,
        "values": values,
        "rankings": rankings,
    }


# ---------------------------------------------------------------------------
# 1. GET /api/signal/list
# ---------------------------------------------------------------------------

@router.get("/list")
async def list_signals(
    include_deprecated: bool = Query(
        default=False,
        description="Include signals flagged ``deprecated=True``.",
    ),
) -> list[dict]:
    """Return all registered signal metadata.

    The :class:`SignalRegistry` is constructed and scanned on every call —
    the scan is cheap (file-hash incremental) and stateless, so we don't
    need to wire it into the app lifespan singleton.
    """
    from tinohelm.signal.registry import SignalRegistry

    registry = SignalRegistry()
    specs = registry.scan()
    items = [
        _spec_to_list_item(spec)
        for spec in sorted(specs.values(), key=lambda s: s.name)
    ]
    if not include_deprecated:
        items = [it for it in items if not it.get("deprecated")]
    return items


# ---------------------------------------------------------------------------
# 2. POST /api/signal/run
# ---------------------------------------------------------------------------

@router.post("/run")
async def run_signal(
    req: RunSignalRequest,
    db: AsyncSession = Depends(get_db),
    rds: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Enqueue a signal evaluation job.

    Validates ``signal_name`` against the registry, **resolves the
    universe** (``req.universe_id`` > ``spec.universe_ref``) into a
    concrete PIT symbol list, creates a queued :class:`SignalRun` row
    with ``instrument_ids`` + ``bar_type_template`` baked into
    ``config``, and pushes the JSON payload onto ``tino:signal:queue``.

    Universe resolution at enqueue time (vs. deferring to the worker or
    the export endpoint) makes the ``signal_runs.config`` snapshot
    **self-contained**: a downstream consumer (worker, ``/export``,
    replay, audit) can reconstruct the exact instrument set that was
    trading at the anchor timestamp without a second DB round-trip.

    Raises
    ------
    HTTPException
        * ``404`` — ``signal_name`` not in the registry.
        * ``422`` — universe cannot be resolved (missing lookup keys, no
          matching row, or empty PIT symbol list).
    """
    from tinohelm.signal.registry import SignalRegistry
    from tinohelm.signal._run_helpers import (
        build_bar_type_template,
        resolve_universe_to_instrument_ids,
    )
    from tinohelm.signal.utils import validate_supported_signal_execution

    registry = SignalRegistry()
    registry.scan()
    spec = registry.get_spec(req.signal_name)
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail=f"Signal '{req.signal_name}' not found in registry",
        )
    try:
        validate_supported_signal_execution(spec)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    # Resolve universe → PIT symbols + NT instrument_ids
    # ------------------------------------------------------------------
    # Anchor the PIT lookup at ``req.end`` when supplied (so the universe
    # reflects the *historical* trading state at the end of the run
    # window), else ``utcnow()``.  ``req.start`` is deliberately not used
    # — signals need the symbol list that was active at the end of the
    # window to avoid look-back bias on delisted names.
    anchor_ts: datetime | None = None
    if req.end:
        try:
            anchor_ts = datetime.fromisoformat(req.end.replace("Z", "+00:00"))
            if anchor_ts.tzinfo is not None:
                anchor_ts = anchor_ts.astimezone(UTC).replace(tzinfo=None)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid ISO-8601 end timestamp: {req.end!r}",
            )

    try:
        (
            resolved_universe_id,
            _universe_name,
            pit_symbols,
            instrument_ids,
            pit_rules_json,
        ) = (
            await resolve_universe_to_instrument_ids(
                universe_id=req.universe_id,
                universe_ref=spec.universe_ref,
                anchor_ts=anchor_ts,
                db=db,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        bar_type_template = build_bar_type_template(spec.rebalance_freq)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    run_id = str(uuid4())

    # Build the persisted config dict.  Includes every field the worker's
    # ``_build_spec_from_config`` knows how to consume + the request-side
    # extras (universe_id / start / end / force) + the freshly resolved
    # ``instrument_ids`` / ``bar_type_template`` so re-runs from the DB
    # row alone are reproducible and the /export endpoint never sees an
    # empty symbol list.
    config_payload: dict = {
        # Spec fields — used by worker._build_spec_from_config
        "factor_ref": spec.factor_ref,
        "method": spec.method,
        "weighting": spec.weighting,
        "rebalance_freq": spec.rebalance_freq,
        "universe_ref": spec.universe_ref,
        "gross_exposure": spec.gross_exposure,
        "net_exposure": spec.net_exposure,
        "max_position": spec.max_position,
        "turnover_budget": spec.turnover_budget,
        "factor_params": dict(spec.factor_params),
        "method_params": dict(spec.method_params),
        "cost_model": dataclasses.asdict(spec.cost_model),
        "extra_warmup_bars": spec.extra_warmup_bars,
        "version": spec.version,
        "code_hash": spec.code_hash,
        "description": spec.description,
        "deprecated": spec.deprecated,
        # Universe resolution — populated from the DB row at enqueue time.
        "universe_id": resolved_universe_id,
        "universe_pit_rules": pit_rules_json,
        "universe_symbols": pit_symbols,
        "instrument_ids": instrument_ids,
        "bar_type_template": bar_type_template,
        # Request-side extras
        "start": req.start,
        "end": req.end,
        "force": req.force,
    }
    if req.config:
        _merge_allowed_run_config(config_payload, req.config)

    run = SignalRun(
        id=run_id,
        signal_name=req.signal_name,
        factor_ref=spec.factor_ref,
        status="queued",
        config=config_payload,
        progress=0,
        progress_stage=None,
        code_hash=spec.code_hash or None,
        universe_id=resolved_universe_id,
    )
    db.add(run)
    await db.commit()

    queue_payload = json.dumps({
        "run_id": run_id,
        "signal_name": req.signal_name,
        "config": config_payload,
    })
    try:
        await rds.lpush(QUEUE_KEY, queue_payload)
    except Exception as exc:
        err = f"enqueue failed: {type(exc).__name__}: {exc}"
        logger.exception("Failed to enqueue signal run %s", run_id)
        run.status = "failed"
        run.error = err
        run.finished_at = datetime.now(UTC).replace(tzinfo=None)
        run.progress_stage = None
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail="failed to enqueue signal run",
        ) from exc

    logger.info(
        "Signal run %s enqueued: %s (universe_id=%s, %d symbols)",
        run_id, req.signal_name, resolved_universe_id, len(pit_symbols),
    )
    return {"run_id": run_id, "status": "queued"}


# ---------------------------------------------------------------------------
# 3. GET /api/signal/runs  (paginated + status filter)
# ---------------------------------------------------------------------------

@router.get("/runs")
async def list_runs(
    status: str | None = Query(
        None,
        description="Filter by status (queued / running / completed / failed / cancelled)",
    ),
    signal_name: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List :class:`SignalRun` records with pagination + optional filters."""
    stmt = select(SignalRun).order_by(SignalRun.created_at.desc())
    if status:
        stmt = stmt.where(SignalRun.status == status)
    if signal_name:
        stmt = stmt.where(SignalRun.signal_name == signal_name)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    rows = (await db.execute(stmt)).scalars().all()
    return {
        "runs": [
            {
                "run_id": r.id,
                "signal_name": r.signal_name,
                "factor_ref": r.factor_ref,
                "status": r.status,
                "progress": r.progress,
                "progress_stage": r.progress_stage,
                "error": r.error,
                "created_at": (r.created_at.isoformat() + "Z") if r.created_at else None,
                "started_at": (r.started_at.isoformat() + "Z") if r.started_at else None,
                "finished_at": (r.finished_at.isoformat() + "Z") if r.finished_at else None,
            }
            for r in rows
        ],
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# 4. GET /api/signal/report/{run_id}
# ---------------------------------------------------------------------------

@router.get("/report/{run_id}")
async def get_report(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the full :class:`SignalEvalResult` for a completed run."""
    run = (await db.execute(
        select(SignalRun).where(SignalRun.id == run_id)
    )).scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail="SignalRun not found")

    base = {
        "run_id": run.id,
        "signal_name": run.signal_name,
        "factor_ref": run.factor_ref,
        "status": run.status,
        "progress": run.progress,
        "progress_stage": run.progress_stage,
        "error": run.error,
    }
    if run.status == "completed":
        base["result"] = run.result
    return base


# ---------------------------------------------------------------------------
# 5. POST /api/signal/cancel/{run_id}
# ---------------------------------------------------------------------------

@router.post("/cancel/{run_id}")
async def cancel_run(
    run_id: str,
    rds: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Set the cancel flag for a signal run.

    The worker re-checks the flag between every stage; setting it after the
    job has already entered the ``persisting`` stage is a no-op.
    """
    cancel_key = f"{CANCEL_KEY_PREFIX}{run_id}"
    await rds.set(cancel_key, "1", ex=_CANCEL_TTL_SECONDS)
    return {"run_id": run_id, "cancel_set": True}


# ---------------------------------------------------------------------------
# 6. POST /api/signal/compare
# ---------------------------------------------------------------------------

@router.post("/compare")
async def compare_signals(
    req: CompareSignalsRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Compare 2+ signal runs by Sharpe / MDD / Turnover.

    Returns a ``ranking_heatmap``-shaped dict identical to
    :func:`tinohelm.factor.evaluation.compare.compare_multi`'s table so
    the frontend reuses its existing scorecard component.
    """
    metrics = _resolve_metrics(req.metrics)

    # Load runs preserving the order the caller requested.
    rows = (
        await db.execute(
            select(SignalRun).where(SignalRun.id.in_(req.run_ids))
        )
    ).scalars().all()
    by_id: dict[str, SignalRun] = {r.id: r for r in rows}
    missing = [rid for rid in req.run_ids if rid not in by_id]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"SignalRun(s) not found: {missing}",
        )
    ordered_runs = [by_id[rid] for rid in req.run_ids]

    not_completed = [
        r.id for r in ordered_runs if r.status != "completed" or not r.result
    ]
    if not_completed:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot compare: the following runs are not completed or "
                f"have no result: {not_completed}"
            ),
        )

    table = _build_compare_table(ordered_runs, metrics)
    # Provide a flat per-run summary alongside the heatmap so callers that
    # only want a list (not the F×M grid) don't have to re-zip the table.
    summaries = [
        {
            "run_id": r.id,
            "signal_name": r.signal_name,
            "factor_ref": r.factor_ref,
            "metrics": {m: (r.result or {}).get(m) for m in metrics},
        }
        for r in ordered_runs
    ]
    return {
        "comparison": summaries,
        "ranking_heatmap": table,
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# 7. GET /api/signal/export/{run_id}
# ---------------------------------------------------------------------------

def _parse_rebalance_to_ns(rebalance_freq: str) -> int:
    """Convert '1h' / '1d' / '5m' / '30s' to nanoseconds.

    Accepts case-insensitive suffix (s/m/h/d).  Invalid or empty values raise
    ``ValueError`` so export cannot silently diverge from the bar-type template
    fallback semantics.
    """
    return rebalance_freq_to_ns(rebalance_freq)


_DEFAULT_STRATEGY_CLASS = (
    "tinohelm.nt_adapter.signal_driven_strategy:SignalDrivenStrategy"
)


def _validate_strategy_class_for_signal(
    strategy_class: str,
    factor_spec,
    factor_name: str,
) -> None:
    """Reject exports that would never trade.

    Two failure modes guarded:

    1. Caller used the default :class:`SignalDrivenStrategy` but the
       referenced factor is unrunnable on the live path because it
       requires non-OHLCV inputs that ``cache.bars`` cannot satisfy
       (funding_rate / open_interest / quote_tick / trade_tick).
    2. Caller used the default :class:`SignalDrivenStrategy` but the
       factor is not registered at all (pre-existing graceful-degrade
       path is still allowed — log only, do not reject — to preserve
       backwards compatibility with tests).

    Raises
    ------
    HTTPException
        ``400`` when the export would produce a strategy that never
        trades.  The detail message names the offending fields so the
        operator can fix the signal (or supply a custom subclass via
        ``?strategy_class=...``).
    """
    if strategy_class != _DEFAULT_STRATEGY_CLASS:
        # Custom subclass: caller takes responsibility for wiring its own
        # _compute_factor_panel.  We trust it and skip validation.
        return

    if factor_spec is None:
        # Graceful-degradation case retained for backwards compatibility:
        # tests may reference factors that aren't in the registry but
        # legitimately rely on monkey-patching at runtime.  Log only.
        return

    # Lazy import — keeps the API route module light at startup time.
    from tinohelm.nt_adapter.factor_panel import factor_uses_only_bar_fields

    if not factor_uses_only_bar_fields(factor_spec):
        unsupported = sorted(
            spec.field_name for spec in factor_spec.input_specs
            if spec.field_name not in ("close", "open", "high", "low", "volume")
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot export signal: factor {factor_name!r} requires "
                f"non-OHLCV inputs {unsupported} which the default "
                f"SignalDrivenStrategy cannot resolve from cache.bars.  "
                "Either switch to a factor whose inputs are limited to "
                "OHLCV (open/high/low/close/volume), or supply a custom "
                "?strategy_class=<module>:<Class> that overrides "
                "_compute_factor_panel for this data source."
            ),
        )


@router.get("/export/{run_id}")
async def export_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    strategy_class: str = Query(
        default=_DEFAULT_STRATEGY_CLASS,
        description=(
            "Fully qualified strategy class path "
            "(format: ``module.path:ClassName``).  Defaults to the built-in "
            "SignalDrivenStrategy.  When the default is used the endpoint "
            "validates that the upstream factor is runnable from "
            "OHLCV-only inputs; non-default values are trusted (caller is "
            "responsible for the custom subclass's _compute_factor_panel)."
        ),
    ),
) -> dict:
    """Export a completed :class:`SignalRun` as portfolio.yaml-compatible JSON.

    The response is intentionally shaped as a ``portfolio.yaml``-compatible
    dict so it can be consumed directly by ``/api/node/strategy/start`` or
    saved to disk.

    Key additions over the ``/report`` endpoint:

    * ``strategy_class`` — fixed import path for :class:`SignalDrivenStrategy`,
      overridable via the ``strategy_class`` query parameter.
    * Server-side ``warmup_bars`` derivation:
      ``factor.lookback + signal_spec.extra_warmup_bars``.  The factor
      lookback is resolved via :class:`tinohelm.factor.registry.Registry`
      using the ``factor_ref`` stored in ``signal_runs.config``; the derived
      value is written into both ``config.warmup_bars`` and the ``metadata``
      block so callers can audit the derivation.
    * Server-side OHLCV-input validation:
      when ``strategy_class`` is the default :class:`SignalDrivenStrategy`,
      we refuse to export signals whose factor needs non-OHLCV inputs
      (funding_rate / open_interest / quote_tick / trade_tick) because the
      live path cannot satisfy them from ``cache.bars``.  Returning 400 at
      this boundary prevents the previous silent-failure mode where the
      strategy ran but never traded.
    * ``metadata`` — attribution fields (``exported_from_run_id``,
      ``factor_lookback``, ``extra_warmup_bars``, ``warmup_bars_derived``).
    """
    run = (await db.execute(
        select(SignalRun).where(SignalRun.id == run_id)
    )).scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail="SignalRun not found")

    if run.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"SignalRun '{run_id}' is not completed (status={run.status!r})",
        )

    config: dict = run.config or {}
    from tinohelm.signal.utils import (
        signal_spec_from_dict,
        validate_supported_signal_execution,
    )

    try:
        validate_supported_signal_execution(
            signal_spec_from_dict(run.signal_name, config)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot export SignalRun {run_id!r}: {exc}",
        ) from exc

    # ------------------------------------------------------------------
    # Server-side warmup_bars derivation
    # ------------------------------------------------------------------
    # factor_ref may be plain "name" or "name@version" — strip version.
    factor_ref: str = config.get("factor_ref") or run.factor_ref or ""
    factor_name = factor_ref.split("@", 1)[0]
    extra_warmup_bars: int = int(config.get("extra_warmup_bars", 0))
    factor_params: dict = dict(config.get("factor_params") or {})

    try:
        from tinohelm.factor.registry import Registry as FactorRegistry

        factor_registry = FactorRegistry()
        factor_registry.scan()
        factor_spec = factor_registry.get_spec(factor_name)
    except Exception as exc:
        logger.warning("export_run: factor registry scan failed: %s", exc)
        factor_spec = None

    # Layer-2 contract guard — reject exports the live path can't honour.
    _validate_strategy_class_for_signal(strategy_class, factor_spec, factor_name)

    if factor_spec is not None:
        factor_lookback: int = int(factor_spec.lookback)
    else:
        # Graceful degradation: use 0 lookback so warmup equals extra only.
        factor_lookback = 0
        logger.warning(
            "export_run: factor %r not found in registry; warmup_bars = extra_warmup_bars only",
            factor_name,
        )
    if "lookback" in factor_params:
        factor_lookback = max(factor_lookback, int(factor_params["lookback"]))

    warmup_bars: int = factor_lookback + extra_warmup_bars

    # ------------------------------------------------------------------
    # Empty-universe safety net
    # ------------------------------------------------------------------
    # Legacy runs created before universe resolution was enforced at the
    # /run boundary may have been persisted without an instrument_ids
    # entry.  Exporting such a run would produce a
    # SignalDrivenStrategyConfig whose ``instrument_ids=()`` crashes
    # :class:`BarSynchronizer.__init__` at on_start time ("expected_symbols
    # is empty").  Reject at the API boundary with a clear, actionable
    # message rather than letting the NT runtime raise deep inside the
    # subprocess.
    stored_instrument_ids = config.get("instrument_ids", [])
    if not stored_instrument_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot export SignalRun {run_id!r}: instrument_ids is "
                "empty.  The run was created before universe resolution "
                "was enforced at /api/signal/run (PR #140).  Re-run with "
                "a valid universe_id or universe_ref to regenerate a "
                "SignalRun whose config carries a non-empty "
                "instrument_ids list."
            ),
        )

    # ------------------------------------------------------------------
    # Build portfolio.yaml-compatible export payload
    # ------------------------------------------------------------------
    rebalance_freq: str = config.get("rebalance_freq", "1h")

    signal_spec_json: dict = {
        "name": config.get("signal_name") or run.signal_name,
        "factor_ref": factor_ref,
        "method": config.get("method", ""),
        "weighting": config.get("weighting", "equal"),
        "rebalance_freq": rebalance_freq,
        "universe_ref": config.get("universe_ref", ""),
        "gross_exposure": config.get("gross_exposure", 1.0),
        "net_exposure": config.get("net_exposure", 0.0),
        "max_position": config.get("max_position", 0.10),
        "turnover_budget": config.get("turnover_budget"),
        "factor_params": config.get("factor_params", {}),
        "method_params": config.get("method_params", {}),
        "cost_model": config.get("cost_model", {}),
        "extra_warmup_bars": extra_warmup_bars,
        "version": config.get("version", "1.0.0"),
        "code_hash": config.get("code_hash", ""),
        "description": config.get("description", ""),
        "deprecated": config.get("deprecated", False),
    }

    # Bar-type template — stored by /run for new records; fall back to
    # the canonical rebalance-freq-derived form for legacy runs that were
    # enqueued before the resolution machinery landed.  The fallback uses
    # the same :func:`build_bar_type_template` helper that /run calls so
    # both code paths stay in lockstep.
    stored_bar_type_template = config.get("bar_type_template") or ""
    if not stored_bar_type_template:
        from tinohelm.signal._run_helpers import build_bar_type_template
        try:
            stored_bar_type_template = build_bar_type_template(rebalance_freq)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        rebalance_freq_ns = _parse_rebalance_to_ns(rebalance_freq)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "strategy_class": strategy_class,
        "config": {
            "signal_name": run.signal_name,
            "instrument_ids": stored_instrument_ids,
            "bar_type_template": stored_bar_type_template,
            "warmup_bars": warmup_bars,
            "rebalance_freq_ns": rebalance_freq_ns,
            "signal_spec_json": signal_spec_json,
            "factor_lookback": factor_lookback,
        },
        "metadata": {
            "exported_from_run_id": run_id,
            "factor_ref": factor_ref,
            "factor_lookback": factor_lookback,
            "extra_warmup_bars": extra_warmup_bars,
            "warmup_bars_derived": warmup_bars,
            "code_hash": run.code_hash,
            "started_at": (run.started_at.isoformat() + "Z") if run.started_at else None,
            "finished_at": (run.finished_at.isoformat() + "Z") if run.finished_at else None,
        },
    }
