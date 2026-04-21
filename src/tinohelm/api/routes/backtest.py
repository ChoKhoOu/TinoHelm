"""Backtest API routes."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, model_validator
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from tinohelm.api._utils import (
    HEX_PREFIX_RE,
    MIN_PREFIX_LEN,
    UUID_RE,
    fetch_redis_progress,
    fetch_redis_progress_batch,
    resolve_artifact_path,
)
from tinohelm.api.deps import get_db, get_redis, get_settings_dep
from tinohelm.core.audit import log_audit
from tinohelm.core.config import Settings
from tinohelm.core.utils import sanitize_for_json
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
    # Bar data source type (klines, markPriceKlines, indexPriceKlines, premiumIndexKlines)
    data_type: str = "klines"
    # Fee config — e.g. "0.02%" or "0.0002"
    maker_fee: str | None = None
    taker_fee: str | None = None
    # Strategy warmup and tagging
    warmup_bars: int | None = None
    tags: str | None = None

    @model_validator(mode="after")
    def _normalise_symbols_intervals(self) -> "BacktestRunRequest":
        if self.symbols is None:
            if self.symbol is not None:
                self.symbols = [self.symbol]
            else:
                # Strategies may define symbols in their config
                self.symbols = []
        # Filter out empty strings (e.g. TUI sends [""] when symbol field is blank)
        self.symbols = [s for s in self.symbols if s.strip()]
        if self.intervals is None:
            if self.interval is not None:
                self.intervals = [self.interval]
            else:
                self.intervals = ["1m"]
        # Filter out empty strings
        self.intervals = [i for i in self.intervals if i.strip()]
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
    progress_pct: int | None = None
    error: str | None = None
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


class BacktestEstimateRequest(BaseModel):
    """Request body for POST /estimate."""

    symbols: list[str]
    interval: str  # e.g. "5m", "15m", "1h"
    start_date: str  # "2026-01-01"
    end_date: str  # "2026-03-31"


class BacktestEstimateResponse(BaseModel):
    """Response body for POST /estimate."""

    total_bars: int
    estimated_seconds: int
    estimated_label: str  # e.g. "~15 min", "~2.5 小时"


class BacktestDeleteResponse(BaseModel):
    """Response body for DELETE /{run_id}."""

    run_id: str
    deleted: bool


# ---- helpers ----

_BARS_PER_DAY_KNOWN: dict[str, int] = {
    "1m": 1440,
    "5m": 288,
    "15m": 96,
    "1h": 24,
    "4h": 6,
}

_BARS_PER_SEC = 50_000


_INTERVAL_RE = re.compile(r"^(\d+)([smhd])$")


def _calc_bars_per_day(interval: str) -> int:
    """Calculate bars per day for any interval string (e.g. '2m', '3h', '1d')."""
    if interval in _BARS_PER_DAY_KNOWN:
        return _BARS_PER_DAY_KNOWN[interval]
    m = _INTERVAL_RE.match(interval.lower())
    if not m:
        return 0
    n = int(m.group(1))
    if n <= 0:
        return 0
    unit = m.group(2)
    if unit == "s":
        return 86400 // n
    if unit == "m":
        return 1440 // n
    if unit == "h":
        return max(1, 24 // n)
    if unit == "d":
        return max(1, 1 // n)
    return 0


def _format_estimated_label(seconds: int) -> str:
    if seconds < 60:
        return f"~{seconds}s"
    if seconds < 3600:
        return f"~{seconds // 60}m"
    hours = seconds / 3600
    return f"~{hours:.1f} 小时"


_ARTIFACT_WHITELIST = {
    "tearsheet.html",
    "results.json",
    "fills_report.csv",
    "orders_report.csv",
    "positions_report.csv",
    "account_report.csv",
    "order_fills_report.csv",
}

_MIME_MAP = {
    ".html": "text/html",
    ".csv": "text/csv",
    ".json": "application/json",
}


async def resolve_run_id(prefix: str, db: AsyncSession) -> str:
    """Resolve a short run_id prefix to a full UUID, git-style.

    - Full UUID → returned as-is (fast path).
    - Short hex prefix → LIKE query; unique match required.
    """
    prefix = prefix.strip().lower()

    # Full UUID — skip prefix search
    if UUID_RE.match(prefix):
        return prefix

    # Must be hex-only to prevent injection via LIKE wildcards
    if not HEX_PREFIX_RE.match(prefix) or len(prefix) < MIN_PREFIX_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"run_id prefix must be at least {MIN_PREFIX_LEN} hex characters",
        )

    stmt = (
        select(BacktestRun.run_id)
        .where(BacktestRun.run_id.like(f"{prefix}%"))
    )
    rows = (await db.execute(stmt)).scalars().all()

    if len(rows) == 0:
        raise HTTPException(status_code=404, detail=f"No backtest run matching prefix '{prefix}'")
    if len(rows) > 1:
        matches = ", ".join(r[:8] for r in rows[:5])
        raise HTTPException(
            status_code=409,
            detail=f"Ambiguous prefix '{prefix}' matches {len(rows)} runs: {matches}",
        )
    return rows[0]


# ---- routes ----

@router.post("/estimate", response_model=BacktestEstimateResponse)
async def estimate_backtest(body: BacktestEstimateRequest) -> BacktestEstimateResponse:
    """Return estimated runtime and bar count for a backtest configuration (no DB queries)."""
    try:
        start = date.fromisoformat(body.start_date)
        end = date.fromisoformat(body.end_date)
        days = max(0, (end - start).days)
    except (ValueError, TypeError):
        return BacktestEstimateResponse(total_bars=0, estimated_seconds=0, estimated_label="—")

    bars_per_day = _calc_bars_per_day(body.interval.lower())
    num_symbols = len([s for s in body.symbols if s.strip()])
    total_bars = days * bars_per_day * max(1, num_symbols)
    estimated_seconds = max(1, total_bars // _BARS_PER_SEC) if total_bars > 0 else 0
    label = _format_estimated_label(estimated_seconds) if total_bars > 0 else "—"

    return BacktestEstimateResponse(
        total_bars=total_bars,
        estimated_seconds=estimated_seconds,
        estimated_label=label,
    )


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
    # Build job payload before DB insert so we can persist it atomically
    job_dict = {
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
        "data_type": body.data_type,
        "fill_model": body.fill_model,
        "maker_fee": body.maker_fee,
        "taker_fee": body.taker_fee,
        "warmup_bars": body.warmup_bars,
        "tags": body.tags,
    }

    run = BacktestRun(
        run_id=run_id,
        strategy_name=body.strategy,
        strategy_id=row.id,
        strategy_version_id=strategy_version_id,
        symbol=",".join(body.symbols) if body.symbols else "(default)",
        interval=",".join(body.intervals),
        start_date=body.start_date,
        end_date=body.end_date,
        params_json=body.params,
        status=RunStatus.queued,
        job_payload_json=job_dict,
    )
    db.add(run)
    await db.flush()
    await db.commit()

    # Push job to Redis queue
    await rds.lpush("tino:backtest:queue", json.dumps(job_dict))

    await log_audit(db, "backtest.queued", {"run_id": run_id, "strategy": body.strategy})
    logger.info("Backtest run queued: %s", run_id)

    return BacktestRunResponse(run_id=run_id, status=RunStatus.queued.value)


@router.get("/runs", response_model=BacktestRunList)
async def list_backtest_runs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    strategy: str | None = None,
    status: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    db: AsyncSession = Depends(get_db),
    rds: aioredis.Redis = Depends(get_redis),
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

    # Fetch progress from Redis for running/queued backtests
    running_ids = [
        r.run_id for r in rows
        if r.status in (RunStatus.running, RunStatus.queued)
    ]
    progress_keys = [f"tino:backtest:progress:{rid}" for rid in running_ids]
    progress_values = await fetch_redis_progress_batch(rds, progress_keys)
    progress_map: dict[str, int] = {
        rid: val for rid, val in zip(running_ids, progress_values) if val is not None
    }

    runs = [
        BacktestRunItem(
            run_id=r.run_id,
            strategy_name=r.strategy_name,
            symbol=r.symbol,
            interval=r.interval,
            start_date=r.start_date,
            end_date=r.end_date,
            status=r.status.value,
            progress_pct=progress_map.get(r.run_id),
            error=r.error,
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
    run_id = await resolve_run_id(run_id, db)
    stmt = select(BacktestRun).where(BacktestRun.run_id == run_id)
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")

    # Read progress percentage from Redis
    progress_pct = await fetch_redis_progress(rds, f"tino:backtest:progress:{run_id}")

    # Include result when completed
    result: dict | None = None
    if run.status == RunStatus.completed and UUID_RE.match(run_id):
        try:
            artifact_path = resolve_artifact_path(
                settings.paths.artifacts, run_id, "results.json"
            )
        except HTTPException:
            artifact_path = None
        if artifact_path is not None and artifact_path.exists():
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
    run_id = await resolve_run_id(run_id, db)

    stmt = select(BacktestRun).where(BacktestRun.run_id == run_id)
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")

    artifact_path = resolve_artifact_path(
        settings.paths.artifacts, run_id, "results.json"
    )
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail="Artifact file not found (run may still be in progress)")

    content = await asyncio.to_thread(artifact_path.read_text)
    data = sanitize_for_json(json.loads(content))
    return data


@router.post("/{run_id}/cancel", response_model=BacktestCancelResponse)
async def cancel_backtest_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    rds: aioredis.Redis = Depends(get_redis),
) -> BacktestCancelResponse:
    """Cancel a queued or running backtest run."""
    run_id = await resolve_run_id(run_id, db)
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


@router.delete("/{run_id}", response_model=BacktestDeleteResponse)
async def delete_backtest_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> BacktestDeleteResponse:
    """Delete a backtest run: remove DB record and artifacts folder."""
    import shutil

    run_id = await resolve_run_id(run_id, db)
    stmt = select(BacktestRun).where(BacktestRun.run_id == run_id)
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")

    if run.status in (RunStatus.running, RunStatus.queued):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete run in '{run.status.value}' state — cancel it first",
        )

    # Delete artifacts folder — run_id is already UUID-validated by resolve_run_id
    # above (full UUIDs pass through unchanged), but resolve_artifact_path
    # re-validates and raises 400 on tampered input.
    try:
        artifact_dir = resolve_artifact_path(settings.paths.artifacts, run_id)
    except HTTPException:
        artifact_dir = None
    if artifact_dir is not None and artifact_dir.exists():
        await asyncio.to_thread(shutil.rmtree, artifact_dir)

    # Delete DB record
    await db.delete(run)
    await db.commit()

    await log_audit(db, "backtest.deleted", {"run_id": run_id})
    logger.info("Backtest run deleted: %s", run_id)

    return BacktestDeleteResponse(run_id=run_id, deleted=True)


@router.get("/compare")
async def backtest_compare(
    strategy_name: str = Query(..., description="Strategy name to compare"),
    node_type: str = Query("sandbox", description="Node type for paper equity"),
    db: AsyncSession = Depends(get_db),
    rds: aioredis.Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Compare backtest results with paper trading equity."""
    # 1. Find most recent completed backtest for this strategy
    stmt = (
        select(BacktestRun)
        .where(BacktestRun.strategy_name == strategy_name, BacktestRun.status == RunStatus.completed)
        .order_by(BacktestRun.created_at.desc())
        .limit(1)
    )
    run = (await db.execute(stmt)).scalar_one_or_none()

    backtest_data = None
    warning = None
    if run:
        try:
            results_file = resolve_artifact_path(
                settings.paths.artifacts, str(run.run_id), "results.json"
            )
        except HTTPException:
            results_file = None
        if results_file is not None and results_file.exists():
            try:
                raw = json.loads(await asyncio.to_thread(results_file.read_text))
                backtest_data = {
                    "equity_curve": raw.get("equity_curve", []),
                    "stats": {
                        k: raw.get(k) for k in [
                            "total_pnl", "win_rate", "sharpe_ratio", "max_drawdown",
                            "total_trades", "avg_trade_pnl", "profit_factor",
                        ] if k in raw
                    },
                    "run_id": run.run_id,
                    "strategy_name": strategy_name,
                }
            except Exception as e:
                warning = f"Failed to load backtest artifacts: {e}"
        else:
            warning = "Backtest artifacts not found (may have been cleaned up)"
    else:
        warning = f"No completed backtest found for strategy '{strategy_name}'"

    # 2. Load paper equity
    paper_equity: list[dict] = []
    try:
        raw_list = await rds.lrange(f"tino:{node_type}:equity_history", 0, -1)
        if raw_list:
            for item in raw_list:
                if isinstance(item, bytes):
                    item = item.decode()
                paper_equity.append(json.loads(item))
    except Exception:
        pass

    # 3. Compute comparison if both available
    comparison = None
    if backtest_data and paper_equity:
        bt_stats = backtest_data.get("stats", {})
        comparison = {
            "backtest_pnl": bt_stats.get("total_pnl"),
            "backtest_win_rate": bt_stats.get("win_rate"),
            "backtest_sharpe": bt_stats.get("sharpe_ratio"),
            "paper_equity_points": len(paper_equity),
            "paper_latest_equity": paper_equity[-1].get("equity") if paper_equity else None,
        }

    return {
        "backtest": backtest_data,
        "paper": {"equity_curve": paper_equity},
        "comparison": comparison,
        "warning": warning,
    }


@router.get("/{run_id}/artifacts", tags=["backtest"])
async def list_artifacts(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> list[dict]:
    """List available artifact files for a backtest run."""
    run_id = await resolve_run_id(run_id, db)

    stmt = select(BacktestRun).where(BacktestRun.run_id == run_id)
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")

    artifacts_dir = resolve_artifact_path(settings.paths.artifacts, run_id)
    if not artifacts_dir.exists():
        return []

    result = []
    for f in artifacts_dir.iterdir():
        if f.name in _ARTIFACT_WHITELIST and f.is_file():
            result.append({
                "filename": f.name,
                "size_bytes": f.stat().st_size,
            })
    return sorted(result, key=lambda x: x["filename"])


@router.get("/{run_id}/artifacts/{filename}", tags=["backtest"])
async def get_artifact(
    run_id: str,
    filename: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    """Serve an individual artifact file (HTML, CSV, JSON)."""
    run_id = await resolve_run_id(run_id, db)

    if filename not in _ARTIFACT_WHITELIST:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid filename. Allowed: {', '.join(sorted(_ARTIFACT_WHITELIST))}",
        )

    stmt = select(BacktestRun).where(BacktestRun.run_id == run_id)
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")

    artifact_path = resolve_artifact_path(settings.paths.artifacts, run_id, filename)
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail=f"Artifact '{filename}' not found")

    suffix = artifact_path.suffix.lower()
    media_type = _MIME_MAP.get(suffix, "application/octet-stream")

    # For HTML files, omit filename to avoid Content-Disposition: attachment
    # which prevents iframe rendering. CSV/JSON keep the download behavior.
    if suffix == ".html":
        return FileResponse(path=str(artifact_path), media_type=media_type)
    return FileResponse(
        path=str(artifact_path),
        media_type=media_type,
        filename=filename,
    )
