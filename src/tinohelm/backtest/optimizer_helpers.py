"""Pure helpers for the Optuna-based backtest optimizer.

This module deliberately avoids importing :mod:`nautilus_trader`, :mod:`optuna`,
:mod:`redis`, and :mod:`sqlalchemy`.  Everything here is plain Python (and
``numpy`` for the heavy sensitivity analysis), which makes the logic trivially
unit-testable from CI without the heavy backtesting stack.

The :class:`BacktestOptimizer` orchestrator in :mod:`tinohelm.backtest.optimizer`
re-exports the symbols it needs and stays focused on Optuna/Redis/DB plumbing.
"""
from __future__ import annotations

import math
import os
import threading
from datetime import date, timedelta
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Constants — single source of truth shared with the orchestrator.
# ---------------------------------------------------------------------------

#: Mapping from CLI-friendly objective name to ``result["statistics"]`` key.
FITNESS_METRICS: dict[str, str] = {
    "sharpe": "sharpe_ratio",
    "calmar": "calmar_ratio",
    "sortino": "sortino_ratio",
    "profit": "total_pnl",
}

#: Sentinel value for failed / missing fitness metrics so Optuna keeps trying.
FAIL_VALUE: float = -999.0

#: Trading days per calendar year — used for de-annualizing Sharpe in DSR.
_TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def split_dates(
    start_date: date, end_date: date, train_pct: float,
) -> tuple[date, date, date, date]:
    """Split a date range into ``(train_start, train_end, test_start, test_end)``.

    The split point is ``train_pct`` percent into the total span, with the
    test segment beginning the following day so the windows do not overlap.
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

    Each window has the same train-to-test ratio defined by *train_pct*.
    Windows slide forward so the test segment of fold *i* ends where fold
    *i+1*'s test segment begins.  When the inputs are degenerate (zero/negative
    folds, 100% train) we fall back to a single :func:`split_dates` window so
    callers never receive an empty list.
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
# Fitness extraction
# ---------------------------------------------------------------------------

def extract_fitness(
    result: dict[str, Any] | None,
    objective: str,
    *,
    fail_value: float = FAIL_VALUE,
) -> float:
    """Pull a fitness metric out of a backtest result dict.

    Returns *fail_value* when the metric is missing, the result is ``None``,
    the objective name is unknown, or the value cannot be coerced to ``float``.
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


# ---------------------------------------------------------------------------
# Smart defaults
# ---------------------------------------------------------------------------

def auto_n_trials(param_ranges: dict[str, dict[str, Any]]) -> int:
    """Pick a reasonable trial count proportional to search-space dimensionality."""
    n_dims = len(param_ranges)
    if n_dims == 0:
        return 50
    return max(50, n_dims * 20)


def auto_sampler(param_ranges: dict[str, dict[str, Any]]) -> str:
    """Select a sampler short name based on parameter types and count.

    CMA-ES is preferred for low-dimensional all-float spaces; TPE is the
    default for everything else (mixed types, many dims).
    """
    n_dims = len(param_ranges)
    has_int = any(p.get("type") == "int" for p in param_ranges.values())
    if n_dims <= 3 and not has_int:
        return "cmaes"
    return "tpe"


def auto_workers(cpu_count: int | None = None) -> int:
    """Pick worker count as ``min(4, max(1, cpu // 2))``.

    *cpu_count* is exposed for testing; production callers should leave it
    ``None`` to read :func:`os.cpu_count`.
    """
    if cpu_count is None:
        cpu_count = os.cpu_count() or 2
    return min(4, max(1, cpu_count // 2))


def auto_patience(n_trials: int) -> int:
    """Compute early-stopping patience for studies with ``n_trials >= 40``.

    Returns 0 when patience should not be auto-enabled.
    """
    if n_trials < 40:
        return 0
    return max(10, n_trials // 4)


# ---------------------------------------------------------------------------
# Result trimming / payload assembly
# ---------------------------------------------------------------------------

def slim_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only the fields needed for IS/OOS comparison charts.

    Returns ``None`` when the input is ``None`` so that downstream callers
    can encode "no validation backtest was attempted" cleanly.
    """
    if result is None:
        return None
    return {
        "statistics": result.get("statistics"),
        "equity_curve": result.get("equity_curve"),
        "monthly_returns": result.get("monthly_returns"),
    }


def build_progress_payload(
    *,
    optimization_id: int,
    trials_completed: int,
    total_trials: int,
    best_value: float,
    best_params: dict[str, Any],
    status: str,
    message: str | None = None,
) -> dict[str, Any]:
    """Canonical Redis publish payload for `tino:backtest:optimization:{id}`.

    All seven keys are always present so the frontend
    ``NotificationListener`` and TUI never see a missing-field shape.
    """
    return {
        "optimization_id": optimization_id,
        "trials_completed": trials_completed,
        "total_trials": total_trials,
        "best_value": best_value,
        "best_params": dict(best_params),
        "status": status,
        "message": message,
    }


def serialize_trial(trial: Any) -> dict[str, Any]:
    """Adapt an Optuna ``FrozenTrial`` (or compatible object) to a plain dict.

    Duck-typed: requires ``trial.number``, ``trial.params``, ``trial.value``,
    and ``trial.state.name`` (or ``trial.state`` already being a string).
    """
    state = getattr(trial, "state", None)
    state_name = getattr(state, "name", state)
    return {
        "number": trial.number,
        "params": dict(trial.params),
        "value": trial.value,
        "state": state_name,
    }


def filter_completed_trials(
    trials_data: Iterable[dict[str, Any]],
    *,
    fail_value: float = FAIL_VALUE,
) -> list[dict[str, Any]]:
    """Drop pruned / failed / sentinel trials so downstream stats only see signal."""
    return [
        t for t in trials_data
        if t.get("state") == "COMPLETE"
        and t.get("value") is not None
        and t["value"] != fail_value
    ]


def select_best_params(
    trial_params: dict[str, Any],
    param_ranges: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Restrict a trial's params dict to keys declared in *param_ranges*.

    Optuna may suggest auxiliary parameters in nested calls; we never want
    to leak those into the persisted "best params" record.
    """
    return {k: v for k, v in trial_params.items() if k in param_ranges}


def build_walk_forward_fold_record(
    *,
    fold_idx: int,
    train_start: date,
    train_end: date,
    test_start: date,
    test_end: date,
    test_value: float,
) -> dict[str, Any]:
    """Build the per-fold record published in walk-forward results."""
    return {
        "fold": fold_idx + 1,
        "train_start": train_start.isoformat(),
        "train_end": train_end.isoformat(),
        "test_start": test_start.isoformat(),
        "test_end": test_end.isoformat(),
        "test_value": test_value,
    }


def build_full_result(
    *,
    best_params: dict[str, Any],
    best_value: float,
    trials: list[dict[str, Any]],
    train_start: date,
    train_end: date,
    test_start: date,
    test_end: date,
    validation: dict[str, Any] | None,
    train_validation: dict[str, Any] | None,
    param_importances: dict[str, float],
    convergence_history: list[float],
    sampler: str,
    n_workers: int,
    pruning_enabled: bool,
    total_pruned: int,
    walk_forward_results: list[dict[str, Any]] | None = None,
    dsr: float | None = None,
    parameter_sensitivity: dict[str, Any] | None = None,
    parameter_stability_score: float | None = None,
) -> dict[str, Any]:
    """Assemble the complete `result_json` blob persisted to the DB.

    Locking the shape here turns the previously implicit frontend contract
    (16 top-level keys consumed by the optimization detail UI) into an
    explicit, testable surface.  ``walk_forward_results`` is conditionally
    included only when walk-forward mode was used.
    """
    full: dict[str, Any] = {
        "best_params": dict(best_params),
        "best_value": best_value,
        "trials": list(trials),
        "train_period": {
            "start": train_start.isoformat(),
            "end": train_end.isoformat(),
        },
        "test_period": {
            "start": test_start.isoformat(),
            "end": test_end.isoformat(),
        },
        "validation": validation,
        "param_importances": dict(param_importances),
        "convergence_history": list(convergence_history),
        "sampler": sampler,
        "n_workers": n_workers,
        "pruning_enabled": pruning_enabled,
        "total_pruned": total_pruned,
        "train_validation": train_validation,
        "dsr": dsr,
        "parameter_sensitivity": parameter_sensitivity,
        "parameter_stability_score": parameter_stability_score,
    }
    if walk_forward_results is not None:
        full["walk_forward_results"] = list(walk_forward_results)
    return full


# ---------------------------------------------------------------------------
# Early-stopping state machine
# ---------------------------------------------------------------------------

class PatienceTracker:
    """Thread-safe "no-improvement counter" for early stopping.

    Decoupled from Optuna types so it can be unit-tested without the optional
    dependency.  The Optuna callback in :mod:`tinohelm.backtest.optimizer`
    is a thin shim that calls :meth:`observe`.
    """

    def __init__(self, patience: int) -> None:
        self._patience = patience
        self._best: float = -math.inf
        self._no_improve_count: int = 0
        self._lock = threading.Lock()

    @property
    def best(self) -> float:
        return self._best

    @property
    def no_improve_count(self) -> int:
        return self._no_improve_count

    def observe(self, value: float | None) -> bool:
        """Record the latest trial's value and return ``True`` if we should stop.

        ``None`` (pruned trials) counts as a non-improving trial.
        """
        with self._lock:
            if value is not None and value > self._best:
                self._best = value
                self._no_improve_count = 0
            else:
                self._no_improve_count += 1
            return self._no_improve_count >= self._patience


# ---------------------------------------------------------------------------
# Statistics — Deflated Sharpe Ratio (Bailey & López de Prado, 2014)
# ---------------------------------------------------------------------------

def compute_dsr(
    *,
    best_sharpe: float | None,
    trials_data: list[dict[str, Any]],
    skewness: float,
    kurtosis: float,
    n_obs: int,
    norm_ppf: Any,
    norm_cdf: Any,
    fail_value: float = FAIL_VALUE,
) -> float | None:
    """Deflated Sharpe Ratio (only valid for Sharpe-objective optimizations).

    Returns ``None`` when there are too few completed trials or observations,
    when the cross-trial Sharpe variance is non-positive, or when the
    denominator under the radical degenerates (``1 - skew*sr + ...`` ≤ 0).

    The two normal-distribution helpers are injected to keep this module
    free of the heavyweight :mod:`tinohelm.backtest.result.statistics` import.
    """
    valid_values = [
        t["value"] for t in trials_data
        if t.get("state") == "COMPLETE"
        and t.get("value") is not None
        and t["value"] != fail_value
    ]
    n_trials = len(valid_values)
    if n_trials < 5 or n_obs < 5 or best_sharpe is None:
        return None

    sr_values = [v / math.sqrt(_TRADING_DAYS) for v in valid_values]
    sr_mean = sum(sr_values) / len(sr_values)
    sr_var = sum((s - sr_mean) ** 2 for s in sr_values) / (len(sr_values) - 1)
    if sr_var <= 0:
        return None

    gamma = 0.5772156649  # Euler-Mascheroni constant
    e_val = math.e
    sr_max_star = math.sqrt(sr_var) * (
        (1 - gamma) * norm_ppf(1 - 1 / n_trials)
        + gamma * norm_ppf(1 - 1 / (n_trials * e_val))
    )

    daily_sr = best_sharpe / math.sqrt(_TRADING_DAYS)
    denom_sq = 1 - skewness * daily_sr + ((kurtosis - 1) / 4) * daily_sr * daily_sr
    if denom_sq <= 0:
        return None
    z = (daily_sr - sr_max_star) * math.sqrt(n_obs - 1) / math.sqrt(denom_sq)
    return round(norm_cdf(z), 4)


# ---------------------------------------------------------------------------
# Statistics — Parameter sensitivity / stability
# ---------------------------------------------------------------------------

def compute_param_sensitivity(
    trials_data: list[dict[str, Any]],
    param_ranges: dict[str, dict[str, Any]],
    param_importances: dict[str, float],
    *,
    n_bins: int = 10,
    max_pairs: int = 3,
    min_trials: int = 10,
    fail_value: float = FAIL_VALUE,
) -> dict[str, Any] | None:
    """Bin trial params and compute mean fitness per bin.

    Returns a dict with two keys:

    * ``single_param`` — histogram of mean fitness per bin for each param
    * ``grid``         — 2D heatmap for the top *max_pairs* importance-ranked
                         parameter pairs

    ``None`` is returned when fewer than *min_trials* completed trials are
    available, since the binning would be statistically meaningless.
    """
    import numpy as np

    valid_trials = filter_completed_trials(trials_data, fail_value=fail_value)
    if len(valid_trials) < min_trials:
        return None

    param_names = list(param_ranges.keys())

    single_param: dict[str, Any] = {}
    for pname in param_names:
        values, fitnesses = [], []
        for t in valid_trials:
            if pname in t.get("params", {}):
                values.append(t["params"][pname])
                fitnesses.append(t["value"])
        if len(values) < min_trials:
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
                bin_centers.append(round(float((bin_edges[bi] + bin_edges[bi + 1]) / 2), 6))
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
        if pairs_done >= max_pairs:
            break
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
            if len(va) < min_trials:
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
            grid[f"{pa}__{pb}"] = {
                "x_bins": [round(float((edges_a[j] + edges_a[j + 1]) / 2), 6) for j in range(na)],
                "y_bins": [round(float((edges_b[j] + edges_b[j + 1]) / 2), 6) for j in range(nb)],
                "values": grid_vals,
                "x_label": pa,
                "y_label": pb,
            }
            pairs_done += 1

    return {"single_param": single_param, "grid": grid}


def compute_param_stability(
    trials_data: list[dict[str, Any]],
    best_params: dict[str, Any],
    *,
    threshold: float = 0.20,
    min_neighbors: int = 3,
    fail_value: float = FAIL_VALUE,
) -> float | None:
    """Std-dev of fitness for trials within ±*threshold* of *best_params*.

    A lower value means the optimum is on a flat plateau (less risk of
    parameter-overfitting); higher means the surface is jagged near the
    chosen optimum.  Returns ``None`` when there are too few neighbors.
    """
    if not best_params:
        return None
    valid_trials = filter_completed_trials(trials_data, fail_value=fail_value)
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

    if len(nearby) < min_neighbors:
        return None
    mean_v = sum(nearby) / len(nearby)
    variance = sum((v - mean_v) ** 2 for v in nearby) / (len(nearby) - 1)
    return round(variance ** 0.5, 4)


__all__ = [
    "FITNESS_METRICS",
    "FAIL_VALUE",
    "split_dates",
    "walk_forward_windows",
    "extract_fitness",
    "auto_n_trials",
    "auto_sampler",
    "auto_workers",
    "auto_patience",
    "slim_result",
    "build_progress_payload",
    "serialize_trial",
    "filter_completed_trials",
    "select_best_params",
    "build_walk_forward_fold_record",
    "build_full_result",
    "PatienceTracker",
    "compute_dsr",
    "compute_param_sensitivity",
    "compute_param_stability",
]
