"""Data catalog and fetch API routes."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.api.deps import get_db, get_redis, get_settings_dep
from tinohelm.core.config import Settings
from tinohelm.data.worker import enqueue_job
from tinohelm.db.models import DataCatalog, DataFetchJob

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
    source_type: str | None = None
    created_at: str | None = None


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
            source_type=r.source_type,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.get("/jobs")
async def list_data_fetch_jobs(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List data-fetch jobs, optionally filtered by status."""
    stmt = select(DataFetchJob).order_by(DataFetchJob.created_at.desc()).limit(100)
    if status:
        stmt = stmt.where(DataFetchJob.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "job_id": j.job_id,
            "symbol": j.symbol,
            "data_type": j.data_type,
            "interval": j.interval,
            "start_date": j.start_date.isoformat(),
            "end_date": j.end_date.isoformat(),
            "status": j.status,
            "progress": j.progress,
            "message": j.message,
            "error": j.error,
            "created_at": (j.created_at.isoformat() + "Z") if j.created_at else None,
            "completed_at": (j.completed_at.isoformat() + "Z") if j.completed_at else None,
        }
        for j in rows
    ]


@router.get("/jobs/{job_id}")
async def get_data_fetch_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get status of a single data-fetch job."""
    job = (await db.execute(
        select(DataFetchJob).where(DataFetchJob.job_id == job_id)
    )).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job.job_id,
        "symbol": job.symbol,
        "data_type": job.data_type,
        "interval": job.interval,
        "start_date": job.start_date.isoformat(),
        "end_date": job.end_date.isoformat(),
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "error": job.error,
        "created_at": (job.created_at.isoformat() + "Z") if job.created_at else None,
        "completed_at": (job.completed_at.isoformat() + "Z") if job.completed_at else None,
    }


@router.post("/jobs/{job_id}/cancel")
async def cancel_data_fetch_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Cancel a queued data-fetch job."""
    from sqlalchemy import update as sa_update
    result = await db.execute(
        sa_update(DataFetchJob)
        .where(DataFetchJob.job_id == job_id, DataFetchJob.status.in_(["queued", "running"]))
        .values(status="cancelled")
    )
    await db.commit()
    if result.rowcount == 0:  # type: ignore[union-attr]
        raise HTTPException(status_code=404, detail="Job not found or already finished")
    return {"status": "cancelled", "job_id": job_id}


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
    db: AsyncSession = Depends(get_db),
    rds: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Create persistent data-fetch jobs for multiple symbols."""
    if not body.symbols:
        raise HTTPException(status_code=400, detail="symbols must not be empty")

    job_ids: list[str] = []
    for symbol in body.symbols:
        for interval in body.intervals:
            job = DataFetchJob(
                symbol=symbol,
                data_type=body.data_type,
                interval=interval,
                start_date=body.start,
                end_date=body.end,
                asset_class=body.asset_class,
                status="queued",
            )
            db.add(job)
            await db.flush()
            job_ids.append(job.job_id)

    await db.commit()

    for jid in job_ids:
        await enqueue_job(rds, jid)

    return {
        "status": "accepted",
        "message": f"Data fetch for {len(job_ids)} job(s) queued ({len(body.symbols)} symbol(s) × {len(body.intervals)} interval(s))",
        "job_ids": job_ids,
        "symbols": body.symbols,
        "intervals": body.intervals,
        "data_type": body.data_type,
        "start": body.start.isoformat(),
        "end": body.end.isoformat(),
        "count": len(job_ids),
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
            existing_rows = (await db.execute(stmt)).scalars().all()
            if existing_rows:
                for row in existing_rows:
                    row.start_date = min(row.start_date, start_date)
                    row.end_date = max(row.end_date, end_date)
                    row.size_bytes = size_bytes
                    row.record_count = record_count
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

    # Scan trade_tick directories (resolve per source_type: aggTrades, trades)
    from tinohelm.data.catalog import resolve_catalog_path
    _tick_source_types = ("aggTrades", "trades")
    seen_tick_symbols: set[str] = set()
    sym_pattern = re.compile(r"^(.+)\.BINANCE$")
    for _src_type in _tick_source_types:
        resolved_path = resolve_catalog_path(catalog_path, _src_type)
        trade_tick_dir = Path(resolved_path) / "data" / "trade_tick"
        if not trade_tick_dir.exists():
            continue
        for entry in sorted(trade_tick_dir.iterdir()):
            if not entry.is_dir():
                continue
            m = sym_pattern.match(entry.name)
            if not m:
                continue
            symbol = m.group(1)
            if symbol in seen_tick_symbols:
                continue
            seen_tick_symbols.add(symbol)
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
                    for rg_idx in range(meta.num_row_groups):
                        rg = meta.row_group(rg_idx)
                        for col_idx in range(rg.num_columns):
                            col = rg.column(col_idx)
                            if col.path_in_schema == "ts_init" and col.statistics and col.statistics.has_min_max:
                                ts_min_val = min(ts_min_val, col.statistics.min)
                                ts_max_val = max(ts_max_val, col.statistics.max)
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
            existing_rows = (await db.execute(stmt)).scalars().all()
            if existing_rows:
                for row in existing_rows:
                    row.start_date = min(row.start_date, start_dt)
                    row.end_date = max(row.end_date, end_dt)
                    row.size_bytes = sz
                    if total_rows is not None:
                        row.record_count = total_rows
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


@router.get("/symbols")
async def list_symbols() -> list[dict]:
    """Return all available Binance Futures perpetual symbols."""
    from tinohelm.data.instruments import fetch_exchange_info

    try:
        info = fetch_exchange_info()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch exchange info: {exc}")

    symbols = []
    for s in info.get("symbols", []):
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("status") != "TRADING":
            continue
        raw = s.get("symbol", "")  # e.g. "BTCUSDT"
        pair = s.get("pair", "")   # e.g. "BTCUSDT"
        quote = s.get("quoteAsset", "")
        base = s.get("baseAsset", "")
        # TinoHelm convention: BTCUSDT-PERP
        tino_symbol = f"{raw}-PERP"
        symbols.append({
            "symbol": tino_symbol,
            "base": base,
            "quote": quote,
            "pair": pair,
        })

    symbols.sort(key=lambda x: x["symbol"])
    return symbols


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
