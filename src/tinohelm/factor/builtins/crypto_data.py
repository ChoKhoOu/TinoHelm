"""Crypto on-chain / market-depth factors — oi_change, orderbook_imbalance_L1.

Both kernels are marked ``experimental=True`` and ``deprecated=True``: the
``DataLayer`` does not yet support the underlying sources (``open_interest``
for ``oi_change``; ``quote_tick`` for ``orderbook_imbalance_L1``).  Calling
either kernel raises ``NotImplementedError`` so the orchestrator surfaces
``FactorRun.status='failed'`` instead of silently emitting an empty Panel
that would later collapse to ``rating=0``.

When data-layer support lands (s21), drop ``experimental=True`` /
``deprecated=True`` and replace the ``raise`` with the documented formulas
below.
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
    deprecated=True,
)
def oi_change(open_interest: Panel, params=None) -> Panel:
    """Open interest percentage change.

    Pending ``open_interest`` DataLayer support; raising forces
    ``FactorRun.status='failed'`` rather than silent empty output.

    Formula (when open_interest is available)::

        open_interest.pct_change(n)
    """
    raise NotImplementedError(
        "oi_change is experimental and requires open_interest DataLayer "
        "support which is not yet implemented (tracked under s21)."
    )


@factor(
    category="链上数据",
    lookback=1,
    params={},
    description="L1 委托不平衡 — (bid_vol - ask_vol) / (bid_vol + ask_vol) (pending DataLayer support)",
    experimental=True,
    deprecated=True,
)
def orderbook_imbalance_L1(orderbook_imbalance: Panel, params=None) -> Panel:
    """L1 orderbook imbalance.

    Pending ``quote_tick`` DataLayer support; same rationale as
    ``oi_change`` — raising prevents the silent-empty-output failure mode.

    Formula (when quote_tick is available)::

        (bid_vol - ask_vol) / (bid_vol + ask_vol),  already in [-1, 1]
    """
    raise NotImplementedError(
        "orderbook_imbalance_L1 is experimental and requires quote_tick "
        "DataLayer support which is not yet implemented (tracked under s21)."
    )
