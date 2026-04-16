"""Optuna-based hyperparameter optimization for backtest strategies.

Supports walk-forward analysis, parallel trials, pruning, sampler selection,
convergence-based early stopping, and parameter importance analysis.

Pure arithmetic / shaping helpers live in :mod:`optimizer_helpers` (Optuna-
and NT-free) so they can be unit-tested without the NT wheel installed.
"""
from __future__ import annotations

import json
import logging
import math
import threading
from datetime import datetime, date
from typing import Any

try:
    import optuna
except ImportError:
    optuna = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

from tinohelm.backtest.optimizer_helpers import (
    FAIL_VALUE as _FAIL_VALUE,
    FITNESS_METRICS,
    auto_n_trials as _auto_n_trials,
    auto_sampler as _auto_sampler,
    auto_workers as _auto_workers,
    compute_dsr as _compute_dsr,
    compute_param_sensitivity as _compute_param_sensitivity,
    compute_param_stability as _compute_param_stability,
    extract_fitness as _extract_fitness,
    slim_result as _slim_result,
    split_dates as _split_dates,
    walk_forward_windows as _walk_forward_windows,
)
from tinohelm.db.sync_engine import get_sync_engine


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
        self._lock = threading.Lock()

    def __call__(
        self,
        study: optuna.Study,
        trial: optuna.trial.FrozenTrial,
    ) -> None:
        with self._lock:
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
# Main optimizer class
# ---------------------------------------------------------------------------


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
