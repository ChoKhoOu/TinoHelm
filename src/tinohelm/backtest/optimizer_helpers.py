"""Pure helpers for :class:`tinohelm.backtest.optimizer.BacktestOptimizer`.

Extracted from ``optimizer.py`` for three reasons:

1. **Eliminate "duplicate + drift" risk** — the trial-filter predicate
   (``state == "COMPLETE" and value not None and value != FAIL_VALUE``) was
   copy-pasted across ``compute_dsr`` / ``compute_param_sensitivity`` /
   ``compute_param_stability``.  Three independent definitions = three places
   the rule can drift.  Now a single :func:`filter_completed_trials`.

2. **Pin canonical constants** — ``FAIL_VALUE``, ``FITNESS_METRICS``, and
   ``TRADING_DAYS_PER_YEAR`` are exported as the single source of truth
   so the API route, the optimizer, and the tests all agree.

3. **NT/optuna-free unit testing** — by quarantining all pure logic here,
   the helpers can be exercised in lean CI environments without the NT
   wheel (~100 MB) or the optuna dependency.  The optimizer module itself
   still requires both at runtime, but its math no longer does.

Keep this module pure: no I/O, no logging, no optuna/NT/Redis imports.
The two normal-distribution approximations (:func:`_norm_ppf` /
:func:`_norm_cdf`) are inlined from ``result/statistics.py`` to dodge the
transitive NT import that ``result/__init__.py`` triggers.
"""
from __future__ import annotations

import math
import os
from datetime import date, timedelta
from typing import Any, Sequence


# ---------------------------------------------------------------------------
# Canonical constants — single source of truth
# ---------------------------------------------------------------------------

#: Sentinel returned to Optuna when a trial fails so the study can continue.
FAIL_VALUE: float = -999.0

#: Map of fitness objective short name -> key in ``result["statistics"]``.
FITNESS_METRICS: dict[str, str] = {
    "sharpe": "sharpe_ratio",
    "calmar": "calmar_ratio",
    "sortino": "sortino_ratio",
    "profit": "total_pnl",
}

#: Trading days per calendar year — used to de-annualize Sharpe in DSR.
TRADING_DAYS_PER_YEAR: int = 252


# ---------------------------------------------------------------------------
# Date / window math
# ---------------------------------------------------------------------------

def split_dates(
    start_date: date,
    end_date: date,
    train_pct: float,
) -> tuple[date, date, date, date]:
    """Split ``[start_date, end_date]`` into a train and test segment.

    Returns ``(train_start, train_end, test_start, test_end)``.  The test
    segment starts the day *after* ``train_end`` so the two windows are
    disjoint.  ``train_pct`` is interpreted as percentage points (0-100).
    """
    total_days = (end_date - start_date).days
    train_days = int(total_days * train_pct / 100.0)
    train_end = start_date + timedelta(days=train_days)
    test_start = train_end + timedelta(days=1)
    return start_date, train_end, test_start, end_date


def walk_forward_windows(
    start_date: date,
    end_date: date,
    train_pct: float,
    n_folds: int,
) -> list[tuple[date, date, date, date]]:
    """Generate ``n_folds`` rolling walk-forward train/test windows.

    Test segments are non-overlapping and slide forward.  Each fold's
    train window is sized by ``train_pct`` relative to its test window.
    Falls back to a single :func:`split_dates` window when the inputs
    can't produce any valid fold (e.g. ``train_pct == 100``).
    """
    total_days = (end_date - start_date).days

    test_ratio = 1.0 - train_pct / 100.0
    if test_ratio <= 0 or n_folds <= 0:
        return [split_dates(start_date, end_date, train_pct)]

    test_window = max(1, int(total_days * test_ratio / n_folds))
    train_window = max(1, int(test_window * train_pct / (100.0 * test_ratio)))

    windows: list[tuple[date, date, date, date]] = []
    for i in range(n_folds):
        test_end_d = end_date - timedelta(days=(n_folds - 1 - i) * test_window)
        test_start_d = test_end_d - timedelta(days=test_window - 1)
        train_end_d = test_start_d - timedelta(days=1)
        train_start_d = train_end_d - timedelta(days=train_window - 1)

        if train_start_d < start_date:
            train_start_d = start_date
        if test_end_d > end_date:
            test_end_d = end_date

        if train_start_d >= train_end_d or test_start_d >= test_end_d:
            continue
        windows.append((train_start_d, train_end_d, test_start_d, test_end_d))

    if not windows:
        return [split_dates(start_date, end_date, train_pct)]

    return windows


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------

def extract_fitness(result: dict[str, Any], objective: str) -> float:
    """Extract the fitness scalar from a backtest result dict.

    Returns :data:`FAIL_VALUE` when the objective is unknown, the metric
    is missing, or the metric isn't coercible to float.  This way Optuna
    keeps marching forward instead of crashing on a single bad trial.
    """
    metric_key = FITNESS_METRICS.get(objective)
    if metric_key is None:
        return FAIL_VALUE
    stats = result.get("statistics", {})
    value = stats.get(metric_key)
    if value is None:
        return FAIL_VALUE
    try:
        return float(value)
    except (TypeError, ValueError):
        return FAIL_VALUE


def filter_completed_trials(
    trials_data: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Single-source-of-truth filter for trials usable in robustness stats.

    A trial qualifies when **all** of these hold:

    - ``state == "COMPLETE"`` (Optuna trial state name)
    - ``value`` is not ``None``
    - ``value != FAIL_VALUE`` (i.e. it isn't the failure sentinel)

    Used by :func:`compute_dsr`, :func:`compute_param_sensitivity`, and
    :func:`compute_param_stability` so the predicate can never drift
    between them.
    """
    return [
        t for t in trials_data
        if t.get("state") == "COMPLETE"
        and t.get("value") is not None
        and t["value"] != FAIL_VALUE
    ]


# ---------------------------------------------------------------------------
# Smart defaults
# ---------------------------------------------------------------------------

def auto_n_trials(param_ranges: dict[str, dict[str, Any]]) -> int:
    """Heuristic ``n_trials`` based on search-space dimensionality."""
    n_dims = len(param_ranges)
    if n_dims == 0:
        return 50
    return max(50, n_dims * 20)


def auto_sampler(param_ranges: dict[str, dict[str, Any]]) -> str:
    """Pick ``"cmaes"`` for low-dim continuous spaces, ``"tpe"`` otherwise."""
    n_dims = len(param_ranges)
    has_int = any(p.get("type") == "int" for p in param_ranges.values())
    if n_dims <= 3 and not has_int:
        return "cmaes"
    return "tpe"


def auto_workers(cpu_count: int | None = None) -> int:
    """Pick worker count: half of available cores, clamped to ``[1, 4]``.

    ``cpu_count=None`` reads from :func:`os.cpu_count` (production path);
    pass an explicit integer in tests for determinism.
    """
    if cpu_count is None:
        cpu_count = os.cpu_count() or 2
    return min(4, max(1, cpu_count // 2))


# ---------------------------------------------------------------------------
# Result transformation
# ---------------------------------------------------------------------------

def slim_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Project a full result dict down to the IS/OOS comparison fields.

    Used to keep the persisted ``train_validation`` payload small —
    callers only render statistics + equity_curve + monthly_returns.
    Returns ``None`` for ``None`` input so it composes with optional
    backtests.
    """
    if result is None:
        return None
    return {
        "statistics": result.get("statistics"),
        "equity_curve": result.get("equity_curve"),
        "monthly_returns": result.get("monthly_returns"),
    }


# ---------------------------------------------------------------------------
# Normal distribution helpers
#
# Inlined from ``result/statistics.py`` to keep this module free of the
# transitive NT import that ``result/__init__.py`` triggers (it imports
# ``extract.py`` which depends on nautilus_trader + pandas).  The two
# implementations MUST stay numerically equivalent — the test suite
# pins a handful of reference values (e.g. _norm_ppf(0.975) ≈ 1.96).
# ---------------------------------------------------------------------------

def _norm_ppf(p: float) -> float:
    """Approximate inverse normal CDF (percent-point function).

    Abramowitz & Stegun, formula 26.2.23.  Accurate to ~4.5e-4 for
    ``0 < p < 1``.  Pure Python — no scipy.
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
    """Standard normal CDF approximation (uses :func:`math.erf`)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# ---------------------------------------------------------------------------
# Robustness statistics (Layer 2/3)
# ---------------------------------------------------------------------------

def compute_dsr(
    best_sharpe: float | None,
    trials_data: Sequence[dict[str, Any]],
    skewness: float,
    kurtosis: float,
    n_obs: int,
) -> float | None:
    """Deflated Sharpe Ratio — Bailey & López de Prado (2014).

    Adjusts the in-sample Sharpe of the best trial by the multiple-testing
    inflation implied by all completed trials.  Only meaningful when the
    fitness objective is ``"sharpe"``; the optimizer guards that.

    Returns ``None`` when there isn't enough data (``< 5`` valid trials,
    ``< 5`` return observations, ``best_sharpe is None``) or when the
    denominator under the square root would be non-positive (degenerate
    skew/kurtosis combination).

    The trial filter is :func:`filter_completed_trials` — DO NOT inline
    a private predicate here, that defeats the whole point of the helper.
    """
    valid = filter_completed_trials(trials_data)
    valid_values = [t["value"] for t in valid]
    n_trials = len(valid_values)
    if n_trials < 5 or n_obs < 5 or best_sharpe is None:
        return None

    sqrt_year = math.sqrt(TRADING_DAYS_PER_YEAR)

    # De-annualize trial Sharpe values (they're stored as annualized).
    sr_values = [v / sqrt_year for v in valid_values]
    sr_mean = sum(sr_values) / len(sr_values)
    sr_var = sum((s - sr_mean) ** 2 for s in sr_values) / (len(sr_values) - 1)
    if sr_var <= 0:
        return None

    gamma = 0.5772156649  # Euler-Mascheroni
    e_val = math.e

    # Expected maximum SR under the null (Bailey-López de Prado 2014, eq 6).
    sr_max_star = math.sqrt(sr_var) * (
        (1 - gamma) * _norm_ppf(1 - 1 / n_trials)
        + gamma * _norm_ppf(1 - 1 / (n_trials * e_val))
    )

    daily_sr = best_sharpe / sqrt_year
    denom_sq = 1 - skewness * daily_sr + ((kurtosis - 1) / 4) * daily_sr * daily_sr
    if denom_sq <= 0:
        return None
    z = (daily_sr - sr_max_star) * math.sqrt(n_obs - 1) / math.sqrt(denom_sq)
    return round(_norm_cdf(z), 4)


def compute_param_sensitivity(
    trials_data: Sequence[dict[str, Any]],
    param_ranges: dict[str, dict[str, Any]],
    param_importances: dict[str, float],
    n_bins: int = 10,
    max_pairs: int = 3,
) -> dict[str, Any] | None:
    """Bin trial parameters and compute mean fitness per bin.

    Returns ``None`` when fewer than 10 trials qualify (binning is
    statistically meaningless below that threshold).  Otherwise returns
    a dict with two sections:

    - ``single_param`` — ``{param_name: {bins: [centers], values: [means]}}``
      for every param with at least 10 valid samples.
    - ``grid`` — ``{ "<pa>__<pb>": {x_bins, y_bins, values, x_label, y_label} }``
      for the top ``max_pairs`` parameter pairs ranked by ``param_importances``.
    """
    import numpy as np  # local import keeps module headerless when unused

    valid_trials = filter_completed_trials(trials_data)
    if len(valid_trials) < 10:
        return None

    param_names = list(param_ranges.keys())

    single_param: dict[str, Any] = {}
    for pname in param_names:
        values = []
        fitnesses = []
        for t in valid_trials:
            if pname in t.get("params", {}):
                values.append(t["params"][pname])
                fitnesses.append(t["value"])
        if len(values) < 10:
            continue
        arr_v = np.array(values, dtype=np.float64)
        arr_f = np.array(fitnesses, dtype=np.float64)
        bin_edges = np.unique(np.percentile(arr_v, np.linspace(0, 100, n_bins + 1)))
        if len(bin_edges) < 2:
            continue
        bin_indices = np.digitize(arr_v, bin_edges[1:-1])
        bin_means: list[float] = []
        bin_centers: list[float] = []
        for bi in range(len(bin_edges) - 1):
            mask = bin_indices == bi
            if mask.any():
                bin_means.append(round(float(arr_f[mask].mean()), 4))
                bin_centers.append(
                    round(float((bin_edges[bi] + bin_edges[bi + 1]) / 2), 6)
                )
        if bin_centers:
            single_param[pname] = {"bins": bin_centers, "values": bin_means}

    grid: dict[str, Any] = {}
    sorted_params = sorted(
        [p for p in param_importances if p in param_names],
        key=lambda k: param_importances.get(k, 0),
        reverse=True,
    )
    top_params = sorted_params[:4]
    pairs_done = 0
    for i, pa in enumerate(top_params):
        for pb in top_params[i + 1:]:
            if pairs_done >= max_pairs:
                break
            va, vb, vf = [], [], []
            for t in valid_trials:
                p = t.get("params", {})
                if pa in p and pb in p:
                    va.append(p[pa])
                    vb.append(p[pb])
                    vf.append(t["value"])
            if len(va) < 10:
                continue
            arr_a = np.array(va, dtype=np.float64)
            arr_b = np.array(vb, dtype=np.float64)
            arr_ff = np.array(vf, dtype=np.float64)
            edges_a = np.unique(np.percentile(arr_a, np.linspace(0, 100, n_bins + 1)))
            edges_b = np.unique(np.percentile(arr_b, np.linspace(0, 100, n_bins + 1)))
            if len(edges_a) < 2 or len(edges_b) < 2:
                continue
            idx_a = np.digitize(arr_a, edges_a[1:-1])
            idx_b = np.digitize(arr_b, edges_b[1:-1])
            na, nb = len(edges_a) - 1, len(edges_b) - 1
            grid_vals: list[list[float | None]] = [[None] * nb for _ in range(na)]
            for ai in range(na):
                for bi_idx in range(nb):
                    mask = (idx_a == ai) & (idx_b == bi_idx)
                    if mask.any():
                        grid_vals[ai][bi_idx] = round(float(arr_ff[mask].mean()), 4)
            key = f"{pa}__{pb}"
            grid[key] = {
                "x_bins": [
                    round(float((edges_a[j] + edges_a[j + 1]) / 2), 6)
                    for j in range(na)
                ],
                "y_bins": [
                    round(float((edges_b[j] + edges_b[j + 1]) / 2), 6)
                    for j in range(nb)
                ],
                "values": grid_vals,
                "x_label": pa,
                "y_label": pb,
            }
            pairs_done += 1
        if pairs_done >= max_pairs:
            break

    return {"single_param": single_param, "grid": grid}


def compute_param_stability(
    trials_data: Sequence[dict[str, Any]],
    best_params: dict[str, Any],
    threshold: float = 0.20,
) -> float | None:
    """Std of fitness for trials within ``±threshold`` of ``best_params``.

    Lower = more stable (fitness doesn't change much in the neighborhood
    of the optimum, so the optimum is robust to parameter perturbation).
    Returns ``None`` when ``best_params`` is empty or fewer than 3 trials
    fall inside the neighborhood (sample too small to compute std).
    """
    if not best_params:
        return None

    valid_trials = filter_completed_trials(trials_data)
    nearby: list[float] = []
    for t in valid_trials:
        params = t.get("params", {})
        is_near = True
        for k, bv in best_params.items():
            if k not in params:
                is_near = False
                break
            if abs(params[k] - bv) / max(abs(bv), 1e-9) > threshold:
                is_near = False
                break
        if is_near:
            nearby.append(t["value"])

    if len(nearby) < 3:
        return None
    mean_v = sum(nearby) / len(nearby)
    variance = sum((v - mean_v) ** 2 for v in nearby) / (len(nearby) - 1)
    return round(variance ** 0.5, 4)
