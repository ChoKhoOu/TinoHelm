"""Data catalog and fetch API routes."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.api.deps import get_db, get_redis, get_settings_dep
from tinohelm.core.config import Settings
from tinohelm.data.catalog_locks import catalog_lock_key, get_catalog_lock
from tinohelm.data.pipeline_helpers import WRITE_CATEGORY, resolve_db_interval
from tinohelm.data.worker import enqueue_job
from tinohelm.db.models import DataCatalog, DataFetchJob

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data"])


# ---- request / response schemas ----

class DataCatalogItem(BaseModel):
    """Single item in the live data catalog summary."""

    id: str
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


_PHASE1_DATA_TYPES: tuple[dict[str, Any], ...] = (
    {
        "data_type": "bar",
        "upstream_data_type": "klines",
        "db_category": "bar",
        "interval_required": True,
        "has_daily": True,
        "has_monthly": True,
    },
    {
        "data_type": "trade_tick",
        "upstream_data_type": "trades",
        "db_category": "trade_tick",
        "interval_required": False,
        "has_daily": True,
        "has_monthly": True,
    },
    {
        "data_type": "quote_tick",
        "upstream_data_type": "bookTicker",
        "db_category": "quote_tick",
        "interval_required": False,
        "has_daily": True,
        "has_monthly": True,
    },
    {
        "data_type": "mark_price",
        "upstream_data_type": "markPriceKlines",
        "db_category": "mark_price",
        "interval_required": True,
        "has_daily": True,
        "has_monthly": True,
    },
    {
        "data_type": "index_price",
        "upstream_data_type": "indexPriceKlines",
        "db_category": "index_price",
        "interval_required": True,
        "has_daily": True,
        "has_monthly": True,
    },
    {
        "data_type": "funding_rate",
        "upstream_data_type": "fundingRate",
        "db_category": "funding_rate",
        "interval_required": False,
        "has_daily": False,
        "has_monthly": True,
    },
)
_PHASE1_UPSTREAM_TO_PUBLIC = {
    item["upstream_data_type"]: item["data_type"] for item in _PHASE1_DATA_TYPES
}
_PHASE1_PUBLIC_TO_UPSTREAM = {
    item["data_type"]: item["upstream_data_type"] for item in _PHASE1_DATA_TYPES
}


class DataFetchBatchRequest(BaseModel):
    """Request body for POST /fetch-batch."""

    symbols: list[str]
    intervals: list[str] = ["1m"]
    start: date
    end: date
    data_type: str = "klines"
    asset_class: str = "um"


class CompactRequest(BaseModel):
    """Request body for POST /consolidate."""

    symbol: str
    interval: str
    data_type: str = "klines"


class ResetFileNamesRequest(BaseModel):
    pass


class ConsolidateByPeriodRequest(BaseModel):
    period: str


class DeleteRangeRequest(BaseModel):
    start: str | None = None
    end: str | None = None


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


def _normalize_requested_data_type(data_type: str) -> str:
    return _PHASE1_PUBLIC_TO_UPSTREAM.get(data_type, data_type)


def _public_data_type(data_type: str) -> str:
    return _PHASE1_UPSTREAM_TO_PUBLIC.get(data_type, data_type)


def _is_bar_data_type(data_type: str) -> bool:
    """Return True for kline-family data types that require bar intervals."""
    return WRITE_CATEGORY.get(_normalize_requested_data_type(data_type)) == "bar"


def _fetch_batch_intervals(data_type: str, intervals: list[str]) -> list[str]:
    """Return the legacy response intervals field; keep it string-only."""
    return intervals if _is_bar_data_type(data_type) else []


def _fetch_batch_job_intervals(data_type: str, intervals: list[str]) -> list[str | None]:
    """Return effective DB/job intervals for fetch-batch job creation."""
    if _is_bar_data_type(data_type):
        return [*intervals]
    return [None]


def _catalog_for_root(catalog_root: Path | str, storage: Any | None = None):
    from tinohelm.data.catalog import _catalog_for_root as _nt_catalog_for_root

    return _nt_catalog_for_root(catalog_root, storage)


def _catalog_for_maintenance(settings: Settings):
    from tinohelm.data.storage import get_active_catalog_root, get_catalog_storage

    base_catalog_path = get_active_catalog_root(settings) if settings else Path("data/catalog")
    storage = get_catalog_storage(settings=settings, catalog_root=base_catalog_path) if settings else get_catalog_storage(catalog_root=base_catalog_path)
    return _catalog_for_root(base_catalog_path, storage), storage


def _parse_period(value: str) -> timedelta:
    token = str(value).strip().lower()
    if not token:
        raise HTTPException(status_code=400, detail="period must not be empty")
    unit = token[-1]
    amount_raw = token[:-1]
    if unit not in {"d", "h", "m"} or not amount_raw.isdigit():
        raise HTTPException(status_code=400, detail=f"Unsupported period {value!r}")
    amount = int(amount_raw)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="period must be positive")
    if unit == "d":
        return timedelta(days=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(minutes=amount)


def _catalog_entry_id(*, symbol: str, data_type: str, interval: str, source_type: str | None) -> str:
    return "|".join([symbol, data_type, interval, source_type or ""])


def _parse_catalog_entry_id(catalog_id: str) -> tuple[str, str, str, str | None]:
    parts = catalog_id.split("|", 3)
    if len(parts) != 4:
        raise HTTPException(status_code=400, detail="Invalid catalog entry id")
    symbol, data_type, interval, source_type = parts
    if not symbol or not data_type or not interval:
        raise HTTPException(status_code=400, detail="Invalid catalog entry id")
    return symbol, data_type, interval, source_type or None


async def _run_catalog_maintenance(
    *,
    settings: Settings,
    verb: str,
    invoke: Callable[[Any], None],
) -> None:
    catalog, _ = _catalog_for_maintenance(settings)
    try:
        await asyncio.to_thread(invoke, catalog)
    except Exception as exc:
        logger.exception("Catalog maintenance failed for %s: %s", verb, exc)
        raise HTTPException(status_code=500, detail=f"Catalog maintenance failed: {verb}")


# ---- routes ----

@router.get("/catalog", response_model=list[DataCatalogItem])
async def list_data_catalog(
    settings: Settings = Depends(get_settings_dep),
) -> list[DataCatalogItem]:
    """List the live catalog summary derived from the current NT catalog."""
    from tinohelm.data.catalog import CatalogSession
    from tinohelm.data.storage import get_active_catalog_root, get_catalog_storage

    base_catalog_path = get_active_catalog_root(settings) if settings else Path("data/catalog")
    storage = get_catalog_storage(settings=settings, catalog_root=base_catalog_path) if settings else get_catalog_storage(catalog_root=base_catalog_path)
    session = CatalogSession(base_catalog_path, storage=storage)
    summary = await asyncio.to_thread(session.live_summary)
    entries = sorted(
        (
            entry
            for entry in summary.entries
            if entry.source_type in _PHASE1_UPSTREAM_TO_PUBLIC
        ),
        key=lambda item: (item.symbol, item.data_type, item.interval, item.source_type),
    )
    return [
        DataCatalogItem(
            id=_catalog_entry_id(
                symbol=entry.symbol,
                data_type=entry.data_type,
                interval=entry.interval,
                source_type=entry.source_type,
            ),
            symbol=entry.symbol,
            data_type=_PHASE1_UPSTREAM_TO_PUBLIC[entry.source_type],
            interval=entry.interval,
            start_date=entry.start_date,
            end_date=entry.end_date,
            file_path=entry.file_path,
            size_bytes=entry.size_bytes,
            record_count=entry.record_count,
            source_type=entry.source_type,
            created_at=None,
        )
        for entry in entries
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
            "data_type": _public_data_type(j.data_type),
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
        "data_type": _public_data_type(job.data_type),
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
        compact_session = CatalogSession(catalog_path, storage=storage)
        async with get_catalog_lock(lock_key):
            result, cancelled = await _await_critical_mutation(
                asyncio.to_thread(compact_session.compact_bars, symbol, interval)
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
    effective_data_type = _normalize_requested_data_type(body.data_type)
    if effective_data_type not in WRITE_CATEGORY:
        supported = ", ".join(sorted({*WRITE_CATEGORY, *tuple(_PHASE1_PUBLIC_TO_UPSTREAM)}))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported data_type {body.data_type!r}. Supported values: {supported}",
        )
    if _is_bar_data_type(body.data_type) and not body.intervals:
        raise HTTPException(status_code=400, detail="intervals must not be empty")

    public_data_type = _public_data_type(effective_data_type)
    job_ids: list[str] = []
    jobs: list[dict[str, str | None]] = []
    # One fetch-batch submission = one FetchBatch. All fanned-out DataFetchJob
    # rows share this batch_id so the scheduler can treat them as a unit.
    batch_id = str(uuid4())
    ranges = _split_fetch_date_ranges(
        data_type=effective_data_type,
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
                    data_type=effective_data_type,
                    interval=interval,
                    start_date=start_date,
                    end_date=end_date,
                    asset_class=body.asset_class,
                    status="queued",
                    batch_id=batch_id,
                )
                db.add(job)
                await db.flush()
                job_ids.append(job.job_id)
                jobs.append({
                    "job_id": job.job_id,
                    "data_type": public_data_type,
                    "db_interval": resolve_db_interval(effective_data_type, interval),
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
        "data_type": public_data_type,
        "start": body.start.isoformat(),
        "end": body.end.isoformat(),
        "count": len(job_ids),
    }


@router.post("/consolidate")
async def trigger_compact(
    body: CompactRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Trigger background consolidation for a symbol/interval."""
    if body.data_type not in WRITE_CATEGORY or not _is_bar_data_type(body.data_type):
        supported = ", ".join(sorted(k for k, v in WRITE_CATEGORY.items() if v == "bar"))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported bar data_type {body.data_type!r}. Supported values: {supported}",
        )
    background_tasks.add_task(_run_compact, body.symbol, body.interval, settings, body.data_type, body.data_type)
    return {
        "status": "accepted",
        "message": f"Consolidation for {body.symbol} {body.data_type} {body.interval} queued",
    }


@router.post("/reset-file-names")
async def reset_file_names(
    _: ResetFileNamesRequest,
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Reset file names across the entire active catalog."""
    await _run_catalog_maintenance(
        settings=settings,
        verb="reset-file-names",
        invoke=lambda catalog: catalog.reset_all_file_names(),
    )
    return {
        "status": "ok",
        "verb": "reset-file-names",
        "scope": "catalog",
    }


@router.post("/consolidate-by-period")
async def consolidate_by_period(
    body: ConsolidateByPeriodRequest,
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Consolidate files by period across the entire active catalog."""
    period = _parse_period(body.period)
    await _run_catalog_maintenance(
        settings=settings,
        verb="consolidate-by-period",
        invoke=lambda catalog: catalog.consolidate_catalog_by_period(
            period=period,
        ),
    )
    return {
        "status": "ok",
        "verb": "consolidate-by-period",
        "scope": "catalog",
        "period": body.period,
    }


@router.post("/delete-range")
async def delete_range(
    body: DeleteRangeRequest,
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Delete a time range across the entire active catalog."""
    await _run_catalog_maintenance(
        settings=settings,
        verb="delete-range",
        invoke=lambda catalog: catalog.delete_catalog_range(
            start=body.start,
            end=body.end,
        ),
    )
    return {
        "status": "ok",
        "verb": "delete-range",
        "scope": "catalog",
        "start": body.start,
        "end": body.end,
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




@router.delete("/catalog/{catalog_id}")
async def delete_catalog_entry(
    catalog_id: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Delete a data catalog entry and its underlying storage files."""
    symbol, data_type, interval, source_type = _parse_catalog_entry_id(catalog_id)
    stmt = select(DataCatalog).where(
        DataCatalog.symbol == symbol,
        DataCatalog.data_type == data_type,
        DataCatalog.interval == interval,
    )
    if source_type is None:
        stmt = stmt.where(DataCatalog.source_type.is_(None))
    else:
        stmt = stmt.where(DataCatalog.source_type == source_type)
    row = (await db.execute(stmt)).scalar_one_or_none()
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
    """Return the static phase-1 NT-native capability list."""
    return [
        {
            **item,
            "implemented": True,
        }
        for item in _PHASE1_DATA_TYPES
    ]


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
