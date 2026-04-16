"""Pure-computation helpers for backtest result sections.

These functions operate on primitive inputs (lists of dicts/tuples, numpy
arrays, dicts) rather than NautilusTrader objects, so they can be unit-tested
independently of NT.  ``extract.py`` is responsible for converting NT objects
into the primitive inputs consumed here.

Each helper returns a small, well-defined result dictionary or list suitable
for JSON serialisation, matching the keys produced by the original monolithic
``extract_backtest_results`` function.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from tinohelm.backtest.result.statistics import (
    _ANN_FACTOR,
    _compute_min_backtest_length,
    _compute_monte_carlo,
    _compute_psr,
    _norm_ppf,
    _safe_float,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Section 8 — equity curve construction
# ---------------------------------------------------------------------------

def build_equity_curve(
    trade_closes: list[tuple[int, float]],
    starting_balance: float,
    max_points: int = 2000,
) -> list[dict[str, Any]]:
    """Build a daily equity curve from closed-trade (ts_closed_ns, pnl) tuples.

    Aggregates realized PnL by UTC close date, then produces a chronologically
    sorted list of ``{timestamp, equity, returns_pct, drawdown_pct}`` points.
    If the resulting curve exceeds *max_points*, it is uniformly downsampled
    (first and last points always preserved).

    ``returns_pct`` is relative to *starting_balance*; it is overwritten with
    proper daily portfolio returns in :func:`recompute_risk_metrics_from_equity_curve`.
    """
    daily_pnl: dict[str, float] = defaultdict(float)
    for ts_closed, pnl in trade_closes:
        if ts_closed and ts_closed > 0:
            close_date = datetime.fromtimestamp(
                int(ts_closed) / 1e9, tz=timezone.utc
            ).strftime("%Y-%m-%d")
            daily_pnl[close_date] += float(pnl)

    equity_curve: list[dict[str, Any]] = []
    if not daily_pnl:
        return equity_curve

    cum_pnl = 0.0
    peak_equity = starting_balance
    for d in sorted(daily_pnl.keys()):
        cum_pnl += daily_pnl[d]
        equity_val = starting_balance + cum_pnl
        peak_equity = max(peak_equity, equity_val)
        dd_pct = ((equity_val - peak_equity) / peak_equity * 100) if peak_equity > 0 else 0.0
        ret_pct = (daily_pnl[d] / starting_balance * 100) if starting_balance > 0 else 0.0
        equity_curve.append({
            "timestamp": d,
            "equity": round(equity_val, 4),
            "returns_pct": round(ret_pct, 4),
            "drawdown_pct": round(dd_pct, 4),
        })

    if max_points > 0 and len(equity_curve) > max_points:
        step = len(equity_curve) / max_points
        indices = [int(i * step) for i in range(max_points)]
        indices[-1] = len(equity_curve) - 1
        equity_curve = [equity_curve[i] for i in indices]

    return equity_curve


# ---------------------------------------------------------------------------
# Section 8b — risk metrics recomputation
# ---------------------------------------------------------------------------

def recompute_risk_metrics_from_equity_curve(
    equity_curve: list[dict[str, Any]],
    starting_balance: float,
) -> dict[str, Any] | None:
    """Recompute Sharpe/Sortino/Calmar/CAGR/MaxDD from a dollar equity curve.

    NT's ``analyzer.returns()`` uses per-trade notional returns which inflate
    metrics for leveraged futures.  Recomputing from equity better matches the
    industry-standard portfolio-return definition.

    Mutates each entry's ``returns_pct`` in *equity_curve* in place to reflect
    the proper daily portfolio return (previously it was a ratio relative to
    starting balance).

    Returns a dict of metrics plus the intermediate arrays needed by
    downstream sections (extended statistics, rolling analytics).  Returns
    ``None`` when fewer than 2 equity points are available.
    """
    if len(equity_curve) < 2:
        return None

    eq_values = [starting_balance] + [pt["equity"] for pt in equity_curve]
    eq_arr = np.array(eq_values, dtype=float)
    daily_rets = np.diff(eq_arr) / eq_arr[:-1]

    # Mutate returns_pct to proper daily portfolio return (replaces placeholder)
    for i, pt in enumerate(equity_curve):
        pt["returns_pct"] = round(float(daily_rets[i]) * 100, 4)

    n_days = len(daily_rets)
    mean_ret = float(daily_rets.mean())
    std_ret = float(daily_rets.std(ddof=1)) if n_days > 1 else 0.0

    first_date = datetime.strptime(equity_curve[0]["timestamp"], "%Y-%m-%d")
    last_date = datetime.strptime(equity_curve[-1]["timestamp"], "%Y-%m-%d")
    calendar_days = max((last_date - first_date).days, 1)

    peak_arr = np.maximum.accumulate(eq_arr[1:])
    dd_arr = (eq_arr[1:] - peak_arr) / peak_arr
    max_drawdown = round(float(dd_arr.min()), 6) if len(dd_arr) > 0 else 0.0

    final_eq = eq_arr[-1]
    if final_eq > 0 and starting_balance > 0:
        cagr = round((final_eq / starting_balance) ** (_ANN_FACTOR / calendar_days) - 1, 6)
    else:
        cagr = None

    sharpe = round(mean_ret / std_ret * math.sqrt(_ANN_FACTOR), 4) if std_ret > 1e-12 else None

    downside = daily_rets[daily_rets < 0]
    ds_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = round(mean_ret / ds_std * math.sqrt(_ANN_FACTOR), 4) if ds_std > 1e-12 else None

    if cagr is not None and abs(max_drawdown) > 1e-12:
        calmar = round(cagr / abs(max_drawdown), 4)
    else:
        calmar = None

    returns_volatility = round(std_ret * math.sqrt(_ANN_FACTOR), 4) if std_ret > 1e-12 else None
    total_return_pct = round((final_eq / starting_balance - 1) * 100, 4) if starting_balance > 0 else 0.0

    return {
        "daily_rets": daily_rets,
        "dd_arr": dd_arr,
        "n_days": n_days,
        "mean_ret": mean_ret,
        "std_ret": std_ret,
        "calendar_days": calendar_days,
        "max_drawdown": max_drawdown,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "returns_volatility": returns_volatility,
        "total_return_pct": total_return_pct,
    }


# ---------------------------------------------------------------------------
# Section 8c — extended statistics
# ---------------------------------------------------------------------------

def compute_extended_statistics(
    daily_rets: np.ndarray,
    dd_arr: np.ndarray,
    mean_ret: float,
    std_ret: float,
) -> dict[str, Any]:
    """Extended per-day and tail-risk statistics.

    Returns a flat dict with best/worst day, skewness, kurtosis, tail ratio,
    stability (R² of cumulative returns), VaR/CVaR, downside deviation, ulcer
    index, and normal distribution parameters.  All values are JSON-safe
    (NaN/Inf sanitised to None).
    """
    n_days = len(daily_rets)
    if n_days == 0:
        return {}

    best_day = round(float(daily_rets.max() * 100), 4)
    worst_day = round(float(daily_rets.min() * 100), 4)
    positive_days_pct = round(float((daily_rets > 0).sum() / n_days * 100), 2)

    try:
        from scipy.stats import skew as _sp_skew, kurtosis as _sp_kurt
        skewness = _safe_float(_sp_skew(daily_rets))
        kurtosis_val = _safe_float(_sp_kurt(daily_rets, fisher=True))
    except ImportError:
        if std_ret > 1e-12:
            skewness = _safe_float(float(np.mean(((daily_rets - mean_ret) / std_ret) ** 3)))
            kurtosis_val = _safe_float(float(np.mean(((daily_rets - mean_ret) / std_ret) ** 4) - 3))
        else:
            skewness = kurtosis_val = None

    p95 = float(np.percentile(daily_rets, 95))
    p5 = float(np.percentile(daily_rets, 5))
    tail_ratio = round(p95 / abs(p5), 4) if abs(p5) > 1e-12 else None

    cum_rets = np.cumsum(daily_rets)
    x_idx = np.arange(len(cum_rets))
    if len(x_idx) > 1:
        slope, intercept = np.polyfit(x_idx, cum_rets, 1)
        y_pred = slope * x_idx + intercept
        ss_res = float(np.sum((cum_rets - y_pred) ** 2))
        ss_tot = float(np.sum((cum_rets - cum_rets.mean()) ** 2))
        stability = round(1 - ss_res / ss_tot, 4) if ss_tot > 1e-12 else None
    else:
        stability = None

    gains = daily_rets[daily_rets > 0].sum()
    losses_abs = abs(daily_rets[daily_rets < 0].sum())
    omega_ratio = round(float(gains / losses_abs), 4) if losses_abs > 1e-12 else None

    var_95 = round(float(np.percentile(daily_rets, 5) * 100), 4)
    var_99 = round(float(np.percentile(daily_rets, 1) * 100), 4)

    var_thresh = np.percentile(daily_rets, 5)
    below_var = daily_rets[daily_rets <= var_thresh]
    cvar_95 = round(float(below_var.mean() * 100), 4) if len(below_var) > 0 else None

    ds_rets = daily_rets[daily_rets < 0]
    downside_dev = round(float(ds_rets.std(ddof=1) * math.sqrt(_ANN_FACTOR)), 4) if len(ds_rets) > 1 else None

    ulcer_index = round(float(np.sqrt(np.mean(dd_arr ** 2))), 6) if len(dd_arr) > 0 else None

    max_daily_loss = round(float(daily_rets.min() * 100), 4)
    normal_dist_mean = round(float(mean_ret * 100), 6)
    normal_dist_std = round(float(std_ret * 100), 6)

    return {
        "best_day": best_day,
        "worst_day": worst_day,
        "positive_days_pct": positive_days_pct,
        "skewness": skewness,
        "kurtosis": kurtosis_val,
        "tail_ratio": tail_ratio,
        "stability": stability,
        "omega_ratio": omega_ratio,
        "var_95": var_95,
        "var_99": var_99,
        "cvar_95": cvar_95,
        "downside_dev": downside_dev,
        "ulcer_index": ulcer_index,
        "max_daily_loss": max_daily_loss,
        "normal_dist_mean": normal_dist_mean,
        "normal_dist_std": normal_dist_std,
    }


# ---------------------------------------------------------------------------
# Section 9 — per-instrument breakdown
# ---------------------------------------------------------------------------

def compute_per_instrument_basic(
    trade_records: list[dict[str, Any]],
    starting_balance: float,
) -> dict[str, dict[str, Any]]:
    """Per-instrument PnL, win rate, profit factor, and trade counts.

    *trade_records* is a list of dicts with keys ``instrument``, ``pnl``.
    Zero-PnL trades are counted as losses (matches the monolithic behaviour
    prior to refactor where the ``else`` branch swept them in).
    """
    inst_buckets: dict[str, list[float]] = defaultdict(list)
    for t in trade_records:
        inst_buckets[t["instrument"]].append(float(t["pnl"]))

    result: dict[str, dict[str, Any]] = {}
    for inst_id, pnls in inst_buckets.items():
        wins = sum(1 for v in pnls if v > 0)
        losses = sum(1 for v in pnls if v <= 0)
        profit = sum(v for v in pnls if v > 0)
        loss = abs(sum(v for v in pnls if v <= 0))
        total = wins + losses
        result[inst_id] = {
            "total_trades": total,
            "winning_trades": wins,
            "losing_trades": losses,
            "win_rate": round(wins / total, 4) if total > 0 else 0.0,
            "total_pnl": round(sum(pnls), 4),
            "gross_profit": round(profit, 4),
            "gross_loss": round(loss, 4),
            "profit_factor": round(profit / loss, 4) if loss > 0 else None,
            "largest_win": round(max(pnls), 4) if pnls else None,
            "largest_loss": round(min(pnls), 4) if pnls else None,
            "avg_pnl": round(sum(pnls) / len(pnls), 4) if pnls else None,
            "return_pct": round(sum(pnls) / starting_balance * 100, 4) if starting_balance > 0 else 0.0,
        }
    return result


# ---------------------------------------------------------------------------
# Section 11 — drawdown periods from equity curve
# ---------------------------------------------------------------------------

def compute_drawdown_periods(
    equity_curve: list[dict[str, Any]],
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """Identify contiguous drawdown periods from an equity curve.

    A drawdown period starts when ``drawdown_pct`` drops below zero and ends
    when it recovers to zero.  Returns the *top_n* most severe periods (most
    negative max_drawdown first).
    """
    if not equity_curve or len(equity_curve) < 2:
        return []

    periods: list[dict[str, Any]] = []
    in_drawdown = False
    dd_start = None
    dd_trough = 0.0
    dd_trough_ts = None

    for pt in equity_curve:
        dd_val = pt["drawdown_pct"] / 100.0
        ts = datetime.strptime(pt["timestamp"], "%Y-%m-%d")

        if dd_val < -1e-8:
            if not in_drawdown:
                in_drawdown = True
                dd_start = ts
                dd_trough = dd_val
                dd_trough_ts = ts
            elif dd_val < dd_trough:
                dd_trough = dd_val
                dd_trough_ts = ts
        else:
            if in_drawdown:
                periods.append({
                    "start": str(dd_start.date()),
                    "trough_date": str(dd_trough_ts.date()),
                    "recovery_date": str(ts.date()),
                    "max_drawdown_pct": round(float(dd_trough) * 100, 4),
                    "duration_days": (ts - dd_start).days,
                    "recovery_days": (ts - dd_trough_ts).days,
                })
                in_drawdown = False

    # Ongoing drawdown at the end
    if in_drawdown and dd_start is not None:
        last_ts = datetime.strptime(equity_curve[-1]["timestamp"], "%Y-%m-%d")
        periods.append({
            "start": str(dd_start.date()),
            "trough_date": str(dd_trough_ts.date()),
            "recovery_date": None,
            "max_drawdown_pct": round(float(dd_trough) * 100, 4),
            "duration_days": (last_ts - dd_start).days,
            "recovery_days": None,
        })

    periods.sort(key=lambda x: x["max_drawdown_pct"])
    return periods[:top_n]


# ---------------------------------------------------------------------------
# Section 11b — annual returns
# ---------------------------------------------------------------------------

def compute_annual_returns(
    equity_curve: list[dict[str, Any]],
    starting_balance: float,
) -> list[dict[str, Any]]:
    """Year-over-year compounded return (%), computed from equity curve."""
    if not equity_curve or len(equity_curve) < 2:
        return []

    year_boundaries: dict[int, dict[str, float]] = {}
    prev_eq = starting_balance
    for pt in equity_curve:
        year = int(pt["timestamp"][:4])
        eq = pt["equity"]
        if year not in year_boundaries:
            year_boundaries[year] = {"first_eq": prev_eq, "last_eq": eq}
        else:
            year_boundaries[year]["last_eq"] = eq
        prev_eq = eq

    out: list[dict[str, Any]] = []
    for y in sorted(year_boundaries.keys()):
        b = year_boundaries[y]
        ret = (b["last_eq"] / b["first_eq"] - 1) * 100 if b["first_eq"] > 0 else 0.0
        out.append({"year": y, "return_pct": round(ret, 4)})
    return out


# ---------------------------------------------------------------------------
# Section 11d — daily-returns distribution histogram
# ---------------------------------------------------------------------------

def compute_returns_distribution(
    daily_rets: np.ndarray,
    bins: int = 40,
) -> list[dict[str, Any]]:
    """Histogram of daily returns (as percentages)."""
    if daily_rets is None or len(daily_rets) < 2:
        return []
    dr_pct = daily_rets * 100
    counts, edges = np.histogram(dr_pct, bins=bins)
    return [
        {
            "bin_start": round(float(edges[j]), 4),
            "bin_end": round(float(edges[j + 1]), 4),
            "count": int(counts[j]),
        }
        for j in range(len(counts))
    ]


# ---------------------------------------------------------------------------
# Section 11e — QQ plot data
# ---------------------------------------------------------------------------

def compute_qq_plot_data(
    daily_rets: np.ndarray,
    max_points: int = 200,
) -> list[dict[str, float]]:
    """Theoretical (normal) vs empirical quantiles for a QQ plot."""
    if daily_rets is None or len(daily_rets) < 2:
        return []

    n = len(daily_rets)
    sorted_rets = np.sort(daily_rets)
    probs = np.linspace(1 / (n + 1), n / (n + 1), n)

    try:
        from scipy.stats import norm as _norm
        theoretical = _norm.ppf(probs)
    except ImportError:
        theoretical = np.array([_norm_ppf(float(p)) for p in probs])

    if n > max_points:
        indices = np.linspace(0, n - 1, max_points, dtype=int)
        sorted_rets = sorted_rets[indices]
        theoretical = theoretical[indices]

    return [
        {"theoretical": round(float(t), 6), "empirical": round(float(e), 6)}
        for t, e in zip(theoretical, sorted_rets)
    ]


# ---------------------------------------------------------------------------
# Section 11k — benchmark-relative metrics (alpha / beta / R² / IR)
# ---------------------------------------------------------------------------

def compute_benchmark_relative_metrics(
    daily_rets: np.ndarray,
    benchmark_daily_returns: np.ndarray,
    min_obs: int = 30,
) -> dict[str, Any]:
    """Alpha (annualised), beta, R², and information ratio vs benchmark."""
    result: dict[str, Any] = {
        "alpha": None,
        "beta": None,
        "r_squared": None,
        "information_ratio": None,
    }
    if daily_rets is None or benchmark_daily_returns is None:
        return result
    min_len = min(len(daily_rets), len(benchmark_daily_returns))
    if min_len < min_obs:
        return result

    sr = daily_rets[:min_len]
    br = benchmark_daily_returns[:min_len]
    cov_sb = float(np.cov(sr, br)[0, 1])
    var_b = float(np.var(br, ddof=1))
    if var_b <= 1e-12:
        return result

    beta = round(cov_sb / var_b, 4)
    alpha = round((float(sr.mean()) - beta * float(br.mean())) * _ANN_FACTOR, 4)
    corr = float(np.corrcoef(sr, br)[0, 1])
    r_squared = round(corr ** 2, 4) if not math.isnan(corr) else None
    excess = sr - br
    te = float(excess.std(ddof=1))
    ir = round(float(excess.mean()) / te * math.sqrt(_ANN_FACTOR), 4) if te > 1e-12 else None

    result.update({
        "alpha": alpha,
        "beta": beta,
        "r_squared": r_squared,
        "information_ratio": ir,
    })
    return result


# ---------------------------------------------------------------------------
# Section 12b — streak sequence
# ---------------------------------------------------------------------------

def compute_streak_sequence(pnls: list[float]) -> list[dict[str, Any]]:
    """Contiguous win/loss streaks with count and aggregated PnL.

    Zero-PnL trades are counted as loss streaks (matches prior behaviour).
    """
    streaks: list[dict[str, Any]] = []
    cur_type: str | None = None
    cur_count = 0
    cur_pnl = 0.0
    for pv in pnls:
        t = "win" if pv > 0 else "loss"
        if t == cur_type:
            cur_count += 1
            cur_pnl += pv
        else:
            if cur_type is not None:
                streaks.append({
                    "streak_num": len(streaks) + 1,
                    "type": cur_type,
                    "count": cur_count,
                    "total_pnl": round(cur_pnl, 4),
                })
            cur_type = t
            cur_count = 1
            cur_pnl = pv
    if cur_type is not None:
        streaks.append({
            "streak_num": len(streaks) + 1,
            "type": cur_type,
            "count": cur_count,
            "total_pnl": round(cur_pnl, 4),
        })
    return streaks


# ---------------------------------------------------------------------------
# Section 12b — long vs short comparison
# ---------------------------------------------------------------------------

def compute_long_vs_short(trade_sides: list[tuple[str, float]]) -> dict[str, Any]:
    """Long/short summary given (side, pnl) tuples."""
    def _side_stats(pnls: list[float]) -> dict[str, Any]:
        if not pnls:
            return {"trades": 0, "total_pnl": 0.0, "avg_pnl": 0.0, "win_rate": 0.0}
        wins = sum(1 for v in pnls if v > 0)
        total = sum(pnls)
        return {
            "trades": len(pnls),
            "total_pnl": round(total, 4),
            "avg_pnl": round(total / len(pnls), 4),
            "win_rate": round(wins / len(pnls), 4),
        }

    longs = [p for s, p in trade_sides if s == "BUY"]
    shorts = [p for s, p in trade_sides if s == "SELL"]
    return {"long": _side_stats(longs), "short": _side_stats(shorts)}


# ---------------------------------------------------------------------------
# Section 12b — return by day-of-week / hour
# ---------------------------------------------------------------------------

_DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def compute_return_by_dow(
    trade_times: list[tuple[int, float]],
) -> list[dict[str, Any]]:
    """Group PnLs by UTC day-of-week (0=Mon..6=Sun) given (ts_ns, pnl) tuples."""
    buckets: dict[int, list[float]] = {i: [] for i in range(7)}
    for ts_ns, pnl in trade_times:
        if not ts_ns or ts_ns <= 0:
            continue
        dt = datetime.fromtimestamp(int(ts_ns) / 1e9, tz=timezone.utc)
        buckets[dt.weekday()].append(float(pnl))
    return [
        {
            "dow": i,
            "dow_name": _DOW_NAMES[i],
            "values": [round(v, 4) for v in buckets[i]],
        }
        for i in range(7)
    ]


def compute_return_by_hour(
    trade_times: list[tuple[int, float]],
) -> list[dict[str, Any]]:
    """Group PnLs by UTC hour-of-day (0..23) given (ts_ns, pnl) tuples."""
    buckets: dict[int, list[float]] = {i: [] for i in range(24)}
    for ts_ns, pnl in trade_times:
        if not ts_ns or ts_ns <= 0:
            continue
        dt = datetime.fromtimestamp(int(ts_ns) / 1e9, tz=timezone.utc)
        buckets[dt.hour].append(float(pnl))
    return [
        {"hour": h, "values": [round(v, 4) for v in buckets[h]]}
        for h in range(24)
    ]


# ---------------------------------------------------------------------------
# Section 10 — monthly & weekly returns from equity curve
# ---------------------------------------------------------------------------

def compute_periodic_returns(
    equity_curve: list[dict[str, Any]],
    starting_balance: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Monthly and weekly compounded daily returns (sum-of-daily approximation).

    Matches the monolithic implementation: weekly key is the Sunday ending date
    (ISO Monday-start week + 6 days).
    """
    if not equity_curve or len(equity_curve) < 2:
        return [], []

    monthly_agg: dict[str, float] = defaultdict(float)
    weekly_agg: dict[str, float] = defaultdict(float)

    prev_equity = starting_balance
    for pt in equity_curve:
        eq = pt["equity"]
        daily_ret = (eq - prev_equity) / prev_equity if prev_equity > 0 else 0.0
        month_key = pt["timestamp"][:7]
        monthly_agg[month_key] += daily_ret

        d = datetime.strptime(pt["timestamp"], "%Y-%m-%d")
        week_start = d - pd.Timedelta(days=d.weekday())
        week_key = (week_start + pd.Timedelta(days=6)).strftime("%Y-%m-%d")
        weekly_agg[week_key] += daily_ret

        prev_equity = eq

    monthly = [
        {"period": p, "return_pct": round(float(monthly_agg[p]) * 100, 4)}
        for p in sorted(monthly_agg.keys())
    ]
    weekly = [
        {"period": p, "return_pct": round(float(weekly_agg[p]) * 100, 4)}
        for p in sorted(weekly_agg.keys())
    ]
    return monthly, weekly


# ---------------------------------------------------------------------------
# Section 9b — advanced per-instrument analytics
# ---------------------------------------------------------------------------

def _safe_round(v: float | None, n: int = 4) -> float | None:
    """Round a value to *n* decimals, returning None for NaN/Inf/None."""
    if v is None:
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, n)


def compute_per_instrument_advanced(
    closed_trades: list[dict[str, Any]],
    per_instrument_basic: dict[str, dict[str, Any]],
    starting_balance: float,
) -> dict[str, Any]:
    """Advanced multi-instrument analytics (correlation, diversification, per-inst risk).

    *closed_trades* is a list of dicts with keys ``instrument`` (str),
    ``ts_closed`` (int nanoseconds), and ``pnl`` (float).  Each entry represents
    one realised trade.  *per_instrument_basic* is the output of
    :func:`compute_per_instrument_basic`; it is consulted for ``total_pnl``
    when deriving recovery factor.

    Returns a dict with five keys (all empty when ``len(per_instrument_basic) < 2``):

    * ``per_instrument_updates`` — dict of dicts containing
      ``sharpe_ratio``, ``sortino_ratio``, ``max_drawdown``, ``recovery_factor``
      for each instrument.  Intended to be merged into the per-instrument rows.
    * ``instrument_cumulative_pnl`` — dict of instrument -> list of
      ``{date, cum_pnl}`` rows suitable for stacked-area plots.
    * ``instrument_correlation`` — dict of dicts giving pairwise Pearson
      correlations of daily PnL (empty when fewer than 10 trading days).
    * ``monthly_pnl_heatmap`` — list of ``{instrument, month, pnl}`` rows
      (cartesian product of sorted instruments × sorted months).
    * ``portfolio_analytics`` — dict with ``diversification_ratio`` and
      ``diversification_benefit_pct`` (equal-weight basket; empty when <10
      days or <2 instruments).

    All annualisation uses 365 (crypto convention) to match the monolithic
    behaviour that preceded the extraction.  NaN/Inf values are sanitised to
    ``None``.
    """
    empty: dict[str, Any] = {
        "per_instrument_updates": {},
        "instrument_cumulative_pnl": {},
        "instrument_correlation": {},
        "monthly_pnl_heatmap": [],
        "portfolio_analytics": {},
    }
    if len(per_instrument_basic) < 2:
        return empty

    # Build per-instrument daily PnL matrix (grouped by UTC close date).
    daily_pnl_map: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    all_dates: set[str] = set()
    for t in closed_trades:
        inst = str(t["instrument"])
        ts_closed = t.get("ts_closed")
        if not ts_closed or ts_closed <= 0:
            continue
        close_date = datetime.fromtimestamp(
            int(ts_closed) / 1e9, tz=timezone.utc,
        ).strftime("%Y-%m-%d")
        daily_pnl_map[inst][close_date] += float(t["pnl"])
        all_dates.add(close_date)

    sorted_dates = sorted(all_dates)
    instruments = sorted(per_instrument_basic.keys())
    n_dates = len(sorted_dates)
    n_inst = len(instruments)

    # Cumulative PnL per instrument (stacked-area chart source).
    instrument_cumulative_pnl: dict[str, list[dict[str, Any]]] = {}
    for inst in instruments:
        cum = 0.0
        curve: list[dict[str, Any]] = []
        for d in sorted_dates:
            cum += daily_pnl_map[inst].get(d, 0.0)
            curve.append({"date": d, "cum_pnl": round(cum, 2)})
        instrument_cumulative_pnl[inst] = curve

    # Daily returns matrix (n_dates x n_inst).  Returns are expressed as
    # fractions of *starting_balance* to match the monolithic behaviour.
    returns_matrix = np.zeros((n_dates, n_inst)) if n_dates > 0 else np.zeros((0, n_inst))
    for j, inst in enumerate(instruments):
        for i, d in enumerate(sorted_dates):
            returns_matrix[i, j] = daily_pnl_map[inst].get(d, 0.0) / starting_balance if starting_balance else 0.0

    # Pairwise Pearson correlation (requires ≥10 trading days).
    instrument_correlation: dict[str, dict[str, float]] = {}
    if n_dates >= 10:
        corr = np.corrcoef(returns_matrix.T)
        for i, inst_i in enumerate(instruments):
            instrument_correlation[inst_i] = {}
            for j, inst_j in enumerate(instruments):
                if i == j:
                    continue
                val = corr[i, j]
                if not (np.isnan(val) or np.isinf(val)):
                    instrument_correlation[inst_i][inst_j] = round(float(val), 4)

    # Per-instrument risk (Sharpe/Sortino/MaxDD/Recovery factor).
    per_instrument_updates: dict[str, dict[str, Any]] = {}
    for idx, inst in enumerate(instruments):
        arr = returns_matrix[:, idx] if n_dates > 0 else np.zeros(0)
        mean_ret = float(arr.mean()) if n_dates > 0 else 0.0
        std_ret = float(arr.std(ddof=1)) if n_dates > 1 else 0.0

        inst_sharpe = (mean_ret / std_ret * math.sqrt(365)) if std_ret > 1e-12 else None

        downside = arr[arr < 0]
        ds_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
        inst_sortino = (mean_ret / ds_std * math.sqrt(365)) if ds_std > 1e-12 else None

        cum_pnl = np.cumsum([daily_pnl_map[inst].get(d, 0.0) for d in sorted_dates]) if n_dates > 0 else np.zeros(0)
        running_max = np.maximum.accumulate(cum_pnl) if len(cum_pnl) > 0 else np.zeros(0)
        dd = cum_pnl - running_max if len(cum_pnl) > 0 else np.zeros(0)
        max_dd = float(dd.min()) if len(dd) > 0 else 0.0
        max_dd_pct = max_dd / starting_balance if starting_balance > 0 else 0.0

        inst_total_pnl = per_instrument_basic[inst].get("total_pnl", 0.0)
        recovery = abs(inst_total_pnl / max_dd) if max_dd < -0.01 else None

        per_instrument_updates[inst] = {
            "sharpe_ratio": _safe_round(inst_sharpe),
            "sortino_ratio": _safe_round(inst_sortino),
            "max_drawdown": _safe_round(max_dd_pct),
            "recovery_factor": _safe_round(recovery),
        }

    # Monthly PnL heatmap (instrument × month).
    monthly_map: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for t in closed_trades:
        ts_closed = t.get("ts_closed")
        if not ts_closed or ts_closed <= 0:
            continue
        inst = str(t["instrument"])
        close_month = datetime.fromtimestamp(
            int(ts_closed) / 1e9, tz=timezone.utc,
        ).strftime("%Y-%m")
        monthly_map[inst][close_month] += float(t["pnl"])

    all_months = sorted({m for mm in monthly_map.values() for m in mm})
    monthly_pnl_heatmap: list[dict[str, Any]] = []
    for inst in instruments:
        for month in all_months:
            monthly_pnl_heatmap.append({
                "instrument": inst,
                "month": month,
                "pnl": round(monthly_map[inst].get(month, 0.0), 2),
            })

    # Diversification ratio (equal-weight basket).
    portfolio_analytics: dict[str, Any] = {}
    if n_dates >= 10 and n_inst >= 2:
        weights = np.ones(n_inst) / n_inst
        inst_vols = np.array([returns_matrix[:, j].std(ddof=1) for j in range(n_inst)])
        cov = np.cov(returns_matrix.T)
        port_vol = float(np.sqrt(weights @ cov @ weights))
        wav = float(np.dot(weights, inst_vols))
        if port_vol > 1e-12 and wav > 1e-12:
            portfolio_analytics["diversification_ratio"] = round(wav / port_vol, 4)
            portfolio_analytics["diversification_benefit_pct"] = round((1.0 - port_vol / wav) * 100, 2)

    return {
        "per_instrument_updates": per_instrument_updates,
        "instrument_cumulative_pnl": instrument_cumulative_pnl,
        "instrument_correlation": instrument_correlation,
        "monthly_pnl_heatmap": monthly_pnl_heatmap,
        "portfolio_analytics": portfolio_analytics,
    }


# ---------------------------------------------------------------------------
# Section 11f — benchmark equity curve (equal-weight buy & hold)
# ---------------------------------------------------------------------------

def compute_benchmark_equity_curve(
    equity_curve: list[dict[str, Any]],
    inst_daily_close: dict[str, dict[str, float]],
    starting_balance: float,
) -> list[dict[str, Any]]:
    """Equal-weight buy-and-hold benchmark curve aligned to *equity_curve* dates.

    *inst_daily_close* is ``{instrument: {YYYY-MM-DD: close_price}}``.  Each
    instrument is allocated ``starting_balance / len(inst_daily_close)`` worth
    of units at its earliest available price on/after the first equity-curve
    date.  Missing daily prices are forward-filled from the last seen price;
    instruments that never trade on any equity date contribute a flat allocation.

    Returns a list of ``{timestamp, equity}`` rows (rounded to 4 decimals) or
    an empty list when the inputs are insufficient.
    """
    if not equity_curve or len(equity_curve) < 2 or not inst_daily_close:
        return []

    eq_dates = [pt["timestamp"] for pt in equity_curve]
    alloc_per_inst = starting_balance / len(inst_daily_close) if len(inst_daily_close) else 0.0

    inst_units: dict[str, float] = {}
    for inst, closes in inst_daily_close.items():
        for d in eq_dates:
            price = closes.get(d)
            if price is not None and price > 0:
                inst_units[inst] = alloc_per_inst / price
                break

    curve: list[dict[str, Any]] = []
    last_price: dict[str, float] = {}
    for d in eq_dates:
        bm_equity = 0.0
        for inst, units in inst_units.items():
            closes = inst_daily_close.get(inst, {})
            price = closes.get(d)
            if price is not None:
                last_price[inst] = price
            elif inst in last_price:
                price = last_price[inst]
            else:
                price = alloc_per_inst / units if units > 0 else 0
            bm_equity += units * price
        curve.append({
            "timestamp": d,
            "equity": round(bm_equity, 4),
        })
    return curve


def compute_benchmark_daily_returns(
    benchmark_equity_curve: list[dict[str, Any]],
    starting_balance: float,
) -> np.ndarray | None:
    """Daily return series prepended by *starting_balance*.

    Returns ``None`` when the curve has fewer than 2 points.
    """
    if not benchmark_equity_curve or len(benchmark_equity_curve) < 2:
        return None
    bm_values = [starting_balance] + [pt["equity"] for pt in benchmark_equity_curve]
    bm_arr = np.array(bm_values, dtype=float)
    denom = bm_arr[:-1]
    if np.any(denom <= 0):
        return None
    return np.diff(bm_arr) / denom


# ---------------------------------------------------------------------------
# Section 12b — trade-level scalar metrics & chart arrays
# ---------------------------------------------------------------------------

def _histogram_bins(n_items: int, *, cap: int = 30, floor: int = 10) -> int:
    """Choose a histogram bin count from sample size.

    Matches the rule used throughout section 12b: ``min(cap, max(floor, n//5))``.
    """
    return min(cap, max(floor, n_items // 5))


def compute_trade_scalar_metrics(
    pnls: list[float],
    *,
    n_orders: int,
    n_filled_orders: int,
    n_returns_periods: int,
    total_trades: int,
    total_pnl: float,
    max_drawdown: float | None,
    starting_balance: float,
    win_rate: float | None,
    avg_win: float | None,
    avg_loss: float | None,
    expectancy: float | None,
) -> dict[str, float | None]:
    """Compute nine scalar trade-analytics metrics.

    Returned keys:

    * ``median_trade_pnl`` — median realised PnL per trade.
    * ``std_trade_pnl`` — sample std of trade PnL (``ddof=1``); ``None`` if
      fewer than 2 trades.
    * ``fill_rate`` — ``filled/total * 100`` percent; ``None`` when no orders.
    * ``avg_trades_per_day`` — ``total_trades / n_returns_periods``; ``None``
      when no return periods.
    * ``recovery_factor`` — ``(total_pnl / starting_balance) / |max_drawdown|``
      when max_drawdown exceeds the numerical floor; ``None`` otherwise.
    * ``sqn`` — System Quality Number: ``sqrt(N) * mean(pnls) / std(pnls)``.
    * ``kelly_criterion`` — ``(win_rate - (1 - win_rate) / R) * 100`` where
      ``R = |avg_win / avg_loss|``.
    * ``k_ratio`` — Kestner (2003) scale-independent regression slope-to-noise
      ratio of ``log(cumulative_equity)``.
    * ``expectancy_r`` — ``expectancy / |avg_loss|`` (R-multiples).

    All values are JSON-safe (NaN/Inf sanitised to ``None`` via
    :func:`_safe_float`).  Empty *pnls* returns all nine keys set to ``None``.
    """
    keys = (
        "median_trade_pnl",
        "std_trade_pnl",
        "fill_rate",
        "avg_trades_per_day",
        "recovery_factor",
        "sqn",
        "kelly_criterion",
        "k_ratio",
        "expectancy_r",
    )
    out: dict[str, float | None] = {k: None for k in keys}
    if not pnls:
        return out

    pnl_arr = np.array(pnls, dtype=float)
    out["median_trade_pnl"] = _safe_float(round(float(np.median(pnl_arr)), 4))
    if len(pnls) > 1:
        out["std_trade_pnl"] = _safe_float(round(float(np.std(pnl_arr, ddof=1)), 4))

    if n_orders > 0:
        out["fill_rate"] = _safe_float(round(n_filled_orders / n_orders * 100, 4))

    if n_returns_periods > 0:
        out["avg_trades_per_day"] = _safe_float(round(total_trades / n_returns_periods, 4))

    if max_drawdown is not None and abs(max_drawdown) > 1e-12 and starting_balance > 0:
        net_return = total_pnl / starting_balance
        out["recovery_factor"] = _safe_float(round(net_return / abs(max_drawdown), 4))

    pnl_std = float(np.std(pnl_arr, ddof=1)) if len(pnls) > 1 else 0.0
    pnl_mean = float(np.mean(pnl_arr))
    if pnl_std > 1e-12:
        out["sqn"] = _safe_float(round(float(np.sqrt(len(pnls)) * pnl_mean / pnl_std), 4))

    if avg_win is not None and avg_loss is not None and abs(avg_loss) > 1e-12:
        r = abs(avg_win / avg_loss)
        wr = win_rate if win_rate is not None else 0.0
        out["kelly_criterion"] = _safe_float(round((wr - (1 - wr) / r) * 100, 4))

    # K-Ratio: slope of OLS on log(cumulative equity) / (std_err(slope) * sqrt(N)).
    try:
        cum_equity = starting_balance + np.cumsum(pnl_arr)
        pos_mask = cum_equity > 0
        if pos_mask.sum() >= 2:
            log_eq = np.log(cum_equity[pos_mask])
            x = np.arange(len(log_eq), dtype=float)
            n_k = len(x)
            coeffs = np.polyfit(x, log_eq, 1)
            slope = coeffs[0]
            y_pred = slope * x + coeffs[1]
            residuals = log_eq - y_pred
            mse = float(np.sum(residuals ** 2) / (n_k - 2)) if n_k > 2 else 0.0
            x_var = float(np.sum((x - x.mean()) ** 2))
            if x_var > 1e-12 and mse > 0:
                std_err = float(np.sqrt(mse / x_var))
                if std_err > 1e-12:
                    out["k_ratio"] = _safe_float(
                        round(float(slope / (std_err * np.sqrt(n_k))), 4),
                    )
    except (ValueError, FloatingPointError):
        pass

    if expectancy is not None and avg_loss is not None and abs(avg_loss) > 1e-12:
        out["expectancy_r"] = _safe_float(round(expectancy / abs(avg_loss), 4))

    return out


def compute_trade_pnl_distribution(pnls: list[float]) -> list[dict[str, Any]]:
    """Histogram of trade PnL values (adaptive bin count)."""
    if not pnls:
        return []
    pnl_arr = np.array(pnls, dtype=float)
    counts, edges = np.histogram(pnl_arr, bins=_histogram_bins(len(pnls)))
    return [
        {
            "bin_start": round(float(edges[j]), 4),
            "bin_end": round(float(edges[j + 1]), 4),
            "count": int(counts[j]),
        }
        for j in range(len(counts))
    ]


def compute_cumulative_trade_pnl(pnls: list[float]) -> list[dict[str, Any]]:
    """Cumulative realised-PnL curve indexed by trade number (1-based)."""
    if not pnls:
        return []
    cum = np.cumsum(np.array(pnls, dtype=float))
    return [
        {"trade_num": idx + 1, "cumulative_pnl": round(float(v), 4)}
        for idx, v in enumerate(cum)
    ]


def compute_trade_pnl_scatter(
    trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Per-trade scatter points: timestamp, PnL, side, instrument.

    *trades* is a list of dicts with keys ``ts_closed`` (int ns), ``pnl``
    (float), ``side`` (str), and ``instrument`` (str).  ``ts_closed`` of zero
    or ``None`` produces a ``null`` timestamp (matches prior behaviour).
    """
    out: list[dict[str, Any]] = []
    for t in trades:
        ts_c = t.get("ts_closed")
        out.append({
            "timestamp": _format_ns_timestamp_local(ts_c) if ts_c else None,
            "pnl": round(float(t["pnl"]), 4),
            "side": str(t["side"]),
            "instrument": str(t["instrument"]),
        })
    return out


def _format_ns_timestamp_local(ts_ns: Any) -> str | None:
    """Isomorphic ns→ISO formatter (mirror of statistics._format_ns_timestamp).

    Inlined here to avoid a cross-module import cycle when sections.py is
    loaded in isolation during tests.
    """
    if ts_ns is None or ts_ns == 0:
        return None
    try:
        return datetime.fromtimestamp(int(ts_ns) / 1e9, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return None


def compute_holding_time_distribution(
    durations_ns: list[int | float],
) -> list[dict[str, Any]]:
    """Histogram of position holding times in hours.

    Entries with ``duration <= 0`` or ``None`` are ignored.  Adaptive bin count
    matches :func:`compute_trade_pnl_distribution`.
    """
    hours = [int(d) / 3.6e12 for d in durations_ns if d and d > 0]
    if not hours:
        return []
    dur_arr = np.array(hours, dtype=float)
    counts, edges = np.histogram(dur_arr, bins=_histogram_bins(len(hours)))
    return [
        {
            "bin_start": round(float(edges[j]), 4),
            "bin_end": round(float(edges[j + 1]), 4),
            "count": int(counts[j]),
        }
        for j in range(len(counts))
    ]


def compute_mae_mfe(
    positions: list[dict[str, Any]],
    bars_by_instrument: dict[str, list[tuple[int, float, float]]],
) -> list[dict[str, Any]]:
    """Maximum adverse/favourable excursion per closed position.

    *positions* entries must contain: ``instrument`` (str), ``ts_opened`` (int
    ns), ``ts_closed`` (int ns), ``entry_price`` (float), ``side`` (``"BUY"``
    or ``"SELL"``), and ``pnl`` (float).

    *bars_by_instrument* is a mapping ``{instrument_id: [(ts_init_ns, high,
    low), ...]}`` of primitive bar tuples.  The caller is responsible for
    collapsing all bar-types for an instrument into a single list (bars may
    be in any order; filtering is done by timestamp window only).

    For a BUY (long) position:

    * MAE = ``entry - min_low`` within the holding window.
    * MFE = ``max_high - entry``.

    For a SELL (short) position these are inverted.  Positions without
    matching bars or timestamps are skipped.  Returned entries are aligned
    with the surviving positions in input order.
    """
    out: list[dict[str, Any]] = []
    for p in positions:
        inst = str(p.get("instrument") or "")
        bars = bars_by_instrument.get(inst)
        if not bars:
            continue
        ts_o = p.get("ts_opened")
        ts_c = p.get("ts_closed")
        if not ts_o or not ts_c:
            continue

        highs: list[float] = []
        lows: list[float] = []
        for ts_init, high, low in bars:
            if ts_o <= ts_init <= ts_c:
                highs.append(float(high))
                lows.append(float(low))
        if not highs:
            continue

        entry = float(p["entry_price"])
        side = str(p["side"])
        max_high = max(highs)
        min_low = min(lows)

        if side == "BUY":
            mae = entry - min_low
            mfe = max_high - entry
        else:
            mae = max_high - entry
            mfe = entry - min_low

        out.append({
            "pnl": round(float(p["pnl"]), 4),
            "mae": round(float(mae), 4),
            "mfe": round(float(mfe), 4),
            "side": side,
        })
    return out


# ---------------------------------------------------------------------------
# Section 14 — robustness (PSR + MBL + Monte Carlo)
# ---------------------------------------------------------------------------

def compute_robustness(
    trade_pnls: list[float],
    starting_balance: float,
    *,
    daily_sharpe: float | None,
    n_days: int,
    skewness: float | None,
    kurtosis: float | None,
) -> dict[str, Any] | None:
    """Assemble the Layer-1 robustness block.

    Combines Probabilistic Sharpe Ratio (PSR), Minimum Backtest Length (MBL),
    and Monte Carlo equity cone into a single dict ready for inclusion in the
    final result.

    PSR / MBL keys are always present (possibly ``None`` when the backtest has
    insufficient signal).  The five Monte-Carlo keys are only added when
    ``len(trade_pnls) >= 2``; otherwise the returned dict contains only the
    four PSR/MBL keys.
    """
    safe_sk = 0.0 if skewness is None else float(skewness)
    safe_ku = 0.0 if kurtosis is None else float(kurtosis)
    nobs = int(n_days) if n_days else 0

    psr_val = _compute_psr(daily_sharpe, nobs, safe_sk, safe_ku)
    mbl_val = _compute_min_backtest_length(daily_sharpe, safe_sk, safe_ku)

    robustness: dict[str, Any] = {
        "psr": psr_val,
        "min_backtest_length_days": mbl_val,
        "actual_backtest_length_days": nobs,
        "backtest_length_sufficient": (
            (nobs >= mbl_val) if mbl_val is not None else None
        ),
    }

    mc_result = _compute_monte_carlo(trade_pnls, starting_balance)
    if mc_result:
        robustness["mc_equity_cone"] = {
            "percentiles": mc_result["percentiles"],
            "curves": mc_result["curves"],
            "original": mc_result["original"],
            "x_labels": mc_result["x_labels"],
        }
        robustness["mc_probability_of_loss"] = mc_result["mc_probability_of_loss"]
        robustness["mc_5th_percentile_return"] = mc_result["mc_5th_percentile_return"]
        robustness["mc_median_max_drawdown"] = mc_result["mc_median_max_drawdown"]
        robustness["mc_median_final_return"] = mc_result["mc_median_final_return"]
        robustness["mc_num_simulations"] = mc_result["mc_num_simulations"]

    return robustness
