"""Watchlist CRUD API routes."""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.api.deps import get_db
from tinohelm.db.models import WatchlistItem

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["watchlist"])


class WatchlistItemResponse(BaseModel):
    id: int
    instrument_id: str
    source: str
    created_at: str | None = None


class WatchlistItemCreate(BaseModel):
    instrument_id: str = Field(..., min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._\-/]+$")
    source: str = Field(default="manual", min_length=1, max_length=50)


@router.get("/watchlist", response_model=list[WatchlistItemResponse])
async def list_watchlist(db: AsyncSession = Depends(get_db)) -> list[WatchlistItemResponse]:
    stmt = select(WatchlistItem).order_by(WatchlistItem.created_at.desc())
    rows = (await db.execute(stmt)).scalars().all()
    return [
        WatchlistItemResponse(
            id=w.id,
            instrument_id=w.instrument_id,
            source=w.source,
            created_at=w.created_at.isoformat() if w.created_at else None,
        )
        for w in rows
    ]


@router.post("/watchlist", response_model=WatchlistItemResponse, status_code=201)
async def add_watchlist_item(
    body: WatchlistItemCreate,
    db: AsyncSession = Depends(get_db),
) -> WatchlistItemResponse:
    item = WatchlistItem(instrument_id=body.instrument_id, source=body.source)
    db.add(item)
    try:
        await db.commit()
        await db.refresh(item)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Instrument already in watchlist")
    return WatchlistItemResponse(
        id=item.id,
        instrument_id=item.instrument_id,
        source=item.source,
        created_at=item.created_at.isoformat() if item.created_at else None,
    )


@router.delete("/watchlist/{item_id}", status_code=204)
async def delete_watchlist_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    stmt = select(WatchlistItem).where(WatchlistItem.id == item_id)
    item = (await db.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    await db.delete(item)
    await db.commit()
