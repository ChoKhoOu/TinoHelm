"""Turnover stats for factor-based rebalancing.

Migrated from ``research.analysis.compute_turnover``.  Produces
``{daily, annualized, fee_drag_monthly}`` with the same math and guards
(AC-13.2).

Design notes
------------
* Assumes daily rebalancing — annualised with 252 trading days.
* Monthly fee-drag assumes 21 trading days/month and a 2-sided fill
  (``daily_turn * 2 * fee_rate * 21``).
* Degenerate factors (constant values) yield ``pd.qcut`` NaN labels; we
  drop those rows to avoid a false 100 % turnover reading.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


_EMPTY_OUTPUT: dict[str, float] = {"daily": 0, "annualized": 0, "fee_drag_monthly": 0}


def compute_turnover(
    factor: pd.Series,
    fwd_ret: pd.Series,
    n_quantiles: int = 5,
    fee_rate: float = 0.0004,
) -> dict[str, float]:
    """Compute turnover stats for factor-based rebalancing.

    Returns
    -------
    dict
        ``{"daily", "annualized", "fee_drag_monthly"}`` — same shape as
        ``research.analysis.compute_turnover``.  Short input (< ``n_quantiles * 20``
        pairs) or degenerate factors return the zero-output payload.
    """
    paired = pd.DataFrame({"factor": factor, "fwd_ret": fwd_ret}).dropna()
    paired = paired[np.isfinite(paired["factor"])]

    if len(paired) < n_quantiles * 20:
        return dict(_EMPTY_OUTPUT)

    try:
        paired["q"] = pd.qcut(paired["factor"], n_quantiles, labels=False, duplicates="drop")
    except ValueError:
        return dict(_EMPTY_OUTPUT)

    # qcut returns NaN when too few unique values exist to form n_quantiles bins.
    # NaN-vs-NaN comparison below would otherwise be True, falsely reporting
    # 100% turnover for a degenerate (constant) factor.
    paired = paired.dropna(subset=["q"])
    if paired.empty:
        return dict(_EMPTY_OUTPUT)

    daily_groups = paired.groupby(pd.Grouper(freq="D"))
    turnovers: list[float] = []
    prev_q = None
    for _, group in daily_groups:
        if len(group) == 0:
            continue
        curr_q = group["q"]
        if prev_q is not None:
            aligned_prev, aligned_curr = prev_q.align(curr_q, join="inner")
            if len(aligned_prev) > 0:
                changed = (aligned_curr.values != aligned_prev.values).mean()
                turnovers.append(changed)
        prev_q = curr_q

    daily_turn = float(np.mean(turnovers)) if turnovers else 0
    annual_turn = daily_turn * 252
    fee_drag = daily_turn * 2 * fee_rate * 21  # monthly, 2-sided

    return {
        "daily": round(daily_turn, 4),
        "annualized": round(annual_turn, 1),
        "fee_drag_monthly": round(fee_drag, 4),
    }


__all__ = ["compute_turnover"]
