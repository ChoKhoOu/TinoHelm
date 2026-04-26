"""LogMcapExposure — log(market_cap) exposure panel.

Algorithm (v1, simplified)
--------------------------
For each timestamp t and symbol s:

    log_mcap[t, s] = ln(close[t, s] × circulating_supply[s])

where ``circulating_supply[s]`` is the **current snapshot** (fetched once per
symbol with a 24 h file cache).  Using a static supply scalar does not
introduce cross-sectional look-ahead bias for *ranking* purposes — only
absolute magnitudes drift from historical truth.  This simplification is
explicitly documented in ``DataLayer._load_market_cap_field``.

PIT note
--------
``close[t, s]`` is the bar-closing price at time t; it is observable at t's
close.  The supply scalar is a non-time-varying multiplier applied uniformly.
Therefore ``log_mcap[t, s]`` is PIT-safe for cross-sectional ranking.

Cross-sectional normalisation is deliberately *not* applied here — that is the
responsibility of the Aligner neutralisation layer.  LogMcapExposure returns
raw ln(mcap) values.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

import polars as pl

from tinohelm.aligner.utils import ns_to_datetime, pd_panel_to_polars

# Module-level import so the function can be patched in tests via
# ``patch("tinohelm.aligner.exposure_logmcap.fetch_circulating_supply", ...)``.
from tinohelm.data.instruments import fetch_circulating_supply

if TYPE_CHECKING:
    from tinohelm.factor.data_layer import DataLayer

logger = logging.getLogger(__name__)

_DEFAULT_FREQ = "1m"


class LogMcapExposure:
    """log(close × circulating_supply) exposure panel, PIT per bar.

    Parameters
    ----------
    data_layer:
        Optional DataLayer instance.  When ``None``, a minimal DataLayer is
        constructed using a permissive Universe covering the requested symbols.
    frequency:
        Bar frequency for close-price loading.  Default ``"1m"``.
    """

    name: str = "log_mcap"

    def __init__(
        self,
        data_layer: "DataLayer | None" = None,
        frequency: str = _DEFAULT_FREQ,
    ) -> None:
        self._data_layer = data_layer
        self._frequency = frequency

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_exposure(
        self,
        timestamps: pl.Series,
        symbols: list[str],
    ) -> pl.DataFrame:
        """Return log(market_cap) panel aligned to ``timestamps``.

        Parameters
        ----------
        timestamps:
            Polars Datetime Series of length T.
        symbols:
            Universe symbol list (length N).

        Returns
        -------
        pl.DataFrame
            Shape (T, N+1).  Column 0: ``"ts"`` (Datetime).
            Columns 1..N: symbol names (Float64).
            Missing close or supply → null for that cell/column.
        """
        if len(timestamps) == 0 or not symbols:
            return pl.DataFrame({"ts": timestamps})

        target_ts_dtype = timestamps.dtype

        ts_int = timestamps.cast(pl.Int64).to_numpy()
        ts_min_ns = int(ts_int.min())
        ts_max_ns = int(ts_int.max())
        load_start = ns_to_datetime(ts_min_ns)
        load_end = ns_to_datetime(ts_max_ns)

        # Step 1: load close prices for all symbols
        close_panel_pd = self._load_close(symbols, load_start, load_end)
        close_panel = pd_panel_to_polars(close_panel_pd, target_ts_dtype=target_ts_dtype)

        # Step 2: fetch circulating supply per symbol (one call each, cached)
        supplies = self._fetch_supplies(symbols)

        # Step 3: compute log(close × supply) per symbol via polars expressions
        n = len(timestamps)
        target_df = pl.DataFrame({"ts": timestamps})

        if close_panel.is_empty():
            # No close data at all → entire output null
            null_cols = {sym: pl.Series(sym, [None] * n, dtype=pl.Float64) for sym in symbols}
            return target_df.with_columns(**null_cols).select(["ts", *symbols])

        # Build log_mcap exprs for symbols that have both close + supply.
        # For symbols missing either, we'll add a null column later.
        log_exprs: list[pl.Expr] = []
        null_syms: list[str] = []
        for sym in symbols:
            supply = supplies.get(sym)
            has_close = sym in close_panel.columns
            if supply is None or supply <= 0.0 or not has_close:
                null_syms.append(sym)
                continue
            # mcap = close * supply; log_mcap = ln(mcap) where mcap > 0 else null
            close_expr = pl.col(sym)
            mcap_expr = close_expr * supply
            # Only take log of strictly positive values; zero / negative / null → null
            log_expr = (
                pl.when((mcap_expr.is_not_null()) & (mcap_expr > 0.0))
                .then(mcap_expr.log())
                .otherwise(None)
                .alias(sym)
            )
            log_exprs.append(log_expr)

        if log_exprs:
            mcap_panel = close_panel.select([pl.col("ts"), *log_exprs])
        else:
            mcap_panel = close_panel.select([pl.col("ts")])

        # Add the null columns for symbols missing supply or close
        if null_syms:
            mcap_panel = mcap_panel.with_columns(
                [pl.lit(None, dtype=pl.Float64).alias(s) for s in null_syms]
            )

        # Align to the requested timestamps via left join (preserves the order of `timestamps`)
        out = target_df.join(mcap_panel, on="ts", how="left")
        out = out.select(["ts", *symbols])
        return out

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_close(
        self,
        symbols: list[str],
        ts_start: datetime,
        ts_end: datetime,
    ) -> Any:
        """Load close-price panel from the DataLayer.

        Returns the DataLayer's pandas-style panel verbatim — the caller is
        responsible for converting to polars via ``pd_panel_to_polars``.
        """
        from tinohelm.factor.data_layer import DataLayer
        from tinohelm.factor.types import DataRequest

        dl = self._data_layer or self._make_data_layer(symbols)

        requests = [
            DataRequest(
                symbol=sym,
                field_name="close",
                frequency=self._frequency,
                lookback=1,
                source="bar",
            )
            for sym in symbols
        ]

        try:
            panels = dl.load(requests, start=ts_start, end=ts_end)
        except Exception:
            logger.warning("LogMcapExposure: DataLayer.load failed", exc_info=True)
            return None

        return panels.get("close")

    def _fetch_supplies(self, symbols: list[str]) -> dict[str, float | None]:
        """Fetch circulating supply for each symbol; return None on failure."""
        result: dict[str, float | None] = {}
        for sym in symbols:
            try:
                supply = fetch_circulating_supply(sym)
                result[sym] = supply
            except Exception:
                logger.warning(
                    "LogMcapExposure: could not fetch circulating supply for %s — "
                    "column will be null",
                    sym,
                    exc_info=True,
                )
                result[sym] = None
        return result

    @staticmethod
    def _make_data_layer(symbols: list[str]) -> "DataLayer":
        """Construct a DataLayer with a permissive Universe covering ``symbols``."""
        from tinohelm.factor.data_layer import DataLayer
        from tinohelm.factor.universe import Universe

        return DataLayer(universe=Universe.from_symbols(symbols))

