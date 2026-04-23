"""Crypto funding-rate factors — funding_rate_level, funding_rate_mom.

These factors have no counterpart in ``research/factors.py`` (no legacy
regression check).  Shape + finite value rate is validated instead.
"""
from __future__ import annotations

from tinohelm.factor.decorator import factor
from tinohelm.factor.types import Panel


@factor(
    category="资金费率",
    lookback=1,
    params={"lookback": 1},
    description="资金费率水平 — raw funding rate signal",
)
def funding_rate_level(funding_rate: Panel, params=None) -> Panel:
    """Raw funding rate level.

    Returns the funding_rate panel directly (no transformation).
    DataLayer provides a Panel aligned to bar timestamps.
    """
    # Pass-through: raw funding rate is the signal.
    return funding_rate.copy()


@factor(
    category="资金费率",
    lookback=2,
    params={"lookback": 1},
    description="资金费率动量 — funding rate momentum (shift diff)",
)
def funding_rate_mom(funding_rate: Panel, params=None) -> Panel:
    """Funding rate momentum: current minus previous period.

    Mirrors the task description:
        funding_rate.diff(shift)

    ``lookback`` controls the diff period (default 1 = one-period change).
    """
    n = (params or {}).get("lookback", 1)
    return funding_rate.diff(n)
