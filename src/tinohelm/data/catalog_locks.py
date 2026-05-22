"""Process-local catalog critical-section locks for data maintenance paths."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from tinohelm.data.pipeline_helpers import resolve_db_category, resolve_db_interval

GLOBAL_CATALOG_LOCK_KEY = "data_catalog:__global__"
_catalog_locks: dict[str, asyncio.Lock] = {}
_catalog_lock_attempt_guard = asyncio.Lock()


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


def get_global_catalog_lock() -> asyncio.Lock:
    return get_catalog_lock(GLOBAL_CATALOG_LOCK_KEY)


async def _wait_for_lock_release(lock: asyncio.Lock) -> None:
    await lock.acquire()
    lock.release()


async def try_acquire_catalog_lock(lock_key: str) -> asyncio.Lock | None:
    """Try to acquire a mutation lock without waiting.

    Returns ``None`` when either catalog-wide maintenance is active or the
    specific key is already busy.
    """
    async with _catalog_lock_attempt_guard:
        global_lock = get_global_catalog_lock()
        if lock_key != GLOBAL_CATALOG_LOCK_KEY and global_lock.locked():
            return None
        lock = get_catalog_lock(lock_key)
        if lock.locked():
            return None
        await lock.acquire()
        return lock


async def acquire_catalog_lock(lock_key: str) -> asyncio.Lock:
    """Wait until a keyed mutation lock becomes available."""
    while True:
        wait_for: asyncio.Lock | None = None
        async with _catalog_lock_attempt_guard:
            global_lock = get_global_catalog_lock()
            if lock_key != GLOBAL_CATALOG_LOCK_KEY and global_lock.locked():
                wait_for = global_lock
            else:
                lock = get_catalog_lock(lock_key)
                if not lock.locked():
                    await lock.acquire()
                    return lock
                wait_for = lock
        await _wait_for_lock_release(wait_for)


async def acquire_global_catalog_lock() -> asyncio.Lock:
    """Acquire the catalog-wide maintenance lock after active keyed mutations drain."""
    global_lock = await acquire_catalog_lock(GLOBAL_CATALOG_LOCK_KEY)
    while True:
        async with _catalog_lock_attempt_guard:
            active_locks = [
                lock
                for key, lock in _catalog_locks.items()
                if key != GLOBAL_CATALOG_LOCK_KEY and lock.locked()
            ]
        if not active_locks:
            return global_lock
        await asyncio.gather(*(_wait_for_lock_release(lock) for lock in active_locks))


@asynccontextmanager
async def hold_catalog_lock(lock_key: str) -> AsyncIterator[None]:
    lock = await acquire_catalog_lock(lock_key)
    try:
        yield
    finally:
        lock.release()


@asynccontextmanager
async def hold_global_catalog_lock() -> AsyncIterator[None]:
    lock = await acquire_global_catalog_lock()
    try:
        yield
    finally:
        lock.release()
