"""Factor analysis — IC, decay, quantile returns, distribution stats, turnover."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)


def forward_returns(close: pd.Series, period: int, log_ret: bool = False) -> pd.Series:
    """Compute forward returns."""
    if log_ret:
        return np.log(close.shift(-period) / close)
    return close.shift(-period) / close - 1


def compute_ic_series(
    factor: pd.Series, fwd_ret: pd.Series, method: str = "spearman", freq: str = "D",
) -> pd.DataFrame:
    """Per-period Rank IC (Spearman correlation).

    Groups by freq (D=daily, W=weekly) and computes IC per group.
    Returns DataFrame with columns: date, ic
    """
    paired = pd.DataFrame({"factor": factor, "fwd_ret": fwd_ret}).dropna()
    paired = paired[np.isfinite(paired["factor"]) & np.isfinite(paired["fwd_ret"])]

    if len(paired) < 30:
        return pd.DataFrame(columns=["date", "ic"])

    # Group by period
    grouped = paired.groupby(pd.Grouper(freq=freq))
    results = []
    for dt, group in grouped:
        if len(group) < 20:
            continue
        if method == "spearman":
            ic, _ = spearmanr(group["factor"], group["fwd_ret"])
        else:
            ic = group["factor"].corr(group["fwd_ret"])
        if np.isfinite(ic):
            results.append({"date": dt.isoformat() if hasattr(dt, "isoformat") else str(dt), "ic": round(ic, 6)})

    if not results:
        return pd.DataFrame(columns=["date", "ic"])
    return pd.DataFrame(results)


def compute_ic_summary(ic_series: pd.DataFrame) -> dict:
    """Compute IC summary stats from IC series."""
    if ic_series.empty or "ic" not in ic_series.columns:
        return {"ic_mean": 0, "ic_std": 0, "ir": 0, "ic_positive_pct": 0, "ic_max_abs": 0, "ic_tstat": 0}
    ics = ic_series["ic"].values
    if len(ics) == 0:
        return {"ic_mean": 0, "ic_std": 0, "ir": 0, "ic_positive_pct": 0, "ic_max_abs": 0, "ic_tstat": 0}
    mean_ic = float(np.mean(ics))
    std_ic = float(np.std(ics))
    ir = mean_ic / std_ic if std_ic > 0 else 0
    ic_tstat = mean_ic / (std_ic / np.sqrt(len(ics))) if std_ic > 0 else 0
    pct_pos = float(np.mean(ics > 0))
    max_abs = float(np.max(np.abs(ics)))

    return {
        "ic_mean": round(mean_ic, 6),
        "ic_std": round(std_ic, 6),
        "ir": round(ir, 4),
        "ic_tstat": round(ic_tstat, 2),
        "ic_positive_pct": round(pct_pos, 4),
        "ic_max_abs": round(max_abs, 6),
    }


def compute_rating(summary: dict) -> int:
    """Rate factor: 3=strong, 2=usable, 1=weak, 0=invalid."""
    ir = abs(summary.get("ir", 0))
    pct = summary.get("ic_positive_pct", 0)
    if ir > 1.0 and pct > 0.60:
        return 3
    if ir > 0.5 and pct > 0.55:
        return 2
    if ir > 0.2:
        return 1
    return 0


def compute_ic_decay(
    factor: pd.Series, close: pd.Series, lags: list[int] | None = None,
) -> list[dict]:
    """IC at multiple forward horizons (decay curve)."""
    if lags is None:
        lags = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89]

    results = []
    for lag in lags:
        fwd = forward_returns(close, lag)
        paired = pd.DataFrame({"f": factor, "r": fwd}).dropna()
        paired = paired[np.isfinite(paired["f"]) & np.isfinite(paired["r"])]
        if len(paired) < 30:
            results.append({"lag": lag, "ic": 0})
            continue
        ic, _ = spearmanr(paired["f"], paired["r"])
        results.append({"lag": lag, "ic": round(float(ic), 6) if np.isfinite(ic) else 0})

    return results


def compute_half_life(decay: list[dict]) -> int | None:
    """Find half-life from decay curve (lag where IC drops to half of max)."""
    if not decay:
        return None
    max_ic = max(abs(d["ic"]) for d in decay)
    if max_ic < 0.001:
        return None
    half = max_ic / 2
    for d in decay:
        if abs(d["ic"]) <= half:
            return d["lag"]
    return decay[-1]["lag"]


def compute_quantile_returns(
    factor: pd.Series, fwd_ret: pd.Series, n_quantiles: int = 5,
) -> dict:
    """Quantile analysis: split by factor value, compute per-quantile returns.

    Returns:
        avg_returns: {Q1: float, ...} average per-period return per quantile
        cum_returns: {Q1: [{date, cum_ret}], ...} cumulative return series
        is_monotonic: bool
    """
    paired = pd.DataFrame({"factor": factor, "fwd_ret": fwd_ret}).dropna()
    paired = paired[np.isfinite(paired["factor"]) & np.isfinite(paired["fwd_ret"])]

    if len(paired) < n_quantiles * 20:
        return {"avg_returns": {}, "cum_returns": {}, "is_monotonic": False}

    try:
        paired["q"] = pd.qcut(paired["factor"], n_quantiles, labels=False, duplicates="drop")
    except ValueError:
        return {"avg_returns": {}, "cum_returns": {}, "is_monotonic": False}

    avg_returns = {}
    cum_returns = {}

    for q in sorted(paired["q"].unique()):
        label = f"Q{int(q) + 1}"
        group = paired[paired["q"] == q]
        avg_returns[label] = round(float(group["fwd_ret"].mean()), 8)

        # Cumulative returns (sampled to keep payload small)
        cum = (1 + group["fwd_ret"]).cumprod() - 1
        # Sample ~100 points
        step = max(1, len(cum) // 100)
        sampled = cum.iloc[::step]
        cum_returns[label] = [
            {"date": idx.isoformat() if hasattr(idx, "isoformat") else str(idx), "cum_ret": round(float(v), 6)}
            for idx, v in sampled.items()
        ]

    # Check monotonicity (Q1 > Q2 > ... > QN)
    vals = list(avg_returns.values())
    is_mono = all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1))

    return {"avg_returns": avg_returns, "cum_returns": cum_returns, "is_monotonic": is_mono}


def compute_distribution(factor: pd.Series, n_bins: int = 50) -> dict:
    """Factor value distribution stats + histogram."""
    clean = factor.dropna()
    clean = clean[np.isfinite(clean)]

    if len(clean) < 10:
        return {"histogram": [], "stats": {}}

    # Histogram
    counts, bin_edges = np.histogram(clean, bins=n_bins)
    histogram = [
        {"bin_start": round(float(bin_edges[i]), 6), "bin_end": round(float(bin_edges[i + 1]), 6), "count": int(counts[i])}
        for i in range(len(counts))
    ]

    # Stats
    arr = clean.values
    acf_1 = float(pd.Series(arr).autocorr(lag=1)) if len(arr) > 1 else 0
    acf_5 = float(pd.Series(arr).autocorr(lag=5)) if len(arr) > 5 else 0

    stats = {
        "mean": round(float(np.mean(arr)), 6),
        "std": round(float(np.std(arr)), 6),
        "skew": round(float(pd.Series(arr).skew()), 4),
        "kurtosis": round(float(pd.Series(arr).kurtosis()), 4),
        "min": round(float(np.min(arr)), 6),
        "max": round(float(np.max(arr)), 6),
        "zero_pct": round(float(np.mean(arr == 0)), 4),
        "autocorr_1": round(acf_1, 4) if np.isfinite(acf_1) else 0,
        "autocorr_5": round(acf_5, 4) if np.isfinite(acf_5) else 0,
    }

    return {"histogram": histogram, "stats": stats}


def compute_turnover(factor: pd.Series, fwd_ret: pd.Series, n_quantiles: int = 5, fee_rate: float = 0.0004) -> dict:
    """Compute turnover stats for factor-based rebalancing."""
    paired = pd.DataFrame({"factor": factor, "fwd_ret": fwd_ret}).dropna()
    paired = paired[np.isfinite(paired["factor"])]

    if len(paired) < n_quantiles * 20:
        return {"daily": 0, "annualized": 0, "fee_drag_monthly": 0}

    try:
        paired["q"] = pd.qcut(paired["factor"], n_quantiles, labels=False, duplicates="drop")
    except ValueError:
        return {"daily": 0, "annualized": 0, "fee_drag_monthly": 0}

    # Daily turnover: fraction of positions that change quantile
    daily_groups = paired.groupby(pd.Grouper(freq="D"))
    turnovers = []
    prev_q = None
    for _, group in daily_groups:
        if len(group) == 0:
            continue
        curr_q = group["q"]
        if prev_q is not None and len(prev_q) == len(curr_q):
            changed = (curr_q.values != prev_q.values).mean()
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


def run_explore(
    factor: pd.Series,
    close: pd.Series,
    forward_period: int = 5,
    n_quantiles: int = 5,
    log_ret: bool = False,
    ic_freq: str = "D",
) -> dict:
    """Run full explore analysis (synchronous, fast).

    Returns all data needed for the explorer results panel.
    """
    fwd_ret = forward_returns(close, forward_period, log_ret)

    ic_series = compute_ic_series(factor, fwd_ret, freq=ic_freq)
    summary = compute_ic_summary(ic_series)
    summary["rating"] = compute_rating(summary)

    decay = compute_ic_decay(factor, close)
    summary["half_life_bars"] = compute_half_life(decay)

    quantiles = compute_quantile_returns(factor, fwd_ret, n_quantiles)
    distribution = compute_distribution(factor)
    turnover = compute_turnover(factor, fwd_ret, n_quantiles)

    return {
        "summary": summary,
        "ic_series": ic_series.to_dict("records") if len(ic_series) > 0 else [],
        "ic_decay": decay,
        "quantile_returns": quantiles,
        "distribution": distribution,
        "turnover": turnover,
    }
