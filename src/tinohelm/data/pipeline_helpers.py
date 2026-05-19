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
})

# Idempotent write-category inputs used by DB/catalog rows. Keep these out of
# WRITE_CATEGORY because that mapping also doubles as source_type → category for
# source-aware catalog roots (e.g. resolve_catalog_path("trades")).
CANONICAL_WRITE_CATEGORIES: frozenset[str] = frozenset({
    "bar",
    "trade_tick",
    "quote_tick",
    "funding_rate",
    "mark_price",
    "index_price",
})

# Mapping: data_type → DB ``interval`` column convention when the user did
# not provide an interval (typically because the data type is intervalless).
INTERVAL_CONVENTION: Mapping[str, str] = MappingProxyType({
    "trades": "tick",
    "bookTicker": "tick",
    "fundingRate": "8h",
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

def detect_gaps(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Detect gaps in a sorted list of (start_ns, end_ns) intervals.

    Parameters
    ----------
    intervals:
        Sorted list of (start, end) nanosecond tuples as returned by
        ``ParquetDataCatalog.get_intervals()``.

    Returns
    -------
    List of (gap_start_ns, gap_end_ns) tuples where data is missing.
    Adjacent intervals (end == next start) are not considered gaps.
    """
    gaps: list[tuple[int, int]] = []
    for i in range(1, len(intervals)):
        prev_end = intervals[i - 1][1]
        curr_start = intervals[i][0]
        if curr_start > prev_end:
            gaps.append((prev_end, curr_start))
    return gaps


def expand_gaps_to_days(gaps: list[tuple[int, int]]) -> list[tuple[date, date]]:
    """Expand nanosecond-level gaps to day-aligned date ranges for Binance Vision.

    Logic:
    - Start date: if gap_start is at midnight, that full day is missing. Otherwise,
      ceil to the next day (the partial day has data from the preceding file).
    - End date: if gap_end is at midnight, the day before (that day already has
      full coverage from midnight). Otherwise, gap_end's calendar date itself
      (the hours before gap_end on that day have no data and need downloading).

    Exception: when the computed end_day < start_day (sub-day gap where both
    surrounding files cover parts of the same day), we fall back to the single
    calendar day containing the gap to ensure it can be filled.

    This aligns with Binance Vision's daily/monthly CSV granularity.
    """
    if not gaps:
        return []

    result: list[tuple[date, date]] = []
    for gap_start_ns, gap_end_ns in gaps:
        start_dt = datetime.fromtimestamp(gap_start_ns / 1_000_000_000, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(gap_end_ns / 1_000_000_000, tz=timezone.utc)

        is_start_midnight = (
            start_dt.hour == 0
            and start_dt.minute == 0
            and start_dt.second == 0
            and start_dt.microsecond == 0
        )
        start_day = start_dt.date() if is_start_midnight else start_dt.date() + timedelta(days=1)

        is_end_midnight = (
            end_dt.hour == 0
            and end_dt.minute == 0
            and end_dt.second == 0
            and end_dt.microsecond == 0
        )
        end_day = end_dt.date() - timedelta(days=1) if is_end_midnight else end_dt.date()

        if end_day < start_day:
            start_day = start_dt.date()
            end_day = start_day

        result.append((start_day, end_day))
    return result


def missing_date_slices_from_intervals(
    *,
    start: date,
    end: date,
    intervals: list[tuple[int, int]],
) -> list[tuple[date, date]]:
    """Return day-aligned missing slices within the requested date window.

    ``intervals`` should come from catalog coverage (UTC nanosecond tuples).
    Any interval portions outside ``[start, end]`` are ignored.
    """
    request_start_ns = date_start_ns(start)
    request_end_ns = date_end_ns(end) - 1
    if not intervals:
        return [(start, end)]

    covered: list[tuple[int, int]] = []
    for interval_start, interval_end in intervals:
        if interval_end < request_start_ns or interval_start > request_end_ns:
            continue
        covered.append((max(interval_start, request_start_ns), min(interval_end, request_end_ns)))
    if not covered:
        return [(start, end)]

    covered.sort()
    merged: list[list[int]] = []
    for interval_start, interval_end in covered:
        if not merged or interval_start > merged[-1][1] + 1:
            merged.append([interval_start, interval_end])
        else:
            merged[-1][1] = max(merged[-1][1], interval_end)

    gaps: list[tuple[int, int]] = []
    cursor = request_start_ns
    for interval_start, interval_end in merged:
        if interval_start > cursor:
            gaps.append((cursor, interval_start))
        cursor = max(cursor, interval_end + 1)
    if cursor <= request_end_ns:
        gaps.append((cursor, request_end_ns))
    if not gaps:
        return []
    return expand_gaps_to_days(gaps)


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


# ---------------------------------------------------------------------------
# Download failure classification (partial completion support, #190)
# ---------------------------------------------------------------------------

class DownloadFailureClassification:
    """Result of classifying download failures as partial or hard failure."""
    __slots__ = ("is_partial", "last_success_date", "tolerated_dates")

    def __init__(
        self,
        is_partial: bool,
        last_success_date: date | None = None,
        tolerated_dates: list[date] | None = None,
    ) -> None:
        self.is_partial = is_partial
        self.last_success_date = last_success_date
        self.tolerated_dates = tolerated_dates or []


def _is_404_error(exc: BaseException) -> bool:
    """Return True if the exception represents an HTTP 404."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        status = getattr(resp, "status_code", None)
        return status == 404
    msg = str(exc).lower()
    return "404" in msg and "not found" in msg


def classify_download_failures(
    *,
    failed_indices: dict[int, BaseException],
    success_indices: set[int],
    task_dates: list[date | None],
    tolerance_days: int = 3,
) -> DownloadFailureClassification:
    """Classify download failures as partial completion or hard failure.

    Partial completion is granted only when ALL of:
    1. At least one task succeeded (we have some data).
    2. All failed tasks form a trailing suffix (contiguous from the end).
    3. Every failed task's date is within ``tolerance_days`` of UTC today.
    4. Every failure is a 404 (not a transport/server error).
    5. Every failed task has a parseable date (None → hard failure).
    """
    if not failed_indices:
        return DownloadFailureClassification(is_partial=False)

    if not success_indices:
        return DownloadFailureClassification(is_partial=False)

    total = len(task_dates)
    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=tolerance_days)

    # Check all failures are 404s
    for exc in failed_indices.values():
        if not _is_404_error(exc):
            return DownloadFailureClassification(is_partial=False)

    # Check failures form a trailing suffix
    failed_sorted = sorted(failed_indices.keys())
    first_failed = failed_sorted[0]

    # All indices from first_failed to end must be failures
    for idx in range(first_failed, total):
        if idx not in failed_indices:
            return DownloadFailureClassification(is_partial=False)

    # All indices before first_failed must be successes
    for idx in range(first_failed):
        if idx not in success_indices:
            return DownloadFailureClassification(is_partial=False)

    # Check every failed date is within the tolerance window
    tolerated_dates: list[date] = []
    for idx in failed_sorted:
        task_date = task_dates[idx]
        if task_date is None:
            return DownloadFailureClassification(is_partial=False)
        if task_date <= cutoff:
            return DownloadFailureClassification(is_partial=False)
        tolerated_dates.append(task_date)

    last_success_idx = first_failed - 1
    last_success_date = task_dates[last_success_idx] if last_success_idx >= 0 else None
    if last_success_date is None:
        return DownloadFailureClassification(is_partial=False)

    return DownloadFailureClassification(
        is_partial=True,
        last_success_date=last_success_date,
        tolerated_dates=tolerated_dates,
    )
