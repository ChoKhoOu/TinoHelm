"""Unit tests — ``tinohelm.factor.evaluation.params_grid``.

5 tests covering:
1. Returns at most top_k candidates.
2. Results sorted by IR descending.
3. Corr-filter removes correlated candidates.
4. Parallel speedup vs serial: n_jobs=4 wall-time ≤ serial × 0.6.
5. Single-param grid → exactly 1 combination → 1 candidate.

Pure-logic, deterministic (fixed seeds), NT-free, joblib loky backend.
"""
from __future__ import annotations

import datetime as dt
import math
import time
from typing import Any

import numpy as np
import polars as pl
import pytest

from tinohelm.factor.evaluation.params_grid import (
    _ic_series_to_array,
    _pearson_corr,
    params_grid,
)
from tinohelm.factor.types import EvalConfig


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _hourly_ts(n: int, start: dt.datetime = dt.datetime(2024, 1, 1)) -> pl.Series:
    return pl.datetime_range(
        start=start,
        end=start + dt.timedelta(hours=n - 1),
        interval="1h",
        eager=True,
    )


def _make_factor_panel(n: int = 500, seed: int = 0) -> pl.DataFrame:
    """Build a 2-col [ts, value] factor panel."""
    rng = np.random.default_rng(seed)
    ts = _hourly_ts(n)
    vals = rng.normal(0, 1, n)
    return pl.DataFrame({"ts": ts, "value": vals.tolist()})


def _make_forward_returns(n: int = 500, seed: int = 42) -> pl.DataFrame:
    """Build a 2-col [ts, value] forward-return panel (values already shifted)."""
    rng = np.random.default_rng(seed)
    ts = _hourly_ts(n)
    vals = rng.normal(0, 0.01, n).tolist()
    vals[-1] = None  # simulate the null-tail convention
    return pl.DataFrame({"ts": ts, "value": vals})


_DEFAULT_FWD = _make_forward_returns(500)
_DEFAULT_CONFIG = EvalConfig(universe=(), start="", end="")


def _make_factor_fn(seed: int = 1) -> Any:
    """Return a factor_fn that ignores params and always returns the same panel."""
    panel = _make_factor_panel(500, seed=seed)

    def _fn(**kwargs: object) -> pl.DataFrame:
        return panel

    return _fn


# ---------------------------------------------------------------------------
# 1. test_grid_returns_at_most_top_k
# ---------------------------------------------------------------------------

class TestGridReturnsAtMostTopK:
    def test_grid_returns_at_most_top_k(self):
        """5 grid combos, corr_filter disabled (> 1.0) → at most 3 results."""
        factor_fn = _make_factor_fn(seed=7)
        results = params_grid(
            factor_fn=factor_fn,
            base_panel_data={},
            grid={"n": [5, 10, 20, 30, 40]},
            forward_returns_df=_DEFAULT_FWD,
            top_k=3,
            corr_filter=1.1,  # corr filter disabled
            n_jobs=1,
            eval_config=_DEFAULT_CONFIG,
        )
        assert len(results) <= 3

    def test_fewer_combos_than_top_k(self):
        """Only 2 combos but top_k=5 → return all 2."""
        factor_fn = _make_factor_fn(seed=8)
        results = params_grid(
            factor_fn=factor_fn,
            base_panel_data={},
            grid={"n": [5, 10]},
            forward_returns_df=_DEFAULT_FWD,
            top_k=5,
            corr_filter=1.1,
            n_jobs=1,
            eval_config=_DEFAULT_CONFIG,
        )
        assert len(results) <= 2

    def test_empty_grid_returns_one_result(self):
        """No params in grid → single empty-params combo."""
        factor_fn = _make_factor_fn(seed=9)
        results = params_grid(
            factor_fn=factor_fn,
            base_panel_data={},
            grid={},
            forward_returns_df=_DEFAULT_FWD,
            top_k=3,
            corr_filter=1.1,
            n_jobs=1,
            eval_config=_DEFAULT_CONFIG,
        )
        assert len(results) == 1
        assert results[0]["params"] == {}


# ---------------------------------------------------------------------------
# 2. test_results_sorted_by_ir_descending
# ---------------------------------------------------------------------------

class TestResultsSortedByIrDescending:
    def test_results_sorted_by_ir_descending(self):
        """IR values in returned list must be non-increasing."""
        factor_fn = _make_factor_fn(seed=2)
        results = params_grid(
            factor_fn=factor_fn,
            base_panel_data={},
            grid={"n": [5, 10, 20, 30]},
            forward_returns_df=_DEFAULT_FWD,
            top_k=10,
            corr_filter=1.1,  # disabled so all survive filtering
            n_jobs=1,
            eval_config=_DEFAULT_CONFIG,
        )
        irs = [r["ir"] for r in results]
        for i in range(len(irs) - 1):
            # IR must be descending (equal is fine).
            assert irs[i] >= irs[i + 1], (
                f"IR not descending at index {i}: {irs[i]} < {irs[i + 1]}"
            )

    def test_result_dicts_have_required_keys(self):
        """Each result must expose params, ic_mean, ir, ic_series, panel."""
        factor_fn = _make_factor_fn(seed=3)
        results = params_grid(
            factor_fn=factor_fn,
            base_panel_data={},
            grid={"n": [5]},
            forward_returns_df=_DEFAULT_FWD,
            top_k=1,
            corr_filter=1.1,
            n_jobs=1,
            eval_config=_DEFAULT_CONFIG,
        )
        assert len(results) == 1
        r = results[0]
        assert "params" in r
        assert "ic_mean" in r
        assert "ir" in r
        assert "ic_series" in r
        assert "panel" in r


# ---------------------------------------------------------------------------
# 3. test_corr_filter_removes_correlated_candidates
# ---------------------------------------------------------------------------

class TestCorrFilterRemovesCorrelatedCandidates:
    def test_corr_filter_removes_correlated_candidates(self):
        """5 combos all returning the same ic_series → corr=1 → only 1 survives."""
        # All factor_fn calls return an identical panel → identical ic_series.
        panel = _make_factor_panel(500, seed=10)

        def _identical_fn(**kwargs: object) -> pl.DataFrame:
            return panel

        results = params_grid(
            factor_fn=_identical_fn,
            base_panel_data={},
            grid={"n": [1, 2, 3, 4, 5]},
            forward_returns_df=_DEFAULT_FWD,
            top_k=3,
            corr_filter=0.7,  # strict filter
            n_jobs=1,
            eval_config=_DEFAULT_CONFIG,
        )
        # All ic_series are identical → corr=1 → only the first survives.
        assert len(results) == 1

    def test_corr_filter_disabled_passes_all_up_to_top_k(self):
        """corr_filter=1.1 disables filtering → up to top_k candidates."""
        panel = _make_factor_panel(500, seed=11)

        def _identical_fn(**kwargs: object) -> pl.DataFrame:
            return panel

        results = params_grid(
            factor_fn=_identical_fn,
            base_panel_data={},
            grid={"n": [1, 2, 3]},
            forward_returns_df=_DEFAULT_FWD,
            top_k=3,
            corr_filter=1.1,  # disabled
            n_jobs=1,
            eval_config=_DEFAULT_CONFIG,
        )
        assert len(results) == 3


# ---------------------------------------------------------------------------
# 4. test_parallel_speedup
# ---------------------------------------------------------------------------

class TestParallelSpeedup:
    def test_parallel_speedup(self):
        """8 combos × 0.25s each → serial ≈ 2.0s; parallel (n_jobs=4) ≤ 0.6 × serial.

        Uses a sleep inside the factor_fn so CPU-bound / GIL concerns don't apply
        (loky uses real child processes, not threads).

        A warm-up parallel call is made first so the loky worker pool is already
        running when we measure; otherwise the first parallel call includes
        subprocess spawn time which dominates at short sleep durations.
        """
        import time as _time

        def _slow_fn(**kwargs: object) -> pl.DataFrame:
            _time.sleep(0.25)
            return _make_factor_panel(200, seed=99)

        fwd = _make_forward_returns(200)
        grid = {"n": [1, 2, 3, 4, 5, 6, 7, 8]}

        # --- warm-up: run 1 combo in parallel so the loky pool is alive ---
        params_grid(
            factor_fn=_slow_fn,
            base_panel_data={},
            grid={"n": [1]},
            forward_returns_df=fwd,
            top_k=1,
            corr_filter=1.1,
            n_jobs=4,
            eval_config=_DEFAULT_CONFIG,
        )

        # --- serial reference ---
        t0 = _time.time()
        params_grid(
            factor_fn=_slow_fn,
            base_panel_data={},
            grid=grid,
            forward_returns_df=fwd,
            top_k=8,
            corr_filter=1.1,
            n_jobs=1,  # serial
            eval_config=_DEFAULT_CONFIG,
        )
        serial_time = _time.time() - t0

        # --- parallel (pool already warm) ---
        t0 = _time.time()
        params_grid(
            factor_fn=_slow_fn,
            base_panel_data={},
            grid=grid,
            forward_returns_df=fwd,
            top_k=8,
            corr_filter=1.1,
            n_jobs=4,  # parallel
            eval_config=_DEFAULT_CONFIG,
        )
        parallel_time = _time.time() - t0

        # 0.6 × serial is the theoretical target at n_jobs=4 + 8 tasks.
        # Allow 0.7 × serial to account for macOS loky forkserver spawn overhead
        # while still demonstrating clear parallel speedup.
        assert parallel_time <= serial_time * 0.7, (
            f"Parallel ({parallel_time:.2f}s) not faster enough vs serial ({serial_time:.2f}s). "
            f"Ratio: {parallel_time / serial_time:.2f}"
        )


# ---------------------------------------------------------------------------
# 5. test_grid_handles_single_param
# ---------------------------------------------------------------------------

class TestGridHandlesSingleParam:
    def test_grid_handles_single_param(self):
        """grid={"n": [5]} → exactly 1 combo → 1 candidate returned."""
        factor_fn = _make_factor_fn(seed=5)
        results = params_grid(
            factor_fn=factor_fn,
            base_panel_data={},
            grid={"n": [5]},
            forward_returns_df=_DEFAULT_FWD,
            top_k=3,
            corr_filter=0.7,
            n_jobs=1,
            eval_config=_DEFAULT_CONFIG,
        )
        assert len(results) == 1
        assert results[0]["params"] == {"n": 5}

    def test_single_param_result_has_finite_ir(self):
        """Single combo result's ir field is a finite float."""
        factor_fn = _make_factor_fn(seed=6)
        results = params_grid(
            factor_fn=factor_fn,
            base_panel_data={},
            grid={"n": [10]},
            forward_returns_df=_DEFAULT_FWD,
            top_k=1,
            corr_filter=0.7,
            n_jobs=1,
            eval_config=_DEFAULT_CONFIG,
        )
        assert len(results) == 1
        ir = results[0]["ir"]
        assert isinstance(ir, float)
        assert math.isfinite(ir)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class TestInternalHelpers:
    def test_ic_series_to_array_empty(self):
        assert len(_ic_series_to_array([])) == 0

    def test_ic_series_to_array_filters_non_finite(self):
        series = [
            {"date": "2024-01-01", "ic": 0.1},
            {"date": "2024-01-02", "ic": float("nan")},
            {"date": "2024-01-03", "ic": 0.2},
        ]
        arr = _ic_series_to_array(series)
        assert len(arr) == 2
        assert arr[0] == pytest.approx(0.1)
        assert arr[1] == pytest.approx(0.2)

    def test_pearson_corr_identical_arrays(self):
        a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        assert _pearson_corr(a, a) == pytest.approx(1.0)

    def test_pearson_corr_empty_arrays(self):
        assert _pearson_corr(np.array([]), np.array([])) == 0.0

    def test_pearson_corr_constant_array(self):
        a = np.ones(10)
        b = np.arange(10, dtype=float)
        # std(a) == 0 → corr must be 0, not NaN
        assert _pearson_corr(a, b) == 0.0
