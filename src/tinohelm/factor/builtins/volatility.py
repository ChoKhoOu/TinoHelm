"""Volatility factors — parkinson_vol, vol_ratio.

All kernels operate on a polars wide-table :data:`Panel`
(``column "ts" + N symbol columns``) and return the same shape.

Numerical alignment with the pre-polars pandas implementation is asserted
in ``tests/factor/test_builtins.py`` via the regression oracle parquet
(absolute difference <= 1e-6).
"""
from __future__ import annotations

import math

import polars as pl

from tinohelm.factor.decorator import factor
from tinohelm.factor.types import Panel


_TS_COL = "ts"


def _value_cols(panel: Panel) -> list[str]:
    return [c for c in panel.columns if c != _TS_COL]


@factor(
    category="波动",
    lookback=20,
    params={"lookback": 20},
    description="Parkinson 波动率 — high/low range estimator",
)
def parkinson_vol(high: Panel, low: Panel, params=None) -> Panel:
    """Parkinson volatility estimator using the H/L range.

    Mirrors the pandas implementation::

        log_hl = np.log(df["high"] / (df["low"] + 1e-12))
        return np.sqrt((log_hl ** 2).rolling(n).mean() / (4 * np.log(2)))
    """
    n = (params or {}).get("lookback", 20)
    cols = _value_cols(high)
    if not cols:
        return high.clone()

    # Pull ``low`` columns through join — both panels share the same ``ts``
    # axis and column ordering by contract.  Compute per-symbol expression on
    # ``high`` and inject the ``low`` series via ``pl.lit``-free column refs.
    factor_const = 4.0 * math.log(2.0)

    # We need both high & low at the same row.  Easiest: join on ``ts`` so we
    # have ``c`` (high) and ``c_low`` (low) in one frame, compute, then strip.
    low_renamed = low.rename({c: f"__low__{c}" for c in cols})
    joined = high.join(low_renamed, on=_TS_COL, how="inner")

    exprs: list[pl.Expr] = []
    for c in cols:
        h = pl.col(c)
        l = pl.col(f"__low__{c}")
        log_hl = (h / (l + 1e-12)).log()
        rolled = (log_hl ** 2).rolling_mean(window_size=n)
        exprs.append((rolled / factor_const).sqrt().alias(c))

    result = joined.with_columns(exprs).select([_TS_COL, *cols])
    return result


@factor(
    category="波动",
    lookback=20,
    params={"fast": 5, "slow": 20},
    description="波动率比 — short-term / long-term volatility ratio",
)
def vol_ratio(close: Panel, params=None) -> Panel:
    """Short-term / long-term volatility ratio.

    Mirrors the pandas implementation::

        ret = df["close"].pct_change()
        vol_f = ret.rolling(fast).std()
        vol_s = ret.rolling(slow).std()
        return vol_f / (vol_s + 1e-12)
    """
    p = params or {}
    fast = p.get("fast", 5)
    slow = p.get("slow", 20)
    cols = _value_cols(close)
    if not cols:
        return close.clone()

    exprs: list[pl.Expr] = []
    for c in cols:
        ret = pl.col(c).pct_change()
        vol_f = ret.rolling_std(window_size=fast)
        vol_s = ret.rolling_std(window_size=slow)
        exprs.append((vol_f / (vol_s + 1e-12)).alias(c))

    return close.with_columns(exprs)
