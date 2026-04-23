"""Momentum factors — ret_N, rsi_signal.

Numerical alignment with ``research/factors.py._COMPUTE_MAP`` is required
(AC-13.2: difference < 1e-10 for bar-data factors).
"""
from __future__ import annotations

import numpy as np

from tinohelm.factor.decorator import factor
from tinohelm.factor.types import Panel


@factor(
    category="动量",
    lookback=20,
    params={"lookback": 20},
    description="N 周期收益率",
)
def ret_N(close: Panel, params=None) -> Panel:
    """N-period return.

    Mirrors ``research/factors.py::ret_N``:
        return df["close"].pct_change(n)
    """
    n = (params or {}).get("lookback", 20)
    return close.pct_change(n)


@factor(
    category="动量",
    lookback=14,
    params={"lookback": 14},
    description="RSI 信号 — RSI centered around 0 (RSI - 50)",
)
def rsi_signal(close: Panel, params=None) -> Panel:
    """RSI centered around 0 (RSI - 50).

    Mirrors ``research/factors.py::rsi_signal``:
        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(n).mean()
        loss = (-delta.clip(upper=0)).rolling(n).mean()
        rs = gain / (loss + 1e-12)
        rsi = 100 - 100 / (1 + rs)
        return rsi - 50
    """
    n = (params or {}).get("lookback", 14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / (loss + 1e-12)
    rsi = 100 - 100 / (1 + rs)
    return rsi - 50
