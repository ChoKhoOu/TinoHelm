"""Pure helpers for :class:`tinohelm.backtest.optimizer.BacktestOptimizer`.

Everything in this module is NautilusTrader-free **and** Optuna-free —
arithmetic, list/dict shaping, date windowing, and thread-safe bookkeeping.
By living outside ``optimizer.py`` these helpers can be unit-tested without
either the NT wheel or Optuna installed (which is valuable in constrained
CI environments, matching the pattern established by ``runner_helpers.py``).

Design rules for this module:

* **No I/O** — no Redis, no DB, no subprocess, no logging side effects.
* **No NT imports** — including transitively via ``tinohelm.backtest.result``
  (which loads NT at package level).  The DSR helper imports the statistics
  submodule lazily so the helpers module can be loaded on its own.
* **No Optuna imports** — the early-stop tracker uses plain types; the
  caller is responsible for wiring it into an Optuna callback.

``optimizer.py`` re-exports a handful of these symbols under the legacy
underscore-prefixed names (``_split_dates``, ``_FAIL_VALUE``, ...) for any
external importer that may have reached into the private API.
"""
from __future__ import annotations

import math
import os
import threading
from datetime import date, timedelta
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fitness objective mapping: short name -> key path into ``result["statistics"]``.
FITNESS_METRICS: dict[str, str] = {
    "sharpe": "sharpe_ratio",
    "calmar": "calmar_ratio",
    "sortino": "sortino_ratio",
    "profit": "total_pnl",
}

# Sentinel returned when a trial's fitness can't be computed.  Optuna treats
# it as just a very low value, which lets the study keep running instead of
# crashing on a single failed backtest.
FAIL_VALUE: float = -999.0


# ---------------------------------------------------------------------------
# Date windows
# ---------------------------------------------------------------------------

def split_dates(
    start_date: date, end_date: date, train_pct: float,
) -> tuple[date, date, date, date]:
    """Split ``[start_date, end_date]`` into contiguous train and test ranges.

    ``train_pct`` is interpreted as a percentage in [0, 100].  Training runs
    from ``start_date`` through ``train_end``; testing runs from the very
    next day through ``end_date``.  When ``train_pct == 100`` the test
    window degenerates to a single 0-width point after ``end_date``; when
    ``train_pct == 0`` the training window collapses to the single start day.
    Callers that care about non-empty windows should validate upstream.
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
    """Generate rolling walk-forward ``(tr_s, tr_e, te_s, te_e)`` windows.

    Each fold's test segment is the same fixed size; folds slide forward so
    fold ``i``'s test segment ends where fold ``i+1``'s starts.  The last
    fold always ends exactly at ``end_date``.

    Edge cases:

    * ``n_folds <= 0`` or fully-saturated training (``train_pct >= 100``) —
      returns ``[split_dates(...)]`` as a degraded single-fold fallback.
    * The first few folds may have their training window clamped to
      ``start_date`` when the requested train span extends before it.
    * Folds that would have ``train_start >= train_end`` or
      ``test_start >= test_end`` after clamping are dropped.  If clamping
      drops every candidate, falls back to a single ``split_dates`` window.
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


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------

def extract_fitness(result: dict[str, Any], objective: str) -> float:
    """Pull the fitness scalar from a backtest result dict.

    Returns :data:`FAIL_VALUE` when the objective is unknown, the
    ``statistics`` section is missing/None, or the value can't be cast to
    float.  Never raises on well-typed inputs so Optuna can keep stepping
    through trials.
    """
    metric_key = FITNESS_METRICS.get(objective)
    if metric_key is None:
        return FAIL_VALUE
    stats = result.get("statistics") or {}
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
    """Pick a reasonable trial budget from the search-space dimensionality.

    Empty search space → 50 (Optuna's conventional floor).
    Otherwise → at least 50, scaling linearly at 20× the number of params.
    """
    n_dims = len(param_ranges)
    if n_dims == 0:
        return 50
    return max(50, n_dims * 20)


def auto_sampler(param_ranges: dict[str, dict[str, Any]]) -> str:
    """Choose between ``"cmaes"`` and ``"tpe"`` based on search space shape.

    CMA-ES is only well-defined on continuous spaces and performs best in
    low-dimensional settings.  Fall back to TPE everywhere else.
    """
    n_dims = len(param_ranges)
    has_int = any(p.get("type") == "int" for p in param_ranges.values())
    if n_dims <= 3 and not has_int:
        return "cmaes"
    return "tpe"


def auto_workers(cpu_count: int | None = None) -> int:
    """Pick a worker count based on CPU cores.

    ``cpu_count`` is overridable for testability; when ``None`` we read the
    host's ``os.cpu_count()`` (falling back to 2 when that returns ``None``).
    The result is always in ``[1, 4]``: single-instance backtesting is
    memory-heavy and doesn't scale cleanly past a handful of workers.
    """
    if cpu_count is None:
        cpu_count = os.cpu_count() or 2
    return min(4, max(1, cpu_count // 2))


# ---------------------------------------------------------------------------
# Result slimming
# ---------------------------------------------------------------------------

def slim_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only the fields the frontend needs for IS/OOS comparison charts.

    Returns ``None`` when the input is ``None`` so the caller can stash it
    into a JSON column without a sentinel.
    """
    if result is None:
        return None
    return {
        "statistics": result.get("statistics"),
        "equity_curve": result.get("equity_curve"),
        "monthly_returns": result.get("monthly_returns"),
    }


# ---------------------------------------------------------------------------
# Deflated Sharpe Ratio (Bailey & López de Prado 2014)
# ---------------------------------------------------------------------------

# Euler-Mascheroni constant — used by the closed-form expected-max-SR
# formula.  Defined here so tests can reference it without reaching into
# private math module internals.
_EULER_MASCHERONI: float = 0.5772156649


def _load_norm_functions() -> tuple[Callable[[float], float], Callable[[float], float]]:
    """Return ``(norm_ppf, norm_cdf)`` from the statistics submodule.

    Prefers the normal package import path (fast, no sys.modules churn).
    Falls back to a direct file-path load when the ``result`` package
    ``__init__.py`` can't be executed — that happens in NT-free
    environments where ``extract.py``'s NT imports fail at package-load
    time.  The fallback leaves ``sys.modules`` unchanged so the rest of
    the test suite isn't affected.
    """
    try:
        from tinohelm.backtest.result.statistics import (
            _norm_cdf, _norm_ppf,
        )
        return _norm_ppf, _norm_cdf
    except Exception:
        import importlib.util
        import sys
        import types
        from pathlib import Path

        stats_path = (
            Path(__file__).resolve().parent / "result" / "statistics.py"
        )
        # Temporarily stub the parent packages so the module's relative
        # positioning resolves cleanly without executing the broken __init__.
        saved: dict[str, object] = {}
        touch_names = [
            "tinohelm",
            "tinohelm.backtest",
            "tinohelm.backtest.result",
        ]
        for n in touch_names:
            if n in sys.modules:
                saved[n] = sys.modules[n]
            else:
                saved[n] = None
                sys.modules[n] = types.ModuleType(n)
        try:
            spec = importlib.util.spec_from_file_location(
                "tinohelm.backtest.result._stats_only", stats_path,
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod._norm_ppf, mod._norm_cdf
        finally:
            for n, original in saved.items():
                if original is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = original  # type: ignore[assignment]


def compute_dsr(
    best_sharpe: float | None,
    trials_data: list[dict[str, Any]],
    skewness: float | None,
    kurtosis: float | None,
    n_obs: int,
    *,
    norm_ppf: Callable[[float], float] | None = None,
    norm_cdf: Callable[[float], float] | None = None,
) -> float | None:
    """Compute the Deflated Sharpe Ratio.

    Only meaningful when the Optuna objective is ``"sharpe"``; returns
    ``None`` whenever the inputs can't support the statistic:

    * Fewer than 5 valid (non-fail, completed) trials.
    * ``n_obs < 5`` observations (DSR requires a minimum sample).
    * ``best_sharpe is None`` — short backtests return ``None`` for Sharpe.
    * Zero or negative trial-SR variance (degenerate optimization).
    * Non-positive denominator under the square root (happens with very
      fat-tailed returns at extreme Sharpes).

    ``norm_ppf``/``norm_cdf`` default to the statistics module's
    implementations (lazy-imported to keep this helper cheap to load).
    They can be overridden in tests to avoid the import.
    """
    if norm_ppf is None or norm_cdf is None:
        default_ppf, default_cdf = _load_norm_functions()
        norm_ppf = norm_ppf or default_ppf
        norm_cdf = norm_cdf or default_cdf

    valid_values = [
        t["value"] for t in trials_data
        if t.get("state") == "COMPLETE"
        and t.get("value") is not None
        and t["value"] != FAIL_VALUE
    ]
    n_trials = len(valid_values)
    if n_trials < 5 or n_obs < 5 or best_sharpe is None:
        return None

    # De-annualize trial Sharpe values (stored as annualized).
    sr_values = [v / math.sqrt(252) for v in valid_values]
    sr_mean = sum(sr_values) / len(sr_values)
    sr_var = sum((s - sr_mean) ** 2 for s in sr_values) / (len(sr_values) - 1)
    if sr_var <= 0:
        return None

    # Expected maximum SR under the null of all trials having true SR 0.
    sr_max_star = math.sqrt(sr_var) * (
        (1 - _EULER_MASCHERONI) * norm_ppf(1 - 1 / n_trials)
        + _EULER_MASCHERONI * norm_ppf(1 - 1 / (n_trials * math.e))
    )

    # Clean skew/kurt: callers may pass None when the stats aren't available.
    skew = 0.0 if skewness is None else float(skewness)
    kurt = 0.0 if kurtosis is None else float(kurtosis)

    daily_sr = best_sharpe / math.sqrt(252)
    denom_sq = 1 - skew * daily_sr + ((kurt - 1) / 4) * daily_sr * daily_sr
    if denom_sq <= 0:
        return None

    z = (daily_sr - sr_max_star) * math.sqrt(n_obs - 1) / math.sqrt(denom_sq)
    return round(norm_cdf(z), 4)


# ---------------------------------------------------------------------------
# Parameter sensitivity & stability
# ---------------------------------------------------------------------------

def compute_param_sensitivity(
    trials_data: list[dict[str, Any]],
    param_ranges: dict[str, dict[str, Any]],
    param_importances: dict[str, float],
    n_bins: int = 10,
    max_pairs: int = 3,
) -> dict[str, Any] | None:
    """Bin trial params by value and report mean fitness per bin.

    Output shape::

        {
            "single_param": {name: {"bins": [...], "values": [...]}, ...},
            "grid":         {f"{a}__{b}": {"x_bins":..., "y_bins":...,
                                           "values":..., "x_label":...,
                                           "y_label":...}, ...},
        }

    Returns ``None`` when fewer than 10 valid trials are available (the
    histograms become too noisy to display).  Grid heatmaps are only
    produced for the top ``max_pairs * 2`` parameters by importance, and
    both axes require ≥ 10 shared observations.
    """
    import numpy as np

    valid_trials = [
        t for t in trials_data
        if t.get("state") == "COMPLETE"
        and t.get("value") is not None
        and t["value"] != FAIL_VALUE
    ]
    if len(valid_trials) < 10:
        return None

    param_names = list(param_ranges.keys())

    # --- Single param sensitivity ---
    single_param: dict[str, Any] = {}
    for pname in param_names:
        values: list[float] = []
        fitnesses: list[float] = []
        for t in valid_trials:
            if pname in t.get("params", {}):
                values.append(t["params"][pname])
                fitnesses.append(t["value"])
        if len(values) < 10:
            continue
        arr_v = np.array(values, dtype=np.float64)
        arr_f = np.array(fitnesses, dtype=np.float64)
        bin_edges = np.unique(
            np.percentile(arr_v, np.linspace(0, 100, n_bins + 1))
        )
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

    # --- Parameter pairs (top by importance) ---
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
            edges_a = np.unique(
                np.percentile(arr_a, np.linspace(0, 100, n_bins + 1))
            )
            edges_b = np.unique(
                np.percentile(arr_b, np.linspace(0, 100, n_bins + 1))
            )
            if len(edges_a) < 2 or len(edges_b) < 2:
                continue
            idx_a = np.digitize(arr_a, edges_a[1:-1])
            idx_b = np.digitize(arr_b, edges_b[1:-1])
            na, nb = len(edges_a) - 1, len(edges_b) - 1
            grid_vals: list[list[float | None]] = [
                [None] * nb for _ in range(na)
            ]
            for ai in range(na):
                for bi_idx in range(nb):
                    mask = (idx_a == ai) & (idx_b == bi_idx)
                    if mask.any():
                        grid_vals[ai][bi_idx] = round(
                            float(arr_ff[mask].mean()), 4,
                        )
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
    threshold: float = 0.20,
) -> float | None:
    """Std. dev. of fitness for trials within ``threshold`` of ``best_params``.

    Lower values mean params near the optimum produce consistent fitness
    (the surface is flat-topped → robust choice of params).  Returns
    ``None`` when:

    * ``best_params`` is empty (every trial failed).
    * Fewer than 3 nearby trials exist (sample too small for a meaningful
      standard deviation).

    The threshold is a fractional deviation; e.g. ``0.20`` means ±20% of
    each best-param value.  Zero-valued best-params are treated with a
    ``1e-9`` floor so division doesn't explode.
    """
    if not best_params:
        return None

    valid_trials = [
        t for t in trials_data
        if t.get("state") == "COMPLETE"
        and t.get("value") is not None
        and t["value"] != FAIL_VALUE
    ]
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


# ---------------------------------------------------------------------------
# Thread-safe patience tracker (Optuna-free)
# ---------------------------------------------------------------------------

class PatienceTracker:
    """Thread-safe best-so-far tracker for convergence-based early stopping.

    Pure bookkeeping — the caller is responsible for wiring this into an
    Optuna callback and invoking ``study.stop()`` when :meth:`observe`
    returns ``True``.

    Semantics match the legacy ``_PatienceCallback``:

    * The first finite value always counts as an improvement (seeded by
      ``-inf``) and resets the counter.
    * ``None`` values (pruned/failed trials) are counted as "no
      improvement" and increment the counter.
    * A value equal to the current best is **not** an improvement.
    * Stop fires when ``no_improve_count >= patience``.

    Callers should only construct this with ``patience > 0``; a zero or
    negative patience causes ``observe`` to return ``True`` immediately
    which disables the study on trial 1.
    """

    __slots__ = ("_patience", "_best", "_no_improve_count", "_lock")

    def __init__(self, patience: int) -> None:
        self._patience = patience
        self._best: float = -math.inf
        self._no_improve_count: int = 0
        self._lock = threading.Lock()

    def observe(self, value: float | None) -> bool:
        """Record a trial's value and return True when stop is warranted."""
        with self._lock:
            if value is not None and value > self._best:
                self._best = value
                self._no_improve_count = 0
            else:
                self._no_improve_count += 1
            return self._no_improve_count >= self._patience

    @property
    def best(self) -> float:
        """Best value observed so far (``-inf`` before the first update)."""
        with self._lock:
            return self._best

    @property
    def no_improve_count(self) -> int:
        """Consecutive trials without an improvement."""
        with self._lock:
            return self._no_improve_count
