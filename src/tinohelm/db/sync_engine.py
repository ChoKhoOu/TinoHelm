"""Shared sync SQLAlchemy engine for subprocess workers."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Engine

_engine_cache: dict[str, "Engine"] = {}
_engine_pid: int = os.getpid()

_ASYNC_TO_SYNC_DRIVERS = {
    "+asyncpg": "+psycopg2",
    "+aiosqlite": "",
}


def get_sync_engine(db_url: str) -> "Engine":
    """Return a cached sync SQLAlchemy engine for the given URL."""
    global _engine_cache, _engine_pid
    from sqlalchemy import create_engine

    current_pid = os.getpid()
    if current_pid != _engine_pid:
        # Process was forked -- cached engines are invalid
        _engine_cache.clear()
        _engine_pid = current_pid

    if db_url not in _engine_cache:
        sync_url = db_url
        for async_driver, sync_driver in _ASYNC_TO_SYNC_DRIVERS.items():
            sync_url = sync_url.replace(async_driver, sync_driver)
        _engine_cache[db_url] = create_engine(sync_url)
    return _engine_cache[db_url]


def dispose_sync_engines() -> None:
    """Dispose all cached sync engines."""
    for engine in _engine_cache.values():
        engine.dispose()
    _engine_cache.clear()
