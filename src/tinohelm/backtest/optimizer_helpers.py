"""Pure helpers for :class:`tinohelm.backtest.optimizer.BacktestOptimizer`.

Everything in this module is Optuna-free, NautilusTrader-free, and
side-effect-free — arithmetic, date arithmetic, dict shaping, and NumPy
vectorisation.  By living outside ``optimizer.py`` these helpers can be
unit-tested without Optuna or the NT wheel installed (valuable in
constrained CI environments).

Keep this module pure:  no I/O, no logging, no Optuna/NT imports.

Functions that depend on Optuna (samplers, pruners, trial-level suggestions)
or NautilusTrader (engine runs) remain in ``optimizer.py``.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fitness objective mapping: key -> path into result["statistics"].
FITNESS_METRICS: dict[str, str] = {
    "sharpe": "sharpe_ratio",
    "calmar": "calmar_ratio",
    "sortino": "sortino_ratio",
    "profit": "total_pnl",
}

# Sentinel value for failed / missing metrics so Optuna can continue.
# Trials returning this value are treated as "not real" by downstream
# analysis (DSR, sensitivity, stability).
FAIL_VALUE: float = -999.0

# Trading days per year — used to de-annualise the Sharpe ratio before
# plugging it into DSR / PSR formulas.
TRADING_DAYS_PER_YEAR: int = 252

# Minimum valid trials required before DSR becomes meaningful.
DSR_MIN_TRIALS: int = 5

# Minimum observations (daily returns) required before DSR is valid.
DSR_MIN_OBSERVATIONS: int = 5

# Minimum trials required before parameter sensitivity is computed.
SENSITIVITY_MIN_TRIALS: int = 10

# Minimum nearby trials required before parameter stability is computed.
STABILITY_MIN_NEARBY: int = 3

# Default "near best" threshold (fraction) for stability score.
STABILITY_DEFAULT_THRESHOLD: float = 0.20

# Epsilon for stability denominator — prevents div-by-zero when best_param
# is 0.  Kept at 1e-9 to match the original inline implementation.
STABILITY_EPSILON: float = 1e-9


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def split_dates(
    start_date: date, end_date: date, train_pct: float,
) -> tuple[date, date, date, date]:
    """Split a date range into train and test periods.

    Returns ``(train_start, train_end, test_start, test_end)``.  The test
    period starts one day after ``train_end`` (a 1-day gap is intentional
    — mirroring the original behaviour so train/test do not overlap).
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
    """Generate rolling walk-forward windows.

    Each fold has the same train-to-test ratio determined by *train_pct*.
    Fold *i*'s test segment ends where fold *i+1*'s test segment begins,
    so fold test segments tile the right-hand portion of the date range.

    Returns a list of ``(train_start, train_end, test_start, test_end)``
    tuples.  Falls back to a single :func:`split_dates` window when
    ``train_pct`` is non-fractional or ``n_folds`` is non-positive.
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
    """Extract the fitness metric from a backtest result dict.

    Returns :data:`FAIL_VALUE` when the objective is unknown, the metric
    is absent, ``None``, or cannot be coerced to ``float``.
    """
    metric_key = FITNESS_METRICS.get(objective)
    if metric_key is None:
        return FAIL_VALUE
    stats = result.get("statistics", {}) if isinstance(result, dict) else {}
    if not isinstance(stats, dict):
        return FAIL_VALUE
    value = stats.get(metric_key)
    if value is None:
        return FAIL_VALUE
    try:
        return float(value)
    except (TypeError, ValueError):
        return FAIL_VALUE


# ---------------------------------------------------------------------------
# Smart defaults
# ---------------------------------------------------------------------------

def auto_n_trials(param_ranges: dict[str, dict[str, Any]]) -> int:
    """Choose a reasonable ``n_trials`` from search-space dimensionality.

    Zero-dim searches still get 50 trials (covers the "evaluate baseline
    many times for noise" use case).  Higher-dim searches get 20 trials
    per dimension with a 50-trial floor.
    """
    n_dims = len(param_ranges)
    if n_dims == 0:
        return 50
    return max(50, n_dims * 20)


def auto_sampler(param_ranges: dict[str, dict[str, Any]]) -> str:
    """Pick a sampler name based on parameter types and count.

    CMA-ES works best on small, continuous search spaces (≤3 dims, no
    integer parameters).  TPE is the safe default otherwise.
    """
    n_dims = len(param_ranges)
    has_int = any(p.get("type") == "int" for p in param_ranges.values())
    if n_dims <= 3 and not has_int:
        return "cmaes"
    return "tpe"


def auto_workers(cpu_count: int | None = None) -> int:
    """Select worker count.

    Defaults to ``os.cpu_count()`` when ``cpu_count`` is ``None``.
    The result is clamped to ``[1, 4]``; a 4-worker ceiling avoids
    over-committing memory-heavy backtest engines.
    """
    if cpu_count is None:
        import os

        cpu_count = os.cpu_count() or 2
    return min(4, max(1, cpu_count // 2))


# ---------------------------------------------------------------------------
# Robustness helpers (Layer 2/3 analysis)
# ---------------------------------------------------------------------------

def slim_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only fields needed for IS/OOS comparison charts."""
    if result is None:
        return None
    return {
        "statistics": result.get("statistics"),
        "equity_curve": result.get("equity_curve"),
        "monthly_returns": result.get("monthly_returns"),
    }


def filter_completed_trials(trials_data: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter to trials that completed with a real (non-sentinel) value."""
    return [
        t for t in trials_data
        if t.get("state") == "COMPLETE"
        and t.get("value") is not None
        and t["value"] != FAIL_VALUE
    ]


# ---------------------------------------------------------------------------
# Inverse normal CDF / standard normal CDF
#
# Duplicated from :mod:`tinohelm.backtest.result.statistics` (see
# ``_norm_ppf`` / ``_norm_cdf`` there) because importing from the ``result``
# package pulls in :mod:`extract`, which transitively requires NautilusTrader.
# Keeping these two 5-line formulas here preserves this module's "NT/Optuna
# free" property, which is tested by CI.  Both files use the same closed-form
# approximations (Abramowitz & Stegun 26.2.23 + ``math.erf``) so they cannot
# drift.
# ---------------------------------------------------------------------------

def _norm_ppf(p: float) -> float:
    """Approximate inverse normal CDF (percent-point function)."""
    if p <= 0 or p >= 1:
        return 0.0
    if p < 0.5:
        return -_norm_ppf(1 - p)
    t = (-2.0 * math.log(1.0 - p)) ** 0.5
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF approximation."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def compute_dsr(
    best_sharpe: float | None,
    trials_data: list[dict[str, Any]],
    skewness: float,
    kurtosis: float,
    n_obs: int,
) -> float | None:
    """Deflated Sharpe Ratio — Bailey & López de Prado (2014).

    Only valid when ``fitness_objective`` is ``"sharpe"``.  Returns
    ``None`` when there are too few observations/trials, when the
    trial-value variance is zero, or when the PSR denominator is
    non-positive.

    ``trials_data`` is a list of dicts with keys ``state``, ``value``.
    """
    valid_values = [t["value"] for t in filter_completed_trials(trials_data)]
    n_trials = len(valid_values)
    if (
        n_trials < DSR_MIN_TRIALS
        or n_obs < DSR_MIN_OBSERVATIONS
        or best_sharpe is None
    ):
        return None

    # De-annualise trial Sharpe values (stored as annualised).
    annualisation = math.sqrt(TRADING_DAYS_PER_YEAR)
    sr_values = [v / annualisation for v in valid_values]
    sr_mean = sum(sr_values) / len(sr_values)
    sr_var = sum((s - sr_mean) ** 2 for s in sr_values) / (len(sr_values) - 1)
    if sr_var <= 0:
        return None

    gamma = 0.5772156649  # Euler-Mascheroni constant
    e_val = math.e

    # Expected maximum SR under null.
    sr_max_star = math.sqrt(sr_var) * (
        (1 - gamma) * _norm_ppf(1 - 1 / n_trials)
        + gamma * _norm_ppf(1 - 1 / (n_trials * e_val))
    )

    # DSR = PSR with ``sr_max_star`` as benchmark.
    daily_sr = best_sharpe / annualisation  # de-annualise best
    denom_sq = 1 - skewness * daily_sr + ((kurtosis - 1) / 4) * daily_sr * daily_sr
    if denom_sq <= 0:
        return None
    z = (daily_sr - sr_max_star) * math.sqrt(n_obs - 1) / math.sqrt(denom_sq)
    return round(_norm_cdf(z), 4)


def compute_param_sensitivity(
    trials_data: list[dict[str, Any]],
    param_ranges: dict[str, dict[str, Any]],
    param_importances: dict[str, float],
    n_bins: int = 10,
    max_pairs: int = 3,
) -> dict[str, Any] | None:
    """Bin trial params and compute mean fitness per bin.

    * ``single_param``: quantile-binned histogram per parameter.
    * ``grid``: 2-D heatmap for the top parameter pairs by importance.

    Returns ``None`` when there are fewer than
    :data:`SENSITIVITY_MIN_TRIALS` completed trials.
    """
    import numpy as np  # local: lets callers skip numpy when sensitivity is unused

    valid_trials = filter_completed_trials(trials_data)
    if len(valid_trials) < SENSITIVITY_MIN_TRIALS:
        return None

    param_names = list(param_ranges.keys())

    # --- Single param sensitivity ---
    single_param: dict[str, Any] = {}
    for pname in param_names:
        values = []
        fitnesses = []
        for t in valid_trials:
            if pname in t.get("params", {}):
                values.append(t["params"][pname])
                fitnesses.append(t["value"])
        if len(values) < SENSITIVITY_MIN_TRIALS:
            continue
        arr_v = np.array(values, dtype=np.float64)
        arr_f = np.array(fitnesses, dtype=np.float64)
        bin_edges = np.unique(np.percentile(arr_v, np.linspace(0, 100, n_bins + 1)))
        if len(bin_edges) < 2:
            continue
        bin_indices = np.digitize(arr_v, bin_edges[1:-1])
        bin_means = []
        bin_centers = []
        for bi in range(len(bin_edges) - 1):
            mask = bin_indices == bi
            if mask.any():
                bin_means.append(round(float(arr_f[mask].mean()), 4))
                bin_centers.append(
                    round(float((bin_edges[bi] + bin_edges[bi + 1]) / 2), 6)
                )
        if bin_centers:
            single_param[pname] = {"bins": bin_centers, "values": bin_means}

    # --- Param pairs (top by importance) ---
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
            if len(va) < SENSITIVITY_MIN_TRIALS:
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
    trials_data: list[dict[str, Any]],
    best_params: dict[str, Any],
    threshold: float = STABILITY_DEFAULT_THRESHOLD,
) -> float | None:
    """Std-dev of fitness for trials within ``±threshold`` of best params.

    Lower std = more stable (small param perturbations do not swing the
    fitness).  Returns ``None`` when there are no best params or fewer
    than :data:`STABILITY_MIN_NEARBY` trials fall inside the threshold.
    """
    if not best_params:
        return None

    valid_trials = filter_completed_trials(trials_data)
    nearby = []
    for t in valid_trials:
        params = t.get("params", {})
        is_near = True
        for k, bv in best_params.items():
            if k not in params:
                is_near = False
                break
            if abs(params[k] - bv) / max(abs(bv), STABILITY_EPSILON) > threshold:
                is_near = False
                break
        if is_near:
            nearby.append(t["value"])

    if len(nearby) < STABILITY_MIN_NEARBY:
        return None
    mean_v = sum(nearby) / len(nearby)
    variance = sum((v - mean_v) ** 2 for v in nearby) / (len(nearby) - 1)
    return round(variance ** 0.5, 4)


__all__ = [
    # Constants
    "FITNESS_METRICS",
    "FAIL_VALUE",
    "TRADING_DAYS_PER_YEAR",
    "DSR_MIN_TRIALS",
    "DSR_MIN_OBSERVATIONS",
    "SENSITIVITY_MIN_TRIALS",
    "STABILITY_MIN_NEARBY",
    "STABILITY_DEFAULT_THRESHOLD",
    "STABILITY_EPSILON",
    # Date helpers
    "split_dates",
    "walk_forward_windows",
    # Metric extraction
    "extract_fitness",
    # Smart defaults
    "auto_n_trials",
    "auto_sampler",
    "auto_workers",
    # Result trimming / robustness
    "slim_result",
    "filter_completed_trials",
    "compute_dsr",
    "compute_param_sensitivity",
    "compute_param_stability",
]
