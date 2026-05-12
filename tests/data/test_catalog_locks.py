from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from tinohelm.data.catalog_locks import _catalog_locks, catalog_lock_key, get_catalog_lock


def test_catalog_lock_key_canonicalizes_binance_symbol_aliases() -> None:
    raw_key = catalog_lock_key("BTCUSDT-PERP", "aggTrades", None)
    venue_key = catalog_lock_key("BTCUSDT-PERP.BINANCE", "aggTrades", None)
    lower_key = catalog_lock_key("btcusdt-perp.binance", "aggTrades", None)

    assert venue_key == raw_key
    assert lower_key == raw_key


def test_catalog_lock_key_does_not_collapse_plain_symbol_and_perp_symbol() -> None:
    assert catalog_lock_key("BTCUSDT", "aggTrades", None) != catalog_lock_key(
        "BTCUSDT-PERP",
        "aggTrades",
        None,
    )


def test_get_catalog_lock_reuses_lock_for_symbol_aliases() -> None:
    _catalog_locks.clear()

    raw_lock = get_catalog_lock(catalog_lock_key("BTCUSDT-PERP", "klines", "1m"))
    alias_lock = get_catalog_lock(catalog_lock_key("BTCUSDT-PERP.BINANCE", "klines", "1m"))

    assert alias_lock is raw_lock


def test_distinct_lock_handles_for_same_key_are_mutually_exclusive(tmp_path, monkeypatch) -> None:
    lock_file = tmp_path / "catalog.lock"
    monkeypatch.setattr(
        "tinohelm.data.catalog_locks._catalog_lock_file_path",
        lambda _key: Path(lock_file),
    )

    key = catalog_lock_key("BTCUSDT-PERP", "klines", "1m")
    _catalog_locks.clear()
    first = get_catalog_lock(key)
    assert asyncio.run(first.try_acquire()) is True

    try:
        _catalog_locks.clear()
        second = get_catalog_lock(key)
        assert asyncio.run(second.try_acquire()) is False
    finally:
        first.release()

    _catalog_locks.clear()
    third = get_catalog_lock(key)
    try:
        assert asyncio.run(third.try_acquire()) is True
    finally:
        third.release()


def test_acquire_times_out_and_logs_when_lock_stays_busy(tmp_path, monkeypatch, caplog) -> None:
    lock_file = tmp_path / "catalog.lock"
    monkeypatch.setattr(
        "tinohelm.data.catalog_locks._catalog_lock_file_path",
        lambda _key: Path(lock_file),
    )

    key = catalog_lock_key("BTCUSDT-PERP", "klines", "1m")
    _catalog_locks.clear()
    first = get_catalog_lock(key)
    assert asyncio.run(first.try_acquire()) is True

    try:
        _catalog_locks.clear()
        second = get_catalog_lock(key)
        caplog.set_level(logging.WARNING)
        with pytest.raises(TimeoutError, match="Could not acquire catalog lock"):
            asyncio.run(second.acquire(timeout=0.01, warn_after=0.0))
    finally:
        first.release()

    assert any("waiting" in record.message for record in caplog.records)


def test_cross_process_lock_is_mutually_exclusive(tmp_path, monkeypatch) -> None:
    lock_file = tmp_path / "catalog.lock"
    monkeypatch.setattr(
        "tinohelm.data.catalog_locks._catalog_lock_file_path",
        lambda _key: Path(lock_file),
    )

    key = catalog_lock_key("BTCUSDT-PERP", "klines", "1m")
    _catalog_locks.clear()
    first = get_catalog_lock(key)
    assert asyncio.run(first.try_acquire()) is True

    script = """
import asyncio
import json
import sys
from pathlib import Path

from tinohelm.data.catalog_locks import _catalog_locks, get_catalog_lock
import tinohelm.data.catalog_locks as catalog_locks

lock_file = Path(sys.argv[1])
key = sys.argv[2]
catalog_locks._catalog_lock_file_path = lambda _key: lock_file
_catalog_locks.clear()
lock = get_catalog_lock(key)
try:
    acquired = asyncio.run(lock.try_acquire())
    print(json.dumps({"acquired": acquired}))
finally:
    if lock.locked():
        lock.release()
"""

    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(lock_file), key],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        first.release()

    payload = json.loads(result.stdout.strip())
    assert payload == {"acquired": False}
