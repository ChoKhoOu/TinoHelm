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
    instrument_id: str
    side: str
    quantity: str
    avg_price: str
    unrealized_pnl: str
    realized_pnl: str
    strategy_id: str | None = None
    updated_at: str | None = None


class FillItem(BaseModel):
    """Single fill record."""

    id: int
    node_type: str
    order_id: str
    instrument_id: str
    side: str
    quantity: str
    price: str
    commission: str
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
    db: AsyncSession = Depends(get_db),
) -> list[PositionItem]:
    """List all positions, optionally filtered."""
    stmt = select(Position).order_by(Position.updated_at.desc())
    if node_type is not None:
        stmt = stmt.where(Position.node_type == node_type)
    if instrument_id is not None:
        stmt = stmt.where(Position.instrument_id == instrument_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        PositionItem(
            id=r.id,
            node_type=r.node_type.value if hasattr(r.node_type, "value") else str(r.node_type),
            instrument_id=r.instrument_id,
            side=r.side,
            quantity=r.quantity,
            avg_price=r.avg_price,
            unrealized_pnl=r.unrealized_pnl,
            realized_pnl=r.realized_pnl,
            strategy_id=r.strategy_id,
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
        node_type=r.node_type.value if hasattr(r.node_type, "value") else str(r.node_type),
        instrument_id=r.instrument_id,
        side=r.side,
        quantity=r.quantity,
        avg_price=r.avg_price,
        unrealized_pnl=r.unrealized_pnl,
        realized_pnl=r.realized_pnl,
        strategy_id=r.strategy_id,
        updated_at=r.updated_at.isoformat() if r.updated_at else None,
    )


@router.get("/fills", response_model=list[FillItem])
async def list_fills(
    node_type: str | None = Query(None, description="Filter by node type (sandbox/live)"),
    order_id: str | None = Query(None, description="Filter by order ID"),
    instrument_id: str | None = Query(None, description="Filter by instrument"),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
    db: AsyncSession = Depends(get_db),
) -> list[FillItem]:
    """List fills ordered by created_at DESC."""
    stmt = select(Fill).order_by(Fill.created_at.desc()).limit(limit)
    if node_type is not None:
        stmt = stmt.where(Fill.node_type == node_type)
    if order_id is not None:
        stmt = stmt.where(Fill.order_id == order_id)
    if instrument_id is not None:
        stmt = stmt.where(Fill.instrument_id == instrument_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        FillItem(
            id=r.id,
            node_type=r.node_type.value if hasattr(r.node_type, "value") else str(r.node_type),
            order_id=r.order_id,
            instrument_id=r.instrument_id,
            side=r.side,
            quantity=r.quantity,
            price=r.price,
            commission=r.commission,
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
        node_type=r.node_type.value if hasattr(r.node_type, "value") else str(r.node_type),
        order_id=r.order_id,
        instrument_id=r.instrument_id,
        side=r.side,
        quantity=r.quantity,
        price=r.price,
        commission=r.commission,
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

    # Open positions: quantity > 0 (non-zero means still open)
    open_pos_stmt = select(func.count(Position.id)).where(
        Position.node_type == node_type,
        Position.quantity != "0",
        Position.quantity != "0.0",
    )
    open_positions = (await db.execute(open_pos_stmt)).scalar_one()

    # Open instruments
    open_instr_stmt = select(Position.instrument_id).where(
        Position.node_type == node_type,
        Position.quantity != "0",
        Position.quantity != "0.0",
    ).distinct()
    open_instruments = list((await db.execute(open_instr_stmt)).scalars().all())

    # Total fills
    total_fills_stmt = select(func.count(Fill.id)).where(Fill.node_type == node_type)
    total_fills = (await db.execute(total_fills_stmt)).scalar_one()

    # Sum realized PnL (stored as string, cast to float in Python)
    pnl_stmt = select(Position.realized_pnl).where(Position.node_type == node_type)
    pnl_rows = (await db.execute(pnl_stmt)).scalars().all()
    total_realized_pnl = 0.0
    for pnl_str in pnl_rows:
        try:
            # Handle strings like "114.60 USDT" — take numeric part only
            val = pnl_str.split()[0] if pnl_str else "0"
            total_realized_pnl += float(val)
        except (ValueError, IndexError):
            pass

    return TradingSummary(
        node_type=node_type,
        open_positions=open_positions,
        total_positions=total_positions,
        total_fills=total_fills,
        total_realized_pnl=total_realized_pnl,
        open_instruments=open_instruments,
    )
