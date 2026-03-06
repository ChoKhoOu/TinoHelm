"""Data catalog and fetch API routes."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.api.deps import get_db, get_settings_dep
from tinohelm.core.config import Settings
from tinohelm.db.models import DataCatalog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data"])


# ---- request / response schemas ----

class DataCatalogItem(BaseModel):
    """Single item in the data catalog."""

    id: int
    symbol: str
    data_type: str
    interval: str
    start_date: date
    end_date: date
    file_path: str
    size_bytes: int
    created_at: str | None = None


class DataFetchRequest(BaseModel):
    """Request body for POST /fetch."""

    symbol: str
    interval: str
    start: date
    end: date


class CompactRequest(BaseModel):
    """Request body for POST /compact."""

    symbol: str
    interval: str


# ---- background task ----

async def _run_data_fetch(symbol: str, interval: str, start: date, end: date, settings: Settings | None = None) -> None:
    """Background task to fetch market data, store to Parquet, and register in DB catalog."""
    try:
        from datetime import datetime, timezone
        from tinohelm.data.fetcher import fetch_and_store
        from tinohelm.db.session import get_session_factory

        start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc)

        catalog_path = str(settings.paths.catalog) if settings else "data/catalog"
        redis_url = str(settings.redis.url) if settings else "redis://redis:6379"
        db_url = str(settings.database.url) if settings else None

        result = await fetch_and_store(
            symbol=symbol,
            interval=interval,
            start=start_dt,
            end=end_dt,
            catalog_path=catalog_path,
            redis_url=redis_url,
            progress_channel=f"tino:data:progress:{symbol}:{interval}",
            db_url=db_url,
        )

        # Always upsert DB catalog (even if skipped — to expand date range)
        # Calculate total size from actual parquet files on disk
        bar_dir = Path(catalog_path) / "data" / "bar"
        total_size = sum(
            f.stat().st_size for f in bar_dir.rglob("*.parquet")
        ) if bar_dir.exists() else 0

        factory = get_session_factory()
        async with factory() as db:
            stmt = select(DataCatalog).where(
                DataCatalog.symbol == symbol,
                DataCatalog.data_type == "bar",
                DataCatalog.interval == interval,
            )
            existing = (await db.execute(stmt)).scalar_one_or_none()
            if existing:
                existing.start_date = min(existing.start_date, start)
                existing.end_date = max(existing.end_date, end)
                existing.size_bytes = total_size
            else:
                db.add(DataCatalog(
                    symbol=symbol,
                    data_type="bar",
                    interval=interval,
                    start_date=start,
                    end_date=end,
                    file_path=catalog_path,
                    size_bytes=total_size,
                ))
            await db.commit()

        logger.info("Data fetch completed: %s %s — %d bars (skipped=%s)",
                     symbol, interval, result["bars_count"], result.get("skipped", False))
    except Exception as exc:
        logger.exception("Data fetch failed: %s", exc)


# ---- routes ----

@router.get("/catalog", response_model=list[DataCatalogItem])
async def list_data_catalog(
    db: AsyncSession = Depends(get_db),
) -> list[DataCatalogItem]:
    """List all data catalog entries."""
    stmt = select(DataCatalog).order_by(DataCatalog.symbol, DataCatalog.interval)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        DataCatalogItem(
            id=r.id,
            symbol=r.symbol,
            data_type=r.data_type,
            interval=r.interval,
            start_date=r.start_date,
            end_date=r.end_date,
            file_path=r.file_path,
            size_bytes=r.size_bytes,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.post("/fetch")
async def trigger_data_fetch(
    body: DataFetchRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Trigger a background data fetch task."""
    background_tasks.add_task(_run_data_fetch, body.symbol, body.interval, body.start, body.end, settings)
    return {
        "status": "accepted",
        "message": f"Data fetch for {body.symbol} {body.interval} queued",
        "symbol": body.symbol,
        "interval": body.interval,
        "start": body.start.isoformat(),
        "end": body.end.isoformat(),
    }


async def _run_compact(symbol: str, interval: str, settings: Settings) -> None:
    """Background task to compact Parquet files and update DB catalog size."""
    try:
        from tinohelm.data.catalog import compact_bars
        from tinohelm.db.session import get_session_factory

        catalog_path = str(settings.paths.catalog) if settings else "data/catalog"
        result = compact_bars(symbol=symbol, interval=interval, catalog_path=catalog_path)

        # Update DB catalog size_bytes with the new total
        bar_dir = Path(catalog_path) / "data" / "bar"
        total_size = sum(
            f.stat().st_size for f in bar_dir.rglob("*.parquet")
        ) if bar_dir.exists() else 0

        factory = get_session_factory()
        async with factory() as db:
            stmt = select(DataCatalog).where(
                DataCatalog.symbol == symbol,
                DataCatalog.data_type == "bar",
                DataCatalog.interval == interval,
            )
            existing = (await db.execute(stmt)).scalar_one_or_none()
            if existing:
                existing.size_bytes = total_size
                await db.commit()

        logger.info(
            "Compaction background task done: %s %s — %d bars, %d -> %d bytes",
            symbol, interval, result["bars_count"], result["size_before"], result["size_after"],
        )
    except Exception as exc:
        logger.exception("Compaction failed for %s %s: %s", symbol, interval, exc)


@router.post("/compact")
async def trigger_compact(
    body: CompactRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Trigger background compaction for a symbol/interval."""
    background_tasks.add_task(_run_compact, body.symbol, body.interval, settings)
    return {
        "status": "accepted",
        "message": f"Compaction for {body.symbol} {body.interval} queued",
    }


@router.get("/validate/{symbol}/{interval}")
async def validate_data(
    symbol: str,
    interval: str,
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Validate data integrity for a symbol/interval. Returns synchronously."""
    from tinohelm.data.catalog import validate_bars

    try:
        result = validate_bars(
            symbol=symbol,
            interval=interval,
            catalog_path=str(settings.paths.catalog),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Validation failed for %s %s: %s", symbol, interval, exc)
        raise HTTPException(status_code=500, detail=f"Validation failed: {exc}")

    return result
