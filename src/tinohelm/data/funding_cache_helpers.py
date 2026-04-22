"""Pure helpers for the funding-rate cache — NT-free, filesystem-free.

These functions factor out the decisions that used to live inline in
``data/funding_cache.py::load_funding_rates`` / ``_save_cache``. Keeping the
math in its own module lets us unit-test the cache logic without touching
the real ``~/.tino/data/funding_rates/`` directory or mocking out the entire
``BinanceVisionPipeline``.

Keys in use throughout this module:
    - ``funding_time_ms`` — epoch milliseconds (int), the per-record timestamp
    - ``funding_rate``    — float
    - ``mark_price``      — optional float

Record dicts with a non-integer, missing, or non-numeric ``funding_time_ms``
are treated as invalid and silently dropped — matching the historical
forgiving behaviour of ``_load_cache`` on a half-written cache file.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

DEFAULT_FUNDING_INTERVAL_MINUTES = 8 * 60  # 8 hours, default for Binance perps


def ensure_utc(dt: datetime) -> datetime:
    """Return ``dt`` with ``tzinfo`` set to UTC if it's naive.

    Naive datetimes use the local timezone when ``.timestamp()`` is called,
    which on non-UTC machines produces a silently wrong epoch conversion.
    Callers accepting user-provided datetimes should run them through this
    before any epoch math.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def to_epoch_ms(dt: datetime) -> int:
    """Convert ``dt`` to UTC epoch milliseconds.

    Naive input is treated as UTC (see :func:`ensure_utc`).
    """
    return int(ensure_utc(dt).timestamp() * 1000)


def from_epoch_ms(ms: int) -> datetime:
    """Inverse of :func:`to_epoch_ms` — returns a UTC-aware datetime."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _valid_record(record: Any) -> bool:
    """True iff ``record`` is a dict carrying a numeric ``funding_time_ms``."""
    if not isinstance(record, dict):
        return False
    ts = record.get("funding_time_ms")
    return isinstance(ts, (int, float)) and not isinstance(ts, bool)


def dedup_and_sort_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return records sorted by ``funding_time_ms`` ascending, deduped.

    Later entries for the same timestamp win (dict in a for-loop overrides
    earlier). Invalid rows (missing/bad ``funding_time_ms``) are filtered out
    rather than raising — this mirrors the forgiving semantics the cache had
    when reading a partially-corrupt JSON file.
    """
    valid = [r for r in records if _valid_record(r)]
    by_ts: dict[int, dict[str, Any]] = {}
    for r in valid:
        ts = int(r["funding_time_ms"])
        by_ts[ts] = r
    return [by_ts[ts] for ts in sorted(by_ts)]


def filter_records_by_range(
    records: list[dict[str, Any]],
    *,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, Any]]:
    """Return records with ``start_ms <= funding_time_ms <= end_ms`` (inclusive).

    Invalid rows are silently dropped. Order is preserved.
    """
    return [
        r
        for r in records
        if _valid_record(r) and start_ms <= int(r["funding_time_ms"]) <= end_ms
    ]


def compute_fetch_start(
    cached_times_ms: list[int],
    *,
    start: datetime,
    end: datetime,
) -> datetime | None:
    """Return the datetime we need to start fetching from, or ``None``.

    Decision rules (mirroring the production behaviour before extraction):
      1. No cache at all → fetch the full ``[start, end]`` range.
      2. Cache missing data older than ``start`` → re-fetch from ``start``
         (simpler than a two-range fetch; incremental save will dedup).
      3. Cache missing data newer than its tail → fetch only the new tail,
         starting at ``latest_cached_ms + 1 ms``.
      4. Cache already covers the full range → ``None`` (no API call).

    ``start`` and ``end`` may be naive; they are normalised to UTC before
    comparing against the cached millisecond timestamps.
    """
    start_ms = to_epoch_ms(start)
    end_ms = to_epoch_ms(end)

    if not cached_times_ms:
        return ensure_utc(start)

    earliest = min(cached_times_ms)
    latest = max(cached_times_ms)

    if start_ms < earliest:
        return ensure_utc(start)
    if end_ms > latest:
        return from_epoch_ms(latest + 1)
    return None
