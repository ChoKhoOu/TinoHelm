"""Unit tests — ``tinohelm.factor.evaluation.orthogonalize``.

Locks the AC-2.7.1 contract:

* ``orthogonalize(panels, reference_idx)`` accepts a list of ≥ 2 factor
  panels and returns the residual panel of the first non-reference
  factor.
* Residual shape preserved (T × N + ts column).
* The reference panel is **not** in the output.
* Golden test (AC-2.7.1):
    ``A = 2 B + ε`` (ε ~ N(0, 1e-3)) → ``orthogonalize([A, B], reference_idx=1)``
    output's cross-section Spearman IC vs B has |IC| < 0.05.

Pure-logic, deterministic (fixed seed), polars + scipy-backed, < 1s.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest
from scipy.stats import spearmanr

from tinohelm.factor.evaluation.orthogonalize import (
    orthogonalize,
    orthogonalize_many,
)


# ──────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────


def _make_panel(values: np.ndarray, sym_prefix: str = "s") -> pl.DataFrame:
    T, N = values.shape
    start = dt.datetime(2024, 1, 1)
    ts = [start + dt.timedelta(hours=i) for i in range(T)]
    data = {"ts": ts}
    for j in range(N):
        data[f"{sym_prefix}{j}"] = values[:, j].tolist()
    return pl.DataFrame(data)


def _values_from(panel: pl.DataFrame) -> np.ndarray:
    cols = [c for c in panel.columns if c != "ts"]
    return panel.select(cols).to_numpy()


# ──────────────────────────────────────────────────────────────────────
# Golden orthogonality test (AC-2.7.1)
# ──────────────────────────────────────────────────────────────────────


class TestGoldenOrthogonal:
    def test_a_equals_2b_plus_noise_residual_decorrelates_from_b(self):
        """The headline AC-2.7.1 contract — verbatim from the spec."""
        np.random.seed(42)
        T, N = 50, 10
        B_data = np.random.randn(T, N)
        eps = np.random.randn(T, N) * 1e-3
        A_data = 2.0 * B_data + eps  # A = 2 B + ε

        A = _make_panel(A_data)
        B = _make_panel(B_data)

        # AC-2.7.1: orthogonalize([A, B], reference_idx=1) returns A's residual.
        residual_A = orthogonalize([A, B], reference_idx=1)
        residual_arr = _values_from(residual_A)

        # Per-timestamp Spearman IC between residual and B.
        ic_per_ts = []
        for t in range(T):
            r_row = residual_arr[t]
            b_row = B_data[t]
            mask = np.isfinite(r_row) & np.isfinite(b_row)
            if int(mask.sum()) < 3:
                continue
            rho, _ = spearmanr(r_row[mask], b_row[mask])
            if np.isfinite(rho):
                ic_per_ts.append(float(rho))

        assert len(ic_per_ts) > 0, "orthogonalize must produce at least one valid timestamp"
        mean_ic = float(np.mean(ic_per_ts))
        # |IC| < 0.05 — orthogonality contract.
        assert abs(mean_ic) < 0.05, f"residual IC = {mean_ic} (expected |IC| < 0.05)"

    def test_residual_is_smaller_in_magnitude_than_input(self):
        """Sanity: the residual shouldn't *amplify* — its energy ≤ input's energy."""
        np.random.seed(7)
        T, N = 40, 8
        B = np.random.randn(T, N)
        A = 2.0 * B + 0.001 * np.random.randn(T, N)
        residual = orthogonalize([_make_panel(A), _make_panel(B)], reference_idx=1)
        r_arr = _values_from(residual)
        # Energy of residual is well below input — A is almost exactly 2B.
        assert np.var(r_arr) < np.var(A)


# ──────────────────────────────────────────────────────────────────────
# Shape preservation
# ──────────────────────────────────────────────────────────────────────


class TestShapePreserved:
    def test_residual_shape_matches_target(self):
        rng = np.random.default_rng(11)
        T, N = 25, 6
        A = rng.normal(0, 1, (T, N))
        B = rng.normal(0, 1, (T, N))
        out = orthogonalize([_make_panel(A), _make_panel(B)], reference_idx=1)
        # Output is (T, N+1) wide (ts col + N symbols).
        assert out.height == T
        assert out.width == N + 1
        assert "ts" in out.columns

    def test_panel_without_ts_column(self):
        rng = np.random.default_rng(12)
        T, N = 20, 5
        A = rng.normal(0, 1, (T, N))
        B = rng.normal(0, 1, (T, N))
        # Build panels without the ts column to exercise the fallback path.
        a_no_ts = pl.DataFrame({f"s{j}": A[:, j].tolist() for j in range(N)})
        b_no_ts = pl.DataFrame({f"s{j}": B[:, j].tolist() for j in range(N)})
        out = orthogonalize([a_no_ts, b_no_ts], reference_idx=1)
        assert out.height == T
        assert out.width == N
        assert "ts" not in out.columns


# ──────────────────────────────────────────────────────────────────────
# Reference exclusion
# ──────────────────────────────────────────────────────────────────────


class TestReferenceExclusion:
    def test_reference_idx_skipped_in_output(self):
        rng = np.random.default_rng(13)
        T, N = 20, 4
        A = rng.normal(0, 1, (T, N))
        B = rng.normal(0, 1, (T, N))
        # Identify the reference panel by hash; the residual cannot equal it.
        a_panel = _make_panel(A)
        b_panel = _make_panel(B)
        out = orthogonalize([a_panel, b_panel], reference_idx=1)
        out_arr = _values_from(out)
        # Output ≠ reference (B) values.
        assert not np.allclose(out_arr, B)

    def test_orthogonalize_many_returns_all_non_reference_residuals(self):
        rng = np.random.default_rng(14)
        T, N = 18, 5
        B = rng.normal(0, 1, (T, N))
        A = 2.0 * B + 0.01 * rng.normal(0, 1, (T, N))
        C = -1.5 * B + 0.01 * rng.normal(0, 1, (T, N))
        residuals = orthogonalize_many(
            [_make_panel(A), _make_panel(B), _make_panel(C)],
            reference_idx=1,
        )
        # Two non-reference panels → two residuals.
        assert len(residuals) == 2
        # Both residuals share shape (T, N+1) with ts column.
        for r in residuals:
            assert r.height == T
            assert r.width == N + 1


# ──────────────────────────────────────────────────────────────────────
# Validation contracts
# ──────────────────────────────────────────────────────────────────────


class TestValidation:
    def test_too_few_panels_raise(self):
        rng = np.random.default_rng(15)
        only_one = _make_panel(rng.normal(0, 1, (5, 3)))
        with pytest.raises(ValueError, match="at least 2 panels"):
            orthogonalize([only_one], reference_idx=0)

    def test_invalid_reference_idx_raises(self):
        rng = np.random.default_rng(16)
        a = _make_panel(rng.normal(0, 1, (5, 3)))
        b = _make_panel(rng.normal(0, 1, (5, 3)))
        with pytest.raises(IndexError, match="out of range"):
            orthogonalize([a, b], reference_idx=5)
        with pytest.raises(IndexError, match="out of range"):
            orthogonalize([a, b], reference_idx=-1)

    def test_mismatched_symbol_columns_raise(self):
        rng = np.random.default_rng(17)
        a = _make_panel(rng.normal(0, 1, (5, 3)))
        # Build a panel with a different symbol column set.
        bad = pl.DataFrame({"ts": a["ts"], "different_sym": [1.0, 2.0, 3.0, 4.0, 5.0]})
        with pytest.raises(ValueError, match="symbol columns"):
            orthogonalize([a, bad], reference_idx=1)

    def test_mismatched_ts_presence_raises(self):
        rng = np.random.default_rng(18)
        a = _make_panel(rng.normal(0, 1, (5, 3)))
        b_no_ts = pl.DataFrame({f"s{j}": rng.normal(0, 1, 5).tolist() for j in range(3)})
        with pytest.raises(ValueError, match="'ts'"):
            orthogonalize([a, b_no_ts], reference_idx=1)


# ──────────────────────────────────────────────────────────────────────
# Numerical edge cases
# ──────────────────────────────────────────────────────────────────────


class TestNumericalEdges:
    def test_constant_reference_falls_back_to_demean(self):
        # If reference is constant in every cross-section, residual = target − target_mean
        T, N = 4, 5
        constant_ref = np.full((T, N), 1.0)
        rng = np.random.default_rng(19)
        target = rng.normal(0, 1, (T, N))
        out = orthogonalize([_make_panel(target), _make_panel(constant_ref)], reference_idx=1)
        out_arr = _values_from(out)
        # Each row of the residual should be the demeaned target row.
        for t in range(T):
            row_mean = float(np.mean(target[t]))
            assert out_arr[t] == pytest.approx(target[t] - row_mean, rel=1e-12)
