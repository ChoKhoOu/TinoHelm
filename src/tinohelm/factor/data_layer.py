"""DataLayer — multi-symbol parallel data loading with time alignment and PIT filtering.

Design
------
- Reuses ``tinohelm.data.catalog`` Parquet read API (100% — no re-implementation).
- Reuses ``tinohelm.data.funding_cache._load_cache`` for funding_rate JSON reads.
- Parallel per-symbol loading via ``ThreadPoolExecutor``.
- Time alignment: non-bar sources (funding_rate) are forward-filled onto the bar index.
  funding_rate uses ``shift(1)`` on the 8h-frequency layer before ffill to implement
  as-of delay (avoid look-ahead bias: a rate published at T is only visible at T+8h).
- PIT filtering: for each timestamp, symbols absent from ``Universe.get_symbols_at``
  are set to NaN (shape is preserved — rows are NOT dropped).

Supported field_name values
---------------------------
Bar fields (source="bar"):
    ``close``, ``open``, ``high``, ``low``, ``volume``
Funding rate (source="funding_rate"):
    ``funding_rate``
Trade-tick derived (source="trade_tick"):
    ``trade_imbalance``  (not yet implemented — raises NotImplementedError)

Frequency strings
-----------------
DataRequest.frequency follows the short-form NT interval convention used by
``catalog_helpers.INTERVAL_MAP``: ``"1m"``, ``"5m"``, ``"1h"``, etc.
For funding_rate, ``"8h"`` is the canonical value.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Sequence

import pandas as pd

from tinohelm.factor.types import DataRequest, Panel
from tinohelm.factor.universe import Universe

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bar field → NT Bar attribute name
# ---------------------------------------------------------------------------

_BAR_FIELD_ATTR: dict[str, str] = {
    "close": "close",
    "open": "open",
    "high": "high",
    "low": "low",
    "volume": "volume",
}

# Default catalog root (matches docker volume mount convention)
_DEFAULT_CATALOG_ROOT = Path.home() / ".tino" / "data" / "catalog"

# Default funding-rate JSON directory (matches funding_cache._CACHE_DIR)
_DEFAULT_FUNDING_DIR = Path.home() / ".tino" / "data" / "funding_rates"


# ---------------------------------------------------------------------------
# DataLayer
# ---------------------------------------------------------------------------

class DataLayer:
    """Load multi-symbol data panels from the local Parquet/JSON catalog.

    Parameters
    ----------
    universe:
        Universe object used for PIT symbol filtering.
    catalog_root:
        Path to the NautilusTrader ``ParquetDataCatalog`` root.  Defaults to
        ``~/.tino/data/catalog``.
    funding_dir:
        Path to the funding-rate JSON directory.  Defaults to
        ``~/.tino/data/funding_rates``.  Each file is ``{symbol.lower()}.json``.
    max_workers:
        Thread-pool size for parallel symbol loading.  Default: 4.
    """

    def __init__(
        self,
        universe: Universe,
        catalog_root: Path | None = None,
        funding_dir: Path | None = None,
        max_workers: int = 4,
    ) -> None:
        self._universe = universe
        self._catalog_root = Path(catalog_root) if catalog_root is not None else _DEFAULT_CATALOG_ROOT
        self._funding_dir = Path(funding_dir) if funding_dir is not None else _DEFAULT_FUNDING_DIR
        self._max_workers = max_workers

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(
        self,
        request: DataRequest | list[DataRequest],
        start: str | pd.Timestamp | None = None,
        end: str | pd.Timestamp | None = None,
    ) -> dict[str, Panel]:
        """Load data for one or more DataRequests and return a Panel dict.

        Parameters
        ----------
        request:
            A single ``DataRequest`` or a list thereof.  Requests with the
            same ``field_name`` are grouped into one Panel (columns = symbols).
        start:
            Override start for all requests.  ISO-8601 string or pd.Timestamp.
        end:
            Override end for all requests.  ISO-8601 string or pd.Timestamp.

        Returns
        -------
        dict[str, Panel]
            Keys are ``field_name`` strings; values are DataFrames with
            ``DatetimeIndex`` (UTC, tz-naive) and symbol columns.
        """
        requests: list[DataRequest] = (
            [request] if isinstance(request, DataRequest) else list(request)
        )

        # Group by (field_name, frequency, source) — each group → one Panel
        groups: dict[tuple[str, str, str], list[DataRequest]] = defaultdict(list)
        for req in requests:
            groups[(req.field_name, req.frequency, req.source)].append(req)

        result: dict[str, Panel] = {}
        for (field_name, frequency, source), reqs in groups.items():
            symbols = [r.symbol for r in reqs]
            # Use the max lookback across all requests for this group
            lookback = max(r.lookback for r in reqs)

            panel = self._load_panel(
                symbols=symbols,
                field_name=field_name,
                frequency=frequency,
                source=source,
                start=start,
                end=end,
                lookback=lookback,
            )
            result[field_name] = panel

        return result

    # ------------------------------------------------------------------
    # Internal orchestration
    # ------------------------------------------------------------------

    def _load_panel(
        self,
        symbols: list[str],
        field_name: str,
        frequency: str,
        source: str,
        start: str | pd.Timestamp | None,
        end: str | pd.Timestamp | None,
        lookback: int,
    ) -> Panel:
        """Load one Panel (time × symbol) for a given field/frequency/source."""
        ts_start = _parse_ts(start) if start is not None else None
        ts_end = _parse_ts(end) if end is not None else None

        # Parallel per-symbol load
        series_by_symbol: dict[str, pd.Series] = {}
        futures: dict = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            for sym in symbols:
                fut = pool.submit(
                    self._load_table,
                    symbol=sym,
                    field_name=field_name,
                    source=source,
                    frequency=frequency,
                    start=ts_start,
                    end=ts_end,
                )
                futures[fut] = sym

            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    series = fut.result()
                    series_by_symbol[sym] = series
                except Exception:
                    logger.warning(
                        "Failed to load %s/%s for symbol %s",
                        source, field_name, sym, exc_info=True,
                    )
                    series_by_symbol[sym] = pd.Series(dtype="float64", name=sym)

        if not series_by_symbol:
            return pd.DataFrame()

        # Determine target bar-frequency index (from bar data)
        if source == "bar":
            panel = self._align_time(series_by_symbol, target_freq=frequency)
        elif source == "funding_rate":
            # Apply as-of delay (shift(1) + ffill) onto the union bar index so
            # a funding rate published at T is only visible at T+8h.  Using the
            # union index of all loaded series as the reference bar index gives
            # correct PIT alignment without requiring a separate bar load.
            raw_panel = self._align_time(series_by_symbol, target_freq=frequency)
            bar_index: pd.DatetimeIndex = raw_panel.index
            aligned_cols: dict[str, pd.Series] = {}
            for sym, series in series_by_symbol.items():
                aligned_cols[sym] = self._align_funding_onto_bar_index(series, bar_index)
            panel = pd.DataFrame(aligned_cols)
        else:
            # trade_tick and others: simple concat along columns
            panel = pd.DataFrame(series_by_symbol)

        # Apply PIT filter
        panel = self._apply_pit(panel)

        return panel

    def _load_table(
        self,
        symbol: str,
        field_name: str,
        source: str,
        frequency: str,
        start: pd.Timestamp | None,
        end: pd.Timestamp | None,
    ) -> pd.Series:
        """Load a single (symbol, field, freq) time series.

        Returns a pd.Series with DatetimeIndex (UTC tz-naive) named ``symbol``.
        """
        if source == "bar":
            return self._load_bar_field(symbol, field_name, frequency, start, end)
        elif source == "funding_rate":
            return self._load_funding_rate(symbol, start, end)
        elif source == "trade_tick":
            raise NotImplementedError(
                "trade_tick source (trade_imbalance) is not yet implemented in DataLayer"
            )
        else:
            raise ValueError(f"Unknown data source: {source!r}")

    # ------------------------------------------------------------------
    # Bar reader (reuses catalog.py private helpers via internal import)
    # ------------------------------------------------------------------

    def _load_bar_field(
        self,
        symbol: str,
        field_name: str,
        frequency: str,
        start: pd.Timestamp | None,
        end: pd.Timestamp | None,
    ) -> pd.Series:
        """Read one OHLCV field for a symbol from the Parquet catalog.

        Delegates entirely to ``nautilus_trader.persistence.catalog.ParquetDataCatalog``
        via the helpers already established in ``tinohelm.data.catalog``.
        """
        from nautilus_trader.persistence.catalog import ParquetDataCatalog
        # Reuse the private helpers from catalog.py — they live in the same package
        # and are the single source of truth for instrument/bar-type construction.
        from tinohelm.data.catalog import _make_instrument, _make_bar_type

        attr = _BAR_FIELD_ATTR.get(field_name)
        if attr is None:
            raise ValueError(
                f"Unknown bar field {field_name!r}. Supported: {list(_BAR_FIELD_ATTR)}"
            )

        instrument = _make_instrument(symbol)
        bar_type = _make_bar_type(instrument.id, frequency)
        bar_type_str = str(bar_type)

        catalog = ParquetDataCatalog(str(self._catalog_root))

        # Pass start/end as nanosecond integers if provided (NT accepts int ns)
        kwargs: dict = {"bar_types": [bar_type_str]}
        if start is not None:
            kwargs["start"] = int(start.value)  # pd.Timestamp.value is ns since epoch
        if end is not None:
            kwargs["end"] = int(end.value)

        try:
            bars = catalog.bars(**kwargs)
        except Exception:
            logger.warning(
                "catalog.bars failed for %s %s — returning empty series",
                symbol, bar_type_str, exc_info=True,
            )
            bars = []

        if not bars:
            return pd.Series(dtype="float64", name=symbol)

        timestamps = [
            pd.Timestamp(b.ts_event, unit="ns") for b in bars
        ]
        values = [float(getattr(b, attr)) for b in bars]

        series = pd.Series(values, index=pd.DatetimeIndex(timestamps), name=symbol)
        series = series.sort_index()
        series = series[~series.index.duplicated(keep="last")]
        return series

    # ------------------------------------------------------------------
    # Funding-rate reader (prefers Parquet; falls back to JSON)
    # ------------------------------------------------------------------

    def _load_funding_rate(
        self,
        symbol: str,
        start: pd.Timestamp | None,
        end: pd.Timestamp | None,
    ) -> pd.Series:
        """Read funding-rate data for a symbol and return a 8h-frequency Series.

        Priority
        --------
        1. Parquet at ``{catalog_root}/data/funding_rate/{symbol.lower()}.parquet``
           (written by pipeline after s13 upgrade).
        2. JSON at ``{funding_dir}/{symbol.lower()}.json``
           (legacy path — present on deployments that have not yet migrated).

        Both paths decode to the same Series shape:
        ``pd.Series`` with ``pd.DatetimeIndex`` (UTC-naive), values = float funding_rate.
        """
        series = self._load_funding_rate_parquet(symbol, start, end)
        if series is not None:
            return series
        return self._load_funding_rate_json(symbol, start, end)

    def _load_funding_rate_parquet(
        self,
        symbol: str,
        start: pd.Timestamp | None,
        end: pd.Timestamp | None,
    ) -> "pd.Series | None":
        """Try to load funding rate from Parquet; return None if not available."""
        from tinohelm.data.catalog import read_funding_rate_parquet

        try:
            df = read_funding_rate_parquet(symbol, self._catalog_root)
        except Exception:
            logger.warning(
                "Failed to read funding-rate Parquet for %s", symbol, exc_info=True
            )
            return None

        if df is None:
            # File does not exist — fall through to JSON
            return None

        if df.empty:
            return pd.Series(dtype="float64", name=symbol)

        timestamps = [pd.Timestamp(ts_ns, unit="ns") for ts_ns in df["ts_event"]]
        values = df["funding_rate"].tolist()

        series = pd.Series(values, index=pd.DatetimeIndex(timestamps), name=symbol)
        series = series.sort_index()
        series = series[~series.index.duplicated(keep="last")]

        if start is not None:
            series = series[series.index >= start]
        if end is not None:
            series = series[series.index <= end]

        logger.debug("Loaded %d funding-rate rows from Parquet for %s", len(series), symbol)
        return series

    def _load_funding_rate_json(
        self,
        symbol: str,
        start: pd.Timestamp | None,
        end: pd.Timestamp | None,
    ) -> pd.Series:
        """Read funding-rate JSON for a symbol (legacy fallback path).

        Format of each JSON record: ``{"funding_time_ms": int, "funding_rate": float}``.
        Path: ``{funding_dir}/{symbol.lower()}.json``.
        """
        path = self._funding_dir / f"{symbol.lower()}.json"
        if not path.exists():
            logger.debug("No funding-rate JSON for %s at %s", symbol, path)
            return pd.Series(dtype="float64", name=symbol)

        try:
            with open(path) as fh:
                records = json.load(fh)
        except (json.JSONDecodeError, OSError):
            logger.warning("Cannot read funding rate JSON for %s", symbol, exc_info=True)
            return pd.Series(dtype="float64", name=symbol)

        if not isinstance(records, list) or not records:
            return pd.Series(dtype="float64", name=symbol)

        timestamps = [
            pd.Timestamp(r["funding_time_ms"], unit="ms") for r in records
        ]
        values = [float(r["funding_rate"]) for r in records]

        series = pd.Series(values, index=pd.DatetimeIndex(timestamps), name=symbol)
        series = series.sort_index()
        series = series[~series.index.duplicated(keep="last")]

        # Apply time range filter
        if start is not None:
            series = series[series.index >= start]
        if end is not None:
            series = series[series.index <= end]

        return series

    # ------------------------------------------------------------------
    # Time alignment
    # ------------------------------------------------------------------

    def _align_time(
        self,
        series_by_symbol: dict[str, pd.Series],
        target_freq: str,
    ) -> Panel:
        """Align multiple symbol Series onto a common DatetimeIndex.

        For bar sources, the target index is the union of all symbol timestamps
        (or the bar-frequency grid if one series serves as the reference).

        For funding_rate sources, the series arrives at 8h frequency and is
        forward-filled onto whatever target index the caller provides.  The
        caller is responsible for passing the right target_freq.

        All missing values after alignment are NaN (not forward-filled here —
        ffill for funding_rate is handled separately).
        """
        # Build a Panel from all series aligned on the union index
        panel = pd.DataFrame(series_by_symbol)
        panel = panel.sort_index()
        return panel

    def _align_funding_onto_bar_index(
        self,
        funding_series: pd.Series,
        bar_index: pd.DatetimeIndex,
    ) -> pd.Series:
        """Apply as-of delay and forward-fill funding_rate onto bar index.

        PIT logic:
        - A funding rate published at time T is only observable *after* that
          settlement.  In Binance perpetuals, rates settle at 00:00, 08:00,
          16:00 UTC.  A bar at exactly 08:00 should NOT yet see the 08:00 rate
          — it becomes visible at the *next* bar.
        - Implementation: shift the funding Series by 1 period (one 8h step)
          on the 8h-frequency index *before* forward-filling onto the bar index.
          This is equivalent to ``method="ffill"`` with ``limit=None`` on the
          already-shifted series.

        Parameters
        ----------
        funding_series:
            8h-frequency Series (raw, already filtered to time range).
        bar_index:
            Target DatetimeIndex (bar frequency, e.g. 1m or 5m).

        Returns
        -------
        pd.Series
            Forward-filled funding values aligned to ``bar_index``, with the
            as-of delay applied.
        """
        if funding_series.empty:
            return pd.Series(
                index=bar_index, dtype="float64", name=funding_series.name
            )

        # Shift by 1 period at 8h frequency to implement as-of delay
        shifted = funding_series.shift(1)

        # Forward-fill onto the bar index via reindex
        aligned = shifted.reindex(bar_index.union(shifted.index)).ffill()
        # Keep only bar_index timestamps
        aligned = aligned.reindex(bar_index)
        aligned.name = funding_series.name
        return aligned

    # ------------------------------------------------------------------
    # PIT filtering
    # ------------------------------------------------------------------

    def _apply_pit(self, panel: Panel) -> Panel:
        """Apply Point-In-Time symbol filtering to a Panel.

        For each timestamp ``ts`` in the panel index, symbols not present in
        ``Universe.get_symbols_at(ts)`` are set to NaN.  The panel shape
        (index × columns) is preserved — rows are NOT dropped.

        Parameters
        ----------
        panel:
            Input panel with DatetimeIndex and symbol columns.

        Returns
        -------
        Panel
            Same shape panel with ineligible cells set to NaN.
        """
        if panel.empty:
            return panel

        # Build a mask: True where a cell should be NaN
        # Shape: (n_timestamps, n_symbols)
        mask = pd.DataFrame(False, index=panel.index, columns=panel.columns)

        # Group consecutive timestamps by their eligible symbol set to reduce
        # calls to get_symbols_at (which iterates over universe rows).
        # For small panels, we just call it per timestamp.
        all_symbols = list(panel.columns)
        for ts in panel.index:
            eligible = set(self._universe.get_symbols_at(ts))
            for sym in all_symbols:
                if sym not in eligible:
                    mask.loc[ts, sym] = True

        panel = panel.copy()
        panel[mask] = float("nan")
        return panel


# ---------------------------------------------------------------------------
# Composite load: bar + funding aligned together
# ---------------------------------------------------------------------------

def load_aligned(
    data_layer: DataLayer,
    bar_requests: list[DataRequest],
    funding_requests: list[DataRequest] | None = None,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
) -> dict[str, Panel]:
    """High-level helper: load bar panels, then align funding_rate onto bar index.

    This convenience function handles the two-step process:
    1. Load bar data → build bar DatetimeIndex.
    2. Load funding_rate per symbol → shift(1) + ffill onto bar index.

    Parameters
    ----------
    data_layer:
        Configured ``DataLayer`` instance.
    bar_requests:
        List of DataRequests with source="bar".
    funding_requests:
        Optional list of DataRequests with source="funding_rate".
    start, end:
        Time range overrides.

    Returns
    -------
    dict[str, Panel]
        Combined panel dict including both bar fields and ``"funding_rate"``.
    """
    result = data_layer.load(bar_requests, start=start, end=end)

    if not funding_requests:
        return result

    # Determine the reference bar index (use the first bar panel's index)
    bar_index: pd.DatetimeIndex | None = None
    for panel in result.values():
        if not panel.empty:
            bar_index = panel.index
            break

    if bar_index is None or len(bar_index) == 0:
        # No bar data — still load funding raw
        result.update(data_layer.load(funding_requests, start=start, end=end))
        return result

    # Load funding_rate per symbol and align onto bar index
    symbols_for_funding = [r.symbol for r in funding_requests]
    ts_start = _parse_ts(start) if start is not None else None
    ts_end = _parse_ts(end) if end is not None else None

    aligned_cols: dict[str, pd.Series] = {}
    for sym in symbols_for_funding:
        raw_series = data_layer._load_funding_rate(sym, ts_start, ts_end)
        aligned = data_layer._align_funding_onto_bar_index(raw_series, bar_index)
        aligned_cols[sym] = aligned

    if aligned_cols:
        funding_panel = pd.DataFrame(aligned_cols)
        funding_panel = data_layer._apply_pit(funding_panel)
        result["funding_rate"] = funding_panel

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_ts(value: str | pd.Timestamp) -> pd.Timestamp:
    """Parse a timestamp string or pd.Timestamp to a tz-naive pd.Timestamp."""
    if isinstance(value, pd.Timestamp):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    ts = pd.Timestamp(value)
    return ts.replace(tzinfo=None) if ts.tzinfo is not None else ts
