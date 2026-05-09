"""Data catalog and fetch API routes."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.api.deps import get_db, get_redis, get_settings_dep
from tinohelm.core.config import Settings
from tinohelm.data.catalog import ScanEntry
from tinohelm.data.catalog_locks import catalog_lock_key, get_catalog_lock
from tinohelm.data.pipeline_helpers import WRITE_CATEGORY, resolve_db_interval
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
    data_type: str = "klines"


# ---- helpers ----

_LEGACY_DEFAULT_SOURCE = {
    "bar": "klines",
    "trade_tick": "aggTrades",
    "quote_tick": "bookTicker",
    "funding_rate": "fundingRate",
    "order_book_delta": "bookDepth",
    "liquidation": "liquidationSnapshot",
    "metrics": "metrics",
}


def _catalog_row_lock_key(row: Any) -> str:
    """Return the mutation lock key for one persisted catalog row."""
    source_type = getattr(row, "source_type", None)
    data_type = getattr(row, "data_type")
    effective_source = source_type or _LEGACY_DEFAULT_SOURCE.get(data_type) or data_type
    return catalog_lock_key(getattr(row, "symbol"), effective_source, getattr(row, "interval", None))


def _clear_current_task_cancellation() -> None:
    current_task = asyncio.current_task()
    while current_task is not None and current_task.cancelling():
        current_task.uncancel()


async def _await_critical_mutation(awaitable: Any) -> tuple[Any, bool]:
    """Finish storage/DB mutation even if the request/background task is cancelled."""
    task = asyncio.ensure_future(awaitable)
    was_cancelled = False
    while True:
        try:
            return await asyncio.shield(task), was_cancelled
        except asyncio.CancelledError:
            was_cancelled = True
            _clear_current_task_cancellation()
            if not task.done():
                continue
        except Exception:
            raise
        return task.result(), was_cancelled


async def _update_compact_catalog_row(
    *,
    symbol: str,
    interval: str,
    effective_source: str,
    total_size: int,
    bars_count: int | None,
) -> None:
    from tinohelm.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        stmt = select(DataCatalog).where(
            DataCatalog.symbol == symbol,
            DataCatalog.data_type == "bar",
            DataCatalog.interval == interval,
            DataCatalog.source_type == effective_source,
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing is None and effective_source == _LEGACY_DEFAULT_SOURCE["bar"]:
            legacy_stmt = select(DataCatalog).where(
                DataCatalog.symbol == symbol,
                DataCatalog.data_type == "bar",
                DataCatalog.interval == interval,
                DataCatalog.source_type.is_(None),
            )
            existing = (await db.execute(legacy_stmt)).scalar_one_or_none()
        if existing:
            existing.size_bytes = total_size
            if bars_count is not None:
                existing.record_count = bars_count
            await db.commit()


async def _delete_catalog_row_after_storage(db: AsyncSession, row: DataCatalog) -> None:
    await db.delete(row)
    await db.commit()


def _split_fetch_date_ranges(
    *,
    data_type: str,
    start: date,
    end: date,
    max_days_per_job: int,
) -> list[tuple[date, date]]:
    """Split large aggTrades requests into bounded inclusive date windows."""
    if data_type != "aggTrades" or max_days_per_job <= 0:
        return [(start, end)]
    ranges: list[tuple[date, date]] = []
    current = start
    step = timedelta(days=max_days_per_job - 1)
    while current <= end:
        chunk_end = min(current + step, end)
        ranges.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return ranges


def _is_bar_data_type(data_type: str) -> bool:
    """Return True for kline-family data types that require bar intervals."""
    return WRITE_CATEGORY.get(data_type) == "bar"


def _fetch_batch_intervals(data_type: str, intervals: list[str]) -> list[str]:
    """Return the legacy response intervals field; keep it string-only."""
    return intervals if _is_bar_data_type(data_type) else []


def _fetch_batch_job_intervals(data_type: str, intervals: list[str]) -> list[str | None]:
    """Return effective DB/job intervals for fetch-batch job creation."""
    return intervals if _is_bar_data_type(data_type) else [None]


def _compact_bars_with_storage(storage, symbol: str, interval: str, catalog_path: str | Path) -> dict:
    """Compact bars for local or remote catalog storage."""
    if getattr(storage, "provider", "local") == "local":
        from tinohelm.data.catalog import compact_bars

        return compact_bars(symbol=symbol, interval=interval, catalog_path=catalog_path)

    from tinohelm.data.catalog import _catalog_for_root, _make_bar_type, _make_instrument
    from tinohelm.data.catalog_helpers import dedupe_by_ts
    from tinohelm.data.storage import delete_prefix, promote_objects_with_rollback

    catalog_path = Path(catalog_path)
    instrument = _make_instrument(symbol)
    bar_type = _make_bar_type(instrument.id, interval)
    bar_dir = catalog_path / "data" / "bar" / str(bar_type)
    existing_objects = list(storage.iter_files(bar_dir, suffix=".parquet", recursive=False))
    files_before = len(existing_objects)
    size_before = sum(int(obj.size or 0) for obj in existing_objects)

    if files_before <= 1:
        logger.info("Compaction skipped for %s %s — only %d file(s)", symbol, interval, files_before)
        return {
            "files_before": files_before,
            "files_after": files_before,
            "bars_count": 0,
            "size_before": size_before,
            "size_after": size_before,
        }

    catalog = _catalog_for_root(catalog_path, storage)
    bars = catalog.bars(bar_types=[str(bar_type)])
    if not bars:
        logger.warning("No bars found for %s %s during compaction", symbol, interval)
        return {
            "files_before": files_before,
            "files_after": files_before,
            "bars_count": 0,
            "size_before": size_before,
            "size_after": size_before,
        }

    bars = dedupe_by_ts(bars)
    logger.info("Compacting remote %s %s: %d files -> %d bars", symbol, interval, files_before, len(bars))

    temp_catalog_path = catalog_path / ".compaction" / f"{bar_type}-{uuid4().hex}"
    rollback_prefix = catalog_path / ".compaction-rollback" / f"{bar_type}-{uuid4().hex}"
    try:
        temp_catalog = _catalog_for_root(temp_catalog_path, storage)
        temp_catalog.write_data([instrument])
        temp_catalog.write_data(bars)

        temp_bar_dir = temp_catalog_path / "data" / "bar" / str(bar_type)
        temp_objects = list(storage.iter_files(temp_bar_dir, suffix=".parquet", recursive=False))
        if not temp_objects:
            raise RuntimeError(f"Remote compaction produced no parquet files for {symbol} {interval}")

        promote_objects_with_rollback(
            storage,
            temp_objects,
            bar_dir,
            existing_objects,
            rollback_prefix=rollback_prefix,
        )
    finally:
        try:
            delete_prefix(storage, temp_catalog_path)
        except Exception:
            logger.warning("Failed to clean temporary remote compaction prefix %s", temp_catalog_path, exc_info=True)

    new_objects = list(storage.iter_files(bar_dir, suffix=".parquet", recursive=False))
    if not new_objects:
        raise RuntimeError(f"Remote compaction produced no parquet files for {symbol} {interval}")
    size_after = sum(int(obj.size or 0) for obj in new_objects)

    return {
        "files_before": files_before,
        "files_after": len(new_objects),
        "bars_count": len(bars),
        "size_before": size_before,
        "size_after": size_after,
    }

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
    """Cancel a queued data-fetch job.

    Running jobs are not cooperatively cancellable yet; refusing them avoids
    reporting ``cancelled`` while the ingest continues mutating catalog files.
    """
    result = await db.execute(
        update(DataFetchJob)
        .where(DataFetchJob.job_id == job_id, DataFetchJob.status == "queued")
        .values(status="cancelled")
    )
    await db.commit()
    if result.rowcount == 1:  # type: ignore[union-attr]
        return {"status": "cancelled", "job_id": job_id}

    job = (await db.execute(
        select(DataFetchJob).where(DataFetchJob.job_id == job_id)
    )).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "running":
        raise HTTPException(
            status_code=409,
            detail="Running data-fetch jobs cannot be cancelled safely; wait for completion",
        )
    raise HTTPException(status_code=404, detail="Job already finished")


async def _run_compact(
    symbol: str,
    interval: str,
    settings: Settings,
    data_type: str = "klines",
    source_type: str | None = None,
) -> None:
    """Background task to compact Parquet files and update DB catalog size."""
    try:
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.data.storage import get_active_catalog_root, get_catalog_storage
        base_catalog_path = get_active_catalog_root(settings) if settings else Path("data/catalog")
        storage = get_catalog_storage(settings=settings, catalog_root=base_catalog_path)
        session = CatalogSession(base_catalog_path, storage=storage)
        effective_source = source_type or data_type
        catalog_path = str(
            session.resolve_bar_catalog_path(effective_source, symbol, interval)
        )
        lock_key = catalog_lock_key(symbol, effective_source, interval)
        was_cancelled = False
        async with get_catalog_lock(lock_key):
            result, cancelled = await _await_critical_mutation(
                asyncio.to_thread(_compact_bars_with_storage, storage, symbol, interval, catalog_path)
            )
            was_cancelled = was_cancelled or cancelled

            # Compact only rewrote the layout that resolve_bar_catalog_path
            # picked, but the DB row describes *both* layouts — re-sum across
            # source-aware + legacy flat so we don't drop the untouched side.
            merged, cancelled = await _await_critical_mutation(
                asyncio.to_thread(
                    session.merged_bar_stats,
                    symbol,
                    interval,
                    source_type=effective_source,
                )
            )
            was_cancelled = was_cancelled or cancelled
            total_size = (
                merged["size_bytes"] if merged is not None else result.get("size_after", 0)
            )
            # Prefer the merged record_count when both sides report rows; fall
            # back to compact's own count if metadata was unreadable (e.g.
            # empty/corrupt parquet) so we don't erase a healthy prior value.
            if merged is not None and merged["record_count"] is not None:
                bars_count = merged["record_count"]
            else:
                bars_count = result.get("bars_count")

            _, cancelled = await _await_critical_mutation(_update_compact_catalog_row(
                symbol=symbol,
                interval=interval,
                effective_source=effective_source,
                total_size=total_size,
                bars_count=bars_count,
            ))
            was_cancelled = was_cancelled or cancelled

        if was_cancelled:
            raise asyncio.CancelledError

        logger.info(
            "Compaction background task done: %s %s %s — %d bars, %d -> %d bytes",
            symbol, data_type, interval, result["bars_count"], result["size_before"], result["size_after"],
        )
    except Exception as exc:
        logger.exception("Compaction failed for %s %s %s: %s", symbol, data_type, interval, exc)


@router.post("/fetch-batch")
async def trigger_data_fetch_batch(
    body: DataFetchBatchRequest,
    db: AsyncSession = Depends(get_db),
    rds: aioredis.Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Create persistent data-fetch jobs for multiple symbols."""
    if not body.symbols:
        raise HTTPException(status_code=400, detail="symbols must not be empty")
    if body.start > body.end:
        raise HTTPException(status_code=400, detail="start must be on or before end")
    if body.data_type not in WRITE_CATEGORY:
        supported = ", ".join(sorted(WRITE_CATEGORY))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported data_type {body.data_type!r}. Supported values: {supported}",
        )
    if _is_bar_data_type(body.data_type) and not body.intervals:
        raise HTTPException(status_code=400, detail="intervals must not be empty")

    job_ids: list[str] = []
    jobs: list[dict[str, str | None]] = []
    ranges = _split_fetch_date_ranges(
        data_type=body.data_type,
        start=body.start,
        end=body.end,
        max_days_per_job=settings.data.agg_trades_max_days_per_job,
    )
    effective_intervals = _fetch_batch_job_intervals(body.data_type, body.intervals)
    for symbol in body.symbols:
        for interval in effective_intervals:
            for start_date, end_date in ranges:
                job = DataFetchJob(
                    symbol=symbol,
                    data_type=body.data_type,
                    interval=interval,
                    start_date=start_date,
                    end_date=end_date,
                    asset_class=body.asset_class,
                    status="queued",
                )
                db.add(job)
                await db.flush()
                job_ids.append(job.job_id)
                jobs.append({
                    "job_id": job.job_id,
                    "data_type": body.data_type,
                    "db_interval": resolve_db_interval(body.data_type, interval),
                    "interval": interval,
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat(),
                })

    await db.commit()

    for jid in job_ids:
        await enqueue_job(rds, jid)

    return {
        "status": "accepted",
        "message": f"Data fetch for {len(job_ids)} job(s) queued ({len(body.symbols)} symbol(s) × {len(effective_intervals)} effective interval(s))",
        "job_ids": job_ids,
        "jobs": jobs,
        "symbols": body.symbols,
        "intervals": _fetch_batch_intervals(body.data_type, body.intervals),
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
    if body.data_type not in WRITE_CATEGORY or not _is_bar_data_type(body.data_type):
        supported = ", ".join(sorted(k for k, v in WRITE_CATEGORY.items() if v == "bar"))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported bar data_type {body.data_type!r}. Supported values: {supported}",
        )
    background_tasks.add_task(_run_compact, body.symbol, body.interval, settings, body.data_type, body.data_type)
    return {
        "status": "accepted",
        "message": f"Compaction for {body.symbol} {body.data_type} {body.interval} queued",
    }


@router.get("/validate/{symbol}/{interval}")
async def validate_data(
    symbol: str,
    interval: str,
    data_type: str = "klines",
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Validate data integrity for a symbol/interval. Returns synchronously."""
    from tinohelm.data.catalog import validate_bars

    if data_type not in WRITE_CATEGORY or not _is_bar_data_type(data_type):
        supported = ", ".join(sorted(k for k, v in WRITE_CATEGORY.items() if v == "bar"))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported bar data_type {data_type!r}. Supported values: {supported}",
        )

    from tinohelm.data.catalog import CatalogSession
    from tinohelm.data.storage import get_active_catalog_root, get_catalog_storage
    base_catalog_path = get_active_catalog_root(settings) if settings else Path("data/catalog")
    storage = get_catalog_storage(settings=settings, catalog_root=base_catalog_path) if settings else get_catalog_storage(catalog_root=base_catalog_path)
    session = CatalogSession(base_catalog_path, storage=storage)
    catalog_path = str(session.resolve_bar_catalog_path(data_type, symbol, interval))
    try:
        result = await asyncio.to_thread(
            validate_bars,
            symbol=symbol,
            interval=interval,
            catalog_path=catalog_path,
            storage=storage,
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

    Discovery + per-symbol aggregation live on ``CatalogSession``; this route
    owns only the DB upsert, including the legacy ``source_type IS NULL`` fallback.
    """
    from tinohelm.data.catalog import CatalogSession
    from tinohelm.data.storage import get_active_catalog_root, get_catalog_storage

    catalog_path = str(get_active_catalog_root(settings))
    storage = get_catalog_storage(settings=settings)
    session = CatalogSession(catalog_path, storage=storage)

    bar_result = await asyncio.to_thread(session.scan_bars)
    tick_result = await asyncio.to_thread(session.scan_ticks)
    single_file_result = await asyncio.to_thread(session.scan_single_files)

    created = 0
    updated = 0
    for entry in (
        *bar_result.entries,
        *tick_result.entries,
        *single_file_result.entries,
    ):
        created_delta, updated_delta = await _upsert_scan_entry(db, entry)
        created += created_delta
        updated += updated_delta

    await db.commit()
    return {
        "status": "ok",
        "scanned": (
            bar_result.scanned
            + tick_result.scanned
            + single_file_result.scanned
        ),
        "created": created,
        "updated": updated,
    }


async def _upsert_scan_entry(db: AsyncSession, entry: ScanEntry) -> tuple[int, int]:
    """Upsert one scan entry, falling back to the legacy ``source_type IS NULL`` row.

    The fallback only applies when the entry's source_type is the legacy
    default for its data_type — matches the rule the old in-route loop used.
    """
    stmt = select(DataCatalog).where(
        DataCatalog.symbol == entry.symbol,
        DataCatalog.data_type == entry.data_type,
        DataCatalog.interval == entry.interval,
        DataCatalog.source_type == entry.source_type,
    )
    existing_row = (await db.execute(stmt)).scalar_one_or_none()
    if existing_row is None and entry.source_type == _LEGACY_DEFAULT_SOURCE.get(entry.data_type):
        stmt_null = select(DataCatalog).where(
            DataCatalog.symbol == entry.symbol,
            DataCatalog.data_type == entry.data_type,
            DataCatalog.interval == entry.interval,
            DataCatalog.source_type.is_(None),
        )
        existing_row = (await db.execute(stmt_null)).scalar_one_or_none()
    if existing_row is not None:
        existing_row.start_date = min(existing_row.start_date, entry.start_date)
        existing_row.end_date = max(existing_row.end_date, entry.end_date)
        existing_row.size_bytes = entry.size_bytes
        existing_row.record_count = entry.record_count
        existing_row.file_path = entry.file_path
        existing_row.source_type = entry.source_type
        return 0, 1
    db.add(DataCatalog(
        symbol=entry.symbol,
        data_type=entry.data_type,
        interval=entry.interval,
        start_date=entry.start_date,
        end_date=entry.end_date,
        file_path=entry.file_path,
        size_bytes=entry.size_bytes,
        record_count=entry.record_count,
        source_type=entry.source_type,
    ))
    return 1, 0


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

    from tinohelm.data.catalog import CatalogSession
    from tinohelm.data.storage import get_active_catalog_root
    session = CatalogSession(str(get_active_catalog_root(settings)))
    was_cancelled = False
    async with get_catalog_lock(_catalog_row_lock_key(row)):
        (deleted_files, freed_bytes), cancelled = await _await_critical_mutation(asyncio.to_thread(
            session.delete_storage,
            row.symbol,
            row.data_type,
            row.interval,
            source_type=row.source_type,
        ))
        was_cancelled = was_cancelled or cancelled

        _, cancelled = await _await_critical_mutation(_delete_catalog_row_after_storage(db, row))
        was_cancelled = was_cancelled or cancelled
    if was_cancelled:
        raise asyncio.CancelledError
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
    from tinohelm.data.pipeline_helpers import resolve_db_category

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
            "db_category": resolve_db_category(dt),
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
            "source_type": r.source_type,
            "interval": r.interval,
            "start_date": r.start_date.isoformat(),
            "end_date": r.end_date.isoformat(),
            "size_bytes": r.size_bytes,
        }
        for r in rows
    ]
