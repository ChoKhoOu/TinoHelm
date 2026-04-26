"""Unit tests — ``tinohelm.factor.evaluation.correlation``.

Locks the AC-2.4.1 contract:

* ``correlation_matrix(panels)`` — same input twice → byte-identical
  output (deterministic).
* Self-correlation is exactly ``1.0``.
* Independent random panels yield near-zero off-diagonal values.
* Both ``cross_section`` and ``ic_time_series`` modes return the
  canonical ``(F, F+1)`` polars wide layout.

Pure-logic, deterministic (fixed seeds), polars-only, < 1s total.
"""
from __future__ import annotations

import datetime as dt
import hashlib

import numpy as np
import polars as pl
import pytest

from tinohelm.factor.evaluation.correlation import (
    correlation_matrix,
    correlation_matrix_cross_section,
    correlation_matrix_ic_time_series,
)


# ──────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────


def _make_panel(values: np.ndarray, sym_prefix: str = "s") -> pl.DataFrame:
    """Wrap a ``(T, N)`` numpy array into a wide ``[ts, s0, …, sN-1]`` panel."""
    T, N = values.shape
    start = dt.datetime(2024, 1, 1)
    ts = [start + dt.timedelta(hours=i) for i in range(T)]
    data = {"ts": ts}
    for j in range(N):
        data[f"{sym_prefix}{j}"] = values[:, j].tolist()
    return pl.DataFrame(data)


def _hash_df(df: pl.DataFrame) -> str:
    """Deterministic hash of a polars DataFrame for byte-equivalence checks."""
    csv_bytes = df.write_csv().encode()
    return hashlib.sha256(csv_bytes).hexdigest()


def _build_random_panels(F: int, T: int, N: int, seed: int) -> dict[str, pl.DataFrame]:
    """Build F independent random panels of shape (T, N)."""
    rng = np.random.default_rng(seed)
    panels: dict[str, pl.DataFrame] = {}
    for f in range(F):
        panels[f"f{f}"] = _make_panel(rng.normal(0, 1, (T, N)))
    return panels


# ──────────────────────────────────────────────────────────────────────
# correlation_matrix_cross_section
# ──────────────────────────────────────────────────────────────────────


class TestCrossSectionMatrix:
    def test_returns_FxF_layout(self):
        panels = _build_random_panels(F=3, T=10, N=5, seed=0)
        out = correlation_matrix_cross_section(panels)
        # Canonical wide layout: 1 factor_name col + F float cols → width F+1.
        assert out.height == 3
        assert out.width == 4
        assert out.columns == ["factor_name", "f0", "f1", "f2"]
        assert out["factor_name"].to_list() == ["f0", "f1", "f2"]

    def test_self_correlation_is_one(self):
        panels = _build_random_panels(F=3, T=20, N=8, seed=1)
        out = correlation_matrix_cross_section(panels)
        # Diagonal = 1.0 by construction.
        for name in ["f0", "f1", "f2"]:
            row = out.filter(pl.col("factor_name") == name)
            assert float(row[name].item()) == pytest.approx(1.0, rel=1e-12)

    def test_matrix_is_symmetric(self):
        panels = _build_random_panels(F=3, T=20, N=8, seed=2)
        out = correlation_matrix_cross_section(panels)
        # Cross-pairs match (within float epsilon).
        for i in range(3):
            for j in range(3):
                left = float(out.filter(pl.col("factor_name") == f"f{i}")[f"f{j}"].item())
                right = float(out.filter(pl.col("factor_name") == f"f{j}")[f"f{i}"].item())
                assert left == pytest.approx(right, abs=1e-12)

    def test_independent_factors_correlate_near_zero(self):
        # Independent random panels — pairwise spearman should average near 0.
        panels = _build_random_panels(F=3, T=200, N=20, seed=3)
        out = correlation_matrix_cross_section(panels)
        # Off-diagonal cells should be near zero (< 0.10 magnitude on 200 samples).
        off_diag = []
        for name in ["f0", "f1", "f2"]:
            for col in ["f0", "f1", "f2"]:
                if name == col:
                    continue
                v = float(out.filter(pl.col("factor_name") == name)[col].item())
                off_diag.append(v)
        assert max(abs(v) for v in off_diag) < 0.20

    def test_perfect_copy_yields_correlation_one(self):
        rng = np.random.default_rng(4)
        T, N = 30, 10
        base = rng.normal(0, 1, (T, N))
        panels = {
            "factor_a": _make_panel(base),
            "factor_b": _make_panel(base.copy()),
        }
        out = correlation_matrix_cross_section(panels)
        cross = float(out.filter(pl.col("factor_name") == "factor_a")["factor_b"].item())
        assert cross == pytest.approx(1.0, rel=1e-12)

    def test_anti_correlated_factors_yield_minus_one(self):
        rng = np.random.default_rng(5)
        T, N = 30, 10
        base = rng.normal(0, 1, (T, N))
        panels = {
            "factor_a": _make_panel(base),
            "factor_b": _make_panel(-base),
        }
        out = correlation_matrix_cross_section(panels)
        cross = float(out.filter(pl.col("factor_name") == "factor_a")["factor_b"].item())
        assert cross == pytest.approx(-1.0, rel=1e-12)

    def test_deterministic_byte_equivalent(self):
        # AC-2.4.1 — same input twice → output hash identical.
        panels1 = _build_random_panels(F=4, T=50, N=10, seed=7)
        panels2 = _build_random_panels(F=4, T=50, N=10, seed=7)
        out1 = correlation_matrix_cross_section(panels1)
        out2 = correlation_matrix_cross_section(panels2)
        assert _hash_df(out1) == _hash_df(out2)


# ──────────────────────────────────────────────────────────────────────
# correlation_matrix_ic_time_series
# ──────────────────────────────────────────────────────────────────────


class TestICTimeSeriesMatrix:
    def test_returns_FxF_layout(self):
        rng = np.random.default_rng(11)
        ic_series = {f"f{i}": rng.normal(0, 0.05, 100) for i in range(3)}
        out = correlation_matrix_ic_time_series(ic_series)
        assert out.height == 3
        assert out.width == 4
        assert out.columns == ["factor_name", "f0", "f1", "f2"]

    def test_self_correlation_is_one(self):
        rng = np.random.default_rng(12)
        ic_series = {f"f{i}": rng.normal(0, 0.05, 200) for i in range(3)}
        out = correlation_matrix_ic_time_series(ic_series)
        for name in ["f0", "f1", "f2"]:
            row = out.filter(pl.col("factor_name") == name)
            assert float(row[name].item()) == pytest.approx(1.0, rel=1e-12)

    def test_perfectly_aligned_ic_series_yield_one(self):
        rng = np.random.default_rng(13)
        ic = rng.normal(0, 0.05, 200)
        ic_series = {"f0": ic, "f1": ic.copy()}
        out = correlation_matrix_ic_time_series(ic_series)
        cross = float(out.filter(pl.col("factor_name") == "f0")["f1"].item())
        assert cross == pytest.approx(1.0, rel=1e-12)

    def test_unequal_lengths_raise(self):
        ic_series = {
            "f0": np.array([0.01, 0.02, 0.03], dtype=float),
            "f1": np.array([0.04, 0.05], dtype=float),
        }
        with pytest.raises(ValueError, match="length"):
            correlation_matrix_ic_time_series(ic_series)


# ──────────────────────────────────────────────────────────────────────
# correlation_matrix — high-level dispatch
# ──────────────────────────────────────────────────────────────────────


class TestCorrelationMatrixDispatch:
    def test_default_method_is_cross_section(self):
        panels = _build_random_panels(F=3, T=40, N=8, seed=21)
        default = correlation_matrix(panels)
        cs = correlation_matrix_cross_section(panels)
        assert _hash_df(default) == _hash_df(cs)

    def test_unknown_method_raises(self):
        panels = _build_random_panels(F=2, T=10, N=5, seed=22)
        with pytest.raises(ValueError, match="Unknown correlation method"):
            correlation_matrix(panels, method="bogus")  # type: ignore[arg-type]

    def test_ic_time_series_requires_forward_input(self):
        panels = _build_random_panels(F=2, T=10, N=5, seed=23)
        with pytest.raises(ValueError, match="forward"):
            correlation_matrix(panels, method="ic_time_series")

    def test_ic_time_series_with_forward_panel(self):
        rng = np.random.default_rng(24)
        T, N = 60, 5
        panels = {
            "f0": _make_panel(rng.normal(0, 1, (T, N))),
            "f1": _make_panel(rng.normal(0, 1, (T, N))),
        }
        fwd = _make_panel(rng.normal(0, 0.01, (T, N)))
        out = correlation_matrix(panels, method="ic_time_series", forward_returns_panel=fwd)
        # Self-corr exactly 1, off-diag is finite.
        for name in ["f0", "f1"]:
            row = out.filter(pl.col("factor_name") == name)
            assert float(row[name].item()) == pytest.approx(1.0, rel=1e-12)
        cross = float(out.filter(pl.col("factor_name") == "f0")["f1"].item())
        assert -1.0 <= cross <= 1.0
