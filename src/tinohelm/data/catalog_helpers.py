"""Pure helpers for :mod:`tinohelm.data.catalog`.

This module contains *only* dependency-free, side-effect-free logic extracted
from ``catalog.py``:

- Canonical mappings (``INTERVAL_MAP``, ``CATEGORY_DIR``,
  ``WRITABLE_CATEGORIES``).
- Interval parsing (``interval_to_step_unit``, ``interval_to_nanoseconds``)
  with explicit ``ValueError`` on unknown keys.
- Storage-path resolution (``resolve_catalog_path``) that delegates the
  source-type → category mapping to ``pipeline_helpers.WRITE_CATEGORY``,
  eliminating the previous duplicate ``_SOURCE_TO_CATEGORY`` table.
- Timestamp utilities (``ns_to_iso``, ``count_duplicates``, ``find_gaps``).
- OHLCV integrity predicates (``is_ohlc_valid``, ``compute_change_pct``,
  ``detect_price_jumps``).
- Validation-report assembly (``classify_status``,
  ``build_validation_issues``).
- Bar merging (``dedupe_by_ts``, ``merge_bars``) used by both the write
  path (``write_bars``) and the compaction path (``compact_bars``).

Importing this module **must not** trigger imports of ``nautilus_trader``
or any heavy dependency — it must remain testable under the lean CI image
that only has ``pytest``, ``httpx``, and the std-lib available.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from tinohelm.data.pipeline_helpers import WRITE_CATEGORY

# Short-form (user-facing) vs NT-catalog-directory aggregation names.
# Intentionally narrower than :data:`INTERVAL_MAP` because NT's on-disk bar
# directory suffixes only use the three time units below.
_INTERVAL_UNIT_MAP: Mapping[str, str] = MappingProxyType({
    "m": "MINUTE",
    "h": "HOUR",
    "d": "DAY",
})
_INTERVAL_UNIT_REVERSE: Mapping[str, str] = MappingProxyType({
    v: k for k, v in _INTERVAL_UNIT_MAP.items()
})
_INTERVAL_PATTERN = re.compile(r"(\d+)([mhd])")
_NT_SUFFIX_PATTERN = re.compile(r"(\d+)-(\w+)")

# ---------------------------------------------------------------------------
# Canonical mappings
# ---------------------------------------------------------------------------

# Interval token → (step, NT BarAggregation member name).
# The name is a ``str`` rather than the NT enum so this module stays NT-free;
# callers that need the enum resolve it via ``getattr(BarAggregation, name)``.
INTERVAL_MAP: Mapping[str, tuple[int, str]] = MappingProxyType({
    "1m": (1, "MINUTE"), "3m": (3, "MINUTE"), "5m": (5, "MINUTE"),
    "15m": (15, "MINUTE"), "30m": (30, "MINUTE"),
    "1h": (1, "HOUR"), "2h": (2, "HOUR"), "4h": (4, "HOUR"),
    "6h": (6, "HOUR"), "8h": (8, "HOUR"), "12h": (12, "HOUR"),
    "1d": (1, "DAY"),
})

# Seconds-per-unit for the NT bar aggregation names above. Anything not
# listed is rejected by ``interval_to_nanoseconds`` so a future aggregation
# (e.g. ``"WEEK"``) must be added here explicitly.
_AGGREGATION_SECONDS: Mapping[str, int] = MappingProxyType({
    "SECOND": 1,
    "MINUTE": 60,
    "HOUR": 3_600,
    "DAY": 86_400,
})

# Catalog-writer category → on-disk directory name under ``{catalog_path}/``.
# NT's ``ParquetDataCatalog`` writes to ``data/{category}/…`` itself; the
# mapping here controls the *extra* grouping that TinoHelm adds so multiple
# source types (e.g. ``klines`` and ``markPriceKlines``) can coexist under
# the same write category without overwriting each other's files.
CATEGORY_DIR: Mapping[str, str] = MappingProxyType({
    "bar": "bar",
    "trade_tick": "ticks",
    "quote_tick": "quotes",
})

# Write categories that ``catalog.write_bars`` / ``write_trade_ticks`` /
# ``write_quote_ticks`` know how to produce via Nautilus ParquetDataCatalog.
# Non-NT raw datasets such as ``funding_rate``, ``order_book_delta``,
# ``liquidation`` and ``metrics`` are written by source-specific Parquet/JSON
# helpers and therefore fall through to the base path in ``resolve_catalog_path``.
WRITABLE_CATEGORIES: frozenset[str] = frozenset(CATEGORY_DIR.keys())


# ---------------------------------------------------------------------------
# Interval parsing
# ---------------------------------------------------------------------------

def interval_to_step_unit(interval: str) -> tuple[int, str]:
    """Return ``(step, aggregation_name)`` for a supported interval token.

    Raises
    ------
    ValueError
        If ``interval`` is not a key of :data:`INTERVAL_MAP`. The error
        message lists the supported tokens so the caller can surface them
        to users without duplicating the list.
    """
    pair = INTERVAL_MAP.get(interval)
    if pair is None:
        raise ValueError(
            f"Unsupported interval {interval!r}. Supported: {list(INTERVAL_MAP.keys())}"
        )
    return pair


def interval_to_nt_suffix(interval: str) -> str:
    """Convert an interval token like ``"7m"`` to an NT directory suffix ``"7-MINUTE"``.

    Raises
    ------
    ValueError
        If ``interval`` does not match ``<digits><m|h|d>``.
    """
    match = _INTERVAL_PATTERN.fullmatch(interval)
    if not match:
        raise ValueError(
            f"Invalid interval {interval!r}: expected <number><m|h|d> (e.g. 5m, 4h, 1d)"
        )
    step, unit = match.group(1), match.group(2)
    return f"{step}-{_INTERVAL_UNIT_MAP[unit]}"


def nt_suffix_to_interval(nt_suffix: str) -> str | None:
    """Invert :func:`interval_to_nt_suffix`; return ``None`` on unknown shapes."""
    match = _NT_SUFFIX_PATTERN.fullmatch(nt_suffix)
    if not match:
        return None
    step, agg = match.group(1), match.group(2)
    unit = _INTERVAL_UNIT_REVERSE.get(agg)
    if unit is None:
        return None
    return f"{step}{unit}"


def interval_to_nanoseconds(interval: str) -> int:
    """Return the number of nanoseconds covered by one bar of ``interval``.

    Raises
    ------
    ValueError
        If ``interval`` is unsupported or its aggregation unit is not in
        :data:`_AGGREGATION_SECONDS`.
    """
    step, agg_name = interval_to_step_unit(interval)
    seconds = _AGGREGATION_SECONDS.get(agg_name)
    if seconds is None:
        raise ValueError(
            f"Unsupported aggregation unit {agg_name!r} for interval {interval!r}"
        )
    return step * seconds * 1_000_000_000


# ---------------------------------------------------------------------------
# Catalog path resolution
# ---------------------------------------------------------------------------

def resolve_catalog_path(base_path: str | Path, source_type: str | None) -> Path:
    """Return the effective catalog root for a given source type.

    Structure: ``base_path / {category_dir} / {source_type}/``

    The category is resolved by looking up ``source_type`` in
    :data:`pipeline_helpers.WRITE_CATEGORY`. Only categories in
    :data:`WRITABLE_CATEGORIES` get a nested path; anything else (including
    an unknown source type or a ``None``/empty string) returns the base path
    unchanged — this preserves the behaviour of callers that treated
    ``fundingRate`` and ``bookTicker`` as "write-to-base".

    Examples
    --------
    >>> resolve_catalog_path("/tmp/cat", "klines").as_posix()
    '/tmp/cat/bar/klines'
    >>> resolve_catalog_path("/tmp/cat", "markPriceKlines").as_posix()
    '/tmp/cat'
    >>> resolve_catalog_path("/tmp/cat", "aggTrades").as_posix()
    '/tmp/cat/ticks/aggTrades'
    >>> resolve_catalog_path("/tmp/cat", "bookTicker").as_posix()
    '/tmp/cat/quotes/bookTicker'
    >>> resolve_catalog_path("/tmp/cat", "fundingRate").as_posix()
    '/tmp/cat'
    >>> resolve_catalog_path("/tmp/cat", None).as_posix()
    '/tmp/cat'
    >>> resolve_catalog_path("/tmp/cat", "unknown").as_posix()
    '/tmp/cat'
    """
    base = Path(base_path)
    if not source_type:
        return base
    category = WRITE_CATEGORY.get(source_type)
    if category not in WRITABLE_CATEGORIES:
        return base
    return base / CATEGORY_DIR[category] / source_type


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def ns_to_iso(ns: int) -> str:
    """Format a nanosecond epoch timestamp as a UTC ISO-8601 string."""
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc).isoformat()


def count_duplicates(timestamps: Iterable[int]) -> int:
    """Return the number of duplicate timestamps in ``timestamps``.

    Equivalent to ``len(ts) - len(set(ts))`` but accepts any iterable.
    """
    total = 0
    unique_count = 0
    seen: set[int] = set()
    for ts in timestamps:
        total += 1
        if ts not in seen:
            seen.add(ts)
            unique_count += 1
    return total - unique_count


def find_gaps(
    sorted_unique_ts: Sequence[int],
    step_ns: int,
    *,
    tolerance_mult: float = 1.5,
) -> list[dict[str, Any]]:
    """Detect time-series gaps larger than ``tolerance_mult × step_ns``.

    Parameters
    ----------
    sorted_unique_ts:
        Monotonically increasing nanosecond timestamps with no duplicates.
    step_ns:
        Expected bar width in nanoseconds (obtain via
        :func:`interval_to_nanoseconds`).
    tolerance_mult:
        A diff up to ``tolerance_mult × step_ns`` counts as "on schedule",
        consistent with the original 1.5× tolerance in ``catalog.py``.

    Returns
    -------
    list of dicts with keys ``start``, ``end``, ``missing_bars``:
        ``start``/``end`` are ISO-formatted UTC strings of the bars that
        bracket the gap. ``missing_bars`` is the number of bars that
        *should* have fallen inside the gap, computed as
        ``(diff / step_ns) - 1``.

    An empty input (0 or 1 timestamps) always returns an empty list.
    """
    if step_ns <= 0:
        raise ValueError(f"step_ns must be positive, got {step_ns!r}")
    tolerance_ns = int(step_ns * tolerance_mult)
    gaps: list[dict[str, Any]] = []
    for i in range(1, len(sorted_unique_ts)):
        prev_ts = sorted_unique_ts[i - 1]
        curr_ts = sorted_unique_ts[i]
        diff = curr_ts - prev_ts
        if diff > tolerance_ns:
            missing = int(diff / step_ns) - 1
            gaps.append({
                "start": ns_to_iso(prev_ts),
                "end": ns_to_iso(curr_ts),
                "missing_bars": missing,
            })
    return gaps


# ---------------------------------------------------------------------------
# OHLCV integrity
# ---------------------------------------------------------------------------

def is_ohlc_valid(
    open_: float,
    high: float,
    low: float,
    close: float,
    *,
    tol: float = 1e-10,
) -> bool:
    """Return True if the OHLC invariants hold.

    Invariants:
      - ``high >= max(open, close) - tol``
      - ``low  <= min(open, close) + tol``
      - ``high >= low``

    ``tol`` accounts for floating-point rounding in Parquet round-trips.
    """
    if high < max(open_, close) - tol:
        return False
    if low > min(open_, close) + tol:
        return False
    if high < low:
        return False
    return True


def compute_change_pct(prev: float, curr: float) -> float | None:
    """Return the fractional change ``(curr - prev) / prev`` or ``None``.

    Returns ``None`` when ``prev`` is ``None``, zero, or negative — those
    cases don't have a meaningful multiplicative change. Uses ``abs()`` so
    the result is always non-negative (jump-threshold style comparison).
    """
    if prev is None or prev <= 0:
        return None
    return abs(curr - prev) / prev


def detect_price_jumps(
    closes_with_ts: Iterable[tuple[int, float]],
    *,
    threshold: float = 0.10,
) -> list[dict[str, Any]]:
    """Detect consecutive-close price jumps exceeding ``threshold``.

    Parameters
    ----------
    closes_with_ts:
        Iterable of ``(ts_event_ns, close_price)`` tuples in chronological
        order. Non-chronological input is still processed but the "prev
        close" pairing follows iteration order — callers should sort
        beforehand if they need time-ordered output.
    threshold:
        Fractional move that qualifies as a "jump" (0.10 = 10%).

    Returns
    -------
    list of dicts with ``timestamp`` (ISO), ``prev_close``,
    ``current_close``, ``change_pct`` (percentage, rounded to 2 decimals).
    """
    jumps: list[dict[str, Any]] = []
    prev_close: float | None = None
    for ts_ns, close in closes_with_ts:
        change = compute_change_pct(prev_close, close)
        if change is not None and change > threshold:
            jumps.append({
                "timestamp": ns_to_iso(ts_ns),
                "prev_close": round(prev_close, 4),  # type: ignore[arg-type]
                "current_close": round(close, 4),
                "change_pct": round(change * 100, 2),
            })
        prev_close = close
    return jumps


# ---------------------------------------------------------------------------
# Validation-report assembly
# ---------------------------------------------------------------------------

def classify_status(*, has_errors: bool, has_warnings: bool) -> str:
    """Return ``"errors"``, ``"warnings"``, or ``"ok"``.

    Errors dominate warnings (both flags set → ``"errors"``). This matches
    the original ``validate_bars`` behaviour — callers rely on the string
    set to drive traffic-light UI colouring.
    """
    if has_errors:
        return "errors"
    if has_warnings:
        return "warnings"
    return "ok"


def build_validation_issues(
    *,
    duplicates: int,
    gaps: Sequence[dict[str, Any]],
    ohlc_violations: int,
    zero_volume_bars: int,
    price_jumps: Sequence[dict[str, Any]],
    jump_threshold: float,
) -> list[str]:
    """Build the human-readable ``issues`` list for a validation report.

    Empty categories are omitted — only detected problems get a line.
    The sentence shapes match the originals in ``validate_bars`` so the
    frontend keeps working without changes.
    """
    issues: list[str] = []
    if duplicates > 0:
        issues.append(f"Found {duplicates} duplicate timestamp(s)")
    if gaps:
        total_missing = sum(g.get("missing_bars", 0) for g in gaps)
        issues.append(f"Found {len(gaps)} gap(s) with ~{total_missing} missing bar(s)")
    if ohlc_violations > 0:
        issues.append(
            f"Found {ohlc_violations} bar(s) with invalid OHLC relationship "
            "(high < max(O,C) or low > min(O,C))"
        )
    if zero_volume_bars > 0:
        issues.append(f"Found {zero_volume_bars} zero-volume bar(s)")
    if price_jumps:
        issues.append(
            f"Found {len(price_jumps)} price jump(s) exceeding "
            f"{jump_threshold * 100:.0f}%"
        )
    return issues


# ---------------------------------------------------------------------------
# Bar merging
# ---------------------------------------------------------------------------

def dedupe_by_ts(items: Iterable[Any]) -> list[Any]:
    """Deduplicate by ``ts_event`` attribute, keeping the last occurrence.

    Returns items sorted ascending by ``ts_event``. Used by ``write_bars``
    (merging with existing catalog content) and ``compact_bars`` (collapsing
    multiple parquet files). Accepts any object with a ``.ts_event`` attribute
    to stay decoupled from NT's ``Bar`` / ``TradeTick`` classes.
    """
    seen: dict[int, Any] = {}
    for item in items:
        seen[item.ts_event] = item
    return sorted(seen.values(), key=lambda x: x.ts_event)


def merge_bars(existing: Iterable[Any], new: Iterable[Any]) -> list[Any]:
    """Merge ``existing`` and ``new`` bars, deduping by ``ts_event``.

    ``new`` overrides ``existing`` on timestamp collision — downstream
    writers treat the freshly-ingested record as more authoritative.
    Returns a single ascending-time list ready for ``write_data``.
    """
    seen: dict[int, Any] = {}
    for bar in existing:
        seen[bar.ts_event] = bar
    for bar in new:
        seen[bar.ts_event] = bar  # new wins on collision
    return sorted(seen.values(), key=lambda b: b.ts_event)
