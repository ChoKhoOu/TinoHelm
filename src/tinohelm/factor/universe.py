"""Universe CSV loading and Point-In-Time (PIT) symbol queries.

CSV schema
----------
The file must have the following columns (header required)::

    symbol,listing_date,delisting_date
    BTCUSDT-PERP,2020-01-01,
    ETHUSDT-PERP,2020-01-01,
    DOTUSDT-PERP,2020-09-01,2024-06-01

- ``symbol`` — instrument symbol in TinoHelm format (``"BTCUSDT-PERP"``).
- ``listing_date`` — ISO-8601 date when the instrument started trading.
  Both ``YYYY-MM-DD`` and full ISO-8601 datetime strings are accepted.
- ``delisting_date`` — ISO-8601 date when the instrument was delisted.
  Empty / missing value means the instrument is still active.

PIT semantics
-------------
``get_symbols_at(ts)`` returns all symbols where:

1. ``listing_date + 7 days <= ts``   (new-coin isolation: skip first 7 days)
2. ``delisting_date IS NULL OR ts < delisting_date``

Default universe directory
--------------------------
``list_universes()`` scans ``~/.tino/research/universes/`` by default.
The directory is NOT auto-created by this module; callers should create
it before calling ``load_csv()``.  ``load_csv`` will raise ``FileNotFoundError``
if the path does not exist.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: New-coin isolation window — a symbol is excluded for 7 days after listing.
_NEW_COIN_ISOLATION_DAYS: int = 7

#: Default base directory scanned by ``list_universes()``.
_DEFAULT_UNIVERSE_DIR: Path = Path.home() / ".tino" / "research" / "universes"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_date(value: str | None) -> datetime | None:
    """Parse an ISO-8601 date/datetime string into a naive ``datetime``.

    Accepts:
    - ``"YYYY-MM-DD"``
    - Full ISO-8601 datetime strings (``"2020-01-01T00:00:00"``, etc.)
    - Empty string / ``None`` → returns ``None``

    Returns a timezone-naive ``datetime`` to match the project-wide convention
    of using naive ``datetime`` / ``utcnow()`` (see CLAUDE.md pitfalls).
    """
    if not value or not value.strip():
        return None
    value = value.strip()
    # pd.Timestamp handles all ISO-8601 variants and strips tz-info
    ts = pd.Timestamp(value)
    # Normalise to midnight naive datetime
    return ts.replace(tzinfo=None).to_pydatetime().replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _to_naive_datetime(ts: datetime | pd.Timestamp) -> datetime:
    """Convert *ts* to a timezone-naive ``datetime`` for comparison.

    Projects follow naive datetime convention; stripping tzinfo here prevents
    ``TypeError`` when comparing aware/naive datetimes.
    """
    if isinstance(ts, pd.Timestamp):
        return ts.replace(tzinfo=None).to_pydatetime()
    if ts.tzinfo is not None:
        # Strip tz without conversion (treat as-is, matching project convention)
        return ts.replace(tzinfo=None)
    return ts


# ---------------------------------------------------------------------------
# Universe record
# ---------------------------------------------------------------------------

class _UniverseRow:
    """Internal representation of a single CSV row."""

    __slots__ = ("symbol", "listing_date", "delisting_date")

    def __init__(
        self,
        symbol: str,
        listing_date: datetime,
        delisting_date: datetime | None,
    ) -> None:
        self.symbol = symbol
        self.listing_date = listing_date
        self.delisting_date = delisting_date

    def is_active_at(self, ts: datetime) -> bool:
        """Return ``True`` if this symbol is eligible at *ts*.

        Eligibility requires:
        - ``listing_date + 7 days <= ts``
        - ``delisting_date is None OR ts < delisting_date``
        """
        eligible_from = self.listing_date + timedelta(days=_NEW_COIN_ISOLATION_DAYS)
        if ts < eligible_from:
            return False
        if self.delisting_date is not None and ts >= self.delisting_date:
            return False
        return True


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

class Universe:
    """Manages a set of symbols with listing/delisting metadata.

    Supports Point-In-Time (PIT) queries via ``get_symbols_at(ts)`` so that
    factor back-tests never include symbols that were not tradeable at the
    query time, and apply a 7-day new-coin isolation window.

    Typical usage::

        uni = Universe.load_csv(Path("~/.tino/research/universes/binance_perp_top20.csv"))
        symbols = uni.get_symbols_at(datetime(2023, 6, 1))

    Class methods
    -------------
    ``load_csv(path)``   — load from a CSV file.
    ``list_universes()`` — discover available universe files.
    """

    def __init__(self, rows: list[_UniverseRow], name: str = "") -> None:
        self._rows = rows
        self.name = name

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_symbols(
        cls,
        symbols: list[str] | tuple[str, ...],
        listing_date: datetime | None = None,
        name: str = "inline",
    ) -> "Universe":
        """Create a Universe from a plain list of symbol strings.

        All symbols are treated as permanently active (no delisting).  The
        ``listing_date`` defaults to the Unix epoch so PIT filtering never
        excludes them (the 7-day isolation window is relative to that date).

        This is the canonical constructor when the caller already holds a
        validated symbol list (e.g. ``EvalConfig.universe``) and does not need
        CSV-based PIT management.

        Parameters
        ----------
        symbols:
            Sequence of symbol strings in TinoHelm format (e.g. ``"BTCUSDT-PERP"``).
        listing_date:
            Optional listing date applied to every symbol.  Defaults to
            ``datetime(1970, 1, 1)`` so the 7-day isolation window is never
            triggered for modern timestamps.
        name:
            Optional name tag for the resulting Universe instance.

        Returns
        -------
        Universe
            Ready-for-use instance where every symbol is always eligible.
        """
        if listing_date is None:
            listing_date = datetime(1970, 1, 1)
        rows = [_UniverseRow(sym, listing_date, None) for sym in symbols]
        return cls(rows, name=name)

    @classmethod
    def load_csv(cls, path: Path) -> "Universe":
        """Load a universe from a CSV file.

        Parameters
        ----------
        path:
            Path to the CSV file.  Must exist; the parent directory is NOT
            auto-created.  Raises ``FileNotFoundError`` if *path* does not
            exist.

        Returns
        -------
        Universe
            Populated ``Universe`` instance ready for PIT queries.

        Raises
        ------
        FileNotFoundError
            If *path* does not exist.
        ValueError
            If the CSV is missing required columns (``symbol``, ``listing_date``).
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Universe CSV not found: {path}")

        rows: list[_UniverseRow] = []
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise ValueError(f"Universe CSV is empty or has no header: {path}")

            # Normalise header names to lowercase for robustness
            fieldnames_lower = [f.strip().lower() for f in reader.fieldnames]
            required = {"symbol", "listing_date"}
            missing = required - set(fieldnames_lower)
            if missing:
                raise ValueError(
                    f"Universe CSV missing required columns: {missing}. "
                    f"Found: {reader.fieldnames}"
                )

            for raw_row in reader:
                # Normalise keys to lowercase
                row: dict[str, str] = {
                    k.strip().lower(): v.strip() for k, v in raw_row.items()
                }
                symbol = row.get("symbol", "").strip()
                if not symbol:
                    continue  # skip blank rows

                listing_str = row.get("listing_date", "")
                listing_date = _parse_date(listing_str)
                if listing_date is None:
                    raise ValueError(
                        f"Row for symbol '{symbol}' has empty listing_date"
                    )

                delisting_str = row.get("delisting_date", "")
                delisting_date = _parse_date(delisting_str)

                rows.append(_UniverseRow(symbol, listing_date, delisting_date))

        name = path.stem  # filename without extension, e.g. "binance_perp_top20"
        return cls(rows, name=name)

    # ------------------------------------------------------------------
    # PIT query
    # ------------------------------------------------------------------

    def get_symbol_boundaries(self) -> dict[str, tuple[datetime, datetime | None]]:
        """Return a mapping of symbol → (eligible_from, delisting_date).

        ``eligible_from`` is ``listing_date + 7 days`` (the new-coin isolation
        boundary).  ``delisting_date`` is ``None`` when the symbol is still
        active (no delisting).

        This is useful for vectorised PIT filtering: callers can build column
        masks without calling ``get_symbols_at`` per timestamp.

        Returns
        -------
        dict[str, tuple[datetime, datetime | None]]
            Keys are symbol strings; values are ``(eligible_from, delisting_date)``
            tuples where both dates are timezone-naive ``datetime`` instances.
        """
        result: dict[str, tuple[datetime, datetime | None]] = {}
        for row in self._rows:
            eligible_from = row.listing_date + timedelta(days=_NEW_COIN_ISOLATION_DAYS)
            result[row.symbol] = (eligible_from, row.delisting_date)
        return result

    def get_symbols_at(self, ts: datetime | pd.Timestamp) -> list[str]:
        """Return eligible symbols at a given point in time.

        Applies both PIT filtering and new-coin isolation (7-day window):

        - A symbol is included only if ``listing_date + 7d <= ts``.
        - A symbol is excluded once ``delisting_date`` is reached (if set).

        Parameters
        ----------
        ts:
            Query timestamp.  Both ``datetime`` and ``pd.Timestamp`` are
            accepted.  Timezone-aware values have tzinfo stripped (naive
            comparison, project convention).

        Returns
        -------
        list[str]
            Sorted list of eligible symbol strings.
        """
        naive_ts = _to_naive_datetime(ts)
        return sorted(
            row.symbol for row in self._rows if row.is_active_at(naive_ts)
        )

    # ------------------------------------------------------------------
    # Iteration helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self) -> Iterator[str]:
        """Iterate over all symbols (regardless of listing status)."""
        return (row.symbol for row in self._rows)

    def __repr__(self) -> str:
        return f"Universe(name={self.name!r}, size={len(self._rows)})"

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    @staticmethod
    def list_universes(base_dir: Path | None = None) -> list[str]:
        """Return the names of available universe CSV files (without extension).

        Scans *base_dir* (default: ``~/.tino/research/universes/``) for
        ``*.csv`` files and returns their stems sorted alphabetically.

        Parameters
        ----------
        base_dir:
            Directory to scan.  Defaults to ``~/.tino/research/universes/``.
            If the directory does not exist, returns an empty list.

        Returns
        -------
        list[str]
            Sorted list of universe names (filename stems, no ``.csv``
            suffix), e.g. ``["binance_perp_top20", "sp500"]``.
        """
        directory = Path(base_dir) if base_dir is not None else _DEFAULT_UNIVERSE_DIR
        if not directory.exists():
            return []
        return sorted(p.stem for p in directory.glob("*.csv"))
