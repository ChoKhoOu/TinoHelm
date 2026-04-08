"""Factor research API routes."""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.api.deps import get_db, get_redis, get_settings_dep
from tinohelm.core.config import Settings
from tinohelm.db.models import ResearchJob
from tinohelm.research.worker import enqueue_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research", tags=["research"])

def _research_reports_dir() -> Path:
    from tinohelm.core.config import get_settings
    return get_settings().paths.research / "reports"


# ---- request / response schemas ----

class ExploreRequest(BaseModel):
    """Request body for POST /explore."""

    symbol: str
    data_type: str = "bar"
    interval: str = "1m"
    start_date: date | None = None
    end_date: date | None = None
    factors: list[str]
    factor_params: dict[str, dict] | None = None
    forward_period: int = 5
    quantiles: int = 5
    return_type: str = "simple"  # simple | log


class DiagnoseRequest(BaseModel):
    """Request body for POST /diagnose."""

    symbol: str
    data_type: str = "bar"
    interval: str = "1m"
    start_date: date
    end_date: date
    factor_name: str
    factor_params: dict | None = None
    forward_period: int = 5
    quantiles: int = 5
    return_type: str = "simple"


# ---- endpoints ----

@router.get("/factors")
async def list_factors() -> list[dict]:
    """List all available factors grouped by category (matches frontend FactorGroup[])."""
    from tinohelm.research.registry import get_all_factors

    factors = get_all_factors()

    # Group by category → [{group, factors: [{name, params}]}]
    groups: dict[str, list[dict]] = {}
    for name, meta in factors.items():
        cat = meta.get("category", "其他")
        if cat not in groups:
            groups[cat] = []
        # Build params list matching frontend FactorDef.params
        params_list = []
        for pkey, pdef in meta.get("params", {}).items():
            params_list.append({
                "key": pkey,
                "label": pdef.get("label", pkey),
                "default": pdef.get("default", 0),
                **({"tip": pdef["tip"]} if "tip" in pdef else {}),
            })
        groups[cat].append({"name": name, "params": params_list if params_list else None})

    return [{"group": g, "factors": fs} for g, fs in groups.items()]


@router.get("/symbols")
async def list_symbols(
    settings: Settings = Depends(get_settings_dep),
) -> list[dict]:
    """List symbols that have bar data in the catalog (matches frontend SymbolOption[])."""
    catalog_bar_dir = settings.paths.catalog / "data" / "bar"
    if not catalog_bar_dir.exists():
        return []

    symbols = set()
    for sub in catalog_bar_dir.iterdir():
        if sub.is_dir():
            # NT bar dir name: SYMBOL.VENUE-N-UNIT-LAST-EXTERNAL
            name = sub.name
            dot_idx = name.find(".")
            if dot_idx > 0:
                symbols.add(name[:dot_idx])

    return [{"symbol": s} for s in sorted(symbols)]


@router.post("/explore")
async def run_explore(
    req: ExploreRequest,
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Run synchronous quick explore analysis for selected factors.

    Returns flattened ExploreResult matching frontend shape:
      factors[], ic_timeseries[], ic_decay[], quantile_returns[], distribution[], turnover{}
    """
    from tinohelm.research.analysis import run_explore as _run_explore
    from tinohelm.research.factors import compute_factor
    from tinohelm.research.loader import load_bars

    # Load bar data
    try:
        df = load_bars(
            symbol=req.symbol,
            interval=req.interval,
            start=req.start_date.isoformat() if req.start_date else None,
            end=req.end_date.isoformat() if req.end_date else None,
            catalog_path=str(settings.paths.catalog),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if len(df) < 50:
        raise HTTPException(status_code=400, detail=f"Insufficient data: {len(df)} bars (need >= 50)")

    if "close" not in df.columns:
        raise HTTPException(status_code=400, detail="Bar data missing 'close' column")

    log_ret = req.return_type == "log"

    # Collect per-factor results
    per_factor: dict[str, dict] = {}
    for factor_name in req.factors:
        try:
            params = (req.factor_params or {}).get(factor_name, {})
            # Fix #4: use compute_factor() which correctly passes params as dict
            factor_series = compute_factor(factor_name, df, params)
            result = _run_explore(
                factor=factor_series,
                close=df["close"],
                forward_period=req.forward_period,
                n_quantiles=req.quantiles,
                log_ret=log_ret,
            )
            per_factor[factor_name] = result
        except Exception as exc:
            logger.warning("Factor %s explore failed: %s", factor_name, exc)

    if not per_factor:
        raise HTTPException(status_code=400, detail="All factors failed to compute")

    # Aggregate into frontend ExploreResult shape
    # 1. factors[] — summary table
    factors_list = []
    rating_map = {3: "strong", 2: "usable", 1: "weak", 0: "weak"}
    for name, r in per_factor.items():
        s = r.get("summary", {})
        factors_list.append({
            "name": name,
            "ic_mean": s.get("ic_mean", 0),
            "ic_std": s.get("ic_std", 0),
            "ir": s.get("ir", 0),
            "ic_positive_pct": round(s.get("ic_positive_pct", 0) * 100, 1),
            "rating": rating_map.get(s.get("rating", 0), "weak"),
        })

    # 2. ic_timeseries[] — merge all factors' IC series into [{date, factor1, factor2, ...}]
    ic_by_date: dict[str, dict] = {}
    for name, r in per_factor.items():
        for row in r.get("ic_series", []):
            d = row.get("date", "")
            if d not in ic_by_date:
                ic_by_date[d] = {"date": d}
            ic_by_date[d][name] = row.get("ic", 0)
    ic_timeseries = sorted(ic_by_date.values(), key=lambda x: x["date"])

    # 3. ic_decay[] — use first factor's decay (representative)
    first_factor = next(iter(per_factor.values()))
    ic_decay = first_factor.get("ic_decay", [])

    # 4. quantile_returns[] — use first factor's quantile cum returns
    qr = first_factor.get("quantile_returns", {})
    cum_rets = qr.get("cum_returns", {})
    # Merge Q1..QN into [{date, Q1, Q2, ...}]
    qr_by_date: dict[str, dict] = {}
    for qlabel, points in cum_rets.items():
        for pt in points:
            d = pt.get("date", "")
            if d not in qr_by_date:
                qr_by_date[d] = {"date": d}
            qr_by_date[d][qlabel] = round(pt.get("cum_ret", 0) * 100, 2)
    quantile_returns = sorted(qr_by_date.values(), key=lambda x: x["date"])

    # 5. distribution[] — use first factor's histogram
    dist = first_factor.get("distribution", {})
    distribution = [
        {"bin": f"{h['bin_start']:.4f}", "count": h["count"]}
        for h in dist.get("histogram", [])
    ]

    # 6. turnover{} — use first factor's turnover
    to = first_factor.get("turnover", {})
    turnover = {
        "daily_avg": f"{to.get('daily', 0):.1%}",
        "annual": f"{to.get('annualized', 0):.0f}x",
        "fee_drag": f"-{abs(to.get('fee_drag_monthly', 0)):.2%}/mo",
        "fee_rate": "0.04%",
    }

    return {
        "factors": factors_list,
        "ic_timeseries": ic_timeseries,
        "ic_decay": ic_decay,
        "quantile_returns": quantile_returns,
        "distribution": distribution,
        "turnover": turnover,
    }


@router.post("/diagnose")
async def submit_diagnose(
    req: DiagnoseRequest,
    db: AsyncSession = Depends(get_db),
    rds: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Submit an async diagnose job."""
    job_id = str(uuid4())

    job = ResearchJob(
        job_id=job_id,
        factor_name=req.factor_name,
        symbol=req.symbol,
        data_type=req.data_type,
        interval=req.interval,
        start_date=req.start_date,
        end_date=req.end_date,
        parameters_json={
            "factor_params": req.factor_params,
            "forward_period": req.forward_period,
            "quantiles": req.quantiles,
            "return_type": req.return_type,
        },
        status="queued",
    )
    db.add(job)
    await db.commit()

    await enqueue_job(rds, job_id)
    logger.info("Research diagnose job %s enqueued", job_id)

    return {"job_id": job_id, "status": "queued"}


@router.get("/jobs")
async def list_jobs(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List research jobs, optionally filtered by status."""
    stmt = select(ResearchJob).order_by(ResearchJob.created_at.desc()).limit(100)
    if status:
        stmt = stmt.where(ResearchJob.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "job_id": j.job_id,
            "factor_name": j.factor_name,
            "symbol": j.symbol,
            "data_type": j.data_type,
            "interval": j.interval,
            "start_date": j.start_date.isoformat(),
            "end_date": j.end_date.isoformat(),
            "status": j.status,
            "progress": j.progress,
            "rating": j.rating,
            "error": j.error,
            "created_at": (j.created_at.isoformat() + "Z") if j.created_at else None,
            "completed_at": (j.completed_at.isoformat() + "Z") if j.completed_at else None,
        }
        for j in rows
    ]


@router.get("/report/{job_id}")
async def get_report(
    job_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get the full diagnose report JSON for a completed job."""
    job = (await db.execute(
        select(ResearchJob).where(ResearchJob.job_id == job_id)
    )).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "completed":
        return {
            "job_id": job_id,
            "status": job.status,
            "progress": job.progress,
            "error": job.error,
        }

    report_path = _research_reports_dir() / f"{job_id}.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report file not found")

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read report: {exc}")

    return {
        "job_id": job_id,
        "status": "completed",
        "rating": job.rating,
        "verdict": job.verdict_json,
        "report": report,
    }


@router.post("/factors/create")
async def create_custom_factor(
    req: dict,
) -> dict:
    """Create a new custom factor file from the template."""
    from tinohelm.research.registry import _custom_factors_dir

    name = req.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Factor name is required")

    # 合法文件名: 仅允许字母、数字、下划线
    import re
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
        raise HTTPException(status_code=400, detail="名称只允许字母、数字、下划线，且以字母或下划线开头")

    factors_dir = _custom_factors_dir()
    factors_dir.mkdir(parents=True, exist_ok=True)
    target = factors_dir / f"{name}.py"
    if target.exists():
        raise HTTPException(status_code=409, detail=f"Factor '{name}' already exists")

    # 读取打包在应用内的模板文件
    template_path = Path(__file__).resolve().parent.parent.parent / "research" / "_template.py"
    if template_path.exists():
        content = template_path.read_text(encoding="utf-8")
        content = content.replace('"_template"', f'"{name}"')
        content = content.replace('"模板因子"', f'"{name}"')
    else:
        logger.warning("Bundled _template.py not found at %s, using minimal fallback", template_path)
        content = f'''"""自定义因子: {name}"""
from __future__ import annotations

import numpy as np
import pandas as pd

FACTOR_META = {{
    "name": "{name}",
    "label": "{name}",
    "category": "自定义",
    "data_type": "bar",
    "params": {{
        "lookback": {{"default": 20, "min": 5, "max": 200, "label": "回看周期"}},
    }},
}}


def compute(df: pd.DataFrame, params: dict) -> pd.Series:
    lookback = params.get("lookback", 20)
    return df["close"].pct_change(lookback)
'''

    target.write_text(content, encoding="utf-8")
    logger.info("Created custom factor: %s", target)

    return {"name": name, "path": str(target)}


@router.post("/deploy")
async def deploy_strategy() -> dict:
    """Generate a strategy file from research results (not yet implemented)."""
    raise HTTPException(status_code=501, detail="Strategy deployment not yet implemented")
