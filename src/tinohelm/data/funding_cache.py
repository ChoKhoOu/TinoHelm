"""Local cache for historical funding rate data.

Stores funding rates per symbol as JSON files under ``~/.tino/data/funding_rates/``.

The pure decision and normalisation logic lives in ``funding_cache_helpers``;
this module is the I/O bridge between that layer and the filesystem.

Fetch responsibility is **not** handled here. Callers that need missing ranges
filled must trigger a fetch upstream (e.g. via
``BacktestRunner._submit_and_wait_fetch(..., data_type="fundingRate")``)
before calling ``load_funding_rates``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from tinohelm.core.paths import paths
from tinohelm.data.funding_cache_helpers import (
    dedup_and_sort_records,
    ensure_utc,
    filter_records_by_range,
    to_epoch_ms,
)

logger = logging.getLogger(__name__)


def _cache_path(symbol: str) -> Path:
    """Return the cache file path for a symbol (e.g. BTCUSDT-PERP → btcusdt-perp.json)."""
    return paths.get("funding_rates") / f"{symbol.lower()}.json"


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
    paths.get("funding_rates").mkdir(parents=True, exist_ok=True)
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
    """Load cached funding rates for a symbol, filtered to [start, end].

    Pure read function: returns the cached records without fetching. Callers
    that need missing ranges filled must trigger a fetch upstream (e.g. via
    ``BacktestRunner._submit_and_wait_fetch(..., data_type="fundingRate")``)
    before calling this function.
    """
    start = ensure_utc(start)
    end = ensure_utc(end)
    start_ms = to_epoch_ms(start)
    end_ms = to_epoch_ms(end)
    cached = _load_cache(symbol)
    return filter_records_by_range(cached, start_ms=start_ms, end_ms=end_ms)

