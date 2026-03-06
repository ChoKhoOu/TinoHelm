"""Data fetcher orchestrator — manages incremental downloads."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select

from tinohelm.data.providers.binance import fetch_klines
from tinohelm.data.catalog import klines_to_parquet

logger = logging.getLogger(__name__)


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

        # --- Fetch missing ranges ------------------------------------
        if existing_start is None:
            await _publish_progress(5, f"Full fetch {symbol} {interval} [{req_start} .. {req_end}]")
        else:
            await _publish_progress(5, f"Incremental fetch: {len(gaps)} gap(s) for {symbol} {interval} (existing [{existing_start} .. {existing_end}])")

        all_klines: list[dict[str, Any]] = []
        for idx, (gap_start, gap_end) in enumerate(gaps):
            gap_start_dt = datetime(gap_start.year, gap_start.month, gap_start.day, tzinfo=start.tzinfo)
            gap_end_dt = datetime(gap_end.year, gap_end.month, gap_end.day, tzinfo=end.tzinfo)

            logger.info(
                "Fetching gap %d/%d: %s %s [%s .. %s]",
                idx + 1, len(gaps), symbol, interval, gap_start, gap_end,
            )
            klines = await fetch_klines(
                symbol=symbol,
                interval=interval,
                start=gap_start_dt,
                end=gap_end_dt,
                testnet=testnet,
            )
            all_klines.extend(klines)

        await _publish_progress(50, f"Fetched {len(all_klines)} bars, writing to Parquet...")

        files = klines_to_parquet(
            klines=all_klines,
            symbol=symbol,
            interval=interval,
            catalog_path=catalog_path,
        )

        await _publish_progress(100, f"Complete: {len(all_klines)} bars in {len(files)} files")

        return {
            "symbol": symbol,
            "interval": interval,
            "bars_count": len(all_klines),
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
