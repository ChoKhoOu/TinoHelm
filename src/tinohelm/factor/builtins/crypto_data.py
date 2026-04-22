"""Crypto on-chain / market-depth factors — oi_change, orderbook_imbalance_L1.

These factors have no counterpart in ``research/factors.py`` (no legacy
regression check).  Shape + finite value rate is validated instead.
"""
from __future__ import annotations

from tinohelm.factor.decorator import factor
from tinohelm.factor.types import Panel


@factor(
    category="链上数据",
    lookback=2,
    params={"lookback": 1},
    description="持仓量变化 — open_interest pct_change",
)
def oi_change(open_interest: Panel, params=None) -> Panel:
    """Open interest percentage change.

    Mirrors the task description:
        open_interest.pct_change(n)

    ``lookback`` controls the pct_change period (default 1).
    """
    n = (params or {}).get("lookback", 1)
    return open_interest.pct_change(n)


@factor(
    category="链上数据",
    lookback=1,
    params={},
    description="L1 委托不平衡 — (bid_vol - ask_vol) / (bid_vol + ask_vol)",
)
def orderbook_imbalance_L1(orderbook_imbalance: Panel, params=None) -> Panel:
    """L1 orderbook imbalance.

    Expects a pre-computed ``orderbook_imbalance`` Panel where each value
    is already ``(bid_vol - ask_vol) / (bid_vol + ask_vol) ∈ [-1, 1]``.
    The DataLayer computes this from quote_tick data before passing it in.

    Returns the panel as-is (signal is already normalised).
    """
    return orderbook_imbalance.copy()
