"""Local cache for historical funding rate data.

Stores funding rates per symbol as JSON files under ``~/.tino/data/funding_rates/``.
Supports incremental updates — only fetches data newer than the latest cached record.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".tino" / "data" / "funding_rates"


def _cache_path(symbol: str) -> Path:
    """Return the cache file path for a symbol (e.g. BTCUSDT-PERP → btcusdt-perp.json)."""
    return _CACHE_DIR / f"{symbol.lower()}.json"


def _load_cache(symbol: str) -> list[dict[str, Any]]:
    """Load cached funding rates from disk. Returns empty list if no cache."""
    path = _cache_path(symbol)
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return data
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt funding rate cache for %s, will re-fetch", symbol)
        return []


def _save_cache(symbol: str, records: list[dict[str, Any]]) -> None:
    """Save funding rate records to disk (sorted, deduped by funding_time_ms)."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Dedup and sort
    seen: set[int] = set()
    deduped: list[dict[str, Any]] = []
    for r in sorted(records, key=lambda x: x["funding_time_ms"]):
        ts = r["funding_time_ms"]
        if ts not in seen:
            seen.add(ts)
            deduped.append(r)
    path = _cache_path(symbol)
    with open(path, "w") as f:
        json.dump(deduped, f)
    logger.info("Saved %d funding rate records to %s", len(deduped), path)


def load_funding_rates(
    symbol: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Load funding rates for a symbol, fetching from Binance only if needed.

    Incremental update logic:
    1. Load existing cache for the symbol
    2. If cache fully covers [start, end] → return from cache (no API call)
    3. Otherwise fetch missing data from Binance, merge into cache, save
    4. Return records filtered to [start, end]
    """
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    cached = _load_cache(symbol)

    # Determine what we need to fetch
    fetch_start: datetime | None = None
    if not cached:
        # No cache at all — fetch everything
        fetch_start = start
    else:
        latest_cached_ms = max(r["funding_time_ms"] for r in cached)
        earliest_cached_ms = min(r["funding_time_ms"] for r in cached)

        # Check if we need older data (before cache start)
        if start_ms < earliest_cached_ms:
            # Need to re-fetch from the beginning — simpler than two-range fetch
            fetch_start = start
        elif end_ms > latest_cached_ms:
            # Need newer data — incremental append
            fetch_start = datetime.fromtimestamp(
                (latest_cached_ms + 1) / 1000, tz=timezone.utc,
            )
        # else: cache fully covers the range, no fetch needed

    if fetch_start is not None:
        try:
            from tinohelm.data.pipeline import BinanceVisionPipeline
            from tinohelm.core.config import get_settings

            settings = get_settings()
            catalog_path = str(settings.paths.catalog) if settings else "data/catalog"
            pipeline = BinanceVisionPipeline(catalog_path=catalog_path)
            result = pipeline.ingest_sync(
                symbol=symbol,
                data_type="fundingRate",
                start=fetch_start.date() if isinstance(fetch_start, datetime) else fetch_start,
                end=end.date() if isinstance(end, datetime) else end,
            )
            if result.objects_count > 0:
                cached = _load_cache(symbol)  # Re-read from cache (Pipeline writes via _save_cache)
                logger.info(
                    "Incremental update: ingested %d records for %s (cache now %d total)",
                    result.objects_count, symbol, len(cached),
                )
            elif not cached:
                logger.warning("No funding rate data available for %s", symbol)
        except Exception:
            logger.warning(
                "Failed to fetch funding rates for %s, using cache (%d records)",
                symbol, len(cached), exc_info=True,
            )

    # Filter to requested range
    return [
        r for r in cached
        if start_ms <= r["funding_time_ms"] <= end_ms
    ]
