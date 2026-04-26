"""Unit tests — ``tinohelm.factor.evaluation.composition``.

Locks the AC-2.5.1 contract:

* ``combine_factors`` supports the four canonical methods (``equal``,
  ``ic_weighted``, ``ir_weighted``, ``ledoit_wolf``).
* All four methods return a panel matching the input shape.
* All-zero / all-NaN input panels collapse to all-zero / all-NaN output
  without raising — explicitly required by the AC.
* IC-weighted respects the per-factor IC mean ranking (factor with
  larger IC has larger absolute weight).

Pure-logic, deterministic, polars + sklearn-backed, < 2s total.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from tinohelm.factor.evaluation.composition import combine_factors


# ──────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────


def _make_panel(values: np.ndarray) -> pl.DataFrame:
    T, N = values.shape
    start = dt.datetime(2024, 1, 1)
    ts = [start + dt.timedelta(hours=i) for i in range(T)]
    data = {"ts": ts}
    for j in range(N):
        data[f"s{j}"] = values[:, j].tolist()
    return pl.DataFrame(data)


def _values_from(panel: pl.DataFrame) -> np.ndarray:
    cols = [c for c in panel.columns if c != "ts"]
    return panel.select(cols).to_numpy()


# ──────────────────────────────────────────────────────────────────────
# Equal-weight composition
# ──────────────────────────────────────────────────────────────────────


class TestEqualCombine:
    def test_simple_average_of_three_panels(self):
        rng = np.random.default_rng(0)
        T, N = 12, 4
        a = rng.normal(0, 1, (T, N))
        b = rng.normal(0, 1, (T, N))
        c = rng.normal(0, 1, (T, N))
        panels = {
            "a": _make_panel(a),
            "b": _make_panel(b),
            "c": _make_panel(c),
        }
        out = combine_factors(panels, method="equal")
        out_arr = _values_from(out)
        expected = (a + b + c) / 3.0
        assert out_arr == pytest.approx(expected, rel=1e-12)

    def test_preserves_ts_column(self):
        a = _make_panel(np.zeros((5, 3)))
        b = _make_panel(np.zeros((5, 3)))
        out = combine_factors({"a": a, "b": b}, method="equal")
        assert "ts" in out.columns
        assert out["ts"].to_list() == a["ts"].to_list()

    def test_output_shape_matches_input(self):
        a = _make_panel(np.ones((10, 6)))
        b = _make_panel(2 * np.ones((10, 6)))
        out = combine_factors({"a": a, "b": b}, method="equal")
        assert out.height == 10
        # Width = 1 (ts) + 6 (symbol cols).
        assert out.width == 7
        out_arr = _values_from(out)
        assert out_arr == pytest.approx(np.full((10, 6), 1.5), rel=1e-12)


# ──────────────────────────────────────────────────────────────────────
# IC-weighted composition
# ──────────────────────────────────────────────────────────────────────


class TestICWeighted:
    def test_factor_with_larger_ic_dominates(self):
        rng = np.random.default_rng(1)
        T, N = 8, 3
        # a is constant 1.0, b is constant 2.0 — easy to verify weighting.
        a = np.full((T, N), 1.0)
        b = np.full((T, N), 2.0)
        panels = {"a": _make_panel(a), "b": _make_panel(b)}
        # f1 dominates: ic_means a=0.10, b=0.05 → weight a:b = 0.10/0.15 : 0.05/0.15
        out = combine_factors(
            panels,
            method="ic_weighted",
            ic_means={"a": 0.10, "b": 0.05},
        )
        out_arr = _values_from(out)
        # composite = (0.10/0.15)*1.0 + (0.05/0.15)*2.0 = 4/3 ≈ 1.3333…
        expected = (0.10 / 0.15) * 1.0 + (0.05 / 0.15) * 2.0
        assert out_arr == pytest.approx(np.full((T, N), expected), rel=1e-12)
        del rng

    def test_negative_ic_factor_flips_sign(self):
        # Negative IC → weight is negative; abs-normalised so weight magnitude
        # contributes correctly. Composite = w_a * a + w_b * b.
        T, N = 6, 2
        a = np.ones((T, N))
        b = np.ones((T, N))
        panels = {"a": _make_panel(a), "b": _make_panel(b)}
        out = combine_factors(
            panels,
            method="ic_weighted",
            ic_means={"a": 0.10, "b": -0.05},
        )
        out_arr = _values_from(out)
        # weights: a=0.10/0.15, b=-0.05/0.15 → composite = (0.10 - 0.05)/0.15 = 1/3
        expected = (0.10 - 0.05) / 0.15
        assert out_arr == pytest.approx(np.full((T, N), expected), rel=1e-12)

    def test_missing_ic_means_raises(self):
        a = _make_panel(np.ones((3, 2)))
        with pytest.raises(ValueError, match="ic_means"):
            combine_factors({"a": a}, method="ic_weighted")

    def test_zero_ic_input_yields_uniform_weights(self):
        # All zero IC means — abs-sum is 0, fallback to uniform weights.
        T, N = 5, 4
        a = np.full((T, N), 1.0)
        b = np.full((T, N), 3.0)
        panels = {"a": _make_panel(a), "b": _make_panel(b)}
        out = combine_factors(
            panels,
            method="ic_weighted",
            ic_means={"a": 0.0, "b": 0.0},
        )
        out_arr = _values_from(out)
        # Uniform 0.5/0.5 → composite = 0.5*1 + 0.5*3 = 2.0
        assert out_arr == pytest.approx(np.full((T, N), 2.0), rel=1e-12)


# ──────────────────────────────────────────────────────────────────────
# IR-weighted composition
# ──────────────────────────────────────────────────────────────────────


class TestIRWeighted:
    def test_higher_ir_factor_gets_more_weight(self):
        T, N = 5, 3
        a = np.full((T, N), 4.0)
        b = np.full((T, N), 2.0)
        panels = {"a": _make_panel(a), "b": _make_panel(b)}
        out = combine_factors(
            panels,
            method="ir_weighted",
            ic_irs={"a": 1.5, "b": 0.5},
        )
        out_arr = _values_from(out)
        # weights: 1.5/2.0 : 0.5/2.0 → composite = 0.75*4 + 0.25*2 = 3.5
        expected = 0.75 * 4.0 + 0.25 * 2.0
        assert out_arr == pytest.approx(np.full((T, N), expected), rel=1e-12)

    def test_missing_ic_irs_raises(self):
        a = _make_panel(np.ones((3, 2)))
        with pytest.raises(ValueError, match="ic_irs"):
            combine_factors({"a": a}, method="ir_weighted")


# ──────────────────────────────────────────────────────────────────────
# Ledoit-Wolf composition
# ──────────────────────────────────────────────────────────────────────


class TestLedoitWolf:
    def test_returns_finite_panel(self):
        rng = np.random.default_rng(2)
        T, N = 12, 4
        a = rng.normal(0, 1, (T, N))
        b = rng.normal(0, 1, (T, N))
        panels = {"a": _make_panel(a), "b": _make_panel(b)}
        # Build a synthetic IC time-series — in real usage these come from
        # ``compute_ic_series``, but for the test any non-degenerate IC vector
        # exercises the Ledoit-Wolf code path.
        ic_a = rng.normal(0.05, 0.02, 200)
        ic_b = rng.normal(0.02, 0.03, 200)
        out = combine_factors(
            panels,
            method="ledoit_wolf",
            ic_time_series={"a": ic_a, "b": ic_b},
        )
        out_arr = _values_from(out)
        assert np.all(np.isfinite(out_arr))
        assert out_arr.shape == (T, N)

    def test_missing_ic_time_series_raises(self):
        a = _make_panel(np.ones((3, 2)))
        with pytest.raises(ValueError, match="ic_time_series"):
            combine_factors({"a": a}, method="ledoit_wolf")

    def test_unequal_ic_lengths_raise(self):
        a = _make_panel(np.ones((5, 3)))
        b = _make_panel(np.ones((5, 3)))
        with pytest.raises(ValueError, match="length"):
            combine_factors(
                {"a": a, "b": b},
                method="ledoit_wolf",
                ic_time_series={
                    "a": np.array([0.01, 0.02, 0.03], dtype=float),
                    "b": np.array([0.04, 0.05], dtype=float),
                },
            )


# ──────────────────────────────────────────────────────────────────────
# Edge-case contracts (AC-2.5.1)
# ──────────────────────────────────────────────────────────────────────


class TestZeroAndNaNInputs:
    @pytest.mark.parametrize("method", ["equal", "ic_weighted", "ir_weighted", "ledoit_wolf"])
    def test_all_zero_input_returns_all_zero(self, method):
        T, N = 6, 4
        zero = np.zeros((T, N))
        panels = {"a": _make_panel(zero), "b": _make_panel(zero)}
        kwargs = {}
        if method == "ic_weighted":
            kwargs["ic_means"] = {"a": 0.0, "b": 0.0}
        elif method == "ir_weighted":
            kwargs["ic_irs"] = {"a": 0.0, "b": 0.0}
        elif method == "ledoit_wolf":
            kwargs["ic_time_series"] = {
                "a": np.zeros(200),
                "b": np.zeros(200),
            }
        out = combine_factors(panels, method=method, **kwargs)
        out_arr = _values_from(out)
        # All zero — explicitly required by the AC, no NaN allowed.
        assert np.all(out_arr == 0.0), f"method={method}: got {out_arr}"

    @pytest.mark.parametrize("method", ["equal", "ic_weighted", "ir_weighted", "ledoit_wolf"])
    def test_all_nan_input_returns_all_nan(self, method):
        T, N = 6, 4
        nan = np.full((T, N), np.nan)
        panels = {"a": _make_panel(nan), "b": _make_panel(nan)}
        kwargs = {}
        if method == "ic_weighted":
            kwargs["ic_means"] = {"a": 0.05, "b": 0.05}
        elif method == "ir_weighted":
            kwargs["ic_irs"] = {"a": 0.5, "b": 0.5}
        elif method == "ledoit_wolf":
            rng = np.random.default_rng(3)
            kwargs["ic_time_series"] = {
                "a": rng.normal(0.05, 0.02, 200),
                "b": rng.normal(0.04, 0.03, 200),
            }
        # All-NaN input must not raise — AC-2.5.1.
        out = combine_factors(panels, method=method, **kwargs)
        out_arr = _values_from(out)
        assert np.all(np.isnan(out_arr)), f"method={method}: got {out_arr}"

    def test_unknown_method_raises(self):
        a = _make_panel(np.ones((3, 2)))
        with pytest.raises(ValueError, match="unknown composition method"):
            combine_factors({"a": a}, method="bogus")  # type: ignore[arg-type]

    def test_empty_panels_raise(self):
        with pytest.raises(ValueError, match="at least one panel"):
            combine_factors({}, method="equal")

    def test_mismatched_symbols_raise(self):
        a = _make_panel(np.ones((3, 2)))
        # Build a panel with different symbol cols.
        ts = a["ts"]
        bad = pl.DataFrame({"ts": ts, "different_sym": [1.0, 2.0, 3.0]})
        with pytest.raises(ValueError, match="symbol columns"):
            combine_factors({"a": a, "b": bad}, method="equal")
