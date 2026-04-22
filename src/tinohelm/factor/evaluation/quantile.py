"""Quantile PnL — migrated from ``research.analysis.compute_quantile_returns``.

Split by factor-value quantile (``pd.qcut``), compute the average per-period
return per quantile plus a sampled cumulative-return series.  The monotonicity
flag mirrors the legacy ``is_monotonic`` semantics (Q1 ≥ Q2 ≥ … ≥ QN).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


_EMPTY_OUTPUT = {"avg_returns": {}, "cum_returns": {}, "is_monotonic": False}


def compute_quantile_returns(
    factor: pd.Series,
    fwd_ret: pd.Series,
    n_quantiles: int = 5,
) -> dict:
    """Quantile analysis: split by factor value, compute per-quantile returns.

    Returns
    -------
    dict with keys:
        ``avg_returns`` — ``{Q1: float, ...}`` average per-period return per quantile
        ``cum_returns`` — ``{Q1: [{date, cum_ret}], ...}`` sampled cumulative return series
        ``is_monotonic`` — bool, ``True`` if Q1 ≥ Q2 ≥ … ≥ QN

    Guards (identical to ``research.analysis.compute_quantile_returns``):
      * Returns empty output if fewer than ``n_quantiles * 20`` paired obs.
      * Handles degenerate factors (constant values) — ``pd.qcut`` with
        ``duplicates="drop"`` yields NaN bin labels; those rows are dropped.
    """
    paired = pd.DataFrame({"factor": factor, "fwd_ret": fwd_ret}).dropna()
    paired = paired[np.isfinite(paired["factor"]) & np.isfinite(paired["fwd_ret"])]

    if len(paired) < n_quantiles * 20:
        return {**_EMPTY_OUTPUT, "avg_returns": {}, "cum_returns": {}}

    try:
        paired["q"] = pd.qcut(paired["factor"], n_quantiles, labels=False, duplicates="drop")
    except ValueError:
        return {**_EMPTY_OUTPUT, "avg_returns": {}, "cum_returns": {}}

    # qcut with duplicates="drop" returns NaN labels when the factor has too
    # few unique values to form n_quantiles bins. Drop those rows so
    # downstream ``int(q) + 1`` doesn't blow up.
    paired = paired.dropna(subset=["q"])
    if paired.empty:
        return {**_EMPTY_OUTPUT, "avg_returns": {}, "cum_returns": {}}

    avg_returns: dict[str, float] = {}
    cum_returns: dict[str, list[dict]] = {}

    for q in sorted(paired["q"].unique()):
        label = f"Q{int(q) + 1}"
        group = paired[paired["q"] == q]
        avg_returns[label] = round(float(group["fwd_ret"].mean()), 8)

        # Cumulative returns — sample to ~100 points to keep payloads small.
        cum = (1 + group["fwd_ret"]).cumprod() - 1
        step = max(1, len(cum) // 100)
        sampled = cum.iloc[::step]
        cum_returns[label] = [
            {
                "date": idx.isoformat() if hasattr(idx, "isoformat") else str(idx),
                "cum_ret": round(float(v), 6),
            }
            for idx, v in sampled.items()
        ]

    # Monotonicity check — Q1 ≥ Q2 ≥ … ≥ QN.
    vals = list(avg_returns.values())
    is_mono = all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1))

    return {"avg_returns": avg_returns, "cum_returns": cum_returns, "is_monotonic": is_mono}


__all__ = ["compute_quantile_returns"]
