"""Factor framework API routes — 8 endpoints for the declarative factor system.

Mirrors the pattern established in ``research.py``:
- Router registered in app.py with ``_auth_deps``
- Pydantic request/response models
- Lazy imports for heavy deps (pandas, numpy)
- ``get_db`` + ``get_redis`` dependency injection
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import platform
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.api.deps import get_db, get_redis, get_settings_dep
from tinohelm.core.config import Settings
from tinohelm.db.models import FactorRun

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from tinohelm.factor.types import EvalResult

router = APIRouter(prefix="/api/factor", tags=["factor"])

QUEUE_KEY = "tino:factor:queue"
CANCEL_KEY_PREFIX = "tino:factor:cancel:"
_CANCEL_TTL_SECONDS = 3600
VALID_SEGMENT_PROVIDERS: tuple[str, ...] = (
    "btc_trend",
    "vol_regime",
    "funding_level",
)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class ExploreRequest(BaseModel):
    """Request body for POST /api/factor/explore (synchronous quick explore)."""

    factor_name: str
    config: dict
    params: dict | None = None
    summary: bool = False
    # Tri-state: omitted preserves legacy full-detail output unless summary=true,
    # where LLM-friendly callers expect compact output by default.
    detail: bool | None = None
    fields: list[str] | None = None


class RunRequest(BaseModel):
    """Request body for POST /api/factor/run (async deep diagnostic)."""

    factor_name: str
    config: dict
    params: dict | None = None
    full: bool = False
    summary: bool = False
    detail: bool | None = None
    fields: list[str] | None = None


class CreateRequest(BaseModel):
    """Request body for POST /api/factor/create."""

    name: str
    category: str = "自定义"
    template: str | None = None


class ParamsGridRequest(BaseModel):
    """Request body for POST /api/factor/params_grid (synchronous grid search)."""

    factor_name: str
    grid: dict[str, list]
    top_k: int = 3
    corr_filter: float = 0.7
    forward_periods: int = 5
    start: str
    end: str
    universe: list[str] | None = None
    n_jobs: int = -1


class CompareRequest(BaseModel):
    """Request body for POST /api/factor/compare (pairwise metric diff + bootstrap CI)."""

    eval_a_run_id: str
    eval_b_run_id: str
    n_bootstrap: int = 1000
    confidence: float = 0.95


class CompareMultiRequest(BaseModel):
    """Request body for POST /api/factor/compare/multi (multi-factor report)."""

    eval_run_ids: list[str]  # at least 2
    n_bootstrap: int = 1000


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _spec_to_dict(spec: object) -> dict:
    """Convert a FactorSpec dataclass to a JSON-serialisable dict."""
    import dataclasses as dc
    d = dc.asdict(spec)  # type: ignore[arg-type]
    # Flatten nested input_specs tuples
    d["input_fields"] = [inp["field_name"] for inp in d.pop("input_specs", [])]
    d["params_schema"] = d.pop("params", {})
    d.pop("output_spec", None)
    if not d.get("metadata"):
        d["metadata"] = {}
    if not d.get("warnings"):
        d["warnings"] = []
    if d.get("name") == "vwap_mid_reversion_proxy":
        d["metadata"].update({
            "research_only_reason": "1m OHLCV proxy; not exact trade+quote execution-premium data.",
            "proxy_level": "1m_ohlcv_bar_proxy",
            "data_granularity": "1m_ohlcv_bars",
            "required_exact_data": ["trade_ticks", "quote_ticks", "l1_mid"],
        })
        d["warnings"].append({
            "code": "research_proxy_not_execution_premium",
            "message": "Do not describe this factor as exact buy/sell VWAP versus L1 mid execution premium.",
        })
    return d


def _effective_params(spec: object, config: object) -> dict:
    merged = dict(getattr(spec, "params", {}) or {})
    merged.update(dict(getattr(config, "params", {}) or {}))
    return merged


def _validate_segments(segments: tuple[str, ...]) -> None:
    invalid = [s for s in segments if s not in VALID_SEGMENT_PROVIDERS]
    if invalid:
        raise ValueError({
            "code": "unknown_segment_provider",
            "message": "Unknown factor segment provider(s).",
            "invalid_values": invalid,
            "valid_values": list(VALID_SEGMENT_PROVIDERS),
        })


def _parse_and_validate_config(config_dict: dict, *, params: dict | None = None):
    from tinohelm.factor.config import parse_eval_config

    config = parse_eval_config(config_dict, params=params)
    _validate_segments(config.segments)
    return config


def _report_payload(
    *,
    run_id: str,
    factor_name: str,
    status: str,
    progress: int | None,
    error: str | None,
    result: dict | None,
    summary: bool = False,
    detail: bool = True,
    fields: list[str] | None = None,
) -> dict:
    """Build the API report payload without hiding job status.

    The HTTP/API response stays backward-compatible (`status` at top level),
    while the Rust CLI maps `status=failed` to a top-level LLM envelope error.
    """
    if status == "completed":
        data = {
            "run_id": run_id,
            "factor_name": factor_name,
            "status": "completed",
            "transport_ok": True,
            "job_ok": True,
            "job_status": status,
            "meta": _stored_result_meta(result),
        }
        if summary:
            data["summary"] = _select_fields(
                _stored_result_summary(result, run_id=run_id, status=status),
                fields,
            )
        if detail:
            data["result"] = _select_fields(result or {}, fields) if fields else result
        if not summary and not detail:
            data["summary"] = _select_fields(
                _stored_result_summary(result, run_id=run_id, status=status),
                fields,
            )
        return data

    safe_message = (error or f"Factor run is {status}")[-4000:]
    failed = status == "failed"
    return {
        "run_id": run_id,
        "factor_name": factor_name,
        "status": status,
        "progress": progress,
        "error": error,
        "message": safe_message,
        "transport_ok": True,
        "job_ok": False if failed else None,
        "job_status": status,
        "error_code": "factor_run_failed" if failed else None,
    }


def _stored_result_summary(
    result: dict | None,
    *,
    run_id: str | None = None,
    status: str | None = None,
) -> dict:
    result = result or {}
    data = {
        "ic_mean": result.get("ic_mean"),
        "ir": result.get("ir"),
        "t_stat": result.get("ic_tstat"),
        "tstat": result.get("ic_tstat"),
        "rating": result.get("rating"),
        "monotonicity": result.get("is_monotonic"),
        "warnings": result.get("warnings", []),
        "effective_params": result.get("effective_params", {}),
        "cache_key": result.get("cache_key"),
        "cache_hit": result.get("cache_hit"),
        "factor_code_hash": result.get("factor_code_hash"),
        "source_file": result.get("factor_source_file"),
        "module_path": result.get("factor_module_path"),
        "walk_forward_status": (result.get("walk_forward") or {}).get("status"),
        "detail_available": True,
    }
    if run_id is not None:
        data["run_id"] = run_id
    if status is not None:
        data["status"] = status
    return data


def _eval_summary(result: object, *, run_id: str | None = None, status: str | None = None) -> dict:
    data = {
        "ic_mean": getattr(result, "ic_mean", None),
        "ir": getattr(result, "ir", None),
        "t_stat": getattr(result, "ic_tstat", None),
        "tstat": getattr(result, "ic_tstat", None),
        "rating": getattr(result, "rating", None),
        "monotonicity": getattr(result, "is_monotonic", None),
        "warnings": getattr(result, "warnings", []),
        "effective_params": getattr(result, "effective_params", {}),
        "cache_key": getattr(result, "cache_key", None),
        "cache_hit": getattr(result, "cache_hit", None),
        "factor_code_hash": getattr(result, "factor_code_hash", None),
        "source_file": getattr(result, "factor_source_file", None),
        "module_path": getattr(result, "factor_module_path", None),
        "detail_available": True,
    }
    if run_id is not None:
        data["run_id"] = run_id
    if status is not None:
        data["status"] = status
    return data


def _select_fields(payload: dict, fields: list[str] | None) -> dict:
    if not fields:
        return payload
    return {key: payload.get(key) for key in fields if key in payload}


def _resolve_detail(summary: bool, detail: bool | None) -> bool:
    """Resolve summary/detail tri-state output controls.

    Backward compatibility: callers that send neither flag still get the
    historical full detail payload. LLM callers can send only summary=true and
    receive compact output without also remembering detail=false.
    """
    if detail is not None:
        return detail
    return not summary


def _stored_result_meta(result: dict | None) -> dict:
    result = result or {}
    return {
        "effective_params": result.get("effective_params", {}),
        "cache_key": result.get("cache_key"),
        "cache_hit": result.get("cache_hit"),
        "factor_code_hash": result.get("factor_code_hash"),
        "source_file": result.get("factor_source_file"),
        "module_path": result.get("factor_module_path"),
    }


def _eval_meta(result: object) -> dict:
    return {
        "effective_params": getattr(result, "effective_params", {}),
        "cache_key": getattr(result, "cache_key", None),
        "cache_hit": getattr(result, "cache_hit", None),
        "factor_code_hash": getattr(result, "factor_code_hash", None),
        "source_file": getattr(result, "factor_source_file", None),
        "module_path": getattr(result, "factor_module_path", None),
    }


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[4],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=1,
        ).strip()
    except Exception:
        return os.environ.get("GIT_SHA") or os.environ.get("TINO_GIT_SHA")


def _platform_version() -> str:
    try:
        return pkg_version("tinohelm")
    except PackageNotFoundError:
        from tinohelm import __version__

        return __version__


def _api_package_path() -> str:
    import tinohelm

    return str(Path(tinohelm.__file__).resolve().parent)


# ---------------------------------------------------------------------------
# 1. GET /api/factor/list
# ---------------------------------------------------------------------------

@router.get("/list")
async def list_factors(
    include_experimental: bool = Query(
        default=False,
        description=(
            "Include factors whose underlying data source is not yet supported "
            "by DataLayer (e.g. oi_change, orderbook_imbalance_L1). Their "
            "kernels raise NotImplementedError so running them always fails."
        ),
    ),
) -> list[dict]:
    """Return all registered factor metadata.

    Each element includes at least ``name``, ``category``, ``description``,
    ``lookback``, ``version``, ``input_fields``, ``params_schema``,
    ``experimental``, and ``needs_backend``.

    Experimental factors are filtered out by default to prevent users from
    scheduling runs that are guaranteed to fail.  Pass
    ``?include_experimental=true`` to include them (e.g. when the UI wants
    to render them greyed-out).
    """
    from tinohelm.factor.registry import Registry

    registry = Registry()
    specs = registry.scan()

    items = [
        _spec_to_dict(spec)
        for spec in sorted(specs.values(), key=lambda s: s.name)
    ]
    if not include_experimental:
        items = [item for item in items if not item.get("experimental", False)]
    return items


@router.get("/capabilities")
async def factor_capabilities() -> dict:
    return {
        "segments": {
            "valid_values": list(VALID_SEGMENT_PROVIDERS),
            "error_code": "unknown_segment_provider",
        },
        "request_body_inputs": ["--body", "--body-file", "--stdin"],
        "params": {
            "normal_run_path_receives_params": True,
            "params_grid_path": True,
        },
        "result_controls": ["summary", "detail", "fields"],
    }


@router.get("/version")
async def factor_version() -> dict:
    from tinohelm.factor.registry import Registry

    registry = Registry()
    return {
        "api_version": "factor-api/v1",
        "platform_version": _platform_version(),
        "python_version": sys.version.split()[0],
        "system": platform.platform(),
        "api_package_path": _api_package_path(),
        "git_sha": _git_sha(),
        "build_time": os.environ.get("BUILD_TIME") or os.environ.get("TINO_BUILD_TIME"),
        "factor_registry_paths": {
            "user_dir": str(registry._user_dir),
            "builtins_package": registry._builtins_package,
        },
    }


# ---------------------------------------------------------------------------
# 2. GET /api/factor/universes
# ---------------------------------------------------------------------------

@router.get("/universes")
async def list_universes() -> list[str]:
    """Return available universe names (CSV stems in the research/universes dir)."""
    from tinohelm.factor.universe import Universe

    return Universe.list_universes()


# ---------------------------------------------------------------------------
# 3. GET /api/factor/symbols
# ---------------------------------------------------------------------------

@router.get("/symbols")
async def list_symbols(
    settings: Settings = Depends(get_settings_dep),
) -> list[str]:
    """Return symbols that have bar data in the NT catalog."""
    from tinohelm.data.storage import get_active_catalog_root, get_catalog_storage

    catalog_root = get_active_catalog_root(settings)
    storage = get_catalog_storage(settings=settings)
    catalog_bar_dir = catalog_root / "data" / "bar"
    symbols: set[str] = set()
    if storage.provider == "local":
        if not catalog_bar_dir.exists():
            return []
        for sub in catalog_bar_dir.iterdir():
            if sub.is_dir():
                # NT bar dir name format: SYMBOL.VENUE-N-UNIT-LAST-EXTERNAL
                name = sub.name
                dot_idx = name.find(".")
                if dot_idx > 0:
                    symbols.add(name[:dot_idx])
        return sorted(symbols)

    for obj in storage.iter_files(catalog_bar_dir, suffix=".parquet", recursive=True):
        name = obj.path.parent.name
        dot_idx = name.find(".")
        if dot_idx > 0:
            symbols.add(name[:dot_idx])

    return sorted(symbols)


# ---------------------------------------------------------------------------
# 4. POST /api/factor/explore  (synchronous quick explore)
# ---------------------------------------------------------------------------

@router.post("/explore")
async def explore_factor(req: ExploreRequest) -> dict:
    """Run a synchronous quick explore and return a simplified EvalResult.

    Returns only ``ic_mean``, ``ic_std``, ``ir``, ``rating``, and
    ``quantile_pnl`` summary — no robustness / cost analysis.
    """
    from tinohelm.factor.backend.polars_backend import PolarsBackend
    from tinohelm.factor.cache import FactorCache
    from tinohelm.factor.data_layer import DataLayer
    from tinohelm.factor.engine.orchestrator import Orchestrator
    from tinohelm.factor.evaluation.evaluator import Evaluator
    from tinohelm.factor.observer import Observer
    from tinohelm.factor.registry import Registry
    from tinohelm.factor.universe import Universe
    from tinohelm.core.config import get_settings
    import asyncio

    from tinohelm.data.storage import get_active_catalog_root

    settings = get_settings()
    catalog_path = str(get_active_catalog_root(settings))

    registry = Registry()
    registry.scan()

    if registry.get_spec(req.factor_name) is None:
        raise HTTPException(status_code=404, detail=f"Factor '{req.factor_name}' not found")

    config_dict = req.config
    try:
        config = _parse_and_validate_config(config_dict, params=req.params)
    except (KeyError, TypeError, ValueError) as exc:
        detail = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else f"Invalid config: {exc}"
        raise HTTPException(status_code=422, detail=detail)

    universe_obj = Universe.from_symbols(config.universe)
    data_layer = DataLayer(universe_obj, catalog_root=Path(catalog_path))
    backend = PolarsBackend()
    evaluator = Evaluator()
    # Cache — use settings.paths.factor_cache (no hardcoded path).
    cache = FactorCache()
    observer = Observer()

    orchestrator = Orchestrator(
        registry=registry,
        data_layer=data_layer,
        backend=backend,
        evaluator=evaluator,
        cache=cache,
        observer=observer,
    )

    try:
        result = await asyncio.to_thread(
            orchestrator.run,
            req.factor_name,
            config,
            params=req.params,
            full=False,
        )
    except Exception as exc:
        logger.warning("Factor explore failed for %s: %s", req.factor_name, exc)
        raise HTTPException(status_code=400, detail=f"Factor explore failed: {exc}")

    # Return simplified summary — no robustness/cost.
    # Chart-ready series (ic_series / quantile_cum_returns / distribution_histogram)
    # and turnover aggregates are already produced by ``Evaluator.evaluate``, so
    # exposing them here keeps /explore self-contained for the frontend panel
    # without triggering the expensive /run path.
    full_payload = {
        "factor_name": req.factor_name,
        "ic_mean": result.ic_mean,
        "ic_std": result.ic_std,
        "ir": result.ir,
        "ic_tstat": result.ic_tstat,
        "ic_positive_pct": result.ic_positive_pct,
        "ic_max_abs": result.ic_max_abs,
        "rating": result.rating,
        "quantile_pnl": result.quantile_pnl,
        "quantile_cum_returns": result.quantile_cum_returns,
        "is_monotonic": result.is_monotonic,
        "ic_series": result.ic_series,
        "ic_decay": result.ic_decay,
        "distribution_histogram": result.distribution_histogram,
        "distribution_stats": result.distribution_stats,
        "turnover": result.turnover,
        "turnover_annualized": result.turnover_annualized,
        "fee_drag_monthly": result.fee_drag_monthly,
        "half_life": result.half_life,
        "oos_ic_series": result.oos_ic_series,
        "segment_results": result.segment_results,
        "neutralization_config": result.neutralization_config,
        "effective_params": result.effective_params,
        "cache_key": result.cache_key,
        "cache_hit": result.cache_hit,
        "factor_code_hash": result.factor_code_hash,
        "factor_source_file": result.factor_source_file,
        "factor_module_path": result.factor_module_path,
        "meta": _eval_meta(result),
        "warnings": result.warnings,
    }
    detail = _resolve_detail(req.summary, req.detail)
    if req.summary and not detail:
        return {"factor_name": req.factor_name, "summary": _select_fields(_eval_summary(result), req.fields)}
    if req.summary and detail:
        return {
            "factor_name": req.factor_name,
            "summary": _select_fields(_eval_summary(result), req.fields),
            "result": _select_fields(full_payload, req.fields) if req.fields else full_payload,
        }
    if not detail:
        return {"factor_name": req.factor_name, "summary": _select_fields(_eval_summary(result), req.fields)}
    if req.fields:
        return _select_fields(full_payload, req.fields)
    return full_payload


# ---------------------------------------------------------------------------
# 5. POST /api/factor/run  (async deep diagnostic)
# ---------------------------------------------------------------------------

@router.post("/run")
async def submit_run(
    req: RunRequest,
    db: AsyncSession = Depends(get_db),
    rds: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Submit an async deep diagnostic job.

    Creates a ``FactorRun`` DB record, pushes to ``tino:factor:queue``, and
    returns ``{run_id, status: "queued"}``.
    """
    # Validate factor_name exists in registry (same guard as /explore).
    from tinohelm.factor.registry import Registry

    registry = Registry()
    registry.scan()
    if registry.get_spec(req.factor_name) is None:
        raise HTTPException(status_code=404, detail=f"Factor '{req.factor_name}' not found")

    try:
        parsed_config = _parse_and_validate_config(req.config, params=req.params)
    except (KeyError, TypeError, ValueError) as exc:
        detail = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else f"Invalid config: {exc}"
        raise HTTPException(status_code=422, detail=detail)

    run_id = str(uuid4())

    stored_config = dict(req.config)
    if req.params is not None:
        stored_config["params"] = dict(req.params)
    stored_config["_tino_run_options"] = {"full": req.full}

    neutralize = list(parsed_config.neutralize)
    run = FactorRun(
        id=run_id,
        factor_name=req.factor_name,
        status="queued",
        config=stored_config,
        progress=0,
        universe_id=parsed_config.universe_id,
        neutralization_config=(
            {"providers": list(neutralize)} if neutralize else None
        ),
    )
    db.add(run)
    await db.commit()

    payload = json.dumps({
        "run_id": run_id,
        "factor_name": req.factor_name,
        "config": stored_config,
        "params": req.params,
        "full": req.full,
    })
    await rds.lpush(QUEUE_KEY, payload)
    logger.info("Factor run %s enqueued: %s", run_id, req.factor_name)

    return {"run_id": run_id, "status": "queued"}


# ---------------------------------------------------------------------------
# 6. GET /api/factor/runs
# ---------------------------------------------------------------------------

@router.get("/runs")
async def list_runs(
    limit: int = Query(20, ge=1, le=200),
    factor_name: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List FactorRun records, optionally filtered by factor_name."""
    stmt = select(FactorRun).order_by(FactorRun.created_at.desc()).limit(limit)
    if factor_name:
        stmt = stmt.where(FactorRun.factor_name == factor_name)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "run_id": r.id,
            "factor_name": r.factor_name,
            "status": r.status,
            "progress": r.progress,
            "error": r.error,
            "created_at": (r.created_at.isoformat() + "Z") if r.created_at else None,
            "started_at": (r.started_at.isoformat() + "Z") if r.started_at else None,
            "finished_at": (r.finished_at.isoformat() + "Z") if r.finished_at else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 7. POST /api/factor/cancel/{run_id}
# ---------------------------------------------------------------------------

@router.post("/cancel/{run_id}")
async def cancel_factor_run(
    run_id: str,
    rds: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Set the cancel flag for a factor run.

    The worker re-checks the flag between every progress checkpoint; setting
    it after the job has already reached the final stage is a no-op.
    """
    cancel_key = f"{CANCEL_KEY_PREFIX}{run_id}"
    await rds.set(cancel_key, "1", ex=_CANCEL_TTL_SECONDS)
    return {"run_id": run_id, "status": "cancellation_requested"}


# ---------------------------------------------------------------------------
# 8. GET /api/factor/report/{run_id}
# ---------------------------------------------------------------------------

@router.get("/report/{run_id}")
async def get_report(
    run_id: str,
    summary: bool = Query(False),
    detail: bool | None = Query(None),
    fields: str | None = Query(None, description="Comma-separated result/summary fields to keep"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the full EvalResult for a completed FactorRun; 404 if not found."""
    run = (await db.execute(
        select(FactorRun).where(FactorRun.id == run_id)
    )).scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail="FactorRun not found")

    requested_fields = (
        [item.strip() for item in fields.split(",") if item.strip()]
        if fields
        else None
    )
    return _report_payload(
        run_id=run_id,
        factor_name=run.factor_name,
        status=run.status,
        progress=run.progress,
        error=run.error,
        result=run.result,
        summary=summary,
        detail=_resolve_detail(summary, detail),
        fields=requested_fields,
    )


# ---------------------------------------------------------------------------
# 8. POST /api/factor/create
# ---------------------------------------------------------------------------

@router.post("/create")
async def create_factor(req: CreateRequest) -> dict:
    """Generate a ``@factor`` decorated template file under the configured
    user-factors directory (``settings.paths.research / "factors"`` —
    see ``tinohelm.core.paths.PathRegistry`` field ``"factors_dir"``).

    Raises 400 for invalid names, 409 if the file already exists.
    """
    from tinohelm.core.paths import paths

    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Factor name is required")

    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
        raise HTTPException(
            status_code=400,
            detail="Name must be a valid Python identifier (letters, digits, underscores; start with letter or underscore)",
        )

    factors_dir = paths.get("factors_dir")
    factors_dir.mkdir(parents=True, exist_ok=True)
    target = factors_dir / f"{name}.py"

    if target.exists():
        raise HTTPException(status_code=409, detail=f"Factor '{name}' already exists at {target}")

    category = req.category or "自定义"
    content = f'''"""Custom factor: {name}"""
from __future__ import annotations

from tinohelm.factor.decorator import factor
from tinohelm.factor.types import Panel


@factor(category="{category}", lookback=20, params={{"n": 20}})
def {name}(close: Panel, params=None) -> Panel:
    """Custom factor: {name}"""
    n = (params or {{}}).get("n", 20)
    return close.pct_change(n)
'''

    target.write_text(content, encoding="utf-8")
    logger.info("Created factor template: %s", target)

    return {"name": name, "path": str(target)}


# ---------------------------------------------------------------------------
# 9. POST /api/factor/params_grid  (synchronous grid search)
# ---------------------------------------------------------------------------

@router.post("/params_grid")
async def params_grid_endpoint(req: ParamsGridRequest) -> dict:
    """Run a synchronous params-grid search and return up to ``top_k`` candidates.

    Evaluates every Cartesian-product combination of ``grid`` values in parallel
    (joblib loky backend), ranks by IR descending, filters correlated candidates,
    and returns up to ``top_k`` results.

    Example request body::

        {
          "factor_name": "ret_N",
          "grid": {"n": [5, 10, 20]},
          "top_k": 3,
          "corr_filter": 0.7,
          "forward_periods": 1,
          "start": "2026-01-01",
          "end": "2026-04-01"
        }

    Response body::

        {
          "factor_name": "ret_N",
          "candidates": [
            {"params": {"n": 10}, "ic_mean": 0.05, "ir": 0.8},
            ...
          ]
        }
    """
    import asyncio

    from tinohelm.factor.data_layer import DataLayer
    from tinohelm.factor.evaluation.params_grid import params_grid
    from tinohelm.factor.registry import Registry
    from tinohelm.factor.types import EvalConfig
    from tinohelm.factor.universe import Universe
    from tinohelm.core.config import get_settings

    from tinohelm.data.storage import get_active_catalog_root

    settings = get_settings()
    catalog_path = str(get_active_catalog_root(settings))

    registry = Registry()
    registry.scan()
    spec = registry.get_spec(req.factor_name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Factor '{req.factor_name}' not found")

    universe_symbols = tuple(req.universe or [])
    config = EvalConfig(
        universe=universe_symbols,
        start=req.start,
        end=req.end,
        forward_period=req.forward_periods,
    )

    if not universe_symbols:
        raise HTTPException(
            status_code=422,
            detail="universe must be non-empty for params_grid search",
        )

    universe_obj = Universe.from_symbols(universe_symbols)
    data_layer = DataLayer(universe_obj, catalog_root=Path(catalog_path))

    # Build DataRequest objects from the factor spec's input_specs.
    # Uses the same helper as the Orchestrator so frequency resolution is
    # consistent: default to "1m" when the spec does not mandate a frequency.
    from tinohelm.factor.engine.orchestrator import _build_data_requests
    from tinohelm.factor.types import DataRequest

    _DEFAULT_INTERVAL = "1m"
    data_requests = _build_data_requests([spec], universe_symbols, _DEFAULT_INTERVAL)
    if not data_requests:
        # Fallback: spec has no input_specs — request the close field directly.
        data_requests = [
            DataRequest(
                symbol=sym,
                field_name="close",
                frequency=_DEFAULT_INTERVAL,
                lookback=spec.lookback,
            )
            for sym in universe_symbols
        ]
    elif not any(r.field_name == "close" and r.source == "bar" for r in data_requests):
        # Factor inputs are not necessarily prices (volume/funding/market_cap),
        # but IC/forward-return evaluation must always be against close returns.
        # Load close as an eval-only panel without passing it to the kernel
        # unless the factor explicitly declares it.
        data_requests.extend(
            DataRequest(
                symbol=sym,
                field_name="close",
                frequency=_DEFAULT_INTERVAL,
                lookback=0,
                source="bar",
            )
            for sym in universe_symbols
        )

    # Load data once; the resulting panels are shared by all grid combinations.
    try:
        raw_panels = await asyncio.to_thread(
            data_layer.load,
            data_requests,
            start=req.start,
            end=req.end,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Data not found: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Data validation error: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Data load failed: {exc}")

    # Extract the close panel used strictly as the forward-return target.
    close_panel = raw_panels.get("close")
    if close_panel is None:
        raise HTTPException(
            status_code=400,
            detail="Params grid requires a close price panel for forward returns",
        )

    # Build kernel callable for the factor.
    from tinohelm.factor.evaluation.ic import forward_returns as _fwd_returns
    from tinohelm.factor.evaluation.evaluator import _to_ts_value

    # Derive forward-return panel from the close panel.
    try:
        close_flat = _to_ts_value(close_panel)
        fwd_df = _fwd_returns(close_flat, config.forward_period)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Forward-return computation failed: {exc}")

    # Wrap kernel to match params_grid's factor_fn signature.
    kernel = registry.get_kernel(req.factor_name)

    declared_input_specs = list(spec.input_specs or [])
    input_kwargs = {
        input_spec.field_name: raw_panels[input_spec.field_name]
        for input_spec in declared_input_specs
        if input_spec.field_name in raw_panels
    }
    missing_inputs = [
        input_spec.field_name
        for input_spec in declared_input_specs
        if input_spec.field_name not in input_kwargs
    ]
    if missing_inputs:
        raise HTTPException(
            status_code=400,
            detail=f"Missing factor inputs for params_grid: {missing_inputs}",
        )
    if not declared_input_specs:
        input_kwargs = {"close": close_panel}

    def _factor_fn(**params_kw: object) -> object:
        merged_params = dict(spec.params)
        merged_params.update(params_kw)
        return kernel(**input_kwargs, params=merged_params)

    try:
        candidates = await asyncio.to_thread(
            params_grid,
            _factor_fn,
            {},
            req.grid,
            fwd_df,
            top_k=req.top_k,
            corr_filter=req.corr_filter,
            n_jobs=req.n_jobs,
            eval_config=config,
        )
    except Exception as exc:
        logger.warning("params_grid failed for %s: %s", req.factor_name, exc)
        raise HTTPException(status_code=400, detail=f"Params grid search failed: {exc}")

    return {
        "factor_name": req.factor_name,
        "candidates": [
            {
                "params": c["params"],
                "ic_mean": c["ic_mean"],
                "ir": c["ir"],
            }
            for c in candidates
        ],
    }


# ---------------------------------------------------------------------------
# Internal helper — load EvalResult from a FactorRun DB record
# ---------------------------------------------------------------------------

async def _load_eval_result_from_db(
    run_id: str,
    db: AsyncSession,
) -> EvalResult:
    """Load a :class:`~tinohelm.factor.types.EvalResult` from a completed FactorRun.

    Raises :exc:`fastapi.HTTPException` (404) if the run does not exist, and
    (400) if the run has not completed or has no result stored.
    """
    from tinohelm.factor.types import EvalResult as _EvalResult

    row = (
        await db.execute(select(FactorRun).where(FactorRun.id == run_id))
    ).scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail=f"FactorRun '{run_id}' not found")
    if row.status != "completed" or row.result is None:
        raise HTTPException(
            status_code=400,
            detail=f"FactorRun '{run_id}' is not completed (status={row.status!r})",
        )

    result_dict: dict = row.result  # JSONB — already a plain dict from SQLAlchemy
    # Reconstruct EvalResult from stored dict.  Only known scalar fields are
    # mapped; unknown keys are ignored to stay forward-compatible.
    field_defaults = {f.name: f.default for f in dataclasses.fields(_EvalResult)}
    kwargs: dict = {}
    for fname in field_defaults:
        if fname in result_dict:
            kwargs[fname] = result_dict[fname]
    return _EvalResult(**kwargs)


# ---------------------------------------------------------------------------
# 10. POST /api/factor/compare  (pairwise bootstrap CI)
# ---------------------------------------------------------------------------

@router.post("/compare")
async def compare_endpoint(
    req: CompareRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Compare two FactorRun results with bootstrap CI on metric differences.

    Loads both FactorRuns from the DB, extracts their IC series, and runs
    ``n_bootstrap`` iterations of paired resampling to estimate the CI on
    ``ic_mean`` and ``ir`` differences.

    Returns ``{"metric_diffs": [{name, a, b, delta, ci_low, ci_high,
    significant}, ...]}``.
    """
    from tinohelm.factor.evaluation.compare import compare_results

    eval_a = await _load_eval_result_from_db(req.eval_a_run_id, db)
    eval_b = await _load_eval_result_from_db(req.eval_b_run_id, db)

    import asyncio

    result = await asyncio.to_thread(
        compare_results,
        eval_a,
        eval_b,
        n_bootstrap=req.n_bootstrap,
        confidence=req.confidence,
    )
    return result


# ---------------------------------------------------------------------------
# 11. POST /api/factor/compare/multi  (multi-factor report)
# ---------------------------------------------------------------------------

@router.post("/compare/multi")
async def compare_multi_endpoint(
    req: CompareMultiRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Multi-factor comparison report.

    Accepts a list of ``eval_run_ids`` (at least 2) and returns a report
    containing:

    * ``ranking_heatmap`` — factor × metric table with 1-based rankings
    * ``rolling_ic_small_multiples`` — 30-bar rolling mean IC per factor
    * ``dendrogram`` — hierarchical cluster linkage matrix (Ward, correlation
      distance) from ``tinohelm.factor.evaluation.clustering``
    * ``ic_time_series_corr`` — pairwise Pearson correlation across IC series
    * ``agent_summary`` — plain-English summary of the comparison
    """
    from tinohelm.factor.evaluation.compare import compare_multi

    if len(req.eval_run_ids) < 2:
        raise HTTPException(
            status_code=422,
            detail="compare/multi requires at least 2 eval_run_ids",
        )

    # Load all EvalResults — preserve insertion order as factor names.
    results: dict = {}
    for run_id in req.eval_run_ids:
        eval_result = await _load_eval_result_from_db(run_id, db)
        # Use run_id as the factor label if we cannot get factor_name more
        # cheaply; factor_name is retrieved from the DB row if needed.
        row = (
            await db.execute(select(FactorRun).where(FactorRun.id == run_id))
        ).scalar_one_or_none()
        label = row.factor_name if row else run_id
        # Deduplicate labels: if the same factor appears twice, append run_id suffix.
        if label in results:
            label = f"{label}:{run_id[:8]}"
        results[label] = eval_result

    import asyncio

    report = await asyncio.to_thread(
        compare_multi,
        results,
        n_bootstrap=req.n_bootstrap,
    )
    return report
