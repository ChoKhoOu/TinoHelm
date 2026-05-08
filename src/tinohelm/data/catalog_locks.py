"""Process-local catalog critical-section locks for data maintenance paths."""
from __future__ import annotations

import asyncio

from tinohelm.data.pipeline_helpers import resolve_db_category, resolve_db_interval

_catalog_locks: dict[str, asyncio.Lock] = {}


def catalog_lock_key(symbol: str, data_type: str, interval: str | None) -> str:
    """Return the lock key shared by ingest and maintenance for one catalog row."""
    category = resolve_db_category(data_type)
    db_interval = resolve_db_interval(data_type, interval)
    return f"data_catalog:{symbol}:{category}:{db_interval}:{data_type}"


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
