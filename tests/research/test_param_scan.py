"""Tests for `tinohelm.research.param_scan` — 1D sweep + 2D heatmap.

Covers the new ``build_ic_matrix`` pure helper (extracted so it's testable
without spawning a ProcessPoolExecutor) and the worker functions
``_sweep_worker`` / ``_heatmap_worker`` invoked synchronously.

The full ``sweep_1d`` / ``sweep_2d`` orchestrators wrap these in a process pool;
we don't run them end-to-end in unit tests because pickling a DataFrame and
spawning workers is slow and provides no extra coverage beyond the helpers.
"""
from __future__ import annotations

import pickle

import numpy as np
import pandas as pd
import pytest

from tinohelm.research import param_scan as PS


# ──────────────────────────────────────────────────────────────────────
# build_ic_matrix (the new pure helper)
# ──────────────────────────────────────────────────────────────────────


class TestBuildIcMatrix:
    def test_basic_2x2_matrix(self):
        results = [
            {"p1": 1, "p2": 10, "ic": 0.1},
            {"p1": 1, "p2": 20, "ic": 0.2},
            {"p1": 2, "p2": 10, "ic": 0.3},
            {"p1": 2, "p2": 20, "ic": 0.4},
        ]
        out = PS.build_ic_matrix(results, [1, 2], [10, 20])
        assert out == [[0.1, 0.2], [0.3, 0.4]]

    def test_results_in_arbitrary_order(self):
        # build_ic_matrix must reorder by (p1, p2) regardless of input order.
        results = [
            {"p1": 2, "p2": 20, "ic": 0.4},
            {"p1": 1, "p2": 10, "ic": 0.1},
            {"p1": 2, "p2": 10, "ic": 0.3},
            {"p1": 1, "p2": 20, "ic": 0.2},
        ]
        out = PS.build_ic_matrix(results, [1, 2], [10, 20])
        assert out == [[0.1, 0.2], [0.3, 0.4]]

    def test_missing_cell_filled_with_zero(self):
        # If a worker dropped a cell, downstream Plotly heatmap shouldn't crash —
        # missing entries default to 0.0.
        results = [
            {"p1": 1, "p2": 10, "ic": 0.1},
            {"p1": 2, "p2": 20, "ic": 0.4},
        ]
        out = PS.build_ic_matrix(results, [1, 2], [10, 20])
        assert out == [[0.1, 0.0], [0.0, 0.4]]

    def test_results_with_error_field_still_used(self):
        # Workers may include an "error" key; the IC field still gets through.
        results = [
            {"p1": 1, "p2": 10, "ic": 0, "error": "boom"},
            {"p1": 1, "p2": 20, "ic": 0.5},
        ]
        out = PS.build_ic_matrix(results, [1], [10, 20])
        assert out == [[0.0, 0.5]]

    def test_missing_ic_treated_as_zero(self):
        results = [{"p1": 1, "p2": 10}]  # no "ic" key
        out = PS.build_ic_matrix(results, [1], [10])
        assert out == [[0.0]]

    def test_empty_results_yields_zero_matrix(self):
        out = PS.build_ic_matrix([], [1, 2], [10, 20])
        assert out == [[0.0, 0.0], [0.0, 0.0]]

    def test_handles_float_param_values(self):
        results = [
            {"p1": 0.5, "p2": 1.5, "ic": 0.3},
            {"p1": 0.5, "p2": 2.5, "ic": 0.4},
        ]
        out = PS.build_ic_matrix(results, [0.5], [1.5, 2.5])
        assert out == [[0.3, 0.4]]

    def test_skips_results_missing_p1_or_p2(self):
        # Workers that didn't return their (p1, p2) coordinates are dropped.
        results = [
            {"p1": 1, "ic": 0.5},  # no p2
            {"p2": 10, "ic": 0.6},  # no p1
            {"p1": 1, "p2": 10, "ic": 0.7},
        ]
        out = PS.build_ic_matrix(results, [1], [10])
        assert out == [[0.7]]

    def test_matrix_dimensions_match_input_value_lengths(self):
        out = PS.build_ic_matrix([], [1, 2, 3], [10, 20])
        assert len(out) == 3
        assert all(len(row) == 2 for row in out)


# ──────────────────────────────────────────────────────────────────────
# _sweep_worker (1D, synchronous)
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_df():
    rng = np.random.default_rng(0)
    n = 200
    close = 100.0 + np.cumsum(rng.normal(0, 0.3, n))
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="1h"),
    )


class TestSweepWorker:
    def test_returns_value_and_ic(self, sample_df):
        df_bytes = pickle.dumps(sample_df)
        out = PS._sweep_worker(("ret_N", df_bytes, "lookback", 10, {}, 5))
        assert "value" in out
        assert "ic" in out
        assert out["value"] == 10
        assert isinstance(out["ic"], float)

    def test_unknown_factor_returns_error_field(self, sample_df):
        df_bytes = pickle.dumps(sample_df)
        out = PS._sweep_worker(("__nope__", df_bytes, "lookback", 10, {}, 5))
        assert out["ic"] == 0
        assert "error" in out

    def test_ic_finite_when_paired_data_sufficient(self, sample_df):
        df_bytes = pickle.dumps(sample_df)
        out = PS._sweep_worker(("ret_N", df_bytes, "lookback", 10, {}, 5))
        assert np.isfinite(out["ic"])


# ──────────────────────────────────────────────────────────────────────
# _heatmap_worker (2D, synchronous)
# ──────────────────────────────────────────────────────────────────────


class TestHeatmapWorker:
    def test_returns_p1_p2_ic_keys(self, sample_df):
        df_bytes = pickle.dumps(sample_df)
        out = PS._heatmap_worker(("mom_ratio", df_bytes, "fast", 5, "slow", 20, {}, 5))
        assert set(out.keys()) >= {"p1", "p2", "ic"}
        assert out["p1"] == 5
        assert out["p2"] == 20

    def test_unknown_factor_returns_error_field(self, sample_df):
        df_bytes = pickle.dumps(sample_df)
        out = PS._heatmap_worker(("__nope__", df_bytes, "fast", 5, "slow", 20, {}, 5))
        assert out["ic"] == 0
        assert "error" in out
        assert out["p1"] == 5
        assert out["p2"] == 20

    def test_short_history_returns_zero_ic(self):
        # Tiny df — fewer than 30 valid pairs after dropna → IC = 0
        rng = np.random.default_rng(1)
        n = 25
        df_short = pd.DataFrame(
            {
                "open": [100.0] * n,
                "high": [101.0] * n,
                "low": [99.0] * n,
                "close": rng.normal(100, 1, n),
                "volume": [1.0] * n,
            }
        )
        df_bytes = pickle.dumps(df_short)
        out = PS._heatmap_worker(("ret_N", df_bytes, "lookback", 10, "lookback", 20, {}, 5))
        assert out["ic"] == 0
