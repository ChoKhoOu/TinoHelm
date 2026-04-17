"""Pure helpers for :mod:`tinohelm.backtest.optimizer`.

Everything in this module is deliberately free of NautilusTrader **and**
Optuna dependencies so the helpers can be unit-tested in a lean Python
environment (just the ``scipy``/``numpy`` wheel is required for the
parameter sensitivity grid).

The main :class:`tinohelm.backtest.optimizer.BacktestOptimizer` class keeps
the Optuna-specific orchestration (sampler factory, study loop, trial
callbacks); this module owns the deterministic, side-effect-free numeric
and data-shaping logic that sits next to it.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from tinohelm.backtest._math_primitives import norm_cdf, norm_ppf

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

#: Mapping of fitness objective short-name to the corresponding
#: ``result["statistics"]`` key.  Exposed as a public constant so the
#: Optuna objective code and downstream tests can refer to the same
#: single source of truth.
FITNESS_METRICS: dict[str, str] = {
    "sharpe": "sharpe_ratio",
    "calmar": "calmar_ratio",
    "sortino": "sortino_ratio",
    "profit": "total_pnl",
}

#: Sentinel fitness value returned for failed / missing metrics so Optuna's
#: ``direction="maximize"`` keeps making progress instead of aborting.
FAIL_VALUE: float = -999.0

#: Euler-Mascheroni constant, used for the expected-maximum-Sharpe term in
#: the Deflated Sharpe Ratio (Bailey & López de Prado, 2014).
_EULER_MASCHERONI: float = 0.5772156649

#: Trading days per year — used to de-annualize Sharpe ratios when
#: computing the DSR.  Matches the convention in ``backtest.result``.
_TRADING_DAYS_PER_YEAR: int = 252

#: Objectives that produce an annualized Sharpe value eligible for DSR.
DSR_COMPATIBLE_OBJECTIVES: frozenset[str] = frozenset({"sharpe"})


# ---------------------------------------------------------------------------
# Smart defaults
# ---------------------------------------------------------------------------

def auto_n_trials(param_ranges: Mapping[str, Mapping[str, Any]]) -> int:
    """Return a reasonable default ``n_trials`` for the given search space.

    Floor of ``50`` trials; above 2-3 dimensions we grow roughly linearly
    so TPE has a chance to map the landscape.
    """
    n_dims = len(param_ranges)
    if n_dims == 0:
        return 50
    return max(50, n_dims * 20)


def auto_sampler(param_ranges: Mapping[str, Mapping[str, Any]]) -> str:
    """Pick a sampler name based on search-space shape.

    CMA-ES is only appropriate for low-dimensional **continuous** spaces;
    integer parameters or >3 dimensions fall back to TPE.
    """
    n_dims = len(param_ranges)
    has_int = any(p.get("type") == "int" for p in param_ranges.values())
    if n_dims <= 3 and not has_int:
        return "cmaes"
    return "tpe"


def auto_workers(cpu_count: int | None = None) -> int:
    """Select a default worker pool size based on CPU count.

    ``cpu_count`` is injected for testability; the real caller passes
    ``None`` which triggers ``os.cpu_count()``.  Result is clamped to
    ``[1, 4]`` to avoid oversubscription on shared hosts.
    """
    if cpu_count is None:
        cpu_count = os.cpu_count() or 2
    return min(4, max(1, cpu_count // 2))


def auto_patience(n_trials: int, min_patience: int = 10, divisor: int = 4) -> int:
    """Return a sensible early-stopping patience for the given ``n_trials``.

    ``0`` means patience is disabled (mirrors the old CLI default).  The
    auto threshold is lifted above ``min_patience`` so very small studies
    don't stop on noise.
    """
    if n_trials < 40:
        return 0
    return max(min_patience, n_trials // divisor)


# ---------------------------------------------------------------------------
# Date / window helpers
# ---------------------------------------------------------------------------

def split_dates(
    start_date: date,
    end_date: date,
    train_pct: float,
) -> tuple[date, date, date, date]:
    """Split ``[start_date, end_date]`` into train and test ranges.

    Returns ``(train_start, train_end, test_start, test_end)``.  The
    train segment occupies ``train_pct`` percent of the calendar days,
    rounded down.  ``test_start`` is always ``train_end + 1 day`` so the
    two segments are disjoint.

    The returned ``train_start`` is identical to the input ``start_date``
    — callers that need the canonical 4-tuple should not pass the first
    element back to this function.
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
    """Generate rolling walk-forward ``(train, test)`` windows.

    Each window has the same train-to-test ratio defined by ``train_pct``.
    Windows slide forward so that the test segment of fold ``i`` ends where
    fold ``i+1``'s test segment begins.  A degenerate configuration
    (``train_pct >= 100``, ``n_folds <= 0``, or clamped-away windows)
    falls back to a single :func:`split_dates` window.

    Returns ``list[tuple[train_start, train_end, test_start, test_end]]``.
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

        # Clamp to data boundaries
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


def build_wf_fold_result(
    fold_index: int,
    train_start: date,
    train_end: date,
    test_start: date,
    test_end: date,
    test_value: float,
) -> dict[str, Any]:
    """Build the JSON-safe ``walk_forward_results[i]`` entry.

    ``fold_index`` is the 0-based position inside ``walk_forward_windows``;
    the emitted ``fold`` key is 1-based to match user-facing numbering.
    """
    return {
        "fold": fold_index + 1,
        "train_start": train_start.isoformat(),
        "train_end": train_end.isoformat(),
        "test_start": test_start.isoformat(),
        "test_end": test_end.isoformat(),
        "test_value": test_value,
    }


# ---------------------------------------------------------------------------
# Metric extraction & trial filtering
# ---------------------------------------------------------------------------

def extract_fitness(
    result: Mapping[str, Any] | None,
    objective: str,
    *,
    fail_value: float = FAIL_VALUE,
) -> float:
    """Pull the fitness metric from a backtest result dict.

    Unknown objective, missing statistics, ``None`` value, or a value
    that can't be floated all return ``fail_value`` (default
    :data:`FAIL_VALUE`) so the Optuna loop can continue.
    """
    if result is None:
        return fail_value
    metric_key = FITNESS_METRICS.get(objective)
    if metric_key is None:
        return fail_value
    stats = result.get("statistics") or {}
    value = stats.get(metric_key)
    if value is None:
        return fail_value
    try:
        return float(value)
    except (TypeError, ValueError):
        return fail_value


def is_valid_trial(
    trial: Mapping[str, Any],
    *,
    fail_value: float = FAIL_VALUE,
) -> bool:
    """Return True when ``trial`` is usable for downstream aggregation.

    A trial is valid when its state is ``"COMPLETE"``, ``value`` is not
    ``None``, and ``value`` is not the failure sentinel.  This is the
    single source of truth previously duplicated three times in
    ``optimizer.py`` (DSR, sensitivity, stability).
    """
    if trial.get("state") != "COMPLETE":
        return False
    value = trial.get("value")
    if value is None:
        return False
    return value != fail_value


def filter_valid_trials(
    trials: Iterable[Mapping[str, Any]],
    *,
    fail_value: float = FAIL_VALUE,
) -> list[Mapping[str, Any]]:
    """Return the subset of ``trials`` satisfying :func:`is_valid_trial`."""
    return [t for t in trials if is_valid_trial(t, fail_value=fail_value)]


# ---------------------------------------------------------------------------
# Result shaping
# ---------------------------------------------------------------------------

def slim_result(result: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Keep only fields needed for IS/OOS comparison charts.

    Used by the optimizer to trim the in-sample (train) validation
    payload before persisting it — we store three keys instead of the
    full 33-key schema returned by ``extract_backtest_results``.  Returns
    ``None`` unchanged when input is ``None``.
    """
    if result is None:
        return None
    return {
        "statistics": result.get("statistics"),
        "equity_curve": result.get("equity_curve"),
        "monthly_returns": result.get("monthly_returns"),
    }


# ---------------------------------------------------------------------------
# Redis event payloads
# ---------------------------------------------------------------------------

def build_progress_event(
    optimization_id: int,
    *,
    trials_completed: int,
    total_trials: int,
    best_value: float,
    best_params: Mapping[str, Any],
    status: str = "running",
) -> dict[str, Any]:
    """Canonical JSON-safe payload published to the optimization channel.

    Consolidates the two inline ``json.dumps({...})`` literals previously
    present in the run-loop (``running`` and ``completed`` branches) so
    the key set is guaranteed identical.  Frontend listeners can count on
    the 6 keys below on every event.
    """
    return {
        "optimization_id": optimization_id,
        "trials_completed": trials_completed,
        "total_trials": total_trials,
        "best_value": best_value,
        "best_params": dict(best_params),
        "status": status,
    }


# ---------------------------------------------------------------------------
# Deflated Sharpe Ratio
# ---------------------------------------------------------------------------

def compute_dsr(
    best_sharpe: float | None,
    trials_data: Sequence[Mapping[str, Any]],
    skewness: float,
    kurtosis: float,
    n_obs: int,
    *,
    fail_value: float = FAIL_VALUE,
    trading_days: int = _TRADING_DAYS_PER_YEAR,
) -> float | None:
    """Deflated Sharpe Ratio — Bailey & López de Prado (2014).

    Only produces a value when the underlying objective is an annualized
    Sharpe (callers gate this via :data:`DSR_COMPATIBLE_OBJECTIVES`).
    Returns ``None`` when insufficient data (<5 completed trials or
    <5 observation days) or when the denominator becomes non-positive
    (heavy-tailed degenerate case).

    The trial Sharpe values are assumed annualized (matching how the
    Optuna objective reports them) and are de-annualized here by dividing
    by ``sqrt(trading_days)``.
    """
    valid = filter_valid_trials(trials_data, fail_value=fail_value)
    n_trials = len(valid)
    if n_trials < 5 or n_obs < 5 or best_sharpe is None:
        return None

    sqrt_t = math.sqrt(trading_days)
    sr_values = [float(t["value"]) / sqrt_t for t in valid]
    sr_mean = sum(sr_values) / len(sr_values)
    sr_var = sum((s - sr_mean) ** 2 for s in sr_values) / (len(sr_values) - 1)
    if sr_var <= 0:
        return None

    # Expected maximum SR under null (Bailey & López de Prado eq. 15)
    sr_max_star = math.sqrt(sr_var) * (
        (1 - _EULER_MASCHERONI) * norm_ppf(1 - 1 / n_trials)
        + _EULER_MASCHERONI * norm_ppf(1 - 1 / (n_trials * math.e))
    )

    daily_sr = best_sharpe / sqrt_t
    denom_sq = 1 - skewness * daily_sr + ((kurtosis - 1) / 4) * daily_sr * daily_sr
    if denom_sq <= 0:
        return None

    z = (daily_sr - sr_max_star) * math.sqrt(n_obs - 1) / math.sqrt(denom_sq)
    return round(norm_cdf(z), 4)


# ---------------------------------------------------------------------------
# Parameter sensitivity (single-param histograms + pair grids)
# ---------------------------------------------------------------------------

def _quantile_bin_edges(values: np.ndarray, n_bins: int) -> np.ndarray:
    """Return the unique quantile boundaries for ``values`` with ``n_bins``.

    Degenerate arrays (constant values, fewer than 2 samples) yield a
    trivial 1-element array — callers must check ``len(edges) >= 2``.
    """
    return np.unique(np.percentile(values, np.linspace(0, 100, n_bins + 1)))


def _digitize_inside(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Bin values using the inner edges only (left-inclusive, right-exclusive).

    We drop the outer edges because ``np.digitize`` treats values equal to
    the rightmost edge as a *new* bucket; feeding only the inner edges
    keeps every sample inside the existing ``len(edges) - 1`` buckets.
    """
    return np.digitize(values, edges[1:-1])


def _bin_mean_histogram(
    values: np.ndarray,
    fitnesses: np.ndarray,
    n_bins: int,
    *,
    bin_round: int = 6,
    value_round: int = 4,
) -> tuple[list[float], list[float]] | None:
    """Return ``(bin_centers, bin_means)`` lists or ``None`` when degenerate.

    Produced by quantile-binning ``values`` into at most ``n_bins`` bins
    and averaging the corresponding ``fitnesses`` inside each bin.  Empty
    bins are dropped (so the output lists may be shorter than ``n_bins``).
    """
    edges = _quantile_bin_edges(values, n_bins)
    if len(edges) < 2:
        return None
    indices = _digitize_inside(values, edges)
    bin_centers: list[float] = []
    bin_means: list[float] = []
    for bi in range(len(edges) - 1):
        mask = indices == bi
        if mask.any():
            bin_centers.append(round(float((edges[bi] + edges[bi + 1]) / 2), bin_round))
            bin_means.append(round(float(fitnesses[mask].mean()), value_round))
    if not bin_centers:
        return None
    return bin_centers, bin_means


def _pair_grid(
    values_a: np.ndarray,
    values_b: np.ndarray,
    fitnesses: np.ndarray,
    n_bins: int,
    *,
    bin_round: int = 6,
    value_round: int = 4,
) -> dict[str, Any] | None:
    """Build a 2D heatmap payload for the ``(pa, pb)`` parameter pair.

    Returns ``None`` when either axis is degenerate.  The ``values`` grid
    is row-major ``[ai][bi]`` with ``None`` entries for empty buckets.
    """
    edges_a = _quantile_bin_edges(values_a, n_bins)
    edges_b = _quantile_bin_edges(values_b, n_bins)
    if len(edges_a) < 2 or len(edges_b) < 2:
        return None
    idx_a = _digitize_inside(values_a, edges_a)
    idx_b = _digitize_inside(values_b, edges_b)
    na, nb = len(edges_a) - 1, len(edges_b) - 1
    grid_vals: list[list[float | None]] = [[None] * nb for _ in range(na)]
    for ai in range(na):
        for bi in range(nb):
            mask = (idx_a == ai) & (idx_b == bi)
            if mask.any():
                grid_vals[ai][bi] = round(float(fitnesses[mask].mean()), value_round)
    x_bins = [
        round(float((edges_a[j] + edges_a[j + 1]) / 2), bin_round)
        for j in range(na)
    ]
    y_bins = [
        round(float((edges_b[j] + edges_b[j + 1]) / 2), bin_round)
        for j in range(nb)
    ]
    return {"x_bins": x_bins, "y_bins": y_bins, "values": grid_vals}


def compute_param_sensitivity(
    trials_data: Sequence[Mapping[str, Any]],
    param_ranges: Mapping[str, Mapping[str, Any]],
    param_importances: Mapping[str, float],
    *,
    n_bins: int = 10,
    max_pairs: int = 3,
    min_trials: int = 10,
    fail_value: float = FAIL_VALUE,
) -> dict[str, Any] | None:
    """Quantile-bin trial parameters and average fitness per bin.

    Returns a payload with two keys:

    * ``single_param`` — per-parameter histograms ``{name: {bins, values}}``
    * ``grid`` — up to ``max_pairs`` 2D heatmaps ``{"pa__pb": {...}}`` for
      the parameter pairs with highest importance

    Returns ``None`` when fewer than ``min_trials`` completed trials are
    available — the aggregates would be too noisy to be useful.
    """
    valid_trials = filter_valid_trials(trials_data, fail_value=fail_value)
    if len(valid_trials) < min_trials:
        return None

    param_names = list(param_ranges.keys())

    # --- Single-param sensitivity ---
    single_param: dict[str, Any] = {}
    for pname in param_names:
        values: list[float] = []
        fitnesses: list[float] = []
        for t in valid_trials:
            params = t.get("params", {}) or {}
            if pname in params:
                values.append(params[pname])
                fitnesses.append(t["value"])
        if len(values) < min_trials:
            continue
        hist = _bin_mean_histogram(
            np.array(values, dtype=np.float64),
            np.array(fitnesses, dtype=np.float64),
            n_bins,
        )
        if hist is None:
            continue
        bin_centers, bin_means = hist
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
            va: list[float] = []
            vb: list[float] = []
            vf: list[float] = []
            for t in valid_trials:
                p = t.get("params", {}) or {}
                if pa in p and pb in p:
                    va.append(p[pa])
                    vb.append(p[pb])
                    vf.append(t["value"])
            if len(va) < min_trials:
                continue
            pair = _pair_grid(
                np.array(va, dtype=np.float64),
                np.array(vb, dtype=np.float64),
                np.array(vf, dtype=np.float64),
                n_bins,
            )
            if pair is None:
                continue
            grid[f"{pa}__{pb}"] = {**pair, "x_label": pa, "y_label": pb}
            pairs_done += 1
        if pairs_done >= max_pairs:
            break

    return {"single_param": single_param, "grid": grid}


# ---------------------------------------------------------------------------
# Parameter stability
# ---------------------------------------------------------------------------

def compute_param_stability(
    trials_data: Sequence[Mapping[str, Any]],
    best_params: Mapping[str, Any] | None,
    *,
    threshold: float = 0.20,
    min_neighbours: int = 3,
    fail_value: float = FAIL_VALUE,
) -> float | None:
    """Standard deviation of fitness for trials near ``best_params``.

    "Near" means every numeric parameter of the trial is within
    ``±threshold`` (relative to ``max(|best|, 1e-9)``) of the best trial's
    parameter.  The returned ``std`` is rounded to 4 decimals — lower
    means the optimum plateau is flat, i.e. the strategy is robust to
    small parameter perturbations.  Returns ``None`` when fewer than
    ``min_neighbours`` neighbouring trials exist.
    """
    if not best_params:
        return None

    valid_trials = filter_valid_trials(trials_data, fail_value=fail_value)
    nearby: list[float] = []
    for t in valid_trials:
        params = t.get("params", {}) or {}
        is_near = True
        for k, bv in best_params.items():
            if k not in params:
                is_near = False
                break
            try:
                diff = abs(params[k] - bv) / max(abs(bv), 1e-9)
            except TypeError:
                is_near = False
                break
            if diff > threshold:
                is_near = False
                break
        if is_near:
            nearby.append(t["value"])

    if len(nearby) < min_neighbours:
        return None
    mean_v = sum(nearby) / len(nearby)
    variance = sum((v - mean_v) ** 2 for v in nearby) / (len(nearby) - 1)
    return round(variance ** 0.5, 4)
