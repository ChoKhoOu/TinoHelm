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

Polars-native contract
----------------------
This module is the *only* layer in :mod:`tinohelm.factor` that touches the
NautilusTrader Parquet catalog (which natively returns ``pandas`` frames).
NT outputs are converted to :class:`polars.DataFrame` immediately on the
boundary so the rest of the framework stays pandas-free.

Internal Series helpers (``_load_*``) return a 2-column ``polars.DataFrame``
with columns ``[ts, value]`` (Datetime[ns], Float64). The public
:meth:`DataLayer.load` returns a ``dict[str, Panel]`` where each ``Panel``
is a ``polars.DataFrame`` with columns ``[ts, sym1, sym2, ...]`` —
matching :data:`tinohelm.factor.types.Panel`.

Supported field_name values
---------------------------
Bar fields (source="bar"):
    ``close``, ``open``, ``high``, ``low``, ``volume``
Funding rate (source="funding_rate"):
    ``funding_rate``
Market cap (source="market_cap"):
    ``market_cap``  (close × current circulating_supply snapshot; non-PIT for
    historical research unless replaced with a dated supply series)
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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Sequence

import polars as pl

from tinohelm.core.paths import paths
from tinohelm.factor.types import DataRequest, Panel
from tinohelm.factor.universe import Universe

logger = logging.getLogger(__name__)

# Column name conventions for the internal 2-col series frames and the
# wide-table panel returned by :meth:`DataLayer.load`.
_TS_COL: str = "ts"
_VAL_COL: str = "value"

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


# ---------------------------------------------------------------------------
# Internal helpers — series construction
# ---------------------------------------------------------------------------

def _empty_series_frame() -> pl.DataFrame:
    """Return an empty 2-col ``[ts, value]`` polars frame with correct dtypes."""
    return pl.DataFrame(
        {_TS_COL: [], _VAL_COL: []},
        schema={_TS_COL: pl.Datetime("ns"), _VAL_COL: pl.Float64},
    )


def _build_series_frame(
    timestamps: Sequence[datetime],
    values: Sequence[float],
) -> pl.DataFrame:
    """Construct a sorted, deduplicated ``[ts, value]`` frame.

    Mirrors the legacy pandas ``Series.sort_index()`` +
    ``Series[~index.duplicated(keep="last")]`` behaviour used by the
    pre-polars implementation.
    """
    if not timestamps:
        return _empty_series_frame()
    df = pl.DataFrame(
        {_TS_COL: list(timestamps), _VAL_COL: [float(v) for v in values]},
        schema={_TS_COL: pl.Datetime("ns"), _VAL_COL: pl.Float64},
    )
    # Sort then keep the *last* row per timestamp so duplicates resolve to
    # the most recent value (matches ``keep="last"``).
    df = df.sort(_TS_COL).unique(subset=[_TS_COL], keep="last", maintain_order=True)
    return df


def _first_panel_timestamps(panels: dict[str, Panel]) -> list[datetime]:
    """Return the first non-empty panel timestamp index from a loaded panel dict."""
    for panel in panels.values():
        if not panel.is_empty() and _TS_COL in panel.columns:
            return panel[_TS_COL].to_list()
    return []


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
        self._catalog_root = Path(catalog_root) if catalog_root is not None else paths.get("catalog")
        self._funding_dir = Path(funding_dir) if funding_dir is not None else paths.get("funding_rates")
        self._max_workers = max_workers

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(
        self,
        request: DataRequest | list[DataRequest],
        start: str | datetime | None = None,
        end: str | datetime | None = None,
    ) -> dict[str, Panel]:
        """Load data for one or more DataRequests and return a Panel dict.

        Parameters
        ----------
        request:
            A single ``DataRequest`` or a list thereof.  Requests with the
            same ``field_name`` are grouped into one Panel (columns = symbols).
        start:
            Override start for all requests.  ISO-8601 string or ``datetime``.
        end:
            Override end for all requests.  ISO-8601 string or ``datetime``.

        Returns
        -------
        dict[str, Panel]
            Keys are ``field_name`` strings; values are :class:`polars.DataFrame`
            panels with ``[ts, sym1, sym2, ...]`` columns (UTC, tz-naive
            ``Datetime("ns")``).
        """
        requests: list[DataRequest] = (
            [request] if isinstance(request, DataRequest) else list(request)
        )

        # Group by (field_name, frequency, source) — each group → one Panel
        groups: dict[tuple[str, str, str], list[DataRequest]] = defaultdict(list)
        for req in requests:
            groups[(req.field_name, req.frequency, req.source)].append(req)

        result: dict[str, Panel] = {}
        funding_groups: list[tuple[tuple[str, str, str], list[DataRequest]]] = []
        for key, reqs in groups.items():
            field_name, frequency, source = key
            if source == "funding_rate":
                # Funding panels need a bar-frequency reference index.  Defer
                # them until bar groups have been loaded so mixed requests like
                # [close, funding_rate] use the same timestamp grid.
                funding_groups.append((key, reqs))
                continue

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

        for (field_name, frequency, source), reqs in funding_groups:
            symbols = [r.symbol for r in reqs]
            lookback = max(r.lookback for r in reqs)
            reference_ts = _first_panel_timestamps(result)

            panel = self._load_panel(
                symbols=symbols,
                field_name=field_name,
                frequency=frequency,
                source=source,
                start=start,
                end=end,
                lookback=lookback,
                reference_ts=reference_ts,
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
        start: str | datetime | None,
        end: str | datetime | None,
        lookback: int,
        reference_ts: Sequence[datetime] | None = None,
    ) -> Panel:
        """Load one Panel (time × symbol) for a given field/frequency/source."""
        ts_start = _parse_ts(start) if start is not None else None
        ts_end = _parse_ts(end) if end is not None else None

        # Apply the warmup lookback offset: shift ``start`` earlier by
        # ``lookback × bar_duration`` so the kernel has enough history for its
        # first valid output inside the requested window.  Without this, a
        # factor like ``ret_N(lookback=20)`` would produce NaN for the first
        # 20 bars of every requested range (see Bug 008).
        ts_load_start = ts_start
        if ts_start is not None:
            offset_ns = _lookback_offset_ns(frequency, lookback)
            if offset_ns is not None:
                # ``datetime`` arithmetic uses microsecond precision; the
                # smallest bar interval supported (1m) is 60 × 10⁹ ns so
                # ns→μs truncation never loses data here.
                ts_load_start = ts_start - timedelta(microseconds=offset_ns // 1_000)

        # Parallel per-symbol load — each future returns a 2-col [ts, value] frame.
        series_by_symbol: dict[str, pl.DataFrame] = {}
        futures: dict = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            for sym in symbols:
                fut = pool.submit(
                    self._load_table,
                    symbol=sym,
                    field_name=field_name,
                    source=source,
                    frequency=frequency,
                    start=ts_load_start,
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
                    series_by_symbol[sym] = _empty_series_frame()

        if not series_by_symbol:
            return pl.DataFrame()

        # Determine target bar-frequency index (from bar data)
        if source == "bar":
            panel = self._align_time(series_by_symbol)
        elif source == "funding_rate":
            # Apply as-of delay (shift(1) + ffill) onto a real bar-frequency
            # index.  Raw funding timestamps are sparse 8h settlement points;
            # using them as the target grid would misrepresent funding factors
            # as 8h-only panels and break mixed bar/funding evaluation.
            bar_ts = list(reference_ts or [])
            if not bar_ts:
                bar_ts = self._load_bar_reference_index(
                    symbols=symbols,
                    frequency=frequency,
                    start=ts_load_start,
                    end=ts_end,
                )
            bar_ts = sorted(dict.fromkeys(bar_ts))
            aligned_cols: dict[str, pl.DataFrame] = {
                sym: self._align_funding_onto_bar_index(series, bar_ts)
                for sym, series in series_by_symbol.items()
            }
            panel = (
                self._align_time(aligned_cols)
                if bar_ts
                else pl.DataFrame(
                    {col: [] for col in [_TS_COL, *symbols]},
                    schema={
                        _TS_COL: pl.Datetime("ns"),
                        **{s: pl.Float64 for s in symbols},
                    },
                )
            )
        else:
            # trade_tick / market_cap and others: simple union-index align.
            panel = self._align_time(series_by_symbol)

        # Apply PIT filter
        panel = self._apply_pit(panel)

        return panel

    def _load_table(
        self,
        symbol: str,
        field_name: str,
        source: str,
        frequency: str,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
        """Load a single (symbol, field, freq) time series.

        Returns a ``polars.DataFrame`` with columns ``[ts, value]``
        (Datetime[ns] UTC tz-naive, Float64).
        """
        if source == "bar":
            return self._load_bar_field(symbol, field_name, frequency, start, end)
        elif source == "funding_rate":
            return self._load_funding_rate(symbol, start, end)
        elif source == "market_cap":
            return self._load_market_cap_field(symbol, frequency, start, end)
        elif source == "trade_tick":
            raise NotImplementedError(
                "trade_tick source (trade_imbalance) is not yet implemented in DataLayer"
            )
        else:
            raise ValueError(f"Unknown data source: {source!r}")

    def _load_bar_reference_index(
        self,
        symbols: Sequence[str],
        frequency: str,
        start: datetime | None,
        end: datetime | None,
    ) -> list[datetime]:
        """Load the union close-bar timestamp grid for funding alignment.

        Funding-rate inputs are sparse settlement events.  When callers ask
        for funding without also requesting a bar field, load close bars only
        for their timestamps so ``DataLayer.load()`` still returns a panel on
        the intended bar cadence.
        """
        ts_values: set[datetime] = set()
        for sym in symbols:
            try:
                close_series = self._load_bar_field(sym, "close", frequency, start, end)
            except Exception:
                logger.warning(
                    "Failed to load bar reference index for funding_rate symbol %s",
                    sym,
                    exc_info=True,
                )
                continue
            if not close_series.is_empty() and _TS_COL in close_series.columns:
                ts_values.update(close_series[_TS_COL].to_list())
        return sorted(ts_values)

    # ------------------------------------------------------------------
    # Bar reader (reuses catalog.py private helpers via internal import)
    # ------------------------------------------------------------------

    def _load_bar_field(
        self,
        symbol: str,
        field_name: str,
        frequency: str,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
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

        # Pass start/end as nanosecond integers if provided (NT accepts int ns).
        kwargs: dict = {"bar_types": [bar_type_str]}
        if start is not None:
            kwargs["start"] = _datetime_to_ns(start)
        if end is not None:
            kwargs["end"] = _datetime_to_ns(end)

        try:
            bars = catalog.bars(**kwargs)
        except Exception:
            logger.warning(
                "catalog.bars failed for %s %s — returning empty series",
                symbol, bar_type_str, exc_info=True,
            )
            bars = []

        if not bars:
            return _empty_series_frame()

        timestamps = [_ns_to_datetime(int(b.ts_event)) for b in bars]
        values = [float(getattr(b, attr)) for b in bars]

        return _build_series_frame(timestamps, values)

    # ------------------------------------------------------------------
    # Market-cap reader (close × current circulating_supply snapshot, non-PIT)
    # ------------------------------------------------------------------

    def _load_market_cap_field(
        self,
        symbol: str,
        frequency: str,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
        """Compute market-cap series as ``close × circulating_supply``.

        PIT note
        --------
        Binance does not expose historical circulating-supply snapshots here.
        This implementation uses the **current snapshot** (fetched with a 24 h
        cache) and applies it uniformly across all bars.  That is non-PIT for
        historical research because supply is a symbol-specific time-varying
        series and the latest snapshot can change historical cross-sectional
        ranks.  Use only for latest/exploratory views unless a dated supply
        series is wired in.
        """
        from tinohelm.data.instruments import fetch_circulating_supply

        # --- Load close prices (reuse bar reader) ---
        close_frame = self._load_bar_field(symbol, "close", frequency, start, end)
        if close_frame.is_empty():
            return _empty_series_frame()

        # --- Fetch circulating supply (24h-cached) ---
        try:
            circulating = fetch_circulating_supply(symbol)
        except Exception:
            logger.warning(
                "Could not fetch circulating supply for %s; returning empty series",
                symbol,
                exc_info=True,
            )
            return _empty_series_frame()

        # --- mcap = close × current circulating snapshot (non-PIT historically) ---
        return close_frame.with_columns(
            (pl.col(_VAL_COL) * float(circulating)).alias(_VAL_COL),
        )

    # ------------------------------------------------------------------
    # Funding-rate reader (prefers Parquet; falls back to JSON)
    # ------------------------------------------------------------------

    def _load_funding_rate(
        self,
        symbol: str,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
        """Read funding-rate data for a symbol and return a 8h-frequency frame.

        Priority
        --------
        1. Parquet at ``{catalog_root}/data/funding_rate/{symbol.lower()}.parquet``
           (written by pipeline after s13 upgrade).
        2. JSON at ``{funding_dir}/{symbol.lower()}.json``
           (legacy path — present on deployments that have not yet migrated).

        Both paths decode to the same shape: a 2-column polars frame
        ``[ts, value]`` (UTC-naive ``Datetime("ns")`` and ``Float64``).
        """
        frame = self._load_funding_rate_parquet(symbol, start, end)
        if frame is not None:
            return frame
        return self._load_funding_rate_json(symbol, start, end)

    def _load_funding_rate_parquet(
        self,
        symbol: str,
        start: datetime | None,
        end: datetime | None,
    ) -> "pl.DataFrame | None":
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

        # ``read_funding_rate_parquet`` returns pandas (NT pyarrow→pandas) — convert at the boundary.
        if df.empty:
            return _empty_series_frame()

        timestamps = [_ns_to_datetime(int(ts_ns)) for ts_ns in df["ts_event"]]
        values = [float(v) for v in df["funding_rate"]]
        frame = _build_series_frame(timestamps, values)

        return _filter_time_range(frame, start, end)

    def _load_funding_rate_json(
        self,
        symbol: str,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
        """Read funding-rate JSON for a symbol (legacy fallback path).

        Format of each JSON record: ``{"funding_time_ms": int, "funding_rate": float}``.
        Path: ``{funding_dir}/{symbol.lower()}.json``.
        """
        path = self._funding_dir / f"{symbol.lower()}.json"
        if not path.exists():
            logger.debug("No funding-rate JSON for %s at %s", symbol, path)
            return _empty_series_frame()

        try:
            with open(path) as fh:
                records = json.load(fh)
        except (json.JSONDecodeError, OSError):
            logger.warning("Cannot read funding rate JSON for %s", symbol, exc_info=True)
            return _empty_series_frame()

        if not isinstance(records, list) or not records:
            return _empty_series_frame()

        timestamps = [_ms_to_datetime(int(r["funding_time_ms"])) for r in records]
        values = [float(r["funding_rate"]) for r in records]
        frame = _build_series_frame(timestamps, values)

        return _filter_time_range(frame, start, end)

    # ------------------------------------------------------------------
    # Time alignment
    # ------------------------------------------------------------------

    def _align_time(
        self,
        series_by_symbol: dict[str, pl.DataFrame],
    ) -> Panel:
        """Align multiple symbol Series onto a common ``ts`` axis.

        The output is a wide-table Panel ``[ts, sym1, sym2, ...]``: each
        per-symbol 2-col ``[ts, value]`` frame is renamed to ``[ts, symbol]``
        and outer-joined on ``ts`` (union index), then sorted by ``ts``.

        For bar sources, the union index *is* the bar grid because every input
        already lives on the same bar frequency.  For funding_rate the caller
        is responsible for forward-filling / aligning onto the bar index
        before this routine sees it.
        """
        if not series_by_symbol:
            return pl.DataFrame()

        symbols = list(series_by_symbol.keys())

        # Drop frames that have no data so they don't kill the join.
        non_empty = [
            (sym, frame)
            for sym, frame in series_by_symbol.items()
            if not frame.is_empty()
        ]
        if not non_empty:
            # All inputs empty — return an empty wide panel preserving column order.
            return pl.DataFrame(
                {col: [] for col in [_TS_COL, *symbols]},
                schema={_TS_COL: pl.Datetime("ns"), **{s: pl.Float64 for s in symbols}},
            )

        # Outer-join all per-symbol frames on ``ts`` (union of timestamps).
        first_sym, first_frame = non_empty[0]
        panel = first_frame.rename({_VAL_COL: first_sym})
        for sym, frame in non_empty[1:]:
            panel = panel.join(
                frame.rename({_VAL_COL: sym}),
                on=_TS_COL,
                how="full",
                coalesce=True,
            )

        # Restore original symbol-column order, keeping all-empty columns as
        # all-null — preserves the legacy pandas ``DataFrame(dict_of_series)``
        # behaviour that downstream callers rely on.
        for sym in symbols:
            if sym not in panel.columns:
                panel = panel.with_columns(
                    pl.lit(None, dtype=pl.Float64).alias(sym),
                )

        return panel.select([_TS_COL, *symbols]).sort(_TS_COL)

    def _align_funding_onto_bar_index(
        self,
        funding_series: pl.DataFrame,
        bar_ts: Sequence[datetime],
    ) -> pl.DataFrame:
        """Apply as-of delay and forward-fill funding_rate onto bar index.

        PIT logic:
        - A funding rate published at time T is only observable *after* that
          settlement.  In Binance perpetuals, rates settle at 00:00, 08:00,
          16:00 UTC.  A bar at exactly 08:00 should NOT yet see the 08:00 rate
          — it becomes visible at the *next* bar.
        - Implementation: shift the funding series by 1 period (one 8h step)
          on the 8h-frequency index *before* forward-filling onto the bar index.
          This is equivalent to ``method="ffill"`` with ``limit=None`` on the
          already-shifted series.

        Parameters
        ----------
        funding_series:
            2-col ``[ts, value]`` polars frame (8h frequency, raw, already
            filtered to time range).
        bar_ts:
            Sequence of ``datetime`` values defining the target bar index
            (e.g. 1m or 5m).

        Returns
        -------
        polars.DataFrame
            2-col ``[ts, value]`` frame aligned to ``bar_ts`` with the as-of
            delay + forward-fill applied.  Missing bars before the first
            observable value are ``null``.
        """
        target = pl.DataFrame(
            {_TS_COL: list(bar_ts)},
            schema={_TS_COL: pl.Datetime("ns")},
        )

        if funding_series.is_empty() or len(bar_ts) == 0:
            return target.with_columns(pl.lit(None, dtype=pl.Float64).alias(_VAL_COL))

        # Shift by 1 period on the native 8h grid to apply the as-of delay.
        shifted = funding_series.with_columns(pl.col(_VAL_COL).shift(1))

        # Forward-fill onto the union of the bar timestamps and the shifted
        # 8h grid, then restrict to the bar grid.
        union = (
            target.select(_TS_COL)
            .vstack(shifted.select(_TS_COL))
            .unique(maintain_order=False)
            .sort(_TS_COL)
        )
        merged = union.join(shifted, on=_TS_COL, how="left").with_columns(
            pl.col(_VAL_COL).forward_fill()
        )
        # Inner-join back onto the bar timestamps in the requested order.
        return target.join(merged, on=_TS_COL, how="left")

    # ------------------------------------------------------------------
    # PIT filtering
    # ------------------------------------------------------------------

    def _apply_pit(self, panel: Panel) -> Panel:
        """Apply Point-In-Time symbol filtering to a Panel.

        For each timestamp ``ts`` in the panel index, symbols not present in
        ``Universe.get_symbols_at(ts)`` are set to ``null``.  The panel shape
        (rows × columns) is preserved — rows are NOT dropped.

        Vectorised implementation: precompute per-symbol (eligible_from,
        delisting_date) boundaries from the Universe, then build column-level
        boolean masks via polars expressions.

        Parameters
        ----------
        panel:
            Input panel with ``ts`` column and N symbol columns.

        Returns
        -------
        Panel
            Same shape panel with ineligible cells set to ``null``.
        """
        if panel.is_empty() or _TS_COL not in panel.columns:
            return panel

        boundaries = self._universe.get_symbol_boundaries()

        symbol_cols = [c for c in panel.columns if c != _TS_COL]
        exprs: list[pl.Expr] = []
        for sym in symbol_cols:
            boundary = boundaries.get(sym)
            if boundary is None:
                # Symbol not in universe at all → entire column null
                exprs.append(pl.lit(None, dtype=pl.Float64).alias(sym))
                continue

            eligible_from, delisting_date = boundary
            # Mask cells where ts < eligible_from
            mask_expr = pl.col(_TS_COL) >= pl.lit(eligible_from)
            if delisting_date is not None:
                mask_expr = mask_expr & (pl.col(_TS_COL) < pl.lit(delisting_date))
            exprs.append(
                pl.when(mask_expr)
                .then(pl.col(sym))
                .otherwise(None)
                .alias(sym)
            )

        return panel.with_columns(exprs)


# ---------------------------------------------------------------------------
# Composite load: bar + funding aligned together
# ---------------------------------------------------------------------------

def load_aligned(
    data_layer: DataLayer,
    bar_requests: list[DataRequest],
    funding_requests: list[DataRequest] | None = None,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
) -> dict[str, Panel]:
    """High-level helper: load bar panels, then align funding_rate onto bar index.

    This convenience function handles the two-step process:
    1. Load bar data → build bar timestamp index.
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

    # Determine the reference bar index (use the first non-empty bar panel).
    bar_ts: list[datetime] = []
    for panel in result.values():
        if not panel.is_empty() and _TS_COL in panel.columns:
            bar_ts = panel[_TS_COL].to_list()
            break

    if not bar_ts:
        # No bar data — still load funding raw
        result.update(data_layer.load(funding_requests, start=start, end=end))
        return result

    # Load funding_rate per symbol and align onto bar index.
    symbols_for_funding = [r.symbol for r in funding_requests]
    ts_start = _parse_ts(start) if start is not None else None
    ts_end = _parse_ts(end) if end is not None else None

    aligned_cols: dict[str, pl.DataFrame] = {}
    for sym in symbols_for_funding:
        raw_series = data_layer._load_funding_rate(sym, ts_start, ts_end)
        aligned = data_layer._align_funding_onto_bar_index(raw_series, bar_ts)
        aligned_cols[sym] = aligned

    if aligned_cols:
        funding_panel = data_layer._align_time(aligned_cols)
        funding_panel = data_layer._apply_pit(funding_panel)
        result["funding_rate"] = funding_panel

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_ts(value: str | datetime) -> datetime:
    """Parse a timestamp string or :class:`datetime` to a tz-naive ``datetime``.

    Accepts:
    - ISO-8601 strings (``"2024-01-01"``, ``"2024-01-01T00:00:00"``,
      ``"2024-01-01T00:00:00+00:00"`` — tz info is stripped).
    - Native ``datetime`` instances (tz info stripped).

    Pandas Timestamp instances are accepted via duck typing
    (``isoformat`` + ``tzinfo`` attributes) so legacy callers still work
    without forcing this module to import pandas.
    """
    if isinstance(value, datetime):
        return (
            value.astimezone(UTC).replace(tzinfo=None)
            if value.tzinfo is not None
            else value
        )
    # Duck-typed pandas Timestamp passthrough — keeps backward compatibility
    # with callers that still construct ``pandas Timestamp`` values.
    if hasattr(value, "isoformat") and hasattr(value, "tzinfo"):
        as_str = value.isoformat()  # type: ignore[union-attr]
        return _parse_ts(as_str)
    return _parse_iso8601(str(value))


def _parse_iso8601(value: str) -> datetime:
    """Parse an ISO-8601 string to a tz-naive ``datetime``.

    Accepts trailing ``Z`` as UTC and strips tz info to match the project's
    naive-datetime convention.
    """
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(s)
    except ValueError:
        # Fallback for date-only strings (datetime.fromisoformat handles this
        # since 3.11; older paths kept for safety).
        ts = datetime.strptime(s, "%Y-%m-%d")
    return ts.astimezone(UTC).replace(tzinfo=None) if ts.tzinfo is not None else ts


def _datetime_to_ns(value: datetime) -> int:
    """Convert a tz-naive ``datetime`` to nanoseconds since the Unix epoch.

    ``datetime`` only stores microsecond precision so the result is exact at
    the ns boundary up to ``1_000`` × the microsecond count.
    """
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    epoch = datetime(1970, 1, 1)
    delta = value - epoch
    seconds = delta.days * 86_400 + delta.seconds
    return seconds * 1_000_000_000 + delta.microseconds * 1_000


def _ns_to_datetime(ns: int) -> datetime:
    """Convert nanoseconds-since-epoch to a tz-naive ``datetime``.

    Microsecond-precision is preserved; sub-microsecond information is
    silently truncated (Python ``datetime`` does not represent it).
    """
    seconds, remainder = divmod(int(ns), 1_000_000_000)
    micros = remainder // 1_000
    base = datetime(1970, 1, 1) + timedelta(seconds=seconds)
    return base.replace(microsecond=micros)


def _ms_to_datetime(ms: int) -> datetime:
    """Convert milliseconds-since-epoch to a tz-naive ``datetime``."""
    seconds, remainder_ms = divmod(int(ms), 1_000)
    base = datetime(1970, 1, 1) + timedelta(seconds=seconds)
    return base.replace(microsecond=remainder_ms * 1_000)


def _filter_time_range(
    frame: pl.DataFrame,
    start: datetime | None,
    end: datetime | None,
) -> pl.DataFrame:
    """Inclusive time-range filter on ``ts`` for a 2-col ``[ts, value]`` frame."""
    if frame.is_empty() or (start is None and end is None):
        return frame
    expr = pl.lit(True)
    if start is not None:
        expr = expr & (pl.col(_TS_COL) >= pl.lit(start))
    if end is not None:
        expr = expr & (pl.col(_TS_COL) <= pl.lit(end))
    return frame.filter(expr)


def _lookback_offset_ns(frequency: str, lookback: int) -> int | None:
    """Return ``lookback * bar_duration`` in nanoseconds, or ``None`` when no
    offset should be applied.

    Returns ``None`` when either the frequency is not a known NT bar interval
    (e.g. ``"tick"`` — such sources are not supported by the loader anyway)
    or ``lookback`` is non-positive.  Callers treat a ``None`` result as
    "leave ``start`` unchanged".
    """
    if lookback <= 0:
        return None
    try:
        from tinohelm.data.catalog_helpers import interval_to_nanoseconds
        return interval_to_nanoseconds(frequency) * lookback
    except ValueError:
        logger.debug(
            "_lookback_offset_ns: frequency %r is not a known bar interval; "
            "skipping warmup offset",
            frequency,
        )
        return None
