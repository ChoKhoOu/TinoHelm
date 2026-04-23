"""Microstructure factors — trade_imbalance, amihud_illiq.

Numerical alignment with ``research/factors.py._COMPUTE_MAP`` is required
(AC-13.2: difference < 1e-10 for bar-data factors).
"""
from __future__ import annotations

from tinohelm.factor.decorator import factor
from tinohelm.factor.types import Panel


@factor(
    category="微观结构",
    lookback=20,
    params={"lookback": 20},
    description="买卖不平衡 — net buy/sell volume from trade_tick data (pending DataLayer support)",
    experimental=True,
)
def trade_imbalance(
    trade_qty: Panel, trade_side: Panel, params=None
) -> Panel:
    """Net buy minus sell volume imbalance from trade tick data.

    NOTE: Pending trade_tick DataLayer support.  DataLayer._load_table raises
    NotImplementedError for source="trade_tick".  This implementation will be
    activated once trade_tick loading is implemented in DataLayer (tracked in
    tech-design §AC-13.1).

    Formula (when trade_tick is available):
        buy_qty  = trade_qty where trade_side == "BUY"  else 0
        sell_qty = trade_qty where trade_side == "SELL" else 0
        return (buy_qty - sell_qty).rolling(lookback).sum()
               / trade_qty.rolling(lookback).sum()
    """
    raise NotImplementedError(
        "trade_imbalance requires trade_tick DataLayer support which is not yet "
        "implemented.  Use bar-based microstructure factors (amihud_illiq) instead."
    )


@factor(
    category="微观结构",
    lookback=20,
    params={"lookback": 20},
    description="Amihud 非流动性 — |return| / dollar_volume",
)
def amihud_illiq(close: Panel, volume: Panel, params=None) -> Panel:
    """Amihud illiquidity: |return| / dollar_volume.

    Mirrors ``research/factors.py::amihud_illiq``:
        ret = df["close"].pct_change().abs()
        dollar_vol = df["close"] * df["volume"]
        return (ret / (dollar_vol + 1e-12)).rolling(n).mean()
    """
    n = (params or {}).get("lookback", 20)
    ret = close.pct_change().abs()
    dollar_vol = close * volume
    return (ret / (dollar_vol + 1e-12)).rolling(n).mean()
