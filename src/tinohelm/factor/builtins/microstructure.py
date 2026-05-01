"""Microstructure factors — trade_imbalance (experimental), amihud_illiq.

All kernels operate on a polars wide-table :data:`Panel`
(``column "ts" + N symbol columns``) and return the same shape.

Numerical alignment with the pre-polars pandas implementation is asserted
in ``tests/factor/test_builtins.py`` via the regression oracle parquet
(absolute difference <= 1e-6).

``trade_imbalance`` smooths the DataLayer-computed per-bucket signed trade
imbalance from downloadable trade-tick data.
"""
from __future__ import annotations

import polars as pl

from tinohelm.factor.decorator import factor
from tinohelm.factor.types import Panel


_TS_COL = "ts"


def _value_cols(panel: Panel) -> list[str]:
    return [c for c in panel.columns if c != _TS_COL]


@factor(
    category="微观结构",
    lookback=20,
    params={"lookback": 20},
    description="买卖不平衡 — smoothed net buy/sell volume from trade_tick data",
    experimental=True,
)
def trade_imbalance(
    trade_imbalance: Panel, params=None
) -> Panel:
    """Rolling mean of loaded signed trade imbalance buckets."""
    n = (params or {}).get("lookback", 20)
    cols = _value_cols(trade_imbalance)
    return trade_imbalance.with_columns([
        pl.col(c).rolling_mean(window_size=n).alias(c) for c in cols
    ])


@factor(
    category="微观结构",
    lookback=20,
    params={"lookback": 20},
    description="Amihud 非流动性 — |return| / dollar_volume",
)
def amihud_illiq(close: Panel, volume: Panel, params=None) -> Panel:
    """Amihud illiquidity: ``|return| / dollar_volume`` rolling-mean.

    Mirrors the pandas implementation::

        ret = df["close"].pct_change().abs()
        dollar_vol = df["close"] * df["volume"]
        return (ret / (dollar_vol + 1e-12)).rolling(n).mean()
    """
    n = (params or {}).get("lookback", 20)
    cols = _value_cols(close)
    if not cols:
        return close.clone()

    volume_renamed = volume.rename({c: f"__vol__{c}" for c in cols})
    joined = close.join(volume_renamed, on=_TS_COL, how="inner")

    exprs: list[pl.Expr] = []
    for c in cols:
        cl = pl.col(c)
        v = pl.col(f"__vol__{c}")
        ret_abs = cl.pct_change().abs()
        dollar_vol = cl * v
        exprs.append(
            (ret_abs / (dollar_vol + 1e-12))
            .rolling_mean(window_size=n)
            .alias(c)
        )

    return joined.with_columns(exprs).select([_TS_COL, *cols])
