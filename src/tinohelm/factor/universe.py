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
``list_universes()`` derives the default scan directory from
``paths.get("universes_dir")`` (the canonical PathRegistry entry for
``research / "universes"``).  In Docker the host mount ``~/.tino/research``
is surfaced inside the container as ``/app/tino/research`` (no leading dot),
so we read the research root from ``Settings`` instead of hard-coding
``Path.home() / ".tino" / "research"``.

The directory is NOT auto-created by this module; callers should create
it before calling ``load_csv()``.  ``load_csv`` will raise ``FileNotFoundError``
if the path does not exist.
"""
from __future__ import annotations

import csv
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from tinohelm.core.paths import paths

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: New-coin isolation window — a symbol is excluded for 7 days after listing.
_NEW_COIN_ISOLATION_DAYS: int = 7


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
    cleaned = value.strip()
    # ``datetime.fromisoformat`` (3.11+) handles ``YYYY-MM-DD``, full datetimes
    # and ``+HH:MM`` offsets.  Trailing ``Z`` is normalised explicitly because
    # the stdlib parser only accepted it in 3.11+ and we want to be robust.
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(cleaned)
    except ValueError:
        # Fall back to plain ``YYYY-MM-DD`` (covers exotic separators).
        ts = datetime.strptime(cleaned, "%Y-%m-%d")
    if ts.tzinfo is not None:
        ts = ts.astimezone(UTC).replace(tzinfo=None)
    # Normalise to midnight naive datetime to match the legacy contract.
    return ts.replace(hour=0, minute=0, second=0, microsecond=0)


def _to_naive_datetime(ts: datetime) -> datetime:
    """Convert *ts* to a timezone-naive ``datetime`` for comparison.

    Projects follow naive datetime convention; stripping tzinfo here prevents
    ``TypeError`` when comparing aware/naive datetimes.

    Accepts plain :class:`datetime` *and* duck-typed pandas Timestamp instances
    (legacy callers).  Pandas timestamps are detected by checking for the
    ``to_pydatetime`` attribute that all real ``datetime`` subclasses on the
    project's pandas pin (>= 2.0) provide.
    """
    if hasattr(ts, "to_pydatetime") and not type(ts).__module__.startswith("datetime"):
        # Duck-typed pandas Timestamp.  Convert via ``to_pydatetime`` so we
        # never import pandas at module top.
        ts = ts.to_pydatetime()  # type: ignore[union-attr]
    if ts.tzinfo is not None:
        return ts.astimezone(UTC).replace(tzinfo=None)
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

    @classmethod
    def from_db_row(cls, row: object) -> "Universe":
        """Construct a ``Universe`` instance from a DB ORM row (``db.models.Universe``).

        Reads ``pit_rules_json`` (a dict keyed by symbol with ``listing_date``
        and optional ``delisting_date`` string values) and reconstructs the
        internal ``_UniverseRow`` list used for PIT queries.

        ``pit_rules_json`` format (stored by ``sync_from_csv``)::

            {
                "BTCUSDT-PERP": {"listing_date": "2020-01-01", "delisting_date": null},
                "ETHUSDT-PERP": {"listing_date": "2020-03-01", "delisting_date": "2024-06-01"},
            }

        Parameters
        ----------
        row:
            SQLAlchemy ORM ``Universe`` instance from ``db.models``.

        Returns
        -------
        Universe
            Populated instance ready for PIT queries.
        """
        pit_rules: dict = row.pit_rules_json or {}  # type: ignore[union-attr]
        rows_list: list[_UniverseRow] = []
        for symbol, rule in pit_rules.items():
            listing_date = _parse_date(rule.get("listing_date", ""))
            if listing_date is None:
                # Gracefully default to epoch so isolation window never fires
                listing_date = datetime(1970, 1, 1)
            delisting_date = _parse_date(rule.get("delisting_date") or "")
            rows_list.append(_UniverseRow(symbol, listing_date, delisting_date))
        return cls(rows_list, name=row.name)  # type: ignore[union-attr]

    @classmethod
    async def sync_from_csv(
        cls,
        csv_path: Path,
        db_session: "AsyncSession",
    ) -> tuple["Universe", int]:
        """Idempotent sync of a CSV file into the ``universes`` DB table.

        Reads the CSV, computes a ``sha256`` hash of its content, then:

        - If a row with the same ``source_csv_hash`` already exists → returns
          the existing row without creating a duplicate.
        - Otherwise → inserts a new row.

        The ``pit_rules_json`` column is populated with a dict mapping each
        symbol to its ``listing_date`` / ``delisting_date`` strings.

        Parameters
        ----------
        csv_path:
            Absolute path to the CSV file.  Raises ``FileNotFoundError`` if
            the file does not exist.
        db_session:
            Active async SQLAlchemy session.  The caller is responsible for
            committing / rolling back.

        Returns
        -------
        (Universe, db_row_id)
            The constructed ``Universe`` instance and the integer primary key
            of the ``universes`` DB row.
        """
        from sqlalchemy import select
        from tinohelm.db.models import Universe as UniverseORM

        csv_path = Path(csv_path)
        if not csv_path.exists():
            raise FileNotFoundError(f"Universe CSV not found: {csv_path}")

        content_bytes = csv_path.read_bytes()
        csv_hash = hashlib.sha256(content_bytes).hexdigest()

        # Idempotent lookup by hash
        existing = (await db_session.execute(
            select(UniverseORM).where(UniverseORM.source_csv_hash == csv_hash)
        )).scalar_one_or_none()

        if existing is not None:
            return cls.from_db_row(existing), existing.id

        # Load CSV to build pit_rules_json
        universe = cls.load_csv(csv_path)
        pit_rules: dict[str, dict] = {}
        for row_obj in universe._rows:
            pit_rules[row_obj.symbol] = {
                "listing_date": row_obj.listing_date.strftime("%Y-%m-%d"),
                "delisting_date": (
                    row_obj.delisting_date.strftime("%Y-%m-%d")
                    if row_obj.delisting_date is not None
                    else None
                ),
            }

        db_row = UniverseORM(
            name=universe.name,
            source_csv_path=str(csv_path),
            source_csv_hash=csv_hash,
            min_history_bars=100,
            new_coin_isolation_days=7,
            pit_rules_json=pit_rules,
        )
        db_session.add(db_row)
        await db_session.flush()  # populate db_row.id without committing

        return universe, db_row.id

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

    def get_symbols_at(self, ts: datetime) -> list[str]:
        """Return eligible symbols at a given point in time.

        Applies both PIT filtering and new-coin isolation (7-day window):

        - A symbol is included only if ``listing_date + 7d <= ts``.
        - A symbol is excluded once ``delisting_date`` is reached (if set).

        Parameters
        ----------
        ts:
            Query timestamp.  Both ``datetime`` and duck-typed
            pandas Timestamp are accepted.  Timezone-aware values have
            tzinfo stripped (naive comparison, project convention).

        Returns
        -------
        list[str]
            Sorted list of eligible symbol strings.
        """
        naive_ts = _to_naive_datetime(ts)
        return sorted(
            row.symbol for row in self._rows if row.is_active_at(naive_ts)
        )

    def get_symbols_between(
        self,
        start: datetime | None,
        end: datetime | None,
    ) -> list[str]:
        """Return symbols eligible at any time inside a historical window.

        This is the loading-set companion to :meth:`get_symbols_at`: a PIT
        backtest must request data for every symbol whose active interval
        intersects the evaluation window, then let downstream PIT masks null
        each timestamp where the symbol is not eligible.  Using only the
        end-anchor constituents would drop symbols delisted during the window
        and create survivorship bias.

        ``None`` boundaries represent an open interval.  When both are
        ``None`` the method returns all known symbols because no finite window
        was supplied.
        """
        start_ts = _to_naive_datetime(start) if start is not None else None
        end_ts = _to_naive_datetime(end) if end is not None else None
        if start_ts is not None and end_ts is not None and start_ts > end_ts:
            raise ValueError(
                f"Universe window start {start_ts!r} is after end {end_ts!r}"
            )

        out: list[str] = []
        for row in self._rows:
            eligible_from = row.listing_date + timedelta(days=_NEW_COIN_ISOLATION_DAYS)
            active_until = row.delisting_date

            # No active timestamp at or before the requested end.
            if end_ts is not None and eligible_from > end_ts:
                continue
            # Symbol stopped trading before (or exactly at) the requested start.
            if start_ts is not None and active_until is not None and active_until <= start_ts:
                continue
            out.append(row.symbol)

        return sorted(out)

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

        Scans *base_dir* for ``*.csv`` files and returns their stems sorted
        alphabetically.  When *base_dir* is ``None``, the directory is derived
        from ``paths.get("universes_dir")`` (the canonical PathRegistry entry
        for ``research / "universes"``).

        Parameters
        ----------
        base_dir:
            Directory to scan.  If ``None``, the default is resolved at call
            time from project settings.  If the directory does not exist,
            returns an empty list.

        Returns
        -------
        list[str]
            Sorted list of universe names (filename stems, no ``.csv``
            suffix), e.g. ``["binance_perp_top20", "sp500"]``.
        """
        directory = Path(base_dir) if base_dir is not None else paths.get("universes_dir")
        if not directory.exists():
            return []
        return sorted(p.stem for p in directory.glob("*.csv"))
