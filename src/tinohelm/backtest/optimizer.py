"""Optuna-based hyperparameter optimization for backtest strategies.

Supports walk-forward analysis, parallel trials, pruning, sampler selection,
convergence-based early stopping, and parameter importance analysis.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, date, timedelta
from typing import Any

try:
    import optuna
except ImportError:
    optuna = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

from tinohelm.db.sync_engine import get_sync_engine

# Fitness objective mapping: key -> path into result["statistics"]
FITNESS_METRICS: dict[str, str] = {
    "sharpe": "sharpe_ratio",
    "calmar": "calmar_ratio",
    "sortino": "sortino_ratio",
    "profit": "total_pnl",
}

# Sentinel value for failed / missing metrics so Optuna can continue.
_FAIL_VALUE: float = -999.0


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _split_dates(
    start_date: date, end_date: date, train_pct: float,
) -> tuple[date, date, date, date]:
    """Split a date range into train and test periods.

    Returns (train_start, train_end, test_start, test_end).
    """
    total_days = (end_date - start_date).days
    train_days = int(total_days * train_pct / 100.0)
    train_end = start_date + timedelta(days=train_days)
    test_start = train_end + timedelta(days=1)
    return start_date, train_end, test_start, end_date


def _walk_forward_windows(
    start_date: date,
    end_date: date,
    train_pct: float,
    n_folds: int,
) -> list[tuple[date, date, date, date]]:
    """Generate rolling walk-forward windows.

    Each window has the same train-to-test ratio defined by *train_pct*.
    Windows slide forward so that the test segment of fold *i* ends where
    fold *i+1*'s test segment begins.

    Returns a list of (train_start, train_end, test_start, test_end) tuples.
    """
    total_days = (end_date - start_date).days

    # Fixed rolling-window approach: divide the total test portion of the
    # date range evenly across *n_folds* non-overlapping test segments.
    # Each fold gets a training window sized by *train_pct* relative to
    # its test window.
    test_ratio = 1.0 - train_pct / 100.0
    if test_ratio <= 0 or n_folds <= 0:
        return [_split_dates(start_date, end_date, train_pct)]

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

    # Fallback: if no valid windows were produced, use simple split
    if not windows:
        return [_split_dates(start_date, end_date, train_pct)]

    return windows


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------

def _extract_fitness(result: dict[str, Any], objective: str) -> float:
    """Extract the fitness metric from backtest results.

    Returns ``_FAIL_VALUE`` when the metric is missing or ``None`` so Optuna
    can continue without crashing.
    """
    metric_key = FITNESS_METRICS.get(objective)
    if metric_key is None:
        return _FAIL_VALUE
    stats = result.get("statistics", {})
    value = stats.get(metric_key)
    if value is None:
        return _FAIL_VALUE
    try:
        return float(value)
    except (TypeError, ValueError):
        return _FAIL_VALUE


# ---------------------------------------------------------------------------
# Sampler factory
# ---------------------------------------------------------------------------

def _create_sampler(name: str) -> Any:
    """Instantiate an Optuna sampler by short name."""
    name_lower = name.lower()
    if name_lower == "cmaes":
        return optuna.samplers.CmaEsSampler()
    if name_lower == "random":
        return optuna.samplers.RandomSampler()
    # Default: TPE
    return optuna.samplers.TPESampler()


# ---------------------------------------------------------------------------
# Early-stopping callback
# ---------------------------------------------------------------------------

class _PatienceCallback:
    """Optuna callback that stops the study after *patience* trials with no
    improvement to the best value."""

    def __init__(self, patience: int) -> None:
        self._patience = patience
        self._best: float = -math.inf
        self._no_improve_count: int = 0

    def __call__(
        self,
        study: optuna.Study,
        trial: optuna.trial.FrozenTrial,
    ) -> None:
        if trial.value is not None and trial.value > self._best:
            self._best = trial.value
            self._no_improve_count = 0
        else:
            self._no_improve_count += 1

        if self._no_improve_count >= self._patience:
            logger.info(
                "Early stopping: no improvement for %d consecutive trials",
                self._patience,
            )
            study.stop()


# ---------------------------------------------------------------------------
# Backtest helper (thread-safe: creates its own engine per call)
# ---------------------------------------------------------------------------

def _run_backtest(
    strategy_path: str,
    config_path: str,
    params: dict[str, Any],
    catalog_path: str,
    symbol: str,
    interval: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Run a single backtest and return the result dict.

    Each call imports ``BacktestRunner`` and creates its own engine, so this
    is safe to call from multiple threads.
    """
    from tinohelm.backtest.runner import BacktestRunner

    runner = BacktestRunner(
        strategy_path=strategy_path,
        config_path=config_path,
        strategy_params=params,
        catalog_path=catalog_path,
        symbol=symbol,
        interval=interval,
        start=datetime(start.year, start.month, start.day),
        end=datetime(end.year, end.month, end.day),
    )
    return runner.run()


# ---------------------------------------------------------------------------
# Smart defaults
# ---------------------------------------------------------------------------

def _auto_n_trials(param_ranges: dict[str, dict[str, Any]]) -> int:
    """Calculate a reasonable n_trials based on search space dimensionality."""
    n_dims = len(param_ranges)
    if n_dims == 0:
        return 50
    return max(50, n_dims * 20)


def _auto_sampler(param_ranges: dict[str, dict[str, Any]]) -> str:
    """Select sampler based on parameter types and count."""
    n_dims = len(param_ranges)
    has_int = any(p.get("type") == "int" for p in param_ranges.values())
    if n_dims <= 3 and not has_int:
        return "cmaes"
    return "tpe"


def _auto_workers() -> int:
    """Select worker count based on CPU cores."""
    import os
    cpu_count = os.cpu_count() or 2
    return min(4, max(1, cpu_count // 2))


# ---------------------------------------------------------------------------
# Main optimizer class
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Robustness helpers (Layer 2/3)
# ---------------------------------------------------------------------------

def _slim_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only fields needed for IS/OOS comparison charts."""
    if result is None:
        return None
    return {
        "statistics": result.get("statistics"),
        "equity_curve": result.get("equity_curve"),
        "monthly_returns": result.get("monthly_returns"),
    }


def _compute_dsr(
    best_sharpe: float,
    trials_data: list[dict[str, Any]],
    skewness: float,
    kurtosis: float,
    n_obs: int,
) -> float | None:
    """Deflated Sharpe Ratio — Bailey & López de Prado (2014).

    Only valid when fitness_objective is "sharpe".
    Filters out failed trials (value == _FAIL_VALUE or state != COMPLETE).
    """
    from tinohelm.backtest.result import _norm_ppf, _norm_cdf

    valid_values = [
        t["value"] for t in trials_data
        if t.get("state") == "COMPLETE"
        and t.get("value") is not None
        and t["value"] != _FAIL_VALUE
    ]
    n_trials = len(valid_values)
    if n_trials < 5 or n_obs < 5 or best_sharpe is None:
        return None

    # De-annualize trial Sharpe values (stored as annualized)
    sr_values = [v / math.sqrt(252) for v in valid_values]
    sr_mean = sum(sr_values) / len(sr_values)
    sr_var = sum((s - sr_mean) ** 2 for s in sr_values) / (len(sr_values) - 1)
    if sr_var <= 0:
        return None

    gamma = 0.5772156649  # Euler-Mascheroni constant
    e_val = math.e

    # Expected maximum SR under null
    sr_max_star = math.sqrt(sr_var) * (
        (1 - gamma) * _norm_ppf(1 - 1 / n_trials)
        + gamma * _norm_ppf(1 - 1 / (n_trials * e_val))
    )

    # DSR = PSR with sr_max_star as benchmark
    daily_sr = best_sharpe / math.sqrt(252)  # de-annualize best
    denom_sq = 1 - skewness * daily_sr + ((kurtosis - 1) / 4) * daily_sr * daily_sr
    if denom_sq <= 0:
        return None
    z = (daily_sr - sr_max_star) * math.sqrt(n_obs - 1) / math.sqrt(denom_sq)
    return round(_norm_cdf(z), 4)


def _compute_param_sensitivity(
    trials_data: list[dict[str, Any]],
    param_ranges: dict[str, dict[str, Any]],
    param_importances: dict[str, float],
    n_bins: int = 10,
    max_pairs: int = 3,
) -> dict[str, Any] | None:
    """Bin trial params and compute mean fitness per bin.

    single_param: histogram for each param.
    grid: 2D heatmap for top param pairs by importance.
    """
    import numpy as np

    valid_trials = [
        t for t in trials_data
        if t.get("state") == "COMPLETE"
        and t.get("value") is not None
        and t["value"] != _FAIL_VALUE
    ]
    if len(valid_trials) < 10:
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
        if len(values) < 10:
            continue
        arr_v = np.array(values, dtype=np.float64)
        arr_f = np.array(fitnesses, dtype=np.float64)
        # Quantile-based bins
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
                bin_centers.append(round(float((bin_edges[bi] + bin_edges[bi + 1]) / 2), 6))
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
            grid_vals = [[None] * nb for _ in range(na)]
            for ai in range(na):
                for bi_idx in range(nb):
                    mask = (idx_a == ai) & (idx_b == bi_idx)
                    if mask.any():
                        grid_vals[ai][bi_idx] = round(float(arr_ff[mask].mean()), 4)
            key = f"{pa}__{pb}"
            grid[key] = {
                "x_bins": [round(float((edges_a[j] + edges_a[j + 1]) / 2), 6) for j in range(na)],
                "y_bins": [round(float((edges_b[j] + edges_b[j + 1]) / 2), 6) for j in range(nb)],
                "values": grid_vals,
                "x_label": pa,
                "y_label": pb,
            }
            pairs_done += 1
        if pairs_done >= max_pairs:
            break

    return {"single_param": single_param, "grid": grid}


def _compute_param_stability(
    trials_data: list[dict[str, Any]],
    best_params: dict[str, Any],
    threshold: float = 0.20,
) -> float | None:
    """Std of fitness for trials within ±threshold of best params.

    Lower = more stable (params don't affect fitness much near optimum).
    """
    if not best_params:
        return None

    valid_trials = [
        t for t in trials_data
        if t.get("state") == "COMPLETE"
        and t.get("value") is not None
        and t["value"] != _FAIL_VALUE
    ]
    nearby = []
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


class BacktestOptimizer:
    """Runs Optuna-based hyperparameter optimization over a backtest strategy.

    New capabilities over the base implementation:
    - Walk-forward analysis (rolling out-of-sample validation)
    - Parallel trials via ``n_workers``
    - Optuna pruning with ``MedianPruner``
    - Sampler selection (TPE / CMA-ES / Random)
    - Convergence-based early stopping (``patience``)
    - Parameter importance analysis
    """

    def __init__(
        self,
        strategy_path: str,
        config_path: str,
        symbol: str,
        interval: str,
        start_date: date,
        end_date: date,
        catalog_path: str,
        n_trials: int,
        fitness_objective: str,
        train_pct: float,
        db_url: str,
        redis_url: str,
        optimization_id: int,
        param_ranges: dict[str, dict[str, Any]] | None = None,
        strategy_params: dict[str, Any] | None = None,
        # --- New parameters ---
        n_workers: int = 1,
        walk_forward_folds: int = 0,
        pruning: bool = True,
        sampler: str = "tpe",
        patience: int = 0,
    ) -> None:
        self.strategy_path = strategy_path
        self.config_path = config_path
        self.symbol = symbol
        self.interval = interval
        self.start_date = start_date
        self.end_date = end_date
        self.catalog_path = catalog_path
        self.n_trials = n_trials
        self.fitness_objective = fitness_objective
        self.train_pct = train_pct
        self.db_url = db_url
        self.redis_url = redis_url
        self.optimization_id = optimization_id
        self.param_ranges = param_ranges or {}
        self.strategy_params = strategy_params or {}

        # New fields
        self.n_workers = n_workers
        self.walk_forward_folds = max(0, walk_forward_folds)
        self.pruning = pruning
        self.sampler = sampler
        self.patience = max(0, patience)

        # Shared engine state (simple mode only — set in run())
        self._shared_runner = None
        self._shared_engine = None
        self._shared_strategy_bundle = None
        self._shared_starting_balance: float = 0.0

    # ------------------------------------------------------------------
    # Parameter suggestion (pure, thread-safe)
    # ------------------------------------------------------------------

    def _suggest_params(
        self, trial: optuna.Trial,
    ) -> dict[str, Any]:
        """Build strategy params from Optuna trial suggestions.

        Returns a *new* dict that merges ``strategy_params`` with trial
        suggestions -- no shared mutable state is touched.
        """
        params = dict(self.strategy_params)
        for name, spec in self.param_ranges.items():
            ptype = spec.get("type", "float")
            lo = spec["min"]
            hi = spec["max"]
            step = spec.get("step")
            if ptype == "int":
                params[name] = trial.suggest_int(name, lo, hi, step=step or 1)
            elif ptype == "float":
                kw: dict[str, Any] = {}
                if step is not None:
                    kw["step"] = step
                params[name] = trial.suggest_float(name, lo, hi, **kw)
        return params

    # ------------------------------------------------------------------
    # Objective variants
    # ------------------------------------------------------------------

    def _objective_simple(
        self,
        trial: optuna.Trial,
        train_start: date,
        train_end: date,
    ) -> float:
        """Objective for simple (non-walk-forward) mode.

        When a shared engine is available (``_shared_engine``), reuses it via
        ``engine.reset()`` to avoid reloading data on every trial.  Falls back
        to full ``_run_backtest()`` otherwise.
        """
        params = self._suggest_params(trial)
        try:
            if self._shared_runner is not None:
                result = self._shared_runner.run_trial(
                    self._shared_engine,
                    self._shared_strategy_bundle,
                    self._shared_starting_balance,
                    trial_params=params,
                )
            else:
                result = _run_backtest(
                    self.strategy_path, self.config_path, params,
                    self.catalog_path, self.symbol, self.interval,
                    train_start, train_end,
                )
        except Exception as exc:
            logger.warning("Trial %d failed: %s", trial.number, exc)
            return _FAIL_VALUE

        return _extract_fitness(result, self.fitness_objective)

    def _objective_walk_forward(
        self,
        trial: optuna.Trial,
        windows: list[tuple[date, date, date, date]],
    ) -> float:
        """Objective for walk-forward mode.

        Runs the strategy on each fold's *test* window (trained params are
        suggested once and applied to all folds).  Reports intermediate values
        per fold so Optuna's pruner can cut underperforming trials early.
        """
        params = self._suggest_params(trial)
        fold_values: list[float] = []

        for step, (tr_start, tr_end, te_start, te_end) in enumerate(windows):
            # Run backtest on the *test* period of this fold
            # (parameters were selected globally, not re-fit per fold)
            try:
                result = _run_backtest(
                    self.strategy_path, self.config_path, params,
                    self.catalog_path, self.symbol, self.interval,
                    te_start, te_end,
                )
            except Exception as exc:
                logger.warning(
                    "Trial %d fold %d failed: %s", trial.number, step, exc,
                )
                fold_values.append(_FAIL_VALUE)
                continue

            value = _extract_fitness(result, self.fitness_objective)
            fold_values.append(value)

            # Report intermediate value for pruning
            if self.pruning:
                running_mean = sum(fold_values) / len(fold_values)
                trial.report(running_mean, step)
                if trial.should_prune():
                    raise optuna.TrialPruned(
                        f"Pruned at fold {step} with mean {running_mean:.4f}"
                    )

        # Aggregate: mean of out-of-sample fold values
        valid_values = [v for v in fold_values if v != _FAIL_VALUE]
        if not valid_values:
            return _FAIL_VALUE

        return sum(valid_values) / len(valid_values)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Execute the full optimization loop.

        This method is designed to run in a subprocess (via
        ``multiprocessing.Process``). It uses sync Redis and sync SQLAlchemy.
        """
        if optuna is None:
            self._fail("optuna is not installed")
            return

        # Smart defaults
        if self.n_trials <= 0:
            self.n_trials = _auto_n_trials(self.param_ranges)
            logger.info("Auto n_trials=%d (based on %d params)", self.n_trials, len(self.param_ranges))

        if self.sampler == "auto":
            self.sampler = _auto_sampler(self.param_ranges)
            logger.info("Auto sampler=%s", self.sampler)

        if self.patience <= 0 and self.n_trials >= 40:
            self.patience = max(10, self.n_trials // 4)
            logger.info("Auto patience=%d", self.patience)

        if self.n_workers <= 0:
            self.n_workers = _auto_workers()
            logger.info("Auto workers=%d", self.n_workers)

        import redis as redis_lib

        r = redis_lib.from_url(self.redis_url)
        cancel_key = f"tino:backtest:optimization:cancel:{self.optimization_id}"

        # --- Date windows ---
        use_walk_forward = self.walk_forward_folds > 0
        if use_walk_forward:
            wf_windows = _walk_forward_windows(
                self.start_date, self.end_date,
                self.train_pct, self.walk_forward_folds,
            )
        else:
            wf_windows = []

        train_start, train_end, test_start, test_end = _split_dates(
            self.start_date, self.end_date, self.train_pct,
        )

        # --- Shared engine for simple mode (avoids reloading data per trial) ---
        if not use_walk_forward:
            try:
                from tinohelm.backtest.runner import BacktestRunner

                runner = BacktestRunner(
                    strategy_path=self.strategy_path,
                    config_path=self.config_path,
                    strategy_params=dict(self.strategy_params),
                    catalog_path=self.catalog_path,
                    symbol=self.symbol,
                    interval=self.interval,
                    start=datetime(train_start.year, train_start.month, train_start.day),
                    end=datetime(train_end.year, train_end.month, train_end.day),
                )
                engine, pc, sb = runner.prepare_engine()
                self._shared_runner = runner
                self._shared_engine = engine
                self._shared_strategy_bundle = pc
                self._shared_starting_balance = sb
                logger.info(
                    "Shared engine prepared for simple mode — "
                    "data loaded once, will reset() between trials"
                )
            except Exception:
                logger.warning(
                    "Failed to prepare shared engine, falling back to per-trial mode",
                    exc_info=True,
                )

        # --- Optuna setup ---
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        sampler_instance = _create_sampler(self.sampler)

        pruner: optuna.pruners.BasePruner | None = None
        if self.pruning:
            pruner = optuna.pruners.MedianPruner(
                n_startup_trials=5, n_warmup_steps=2,
            )

        study = optuna.create_study(
            sampler=sampler_instance,
            pruner=pruner if pruner is not None else optuna.pruners.NopPruner(),
            direction="maximize",
        )

        # Convergence tracking -- thread-safe via study.trials which is
        # internally locked in Optuna.
        convergence_history: list[float] = []
        best_value: float = _FAIL_VALUE
        best_params: dict[str, Any] = {}

        # Per-fold tracking (walk-forward mode only)
        wf_fold_results: list[dict[str, Any]] = []

        def objective(trial: optuna.Trial) -> float:
            nonlocal best_value, best_params

            # --- Cancellation check ---
            if r.get(cancel_key):
                logger.info(
                    "Optimization %d cancelled by user", self.optimization_id,
                )
                r.delete(cancel_key)
                study.stop()
                return _FAIL_VALUE

            # --- Run the appropriate objective ---
            if use_walk_forward:
                fitness = self._objective_walk_forward(trial, wf_windows)
            else:
                fitness = self._objective_simple(trial, train_start, train_end)

            # --- Track best (lock-free: Optuna calls objective serially
            # when n_jobs=1; for n_jobs>1 the GIL protects these simple
            # assignments, and a stale read is acceptable since Optuna
            # tracks its own best internally) ---
            if fitness > best_value:
                best_value = fitness
                best_params = {
                    k: v
                    for k, v in self._suggest_params_from_trial(trial).items()
                    if k in self.param_ranges
                }

            convergence_history.append(best_value)

            # --- Redis progress ---
            trials_done = trial.number + 1
            try:
                r.publish(
                    f"tino:backtest:optimization:{self.optimization_id}",
                    json.dumps({
                        "optimization_id": self.optimization_id,
                        "trials_completed": trials_done,
                        "total_trials": self.n_trials,
                        "best_value": best_value,
                        "best_params": best_params,
                        "status": "running",
                    }),
                )
            except Exception:
                logger.debug("Redis publish failed (non-fatal)")

            # --- DB progress ---
            self._update_progress(trials_done, best_params, best_value)

            return fitness

        # --- Callbacks ---
        callbacks: list[Any] = []
        if self.patience > 0:
            callbacks.append(_PatienceCallback(self.patience))

        # --- Run optimisation ---
        try:
            study.optimize(
                objective,
                n_trials=self.n_trials,
                n_jobs=self.n_workers,
                callbacks=callbacks,
            )
        except Exception as exc:
            self._fail(str(exc))
            if self._shared_engine is not None:
                try:
                    self._shared_engine.dispose()
                except Exception:
                    pass
            r.close()
            return

        # --- Check cancellation ---
        if r.get(cancel_key):
            r.delete(cancel_key)
            self._fail("Cancelled by user")
            r.close()
            logger.info("Optimization %d cancelled", self.optimization_id)
            return

        # --- Resolve best from study (authoritative source) ---
        try:
            best_trial = study.best_trial
            best_value = best_trial.value if best_trial.value is not None else _FAIL_VALUE
            best_params = {
                k: v for k, v in best_trial.params.items()
                if k in self.param_ranges
            }
        except ValueError:
            # No completed trials
            pass

        # --- Walk-forward fold details ---
        if use_walk_forward and best_params:
            merged_params = dict(self.strategy_params)
            merged_params.update(best_params)
            for fold_idx, (tr_s, tr_e, te_s, te_e) in enumerate(wf_windows):
                fold_value = _FAIL_VALUE
                try:
                    fold_result = _run_backtest(
                        self.strategy_path, self.config_path, merged_params,
                        self.catalog_path, self.symbol, self.interval,
                        te_s, te_e,
                    )
                    fold_value = _extract_fitness(fold_result, self.fitness_objective)
                except Exception as exc:
                    logger.warning("WF fold %d detail run failed: %s", fold_idx, exc)

                wf_fold_results.append({
                    "fold": fold_idx + 1,
                    "train_start": tr_s.isoformat(),
                    "train_end": tr_e.isoformat(),
                    "test_start": te_s.isoformat(),
                    "test_end": te_e.isoformat(),
                    "test_value": fold_value,
                })

        # --- Validation backtest on held-out test period ---
        validation_result: dict[str, Any] | None = None
        if best_params:
            try:
                val_params = dict(self.strategy_params)
                val_params.update(best_params)
                validation_result = _run_backtest(
                    self.strategy_path, self.config_path, val_params,
                    self.catalog_path, self.symbol, self.interval,
                    test_start, test_end,
                )
            except Exception as exc:
                logger.warning("Validation backtest failed: %s", exc)

        # --- IS (train) validation backtest — simple split mode only ---
        train_validation_result: dict[str, Any] | None = None
        if best_params and not use_walk_forward:
            try:
                tv_params = dict(self.strategy_params)
                tv_params.update(best_params)
                train_validation_result = _run_backtest(
                    self.strategy_path, self.config_path, tv_params,
                    self.catalog_path, self.symbol, self.interval,
                    train_start, train_end,
                )
            except Exception as exc:
                logger.warning("Train validation backtest failed: %s", exc)

        # --- Parameter importance ---
        param_importances: dict[str, float] = {}
        try:
            param_importances = optuna.importance.get_param_importances(study)
        except Exception:
            logger.debug("Parameter importance analysis unavailable (not enough trials)")

        # --- Pruning stats ---
        total_pruned = len([
            t for t in study.trials
            if t.state == optuna.trial.TrialState.PRUNED
        ])

        # --- Trial history ---
        trials_data = []
        for t in study.trials:
            trials_data.append({
                "number": t.number,
                "params": t.params,
                "value": t.value,
                "state": t.state.name,
            })

        # --- Build full result ---
        full_result: dict[str, Any] = {
            "best_params": best_params,
            "best_value": best_value,
            "trials": trials_data,
            "train_period": {
                "start": train_start.isoformat(),
                "end": train_end.isoformat(),
            },
            "test_period": {
                "start": test_start.isoformat(),
                "end": test_end.isoformat(),
            },
            "validation": validation_result,
            "param_importances": param_importances,
            "convergence_history": convergence_history,
            "sampler": self.sampler,
            "n_workers": self.n_workers,
            "pruning_enabled": self.pruning,
            "total_pruned": total_pruned,
        }

        if use_walk_forward:
            full_result["walk_forward_results"] = wf_fold_results

        # --- Layer 2: IS validation (slimmed) ---
        full_result["train_validation"] = _slim_result(train_validation_result)

        # --- Layer 3: DSR (only for Sharpe objective) ---
        dsr_value = None
        if self.fitness_objective == "sharpe" and validation_result:
            try:
                val_stats = validation_result.get("statistics", {})
                dsr_value = _compute_dsr(
                    best_sharpe=val_stats.get("sharpe_ratio", 0),
                    trials_data=trials_data,
                    skewness=val_stats.get("skewness", 0) or 0,
                    kurtosis=val_stats.get("kurtosis", 0) or 0,
                    n_obs=len(validation_result.get("daily_returns", []) or []),
                )
            except Exception:
                logger.debug("DSR computation failed", exc_info=True)
        full_result["dsr"] = dsr_value

        # --- Layer 3: Parameter sensitivity & stability ---
        try:
            full_result["parameter_sensitivity"] = _compute_param_sensitivity(
                trials_data, self.param_ranges, param_importances,
            )
        except Exception:
            logger.debug("Parameter sensitivity computation failed", exc_info=True)
            full_result["parameter_sensitivity"] = None

        try:
            full_result["parameter_stability_score"] = _compute_param_stability(
                trials_data, best_params,
            )
        except Exception:
            logger.debug("Parameter stability computation failed", exc_info=True)
            full_result["parameter_stability_score"] = None

        # --- Persist to DB ---
        self._complete(
            best_params, best_value, len(study.trials), full_result,
        )

        # --- Publish completion ---
        try:
            r.publish(
                f"tino:backtest:optimization:{self.optimization_id}",
                json.dumps({
                    "optimization_id": self.optimization_id,
                    "status": "completed",
                    "trials_completed": len(study.trials),
                    "total_trials": self.n_trials,
                    "best_value": best_value,
                    "best_params": best_params,
                }),
            )
        except Exception:
            logger.debug("Redis publish failed (non-fatal)")

        # --- Cleanup shared engine ---
        if self._shared_engine is not None:
            try:
                self._shared_engine.dispose()
            except Exception:
                pass
            self._shared_engine = None
            self._shared_runner = None

        r.close()
        logger.info("Optimization %d completed", self.optimization_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _suggest_params_from_trial(trial: optuna.Trial) -> dict[str, Any]:
        """Return the params dict that was suggested for *trial*.

        Works for both ``Trial`` (in-progress) and ``FrozenTrial`` objects.
        """
        return dict(trial.params)

    # ------------------------------------------------------------------
    # DB helpers (sync, same pattern as worker.py)
    # ------------------------------------------------------------------

    def _update_progress(
        self, trials_completed: int, best_params: dict, best_value: float,
    ) -> None:
        """Update intermediate progress in DB."""
        try:
            from sqlalchemy import update
            from sqlalchemy.orm import Session

            from tinohelm.db.models import OptimizationRun

            engine = get_sync_engine(self.db_url)
            with Session(engine) as session:
                session.execute(
                    update(OptimizationRun)
                    .where(OptimizationRun.id == self.optimization_id)
                    .values(
                        trials_completed=trials_completed,
                        best_params_json=best_params,
                        best_value=best_value,
                    )
                )
                session.commit()
        except Exception:
            logger.exception(
                "Failed to update optimization progress for %d",
                self.optimization_id,
            )

    def _complete(
        self,
        best_params: dict,
        best_value: float,
        trials_completed: int,
        result_json: dict,
    ) -> None:
        """Mark the optimization as completed in the DB."""
        try:
            from sqlalchemy import update
            from sqlalchemy.orm import Session

            from tinohelm.db.models import OptimizationRun, OptimizationStatus

            engine = get_sync_engine(self.db_url)
            with Session(engine) as session:
                session.execute(
                    update(OptimizationRun)
                    .where(OptimizationRun.id == self.optimization_id)
                    .values(
                        status=OptimizationStatus.completed,
                        best_params_json=best_params,
                        best_value=best_value,
                        trials_completed=trials_completed,
                        result_json=result_json,
                        completed_at=datetime.utcnow(),
                    )
                )
                session.commit()
        except Exception:
            logger.exception(
                "Failed to mark optimization %d as completed",
                self.optimization_id,
            )

    def _fail(self, error_msg: str) -> None:
        """Mark the optimization as failed in the DB."""
        try:
            from sqlalchemy import update
            from sqlalchemy.orm import Session

            from tinohelm.db.models import OptimizationRun, OptimizationStatus

            engine = get_sync_engine(self.db_url)
            with Session(engine) as session:
                session.execute(
                    update(OptimizationRun)
                    .where(OptimizationRun.id == self.optimization_id)
                    .values(
                        status=OptimizationStatus.failed,
                        error=error_msg,
                        completed_at=datetime.utcnow(),
                    )
                )
                session.commit()
        except Exception:
            logger.exception(
                "Failed to mark optimization %d as failed",
                self.optimization_id,
            )


def run_optimization(**kwargs: Any) -> None:
    """Entry point for multiprocessing.Process target.

    Accepts keyword arguments and delegates to BacktestOptimizer.run().
    """
    optimizer = BacktestOptimizer(**kwargs)
    optimizer.run()
