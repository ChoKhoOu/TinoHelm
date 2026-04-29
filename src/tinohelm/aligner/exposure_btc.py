"""BTCBetaExposure — rolling-window OLS beta against BTC returns.

PIT guarantee
-------------
beta[ts=t] is computed using returns from (t - window, t-1] inclusive.  The
rolling window's **upper bound is t** in standard rolling notation, meaning the
window includes bar t's own return.  Because returns are computed as
``pct_change()`` from close prices, the return at bar t uses close[t] and
close[t-1].  For cross-sectional neutralisation purposes this is standard
practice (the return at bar t is *observable* at bar t's close), and is
consistent with how the DataLayer loads bars: the bar at ts=t carries the
closing price at t.

Algorithm
---------
For each symbol s and timestamp t:

    beta[t, s] = cov(ret_s[t-W:t], ret_btc[t-W:t]) / var(ret_btc[t-W:t])

where ret = pct_change of close prices.  Rolling cov / var are computed via
the identity:

    cov(X, Y) = mean(X*Y) - mean(X)*mean(Y)
    var(Y)    = mean(Y^2) - mean(Y)^2

using polars ``rolling_mean(window_size=W)``.  Polars rolling propagates null
when any value in the window is null, matching pandas ``rolling().mean()``
default behaviour (``min_periods=window``).

Special cases
-------------
- BTC itself → beta = 1.0 (constant, by definition).
- BTC data missing → all non-BTC betas are null for affected timestamps.
- Rolling warmup: first ``window-1`` bars → null (insufficient history).
- Any symbol with missing data → null for that cell (propagated naturally).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import polars as pl

from tinohelm.aligner.utils import ns_to_datetime, pd_panel_to_polars

if TYPE_CHECKING:
    from tinohelm.factor.data_layer import DataLayer

logger = logging.getLogger(__name__)

# Default bar frequency used for close-price loading
_DEFAULT_FREQ = "1m"


class BTCBetaExposure:
    """Rolling-window OLS beta of each symbol's pct-change return against BTC.

    Parameters
    ----------
    window:
        Rolling window in bars.  Default 60.
    btc_symbol:
        The BTC perpetual symbol used as the market proxy.
    data_layer:
        Optional DataLayer instance.  When ``None``, a default DataLayer is
        constructed lazily (requires a configured universe and catalog).
    frequency:
        Bar frequency for close-price loading.  Default ``"1m"``.
    """

    name: str = "btc_beta"

    def __init__(
        self,
        window: int = 60,
        btc_symbol: str = "BTCUSDT-PERP",
        data_layer: "DataLayer | None" = None,
        frequency: str = _DEFAULT_FREQ,
    ) -> None:
        self._window = window
        self._btc_symbol = btc_symbol
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
        """Compute rolling-window beta for each symbol at each timestamp.

        Parameters
        ----------
        timestamps:
            Polars Datetime Series of length T.  These are the *target*
            timestamps; close data is loaded from (timestamps[0] - window bars)
            to timestamps[-1] to provide rolling warmup.
        symbols:
            Universe of symbols (length N).  BTC itself → 1.0.

        Returns
        -------
        pl.DataFrame
            Shape (T, N+1).  Column 0: ``"ts"`` (Datetime).
            Columns 1..N: symbol names (Float64).
            Warmup rows → null.  BTC self-beta → 1.0.
        """
        if len(timestamps) == 0 or not symbols:
            return pl.DataFrame({"ts": timestamps})

        target_ts_dtype = timestamps.dtype

        # Determine load range from target timestamps
        ts_int = timestamps.cast(pl.Int64).to_numpy()
        ts_min_ns = int(ts_int.min())
        ts_max_ns = int(ts_int.max())
        load_start = ns_to_datetime(ts_min_ns)
        load_end = ns_to_datetime(ts_max_ns)

        # Load close prices for all symbols + BTC (BTC may already be in symbols)
        all_symbols = list(symbols)
        if self._btc_symbol not in all_symbols:
            all_symbols = [self._btc_symbol] + all_symbols

        close_panel_pd = self._load_close(all_symbols, load_start, load_end)
        close_panel = pd_panel_to_polars(close_panel_pd, target_ts_dtype=target_ts_dtype)

        if self._btc_symbol not in close_panel.columns or close_panel.is_empty():
            # BTC data entirely absent → all betas null
            logger.warning(
                "BTCBetaExposure: BTC data missing for %s — all betas will be null",
                self._btc_symbol,
            )
            return self._null_frame(timestamps, symbols)

        btc_col = self._btc_symbol
        # Quick check: if BTC column is entirely null, we cannot compute betas
        if close_panel[btc_col].null_count() == len(close_panel):
            logger.warning(
                "BTCBetaExposure: BTC close column is all-null — all betas will be null",
            )
            return self._null_frame(timestamps, symbols)

        # Compute pct_change returns: ret_t = close_t / close_{t-1} - 1
        # Use polars expression — null propagation matches pandas pct_change()
        ret_exprs = [
            (pl.col(c) / pl.col(c).shift(1) - 1.0).alias(c)
            for c in close_panel.columns
            if c != "ts"
        ]
        rets = close_panel.select([pl.col("ts"), *ret_exprs])

        w = self._window

        # Rolling stats for BTC (used by all non-BTC symbols)
        rets = rets.with_columns(
            [
                pl.col(btc_col).rolling_mean(window_size=w).alias("__btc_mean"),
                (pl.col(btc_col) ** 2).rolling_mean(window_size=w).alias("__btc_sq_mean"),
            ]
        )
        rets = rets.with_columns(
            (pl.col("__btc_sq_mean") - pl.col("__btc_mean") ** 2).alias("__btc_var")
        )

        # Per-symbol rolling cov + beta = cov / var
        beta_exprs: list[pl.Expr] = []
        for sym in symbols:
            if sym == self._btc_symbol:
                # BTC self-beta = 1.0 everywhere (including warmup rows)
                beta_exprs.append(pl.lit(1.0).alias(sym))
                continue

            if sym not in rets.columns:
                beta_exprs.append(pl.lit(None, dtype=pl.Float64).alias(sym))
                continue

            # Compute rolling cov(sym, btc) = E[XY] - E[X] * E[Y]
            xy_mean = (pl.col(sym) * pl.col(btc_col)).rolling_mean(window_size=w)
            sym_mean = pl.col(sym).rolling_mean(window_size=w)
            cov_expr = xy_mean - sym_mean * pl.col("__btc_mean")
            # var(BTC) = 0 → beta = null (avoid div-by-zero); polars division
            # by 0 yields NaN/inf, so guard explicitly.
            var_safe = (
                pl.when(pl.col("__btc_var") == 0.0)
                .then(None)
                .otherwise(pl.col("__btc_var"))
            )
            beta = cov_expr / var_safe
            # Replace NaN (from edge cases) with null for consistency
            beta = pl.when(beta.is_nan()).then(None).otherwise(beta).cast(pl.Float64)
            beta_exprs.append(beta.alias(sym))

        beta_panel = rets.select([pl.col("ts"), *beta_exprs])

        # Align to the requested timestamps via left join
        target_df = pl.DataFrame({"ts": timestamps})
        out = target_df.join(beta_panel, on="ts", how="left")

        # If BTC self-beta should be 1.0 even on warmup rows — overwrite explicitly
        # because the join may have introduced nulls if target_ts is outside the
        # close_panel range.  We force BTC = 1.0 only on rows where target_ts
        # equals one of the close_panel ts values.  Rows outside that domain
        # remain null (consistent with "no data" semantics).
        if self._btc_symbol in symbols:
            close_ts_set = set(close_panel["ts"].to_list())
            in_domain = [t in close_ts_set for t in timestamps.to_list()]
            mask = pl.Series("__in", in_domain)
            out = out.with_columns(
                pl.when(mask)
                .then(pl.lit(1.0))
                .otherwise(pl.col(self._btc_symbol))
                .alias(self._btc_symbol)
            )

        # Drop rows columns we don't need; ensure final ordering = ts + symbols
        cols_order = ["ts", *symbols]
        out = out.select(cols_order)
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
        """Load close-price panel for all symbols with rolling warmup pre-fetch.

        The start is pushed back by ``window`` bars (in bar-frequency units) so
        the first valid output timestamp has a full rolling window.

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
                lookback=self._window,
                source="bar",
            )
            for sym in symbols
        ]

        try:
            panels = dl.load(requests, start=ts_start, end=ts_end)
        except Exception:
            logger.warning("BTCBetaExposure: DataLayer.load failed", exc_info=True)
            return None

        return panels.get("close")

    @staticmethod
    def _make_data_layer(symbols: list[str]) -> "DataLayer":
        """Construct a DataLayer with a permissive Universe covering ``symbols``."""
        from tinohelm.factor.data_layer import DataLayer
        from tinohelm.factor.universe import Universe

        return DataLayer(universe=Universe.from_symbols(symbols))

    def _null_frame(
        self,
        timestamps: pl.Series,
        symbols: list[str],
    ) -> pl.DataFrame:
        """Return a (T, N+1) frame where all exposure columns are null."""
        cols: dict[str, pl.Series] = {"ts": timestamps}
        n = len(timestamps)
        for sym in symbols:
            cols[sym] = pl.Series(sym, [None] * n, dtype=pl.Float64)
        return pl.DataFrame(cols)
