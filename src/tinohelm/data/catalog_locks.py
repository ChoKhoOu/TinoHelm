"""Process-local catalog critical-section locks for data maintenance paths."""
from __future__ import annotations

import asyncio

from tinohelm.data.pipeline_helpers import resolve_db_category, resolve_db_interval

_catalog_locks: dict[str, asyncio.Lock] = {}


def canonical_catalog_lock_symbol(symbol: str) -> str:
    """Return the symbol identity used by the physical NT catalog writer.

    ``make_instrument()`` maps ``BTCUSDT-PERP`` and
    ``BTCUSDT-PERP.BINANCE`` to the same ``BTCUSDT-PERP.BINANCE`` catalog
    directory. The mutation lock must use that same identity, otherwise API
    aliases can take different process-local locks and concurrently mutate the
    same physical catalog.
    """
    normalized = str(symbol).strip().upper()
    if normalized.endswith(".BINANCE"):
        normalized = normalized[: -len(".BINANCE")]
    return normalized


def catalog_lock_key(symbol: str, data_type: str, interval: str | None) -> str:
    """Return the lock key shared by ingest and maintenance for one catalog row."""
    category = resolve_db_category(data_type)
    db_interval = resolve_db_interval(data_type, interval)
    lock_symbol = canonical_catalog_lock_symbol(symbol)
    return f"data_catalog:{lock_symbol}:{category}:{db_interval}:{data_type}"


def get_catalog_lock(lock_key: str) -> asyncio.Lock:
    """Return a process-local lock for ``lock_key``.

    TinoHelm is deployed as a single API process; this serializes worker
    consumers and background maintenance tasks inside that process.
    """
    lock = _catalog_locks.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        _catalog_locks[lock_key] = lock
    return lock
