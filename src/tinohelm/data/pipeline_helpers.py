"""Pure helpers for :mod:`tinohelm.data.pipeline`.

This module contains *only* dependency-free, side-effect-free logic extracted
from ``pipeline.py``:

- Canonical mappings (``WRITE_CATEGORY``, ``INTERVAL_CONVENTION``).
- Category-resolution functions for DB and catalog writer dispatch.
- Progress-percentage math for the download/convert pipeline.
- UTC date-boundary conversions for parquet pruning.
- Vision filename stem parsing for coverage-end detection.
- CSV header sniffing predicate.

Importing this module **must not** trigger imports of ``nautilus_trader``,
``sqlalchemy``, ``httpx`` or ``pandas`` — that allows the helpers to be unit
tested under a lean CI image without the heavy NT wheel.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType
from typing import Mapping

# ---------------------------------------------------------------------------
# Canonical mappings
# ---------------------------------------------------------------------------

# Mapping: data_type → catalog write category. Drives storage layout and
# which writer in :mod:`tinohelm.data.catalog` is used.
WRITE_CATEGORY: Mapping[str, str] = MappingProxyType({
    "klines": "bar",
    "markPriceKlines": "mark_price",
    "indexPriceKlines": "index_price",
    "trades": "trade_tick",
    "bookTicker": "quote_tick",
    "fundingRate": "funding_rate",
    "bookDepth": "order_book_delta",
    "liquidationSnapshot": "liquidation",
    "metrics": "metrics",
})

# Idempotent write-category inputs used by DB/catalog rows. Keep these out of
# WRITE_CATEGORY because that mapping also doubles as source_type → category for
# source-aware catalog roots (e.g. resolve_catalog_path("trades")).
CANONICAL_WRITE_CATEGORIES: frozenset[str] = frozenset({
    "bar",
    "trade_tick",
    "quote_tick",
    "funding_rate",
    "order_book_delta",
    "liquidation",
    "metrics",
})

# Mapping: data_type → DB ``interval`` column convention when the user did
# not provide an interval (typically because the data type is intervalless).
INTERVAL_CONVENTION: Mapping[str, str] = MappingProxyType({
    "trades": "tick",
    "bookTicker": "tick",
    "fundingRate": "8h",
    "bookDepth": "tick",
    "liquidationSnapshot": "tick",
    "metrics": "5m",
})


DOWNLOAD_PROGRESS_BASE = 5
DOWNLOAD_PROGRESS_SPAN = 90


# ---------------------------------------------------------------------------
# Category resolution
# ---------------------------------------------------------------------------

def resolve_write_category(data_type: str) -> str:
    """Return the catalog writer key for ``data_type``.

    Unknown types fall back to ``"custom"`` — the caller is expected to log a
    warning and skip writing rather than crash.
    """
    if data_type in CANONICAL_WRITE_CATEGORIES:
        return data_type
    return WRITE_CATEGORY.get(data_type, "custom")


def resolve_db_category(data_type: str) -> str:
    """Return the value to store in the ``data_catalog.data_type`` column.

    Unknown types fall back to the input ``data_type`` itself. Keeps the DB
    record discoverable even when the type isn't yet a first-class category.
    """
    if data_type in CANONICAL_WRITE_CATEGORIES:
        return data_type
    return WRITE_CATEGORY.get(data_type, data_type)


def resolve_db_interval(data_type: str, interval: str | None) -> str:
    """Return the value to store in the ``data_catalog.interval`` column.

    Precedence: user-supplied ``interval`` > convention map > ``"tick"``.
    Empty string is treated as missing.
    """
    if interval:
        return interval
    return INTERVAL_CONVENTION.get(data_type, "tick")




# ---------------------------------------------------------------------------
# Progress math
# ---------------------------------------------------------------------------

def compute_stage_pct(
    done: int,
    total: int,
    *,
    base: int = DOWNLOAD_PROGRESS_BASE,
    span: int = DOWNLOAD_PROGRESS_SPAN,
) -> int:
    """Compute the linear stage percentage for ``done / total`` work units.

    Returns ``base`` when ``total <= 0`` (avoids zero-division and pins the
    floor of the band). Result is always an ``int`` in ``[base, base + span]``.
    """
    if total <= 0:
        return base
    if done < 0:
        done = 0
    if done > total:
        done = total
    return base + round(span * done / total)


def compute_chunk_subprogress(
    stage_done: int,
    total_tasks: int,
    chunks: int,
    *,
    base: int = DOWNLOAD_PROGRESS_BASE,
    span: int = DOWNLOAD_PROGRESS_SPAN,
) -> int:
    """Interpolate intra-task progress between two adjacent stage percentages.

    The pipeline reports task-level progress in equal slices of ``span`` (one
    slice per task). Within a single task, large CSV files emit chunk
    callbacks; this helper maps the chunk count to a sub-percentage that
    stays *strictly below* the next task slice.

    The interpolation uses ``chunks / (chunks + 2)`` so 1 chunk = 33% of the
    sub-window, 4 chunks = 67%, never reaching 100%.
    """
    base_pct = compute_stage_pct(stage_done, total_tasks, base=base, span=span)
    next_pct = compute_stage_pct(stage_done + 1, total_tasks, base=base, span=span)
    if next_pct <= base_pct:
        return base_pct
    chunks = max(1, chunks)
    sub = base_pct + round((next_pct - base_pct) * chunks / (chunks + 2))
    return min(sub, next_pct - 1)


# ---------------------------------------------------------------------------
# UTC date-boundary conversions
# ---------------------------------------------------------------------------

def date_start_dt(d: date) -> datetime:
    """Return the UTC midnight datetime at the start of ``d``."""
    return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)


def date_end_dt(d: date) -> datetime:
    """Return the UTC midnight datetime at the *end* of ``d``.

    By convention this is the start of ``d + 1 day``. Pair with
    :func:`date_start_dt` for half-open ``[start, end)`` ranges that are the
    preferred shape for time-window comparisons.
    """
    return datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)


def date_start_ns(d: date) -> int:
    """Return the nanosecond UTC timestamp at the start of ``d``."""
    return int(date_start_dt(d).timestamp() * 1_000_000_000)


def date_end_ns(d: date) -> int:
    """Return the nanosecond UTC timestamp at the *end* of ``d``."""
    return int(date_end_dt(d).timestamp() * 1_000_000_000)


# ---------------------------------------------------------------------------
# Vision filename stem parsing
# ---------------------------------------------------------------------------

def parse_vision_coverage_end(granularity: str, stem: str) -> date | None:
    """Parse the last covered date from a Vision archive's filename stem.

    Stems look like:

    - daily:   ``BTCUSDT-trades-2025-03-15``             → ``date(2025, 3, 15)``
    - daily:   ``BTCUSDT-klines-1m-2025-03-15``         → ``date(2025, 3, 15)``
    - monthly: ``BTCUSDT-klines-1m-2025-03``            → ``date(2025, 3, 31)``
    - monthly: ``BTCUSDT-trades-2024-12``               → ``date(2024, 12, 31)``

    Returns ``None`` for unrecognized formats.
    """
    if not stem:
        return None
    parts = stem.split("-")

    if granularity == "daily" and len(parts) >= 3:
        try:
            date_str = "-".join(parts[-3:])
            return date.fromisoformat(date_str)
        except ValueError:
            return None

    if granularity == "monthly" and len(parts) >= 2:
        try:
            year = int(parts[-2])
            month = int(parts[-1])
            if not (1 <= month <= 12):
                return None
            if month == 12:
                return date(year + 1, 1, 1) - timedelta(days=1)
            return date(year, month + 1, 1) - timedelta(days=1)
        except (ValueError, IndexError):
            return None

    return None


# ---------------------------------------------------------------------------
# CSV header sniffing
# ---------------------------------------------------------------------------

def csv_has_header(first_line: str) -> bool:
    """Return True if a CSV's first line looks like a text header.

    Binance Vision CSVs with headers begin with column names like
    ``"open_time"``; CSVs without headers begin with a digit (a Unix epoch
    millisecond, a price, etc.). Empty or whitespace-only lines fall through
    to "no header" so the data row count isn't off-by-one.
    """
    if not first_line:
        return False
    first_char = first_line[0]
    if first_char.isspace():
        return False
    return not first_char.isdigit()
