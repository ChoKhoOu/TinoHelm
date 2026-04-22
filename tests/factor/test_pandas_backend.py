"""Unit tests for ``tinohelm.factor.backend.PandasBackend``.

Coverage
--------
- shift: matches ``panel.shift(n)`` exactly
- rolling: mean/sum/std/min/max correctness; min_periods support
- rolling: invalid op raises ValueError
- diff: matches ``panel.diff(n)``
- pct_change: matches ``panel.pct_change(n)``
- ewm: mean and std; mutual-exclusion guard
- rank axis=0 (cross-sectional): pct values in [0, 1] per row
- rank axis=1 (time-series): pct values in [0, 1] per column
- rank NaN pass-through
- clip: low/high bounds enforced; None bounds no-op
- zscore axis=0: row means ≈ 0, row stds ≈ 1
- zscore axis=1: column means ≈ 0, column stds ≈ 1
- log: matches np.log element-wise
- abs: matches DataFrame.abs()
- AbstractBackend is abstract (cannot instantiate directly)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tinohelm.factor.backend import AbstractBackend, PandasBackend
from tinohelm.factor.backend.base import AbstractBackend as _AbstractBackend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def backend() -> PandasBackend:
    return PandasBackend()


@pytest.fixture
def panel() -> pd.DataFrame:
    """5-row × 3-symbol panel with no NaN."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    symbols = ["BTC", "ETH", "SOL"]
    return pd.DataFrame(
        rng.uniform(100, 200, size=(5, 3)),
        index=dates,
        columns=symbols,
    )


@pytest.fixture
def panel_with_nan() -> pd.DataFrame:
    """5-row × 3-symbol panel where first row has a NaN."""
    rng = np.random.default_rng(7)
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    symbols = ["BTC", "ETH", "SOL"]
    data = rng.uniform(50, 150, size=(5, 3))
    data[0, 1] = np.nan  # ETH row-0 is NaN
    return pd.DataFrame(data, index=dates, columns=symbols)


# ---------------------------------------------------------------------------
# AbstractBackend is abstract
# ---------------------------------------------------------------------------

class TestAbstractBackend:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            _AbstractBackend()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# shift
# ---------------------------------------------------------------------------

class TestShift:
    def test_shift_positive_matches_pandas(self, backend, panel):
        result = backend.shift(panel, 3)
        expected = panel.shift(3)
        pd.testing.assert_frame_equal(result, expected)

    def test_shift_zero_identity(self, backend, panel):
        result = backend.shift(panel, 0)
        pd.testing.assert_frame_equal(result, panel)

    def test_shift_negative_matches_pandas(self, backend, panel):
        result = backend.shift(panel, -1)
        expected = panel.shift(-1)
        pd.testing.assert_frame_equal(result, expected)

    def test_shift_introduces_nan_at_start(self, backend, panel):
        result = backend.shift(panel, 2)
        # First 2 rows must all be NaN
        assert result.iloc[:2].isna().all(axis=None)

    def test_shift_does_not_mutate_input(self, backend, panel):
        original = panel.copy()
        backend.shift(panel, 2)
        pd.testing.assert_frame_equal(panel, original)


# ---------------------------------------------------------------------------
# rolling
# ---------------------------------------------------------------------------

class TestRolling:
    def test_rolling_mean_matches_pandas(self, backend, panel):
        result = backend.rolling(panel, 3, "mean")
        expected = panel.rolling(3).mean()
        pd.testing.assert_frame_equal(result, expected)

    def test_rolling_sum_matches_pandas(self, backend, panel):
        result = backend.rolling(panel, 2, "sum")
        expected = panel.rolling(2).sum()
        pd.testing.assert_frame_equal(result, expected)

    def test_rolling_std_matches_pandas(self, backend, panel):
        result = backend.rolling(panel, 3, "std")
        expected = panel.rolling(3).std()
        pd.testing.assert_frame_equal(result, expected)

    def test_rolling_min_matches_pandas(self, backend, panel):
        result = backend.rolling(panel, 2, "min")
        expected = panel.rolling(2).min()
        pd.testing.assert_frame_equal(result, expected)

    def test_rolling_max_matches_pandas(self, backend, panel):
        result = backend.rolling(panel, 2, "max")
        expected = panel.rolling(2).max()
        pd.testing.assert_frame_equal(result, expected)

    def test_rolling_min_periods(self, backend, panel):
        """With min_periods=1, no NaN in output for a window-2 rolling mean."""
        result = backend.rolling(panel, 2, "mean", min_periods=1)
        # First row should not be NaN (only 1 observation, but min_periods=1)
        assert not result.iloc[0].isna().any()

    def test_rolling_invalid_op_raises(self, backend, panel):
        with pytest.raises(ValueError, match="Unsupported rolling op"):
            backend.rolling(panel, 3, "variance")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------

class TestDiff:
    def test_diff_default_matches_pandas(self, backend, panel):
        result = backend.diff(panel)
        expected = panel.diff()
        pd.testing.assert_frame_equal(result, expected)

    def test_diff_n2_matches_pandas(self, backend, panel):
        result = backend.diff(panel, 2)
        expected = panel.diff(2)
        pd.testing.assert_frame_equal(result, expected)


# ---------------------------------------------------------------------------
# pct_change
# ---------------------------------------------------------------------------

class TestPctChange:
    def test_pct_change_default_matches_pandas(self, backend, panel):
        result = backend.pct_change(panel)
        expected = panel.pct_change()
        pd.testing.assert_frame_equal(result, expected)

    def test_pct_change_n3_matches_pandas(self, backend, panel):
        result = backend.pct_change(panel, 3)
        expected = panel.pct_change(3)
        pd.testing.assert_frame_equal(result, expected)


# ---------------------------------------------------------------------------
# ewm
# ---------------------------------------------------------------------------

class TestEwm:
    def test_ewm_span_mean_matches_pandas(self, backend, panel):
        result = backend.ewm(panel, span=3, op="mean")
        expected = panel.ewm(span=3).mean()
        pd.testing.assert_frame_equal(result, expected)

    def test_ewm_alpha_mean_matches_pandas(self, backend, panel):
        result = backend.ewm(panel, alpha=0.5, op="mean")
        expected = panel.ewm(alpha=0.5).mean()
        pd.testing.assert_frame_equal(result, expected)

    def test_ewm_span_std_matches_pandas(self, backend, panel):
        result = backend.ewm(panel, span=3, op="std")
        expected = panel.ewm(span=3).std()
        pd.testing.assert_frame_equal(result, expected)

    def test_ewm_both_span_and_alpha_raises(self, backend, panel):
        with pytest.raises(ValueError, match="Exactly one"):
            backend.ewm(panel, span=3, alpha=0.5)

    def test_ewm_neither_span_nor_alpha_raises(self, backend, panel):
        with pytest.raises(ValueError, match="Exactly one"):
            backend.ewm(panel)

    def test_ewm_invalid_op_raises(self, backend, panel):
        with pytest.raises(ValueError, match="Unsupported ewm op"):
            backend.ewm(panel, span=3, op="sum")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# rank
# ---------------------------------------------------------------------------

class TestRankCrossSectional:
    """axis=0 → cross-sectional rank (pandas axis=1)."""

    def test_rank_cross_sectional_values_in_01(self, backend, panel):
        result = backend.rank(panel, axis=0, pct=True)
        valid = result.dropna()
        assert (valid >= 0).all(axis=None)
        assert (valid <= 1).all(axis=None)

    def test_rank_cross_sectional_matches_pandas_axis1(self, backend, panel):
        """Verify axis translation: project axis=0 == pandas axis=1."""
        result = backend.rank(panel, axis=0, pct=True)
        expected = panel.rank(axis=1, pct=True, na_option="keep")
        pd.testing.assert_frame_equal(result, expected)

    def test_rank_cross_sectional_no_pct_integer_like(self, backend, panel):
        """Without pct, values should be integer-valued ranks (1 to N)."""
        result = backend.rank(panel, axis=0, pct=False)
        n_symbols = panel.shape[1]
        # All values should be between 1 and n_symbols
        assert (result >= 1).all(axis=None)
        assert (result <= n_symbols).all(axis=None)

    def test_rank_cross_sectional_nan_propagates(self, backend, panel_with_nan):
        result = backend.rank(panel_with_nan, axis=0, pct=True)
        # Row 0, col ETH was NaN → should remain NaN in rank
        assert np.isnan(result.iloc[0]["ETH"])


class TestRankTimeSeries:
    """axis=1 → time-series rank (pandas axis=0)."""

    def test_rank_time_series_values_in_01(self, backend, panel):
        result = backend.rank(panel, axis=1, pct=True)
        valid = result.dropna()
        assert (valid >= 0).all(axis=None)
        assert (valid <= 1).all(axis=None)

    def test_rank_time_series_matches_pandas_axis0(self, backend, panel):
        """Verify axis translation: project axis=1 == pandas axis=0."""
        result = backend.rank(panel, axis=1, pct=True)
        expected = panel.rank(axis=0, pct=True, na_option="keep")
        pd.testing.assert_frame_equal(result, expected)


# ---------------------------------------------------------------------------
# clip
# ---------------------------------------------------------------------------

class TestClip:
    def test_clip_both_bounds(self, backend, panel):
        """Values > 1 become 1; values < -1 become -1."""
        # Create a panel with values clearly outside [-1, 1]
        data = pd.DataFrame(
            {"A": [-3.0, -0.5, 0.0, 0.5, 3.0]},
            index=pd.date_range("2024-01-01", periods=5),
        )
        result = backend.clip(data, low=-1.0, high=1.0)
        assert result["A"].max() == pytest.approx(1.0)
        assert result["A"].min() == pytest.approx(-1.0)
        assert result["A"].iloc[1] == pytest.approx(-0.5)  # unchanged

    def test_clip_matches_pandas(self, backend, panel):
        result = backend.clip(panel, low=120.0, high=180.0)
        expected = panel.clip(lower=120.0, upper=180.0)
        pd.testing.assert_frame_equal(result, expected)

    def test_clip_no_low_bound(self, backend, panel):
        """low=None means no lower clipping."""
        result = backend.clip(panel, low=None, high=150.0)
        assert result.max(axis=None).max() == pytest.approx(150.0)

    def test_clip_no_high_bound(self, backend, panel):
        """high=None means no upper clipping."""
        result = backend.clip(panel, low=150.0, high=None)
        assert result.min(axis=None).min() == pytest.approx(150.0)

    def test_clip_no_bounds_identity(self, backend, panel):
        """Both None → no clipping → identical output."""
        result = backend.clip(panel, low=None, high=None)
        pd.testing.assert_frame_equal(result, panel)

    def test_clip_does_not_mutate_input(self, backend, panel):
        original = panel.copy()
        backend.clip(panel, low=130.0, high=170.0)
        pd.testing.assert_frame_equal(panel, original)


# ---------------------------------------------------------------------------
# zscore
# ---------------------------------------------------------------------------

class TestZscore:
    def test_zscore_cross_sectional_row_means_near_zero(self, backend, panel):
        result = backend.zscore(panel, axis=0)
        row_means = result.mean(axis=1)
        np.testing.assert_allclose(row_means.values, 0.0, atol=1e-10)

    def test_zscore_cross_sectional_row_stds_near_one(self, backend, panel):
        result = backend.zscore(panel, axis=0)
        row_stds = result.std(axis=1)
        np.testing.assert_allclose(row_stds.values, 1.0, atol=1e-10)

    def test_zscore_time_series_col_means_near_zero(self, backend, panel):
        result = backend.zscore(panel, axis=1)
        col_means = result.mean(axis=0)
        np.testing.assert_allclose(col_means.values, 0.0, atol=1e-10)

    def test_zscore_time_series_col_stds_near_one(self, backend, panel):
        result = backend.zscore(panel, axis=1)
        col_stds = result.std(axis=0)
        np.testing.assert_allclose(col_stds.values, 1.0, atol=1e-10)


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------

class TestLog:
    def test_log_positive_values(self, backend, panel):
        result = backend.log(panel)
        expected = pd.DataFrame(
            np.log(panel.values),
            index=panel.index,
            columns=panel.columns,
        )
        pd.testing.assert_frame_equal(result, expected)

    def test_log_preserves_shape(self, backend, panel):
        result = backend.log(panel)
        assert result.shape == panel.shape

    def test_log_preserves_index_and_columns(self, backend, panel):
        result = backend.log(panel)
        pd.testing.assert_index_equal(result.index, panel.index)
        pd.testing.assert_index_equal(result.columns, panel.columns)


# ---------------------------------------------------------------------------
# abs
# ---------------------------------------------------------------------------

class TestAbs:
    def test_abs_positive_unchanged(self, backend, panel):
        result = backend.abs(panel)
        pd.testing.assert_frame_equal(result, panel)

    def test_abs_negative_values(self, backend):
        data = pd.DataFrame(
            {"A": [-1.0, 0.0, 2.0], "B": [-3.0, -0.5, 1.5]},
            index=pd.date_range("2024-01-01", periods=3),
        )
        result = backend.abs(data)
        expected = data.abs()
        pd.testing.assert_frame_equal(result, expected)

    def test_abs_matches_pandas(self, backend, panel_with_nan):
        result = backend.abs(panel_with_nan)
        expected = panel_with_nan.abs()
        pd.testing.assert_frame_equal(result, expected)


# ---------------------------------------------------------------------------
# Import-level: PandasBackend is a subclass of AbstractBackend
# ---------------------------------------------------------------------------

class TestInheritance:
    def test_pandas_backend_is_subclass(self):
        assert issubclass(PandasBackend, AbstractBackend)

    def test_pandas_backend_is_instance(self, backend):
        assert isinstance(backend, AbstractBackend)
