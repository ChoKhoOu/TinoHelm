"""Crypto on-chain / market-depth factors — oi_change, orderbook_imbalance_L1.

Both kernels are marked ``experimental=True`` because ``DataLayer`` does not yet
support the underlying sources (``open_interest``, ``quote_tick``).  Calling
them via the orchestrator therefore raises ``NotImplementedError`` and the
corresponding ``FactorRun`` is marked ``status='failed'`` — the silent empty
Panel + ``rating=0`` behaviour that previously masked the missing support is
now impossible.

When data-layer support lands, drop ``experimental=True`` and replace the
``raise`` with the (already documented) formulas below.
"""
from __future__ import annotations

from tinohelm.factor.decorator import factor
from tinohelm.factor.types import Panel


@factor(
    category="链上数据",
    lookback=2,
    params={"lookback": 1},
    description="持仓量变化 — open_interest pct_change (pending DataLayer support)",
    experimental=True,
)
def oi_change(open_interest: Panel, params=None) -> Panel:
    """Open interest percentage change.

    NOTE: Pending ``open_interest`` DataLayer support.  ``DataLayer._load_table``
    does not know how to load ``source="open_interest"`` and silently returns an
    empty Panel, which would otherwise surface to the user as ``rating=0`` with
    no error.  Raising here forces ``FactorRun.status='failed'`` so the problem
    is visible (matches the ``trade_imbalance`` pattern).

    Formula (when open_interest is available):
        open_interest.pct_change(n)
    """
    raise NotImplementedError(
        "oi_change requires open_interest DataLayer support which is not yet "
        "implemented."
    )


@factor(
    category="链上数据",
    lookback=1,
    params={},
    description="L1 委托不平衡 — (bid_vol - ask_vol) / (bid_vol + ask_vol) (pending DataLayer support)",
    experimental=True,
)
def orderbook_imbalance_L1(orderbook_imbalance: Panel, params=None) -> Panel:
    """L1 orderbook imbalance.

    NOTE: Pending ``quote_tick`` DataLayer support.  Same rationale as
    ``oi_change`` above — raising prevents the silent-empty-output failure mode.

    Formula (when quote_tick is available):
        (bid_vol - ask_vol) / (bid_vol + ask_vol), already ∈ [-1, 1].
    """
    raise NotImplementedError(
        "orderbook_imbalance_L1 requires quote_tick DataLayer support which is "
        "not yet implemented."
    )
