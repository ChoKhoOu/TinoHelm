"""Cross-process catalog critical-section locks for data maintenance paths."""
from __future__ import annotations

import asyncio
import errno
import fcntl
import hashlib
from pathlib import Path
from typing import BinaryIO

from tinohelm.data.pipeline_helpers import resolve_db_category, resolve_db_interval


class CatalogLock:
    def __init__(self, lock_key: str) -> None:
        self._lock_key = lock_key
        self._local_lock = asyncio.Lock()
        self._file_handle: BinaryIO | None = None

    async def acquire(self) -> bool:
        await self._local_lock.acquire()
        try:
            while True:
                handle = self._open_lock_file()
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._file_handle = handle
                    return True
                except OSError as exc:
                    handle.close()
                    if not _is_lock_busy(exc):
                        raise
                    await asyncio.sleep(0.05)
        except BaseException:
            if self._local_lock.locked():
                self._local_lock.release()
            raise

    async def try_acquire(self) -> bool:
        if self._local_lock.locked():
            return False
        await self._local_lock.acquire()
        try:
            handle = self._open_lock_file()
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                if _is_lock_busy(exc):
                    self._local_lock.release()
                    return False
                raise
            self._file_handle = handle
            return True
        except BaseException:
            if self._local_lock.locked() and self._file_handle is None:
                self._local_lock.release()
            raise

    def release(self) -> None:
        handle = self._file_handle
        self._file_handle = None
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        if self._local_lock.locked():
            self._local_lock.release()

    def locked(self) -> bool:
        return self._local_lock.locked()

    async def __aenter__(self) -> CatalogLock:
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False

    def _open_lock_file(self) -> BinaryIO:
        path = _catalog_lock_file_path(self._lock_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        return open(path, "a+b")


_catalog_locks: dict[str, CatalogLock] = {}


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


def _catalog_lock_file_path(lock_key: str) -> Path:
    from tinohelm.data.storage import get_active_catalog_root

    digest = hashlib.sha256(lock_key.encode("utf-8")).hexdigest()
    return get_active_catalog_root() / ".catalog-locks" / f"{digest}.lock"


def _is_lock_busy(exc: OSError) -> bool:
    return getattr(exc, "errno", None) in {errno.EACCES, errno.EAGAIN}


def get_catalog_lock(lock_key: str) -> CatalogLock:
    """Return a cross-process lock for ``lock_key``.

    A process-local ``asyncio.Lock`` serializes tasks inside one process, while
    a sibling lock file under the shared catalog root serializes API and worker
    processes against each other.
    """
    lock = _catalog_locks.get(lock_key)
    if lock is None:
        lock = CatalogLock(lock_key)
        _catalog_locks[lock_key] = lock
    return lock
