"""Data catalog and fetch API routes."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
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


class DataFetchBatchRequest(BaseModel):
    """Request body for POST /fetch-batch."""

    symbols: list[str]
    intervals: list[str]
    start: date
    end: date


class CompactRequest(BaseModel):
    """Request body for POST /compact."""

    symbol: str
    interval: str


# ---- helpers ----

_UNIT_MAP = {"m": "MINUTE", "h": "HOUR", "d": "DAY"}
_UNIT_REVERSE = {v: k for k, v in _UNIT_MAP.items()}


def _interval_to_nt(interval: str) -> str:
    """Convert interval like '7m' to NT dir suffix like '7-MINUTE'.

    Raises ValueError if format is invalid.
    """
    m = re.fullmatch(r"(\d+)([mhd])", interval)
    if not m:
        raise ValueError(f"Invalid interval '{interval}': expected <number><m|h|d> (e.g. 5m, 4h, 1d)")
    step, unit = m.group(1), m.group(2)
    return f"{step}-{_UNIT_MAP[unit]}"


def _nt_to_interval(nt_suffix: str) -> str | None:
    """Convert NT dir suffix like '7-MINUTE' back to '7m'.

    Returns None if format is unrecognized.
    """
    m = re.fullmatch(r"(\d+)-(\w+)", nt_suffix)
    if not m:
        return None
    step, agg = m.group(1), m.group(2)
    unit = _UNIT_REVERSE.get(agg)
    if unit is None:
        return None
    return f"{step}{unit}"


def _parquet_size_for(catalog_path: str, symbol: str, interval: str) -> int:
    """Calculate total Parquet size on disk for a specific symbol/interval."""
    from tinohelm.portfolio.loader import _normalize_symbol
    nt_sym = _normalize_symbol(symbol)
    nt_interval = _interval_to_nt(interval)
    bar_type_dir = Path(catalog_path) / "data" / "bar" / f"{nt_sym}-{nt_interval}-LAST-EXTERNAL"
    if not bar_type_dir.exists():
        return 0
    return sum(f.stat().st_size for f in bar_type_dir.glob("*.parquet"))


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
        # Calculate size for THIS symbol/interval only
        total_size = _parquet_size_for(catalog_path, symbol, interval)

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

        # Update DB catalog size_bytes
        total_size = _parquet_size_for(catalog_path, symbol, interval)

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


@router.post("/fetch-batch")
async def trigger_data_fetch_batch(
    body: DataFetchBatchRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Trigger background data fetch for multiple symbols in parallel."""
    if not body.symbols:
        raise HTTPException(status_code=400, detail="symbols must not be empty")
    if not body.intervals:
        raise HTTPException(status_code=400, detail="intervals must not be empty")
    count = 0
    for symbol in body.symbols:
        for interval in body.intervals:
            background_tasks.add_task(_run_data_fetch, symbol, interval, body.start, body.end, settings)
            count += 1
    return {
        "status": "accepted",
        "message": f"Data fetch for {count} task(s) queued ({len(body.symbols)} symbol(s) × {len(body.intervals)} interval(s))",
        "symbols": body.symbols,
        "intervals": body.intervals,
        "start": body.start.isoformat(),
        "end": body.end.isoformat(),
        "count": count,
    }


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


@router.post("/scan")
async def scan_data_catalog(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Scan Parquet files on disk and sync missing entries into DB catalog.

    Discovers bar data directories, reads date ranges from the actual
    Parquet data, and upserts DataCatalog rows for any that are missing.
    """
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    catalog_path = str(settings.paths.catalog)
    bar_dir = Path(catalog_path) / "data" / "bar"
    if not bar_dir.exists():
        return {"status": "ok", "scanned": 0, "created": 0, "updated": 0}

    # Parse bar_type directory names:
    #   BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL
    pattern = re.compile(r"^(.+\.BINANCE)-(\d+-\w+)-LAST-EXTERNAL$")

    created = 0
    updated = 0
    scanned = 0

    for entry in sorted(bar_dir.iterdir()):
        if not entry.is_dir():
            continue
        m = pattern.match(entry.name)
        if not m:
            continue

        nt_sym = m.group(1)          # e.g. BTCUSDT-PERP.BINANCE
        nt_interval = m.group(2)     # e.g. 1-MINUTE
        interval = _nt_to_interval(nt_interval)
        if interval is None:
            logger.warning("Scan: unknown interval %s in %s, skipping", nt_interval, entry.name)
            continue

        # Strip .BINANCE suffix for user-facing symbol
        symbol = nt_sym.removesuffix(".BINANCE")

        parquet_files = list(entry.glob("*.parquet"))
        if not parquet_files:
            continue

        scanned += 1
        size_bytes = sum(f.stat().st_size for f in parquet_files)

        # Read actual date range from Parquet data
        bar_type_str = f"{nt_sym}-{nt_interval}-LAST-EXTERNAL"
        try:
            catalog = ParquetDataCatalog(catalog_path)
            bars = catalog.bars(bar_types=[bar_type_str])
            if not bars:
                logger.warning("Scan: no bars readable for %s, skipping", bar_type_str)
                continue
            ts_min = min(b.ts_event for b in bars)
            ts_max = max(b.ts_event for b in bars)
            start_date = datetime.fromtimestamp(ts_min / 1_000_000_000, tz=timezone.utc).date()
            end_date = datetime.fromtimestamp(ts_max / 1_000_000_000, tz=timezone.utc).date()
        except Exception:
            logger.warning("Scan: failed to read bars for %s", bar_type_str, exc_info=True)
            continue

        # Upsert DB
        stmt = select(DataCatalog).where(
            DataCatalog.symbol == symbol,
            DataCatalog.data_type == "bar",
            DataCatalog.interval == interval,
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            existing.start_date = min(existing.start_date, start_date)
            existing.end_date = max(existing.end_date, end_date)
            existing.size_bytes = size_bytes
            updated += 1
        else:
            db.add(DataCatalog(
                symbol=symbol,
                data_type="bar",
                interval=interval,
                start_date=start_date,
                end_date=end_date,
                file_path=catalog_path,
                size_bytes=size_bytes,
            ))
            created += 1

        logger.info("Scan: %s %s [%s..%s] %d bytes", symbol, interval, start_date, end_date, size_bytes)

    await db.commit()
    return {"status": "ok", "scanned": scanned, "created": created, "updated": updated}
