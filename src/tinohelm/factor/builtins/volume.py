"""Volume / price-volume factors — obv_slope, vwap_dev.

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
    return [c for c in panel.columns if c != _TS_COL]


@factor(
    category="成交量",
    lookback=20,
    params={"lookback": 20},
    description="OBV 斜率 — OBV slope over N periods",
)
def obv_slope(close: Panel, volume: Panel, params=None) -> Panel:
    """OBV slope over N periods.

    Mirrors the pandas implementation::

        direction = np.sign(close.diff())
        obv = (direction * volume).cumsum()
        return obv.diff(n) / n

    The polars equivalent of ``np.sign(...)`` is ``pl.col(c).sign()``;
    polars propagates ``null`` from ``diff`` exactly like pandas.
    """
    n = (params or {}).get("lookback", 20)
    cols = _value_cols(close)
    if not cols:
        return close.clone()

    # Join close + volume on ``ts`` so we can express the formula per column.
    volume_renamed = volume.rename({c: f"__vol__{c}" for c in cols})
    joined = close.join(volume_renamed, on=_TS_COL, how="inner")

    exprs: list[pl.Expr] = []
    for c in cols:
        direction = pl.col(c).diff().sign()
        obv = (direction * pl.col(f"__vol__{c}")).cum_sum()
        exprs.append((obv.diff(n) / n).alias(c))

    return joined.with_columns(exprs).select([_TS_COL, *cols])


@factor(
    category="成交量",
    lookback=20,
    params={"lookback": 20},
    description="VWAP 偏离 — price deviation from VWAP",
)
def vwap_dev(
    high: Panel, low: Panel, close: Panel, volume: Panel, params=None
) -> Panel:
    """Price deviation from VWAP.

    Mirrors the pandas implementation::

        tp = (df["high"] + df["low"] + df["close"]) / 3
        vwap = (tp * df["volume"]).rolling(n).sum() / (df["volume"].rolling(n).sum() + 1e-12)
        return (df["close"] - vwap) / (vwap + 1e-12)
    """
    n = (params or {}).get("lookback", 20)
    cols = _value_cols(close)
    if not cols:
        return close.clone()

    high_renamed = high.rename({c: f"__h__{c}" for c in cols})
    low_renamed = low.rename({c: f"__l__{c}" for c in cols})
    volume_renamed = volume.rename({c: f"__v__{c}" for c in cols})

    joined = (
        close.join(high_renamed, on=_TS_COL, how="inner")
        .join(low_renamed, on=_TS_COL, how="inner")
        .join(volume_renamed, on=_TS_COL, how="inner")
    )

    exprs: list[pl.Expr] = []
    for c in cols:
        h = pl.col(f"__h__{c}")
        l = pl.col(f"__l__{c}")
        v = pl.col(f"__v__{c}")
        cl = pl.col(c)
        tp = (h + l + cl) / 3.0
        vwap = (tp * v).rolling_sum(window_size=n) / (
            v.rolling_sum(window_size=n) + 1e-12
        )
        exprs.append(((cl - vwap) / (vwap + 1e-12)).alias(c))

    return joined.with_columns(exprs).select([_TS_COL, *cols])
