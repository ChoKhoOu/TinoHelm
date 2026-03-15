"""Live/sandbox trading data API routes (positions, fills, summary)."""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.api.deps import get_db
from tinohelm.db.models import Fill, Position

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trading", tags=["trading"])


# ---- response schemas ----


class PositionItem(BaseModel):
    """Single position record."""

    id: int
    node_type: str
    position_id: str
    strategy_id_tag: str
    instrument_id: str
    side: str
    quantity: str
    signed_qty: float
    avg_px_open: float | None = None
    avg_px_close: float | None = None
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    currency: str | None = None
    entry_side: str | None = None
    peak_qty: str | None = None
    ts_opened: str | None = None
    ts_closed: str | None = None
    duration: str | None = None
    is_open: bool
    event_count: int
    updated_at: str | None = None


class FillItem(BaseModel):
    """Single fill record."""

    id: int
    node_type: str
    trade_id: str
    position_id: str | None = None
    client_order_id: str
    venue_order_id: str | None = None
    strategy_id_tag: str | None = None
    instrument_id: str
    order_side: str
    last_qty: str
    last_px: str
    commission: str | None = None
    liquidity_side: str | None = None
    ts_event: str
    created_at: str | None = None


class TradingSummary(BaseModel):
    """Aggregated trading summary for a node type."""

    node_type: str
    open_positions: int
    total_positions: int
    total_fills: int
    total_realized_pnl: float
    open_instruments: list[str]


# ---- routes ----


@router.get("/positions", response_model=list[PositionItem])
async def list_positions(
    node_type: str | None = Query(None, description="Filter by node type (sandbox/live)"),
    instrument_id: str | None = Query(None, description="Filter by instrument"),
    strategy_id_tag: str | None = Query(None, description="Filter by strategy ID tag"),
    is_open: bool | None = Query(None, description="Filter by open/closed status"),
    db: AsyncSession = Depends(get_db),
) -> list[PositionItem]:
    """List all positions, optionally filtered."""
    stmt = select(Position).order_by(Position.updated_at.desc())
    if node_type is not None:
        stmt = stmt.where(Position.node_type == node_type)
    if instrument_id is not None:
        stmt = stmt.where(Position.instrument_id == instrument_id)
    if strategy_id_tag is not None:
        stmt = stmt.where(Position.strategy_id_tag == strategy_id_tag)
    if is_open is not None:
        stmt = stmt.where(Position.is_open == is_open)  # noqa: E712
    rows = (await db.execute(stmt)).scalars().all()
    return [
        PositionItem(
            id=r.id,
            node_type=r.node_type,
            position_id=r.position_id,
            strategy_id_tag=r.strategy_id_tag,
            instrument_id=r.instrument_id,
            side=r.side,
            quantity=r.quantity,
            signed_qty=r.signed_qty,
            avg_px_open=r.avg_px_open,
            avg_px_close=r.avg_px_close,
            realized_pnl=r.realized_pnl,
            unrealized_pnl=r.unrealized_pnl,
            currency=r.currency,
            entry_side=r.entry_side,
            peak_qty=r.peak_qty,
            ts_opened=r.ts_opened,
            ts_closed=r.ts_closed,
            duration=r.duration,
            is_open=r.is_open,
            event_count=r.event_count,
            updated_at=r.updated_at.isoformat() if r.updated_at else None,
        )
        for r in rows
    ]


@router.get("/positions/{position_id:int}", response_model=PositionItem)
async def get_position(
    position_id: int,
    db: AsyncSession = Depends(get_db),
) -> PositionItem:
    """Get a single position by its database ID."""
    stmt = select(Position).where(Position.id == position_id)
    r = (await db.execute(stmt)).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail=f"Position {position_id} not found")
    return PositionItem(
        id=r.id,
        node_type=r.node_type,
        position_id=r.position_id,
        strategy_id_tag=r.strategy_id_tag,
        instrument_id=r.instrument_id,
        side=r.side,
        quantity=r.quantity,
        signed_qty=r.signed_qty,
        avg_px_open=r.avg_px_open,
        avg_px_close=r.avg_px_close,
        realized_pnl=r.realized_pnl,
        unrealized_pnl=r.unrealized_pnl,
        currency=r.currency,
        entry_side=r.entry_side,
        peak_qty=r.peak_qty,
        ts_opened=r.ts_opened,
        ts_closed=r.ts_closed,
        duration=r.duration,
        is_open=r.is_open,
        event_count=r.event_count,
        updated_at=r.updated_at.isoformat() if r.updated_at else None,
    )


@router.get("/fills", response_model=list[FillItem])
async def list_fills(
    node_type: str | None = Query(None, description="Filter by node type (sandbox/live)"),
    client_order_id: str | None = Query(None, description="Filter by client order ID"),
    instrument_id: str | None = Query(None, description="Filter by instrument"),
    strategy_id_tag: str | None = Query(None, description="Filter by strategy ID tag"),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
    db: AsyncSession = Depends(get_db),
) -> list[FillItem]:
    """List fills ordered by created_at DESC."""
    stmt = select(Fill).order_by(Fill.created_at.desc()).limit(limit)
    if node_type is not None:
        stmt = stmt.where(Fill.node_type == node_type)
    if client_order_id is not None:
        stmt = stmt.where(Fill.client_order_id == client_order_id)
    if instrument_id is not None:
        stmt = stmt.where(Fill.instrument_id == instrument_id)
    if strategy_id_tag is not None:
        stmt = stmt.where(Fill.strategy_id_tag == strategy_id_tag)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        FillItem(
            id=r.id,
            node_type=r.node_type,
            trade_id=r.trade_id,
            position_id=r.position_id,
            client_order_id=r.client_order_id,
            venue_order_id=r.venue_order_id,
            strategy_id_tag=r.strategy_id_tag,
            instrument_id=r.instrument_id,
            order_side=r.order_side,
            last_qty=r.last_qty,
            last_px=r.last_px,
            commission=r.commission,
            liquidity_side=r.liquidity_side,
            ts_event=r.ts_event,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.get("/fills/{fill_id:int}", response_model=FillItem)
async def get_fill(
    fill_id: int,
    db: AsyncSession = Depends(get_db),
) -> FillItem:
    """Get a single fill by its database ID."""
    stmt = select(Fill).where(Fill.id == fill_id)
    r = (await db.execute(stmt)).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail=f"Fill {fill_id} not found")
    return FillItem(
        id=r.id,
        node_type=r.node_type,
        trade_id=r.trade_id,
        position_id=r.position_id,
        client_order_id=r.client_order_id,
        venue_order_id=r.venue_order_id,
        strategy_id_tag=r.strategy_id_tag,
        instrument_id=r.instrument_id,
        order_side=r.order_side,
        last_qty=r.last_qty,
        last_px=r.last_px,
        commission=r.commission,
        liquidity_side=r.liquidity_side,
        ts_event=r.ts_event,
        created_at=r.created_at.isoformat() if r.created_at else None,
    )


@router.get("/summary", response_model=TradingSummary)
async def trading_summary(
    node_type: str = Query(..., description="Node type (sandbox/live)"),
    db: AsyncSession = Depends(get_db),
) -> TradingSummary:
    """Aggregated trading summary for a given node type."""
    # Total positions
    total_pos_stmt = select(func.count(Position.id)).where(Position.node_type == node_type)
    total_positions = (await db.execute(total_pos_stmt)).scalar_one()

    # Open positions: use the is_open boolean column
    open_pos_stmt = select(func.count(Position.id)).where(
        Position.node_type == node_type,
        Position.is_open == True,  # noqa: E712
    )
    open_positions = (await db.execute(open_pos_stmt)).scalar_one()

    # Open instruments
    open_instr_stmt = (
        select(Position.instrument_id)
        .where(
            Position.node_type == node_type,
            Position.is_open == True,  # noqa: E712
        )
        .distinct()
    )
    open_instruments = list((await db.execute(open_instr_stmt)).scalars().all())

    # Total fills
    total_fills_stmt = select(func.count(Fill.id)).where(Fill.node_type == node_type)
    total_fills = (await db.execute(total_fills_stmt)).scalar_one()

    # Sum realized PnL (Float column, sum directly in DB)
    pnl_stmt = select(func.coalesce(func.sum(Position.realized_pnl), 0.0)).where(
        Position.node_type == node_type
    )
    total_realized_pnl = float((await db.execute(pnl_stmt)).scalar_one())

    return TradingSummary(
        node_type=node_type,
        open_positions=open_positions,
        total_positions=total_positions,
        total_fills=total_fills,
        total_realized_pnl=total_realized_pnl,
        open_instruments=open_instruments,
    )
