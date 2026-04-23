"""Volume / price-volume factors — obv_slope, vwap_dev.

Numerical alignment with ``research/factors.py._COMPUTE_MAP`` is required
(AC-13.2: difference < 1e-10 for bar-data factors).
"""
from __future__ import annotations

import numpy as np

from tinohelm.factor.decorator import factor
from tinohelm.factor.types import Panel


@factor(
    category="成交量",
    lookback=20,
    params={"lookback": 20},
    description="OBV 斜率 — OBV slope over N periods",
)
def obv_slope(close: Panel, volume: Panel, params=None) -> Panel:
    """OBV slope over N periods.

    Mirrors ``research/factors.py::obv_slope``:
        direction = np.sign(df["close"].diff())
        obv = (direction * df["volume"]).cumsum()
        return obv.diff(n) / n
    """
    n = (params or {}).get("lookback", 20)
    direction = np.sign(close.diff())
    obv = (direction * volume).cumsum()
    return obv.diff(n) / n


@factor(
    category="成交量",
    lookback=20,
    params={"lookback": 20},
    description="VWAP 偏离 — price deviation from VWAP",
)
def vwap_dev(high: Panel, low: Panel, close: Panel, volume: Panel, params=None) -> Panel:
    """Price deviation from VWAP.

    Mirrors ``research/factors.py::vwap_dev``:
        tp = (df["high"] + df["low"] + df["close"]) / 3
        vwap = (tp * df["volume"]).rolling(n).sum() / (df["volume"].rolling(n).sum() + 1e-12)
        return (df["close"] - vwap) / (vwap + 1e-12)
    """
    n = (params or {}).get("lookback", 20)
    tp = (high + low + close) / 3
    vwap = (tp * volume).rolling(n).sum() / (volume.rolling(n).sum() + 1e-12)
    return (close - vwap) / (vwap + 1e-12)
