from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.data.catalog import CatalogSession
from tinohelm.data.pipeline_helpers import date_end_ns, date_start_ns, missing_date_slices_from_intervals
from tinohelm.db.models import DataFetchJob


def catalog_session_for_fetch(catalog_path: Path | str, storage=None) -> CatalogSession:
    return CatalogSession(Path(catalog_path), storage=storage)


def _subtract_active_slices(
    requested: list[tuple[date, date]],
    active_rows: list[DataFetchJob],
) -> list[tuple[date, date]]:
    remaining: list[tuple[date, date]] = []
    active_intervals = [
        (row.start_date, row.end_date)
        for row in active_rows
        if getattr(row, "start_date", None) is not None
        and getattr(row, "end_date", None) is not None
    ]
    if not active_intervals:
        return requested
    for slice_start, slice_end in requested:
        overlapping = [
            (active_start, active_end)
            for active_start, active_end in active_intervals
            if active_end >= slice_start and active_start <= slice_end
        ]
        if not overlapping:
            remaining.append((slice_start, slice_end))
            continue
        remaining.extend(missing_date_slices_from_intervals(
            start=slice_start,
            end=slice_end,
            intervals=[
                (date_start_ns(active_start), date_end_ns(active_end) - 1)
                for active_start, active_end in overlapping
            ],
        ))
    return remaining


def plan_catalog_missing_slices(
    catalog_path: Path | str,
    symbol: str,
    data_type: str,
    interval: str | None,
    start: date,
    end: date,
    *,
    storage=None,
) -> list[tuple[date, date]]:
    session = catalog_session_for_fetch(catalog_path, storage=storage)
    return session.missing_date_slices(symbol, data_type, interval, start, end)


async def plan_submission_slices(
    db: AsyncSession,
    catalog_path: Path | str,
    symbol: str,
    data_type: str,
    interval: str | None,
    start: date,
    end: date,
    *,
    storage=None,
    exclude_job_ids: set[str] | None = None,
) -> list[tuple[date, date]]:
    requested_slices = plan_catalog_missing_slices(
        catalog_path=catalog_path,
        symbol=symbol,
        data_type=data_type,
        interval=interval,
        start=start,
        end=end,
        storage=storage,
    )
    if not requested_slices:
        return []
    query = select(DataFetchJob).where(
        DataFetchJob.symbol == symbol,
        DataFetchJob.data_type == data_type,
        DataFetchJob.interval.is_(interval) if interval is None else DataFetchJob.interval == interval,
        DataFetchJob.status.in_(["queued", "running"]),
        DataFetchJob.start_date <= end,
        DataFetchJob.end_date >= start,
    )
    if exclude_job_ids:
        query = query.where(DataFetchJob.job_id.not_in(exclude_job_ids))
    rows = (await db.execute(query)).scalars().all()
    return _subtract_active_slices(requested_slices, list(rows))
