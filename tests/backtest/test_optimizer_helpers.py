"""Unit tests for :mod:`tinohelm.backtest.optimizer_helpers`.

These tests are Optuna-free and NautilusTrader-free — they exercise the
pure arithmetic / date / dict-shaping helpers that used to be inline in
``optimizer.py``.  By testing them here, we get a safety net even when
Optuna or the NT wheel is not installed in the CI environment.
"""
from __future__ import annotations

import math
import sys
from datetime import date
from pathlib import Path

import pytest


def _project_root() -> Path:
    """Return the repository root (the parent of ``tests/``)."""
    return Path(__file__).resolve().parents[2]


from tinohelm.backtest.optimizer_helpers import (
    DSR_MIN_OBSERVATIONS,
    DSR_MIN_TRIALS,
    FAIL_VALUE,
    FITNESS_METRICS,
    SENSITIVITY_MIN_TRIALS,
    STABILITY_DEFAULT_THRESHOLD,
    STABILITY_MIN_NEARBY,
    TRADING_DAYS_PER_YEAR,
    auto_n_trials,
    auto_sampler,
    auto_workers,
    compute_dsr,
    compute_param_sensitivity,
    compute_param_stability,
    extract_fitness,
    filter_completed_trials,
    slim_result,
    split_dates,
    walk_forward_windows,
)


# ---------------------------------------------------------------------------
# Module isolation: ``optimizer_helpers`` must stay Optuna-free / NT-free.
# ---------------------------------------------------------------------------

class TestModuleIsolation:
    def test_helpers_module_has_no_optuna_or_nt_import(self) -> None:
        """Importing the helpers in a clean process must not pull in Optuna or NT.

        We spawn a fresh subprocess because the in-process ``sys.modules`` is
        polluted once any other test has loaded NT.  The subprocess starts
        cold, imports only our helpers, and reports which heavyweight modules
        ended up in ``sys.modules``.
        """
        import subprocess

        script = (
            "import sys\n"
            "import tinohelm.backtest.optimizer_helpers  # noqa: F401\n"
            "leaked = sorted(m for m in sys.modules if m.split('.')[0] in "
            "('optuna', 'nautilus_trader', 'sqlalchemy', 'redis'))\n"
            "print(','.join(leaked))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"},
            cwd=str(_project_root()),
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        leaked = [name for name in result.stdout.strip().split(",") if name]
        assert leaked == [], f"heavyweight modules leaked: {leaked}"

    def test_constants_are_frozen(self) -> None:
        """Constants are not accidentally mutable dicts/lists that callers can wreck."""
        assert FAIL_VALUE == -999.0
        assert TRADING_DAYS_PER_YEAR == 252
        assert DSR_MIN_TRIALS == 5
        assert DSR_MIN_OBSERVATIONS == 5
        assert SENSITIVITY_MIN_TRIALS == 10
        assert STABILITY_MIN_NEARBY == 3
        assert STABILITY_DEFAULT_THRESHOLD == pytest.approx(0.20)

    def test_fitness_metrics_keys(self) -> None:
        assert set(FITNESS_METRICS.keys()) == {"sharpe", "calmar", "sortino", "profit"}
        assert FITNESS_METRICS["sharpe"] == "sharpe_ratio"
        assert FITNESS_METRICS["profit"] == "total_pnl"


# ---------------------------------------------------------------------------
# split_dates
# ---------------------------------------------------------------------------

class TestSplitDates:
    def test_happy_70_pct_train(self) -> None:
        ts, te, vs, ve = split_dates(date(2024, 1, 1), date(2024, 12, 31), 70.0)
        assert ts == date(2024, 1, 1)
        assert ve == date(2024, 12, 31)
        # 70% of 365 days = 255.5 -> int = 255; train_end = 1/1 + 255d.
        assert te == date(2024, 1, 1).fromordinal(date(2024, 1, 1).toordinal() + 255)
        assert vs == te.fromordinal(te.toordinal() + 1)

    def test_zero_pct_train(self) -> None:
        ts, te, vs, ve = split_dates(date(2024, 1, 1), date(2024, 12, 31), 0.0)
        assert te == date(2024, 1, 1)  # 0 days = start
        assert vs == date(2024, 1, 2)
        assert ve == date(2024, 12, 31)

    def test_full_train_gap_intended(self) -> None:
        """train_pct=100 produces a 1-day gap — intentional to keep train/test disjoint."""
        ts, te, vs, ve = split_dates(date(2024, 1, 1), date(2024, 1, 31), 100.0)
        assert te == date(2024, 1, 31)
        assert vs == date(2024, 2, 1)

    def test_single_day_range(self) -> None:
        ts, te, vs, ve = split_dates(date(2024, 1, 1), date(2024, 1, 1), 50.0)
        assert te == date(2024, 1, 1)
        assert vs == date(2024, 1, 2)

    def test_returns_original_start_end(self) -> None:
        """ts and ve should equal the input start/end exactly."""
        s, e = date(2020, 3, 5), date(2023, 8, 17)
        ts, te, vs, ve = split_dates(s, e, 50.0)
        assert ts is s
        assert ve is e


# ---------------------------------------------------------------------------
# walk_forward_windows
# ---------------------------------------------------------------------------

class TestWalkForwardWindows:
    def test_three_folds_happy_path(self) -> None:
        windows = walk_forward_windows(
            date(2024, 1, 1), date(2024, 12, 31), 70.0, 3,
        )
        assert len(windows) == 3
        # All tuples (train_s, train_e, test_s, test_e) with valid ordering.
        for tr_s, tr_e, te_s, te_e in windows:
            assert tr_s <= tr_e
            assert te_s <= te_e
            # Test follows training with 1-day gap.
            assert (te_s - tr_e).days == 1

    def test_n_folds_zero_falls_back_to_simple_split(self) -> None:
        windows = walk_forward_windows(
            date(2024, 1, 1), date(2024, 12, 31), 70.0, 0,
        )
        assert len(windows) == 1
        assert windows[0] == split_dates(date(2024, 1, 1), date(2024, 12, 31), 70.0)

    def test_train_pct_100_falls_back(self) -> None:
        windows = walk_forward_windows(
            date(2024, 1, 1), date(2024, 12, 31), 100.0, 3,
        )
        assert len(windows) == 1

    def test_train_pct_above_100_falls_back(self) -> None:
        windows = walk_forward_windows(
            date(2024, 1, 1), date(2024, 12, 31), 150.0, 3,
        )
        assert len(windows) == 1

    def test_single_fold(self) -> None:
        windows = walk_forward_windows(
            date(2024, 1, 1), date(2024, 6, 30), 70.0, 1,
        )
        assert len(windows) == 1
        tr_s, tr_e, te_s, te_e = windows[0]
        assert te_e == date(2024, 6, 30)

    def test_test_segments_do_not_overlap(self) -> None:
        windows = walk_forward_windows(
            date(2023, 1, 1), date(2024, 12, 31), 60.0, 4,
        )
        for (_, _, _, te_prev_end), (_, _, te_next_start, _) in zip(windows, windows[1:]):
            assert te_prev_end < te_next_start

    def test_clamping_to_start_boundary(self) -> None:
        """Early folds' train windows clamp to start_date — never go before."""
        windows = walk_forward_windows(
            date(2024, 1, 1), date(2024, 12, 31), 90.0, 5,
        )
        for tr_s, _, _, _ in windows:
            assert tr_s >= date(2024, 1, 1)

    def test_end_clamping(self) -> None:
        """test_end never exceeds end_date."""
        windows = walk_forward_windows(
            date(2024, 1, 1), date(2024, 6, 30), 70.0, 3,
        )
        for _, _, _, te_e in windows:
            assert te_e <= date(2024, 6, 30)


# ---------------------------------------------------------------------------
# extract_fitness
# ---------------------------------------------------------------------------

class TestExtractFitness:
    def test_happy_sharpe(self) -> None:
        result = {"statistics": {"sharpe_ratio": 1.83}}
        assert extract_fitness(result, "sharpe") == pytest.approx(1.83)

    def test_happy_calmar(self) -> None:
        result = {"statistics": {"calmar_ratio": 2.5}}
        assert extract_fitness(result, "calmar") == pytest.approx(2.5)

    def test_happy_sortino(self) -> None:
        result = {"statistics": {"sortino_ratio": 1.2}}
        assert extract_fitness(result, "sortino") == pytest.approx(1.2)

    def test_happy_profit(self) -> None:
        result = {"statistics": {"total_pnl": 12345.67}}
        assert extract_fitness(result, "profit") == pytest.approx(12345.67)

    def test_unknown_objective_returns_fail(self) -> None:
        assert extract_fitness({"statistics": {"sharpe_ratio": 3}}, "nope") == FAIL_VALUE

    def test_missing_metric_returns_fail(self) -> None:
        assert extract_fitness({"statistics": {}}, "sharpe") == FAIL_VALUE

    def test_missing_statistics_returns_fail(self) -> None:
        assert extract_fitness({}, "sharpe") == FAIL_VALUE

    def test_none_value_returns_fail(self) -> None:
        assert extract_fitness({"statistics": {"sharpe_ratio": None}}, "sharpe") == FAIL_VALUE

    def test_non_numeric_value_returns_fail(self) -> None:
        assert extract_fitness(
            {"statistics": {"sharpe_ratio": "not-a-number"}}, "sharpe",
        ) == FAIL_VALUE

    def test_integer_coerces_to_float(self) -> None:
        result = {"statistics": {"sharpe_ratio": 5}}
        val = extract_fitness(result, "sharpe")
        assert isinstance(val, float)
        assert val == 5.0

    def test_non_dict_result_returns_fail(self) -> None:
        assert extract_fitness("not a dict", "sharpe") == FAIL_VALUE  # type: ignore[arg-type]

    def test_non_dict_statistics_returns_fail(self) -> None:
        assert extract_fitness({"statistics": "not a dict"}, "sharpe") == FAIL_VALUE


# ---------------------------------------------------------------------------
# auto_n_trials / auto_sampler / auto_workers
# ---------------------------------------------------------------------------

class TestAutoDefaults:
    def test_auto_n_trials_zero_dim(self) -> None:
        assert auto_n_trials({}) == 50

    def test_auto_n_trials_single_dim(self) -> None:
        assert auto_n_trials({"a": {"type": "float", "min": 0, "max": 1}}) == 50

    def test_auto_n_trials_scales_with_dims(self) -> None:
        ranges = {f"p{i}": {"type": "float", "min": 0, "max": 1} for i in range(5)}
        assert auto_n_trials(ranges) == 100  # max(50, 5 * 20)

    def test_auto_n_trials_floor_at_50(self) -> None:
        """Below 3 dims, still get 50 (the floor)."""
        ranges = {"a": {"type": "float"}, "b": {"type": "float"}}
        assert auto_n_trials(ranges) == 50

    def test_auto_sampler_cmaes_for_small_continuous(self) -> None:
        ranges = {
            "a": {"type": "float", "min": 0, "max": 1},
            "b": {"type": "float", "min": 0, "max": 1},
        }
        assert auto_sampler(ranges) == "cmaes"

    def test_auto_sampler_cmaes_at_boundary_3_dims(self) -> None:
        ranges = {
            f"p{i}": {"type": "float", "min": 0, "max": 1} for i in range(3)
        }
        assert auto_sampler(ranges) == "cmaes"

    def test_auto_sampler_tpe_for_int_param(self) -> None:
        ranges = {"a": {"type": "int", "min": 1, "max": 10}}
        assert auto_sampler(ranges) == "tpe"

    def test_auto_sampler_tpe_for_many_dims(self) -> None:
        ranges = {f"p{i}": {"type": "float"} for i in range(5)}
        assert auto_sampler(ranges) == "tpe"

    def test_auto_sampler_default_float_type_triggers_cmaes(self) -> None:
        """When type is absent, default is 'float' so cmaes is selected."""
        ranges = {"a": {"min": 0, "max": 1}}
        assert auto_sampler(ranges) == "cmaes"

    def test_auto_workers_bounded_upper(self) -> None:
        assert auto_workers(16) == 4  # capped at 4

    def test_auto_workers_bounded_lower(self) -> None:
        assert auto_workers(1) == 1  # floor at 1 even on 1-cpu host
        assert auto_workers(0) == 1  # even 0 returns 1

    def test_auto_workers_mid(self) -> None:
        assert auto_workers(4) == 2  # 4 // 2 = 2

    def test_auto_workers_none_falls_back_to_os(self) -> None:
        # Just verify it returns an int in the valid range.
        result = auto_workers(None)
        assert 1 <= result <= 4


# ---------------------------------------------------------------------------
# slim_result / filter_completed_trials
# ---------------------------------------------------------------------------

class TestSlimResult:
    def test_none_input_returns_none(self) -> None:
        assert slim_result(None) is None

    def test_keeps_only_three_keys(self) -> None:
        full = {
            "statistics": {"sharpe": 1.5},
            "equity_curve": [100, 110],
            "monthly_returns": [{"month": "2024-01", "ret": 0.05}],
            "trades": ["A", "B", "C"],  # should be dropped
            "foo": "bar",  # should be dropped
        }
        slim = slim_result(full)
        assert slim is not None
        assert set(slim.keys()) == {"statistics", "equity_curve", "monthly_returns"}

    def test_missing_keys_become_none(self) -> None:
        slim = slim_result({"statistics": {"s": 1}})
        assert slim == {
            "statistics": {"s": 1},
            "equity_curve": None,
            "monthly_returns": None,
        }

    def test_empty_dict(self) -> None:
        assert slim_result({}) == {
            "statistics": None,
            "equity_curve": None,
            "monthly_returns": None,
        }


class TestFilterCompletedTrials:
    def test_filters_incomplete(self) -> None:
        trials = [
            {"state": "COMPLETE", "value": 1.0},
            {"state": "PRUNED", "value": 0.5},
            {"state": "RUNNING", "value": None},
        ]
        assert filter_completed_trials(trials) == [{"state": "COMPLETE", "value": 1.0}]

    def test_filters_none_value(self) -> None:
        trials = [
            {"state": "COMPLETE", "value": None},
            {"state": "COMPLETE", "value": 2.0},
        ]
        assert filter_completed_trials(trials) == [{"state": "COMPLETE", "value": 2.0}]

    def test_filters_fail_sentinel(self) -> None:
        trials = [
            {"state": "COMPLETE", "value": FAIL_VALUE},
            {"state": "COMPLETE", "value": 1.5},
        ]
        assert filter_completed_trials(trials) == [{"state": "COMPLETE", "value": 1.5}]

    def test_empty(self) -> None:
        assert filter_completed_trials([]) == []

    def test_all_valid(self) -> None:
        trials = [
            {"state": "COMPLETE", "value": 1.0},
            {"state": "COMPLETE", "value": 2.0},
        ]
        assert filter_completed_trials(trials) == trials


# ---------------------------------------------------------------------------
# compute_dsr
# ---------------------------------------------------------------------------

class TestComputeDsr:
    def _mk_trials(self, values: list[float]) -> list[dict[str, any]]:  # type: ignore[type-arg]
        return [{"state": "COMPLETE", "value": v} for v in values]

    def test_too_few_trials_returns_none(self) -> None:
        trials = self._mk_trials([1.0, 1.1, 1.2])  # < DSR_MIN_TRIALS
        assert compute_dsr(1.5, trials, 0.1, 3.0, 252) is None

    def test_too_few_observations_returns_none(self) -> None:
        trials = self._mk_trials([1.0, 1.1, 1.2, 1.3, 1.4])
        assert compute_dsr(1.5, trials, 0.1, 3.0, 2) is None

    def test_none_best_sharpe_returns_none(self) -> None:
        trials = self._mk_trials([1.0, 1.1, 1.2, 1.3, 1.4])
        assert compute_dsr(None, trials, 0.1, 3.0, 252) is None

    def test_zero_variance_returns_none(self) -> None:
        """Identical values -> var=0 -> DSR undefined."""
        trials = self._mk_trials([1.0] * 10)
        assert compute_dsr(1.5, trials, 0.1, 3.0, 252) is None

    def test_happy_path_returns_probability(self) -> None:
        trials = self._mk_trials([1.0 + 0.1 * i for i in range(10)])
        dsr = compute_dsr(1.5, trials, 0.1, 3.0, 252)
        assert dsr is not None
        assert 0.0 <= dsr <= 1.0
        # Also verify rounded to 4 decimals.
        assert dsr == round(dsr, 4)

    def test_filters_sentinel_and_incomplete(self) -> None:
        """FAIL_VALUE and non-COMPLETE trials are excluded from variance calc."""
        trials = (
            [{"state": "COMPLETE", "value": FAIL_VALUE}] * 5
            + [{"state": "PRUNED", "value": 1.5}] * 5
            + [{"state": "COMPLETE", "value": v} for v in [1.0, 1.1, 1.2, 1.3, 1.4]]
        )
        dsr = compute_dsr(1.5, trials, 0.1, 3.0, 252)
        assert dsr is not None

    def test_negative_denom_returns_none(self) -> None:
        """Extreme skewness/kurtosis that drive PSR denominator non-positive."""
        trials = self._mk_trials([1.0 + 0.01 * i for i in range(10)])
        # Pick skewness * daily_sr > 1 to force denom <= 0.
        dsr = compute_dsr(best_sharpe=100.0, trials_data=trials, skewness=100.0, kurtosis=1.0, n_obs=252)
        # Negative denom triggers None per the guard in compute_dsr.
        assert dsr is None

    def test_reproducibility(self) -> None:
        """Same input -> same output."""
        trials = self._mk_trials([1.0 + 0.1 * i for i in range(10)])
        a = compute_dsr(1.5, trials, 0.1, 3.0, 252)
        b = compute_dsr(1.5, trials, 0.1, 3.0, 252)
        assert a == b


# ---------------------------------------------------------------------------
# compute_param_sensitivity
# ---------------------------------------------------------------------------

class TestComputeParamSensitivity:
    def test_too_few_trials_returns_none(self) -> None:
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"a": 0.5}}
            for _ in range(5)
        ]
        assert compute_param_sensitivity(trials, {"a": {}}, {"a": 1.0}) is None

    def test_empty_trials(self) -> None:
        assert compute_param_sensitivity([], {"a": {}}, {}) is None

    def test_single_param_sensitivity(self) -> None:
        trials = [
            {"state": "COMPLETE", "value": float(i), "params": {"a": i * 0.1}}
            for i in range(1, 21)  # 20 trials
        ]
        result = compute_param_sensitivity(
            trials, {"a": {"min": 0, "max": 2}}, {"a": 1.0}, n_bins=5,
        )
        assert result is not None
        assert "a" in result["single_param"]
        sp = result["single_param"]["a"]
        assert len(sp["bins"]) == len(sp["values"])
        assert all(isinstance(v, float) for v in sp["values"])
        # Strictly increasing fitness -> bin values should be non-decreasing.
        assert sp["values"] == sorted(sp["values"])

    def test_pair_grid(self) -> None:
        import random

        random.seed(42)
        trials = []
        for i in range(30):
            a = random.uniform(0, 1)
            b = random.uniform(0, 1)
            fitness = a + b
            trials.append({
                "state": "COMPLETE",
                "value": fitness,
                "params": {"a": a, "b": b},
            })
        result = compute_param_sensitivity(
            trials,
            {"a": {}, "b": {}},
            {"a": 1.0, "b": 1.0},
            n_bins=3,
        )
        assert result is not None
        grid = result["grid"]
        assert "a__b" in grid
        entry = grid["a__b"]
        assert entry["x_label"] == "a"
        assert entry["y_label"] == "b"
        assert len(entry["x_bins"]) == len(entry["values"])

    def test_missing_param_in_trials(self) -> None:
        """Trials lacking the parameter are skipped per-param — does not crash."""
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"a": 0.5}}
            for _ in range(15)
        ]
        # Param 'b' is declared but never appears in trials.
        result = compute_param_sensitivity(
            trials, {"a": {}, "b": {}}, {"a": 1.0, "b": 0.5},
        )
        assert result is not None
        assert "b" not in result["single_param"]

    def test_max_pairs_limits_grid_size(self) -> None:
        import random

        random.seed(7)
        trials = []
        for _ in range(40):
            params = {k: random.random() for k in ["a", "b", "c", "d"]}
            fitness = sum(params.values())
            trials.append({"state": "COMPLETE", "value": fitness, "params": params})
        importances = {"a": 0.4, "b": 0.3, "c": 0.2, "d": 0.1}
        result = compute_param_sensitivity(
            trials,
            {"a": {}, "b": {}, "c": {}, "d": {}},
            importances,
            max_pairs=2,
        )
        assert result is not None
        assert len(result["grid"]) == 2

    def test_sentinel_trials_are_filtered(self) -> None:
        trials = [
            {"state": "COMPLETE", "value": FAIL_VALUE, "params": {"a": 0.1}}
            for _ in range(20)
        ]
        # All trials are sentinel -> no valid trials -> None.
        assert compute_param_sensitivity(trials, {"a": {}}, {"a": 1.0}) is None

    def test_output_shape_for_empty_importances(self) -> None:
        """When importances is empty, grid is empty but single_param still populates."""
        trials = [
            {"state": "COMPLETE", "value": float(i), "params": {"a": i * 0.1}}
            for i in range(1, 16)
        ]
        result = compute_param_sensitivity(trials, {"a": {}}, {}, n_bins=4)
        assert result is not None
        assert result["grid"] == {}
        assert "a" in result["single_param"]

    def test_grid_values_are_rounded(self) -> None:
        import random

        random.seed(123)
        trials = [
            {
                "state": "COMPLETE",
                "value": random.uniform(0, 1),
                "params": {"a": random.random(), "b": random.random()},
            }
            for _ in range(30)
        ]
        result = compute_param_sensitivity(
            trials, {"a": {}, "b": {}}, {"a": 1.0, "b": 0.9}, n_bins=3,
        )
        assert result is not None
        grid_vals = result["grid"]["a__b"]["values"]
        for row in grid_vals:
            for v in row:
                if v is not None:
                    assert v == round(v, 4)


# ---------------------------------------------------------------------------
# compute_param_stability
# ---------------------------------------------------------------------------

class TestComputeParamStability:
    def test_empty_best_params_returns_none(self) -> None:
        trials = [{"state": "COMPLETE", "value": 1.0, "params": {"a": 1.0}}]
        assert compute_param_stability(trials, {}) is None

    def test_too_few_nearby_returns_none(self) -> None:
        best = {"a": 1.0}
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"a": 10.0}},  # far
            {"state": "COMPLETE", "value": 1.1, "params": {"a": 11.0}},  # far
        ]
        assert compute_param_stability(trials, best) is None

    def test_happy_path_returns_stdev(self) -> None:
        best = {"a": 1.0}
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"a": 1.0}},
            {"state": "COMPLETE", "value": 1.05, "params": {"a": 1.05}},
            {"state": "COMPLETE", "value": 1.1, "params": {"a": 1.1}},
            {"state": "COMPLETE", "value": 1.15, "params": {"a": 1.15}},
        ]
        result = compute_param_stability(trials, best)
        assert result is not None
        assert result > 0
        # Sanity: rounded to 4 decimals.
        assert result == round(result, 4)

    def test_zero_best_param_uses_epsilon(self) -> None:
        """Dividing by abs(0) would crash; STABILITY_EPSILON must save us."""
        best = {"a": 0.0}
        # With bv=0, any params[a]/eps > 0.2 drops the trial.  So all need to be
        # within ±(0.2 * eps) ≈ 0 — only exact matches count.
        trials = [
            {"state": "COMPLETE", "value": v, "params": {"a": 0.0}}
            for v in [0.95, 1.0, 1.05, 1.10]
        ]
        result = compute_param_stability(trials, best)
        assert result is not None

    def test_missing_best_param_in_trial_is_excluded(self) -> None:
        best = {"a": 1.0, "b": 2.0}
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"a": 1.0}},  # missing b
            {"state": "COMPLETE", "value": 1.05, "params": {"a": 1.05, "b": 2.05}},
            {"state": "COMPLETE", "value": 1.1, "params": {"a": 1.1, "b": 2.1}},
            {"state": "COMPLETE", "value": 1.15, "params": {"a": 1.15, "b": 2.15}},
        ]
        # First trial excluded (missing 'b'); 3 remain which meets min.
        result = compute_param_stability(trials, best)
        assert result is not None

    def test_custom_threshold_expands_neighborhood(self) -> None:
        best = {"a": 1.0}
        trials = [
            {"state": "COMPLETE", "value": 1.0 + 0.01 * i, "params": {"a": 1.0 + 0.05 * i}}
            for i in range(5)
        ]
        # Narrow threshold excludes most; wide includes more.
        narrow = compute_param_stability(trials, best, threshold=0.01)
        wide = compute_param_stability(trials, best, threshold=0.5)
        # Narrow may be None or small sample; wide should exist.
        assert wide is not None

    def test_filters_sentinel_fail_trials(self) -> None:
        best = {"a": 1.0}
        trials = (
            [{"state": "COMPLETE", "value": FAIL_VALUE, "params": {"a": 1.0}}] * 5
            + [
                {"state": "COMPLETE", "value": v, "params": {"a": 1.0 + 0.01 * i}}
                for i, v in enumerate([1.0, 1.01, 1.02])
            ]
        )
        # Only 3 real trials, still meets min.
        result = compute_param_stability(trials, best)
        assert result is not None


# ---------------------------------------------------------------------------
# Numerical equivalence with result.statistics module
# ---------------------------------------------------------------------------

class TestNormalApproximationsEquivalence:
    """The inlined _norm_ppf / _norm_cdf must match the ones in
    ``result.statistics`` bit-for-bit (within float precision).

    Duplication is deliberate (to keep this module NT-free); this test
    prevents silent drift if either copy is edited."""

    def test_norm_ppf_matches(self) -> None:
        # Load statistics.py directly, bypassing its NT-tainted package init.
        import importlib.util
        from pathlib import Path

        from tinohelm.backtest import optimizer_helpers as oh

        stats_path = (
            Path(__file__).resolve().parents[2]
            / "src" / "tinohelm" / "backtest" / "result" / "statistics.py"
        )
        spec = importlib.util.spec_from_file_location("_stats_mod", stats_path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        for p in [0.001, 0.01, 0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 0.99, 0.999]:
            a = oh._norm_ppf(p)
            b = mod._norm_ppf(p)
            assert math.isclose(a, b, rel_tol=0, abs_tol=1e-12), (p, a, b)

    def test_norm_cdf_matches(self) -> None:
        import importlib.util
        from pathlib import Path

        from tinohelm.backtest import optimizer_helpers as oh

        stats_path = (
            Path(__file__).resolve().parents[2]
            / "src" / "tinohelm" / "backtest" / "result" / "statistics.py"
        )
        spec = importlib.util.spec_from_file_location("_stats_mod", stats_path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        for x in [-5, -3, -1, -0.5, 0, 0.5, 1, 3, 5]:
            a = oh._norm_cdf(x)
            b = mod._norm_cdf(x)
            assert math.isclose(a, b, rel_tol=0, abs_tol=1e-12), (x, a, b)


# ---------------------------------------------------------------------------
# Backward-compat: optimizer.py re-exports
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    """``api/routes/optimize.py`` imports ``_auto_n_trials`` / ``_auto_workers``
    directly from ``optimizer``.  Guard that surface."""

    def test_optimizer_module_exposes_legacy_private_names(self) -> None:
        from tinohelm.backtest import optimizer as opt

        assert opt._FAIL_VALUE == FAIL_VALUE
        assert opt.FITNESS_METRICS is FITNESS_METRICS
        assert opt._auto_n_trials is auto_n_trials
        assert opt._auto_workers is auto_workers
        assert opt._auto_sampler is auto_sampler
        assert opt._split_dates is split_dates
        assert opt._walk_forward_windows is walk_forward_windows
        assert opt._extract_fitness is extract_fitness
        assert opt._slim_result is slim_result
        assert opt._compute_dsr is compute_dsr
        assert opt._compute_param_sensitivity is compute_param_sensitivity
        assert opt._compute_param_stability is compute_param_stability
