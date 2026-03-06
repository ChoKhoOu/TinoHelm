"""Data fetcher orchestrator — manages incremental downloads."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select

from tinohelm.data.providers.binance import fetch_klines
from tinohelm.data.catalog import klines_to_bars, write_bars

logger = logging.getLogger(__name__)


def _monthly_chunks(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Split a datetime range into monthly sub-ranges."""
    chunks = []
    current = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if current < start:
        current = start
    while current < end:
        if current.month == 12:
            next_month = current.replace(year=current.year + 1, month=1, day=1,
                                         hour=0, minute=0, second=0, microsecond=0)
        else:
            next_month = current.replace(month=current.month + 1, day=1,
                                         hour=0, minute=0, second=0, microsecond=0)
        chunk_end = min(next_month, end)
        chunks.append((current, chunk_end))
        current = next_month
    return chunks


async def _query_existing_coverage(
    symbol: str,
    interval: str,
    db_url: str | None,
) -> tuple[date | None, date | None]:
    """Query DataCatalog for the widest existing date coverage.

    Returns (earliest_start, latest_end) across all matching rows,
    or (None, None) if no coverage exists.
    """
    if db_url is None:
        return None, None

    from tinohelm.db.models import DataCatalog
    from tinohelm.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(DataCatalog.start_date, DataCatalog.end_date)
            .where(DataCatalog.symbol == symbol)
            .where(DataCatalog.interval == interval)
        )
        rows = (await session.execute(stmt)).all()

    if not rows:
        return None, None

    earliest = min(r[0] for r in rows)
    latest = max(r[1] for r in rows)
    return earliest, latest


def _compute_gaps(
    req_start: date,
    req_end: date,
    existing_start: date | None,
    existing_end: date | None,
) -> list[tuple[date, date]]:
    """Compute date ranges missing from existing coverage.

    Returns a list of 0, 1, or 2 (start, end) tuples to fetch.
    """
    if existing_start is None or existing_end is None:
        return [(req_start, req_end)]

    # Fully covered
    if req_start >= existing_start and req_end <= existing_end:
        return []

    gaps: list[tuple[date, date]] = []

    # Gap before existing coverage
    if req_start < existing_start:
        gaps.append((req_start, existing_start))

    # Gap after existing coverage
    if req_end > existing_end:
        gaps.append((existing_end, req_end))

    return gaps


async def fetch_and_store(
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    catalog_path: str,
    redis_url: str,
    testnet: bool = False,
    progress_channel: str | None = None,
    db_url: str | None = None,
) -> dict:
    """Fetch data from Binance and store in Parquet catalog.

    When *db_url* is provided (or the global DB session is configured),
    queries DataCatalog for existing coverage and only fetches the
    missing date ranges (incremental mode).

    Publishes progress to Redis if progress_channel is provided.
    Returns summary dict.
    """
    r = aioredis.from_url(redis_url, decode_responses=True) if progress_channel else None

    async def _publish_progress(pct: int, msg: str):
        if r and progress_channel:
            await r.publish(progress_channel, json.dumps({
                "symbol": symbol,
                "interval": interval,
                "progress": pct,
                "message": msg,
            }))

    try:
        await _publish_progress(0, f"Checking existing coverage for {symbol} {interval}...")

        # --- Incremental logic: determine what needs fetching --------
        req_start = start.date() if isinstance(start, datetime) else start
        req_end = end.date() if isinstance(end, datetime) else end

        existing_start, existing_end = await _query_existing_coverage(
            symbol=symbol,
            interval=interval,
            db_url=db_url,
        )

        gaps = _compute_gaps(req_start, req_end, existing_start, existing_end)

        if not gaps:
            msg = (
                f"Skipping {symbol} {interval}: fully covered "
                f"[{existing_start} .. {existing_end}]"
            )
            logger.info(msg)
            await _publish_progress(100, msg)
            return {
                "symbol": symbol,
                "interval": interval,
                "bars_count": 0,
                "files_written": 0,
                "file_paths": [],
                "start": start.isoformat(),
                "end": end.isoformat(),
                "skipped": True,
            }

        if existing_start is not None:
            logger.info(
                "Existing coverage for %s %s: [%s .. %s]. "
                "Gaps to fetch: %s",
                symbol, interval, existing_start, existing_end,
                [(str(s), str(e)) for s, e in gaps],
            )
        else:
            logger.info(
                "No existing coverage for %s %s, fetching full range [%s .. %s]",
                symbol, interval, req_start, req_end,
            )

        # --- Chunked fetch + immediate write (one month at a time) ----------
        if existing_start is None:
            await _publish_progress(5, f"Full fetch {symbol} {interval} [{req_start} .. {req_end}]")
        else:
            await _publish_progress(5, f"Incremental fetch: {len(gaps)} gap(s) for {symbol} {interval} (existing [{existing_start} .. {existing_end}])")

        # Build a flat list of monthly chunks across all gaps
        all_chunks: list[tuple[datetime, datetime]] = []
        for gap_start, gap_end in gaps:
            gap_start_dt = datetime(gap_start.year, gap_start.month, gap_start.day, tzinfo=start.tzinfo)
            gap_end_dt = datetime(gap_end.year, gap_end.month, gap_end.day, tzinfo=end.tzinfo)
            all_chunks.extend(_monthly_chunks(gap_start_dt, gap_end_dt))

        total_chunks = len(all_chunks)
        # Accumulate NT Bar objects (compact Cython structs) instead of raw klines dicts.
        # Each monthly chunk's klines are converted and freed immediately;
        # only the lean Bar objects are kept. One write_bars() call at the end
        # satisfies NT's disjoint-interval constraint.
        all_bars: list = []

        for chunk_idx, (chunk_start, chunk_end) in enumerate(all_chunks):
            pct = 5 + int(85 * chunk_idx / total_chunks)
            await _publish_progress(pct, f"Chunk {chunk_idx + 1}/{total_chunks}: {chunk_start.date()} .. {chunk_end.date()}")

            logger.info(
                "Fetching chunk %d/%d: %s %s [%s .. %s]",
                chunk_idx + 1, total_chunks, symbol, interval, chunk_start.date(), chunk_end.date(),
            )
            klines = await fetch_klines(
                symbol=symbol,
                interval=interval,
                start=chunk_start,
                end=chunk_end,
                testnet=testnet,
            )
            if klines:
                bars = klines_to_bars(klines=klines, symbol=symbol, interval=interval)
                all_bars.extend(bars)
                del klines, bars  # free raw klines + intermediate refs immediately

        await _publish_progress(92, f"Writing {len(all_bars)} bars to catalog...")
        files = write_bars(
            bars=all_bars,
            symbol=symbol,
            interval=interval,
            catalog_path=catalog_path,
        )

        await _publish_progress(100, f"Complete: {len(all_bars)} bars in {len(files)} file(s)")

        return {
            "symbol": symbol,
            "interval": interval,
            "bars_count": len(all_bars),
            "files_written": len(files),
            "file_paths": [str(f) for f in files],
            "start": start.isoformat(),
            "end": end.isoformat(),
        }

    except Exception as e:
        logger.error(f"Fetch failed for {symbol}: {e}")
        await _publish_progress(-1, f"Error: {e}")
        raise
    finally:
        if r:
            await r.close()
