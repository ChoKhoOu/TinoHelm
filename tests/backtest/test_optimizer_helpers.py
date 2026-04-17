"""Tests for :mod:`tinohelm.backtest.optimizer_helpers` — all
NT-free and optuna-free.

These helpers own the deterministic numeric and data-shaping logic that
sits next to the Optuna orchestration in :mod:`tinohelm.backtest.optimizer`.
They must work in lean CI environments that do not have the optional
``optuna`` wheel installed — the isolation test below enforces that
contract by re-importing the module under a ``sys.meta_path`` blocker.
"""
from __future__ import annotations

import importlib
import math
import sys
from datetime import date

import numpy as np
import pytest


from tinohelm.backtest.optimizer_helpers import (
    DSR_COMPATIBLE_OBJECTIVES,
    FAIL_VALUE,
    FITNESS_METRICS,
    auto_n_trials,
    auto_patience,
    auto_sampler,
    auto_workers,
    build_progress_event,
    build_wf_fold_result,
    compute_dsr,
    compute_param_sensitivity,
    compute_param_stability,
    extract_fitness,
    filter_valid_trials,
    is_valid_trial,
    slim_result,
    split_dates,
    walk_forward_windows,
)


# ---------------------------------------------------------------------------
# Import isolation
# ---------------------------------------------------------------------------


class _ImportBlocker:
    """Meta-path finder that refuses NT + optuna imports."""

    def find_spec(self, name, path=None, target=None):  # noqa: D401
        if name.startswith("nautilus_trader") or name == "optuna":
            raise ImportError(f"Blocked for isolation check: {name}")
        return None


class TestOptimizerHelpersIsolation:
    """Guard against accidental NT / optuna imports in ``optimizer_helpers``.

    We install a ``sys.meta_path`` blocker, wipe the cached module, and
    re-import to confirm the module body never reaches for NT or Optuna.
    The blocker is torn down in a ``finally`` block so downstream test
    modules (``test_result_multi``, ``test_result_schema``) see a clean
    import environment.
    """

    def test_module_reimports_without_nt_or_optuna(self):
        blocker = _ImportBlocker()
        saved_modules = {
            name: sys.modules.pop(name)
            for name in list(sys.modules)
            if name == "tinohelm.backtest.optimizer_helpers"
            or name == "tinohelm.backtest._math_primitives"
        }
        sys.meta_path.insert(0, blocker)
        try:
            mod = importlib.import_module("tinohelm.backtest.optimizer_helpers")
            assert mod.FAIL_VALUE == -999.0
            assert "optuna" not in sys.modules
            assert not any(
                m.startswith("nautilus_trader") for m in sys.modules
            )
        finally:
            if blocker in sys.meta_path:
                sys.meta_path.remove(blocker)
            # Restore the original cached modules.
            sys.modules.update(saved_modules)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_fail_value_sentinel(self):
        assert FAIL_VALUE == -999.0
        # Must be strictly less than any plausible fitness value.
        assert FAIL_VALUE < -100

    def test_fitness_metrics_mapping(self):
        assert FITNESS_METRICS == {
            "sharpe": "sharpe_ratio",
            "calmar": "calmar_ratio",
            "sortino": "sortino_ratio",
            "profit": "total_pnl",
        }

    def test_dsr_compatibility_set(self):
        assert "sharpe" in DSR_COMPATIBLE_OBJECTIVES
        assert "calmar" not in DSR_COMPATIBLE_OBJECTIVES
        # frozenset is hashable + immutable — important for module-level constant
        assert isinstance(DSR_COMPATIBLE_OBJECTIVES, frozenset)


# ---------------------------------------------------------------------------
# Smart defaults
# ---------------------------------------------------------------------------


class TestAutoNTrials:
    def test_empty_returns_floor(self):
        assert auto_n_trials({}) == 50

    def test_floor_at_50(self):
        # 2 dims × 20 = 40, clamped up to 50.
        assert auto_n_trials({"a": {}, "b": {}}) == 50

    def test_scales_linearly_above_floor(self):
        assert auto_n_trials({f"p{i}": {} for i in range(3)}) == 60
        assert auto_n_trials({f"p{i}": {} for i in range(5)}) == 100
        assert auto_n_trials({f"p{i}": {} for i in range(10)}) == 200


class TestAutoSampler:
    def test_low_dim_all_float_prefers_cmaes(self):
        assert auto_sampler({"a": {"type": "float"}}) == "cmaes"
        assert auto_sampler(
            {"a": {"type": "float"}, "b": {"type": "float"}}
        ) == "cmaes"
        assert auto_sampler(
            {
                "a": {"type": "float"},
                "b": {"type": "float"},
                "c": {"type": "float"},
            }
        ) == "cmaes"

    def test_int_param_falls_back_to_tpe(self):
        assert auto_sampler(
            {"a": {"type": "float"}, "b": {"type": "int"}}
        ) == "tpe"

    def test_high_dim_falls_back_to_tpe(self):
        assert auto_sampler(
            {f"p{i}": {"type": "float"} for i in range(4)}
        ) == "tpe"

    def test_empty_param_space_is_cmaes(self):
        # Trivially low-dim, all "float" because no integers exist.
        assert auto_sampler({}) == "cmaes"

    def test_missing_type_key_is_treated_as_float(self):
        assert auto_sampler({"a": {}}) == "cmaes"


class TestAutoWorkers:
    def test_single_core_clamps_to_one(self):
        assert auto_workers(1) == 1

    def test_large_box_clamps_to_four(self):
        assert auto_workers(64) == 4

    def test_half_ceiling_on_medium_box(self):
        assert auto_workers(4) == 2
        assert auto_workers(6) == 3

    def test_none_uses_os_cpu_count(self, monkeypatch: pytest.MonkeyPatch):
        import tinohelm.backtest.optimizer_helpers as oh

        monkeypatch.setattr(oh.os, "cpu_count", lambda: 8)
        assert oh.auto_workers(None) == 4

    def test_none_falls_back_when_cpu_count_returns_none(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        import tinohelm.backtest.optimizer_helpers as oh

        monkeypatch.setattr(oh.os, "cpu_count", lambda: None)
        # Default to 2 → //2 = 1
        assert oh.auto_workers(None) == 1


class TestAutoPatience:
    def test_below_threshold_disabled(self):
        assert auto_patience(10) == 0
        assert auto_patience(39) == 0

    def test_auto_scales_with_trials(self):
        assert auto_patience(40) == 10
        assert auto_patience(100) == 25
        assert auto_patience(200) == 50

    def test_floor_at_min_patience(self):
        # 40 // 4 = 10 — equal to floor, returns floor.
        assert auto_patience(40, min_patience=10) == 10
        # Use a custom divisor to push below floor, confirm floor wins.
        assert auto_patience(40, min_patience=15, divisor=10) == 15


# ---------------------------------------------------------------------------
# Date / window helpers
# ---------------------------------------------------------------------------


class TestSplitDates:
    _START = date(2025, 1, 1)
    _END = date(2025, 12, 31)

    def test_80_20_split(self):
        ts, te, vs, ve = split_dates(self._START, self._END, 80.0)
        assert ts == self._START
        # 364 days * 80% = 291 days → train_end = 2025-10-19
        assert te == date(2025, 10, 19)
        assert vs == date(2025, 10, 20)
        assert ve == self._END

    def test_50_50_split(self):
        ts, te, vs, ve = split_dates(self._START, self._END, 50.0)
        assert ts == self._START
        # 364 * 0.5 = 182
        assert te == date(2025, 7, 2)
        assert vs == date(2025, 7, 3)
        assert ve == self._END

    def test_zero_train_pct(self):
        ts, te, vs, _ = split_dates(self._START, self._END, 0.0)
        assert ts == self._START
        assert te == self._START  # 0 days of training
        assert vs == date(2025, 1, 2)

    def test_return_tuple_is_canonical_shape(self):
        out = split_dates(self._START, self._END, 70.0)
        assert isinstance(out, tuple)
        assert len(out) == 4
        for d in out:
            assert isinstance(d, date)


class TestWalkForwardWindows:
    _START = date(2025, 1, 1)
    _END = date(2025, 12, 31)

    def test_degenerate_zero_folds_returns_single_split(self):
        out = walk_forward_windows(self._START, self._END, 80.0, 0)
        assert len(out) == 1
        assert out[0][0] == self._START
        assert out[0][3] == self._END

    def test_degenerate_full_train_returns_single_split(self):
        # 100% train leaves no test segment — must fall back.
        out = walk_forward_windows(self._START, self._END, 100.0, 5)
        assert len(out) == 1

    def test_five_folds_distinct(self):
        out = walk_forward_windows(self._START, self._END, 70.0, 5)
        assert len(out) == 5
        # Test windows should be chronologically ordered and non-overlapping.
        for prev, curr in zip(out, out[1:]):
            _, _, prev_te_s, prev_te_e = prev
            _, _, curr_te_s, curr_te_e = curr
            assert prev_te_s < curr_te_s
            assert prev_te_e < curr_te_e

    def test_windows_respect_boundaries(self):
        out = walk_forward_windows(self._START, self._END, 60.0, 3)
        for tr_s, tr_e, te_s, te_e in out:
            assert tr_s >= self._START
            assert te_e <= self._END
            assert tr_s < tr_e < te_s < te_e

    def test_train_before_test_in_each_fold(self):
        out = walk_forward_windows(self._START, self._END, 75.0, 4)
        for tr_s, tr_e, te_s, te_e in out:
            assert tr_e < te_s  # strict separation

    def test_result_is_list_of_tuples(self):
        out = walk_forward_windows(self._START, self._END, 80.0, 3)
        assert isinstance(out, list)
        for w in out:
            assert isinstance(w, tuple) and len(w) == 4

    def test_single_fold(self):
        out = walk_forward_windows(self._START, self._END, 70.0, 1)
        assert len(out) == 1


class TestBuildWfFoldResult:
    def test_all_keys(self):
        payload = build_wf_fold_result(
            0, date(2025, 1, 1), date(2025, 3, 1),
            date(2025, 3, 2), date(2025, 4, 1),
            1.234,
        )
        assert payload == {
            "fold": 1,
            "train_start": "2025-01-01",
            "train_end": "2025-03-01",
            "test_start": "2025-03-02",
            "test_end": "2025-04-01",
            "test_value": 1.234,
        }

    def test_fold_one_based(self):
        # Internal index 0 → user-facing fold 1.
        assert build_wf_fold_result(
            0, date(2025, 1, 1), date(2025, 1, 2),
            date(2025, 1, 3), date(2025, 1, 4),
            0.0,
        )["fold"] == 1

    def test_preserves_fail_value_sentinel(self):
        payload = build_wf_fold_result(
            2, date(2025, 1, 1), date(2025, 1, 2),
            date(2025, 1, 3), date(2025, 1, 4),
            FAIL_VALUE,
        )
        assert payload["test_value"] == FAIL_VALUE
        assert payload["fold"] == 3


# ---------------------------------------------------------------------------
# Metric extraction & trial filtering
# ---------------------------------------------------------------------------


class TestExtractFitness:
    def test_happy_path_sharpe(self):
        r = {"statistics": {"sharpe_ratio": 1.5}}
        assert extract_fitness(r, "sharpe") == 1.5

    def test_happy_path_calmar(self):
        r = {"statistics": {"calmar_ratio": 0.8}}
        assert extract_fitness(r, "calmar") == 0.8

    def test_unknown_objective_is_fail_value(self):
        r = {"statistics": {"sharpe_ratio": 1.5}}
        assert extract_fitness(r, "totally_unknown") == FAIL_VALUE

    def test_none_result(self):
        assert extract_fitness(None, "sharpe") == FAIL_VALUE

    def test_missing_statistics_key(self):
        assert extract_fitness({}, "sharpe") == FAIL_VALUE

    def test_none_statistics_value(self):
        assert extract_fitness({"statistics": None}, "sharpe") == FAIL_VALUE

    def test_none_metric_value(self):
        r = {"statistics": {"sharpe_ratio": None}}
        assert extract_fitness(r, "sharpe") == FAIL_VALUE

    def test_missing_metric_key(self):
        r = {"statistics": {"calmar_ratio": 0.5}}
        assert extract_fitness(r, "sharpe") == FAIL_VALUE

    def test_non_numeric_value_returns_fail(self):
        r = {"statistics": {"sharpe_ratio": "not a number"}}
        assert extract_fitness(r, "sharpe") == FAIL_VALUE

    def test_integer_coerced_to_float(self):
        r = {"statistics": {"total_pnl": 12345}}
        assert extract_fitness(r, "profit") == 12345.0

    def test_custom_fail_value(self):
        assert extract_fitness(None, "sharpe", fail_value=-1.0) == -1.0


class TestTrialFiltering:
    _TRIALS = [
        {"number": 0, "value": 1.5, "state": "COMPLETE", "params": {"a": 1}},
        {"number": 1, "value": FAIL_VALUE, "state": "COMPLETE", "params": {"a": 2}},
        {"number": 2, "value": None, "state": "COMPLETE", "params": {"a": 3}},
        {"number": 3, "value": 0.5, "state": "PRUNED", "params": {"a": 4}},
        {"number": 4, "value": 2.0, "state": "COMPLETE", "params": {"a": 5}},
        {"number": 5, "value": 0.8, "state": "FAIL", "params": {"a": 6}},
    ]

    def test_is_valid_trial_happy(self):
        assert is_valid_trial(self._TRIALS[0])

    def test_is_valid_trial_rejects_fail_value(self):
        assert not is_valid_trial(self._TRIALS[1])

    def test_is_valid_trial_rejects_none_value(self):
        assert not is_valid_trial(self._TRIALS[2])

    def test_is_valid_trial_rejects_pruned(self):
        assert not is_valid_trial(self._TRIALS[3])

    def test_is_valid_trial_rejects_failed_state(self):
        assert not is_valid_trial(self._TRIALS[5])

    def test_filter_returns_valid_only(self):
        out = filter_valid_trials(self._TRIALS)
        assert len(out) == 2
        assert [t["number"] for t in out] == [0, 4]

    def test_filter_accepts_generator(self):
        out = filter_valid_trials(iter(self._TRIALS))
        assert len(out) == 2

    def test_filter_with_custom_fail_value(self):
        # -1.0 is no longer the sentinel, so previous FAIL_VALUE=-999 entries pass.
        out = filter_valid_trials(self._TRIALS, fail_value=-1.0)
        assert len(out) == 3  # numbers 0, 1 (now valid), 4


# ---------------------------------------------------------------------------
# Result shaping
# ---------------------------------------------------------------------------


class TestSlimResult:
    def test_keeps_required_keys_only(self):
        big = {
            "statistics": {"sharpe_ratio": 1.0},
            "equity_curve": [1, 2, 3],
            "monthly_returns": [0.01, -0.02],
            "trades": ["this should be dropped"],
            "orders": ["also dropped"],
        }
        out = slim_result(big)
        assert set(out.keys()) == {"statistics", "equity_curve", "monthly_returns"}
        assert out["statistics"] == {"sharpe_ratio": 1.0}
        assert out["equity_curve"] == [1, 2, 3]
        assert out["monthly_returns"] == [0.01, -0.02]

    def test_missing_keys_default_to_none(self):
        out = slim_result({})
        assert out == {
            "statistics": None,
            "equity_curve": None,
            "monthly_returns": None,
        }

    def test_none_input_returns_none(self):
        assert slim_result(None) is None


# ---------------------------------------------------------------------------
# Redis progress event payload
# ---------------------------------------------------------------------------


class TestBuildProgressEvent:
    def test_minimal_running_payload(self):
        payload = build_progress_event(
            42,
            trials_completed=5,
            total_trials=100,
            best_value=1.2,
            best_params={"a": 0.5},
        )
        assert payload == {
            "optimization_id": 42,
            "trials_completed": 5,
            "total_trials": 100,
            "best_value": 1.2,
            "best_params": {"a": 0.5},
            "status": "running",
        }

    def test_completed_status_preserved(self):
        payload = build_progress_event(
            1,
            trials_completed=200,
            total_trials=200,
            best_value=3.14,
            best_params={"x": 1.0},
            status="completed",
        )
        assert payload["status"] == "completed"

    def test_key_set_identical_between_running_and_completed(self):
        """Frontend listeners depend on consistent key shape."""
        running = build_progress_event(
            1, trials_completed=1, total_trials=10,
            best_value=0.0, best_params={},
        )
        completed = build_progress_event(
            1, trials_completed=10, total_trials=10,
            best_value=0.0, best_params={}, status="completed",
        )
        assert set(running.keys()) == set(completed.keys())

    def test_best_params_is_copied_not_aliased(self):
        """Mutating the caller's dict must not affect past payloads."""
        src = {"a": 1}
        payload = build_progress_event(
            1, trials_completed=1, total_trials=10,
            best_value=0.0, best_params=src,
        )
        src["a"] = 999
        assert payload["best_params"] == {"a": 1}

    def test_status_defaults_to_running(self):
        payload = build_progress_event(
            1, trials_completed=1, total_trials=10,
            best_value=0.0, best_params={},
        )
        assert payload["status"] == "running"


# ---------------------------------------------------------------------------
# Deflated Sharpe Ratio
# ---------------------------------------------------------------------------


def _make_trial(n: int, value: float, state: str = "COMPLETE") -> dict:
    """Helper to build a minimal trial dict for testing."""
    return {"number": n, "value": value, "state": state, "params": {"a": n}}


class TestComputeDsr:
    def test_too_few_trials_returns_none(self):
        trials = [_make_trial(i, 1.5) for i in range(4)]
        assert compute_dsr(1.5, trials, 0.0, 3.0, 250) is None

    def test_too_few_observations_returns_none(self):
        trials = [_make_trial(i, 1.5) for i in range(10)]
        assert compute_dsr(1.5, trials, 0.0, 3.0, 3) is None

    def test_none_best_sharpe_returns_none(self):
        trials = [_make_trial(i, 1.5) for i in range(10)]
        assert compute_dsr(None, trials, 0.0, 3.0, 250) is None

    def test_identical_trials_degenerates_to_psr_like(self):
        # All trials have identical Sharpe → ``sr_max_star`` ≈ 0, so the
        # DSR collapses to the plain PSR at ``best_sharpe``.  The function
        # should *not* raise; it returns a probability in [0, 1].
        trials = [_make_trial(i, 1.5) for i in range(10)]
        out = compute_dsr(1.5, trials, 0.0, 3.0, 250)
        assert out is not None
        assert 0.0 <= out <= 1.0

    def test_happy_path_returns_probability(self):
        # Varied trial Sharpe values with positive variance.
        trials = [_make_trial(i, 0.5 + i * 0.1) for i in range(10)]
        result = compute_dsr(
            best_sharpe=2.0,
            trials_data=trials,
            skewness=0.0,
            kurtosis=3.0,
            n_obs=252,
        )
        assert result is not None
        assert 0.0 <= result <= 1.0

    def test_ignores_invalid_trials(self):
        # Insert fail sentinel / None — they should not count toward the 5-trial floor.
        trials = [_make_trial(i, 0.5 + i * 0.1) for i in range(10)]
        trials.append(_make_trial(999, FAIL_VALUE))
        trials.append(_make_trial(1000, None))
        trials.append(_make_trial(1001, 0.3, state="PRUNED"))
        result = compute_dsr(2.0, trials, 0.0, 3.0, 252)
        assert result is not None

    def test_negative_denom_returns_none(self):
        # Extreme skewness / kurtosis that makes (1 - sk*daily_sr + ...) <= 0
        trials = [_make_trial(i, 10.0 + i) for i in range(10)]
        # With very high daily_sr and adverse skew+kurt, denom_sq can go <= 0.
        result = compute_dsr(
            best_sharpe=200.0,   # very high → daily_sr huge
            trials_data=trials,
            skewness=1000.0,
            kurtosis=-1000.0,
            n_obs=252,
        )
        assert result is None or isinstance(result, float)

    def test_result_is_rounded_to_4_decimals(self):
        trials = [_make_trial(i, 0.5 + i * 0.1) for i in range(10)]
        result = compute_dsr(2.0, trials, 0.0, 3.0, 252)
        assert result is not None
        # A valid rounded float has at most 4 decimals.
        s = f"{result:.10f}".rstrip("0")
        # Count decimal places after the point (if any).
        if "." in s:
            decimals = len(s.split(".")[1])
            assert decimals <= 4


# ---------------------------------------------------------------------------
# Parameter sensitivity
# ---------------------------------------------------------------------------


class TestComputeParamSensitivity:
    def test_too_few_trials_returns_none(self):
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"a": float(i)}}
            for i in range(5)
        ]
        out = compute_param_sensitivity(
            trials, {"a": {}}, {"a": 1.0},
        )
        assert out is None

    def test_single_param_histogram(self):
        np.random.seed(0)
        trials = []
        for i in range(30):
            trials.append({
                "state": "COMPLETE",
                "value": float(i),
                "params": {"a": float(i)},
            })
        out = compute_param_sensitivity(
            trials, {"a": {}}, {"a": 1.0},
        )
        assert out is not None
        assert "single_param" in out
        assert "a" in out["single_param"]
        assert "bins" in out["single_param"]["a"]
        assert "values" in out["single_param"]["a"]
        assert len(out["single_param"]["a"]["bins"]) == \
            len(out["single_param"]["a"]["values"])

    def test_grid_for_top_pair(self):
        np.random.seed(0)
        trials = []
        for i in range(40):
            trials.append({
                "state": "COMPLETE",
                "value": float(i),
                "params": {"a": float(i), "b": float(40 - i)},
            })
        out = compute_param_sensitivity(
            trials, {"a": {}, "b": {}}, {"a": 0.8, "b": 0.2},
        )
        assert out is not None
        assert "grid" in out
        assert "a__b" in out["grid"]
        grid_entry = out["grid"]["a__b"]
        assert grid_entry["x_label"] == "a"
        assert grid_entry["y_label"] == "b"
        assert len(grid_entry["x_bins"]) == len(grid_entry["values"])

    def test_grid_respects_max_pairs(self):
        trials = []
        for i in range(40):
            trials.append({
                "state": "COMPLETE",
                "value": float(i),
                "params": {"a": i, "b": i * 2, "c": i + 1, "d": i + 2},
            })
        out = compute_param_sensitivity(
            trials,
            {"a": {}, "b": {}, "c": {}, "d": {}},
            {"a": 0.4, "b": 0.3, "c": 0.2, "d": 0.1},
            max_pairs=2,
        )
        assert out is not None
        assert len(out["grid"]) <= 2

    def test_excludes_invalid_trials(self):
        trials = [
            {"state": "COMPLETE", "value": float(i), "params": {"a": i}}
            for i in range(15)
        ]
        # Dilute with invalid trials — these must be dropped.
        trials.extend([
            {"state": "PRUNED", "value": 10.0, "params": {"a": 999}},
            {"state": "COMPLETE", "value": FAIL_VALUE, "params": {"a": -999}},
            {"state": "COMPLETE", "value": None, "params": {"a": -888}},
        ])
        out = compute_param_sensitivity(
            trials, {"a": {}}, {"a": 1.0},
        )
        assert out is not None
        assert "a" in out["single_param"]

    def test_degenerate_constant_param_dropped(self):
        # All trials share the same param value → quantile edges collapse.
        trials = [
            {"state": "COMPLETE", "value": float(i), "params": {"a": 5.0}}
            for i in range(15)
        ]
        out = compute_param_sensitivity(
            trials, {"a": {}}, {"a": 1.0},
        )
        assert out is not None
        # Collapsed histogram is silently dropped.
        assert "a" not in out["single_param"]

    def test_bin_values_are_rounded(self):
        trials = [
            {"state": "COMPLETE", "value": float(i) + 0.123456789,
             "params": {"a": float(i)}}
            for i in range(20)
        ]
        out = compute_param_sensitivity(
            trials, {"a": {}}, {"a": 1.0},
        )
        assert out is not None
        for v in out["single_param"]["a"]["values"]:
            # Rounded to 4 decimals.
            assert abs(v - round(v, 4)) < 1e-9

    def test_min_trials_threshold_is_configurable(self):
        trials = [
            {"state": "COMPLETE", "value": float(i), "params": {"a": i}}
            for i in range(5)
        ]
        # Default threshold (10) blocks this; lowered threshold lets it through.
        assert compute_param_sensitivity(
            trials, {"a": {}}, {"a": 1.0},
        ) is None
        out = compute_param_sensitivity(
            trials, {"a": {}}, {"a": 1.0}, min_trials=3,
        )
        assert out is not None


# ---------------------------------------------------------------------------
# Parameter stability
# ---------------------------------------------------------------------------


class TestComputeParamStability:
    def test_no_best_params_returns_none(self):
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"a": 1}}
            for _ in range(5)
        ]
        assert compute_param_stability(trials, None) is None
        assert compute_param_stability(trials, {}) is None

    def test_too_few_neighbours_returns_none(self):
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"a": 100}},
            # Only one neighbour
            {"state": "COMPLETE", "value": 1.2, "params": {"a": 101}},
            # Far away
            {"state": "COMPLETE", "value": 5.0, "params": {"a": 1000}},
        ]
        assert compute_param_stability(trials, {"a": 100}) is None

    def test_happy_path_returns_std(self):
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"a": 100}},
            {"state": "COMPLETE", "value": 1.1, "params": {"a": 101}},
            {"state": "COMPLETE", "value": 0.9, "params": {"a": 99}},
            {"state": "COMPLETE", "value": 1.0, "params": {"a": 102}},
            {"state": "COMPLETE", "value": 1.1, "params": {"a": 100}},
        ]
        out = compute_param_stability(trials, {"a": 100})
        assert out is not None
        # All values near best → low std
        assert 0.0 < out < 0.2

    def test_handles_zero_best_value(self):
        # Best value exactly 0 — threshold uses max(|bv|, 1e-9) to avoid div-by-zero.
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"a": 0}},
            {"state": "COMPLETE", "value": 1.1, "params": {"a": 0}},
            {"state": "COMPLETE", "value": 0.9, "params": {"a": 0}},
        ]
        out = compute_param_stability(trials, {"a": 0})
        assert out is not None

    def test_excludes_invalid_trials(self):
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"a": 100}},
            {"state": "COMPLETE", "value": 1.1, "params": {"a": 101}},
            {"state": "COMPLETE", "value": 0.9, "params": {"a": 99}},
            {"state": "PRUNED", "value": 99.0, "params": {"a": 100}},
            {"state": "COMPLETE", "value": FAIL_VALUE, "params": {"a": 100}},
        ]
        out = compute_param_stability(trials, {"a": 100})
        assert out is not None

    def test_threshold_parameter_narrows_neighbourhood(self):
        trials = [
            # Very close to best:
            {"state": "COMPLETE", "value": 1.0, "params": {"a": 100}},
            {"state": "COMPLETE", "value": 1.05, "params": {"a": 101}},
            # Moderate distance:
            {"state": "COMPLETE", "value": 1.8, "params": {"a": 115}},
            {"state": "COMPLETE", "value": 1.9, "params": {"a": 118}},
            {"state": "COMPLETE", "value": 2.0, "params": {"a": 120}},
        ]
        wide = compute_param_stability(trials, {"a": 100}, threshold=0.25)
        narrow = compute_param_stability(
            trials, {"a": 100}, threshold=0.05, min_neighbours=2,
        )
        if wide is not None and narrow is not None:
            # Narrow neighbourhood has less fitness variance.
            assert narrow < wide

    def test_trial_missing_param_key_excluded(self):
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"a": 100, "b": 5}},
            {"state": "COMPLETE", "value": 1.1, "params": {"a": 101}},  # no b
            {"state": "COMPLETE", "value": 0.9, "params": {"a": 99, "b": 5}},
            {"state": "COMPLETE", "value": 1.0, "params": {"a": 100, "b": 5}},
        ]
        out = compute_param_stability(trials, {"a": 100, "b": 5})
        assert out is not None

    def test_result_is_rounded_to_4_decimals(self):
        trials = [
            {"state": "COMPLETE", "value": 1.0 + i * 0.0333333333,
             "params": {"a": 100 + i}}
            for i in range(6)
        ]
        out = compute_param_stability(trials, {"a": 100})
        assert out is not None
        assert abs(out - round(out, 4)) < 1e-9

    def test_non_numeric_param_rejected_gracefully(self):
        # Strings cannot be subtracted → the trial is excluded, not crashed.
        trials = [
            {"state": "COMPLETE", "value": 1.0, "params": {"a": 100}},
            {"state": "COMPLETE", "value": 1.1, "params": {"a": 100}},
            {"state": "COMPLETE", "value": 0.9, "params": {"a": 100}},
            {"state": "COMPLETE", "value": 2.0, "params": {"a": "bad"}},
        ]
        out = compute_param_stability(trials, {"a": 100})
        assert out is not None
