"""Optimization API routes."""
from __future__ import annotations

import logging
import multiprocessing
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.api.deps import get_db, get_settings_dep
from tinohelm.core.audit import log_audit
from tinohelm.core.config import Settings
from tinohelm.data.storage import get_active_catalog_root
from tinohelm.db.models import OptimizationRun, OptimizationStatus, Strategy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest/optimize", tags=["optimize"])

# Track running optimizer processes for cleanup
_optimizer_processes: dict[int, multiprocessing.Process] = {}


def cleanup_optimizer_processes() -> None:
    """Clean up running optimizer processes on shutdown."""
    for key, proc in list(_optimizer_processes.items()):
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
        del _optimizer_processes[key]


# ---- request / response schemas ----

class ParamRangeSpec(BaseModel):
    """Validated parameter range for optimization."""

    type: Literal["int", "float"] = "float"
    min: float
    max: float
    step: float | None = None


class OptimizeRequest(BaseModel):
    """Request body for POST /api/backtest/optimize."""

    strategy: str
    symbol: str
    interval: str
    start_date: date
    end_date: date
    initial_capital: float | None = None
    leverage: float | None = None
    n_trials: int = Field(default=0, ge=0, le=10000)
    fitness_objective: str = Field(pattern=r"^(sharpe|calmar|sortino|profit)$")
    train_pct: float = Field(default=85.0, ge=50.0, le=99.0)
    param_ranges: dict[str, ParamRangeSpec] | None = None
    n_workers: int = Field(default=0, ge=0, le=16, description="Parallel trial workers (0=auto)")
    walk_forward_folds: int = Field(default=0, ge=0, le=20, description="Walk-forward folds (0=disabled)")
    pruning: bool = Field(default=True, description="Enable Optuna trial pruning")
    sampler: str = Field(default="auto", pattern=r"^(auto|tpe|cmaes|random)$", description="Optuna sampler (auto=smart select)")
    patience: int = Field(default=0, ge=0, le=1000, description="Early stopping patience (0=disabled)")


class OptimizeStartResponse(BaseModel):
    """Response body for POST /api/backtest/optimize."""

    optimization_id: int
    status: str


class OptimizeStatusResponse(BaseModel):
    """Response body for GET /status."""

    optimization_id: int
    status: str
    trials_completed: int
    total_trials: int
    best_params: dict | None = None
    best_value: float | None = None
    pruned_trials: int = 0


class OptimizeRunItem(BaseModel):
    """Single item in the runs list."""

    optimization_id: int
    strategy_name: str | None = None
    symbol: str
    interval: str
    start_date: date
    end_date: date
    n_trials: int
    fitness_objective: str
    status: str
    best_value: float | None = None
    trials_completed: int
    created_at: str | None = None
    completed_at: str | None = None


# ---- helpers ----

def _discover_optimize_ranges(strategy_row: Strategy) -> dict[str, dict[str, Any]]:
    """Load optimize ranges from strategy's OPTIMIZE dict."""
    from pathlib import Path as P

    from tinohelm.strategy.module_loader import load_strategy_module

    file_path = P(strategy_row.file_path)

    if file_path.suffix == ".py" and file_path.exists():
        try:
            result = load_strategy_module(file_path)
            return result.optimize_ranges
        except Exception as e:
            logger.warning("Failed to load OPTIMIZE from %s: %s", file_path, e)

    return {}


# ---- routes ----

@router.post("", response_model=OptimizeStartResponse)
async def start_optimization(
    body: OptimizeRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> OptimizeStartResponse:
    """Start a hyperparameter optimization run."""
    # Validate strategy exists
    stmt = select(Strategy).where(Strategy.name == body.strategy)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{body.strategy}' not found")

    # Create OptimizationRun record
    opt_run = OptimizationRun(
        strategy_id=row.id,
        symbol=body.symbol,
        interval=body.interval,
        start_date=body.start_date,
        end_date=body.end_date,
        n_trials=body.n_trials,
        fitness_objective=body.fitness_objective,
        train_pct=body.train_pct,
        status=OptimizationStatus.running,
    )
    db.add(opt_run)
    await db.flush()
    await db.commit()
    await db.refresh(opt_run)

    optimization_id = opt_run.id

    # Build strategy params for the optimizer
    strategy_params: dict[str, Any] = {}
    if body.initial_capital is not None:
        strategy_params["starting_balance"] = body.initial_capital
    if body.leverage is not None:
        strategy_params["leverage"] = body.leverage

    # Auto-discover param_ranges if not provided
    if body.param_ranges:
        param_ranges_dict: dict[str, Any] | None = {k: v.model_dump() for k, v in body.param_ranges.items()}
    else:
        discovered = _discover_optimize_ranges(row)
        if discovered:
            param_ranges_dict = discovered
            logger.info("Auto-discovered %d optimize ranges for '%s'", len(discovered), body.strategy)
        else:
            param_ranges_dict = None

    # Resolve smart defaults for n_trials/n_workers so the DB has correct values
    from tinohelm.backtest.optimizer_helpers import auto_n_trials, auto_workers

    resolved_n_trials = body.n_trials
    if resolved_n_trials <= 0:
        resolved_n_trials = auto_n_trials(param_ranges_dict or {})
    resolved_n_workers = body.n_workers
    if resolved_n_workers <= 0:
        resolved_n_workers = auto_workers()

    # Update DB record with resolved values
    opt_run.n_trials = resolved_n_trials
    await db.commit()

    # Launch optimizer in a background process
    from tinohelm.backtest.optimizer import run_optimization

    proc = multiprocessing.Process(
        target=run_optimization,
        kwargs={
            "strategy_path": f"{row.file_path}:{row.strategy_class}",
            "config_path": f"{row.file_path}:{row.config_class}",
            "symbol": body.symbol,
            "interval": body.interval,
            "start_date": body.start_date,
            "end_date": body.end_date,
            "catalog_path": str(get_active_catalog_root(settings)),
            "n_trials": resolved_n_trials,
            "fitness_objective": body.fitness_objective,
            "train_pct": body.train_pct,
            "db_url": settings.database.url,
            "redis_url": settings.redis.url,
            "optimization_id": optimization_id,
            "param_ranges": param_ranges_dict,
            "strategy_params": strategy_params,
            "n_workers": resolved_n_workers,
            "walk_forward_folds": body.walk_forward_folds,
            "pruning": body.pruning,
            "sampler": body.sampler,
            "patience": body.patience,
        },
        daemon=True,
    )
    proc.start()
    _optimizer_processes[optimization_id] = proc

    # Clean up dead processes from registry
    for pid in list(_optimizer_processes):
        if not _optimizer_processes[pid].is_alive():
            del _optimizer_processes[pid]

    await log_audit(db, "optimization.started", {
        "optimization_id": optimization_id,
        "strategy": body.strategy,
        "n_trials": body.n_trials,
    })
    logger.info("Optimization %d started for strategy '%s'", optimization_id, body.strategy)

    return OptimizeStartResponse(
        optimization_id=optimization_id,
        status=OptimizationStatus.running.value,
    )


@router.get("/{optimization_id}/status", response_model=OptimizeStatusResponse)
async def get_optimization_status(
    optimization_id: int,
    db: AsyncSession = Depends(get_db),
) -> OptimizeStatusResponse:
    """Get the progress of an optimization run."""
    stmt = select(OptimizationRun).where(OptimizationRun.id == optimization_id)
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Optimization run not found")

    result_data = run.result_json or {}
    return OptimizeStatusResponse(
        optimization_id=run.id,
        status=run.status.value,
        trials_completed=run.trials_completed,
        total_trials=run.n_trials,
        best_params=run.best_params_json,
        best_value=run.best_value,
        pruned_trials=result_data.get("total_pruned", 0),
    )


@router.get("/{optimization_id}/result")
async def get_optimization_result(
    optimization_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get the full results of a completed optimization run."""
    stmt = select(OptimizationRun).where(OptimizationRun.id == optimization_id)
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Optimization run not found")

    if run.status == OptimizationStatus.running:
        raise HTTPException(status_code=409, detail="Optimization is still running")

    if run.status == OptimizationStatus.failed:
        return {
            "optimization_id": run.id,
            "status": run.status.value,
            "error": run.error,
        }

    result_data = run.result_json or {}
    all_trials = result_data.get("trials", [])
    validation = result_data.get("validation", {})

    return {
        "optimization_id": run.id,
        "status": run.status.value,
        "best_params": run.best_params_json,
        "best_value": run.best_value,
        "fitness_objective": run.fitness_objective,
        "all_trials": all_trials,
        "train_metrics": validation.get("train_metrics"),
        "test_metrics": validation.get("statistics") if validation else None,
        "param_importances": result_data.get("param_importances"),
        "walk_forward_results": result_data.get("walk_forward_results"),
        "convergence_history": result_data.get("convergence_history"),
        "sampler": result_data.get("sampler"),
        "total_pruned": result_data.get("total_pruned", 0),
    }


@router.get("/runs", response_model=list[OptimizeRunItem])
async def list_optimization_runs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    strategy: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[OptimizeRunItem]:
    """List optimization runs with pagination and optional filters."""
    stmt = (
        select(OptimizationRun, Strategy.name.label("strategy_name"))
        .join(Strategy, Strategy.id == OptimizationRun.strategy_id)
    )
    if strategy:
        stmt = stmt.where(Strategy.name == strategy)
    if status:
        stmt = stmt.where(OptimizationRun.status == status)
    stmt = stmt.order_by(OptimizationRun.created_at.desc()).offset(offset).limit(limit)

    rows = (await db.execute(stmt)).all()
    return [
        OptimizeRunItem(
            optimization_id=r.OptimizationRun.id,
            strategy_name=r.strategy_name,
            symbol=r.OptimizationRun.symbol,
            interval=r.OptimizationRun.interval,
            start_date=r.OptimizationRun.start_date,
            end_date=r.OptimizationRun.end_date,
            n_trials=r.OptimizationRun.n_trials,
            fitness_objective=r.OptimizationRun.fitness_objective,
            status=r.OptimizationRun.status.value,
            best_value=r.OptimizationRun.best_value,
            trials_completed=r.OptimizationRun.trials_completed,
            created_at=r.OptimizationRun.created_at.isoformat() if r.OptimizationRun.created_at else None,
            completed_at=r.OptimizationRun.completed_at.isoformat() if r.OptimizationRun.completed_at else None,
        )
        for r in rows
    ]
