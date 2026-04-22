"""Volatility factors — parkinson_vol, vol_ratio.

Numerical alignment with ``research/factors.py._COMPUTE_MAP`` is required
(AC-13.2: difference < 1e-10 for bar-data factors).
"""
from __future__ import annotations

import numpy as np

from tinohelm.factor.decorator import factor
from tinohelm.factor.types import Panel


@factor(
    category="波动",
    lookback=20,
    params={"lookback": 20},
    description="Parkinson 波动率 — high/low range estimator",
)
def parkinson_vol(high: Panel, low: Panel, params=None) -> Panel:
    """Parkinson volatility estimator using high/low range.

    Mirrors ``research/factors.py::parkinson_vol``:
        log_hl = np.log(df["high"] / (df["low"] + 1e-12))
        return np.sqrt((log_hl ** 2).rolling(n).mean() / (4 * np.log(2)))
    """
    n = (params or {}).get("lookback", 20)
    log_hl = np.log(high / (low + 1e-12))
    return np.sqrt((log_hl ** 2).rolling(n).mean() / (4 * np.log(2)))


@factor(
    category="波动",
    lookback=20,
    params={"fast": 5, "slow": 20},
    description="波动率比 — short-term / long-term volatility ratio",
)
def vol_ratio(close: Panel, params=None) -> Panel:
    """Short-term / long-term volatility ratio.

    Mirrors ``research/factors.py::vol_ratio``:
        fast = params.get("fast", 5)
        slow = params.get("slow", 20)
        ret = df["close"].pct_change()
        vol_f = ret.rolling(fast).std()
        vol_s = ret.rolling(slow).std()
        return vol_f / (vol_s + 1e-12)
    """
    p = params or {}
    fast = p.get("fast", 5)
    slow = p.get("slow", 20)
    ret = close.pct_change()
    vol_f = ret.rolling(fast).std()
    vol_s = ret.rolling(slow).std()
    return vol_f / (vol_s + 1e-12)
