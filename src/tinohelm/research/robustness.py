"""Robustness tests — shuffle, subsample, cross-symbol."""
from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)


def _single_shuffle_ic(args: tuple) -> float:
    """Worker function for shuffle test (must be top-level for pickling)."""
    factor_vals, fwd_vals, seed = args
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(factor_vals)
    ic, _ = spearmanr(shuffled, fwd_vals)
    return float(ic) if np.isfinite(ic) else 0.0


def shuffle_test(
    factor: pd.Series,
    fwd_ret: pd.Series,
    n_iter: int = 1000,
    max_workers: int = 4,
) -> dict:
    """Shuffle factor values N times, compute null IC distribution.

    Returns real_ic, shuffle distribution, p_value, significant.
    """
    paired = pd.DataFrame({"f": factor, "r": fwd_ret}).dropna()
    paired = paired[np.isfinite(paired["f"]) & np.isfinite(paired["r"])]

    if len(paired) < 100:
        return {"real_ic": 0, "shuffle_distribution": [], "p_value": 1.0, "significant": False}

    f_vals = paired["f"].values
    r_vals = paired["r"].values

    # Real IC
    real_ic, _ = spearmanr(f_vals, r_vals)
    real_ic = float(real_ic) if np.isfinite(real_ic) else 0.0

    # Parallel shuffle
    args_list = [(f_vals, r_vals, seed) for seed in range(n_iter)]
    shuffle_ics = []

    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_single_shuffle_ic, a) for a in args_list]
        for fut in as_completed(futures):
            shuffle_ics.append(fut.result())

    shuffle_ics = np.array(shuffle_ics)

    # p-value: fraction of shuffles with |IC| >= |real IC|
    p_value = float(np.mean(np.abs(shuffle_ics) >= abs(real_ic)))

    # Histogram of shuffle distribution (for visualization)
    counts, edges = np.histogram(shuffle_ics, bins=50)
    distribution = [
        {"bin_start": round(float(edges[i]), 6), "bin_end": round(float(edges[i+1]), 6), "count": int(counts[i])}
        for i in range(len(counts))
    ]

    return {
        "real_ic": round(real_ic, 6),
        "shuffle_distribution": distribution,
        "p_value": round(p_value, 4),
        "significant": p_value < 0.05,
    }


def subsample_ic(
    factor: pd.Series,
    fwd_ret: pd.Series,
    freq: str = "ME",
) -> list[dict]:
    """Compute IC per time segment (monthly/quarterly)."""
    paired = pd.DataFrame({"f": factor, "r": fwd_ret}).dropna()
    paired = paired[np.isfinite(paired["f"]) & np.isfinite(paired["r"])]

    results = []
    for period, group in paired.groupby(pd.Grouper(freq=freq)):
        if len(group) < 20:
            continue
        ic, _ = spearmanr(group["f"], group["r"])
        if np.isfinite(ic):
            results.append({
                "period": period.strftime("%Y-%m") if hasattr(period, "strftime") else str(period),
                "ic": round(float(ic), 6),
            })

    return results


def _cross_symbol_worker(args: tuple) -> dict:
    """Worker for cross-symbol IC (top-level for pickling)."""
    symbol, factor_name, factor_params, interval, start, end, forward_period, catalog_path = args
    try:
        from tinohelm.research.loader import load_data
        from tinohelm.research.factors import compute_factor
        from tinohelm.research.analysis import forward_returns

        df = load_data(symbol, "bar", interval, start, end, catalog_path)
        if len(df) < 100:
            return {"symbol": symbol, "ic": 0, "n_obs": 0}

        sig = compute_factor(factor_name, df, factor_params)
        fwd = forward_returns(df["close"], forward_period)

        paired = pd.DataFrame({"f": sig, "r": fwd}).dropna()
        paired = paired[np.isfinite(paired["f"]) & np.isfinite(paired["r"])]

        if len(paired) < 30:
            return {"symbol": symbol, "ic": 0, "n_obs": len(paired)}

        ic, _ = spearmanr(paired["f"], paired["r"])
        return {"symbol": symbol, "ic": round(float(ic), 6) if np.isfinite(ic) else 0, "n_obs": len(paired)}
    except Exception as exc:
        logger.warning("Cross-symbol IC failed for %s: %s", symbol, exc)
        return {"symbol": symbol, "ic": 0, "n_obs": 0, "error": str(exc)}


def cross_symbol_ic(
    factor_name: str,
    factor_params: dict,
    symbols: list[str],
    interval: str = "1m",
    start: str | None = None,
    end: str | None = None,
    forward_period: int = 5,
    catalog_path: str | None = None,
    max_workers: int = 4,
) -> list[dict]:
    """Compute IC for the same factor across multiple symbols in parallel."""
    args_list = [
        (sym, factor_name, factor_params, interval, start, end, forward_period, catalog_path)
        for sym in symbols
    ]

    results = []
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_cross_symbol_worker, a): a[0] for a in args_list}
        for fut in as_completed(futures):
            results.append(fut.result())

    # Sort by IC descending
    results.sort(key=lambda x: abs(x.get("ic", 0)), reverse=True)
    return results
