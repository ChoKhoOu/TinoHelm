"""Backtest result statistics helpers.

Pure-computation helpers used by ``extract.py``.  These functions have no
NautilusTrader dependency so they can be unit-tested in isolation.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Callable

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float | None = None) -> float | None:
    """Safely convert a value to float, returning *default* on failure.

    Returns *default* for NaN and Infinity to ensure JSON compatibility.
    """
    if value is None:
        return default
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


def _format_duration_ns(ns: int | float) -> str | None:
    """Convert nanoseconds to a human-readable duration string."""
    if ns is None or ns <= 0:
        return None
    total_seconds = int(ns) // 1_000_000_000
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def _parse_realized_pnl(pnl_obj: Any) -> float:
    """Extract float value from NT Money/realized_pnl.

    NT Money objects stringify as ``"114.60 USDT"``.  ``Decimal(...)`` chokes
    on the currency suffix, so we strip it first.  Also handles ``as_double()``
    when available.
    """
    if pnl_obj is None:
        return 0.0
    try:
        # Prefer .as_double() if the object supports it (Money)
        if hasattr(pnl_obj, "as_double"):
            return float(pnl_obj.as_double())
        s = str(pnl_obj).strip()
        # Strip trailing currency like "USDT", "USD", "BTC"
        parts = s.split()
        return float(parts[0]) if parts else 0.0
    except (ValueError, TypeError, IndexError):
        return 0.0


def _format_order_side(entry: Any) -> str:
    """Convert NT OrderSide enum to readable string."""
    try:
        if hasattr(entry, "name"):
            return entry.name  # e.g. "BUY", "SELL"
        val = int(entry)
        return {1: "BUY", 2: "SELL"}.get(val, str(entry))
    except (ValueError, TypeError):
        return str(entry)


def _format_ns_timestamp(ts_ns: Any) -> str | None:
    """Convert nanosecond timestamp to ISO string."""
    if ts_ns is None or ts_ns == 0:
        return None
    try:
        from datetime import datetime, timezone
        ts_sec = int(ts_ns) / 1e9
        return datetime.fromtimestamp(ts_sec, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return None


def _compute_streaks(pnl_values: list[float]) -> tuple[int, int]:
    """Compute the longest winning and losing streaks from a PnL sequence."""
    max_win_streak = 0
    max_lose_streak = 0
    cur_win = 0
    cur_lose = 0
    for pnl in pnl_values:
        if pnl > 0:
            cur_win += 1
            cur_lose = 0
            max_win_streak = max(max_win_streak, cur_win)
        elif pnl < 0:
            cur_lose += 1
            cur_win = 0
            max_lose_streak = max(max_lose_streak, cur_lose)
    return max_win_streak, max_lose_streak


def _norm_ppf(p: float) -> float:
    """Approximate inverse normal CDF (percent-point function).

    Uses the rational approximation from Abramowitz & Stegun, formula 26.2.23.
    Accurate to ~4.5e-4 for 0 < p < 1.  Pure Python, no scipy required.
    """
    if p <= 0 or p >= 1:
        return 0.0
    if p < 0.5:
        return -_norm_ppf(1 - p)
    t = (-2.0 * math.log(1.0 - p)) ** 0.5
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF approximation. Pure Python, no scipy."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _compute_psr(
    daily_sharpe: float | None,
    n_obs: int,
    skewness: float,
    kurtosis: float,
    benchmark_sr: float = 0.0,
) -> float | None:
    """Probabilistic Sharpe Ratio — Bailey & López de Prado (2012).

    Parameters
    ----------
    daily_sharpe : per-period (daily) Sharpe = mean_ret / std_ret (NOT annualized)
    n_obs : number of daily return observations
    skewness, kurtosis : of daily returns (excess kurtosis, i.e. normal=0)
    benchmark_sr : benchmark daily Sharpe (default 0)
    """
    if daily_sharpe is None or n_obs < 5:
        return None
    sr = daily_sharpe
    denom_sq = 1 - skewness * sr + ((kurtosis - 1) / 4) * sr * sr
    if denom_sq <= 0:
        return None
    z = (sr - benchmark_sr) * math.sqrt(n_obs - 1) / math.sqrt(denom_sq)
    return round(_norm_cdf(z), 4)


def _compute_min_backtest_length(
    daily_sharpe: float | None,
    skewness: float,
    kurtosis: float,
    confidence: float = 0.95,
) -> int | None:
    """Minimum Backtest Length in days — Bailey & López de Prado (2012).

    Returns None if daily_sharpe <= 0 (undefined for non-positive SR).
    """
    if daily_sharpe is None or daily_sharpe <= 0:
        return None
    z_alpha = _norm_ppf(confidence)
    sr = daily_sharpe
    numer = 1 - skewness * sr + ((kurtosis - 1) / 4) * sr * sr
    if numer <= 0:
        return None
    mbl = 1 + numer * (z_alpha / sr) ** 2
    return max(1, int(math.ceil(mbl)))


def _compute_monte_carlo(
    trade_pnls: list[float],
    starting_balance: float,
    n_sims: int = 1000,
    max_curve_points: int = 500,
) -> dict | None:
    """Monte Carlo equity cone via trade-order shuffling.

    Returns None if fewer than 2 trades.
    Uses fixed seed for reproducibility.
    Downsamples curves to *max_curve_points* if n_trades exceeds that.
    """
    if len(trade_pnls) < 2:
        return None

    import numpy as np

    rng = np.random.default_rng(seed=42)
    pnls = np.array(trade_pnls, dtype=np.float64)
    n_trades = len(pnls)

    # Run simulations
    all_curves = np.empty((n_sims, n_trades), dtype=np.float64)
    for i in range(n_sims):
        shuffled = rng.permutation(pnls)
        all_curves[i] = starting_balance + np.cumsum(shuffled)

    # Original equity curve
    original = starting_balance + np.cumsum(pnls)

    # Percentile bands at each trade index
    p5, p25, p50, p75, p95 = np.percentile(
        all_curves, [5, 25, 50, 75, 95], axis=0,
    )

    # Summary metrics
    final_values = all_curves[:, -1]
    final_returns = (final_values - starting_balance) / starting_balance

    # Max drawdown per simulation
    def _mc_max_dd(curve: np.ndarray) -> float:
        peak = np.maximum.accumulate(curve)
        dd = (curve - peak) / np.where(peak > 0, peak, 1.0)
        return float(dd.min())

    max_dds = np.array([_mc_max_dd(all_curves[i]) for i in range(n_sims)])

    # Downsample if too many trades
    if n_trades > max_curve_points:
        indices = np.linspace(0, n_trades - 1, max_curve_points, dtype=int)
        p5, p25, p50, p75, p95 = p5[indices], p25[indices], p50[indices], p75[indices], p95[indices]
        original = original[indices]
        x_labels = indices.tolist()
    else:
        x_labels = list(range(n_trades))

    return {
        "percentiles": [5, 25, 50, 75, 95],
        "curves": {
            "p5": [round(float(v), 2) for v in p5],
            "p25": [round(float(v), 2) for v in p25],
            "p50": [round(float(v), 2) for v in p50],
            "p75": [round(float(v), 2) for v in p75],
            "p95": [round(float(v), 2) for v in p95],
        },
        "original": [round(float(v), 2) for v in original],
        "x_labels": x_labels,
        "mc_probability_of_loss": round(float(np.mean(final_returns < 0)) * 100, 2),
        "mc_5th_percentile_return": round(float(np.percentile(final_returns, 5)) * 100, 2),
        "mc_median_max_drawdown": round(float(np.median(max_dds)) * 100, 2),
        "mc_median_final_return": round(float(np.median(final_returns)) * 100, 2),
        "mc_num_simulations": n_sims,
    }


# ---------------------------------------------------------------------------
# Rolling metric computation helpers
# ---------------------------------------------------------------------------

def _compute_rolling_series(
    daily_rets: np.ndarray,
    timestamps: list[str],
    windows: dict[str, int],
    metric_fn: Callable[[np.ndarray, int, int], float | None],
    max_points: int = 500,
) -> list[dict[str, Any]]:
    """Compute a rolling metric over daily returns with multiple window sizes.

    Parameters
    ----------
    daily_rets : 1-D array of daily portfolio returns.
    timestamps : date strings aligned with *daily_rets* (same length).
    windows : ``{"rolling_3m": 63, "rolling_6m": 126, ...}`` — key names
        become dict keys in the output; values are the look-back window size.
    metric_fn : ``fn(daily_rets, start, end) -> float | None``.  Receives the
        full *daily_rets* array and the half-open slice indices ``[start, end)``
        for the current window.
    max_points : downsample the output to at most this many evenly-spaced
        points.  Set to 0 to disable downsampling.

    Returns
    -------
    list[dict] — each entry has ``"timestamp"`` plus one key per window.
    """
    result: list[dict[str, Any]] = []
    for i, ts in enumerate(timestamps):
        entry: dict[str, Any] = {"timestamp": ts}
        for key, w in windows.items():
            if i + 1 >= w:
                entry[key] = metric_fn(daily_rets, i + 1 - w, i + 1)
            else:
                entry[key] = None
        result.append(entry)

    if max_points > 0 and len(result) > max_points:
        indices = np.linspace(0, len(result) - 1, max_points, dtype=int)
        result = [result[int(idx)] for idx in indices]

    return result


# ---- Metric functions for _compute_rolling_series ----

_ANN_FACTOR = 365  # crypto markets trade 365 days/year


def _rolling_sharpe_fn(daily_rets: np.ndarray, start: int, end: int) -> float | None:
    """Annualized Sharpe ratio for a window slice."""
    wr = daily_rets[start:end]
    m, s = float(wr.mean()), float(wr.std(ddof=1))
    return round(m / s * np.sqrt(_ANN_FACTOR), 4) if s > 1e-12 else None


def _rolling_sortino_fn(daily_rets: np.ndarray, start: int, end: int) -> float | None:
    """Annualized Sortino ratio for a window slice."""
    wr = daily_rets[start:end]
    ds = wr[wr < 0]
    ds_s = float(ds.std(ddof=1)) if len(ds) > 1 else 0.0
    return round(float(wr.mean()) / ds_s * np.sqrt(_ANN_FACTOR), 4) if ds_s > 1e-12 else None


def _rolling_volatility_fn(daily_rets: np.ndarray, start: int, end: int) -> float | None:
    """Annualized volatility for a window slice."""
    wr = daily_rets[start:end]
    s = float(wr.std(ddof=1))
    return round(s * np.sqrt(_ANN_FACTOR), 4)


def _rolling_cumret_fn(daily_rets: np.ndarray, start: int, end: int) -> float | None:
    """Cumulative return (%) for a window slice."""
    wr = daily_rets[start:end]
    return round(float(np.prod(1 + wr) - 1) * 100, 4)


def _make_rolling_beta_fn(
    benchmark_rets: np.ndarray,
) -> Callable[[np.ndarray, int, int], float | None]:
    """Create a rolling beta metric function bound to *benchmark_rets*."""

    def fn(daily_rets: np.ndarray, start: int, end: int) -> float | None:
        sr = daily_rets[start:end]
        br = benchmark_rets[start:end]
        cov_val = float(np.cov(sr, br)[0, 1])
        var_bm = float(np.var(br, ddof=1))
        return round(cov_val / var_bm, 4) if var_bm > 1e-12 else None

    return fn

