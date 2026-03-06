"""Backtest API routes."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from tinohelm.api.deps import get_db, get_redis, get_settings_dep
from tinohelm.core.audit import log_audit
from tinohelm.core.config import Settings
from tinohelm.db.models import BacktestRun, RunStatus, Strategy, StrategyVersion

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


# ---- request / response schemas ----

class BacktestRunRequest(BaseModel):
    """Request body for POST /run."""

    strategy: str
    # Backward compat: single values
    symbol: str | None = None
    interval: str | None = None
    # New: multiple values
    symbols: list[str] | None = None
    intervals: list[str] | None = None
    # Existing
    start_date: date
    end_date: date
    initial_capital: float = 10000
    leverage: float = 1
    params: dict | None = None
    # New: fill model config
    fill_model: dict | None = None

    @model_validator(mode="after")
    def _normalise_symbols_intervals(self) -> "BacktestRunRequest":
        if self.symbols is None:
            if self.symbol is not None:
                self.symbols = [self.symbol]
            else:
                raise ValueError("Either 'symbols' or 'symbol' must be provided")
        if self.intervals is None:
            if self.interval is not None:
                self.intervals = [self.interval]
            else:
                self.intervals = ["1m"]
        return self


class BacktestRunResponse(BaseModel):
    """Response body for POST /run."""

    run_id: str
    status: str


class BacktestRunItem(BaseModel):
    """Single item in the runs list."""

    run_id: str
    strategy_name: str | None = None
    symbol: str
    interval: str
    start_date: date
    end_date: date
    status: str
    created_at: str | None = None
    completed_at: str | None = None
    result_summary: dict | None = None


class BacktestRunList(BaseModel):
    """Wrapper for paginated runs list."""

    runs: list[BacktestRunItem]
    total: int


class BacktestRunStatus(BaseModel):
    """Status response for a single run."""

    run_id: str
    status: str
    error: str | None = None
    progress_pct: int | None = None
    result: dict | None = None


class BacktestCancelResponse(BaseModel):
    """Response body for POST /{run_id}/cancel."""

    run_id: str
    status: str


# ---- routes ----

@router.post("/run", response_model=BacktestRunResponse)
async def create_backtest_run(
    body: BacktestRunRequest,
    db: AsyncSession = Depends(get_db),
    rds: aioredis.Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings_dep),
) -> BacktestRunResponse:
    """Validate strategy, create a BacktestRun record, and enqueue the job."""
    # Check strategy exists
    stmt = select(Strategy).where(Strategy.name == body.strategy)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{body.strategy}' not found")

    # Look up latest StrategyVersion for strategy_version_id
    sv_stmt = (
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == row.id)
        .order_by(StrategyVersion.version.desc())
        .limit(1)
    )
    latest_version = (await db.execute(sv_stmt)).scalar_one_or_none()
    strategy_version_id = latest_version.id if latest_version else None

    run_id = str(uuid4())
    run = BacktestRun(
        run_id=run_id,
        strategy_name=body.strategy,
        strategy_id=row.id,
        strategy_version_id=strategy_version_id,
        symbol=",".join(body.symbols),
        interval=",".join(body.intervals),
        start_date=body.start_date,
        end_date=body.end_date,
        params_json=body.params,
        status=RunStatus.queued,
    )
    db.add(run)
    await db.flush()
    await db.commit()

    # Push job to Redis queue — include all fields the worker expects
    job_payload = json.dumps({
        "run_id": run_id,
        "strategy_path": f"{row.file_path}:{row.strategy_class}",
        "config_path": f"{row.file_path}:{row.config_class}",
        "strategy_name": body.strategy,
        "symbols": body.symbols,
        "intervals": body.intervals,
        "start": body.start_date.isoformat(),
        "end": body.end_date.isoformat(),
        "params": {
            **(body.params or {}),
            "starting_balance": body.initial_capital,
            "leverage": body.leverage,
        },
        "fill_model": body.fill_model,
    })
    await rds.lpush("tino:backtest:queue", job_payload)

    await log_audit(db, "backtest.queued", {"run_id": run_id, "strategy": body.strategy})
    logger.info("Backtest run queued: %s", run_id)

    return BacktestRunResponse(run_id=run_id, status=RunStatus.queued.value)


@router.get("/runs", response_model=BacktestRunList)
async def list_backtest_runs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = 0,
    strategy: str | None = None,
    status: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    db: AsyncSession = Depends(get_db),
) -> BacktestRunList:
    """List backtest runs with pagination and optional filters."""
    # Base filter conditions (uses strategy_name column, no FK join needed)
    base_stmt = select(BacktestRun)
    count_stmt = select(sa_func.count(BacktestRun.id))
    if strategy:
        base_stmt = base_stmt.where(BacktestRun.strategy_name == strategy)
        count_stmt = count_stmt.where(BacktestRun.strategy_name == strategy)
    if status:
        base_stmt = base_stmt.where(BacktestRun.status == status)
        count_stmt = count_stmt.where(BacktestRun.status == status)
    if start_date:
        base_stmt = base_stmt.where(BacktestRun.start_date >= start_date)
        count_stmt = count_stmt.where(BacktestRun.start_date >= start_date)
    if end_date:
        base_stmt = base_stmt.where(BacktestRun.end_date <= end_date)
        count_stmt = count_stmt.where(BacktestRun.end_date <= end_date)

    total = (await db.execute(count_stmt)).scalar() or 0

    base_stmt = base_stmt.order_by(BacktestRun.created_at.desc()).offset(offset).limit(limit)
    rows = (await db.execute(base_stmt)).scalars().all()

    runs = [
        BacktestRunItem(
            run_id=r.run_id,
            strategy_name=r.strategy_name,
            symbol=r.symbol,
            interval=r.interval,
            start_date=r.start_date,
            end_date=r.end_date,
            status=r.status.value,
            created_at=r.created_at.isoformat() if r.created_at else None,
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
            result_summary=r.result_summary_json,
        )
        for r in rows
    ]
    return BacktestRunList(runs=runs, total=total)


@router.get("/{run_id}/status", response_model=BacktestRunStatus)
async def get_backtest_status(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    rds: aioredis.Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings_dep),
) -> BacktestRunStatus:
    """Get the status of a backtest run."""
    stmt = select(BacktestRun).where(BacktestRun.run_id == run_id)
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")

    # Read progress percentage from Redis
    progress_pct: int | None = None
    progress_raw = await rds.get(f"tino:backtest:progress:{run_id}")
    if progress_raw is not None:
        try:
            progress_pct = int(progress_raw)
        except (ValueError, TypeError):
            pass

    # Include result when completed
    result: dict | None = None
    if run.status == RunStatus.completed:
        # Validate run_id format to prevent path traversal
        if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', run_id):
            artifact_path = (Path(settings.paths.artifacts) / run_id / "results.json").resolve()
            artifacts_root = Path(settings.paths.artifacts).resolve()
            if str(artifact_path).startswith(str(artifacts_root)) and artifact_path.exists():
                try:
                    content = await asyncio.to_thread(artifact_path.read_text)
                    result = json.loads(content)
                except Exception:
                    logger.warning("Failed to load artifact for run %s", run_id, exc_info=True)

    return BacktestRunStatus(
        run_id=run.run_id,
        status=run.status.value,
        error=run.error,
        progress_pct=progress_pct,
        result=result,
    )


@router.get("/{run_id}/result")
async def get_backtest_result(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Get the full result from the artifact file."""
    # Validate run_id is a UUID to prevent path traversal
    if not re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', run_id):
        raise HTTPException(status_code=400, detail="Invalid run_id format")

    stmt = select(BacktestRun).where(BacktestRun.run_id == run_id)
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")

    artifact_path = (Path(settings.paths.artifacts) / run_id / "results.json").resolve()
    artifacts_root = Path(settings.paths.artifacts).resolve()
    if not str(artifact_path).startswith(str(artifacts_root)):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail="Artifact file not found (run may still be in progress)")

    content = await asyncio.to_thread(artifact_path.read_text)
    return json.loads(content)


@router.post("/{run_id}/cancel", response_model=BacktestCancelResponse)
async def cancel_backtest_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    rds: aioredis.Redis = Depends(get_redis),
) -> BacktestCancelResponse:
    """Cancel a queued or running backtest run."""
    stmt = select(BacktestRun).where(BacktestRun.run_id == run_id)
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")

    if run.status not in (RunStatus.queued, RunStatus.running):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel run in '{run.status.value}' state",
        )

    # Set Redis cancel key so the worker picks it up
    await rds.set(f"tino:backtest:cancel:{run_id}", "1", ex=86400)

    return BacktestCancelResponse(run_id=run_id, status="cancelling")
