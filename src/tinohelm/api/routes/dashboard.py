"""Dashboard, portfolio, orders, and analytics API routes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import cast, select, func, Float
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.api.deps import get_db
from tinohelm.db.models import BacktestRun, Order, Position, RunStatus, Strategy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["dashboard"])


# ---- response schemas ----

class DashboardSummary(BaseModel):
    """Placeholder dashboard summary statistics."""

    total_equity: float = 0.0
    daily_pnl: float = 0.0
    open_positions: int = 0
    total_orders_today: int = 0
    active_strategy_count: int = 0
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0


class AllocationItem(BaseModel):
    """A single position / allocation entry."""

    instrument_id: str
    side: str
    quantity: str
    avg_px_open: float | None = None
    unrealized_pnl: float | None = None
    node_type: str


class OrderItem(BaseModel):
    """A single order entry."""

    id: int
    node_type: str
    order_id: str
    instrument_id: str
    side: str
    order_type: str
    quantity: str
    price: str | None = None
    status: str
    strategy_id: str | None = None
    created_at: str | None = None


# ---- routes ----

@router.get("/dashboard/summary", response_model=DashboardSummary)
async def dashboard_summary(
    db: AsyncSession = Depends(get_db),
) -> DashboardSummary:
    """Return dashboard summary stats computed from live DB data."""
    # Positions: count, total equity, daily pnl (SQL aggregation)
    agg = await db.execute(
        select(
            func.count(Position.id).label("count"),
            func.coalesce(func.sum(cast(Position.quantity, Float) * cast(Position.avg_px_open, Float)), 0).label("equity"),
            func.coalesce(func.sum(cast(Position.unrealized_pnl, Float)), 0).label("pnl"),
        ).where(Position.is_open == True)
    )
    row = agg.one()
    open_positions = row.count
    total_equity = float(row.equity)
    daily_pnl = float(row.pnl)

    # Orders today
    order_count = (await db.execute(select(func.count(Order.id)))).scalar() or 0

    # Active strategies
    strategy_count = (await db.execute(select(func.count(Strategy.id)))).scalar() or 0

    return DashboardSummary(
        total_equity=total_equity,
        daily_pnl=daily_pnl,
        open_positions=open_positions,
        total_orders_today=order_count,
        active_strategy_count=strategy_count,
        win_rate=0.0,
        sharpe_ratio=0.0,
    )


@router.get("/portfolio/allocation", response_model=list[AllocationItem])
async def portfolio_allocation(
    db: AsyncSession = Depends(get_db),
) -> list[AllocationItem]:
    """Return current positions grouped by instrument."""
    stmt = select(Position).order_by(Position.instrument_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        AllocationItem(
            instrument_id=p.instrument_id,
            side=p.side,
            quantity=p.quantity,
            avg_px_open=p.avg_px_open,
            unrealized_pnl=p.unrealized_pnl,
            node_type=p.node_type,
        )
        for p in rows
    ]


@router.get("/orders", response_model=list[OrderItem])
async def list_orders(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    node_type: str | None = None,
    instrument: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[OrderItem]:
    """Paginated order history with optional filters."""
    stmt = select(Order)
    if node_type:
        stmt = stmt.where(Order.node_type == node_type)
    if instrument:
        stmt = stmt.where(Order.instrument_id == instrument)
    if status:
        stmt = stmt.where(Order.status == status)
    stmt = stmt.order_by(Order.created_at.desc()).offset(offset).limit(limit)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        OrderItem(
            id=o.id,
            node_type=o.node_type.value,
            order_id=o.order_id,
            instrument_id=o.instrument_id,
            side=o.side,
            order_type=o.order_type,
            quantity=o.quantity,
            price=o.price,
            status=o.status,
            strategy_id=o.strategy_id,
            created_at=o.created_at.isoformat() if o.created_at else None,
        )
        for o in rows
    ]


# ---- analytics endpoints ----


def _completed_runs_stmt():
    """Base select for completed backtest runs."""
    return select(BacktestRun).where(BacktestRun.status == RunStatus.completed)


@router.get("/analytics/returns-heatmap")
async def returns_heatmap(db: AsyncSession = Depends(get_db)) -> dict:
    """Returns heatmap: average total_return grouped by year/month of completed_at."""
    stmt = _completed_runs_stmt().where(BacktestRun.completed_at.isnot(None)).limit(1000)
    rows = (await db.execute(stmt)).scalars().all()

    # Group by (year, month) and average total_return
    buckets: dict[tuple[int, int], list[float]] = {}
    for run in rows:
        summary = run.result_summary_json
        if not summary or "total_return" not in summary:
            continue
        try:
            ret = float(summary["total_return"])
        except (TypeError, ValueError):
            continue
        key = (run.completed_at.year, run.completed_at.month)
        buckets.setdefault(key, []).append(ret)

    if not buckets:
        return {"data": [], "message": "No backtest data available"}

    data = [
        {
            "year": str(year),
            "month": month,
            "return_pct": round(sum(vals) / len(vals) * 100, 2),
        }
        for (year, month), vals in sorted(buckets.items())
    ]
    return {"data": data}


@router.get("/analytics/drawdown")
async def drawdown(db: AsyncSession = Depends(get_db)) -> dict:
    """Drawdown chart: max_drawdown per completed run ordered by completed_at."""
    stmt = (
        _completed_runs_stmt()
        .where(BacktestRun.completed_at.isnot(None))
        .order_by(BacktestRun.completed_at)
        .limit(1000)
    )
    rows = (await db.execute(stmt)).scalars().all()

    data = []
    for run in rows:
        summary = run.result_summary_json
        if not summary or "max_drawdown" not in summary:
            continue
        try:
            dd = float(summary["max_drawdown"])
        except (TypeError, ValueError):
            continue
        data.append({
            "date": run.completed_at.strftime("%Y-%m-%d"),
            "drawdown": round(dd * 100, 2),
        })

    if not data:
        return {"data": [], "message": "No backtest data available"}
    return {"data": data}


@router.get("/analytics/distribution")
async def distribution(db: AsyncSession = Depends(get_db)) -> dict:
    """Return distribution: bucket total_return into percentage bins."""
    stmt = _completed_runs_stmt().limit(1000)
    rows = (await db.execute(stmt)).scalars().all()

    # Define bin edges and labels
    bin_edges = [-float("inf"), -8, -6, -4, -2, 0, 2, 4, 6, 8, float("inf")]
    bin_labels = ["-8%", "-6%", "-4%", "-2%", "0%", "2%", "4%", "6%", "8%", ">8%"]
    counts = [0] * len(bin_labels)

    has_data = False
    for run in rows:
        summary = run.result_summary_json
        if not summary or "total_return" not in summary:
            continue
        try:
            ret_pct = float(summary["total_return"]) * 100
        except (TypeError, ValueError):
            continue
        has_data = True
        for i in range(len(bin_edges) - 1):
            if bin_edges[i] <= ret_pct < bin_edges[i + 1]:
                counts[i] += 1
                break

    if not has_data:
        return {"data": [], "message": "No backtest data available"}

    data = [{"range": label, "count": count} for label, count in zip(bin_labels, counts)]
    return {"data": data}


@router.get("/analytics/rolling-sharpe")
async def rolling_sharpe(db: AsyncSession = Depends(get_db)) -> dict:
    """Rolling Sharpe ratio per completed run ordered by completed_at."""
    stmt = (
        _completed_runs_stmt()
        .where(BacktestRun.completed_at.isnot(None))
        .order_by(BacktestRun.completed_at)
        .limit(1000)
    )
    rows = (await db.execute(stmt)).scalars().all()

    data = []
    for run in rows:
        summary = run.result_summary_json
        if not summary or "sharpe_ratio" not in summary:
            continue
        try:
            sharpe = float(summary["sharpe_ratio"])
        except (TypeError, ValueError):
            continue
        data.append({
            "date": run.completed_at.strftime("%Y-%m-%d"),
            "sharpe": round(sharpe, 2),
        })

    if not data:
        return {"data": [], "message": "No backtest data available"}
    return {"data": data}
