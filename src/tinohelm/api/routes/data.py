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
    record_count: int | None = None
    created_at: str | None = None


class DataFetchRequest(BaseModel):
    """Request body for POST /fetch."""

    symbol: str
    interval: str | None = None
    start: date
    end: date
    data_type: str = "klines"
    asset_class: str = "um"


class DataFetchBatchRequest(BaseModel):
    """Request body for POST /fetch-batch."""

    symbols: list[str]
    intervals: list[str] = ["1m"]
    start: date
    end: date
    data_type: str = "klines"
    asset_class: str = "um"


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
    from tinohelm.strategy.loader import normalize_symbol
    nt_sym = normalize_symbol(symbol)
    nt_interval = _interval_to_nt(interval)
    bar_type_dir = Path(catalog_path) / "data" / "bar" / f"{nt_sym}-{nt_interval}-LAST-EXTERNAL"
    if not bar_type_dir.exists():
        return 0
    return sum(f.stat().st_size for f in bar_type_dir.glob("*.parquet"))


def _delete_storage_files(
    symbol: str, data_type: str, interval: str, catalog_path: str,
) -> tuple[int, int]:
    """Delete storage files for a catalog entry. Returns (deleted_files, freed_bytes)."""
    if data_type == "bar":
        from tinohelm.strategy.loader import normalize_symbol
        nt_sym = normalize_symbol(symbol)
        nt_interval = _interval_to_nt(interval)
        target_dir = Path(catalog_path) / "data" / "bar" / f"{nt_sym}-{nt_interval}-LAST-EXTERNAL"
    elif data_type == "trade_tick":
        from tinohelm.strategy.loader import normalize_symbol
        nt_sym = normalize_symbol(symbol)
        target_dir = Path(catalog_path) / "data" / "trade_tick" / nt_sym
    elif data_type == "funding_rate":
        from tinohelm.data.funding_cache import _CACHE_DIR
        json_path = _CACHE_DIR / f"{symbol.lower()}.json"
        if json_path.exists():
            size = json_path.stat().st_size
            json_path.unlink()
            return (1, size)
        return (0, 0)
    elif data_type == "quote_tick":
        from tinohelm.strategy.loader import normalize_symbol
        nt_sym = normalize_symbol(symbol)
        target_dir = Path(catalog_path) / "data" / "quote_tick" / nt_sym
    else:
        logger.warning("No storage handler for data_type=%r, removing DB row only", data_type)
        return (0, 0)

    if not target_dir.exists():
        return (0, 0)
    files = list(target_dir.glob("*.parquet"))
    total_size = sum(f.stat().st_size for f in files)
    for f in files:
        f.unlink()
    if target_dir.exists() and not list(target_dir.iterdir()):
        target_dir.rmdir()
    return (len(files), total_size)


# ---- background task ----

async def _run_data_fetch(
    symbol: str, interval: str | None, start: date, end: date,
    settings: Settings | None = None, task_id: str | None = None,
    data_type: str = "klines", asset_class: str = "um",
) -> None:
    """Background task to fetch market data via BinanceVisionPipeline."""
    import json as _json
    import redis.asyncio as aioredis

    redis_url = str(settings.redis.url) if settings else "redis://redis:6379"
    progress_channel = f"tino:data:progress:{task_id or f'{symbol}:{data_type}'}"
    r = aioredis.from_url(redis_url, decode_responses=True)

    async def _pub(pct: int, msg: str):
        payload: dict = {"symbol": symbol, "data_type": data_type, "progress": pct, "message": msg}
        if interval:
            payload["interval"] = interval
        if task_id:
            payload["task_id"] = task_id
        await r.publish(progress_channel, _json.dumps(payload))

    lock_key = f"tino:data:lock:{symbol}:{data_type}:{interval or 'none'}"
    lock = r.lock(lock_key, timeout=3600)
    acquired = await lock.acquire(blocking=False)
    if not acquired:
        await _pub(-1, f"{symbol} {data_type} 正在下载中，请等待完成后再试")
        await r.close()
        return

    try:
        from tinohelm.data.pipeline import BinanceVisionPipeline

        catalog_path = str(settings.paths.catalog) if settings else "data/catalog"
        pipeline = BinanceVisionPipeline(catalog_path=catalog_path)

        result = await pipeline.ingest(
            symbol=symbol,
            data_type=data_type,
            start=start,
            end=end,
            asset_class=asset_class,
            interval=interval,
            progress_cb=_pub,
        )

        logger.info(
            "Data fetch completed: %s %s — %d objects (skipped=%s, rest_fallback=%s)",
            symbol, data_type, result.objects_count,
            result.skipped, result.rest_fallback_used,
        )
    except Exception as exc:
        logger.exception("Data fetch failed: %s", exc)
    finally:
        await lock.release()
        await r.close()


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
            record_count=r.record_count,
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
    import uuid
    task_id = str(uuid.uuid4())
    background_tasks.add_task(
        _run_data_fetch, body.symbol, body.interval, body.start, body.end,
        settings, task_id, body.data_type, body.asset_class,
    )
    return {
        "status": "accepted",
        "task_id": task_id,
        "message": f"Data fetch for {body.symbol} {body.data_type} queued",
        "symbol": body.symbol,
        "data_type": body.data_type,
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
    count = 0
    for symbol in body.symbols:
        for interval in body.intervals:
            background_tasks.add_task(
                _run_data_fetch, symbol, interval, body.start, body.end,
                settings, None, body.data_type, body.asset_class,
            )
            count += 1
    return {
        "status": "accepted",
        "message": f"Data fetch for {count} task(s) queued ({len(body.symbols)} symbol(s) × {len(body.intervals)} interval(s))",
        "symbols": body.symbols,
        "intervals": body.intervals,
        "data_type": body.data_type,
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
        raise HTTPException(status_code=500, detail="Internal validation error")

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

    # Parse bar_type directory names:
    #   BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL
    pattern = re.compile(r"^(.+\.BINANCE)-(\d+-\w+)-LAST-EXTERNAL$")

    created = 0
    updated = 0
    scanned = 0

    if bar_dir.exists():
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
                record_count = len(bars)
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
                existing.record_count = record_count
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
                    record_count=record_count,
                ))
                created += 1

            logger.info("Scan: %s %s [%s..%s] %d bytes", symbol, interval, start_date, end_date, size_bytes)

    # Scan trade_tick directories
    trade_tick_dir = Path(catalog_path) / "data" / "trade_tick"
    if trade_tick_dir.exists():
        sym_pattern = re.compile(r"^(.+)\.BINANCE$")
        for entry in sorted(trade_tick_dir.iterdir()):
            if not entry.is_dir():
                continue
            m = sym_pattern.match(entry.name)
            if not m:
                continue
            symbol = m.group(1)
            parquet_files = list(entry.glob("*.parquet"))
            if not parquet_files:
                continue
            scanned += 1
            sz = sum(f.stat().st_size for f in parquet_files)

            try:
                import pyarrow.parquet as pq
                total_rows = 0
                ts_min_val, ts_max_val = float("inf"), 0
                for pf in parquet_files:
                    meta = pq.read_metadata(pf)
                    total_rows += meta.num_rows
                start_dt = datetime.fromtimestamp(ts_min_val / 1e9, tz=timezone.utc).date() if ts_min_val != float("inf") else None
                end_dt = datetime.fromtimestamp(ts_max_val / 1e9, tz=timezone.utc).date() if ts_max_val != 0 else None
            except Exception:
                # Fallback: use file modification times
                import os
                file_times = [os.path.getmtime(pf) for pf in parquet_files]
                start_dt = datetime.fromtimestamp(min(file_times), tz=timezone.utc).date()
                end_dt = datetime.fromtimestamp(max(file_times), tz=timezone.utc).date()
                total_rows = None

            if start_dt is None or end_dt is None:
                continue

            stmt = select(DataCatalog).where(
                DataCatalog.symbol == symbol,
                DataCatalog.data_type == "trade_tick",
                DataCatalog.interval == "tick",
            )
            existing = (await db.execute(stmt)).scalar_one_or_none()
            if existing:
                existing.start_date = min(existing.start_date, start_dt)
                existing.end_date = max(existing.end_date, end_dt)
                existing.size_bytes = sz
                if total_rows is not None:
                    existing.record_count = total_rows
                updated += 1
            else:
                db.add(DataCatalog(
                    symbol=symbol,
                    data_type="trade_tick",
                    interval="tick",
                    start_date=start_dt,
                    end_date=end_dt,
                    file_path=catalog_path,
                    size_bytes=sz,
                    record_count=total_rows,
                ))
                created += 1

    await db.commit()
    return {"status": "ok", "scanned": scanned, "created": created, "updated": updated}


@router.delete("/catalog/{catalog_id}")
async def delete_catalog_entry(
    catalog_id: int,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Delete a data catalog entry and its underlying storage files."""
    row = (await db.execute(
        select(DataCatalog).where(DataCatalog.id == catalog_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Catalog entry not found")

    deleted_files, freed_bytes = _delete_storage_files(
        row.symbol, row.data_type, row.interval,
        str(settings.paths.catalog),
    )

    await db.delete(row)
    await db.commit()
    return {
        "status": "deleted",
        "symbol": row.symbol,
        "data_type": row.data_type,
        "deleted_files": deleted_files,
        "freed_bytes": freed_bytes,
    }


@router.get("/types")
async def list_data_types() -> list[dict]:
    """Return supported data types and their availability."""
    from tinohelm.data.downloader import DATA_TYPE_AVAILABILITY
    from tinohelm.data.converters import CONVERTER_REGISTRY
    from tinohelm.data.pipeline import _WRITE_CATEGORY

    result = []
    for dt, (has_daily, has_monthly) in DATA_TYPE_AVAILABILITY.items():
        converter = CONVERTER_REGISTRY.get(dt)
        implemented = converter is not None
        try:
            if implemented and converter is not None:
                converter.convert.__func__  # check if it's not a stub
                # Stubs raise NotImplementedError — detect via class check
                from tinohelm.data.converters.book_ticker import BookTickerConverter
                from tinohelm.data.converters.book_depth import BookDepthConverter
                from tinohelm.data.converters.liquidation import LiquidationConverter
                from tinohelm.data.converters.metrics import MetricsConverter
                stub_types = (BookTickerConverter, BookDepthConverter, LiquidationConverter, MetricsConverter)
                if isinstance(converter, stub_types):
                    implemented = False
        except Exception:
            pass

        result.append({
            "data_type": dt,
            "has_daily": has_daily,
            "has_monthly": has_monthly,
            "implemented": implemented,
            "db_category": _WRITE_CATEGORY.get(dt, dt),
        })
    return result


@router.get("/coverage/{symbol}")
async def get_data_coverage(
    symbol: str,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Return data coverage ranges for a symbol across all data types."""
    stmt = (
        select(DataCatalog)
        .where(DataCatalog.symbol == symbol)
        .order_by(DataCatalog.data_type, DataCatalog.interval)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "data_type": r.data_type,
            "interval": r.interval,
            "start_date": r.start_date.isoformat(),
            "end_date": r.end_date.isoformat(),
            "size_bytes": r.size_bytes,
        }
        for r in rows
    ]
