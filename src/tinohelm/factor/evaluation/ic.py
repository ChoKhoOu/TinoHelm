"""IC / RankIC / IR / t-stat / decay — migrated from ``research/analysis.py``.

Numerical contract
------------------
Output values (``ic_mean``, ``ic_std``, ``ir``, ``ic_tstat``,
``ic_positive_pct``, ``ic_max_abs``) must match
``research.analysis.compute_ic_summary`` bit-for-bit up to the
``round(..., N)`` rounding inherited from the legacy implementation
(AC-13.2: regression diff < 1e-10).

The functions here still work on ``pd.Series`` for factor + forward-return
pairs — the higher-level ``Evaluator`` takes care of flattening a
``Panel`` (time × symbol) into series form before calling these.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr


# ---------------------------------------------------------------------------
# forward_returns — shared helper (used by ic.py + quantile.py + turnover.py)
# ---------------------------------------------------------------------------

def forward_returns(close: pd.Series, period: int, log_ret: bool = False) -> pd.Series:
    """Compute forward returns.

    ``fwd[t] = close[t+period] / close[t] - 1`` (or log-variant).  The last
    ``period`` rows are ``NaN`` because the future bar isn't available.
    """
    if log_ret:
        return np.log(close.shift(-period) / close)
    return close.shift(-period) / close - 1


# ---------------------------------------------------------------------------
# compute_ic_series — per-period (daily / weekly) rank IC
# ---------------------------------------------------------------------------

def compute_ic_series(
    factor: pd.Series,
    fwd_ret: pd.Series,
    method: str = "spearman",
    freq: str = "D",
) -> pd.DataFrame:
    """Per-period Rank IC (Spearman by default).

    Returns a DataFrame with columns ``[date, ic]``.  Groups with < 20 observations
    or a non-finite IC are dropped.  If fewer than 30 valid pairs exist overall,
    the function returns an empty frame (with the same columns) so downstream
    ``compute_ic_summary`` can short-circuit to a zero summary.
    """
    paired = pd.DataFrame({"factor": factor, "fwd_ret": fwd_ret}).dropna()
    paired = paired[np.isfinite(paired["factor"]) & np.isfinite(paired["fwd_ret"])]

    if len(paired) < 30:
        return pd.DataFrame(columns=["date", "ic"])

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
            results.append({
                "date": dt.isoformat() if hasattr(dt, "isoformat") else str(dt),
                "ic": round(ic, 6),
            })

    if not results:
        return pd.DataFrame(columns=["date", "ic"])
    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# compute_ic_summary — IR / t-stat / positive-pct aggregation
# ---------------------------------------------------------------------------

_EMPTY_SUMMARY: dict[str, float] = {
    "ic_mean": 0,
    "ic_std": 0,
    "ir": 0,
    "ic_positive_pct": 0,
    "ic_max_abs": 0,
    "ic_tstat": 0,
}


def compute_ic_summary(ic_series: pd.DataFrame) -> dict[str, float]:
    """Compute IC summary stats from IC series.

    Returns a dict with exactly 6 keys: ``ic_mean``, ``ic_std``, ``ir``,
    ``ic_tstat``, ``ic_positive_pct``, ``ic_max_abs``.  All values are rounded
    to match the legacy ``research.analysis.compute_ic_summary`` implementation
    (AC-13.2).
    """
    if ic_series.empty or "ic" not in ic_series.columns:
        return dict(_EMPTY_SUMMARY)

    ics = ic_series["ic"].values
    if len(ics) == 0:
        return dict(_EMPTY_SUMMARY)

    mean_ic = float(np.mean(ics))
    std_ic = float(np.std(ics))
    # Guard against IEEE float noise: np.std of identical values (e.g. [0.1]*N)
    # leaks a ~1e-17 residue from the double-precision representation of 0.1,
    # which would otherwise turn IR / t-stat into 10^15-scale garbage. IC
    # values are rounded to 6 dp in compute_ic_series, so anything below 1e-12
    # is provably within that rounding tolerance and must be treated as zero.
    std_eff = std_ic if std_ic > 1e-12 else 0.0
    ir = mean_ic / std_eff if std_eff > 0 else 0
    ic_tstat = mean_ic / (std_eff / np.sqrt(len(ics))) if std_eff > 0 else 0
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


# ---------------------------------------------------------------------------
# compute_ic_decay — IC at multiple forward horizons
# ---------------------------------------------------------------------------

# Fibonacci-ish default lag grid — matches legacy behaviour.
_DEFAULT_LAGS: tuple[int, ...] = (1, 2, 3, 5, 8, 13, 21, 34, 55, 89)


def compute_ic_decay(
    factor: pd.Series,
    close: pd.Series,
    lags: list[int] | None = None,
) -> list[dict]:
    """IC at multiple forward horizons (decay curve).

    Output shape: ``[{"lag": int, "ic": float}, ...]``.  Lag-groups with
    fewer than 30 paired observations emit ``ic=0`` (matches legacy).
    """
    if lags is None:
        lags = list(_DEFAULT_LAGS)

    results = []
    for lag in lags:
        fwd = forward_returns(close, lag)
        paired = pd.DataFrame({"f": factor, "r": fwd}).dropna()
        paired = paired[np.isfinite(paired["f"]) & np.isfinite(paired["r"])]
        if len(paired) < 30:
            results.append({"lag": lag, "ic": 0})
            continue
        ic, _ = spearmanr(paired["f"], paired["r"])
        results.append({
            "lag": lag,
            "ic": round(float(ic), 6) if np.isfinite(ic) else 0,
        })

    return results


# ---------------------------------------------------------------------------
# compute_half_life — first lag where |IC| drops to ≤ half of max |IC|
# ---------------------------------------------------------------------------

def compute_half_life(decay: list[dict]) -> int | None:
    """Find half-life from decay curve (lag where |IC| drops to half of max).

    Returns ``None`` if the curve is empty or the peak |IC| is below a
    noise-floor threshold (< 0.001).  Otherwise returns the first lag whose
    |IC| is ≤ half of the peak; falls back to the last lag if no such drop
    is found.  Matches ``research.analysis.compute_half_life`` exactly.
    """
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


__all__ = [
    "compute_ic_decay",
    "compute_ic_series",
    "compute_ic_summary",
    "compute_half_life",
    "forward_returns",
]
