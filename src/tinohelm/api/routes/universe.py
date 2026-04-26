"""Universe API routes — sync CSV to DB and retrieve universe records.

Endpoints
---------
POST /api/factor/universes/sync  — idempotent upsert of a CSV into DB
GET  /api/factor/universes/{id}  — retrieve a universe row by integer ID
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.api.deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/factor/universes", tags=["universes"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class SyncRequest(BaseModel):
    """Request body for POST /api/factor/universes/sync."""

    csv_path: str


class SyncResponse(BaseModel):
    """Response for POST /api/factor/universes/sync."""

    id: int
    name: str
    created: bool  # True if a new row was inserted; False if existing was returned


class UniverseResponse(BaseModel):
    """Response for GET /api/factor/universes/{id}."""

    id: int
    name: str
    source_csv_path: str
    source_csv_hash: str
    min_history_bars: int
    new_coin_isolation_days: int
    pit_rules_json: dict
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# POST /api/factor/universes/sync
# ---------------------------------------------------------------------------

@router.post("/sync", response_model=SyncResponse)
async def sync_universe(
    req: SyncRequest,
    db: AsyncSession = Depends(get_db),
) -> SyncResponse:
    """Sync a universe CSV file into the DB (idempotent upsert by sha256 hash).

    - Same CSV content → returns existing row, ``created=false``.
    - New content → inserts new row, ``created=true``.

    Raises 404 if the CSV file does not exist at the given path.
    Raises 422 if the CSV is missing required columns or has parse errors.
    """
    from tinohelm.db.models import Universe as UniverseORM
    from tinohelm.factor.universe import Universe
    import hashlib

    csv_path = Path(req.csv_path)

    if not csv_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"CSV file not found: {csv_path}",
        )

    # Detect whether a row with this hash already exists BEFORE calling sync_from_csv
    # so we can report the `created` flag accurately.
    content_bytes = csv_path.read_bytes()
    csv_hash = hashlib.sha256(content_bytes).hexdigest()

    existing = (await db.execute(
        select(UniverseORM).where(UniverseORM.source_csv_hash == csv_hash)
    )).scalar_one_or_none()

    was_new = existing is None

    try:
        _universe, db_id = await Universe.sync_from_csv(csv_path, db)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if was_new:
        await db.commit()

    logger.info(
        "Universe sync: name=%s id=%d created=%s",
        _universe.name,
        db_id,
        was_new,
    )

    return SyncResponse(id=db_id, name=_universe.name, created=was_new)


# ---------------------------------------------------------------------------
# GET /api/factor/universes/{id}
# ---------------------------------------------------------------------------

@router.get("/{universe_id}", response_model=UniverseResponse)
async def get_universe(
    universe_id: int,
    db: AsyncSession = Depends(get_db),
) -> UniverseResponse:
    """Return the full universe record for a given integer ID.

    ``pit_rules_json`` is returned as a dict (already deserialized by
    SQLAlchemy JSON column — no manual json.loads required).

    Raises 404 if the ID does not exist.
    """
    from tinohelm.db.models import Universe as UniverseORM

    row = (await db.execute(
        select(UniverseORM).where(UniverseORM.id == universe_id)
    )).scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Universe id={universe_id} not found")

    return UniverseResponse(
        id=row.id,
        name=row.name,
        source_csv_path=row.source_csv_path,
        source_csv_hash=row.source_csv_hash,
        min_history_bars=row.min_history_bars,
        new_coin_isolation_days=row.new_coin_isolation_days,
        pit_rules_json=row.pit_rules_json or {},
        created_at=(row.created_at.isoformat() + "Z") if row.created_at else "",
        updated_at=(row.updated_at.isoformat() + "Z") if row.updated_at else "",
    )
