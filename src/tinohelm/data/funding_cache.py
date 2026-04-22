"""Local cache for historical funding rate data.

Stores funding rates per symbol as JSON files under ``~/.tino/data/funding_rates/``.
Supports incremental updates — only fetches data newer than the latest cached record.

The pure decision and normalisation logic lives in ``funding_cache_helpers``;
this module is the I/O bridge between that layer and the filesystem + Binance
pipeline.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from tinohelm.data.funding_cache_helpers import (
    compute_fetch_start,
    dedup_and_sort_records,
    ensure_utc,
    filter_records_by_range,
    to_epoch_ms,
)

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
    deduped = dedup_and_sort_records(records)
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

    Incremental update logic (pure decision lives in :func:`compute_fetch_start`):
    1. Load existing cache for the symbol.
    2. If cache fully covers ``[start, end]`` → return filtered cache (no API call).
    3. Otherwise fetch missing data from Binance, merge into cache, save.
    4. Return records filtered to ``[start, end]``.
    """
    start = ensure_utc(start)
    end = ensure_utc(end)
    start_ms = to_epoch_ms(start)
    end_ms = to_epoch_ms(end)

    cached = _load_cache(symbol)
    cached_times = [
        int(r["funding_time_ms"])
        for r in cached
        if isinstance(r, dict) and isinstance(r.get("funding_time_ms"), (int, float))
    ]
    fetch_start = compute_fetch_start(cached_times, start=start, end=end)

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
                start=fetch_start.date(),
                end=end.date(),
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

    return filter_records_by_range(cached, start_ms=start_ms, end_ms=end_ms)
