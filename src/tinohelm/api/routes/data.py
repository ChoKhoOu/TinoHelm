"""Data catalog and fetch API routes."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.api.deps import get_db, get_redis, get_settings_dep
from tinohelm.core.config import Settings
from tinohelm.data.pipeline_helpers import WRITE_CATEGORY, resolve_db_interval
from tinohelm.data.storage import stage_prefix_for_local_consumer
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

_UNIT_MAP = {"m": "MINUTE", "h": "HOUR", "d": "DAY"}
_UNIT_REVERSE = {v: k for k, v in _UNIT_MAP.items()}
_LEGACY_DEFAULT_SOURCE = {
    "bar": "klines",
    "trade_tick": "aggTrades",
    "quote_tick": "bookTicker",
}


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


def _parquet_stat_value_to_ns(value: Any) -> int:
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1_000_000_000)
    return int(value)


def _parquet_object_stats(path_or_object, storage=None) -> dict[str, Any] | None:
    """Return row-count/date/size stats from one parquet object metadata."""
    try:
        import pyarrow.parquet as pq

        if storage is not None and getattr(storage, "provider", "local") != "local":
            with storage.open_input_file(path_or_object) as fh:
                metadata = pq.ParquetFile(fh).metadata
        else:
            path = path_or_object.path if hasattr(path_or_object, "path") else path_or_object
            metadata = pq.read_metadata(Path(path))
    except Exception:
        return None

    total_rows = int(metadata.num_rows)
    ts_min: int | None = None
    ts_max: int | None = None
    for rg_idx in range(metadata.num_row_groups):
        rg = metadata.row_group(rg_idx)
        for col_idx in range(rg.num_columns):
            col = rg.column(col_idx)
            if col.path_in_schema not in {"ts_event", "ts_init"}:
                continue
            if col.statistics and col.statistics.has_min_max:
                stat_min = _parquet_stat_value_to_ns(col.statistics.min)
                stat_max = _parquet_stat_value_to_ns(col.statistics.max)
                ts_min = stat_min if ts_min is None else min(ts_min, stat_min)
                ts_max = stat_max if ts_max is None else max(ts_max, stat_max)
    size = getattr(path_or_object, "size", None)
    if size is None:
        path = getattr(path_or_object, "path", path_or_object)
        try:
            size = Path(path).stat().st_size
        except OSError:
            size = 0
    return {"rows": total_rows, "ts_min": ts_min, "ts_max": ts_max, "size": int(size or 0)}


def _last_modified_date(obj) -> date | None:
    value = getattr(obj, "last_modified", None)
    if value is None:
        path = getattr(obj, "path", None)
        if path is not None:
            try:
                return datetime.fromtimestamp(Path(path).stat().st_mtime, tz=timezone.utc).date()
            except OSError:
                return None
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return dt.date()
    if isinstance(value, (int, float)):
        if value > 10_000_000_000_000_000:  # nanoseconds
            scale = 1_000_000_000
        elif value > 10_000_000_000_000:  # microseconds
            scale = 1_000_000
        elif value > 10_000_000_000:  # milliseconds
            scale = 1_000
        else:
            scale = 1
        return datetime.fromtimestamp(value / scale, tz=timezone.utc).date()
    return None


def _group_parquet_objects_by_child_dir(storage, root: Path) -> dict[Path, list]:
    """Group parquet objects under ``root/<child>/*.parquet`` by child dir."""
    groups: dict[Path, list] = {}
    for obj in storage.iter_files(root, suffix=".parquet", recursive=True):
        try:
            rel = obj.path.relative_to(root)
        except ValueError:
            continue
        if len(rel.parts) < 2:
            continue
        groups.setdefault(root / rel.parts[0], []).append(obj)
    return groups


def _aggregate_parquet_object_stats(objects: list, storage) -> dict[str, Any] | None:
    """Aggregate size, row count, and date coverage from parquet metadata."""
    total_size = 0
    total_rows = 0
    all_rows_known = True
    min_ts: int | None = None
    max_ts: int | None = None
    fallback_dates: list[date] = []

    for obj in objects:
        stats = _parquet_object_stats(obj, storage=storage)
        if stats is None:
            all_rows_known = False
            if getattr(obj, "size", None) is not None:
                total_size += int(obj.size)
            fallback = _last_modified_date(obj)
            if fallback is not None:
                fallback_dates.append(fallback)
            continue

        total_size += int(stats["size"])
        rows = stats.get("rows")
        if rows is None:
            all_rows_known = False
        elif all_rows_known:
            total_rows += int(rows)
        obj_min = stats.get("ts_min")
        obj_max = stats.get("ts_max")
        if obj_min is None or obj_max is None:
            fallback = _last_modified_date(obj)
            if fallback is not None:
                fallback_dates.append(fallback)
        else:
            min_ts = int(obj_min) if min_ts is None else min(min_ts, int(obj_min))
            max_ts = int(obj_max) if max_ts is None else max(max_ts, int(obj_max))

    if min_ts is not None and max_ts is not None:
        start_dt = datetime.fromtimestamp(min_ts // 1_000_000_000, tz=timezone.utc).date()
        end_dt = datetime.fromtimestamp(max_ts // 1_000_000_000, tz=timezone.utc).date()
    elif fallback_dates:
        start_dt = min(fallback_dates)
        end_dt = max(fallback_dates)
    else:
        return None

    return {
        "start_date": start_dt,
        "end_date": end_dt,
        "size_bytes": total_size,
        "record_count": total_rows if all_rows_known else None,
        "all_record_counts_known": all_rows_known,
    }


def _bar_parquet_objects(catalog_path: str | Path, symbol: str, interval: str, storage=None) -> list:
    from tinohelm.strategy.loader import normalize_symbol
    from tinohelm.data.storage import get_catalog_storage

    storage = storage or get_catalog_storage(catalog_root=catalog_path)
    nt_sym = normalize_symbol(symbol)
    nt_interval = _interval_to_nt(interval)
    bar_type_dir = Path(catalog_path) / "data" / "bar" / f"{nt_sym}-{nt_interval}-LAST-EXTERNAL"
    return list(storage.iter_files(bar_type_dir, suffix=".parquet", recursive=False))


def _bar_parquet_files(catalog_path: str | Path, symbol: str, interval: str) -> list[Path]:
    return [obj.path for obj in _bar_parquet_objects(catalog_path, symbol, interval)]


def _parquet_size_for(catalog_path: str, symbol: str, interval: str, storage=None) -> int:
    """Calculate total Parquet size for a specific symbol/interval."""
    total = 0
    for obj in _bar_parquet_objects(catalog_path, symbol, interval, storage=storage):
        if getattr(obj, "size", None) is not None:
            total += int(obj.size)
        else:
            try:
                total += obj.path.stat().st_size
            except OSError:
                pass
    return total


def _bar_catalog_path_for(
    base_catalog_path: str | Path,
    data_type: str,
    symbol: str,
    interval: str,
    source_type: str | None = None,
    storage=None,
) -> str:
    """Resolve a bar catalog root, falling back to legacy flat default-source files."""
    from tinohelm.data.catalog_helpers import resolve_catalog_path

    effective_source = source_type or data_type
    resolved = resolve_catalog_path(base_catalog_path, effective_source)
    if effective_source == _LEGACY_DEFAULT_SOURCE["bar"]:
        if not _bar_parquet_objects(resolved, symbol, interval, storage=storage) and _bar_parquet_objects(base_catalog_path, symbol, interval, storage=storage):
            return str(base_catalog_path)
    return str(resolved)


def _delete_storage_files(
    symbol: str, data_type: str, interval: str, catalog_path: str, source_type: str | None = None,
) -> tuple[int, int]:
    """Delete storage files for a catalog entry. Returns (deleted_files, freed_bytes)."""
    from tinohelm.data.catalog_helpers import resolve_catalog_path
    from tinohelm.data.storage import delete_prefix, get_catalog_storage

    storage = get_catalog_storage(catalog_root=catalog_path)

    def _target_roots(category: str) -> list[Path]:
        base = Path(catalog_path)
        if not source_type:
            return [base]

        resolved = resolve_catalog_path(catalog_path, source_type)
        if source_type == _LEGACY_DEFAULT_SOURCE.get(category):
            return [resolved, base]
        if resolved == base:
            return []
        return [resolved]

    def _delete_parquet_dirs(target_dirs: list[Path]) -> tuple[int, int]:
        deleted_files = 0
        freed_bytes = 0
        seen: set[Path] = set()
        for target_dir in target_dirs:
            if target_dir in seen:
                continue
            seen.add(target_dir)
            if getattr(storage, "provider", "local") != "local":
                remote_deleted, remote_freed = delete_prefix(storage, target_dir)
                deleted_files += remote_deleted
                freed_bytes += remote_freed
                continue
            stage_prefix_for_local_consumer(storage, target_dir)
            if not target_dir.exists():
                continue
            files = list(target_dir.glob("*.parquet"))
            total_size = sum(f.stat().st_size for f in files)
            for f in files:
                f.unlink(missing_ok=True)
            if target_dir.exists() and not list(target_dir.iterdir()):
                target_dir.rmdir()
            deleted_files += len(files)
            freed_bytes += total_size
        return deleted_files, freed_bytes

    def _delete_parquet_files(target_files: list[Path]) -> tuple[int, int]:
        deleted_files = 0
        freed_bytes = 0
        seen: set[Path] = set()
        for target_file in target_files:
            if target_file in seen:
                continue
            seen.add(target_file)
            if getattr(storage, "provider", "local") != "local":
                remote_deleted, remote_freed = delete_prefix(storage, target_file)
                deleted_files += remote_deleted
                freed_bytes += remote_freed
                continue
            storage.materialize_path(target_file)
            if not target_file.exists():
                continue
            size = target_file.stat().st_size
            target_file.unlink()
            deleted_files += 1
            freed_bytes += size
        return deleted_files, freed_bytes

    if data_type == "bar":
        from tinohelm.strategy.loader import normalize_symbol
        nt_sym = normalize_symbol(symbol)
        nt_interval = _interval_to_nt(interval)
        dir_name = f"{nt_sym}-{nt_interval}-LAST-EXTERNAL"
        return _delete_parquet_dirs([root / "data" / "bar" / dir_name for root in _target_roots("bar")])
    elif data_type == "trade_tick":
        from tinohelm.strategy.loader import normalize_symbol
        nt_sym = normalize_symbol(symbol)
        return _delete_parquet_dirs([root / "data" / "trade_tick" / nt_sym for root in _target_roots("trade_tick")])
    elif data_type == "metrics":
        from tinohelm.data.catalog import metrics_parquet_path

        return _delete_parquet_files([metrics_parquet_path(symbol, catalog_path)])
    elif data_type == "order_book_delta":
        from tinohelm.data.catalog import book_depth_parquet_path

        return _delete_parquet_files([book_depth_parquet_path(symbol, catalog_path)])
    elif data_type == "funding_rate":
        from tinohelm.core.paths import paths

        from tinohelm.data.catalog import funding_rate_parquet_path

        deleted_files, freed_bytes = _delete_parquet_files([funding_rate_parquet_path(symbol, catalog_path)])
        json_path = paths.get("funding_rates") / f"{symbol.lower()}.json"
        if json_path.exists():
            size = json_path.stat().st_size
            json_path.unlink()
            deleted_files += 1
            freed_bytes += size
        return (deleted_files, freed_bytes)
    elif data_type == "quote_tick":
        from tinohelm.strategy.loader import normalize_symbol
        nt_sym = normalize_symbol(symbol)
        return _delete_parquet_dirs([root / "data" / "quote_tick" / nt_sym for root in _target_roots("quote_tick")])
    else:
        logger.warning("No storage handler for data_type=%r, removing DB row only", data_type)
        return (0, 0)


def _compact_bars_with_storage(storage, symbol: str, interval: str, catalog_path: str | Path) -> dict:
    """Compact bars for local or remote catalog storage."""
    if getattr(storage, "provider", "local") == "local":
        from tinohelm.data.catalog import compact_bars

        return compact_bars(symbol=symbol, interval=interval, catalog_path=catalog_path)

    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    from tinohelm.data.catalog import _make_bar_type, _make_instrument
    from tinohelm.data.catalog_helpers import dedupe_by_ts
    from tinohelm.data.storage import delete_prefix, upload_paths

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

    catalog_uri = storage.uri_for_catalog_root(catalog_path)
    catalog = ParquetDataCatalog.from_uri(
        catalog_uri,
        fs_storage_options=getattr(storage, "fs_storage_options", None),
        fs_rust_storage_options=getattr(storage, "fs_rust_storage_options", None),
    )
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
    if bar_dir.exists():
        for local_file in bar_dir.glob("*.parquet"):
            local_file.unlink(missing_ok=True)
    bar_dir.mkdir(parents=True, exist_ok=True)

    local_catalog = ParquetDataCatalog(str(catalog_path))
    local_catalog.write_data([instrument])
    local_catalog.write_data(bars)
    new_files = list(bar_dir.glob("*.parquet")) if bar_dir.exists() else []
    if not new_files:
        raise RuntimeError(f"Remote compaction produced no parquet files for {symbol} {interval}")
    size_after = sum(path.stat().st_size for path in new_files)

    delete_prefix(storage, bar_dir)
    upload_paths(storage, new_files)

    return {
        "files_before": files_before,
        "files_after": len(new_files),
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


async def _run_compact(
    symbol: str,
    interval: str,
    settings: Settings,
    data_type: str = "klines",
    source_type: str | None = None,
) -> None:
    """Background task to compact Parquet files and update DB catalog size."""
    try:
        from tinohelm.db.session import get_session_factory

        from tinohelm.data.storage import get_active_catalog_root, get_catalog_storage
        base_catalog_path = get_active_catalog_root(settings) if settings else Path("data/catalog")
        storage = get_catalog_storage(settings=settings, catalog_root=base_catalog_path)
        effective_source = source_type or data_type
        catalog_path = _bar_catalog_path_for(
            base_catalog_path,
            data_type,
            symbol,
            interval,
            source_type=effective_source,
            storage=storage,
        )
        result = await asyncio.to_thread(
            _compact_bars_with_storage, storage, symbol, interval, catalog_path
        )

        # Update DB catalog size_bytes for the same source-aware bar row.
        total_size = result.get("size_after")
        if total_size is None:
            total_size = await asyncio.to_thread(_parquet_size_for, catalog_path, symbol, interval, storage)

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
                if "bars_count" in result:
                    existing.record_count = result["bars_count"]
                await db.commit()

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

    from tinohelm.data.storage import get_active_catalog_root
    base_catalog_path = get_active_catalog_root(settings) if settings else Path("data/catalog")
    catalog_path = _bar_catalog_path_for(base_catalog_path, data_type, symbol, interval, source_type=data_type)
    try:
        result = await asyncio.to_thread(
            validate_bars,
            symbol=symbol,
            interval=interval,
            catalog_path=catalog_path,
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
    from tinohelm.data.catalog import resolve_catalog_path
    from tinohelm.data.storage import get_active_catalog_root, get_catalog_storage

    catalog_path = str(get_active_catalog_root(settings))
    storage = get_catalog_storage(settings=settings)

    # Parse bar_type directory names:
    #   BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL
    pattern = re.compile(r"^(.+\.BINANCE)-(\d+-\w+)-LAST-EXTERNAL$")

    created = 0
    updated = 0
    scanned = 0

    # Scan bar directories per source_type
    _bar_source_types = ("klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines")
    _bar_scan_targets: list[tuple[Path, str, str, dict[Path, list]]] = []
    for _src_type in _bar_source_types:
        resolved_path = resolve_catalog_path(catalog_path, _src_type)
        d = resolved_path / "data" / "bar"
        grouped = _group_parquet_objects_by_child_dir(storage, d)
        if grouped:
            _bar_scan_targets.append((d, str(resolved_path), _src_type, grouped))
    # Old flat path fallback (pre-migration data)
    _old_bar = Path(catalog_path) / "data" / "bar"
    old_grouped = _group_parquet_objects_by_child_dir(storage, _old_bar)
    if old_grouped and not any(d == _old_bar for d, _, _, _ in _bar_scan_targets):
        _bar_scan_targets.append((_old_bar, catalog_path, "klines", old_grouped))

    bar_stats: dict[tuple[str, str, str, str], dict] = {}
    for _bar_dir, cat_root, _bar_src, grouped in _bar_scan_targets:
        is_source_aware = Path(cat_root) != Path(catalog_path)
        for entry, parquet_objects in sorted(grouped.items(), key=lambda item: item[0].name):
            m = pattern.match(entry.name)
            if not m:
                continue

            nt_sym = m.group(1)          # e.g. BTCUSDT-PERP.BINANCE
            nt_interval = m.group(2)     # e.g. 1-MINUTE
            interval = _nt_to_interval(nt_interval)
            if interval is None:
                logger.warning("Scan: unknown interval %s in %s, skipping", nt_interval, entry.name)
                continue

            symbol = nt_sym.removesuffix(".BINANCE")
            stat_values = _aggregate_parquet_object_stats(parquet_objects, storage)
            if stat_values is None:
                logger.warning("Scan: no timestamp range readable for %s, skipping", entry.name)
                continue

            scanned += 1
            size_bytes = stat_values["size_bytes"]
            record_count = stat_values["record_count"]
            start_date = stat_values["start_date"]
            end_date = stat_values["end_date"]

            key = (symbol, "bar", interval, _bar_src)
            existing = bar_stats.get(key)
            if existing is None:
                bar_stats[key] = {
                    "start_date": start_date,
                    "end_date": end_date,
                    "size_bytes": size_bytes,
                    "record_count": record_count,
                    "all_record_counts_known": record_count is not None,
                    "file_path": cat_root,
                    "has_source_aware": is_source_aware,
                }
            else:
                existing["start_date"] = min(existing["start_date"], start_date)
                existing["end_date"] = max(existing["end_date"], end_date)
                existing["size_bytes"] += size_bytes
                if record_count is None:
                    existing["all_record_counts_known"] = False
                    existing["record_count"] = None
                elif existing["all_record_counts_known"]:
                    existing["record_count"] = (existing["record_count"] or 0) + record_count
                if is_source_aware and not existing["has_source_aware"]:
                    existing["file_path"] = cat_root
                    existing["has_source_aware"] = True

            logger.info("Scan: %s %s %s [%s..%s] %d bytes", _bar_src, symbol, interval, start_date, end_date, size_bytes)

    for (symbol, _data_type, interval, _bar_src), stat in bar_stats.items():
        stmt = select(DataCatalog).where(
            DataCatalog.symbol == symbol,
            DataCatalog.data_type == "bar",
            DataCatalog.interval == interval,
            DataCatalog.source_type == _bar_src,
        )
        existing_row = (await db.execute(stmt)).scalar_one_or_none()
        if not existing_row and _bar_src == "klines":
            stmt_null = select(DataCatalog).where(
                DataCatalog.symbol == symbol,
                DataCatalog.data_type == "bar",
                DataCatalog.interval == interval,
                DataCatalog.source_type.is_(None),
            )
            existing_row = (await db.execute(stmt_null)).scalar_one_or_none()
        if existing_row:
            existing_row.start_date = min(existing_row.start_date, stat["start_date"])
            existing_row.end_date = max(existing_row.end_date, stat["end_date"])
            existing_row.size_bytes = stat["size_bytes"]
            existing_row.record_count = stat["record_count"]
            existing_row.file_path = stat["file_path"]
            existing_row.source_type = _bar_src
            updated += 1
        else:
            db.add(DataCatalog(
                symbol=symbol,
                data_type="bar",
                interval=interval,
                start_date=stat["start_date"],
                end_date=stat["end_date"],
                file_path=stat["file_path"],
                size_bytes=stat["size_bytes"],
                record_count=stat["record_count"],
                source_type=_bar_src,
            ))
            created += 1

    # Scan source-aware tick directories plus old flat fallbacks.
    _tick_scan_targets: list[tuple[Path, str, str, str, dict[Path, list]]] = []
    for _tick_data_type, _tick_dir_name, _tick_source_types in (
        ("trade_tick", "trade_tick", ("aggTrades", "trades")),
        ("quote_tick", "quote_tick", ("bookTicker",)),
    ):
        for _src_type in _tick_source_types:
            resolved_path = resolve_catalog_path(catalog_path, _src_type)
            d = resolved_path / "data" / _tick_dir_name
            grouped = _group_parquet_objects_by_child_dir(storage, d)
            if grouped:
                _tick_scan_targets.append((d, str(resolved_path), _src_type, _tick_data_type, grouped))
        _old_tick = Path(catalog_path) / "data" / _tick_dir_name
        old_grouped = _group_parquet_objects_by_child_dir(storage, _old_tick)
        if old_grouped and not any(d == _old_tick for d, _, _, dt, _ in _tick_scan_targets if dt == _tick_data_type):
            _tick_scan_targets.append((_old_tick, catalog_path, _tick_source_types[0], _tick_data_type, old_grouped))

    tick_stats: dict[tuple[str, str, str, str], dict] = {}
    sym_pattern = re.compile(r"^(.+)\.BINANCE$")
    for _trade_tick_dir, _tick_root, _src_type, _tick_data_type, grouped in _tick_scan_targets:
        resolved_path = _tick_root
        is_source_aware = Path(resolved_path) != Path(catalog_path)
        for entry, parquet_objects in sorted(grouped.items(), key=lambda item: item[0].name):
            m = sym_pattern.match(entry.name)
            if not m:
                continue
            symbol = m.group(1)
            stat_values = _aggregate_parquet_object_stats(parquet_objects, storage)
            if stat_values is None:
                continue

            scanned += 1
            sz = stat_values["size_bytes"]
            total_rows = stat_values["record_count"]
            start_dt = stat_values["start_date"]
            end_dt = stat_values["end_date"]

            key = (symbol, _tick_data_type, "tick", _src_type)
            existing = tick_stats.get(key)
            if existing is None:
                tick_stats[key] = {
                    "start_date": start_dt,
                    "end_date": end_dt,
                    "size_bytes": sz,
                    "record_count": total_rows,
                    "all_record_counts_known": total_rows is not None,
                    "file_path": resolved_path,
                    "has_source_aware": is_source_aware,
                }
            else:
                existing["start_date"] = min(existing["start_date"], start_dt)
                existing["end_date"] = max(existing["end_date"], end_dt)
                existing["size_bytes"] += sz
                if total_rows is None:
                    existing["all_record_counts_known"] = False
                    existing["record_count"] = None
                elif existing["all_record_counts_known"]:
                    existing["record_count"] = (existing["record_count"] or 0) + total_rows
                if is_source_aware and not existing["has_source_aware"]:
                    existing["file_path"] = resolved_path
                    existing["has_source_aware"] = True

    for (symbol, _tick_data_type, interval, _src_type), stat in tick_stats.items():
        stmt = select(DataCatalog).where(
            DataCatalog.symbol == symbol,
            DataCatalog.data_type == _tick_data_type,
            DataCatalog.interval == interval,
            DataCatalog.source_type == _src_type,
        )
        existing_row = (await db.execute(stmt)).scalar_one_or_none()
        if not existing_row and _src_type == _LEGACY_DEFAULT_SOURCE.get(_tick_data_type):
            stmt_null = select(DataCatalog).where(
                DataCatalog.symbol == symbol,
                DataCatalog.data_type == _tick_data_type,
                DataCatalog.interval == interval,
                DataCatalog.source_type.is_(None),
            )
            existing_row = (await db.execute(stmt_null)).scalar_one_or_none()
        if existing_row:
            existing_row.start_date = min(existing_row.start_date, stat["start_date"])
            existing_row.end_date = max(existing_row.end_date, stat["end_date"])
            existing_row.size_bytes = stat["size_bytes"]
            existing_row.file_path = stat["file_path"]
            existing_row.source_type = _src_type
            existing_row.record_count = stat["record_count"]
            updated += 1
        else:
            db.add(DataCatalog(
                symbol=symbol,
                data_type=_tick_data_type,
                interval=interval,
                start_date=stat["start_date"],
                end_date=stat["end_date"],
                file_path=stat["file_path"],
                size_bytes=stat["size_bytes"],
                record_count=stat["record_count"],
                source_type=_src_type,
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

    from tinohelm.data.storage import get_active_catalog_root
    deleted_files, freed_bytes = await asyncio.to_thread(
        _delete_storage_files,
        row.symbol, row.data_type, row.interval,
        str(get_active_catalog_root(settings)),
        row.source_type,
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
