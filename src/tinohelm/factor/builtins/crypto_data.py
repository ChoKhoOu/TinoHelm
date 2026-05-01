"""Crypto on-chain / market-depth factors."""
from __future__ import annotations

import polars as pl

from tinohelm.factor.decorator import factor
from tinohelm.factor.types import Panel

_TS_COL = "ts"


def _value_cols(panel: Panel) -> list[str]:
    return [c for c in panel.columns if c != _TS_COL]


@factor(
    category="链上数据",
    lookback=2,
    params={"lookback": 1},
    description="持仓量变化 — open_interest pct_change",
    experimental=True,
)
def oi_change(open_interest: Panel, params=None) -> Panel:
    """Open interest percentage change."""
    n = (params or {}).get("lookback", 1)
    cols = _value_cols(open_interest)
    return open_interest.with_columns([pl.col(c).pct_change(n).alias(c) for c in cols])


@factor(
    category="链上数据",
    lookback=1,
    params={},
    description="L1 委托不平衡 — (bid_vol - ask_vol) / (bid_vol + ask_vol)",
    experimental=True,
)
def orderbook_imbalance_L1(orderbook_imbalance: Panel, params=None) -> Panel:
    """Pass through the DataLayer-computed L1 orderbook imbalance."""
    return orderbook_imbalance.clone()
