"""Momentum factors — ret_N, rsi_signal.

All kernels operate on a polars wide-table :data:`Panel`
(``column "ts" + N symbol columns``) and return the same shape.

Numerical alignment with the pre-polars pandas implementation is asserted
in ``tests/factor/test_builtins.py`` via the regression oracle parquet
(absolute difference <= 1e-6).
"""
from __future__ import annotations

import polars as pl

from tinohelm.factor.decorator import factor
from tinohelm.factor.types import Panel


_TS_COL = "ts"


def _value_cols(panel: Panel) -> list[str]:
    """Return the symbol columns (everything except ``ts``)."""
    return [c for c in panel.columns if c != _TS_COL]


@factor(
    category="动量",
    lookback=20,
    params={"lookback": 20},
    description="N 周期收益率",
)
def ret_N(close: Panel, params=None) -> Panel:
    """N-period return: ``close.pct_change(n)``.

    Mirrors the pandas legacy ``df["close"].pct_change(n)``: the first ``n``
    rows are ``null``, subsequent rows are ``close[t] / close[t - n] - 1``.
    """
    n = (params or {}).get("lookback", 20)
    cols = _value_cols(close)
    if not cols:
        return close.clone()
    return close.with_columns([pl.col(c).pct_change(n).alias(c) for c in cols])


@factor(
    category="动量",
    lookback=14,
    params={"lookback": 14},
    description="RSI 信号 — RSI centered around 0 (RSI - 50)",
)
def rsi_signal(close: Panel, params=None) -> Panel:
    """RSI centered around 0 (``RSI - 50``).

    Mirrors the pandas implementation::

        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(n).mean()
        loss  = (-delta.clip(upper=0)).rolling(n).mean()
        rs    = gain / (loss + 1e-12)
        rsi   = 100 - 100 / (1 + rs)
        return rsi - 50
    """
    n = (params or {}).get("lookback", 14)
    cols = _value_cols(close)
    if not cols:
        return close.clone()

    exprs: list[pl.Expr] = []
    for c in cols:
        delta = pl.col(c).diff()
        # ``delta.clip(lower_bound=0)`` keeps only the gains; losses → 0.
        gain = delta.clip(lower_bound=0).rolling_mean(window_size=n)
        # ``(-delta).clip(lower_bound=0)`` keeps only the losses (sign-flipped);
        # equivalent to the pandas ``-delta.clip(upper=0)``.
        loss = (-delta).clip(lower_bound=0).rolling_mean(window_size=n)
        rs = gain / (loss + 1e-12)
        rsi = 100.0 - 100.0 / (1.0 + rs)
        exprs.append((rsi - 50.0).alias(c))

    return close.with_columns(exprs)
