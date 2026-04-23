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
import re
from datetime import datetime
from pathlib import Path
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

router = APIRouter(prefix="/api/factor", tags=["factor"])

QUEUE_KEY = "tino:factor:queue"

_DEFAULT_FACTORS_DIR = Path.home() / ".tino" / "research" / "factors"


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class ExploreRequest(BaseModel):
    """Request body for POST /api/factor/explore (synchronous quick explore)."""

    factor_name: str
    config: dict
    params: dict | None = None


class RunRequest(BaseModel):
    """Request body for POST /api/factor/run (async deep diagnostic)."""

    factor_name: str
    config: dict
    params: dict | None = None
    full: bool = False


class CreateRequest(BaseModel):
    """Request body for POST /api/factor/create."""

    name: str
    category: str = "自定义"
    template: str | None = None


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
    d.pop("code_hash", None)
    return d


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
    catalog_bar_dir = settings.paths.catalog / "data" / "bar"
    if not catalog_bar_dir.exists():
        return []

    symbols: set[str] = set()
    for sub in catalog_bar_dir.iterdir():
        if sub.is_dir():
            # NT bar dir name format: SYMBOL.VENUE-N-UNIT-LAST-EXTERNAL
            name = sub.name
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
    import asyncio

    settings = get_settings()
    catalog_path = str(settings.paths.catalog)

    registry = Registry()
    registry.scan()

    if registry.get_spec(req.factor_name) is None:
        raise HTTPException(status_code=404, detail=f"Factor '{req.factor_name}' not found")

    config_dict = req.config
    try:
        config = EvalConfig(
            universe=tuple(config_dict.get("universe", [])),
            start=config_dict["start"],
            end=config_dict["end"],
            forward_period=config_dict.get("forward_period", 5),
            quantiles=config_dict.get("quantiles", 5),
            cost_bps=config_dict.get("cost_bps", 4.0),
            ic_freq=config_dict.get("ic_freq", "D"),
            log_ret=config_dict.get("log_ret", False),
            params=req.params or config_dict.get("params", {}),
        )
    except (KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid config: {exc}")

    universe_obj = Universe.from_symbols(config.universe)
    data_layer = DataLayer(universe_obj, catalog_root=Path(catalog_path))
    backend = PandasBackend()
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
    return {
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
    }


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

    run_id = str(uuid4())

    run = FactorRun(
        id=run_id,
        factor_name=req.factor_name,
        status="queued",
        config=req.config,
        progress=0,
    )
    db.add(run)
    await db.commit()

    payload = json.dumps({
        "run_id": run_id,
        "factor_name": req.factor_name,
        "config": req.config,
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
# 7. GET /api/factor/report/{run_id}
# ---------------------------------------------------------------------------

@router.get("/report/{run_id}")
async def get_report(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the full EvalResult for a completed FactorRun; 404 if not found."""
    run = (await db.execute(
        select(FactorRun).where(FactorRun.id == run_id)
    )).scalar_one_or_none()

    if run is None:
        raise HTTPException(status_code=404, detail="FactorRun not found")

    if run.status != "completed":
        return {
            "run_id": run_id,
            "factor_name": run.factor_name,
            "status": run.status,
            "progress": run.progress,
            "error": run.error,
        }

    return {
        "run_id": run_id,
        "factor_name": run.factor_name,
        "status": "completed",
        "result": run.result,
    }


# ---------------------------------------------------------------------------
# 8. POST /api/factor/create
# ---------------------------------------------------------------------------

@router.post("/create")
async def create_factor(req: CreateRequest) -> dict:
    """Generate a ``@factor`` decorated template file under ~/.tino/research/factors/.

    Raises 400 for invalid names, 409 if the file already exists.
    """
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Factor name is required")

    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
        raise HTTPException(
            status_code=400,
            detail="Name must be a valid Python identifier (letters, digits, underscores; start with letter or underscore)",
        )

    factors_dir = _DEFAULT_FACTORS_DIR
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
