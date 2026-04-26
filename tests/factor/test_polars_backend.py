"""Unit tests for ``tinohelm.factor.backend.PolarsBackend``.

Coverage (per task spec — 10 core algorithms)
---------------------------------------------
1. ``rolling`` — mean / std / sum
2. ``shift``
3. ``diff``
4. ``pct_change``
5. ``rank`` (axis=0 cross-sectional, axis=1 time-series)
6. ``zscore`` (axis=0 cross-sectional, axis=1 time-series)
7. ``clip``
8. ``fillna``

Plus the supplementary algorithms exposed by the
:class:`~tinohelm.factor.backend.base.AbstractBackend` Protocol:
``ewm``, ``log``, ``abs``.

Also asserts:
- ``isinstance(PolarsBackend(), AbstractBackend)`` succeeds via the
  ``@runtime_checkable`` Protocol decorator.
- Cross-sectional (``axis=0``) and time-series (``axis=1``) semantics
  are preserved separately by the rank/zscore operators.
- The returned panel preserves the original ``ts`` column and the
  original symbol-column ordering (regression test for the unpivot/pivot
  round-trip).
"""
from __future__ import annotations

import datetime as dt
import math

import polars as pl
import pytest

from tinohelm.factor.backend import AbstractBackend, PolarsBackend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

T_BARS = 20
N_SYMBOLS = 5
SYMBOL_COLS = ["BTC", "ETH", "SOL", "BNB", "XRP"]


@pytest.fixture
def backend() -> PolarsBackend:
    return PolarsBackend()


@pytest.fixture
def panel() -> pl.DataFrame:
    """Deterministic (T=20, N=5) panel with no nulls.

    Each symbol is a slow trend (``base + i + j * scale``) so cross-section
    and time-series stats are easy to reason about.
    """
    ts = pl.datetime_range(
        start=dt.datetime(2024, 1, 1),
        end=dt.datetime(2024, 1, 20),
        interval="1d",
        eager=True,
    )
    data: dict[str, list[float]] = {"ts": ts.to_list()}
    for j, sym in enumerate(SYMBOL_COLS):
        # Rising trend: each symbol has its own slope and offset.
        data[sym] = [100.0 + 5.0 * j + 1.0 * i + 0.1 * (i * j) for i in range(T_BARS)]
    return pl.DataFrame(data)


@pytest.fixture
def panel_with_null() -> pl.DataFrame:
    """Same shape as ``panel`` but injects nulls to validate null handling."""
    ts = pl.datetime_range(
        start=dt.datetime(2024, 1, 1),
        end=dt.datetime(2024, 1, 20),
        interval="1d",
        eager=True,
    )
    data: dict[str, list[float | None]] = {"ts": ts.to_list()}
    for j, sym in enumerate(SYMBOL_COLS):
        col = [100.0 + 5.0 * j + 1.0 * i + 0.1 * (i * j) for i in range(T_BARS)]
        data[sym] = col  # type: ignore[assignment]
    df = pl.DataFrame(data)
    # Null out a few cells to test propagation.
    df = df.with_columns(
        pl.when(pl.int_range(0, T_BARS).cast(pl.Int64) == 0)
        .then(None)
        .otherwise(pl.col("ETH"))
        .alias("ETH")
    )
    df = df.with_columns(
        pl.when(pl.int_range(0, T_BARS).cast(pl.Int64) == 5)
        .then(None)
        .otherwise(pl.col("SOL"))
        .alias("SOL")
    )
    return df


# ---------------------------------------------------------------------------
# Protocol / runtime_checkable
# ---------------------------------------------------------------------------


class TestProtocolRuntimeCheckable:
    def test_isinstance_check_passes(self, backend: PolarsBackend) -> None:
        """``@runtime_checkable`` Protocol enables structural ``isinstance``."""
        assert isinstance(backend, AbstractBackend)

    def test_polars_backend_does_not_subclass_protocol(self) -> None:
        """:class:`PolarsBackend` satisfies the Protocol structurally,
        without explicit subclassing — no ``ABC.register``-style coupling."""
        assert AbstractBackend not in PolarsBackend.__mro__


# ---------------------------------------------------------------------------
# Panel-shape preservation
# ---------------------------------------------------------------------------


class TestShapePreservation:
    def test_shift_preserves_ts_and_column_order(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.shift(panel, 3)
        assert result.columns == ["ts", *SYMBOL_COLS]
        assert result.height == panel.height
        # ts column is identical.
        assert result["ts"].to_list() == panel["ts"].to_list()

    def test_rank_axis0_preserves_column_order(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        """Cross-sectional rank goes through unpivot/pivot — verify column
        ordering survives the round-trip (this is the regression case)."""
        result = backend.rank(panel, axis=0, pct=True)
        assert result.columns == ["ts", *SYMBOL_COLS]
        assert result["ts"].to_list() == panel["ts"].to_list()

    def test_zscore_axis0_preserves_column_order(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.zscore(panel, axis=0)
        assert result.columns == ["ts", *SYMBOL_COLS]


# ---------------------------------------------------------------------------
# shift
# ---------------------------------------------------------------------------


class TestShift:
    def test_shift_positive_introduces_nulls_at_start(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.shift(panel, 2)
        # First 2 rows of every symbol column must be null.
        for col in SYMBOL_COLS:
            assert result[col][0] is None
            assert result[col][1] is None
            assert result[col][2] == panel[col][0]

    def test_shift_negative_introduces_nulls_at_end(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.shift(panel, -1)
        for col in SYMBOL_COLS:
            assert result[col][-1] is None
            assert result[col][0] == panel[col][1]

    def test_shift_zero_returns_identical_values(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.shift(panel, 0)
        for col in SYMBOL_COLS:
            assert result[col].to_list() == panel[col].to_list()

    def test_shift_does_not_mutate_input(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        original = panel.clone()
        backend.shift(panel, 3)
        assert panel.equals(original)


# ---------------------------------------------------------------------------
# rolling — mean / std / sum
# ---------------------------------------------------------------------------


class TestRolling:
    def test_rolling_mean_first_window_minus_one_rows_are_null(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.rolling(panel, window=3, op="mean")
        for col in SYMBOL_COLS:
            assert result[col][0] is None
            assert result[col][1] is None
            # 3rd row should equal the mean of the first 3 input rows.
            expected = sum(panel[col][:3].to_list()) / 3
            assert result[col][2] == pytest.approx(expected, rel=1e-12)

    def test_rolling_sum_value_correctness(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.rolling(panel, window=4, op="sum")
        for col in SYMBOL_COLS:
            expected = sum(panel[col][:4].to_list())
            assert result[col][3] == pytest.approx(expected, rel=1e-12)

    def test_rolling_std_value_correctness(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.rolling(panel, window=5, op="std")
        # Compare against polars' direct call (this is the contract).
        expected = panel.select(
            pl.col("BTC").rolling_std(window_size=5, min_samples=None).alias("BTC")
        )["BTC"].to_list()
        actual = result["BTC"].to_list()
        for a, e in zip(actual, expected):
            if a is None:
                assert e is None
            else:
                assert a == pytest.approx(e, rel=1e-12)

    def test_rolling_min_periods_one(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        """With ``min_periods=1`` the first row should already be filled."""
        result = backend.rolling(panel, window=3, op="mean", min_periods=1)
        for col in SYMBOL_COLS:
            assert result[col][0] == pytest.approx(panel[col][0], rel=1e-12)

    def test_rolling_invalid_op_raises(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        with pytest.raises(ValueError, match="Unsupported rolling op"):
            backend.rolling(panel, window=3, op="variance")  # type: ignore[arg-type]

    def test_rolling_invalid_window_raises(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        with pytest.raises(ValueError, match="window must be >= 1"):
            backend.rolling(panel, window=0, op="mean")


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


class TestDiff:
    def test_diff_default_first_row_null(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.diff(panel)
        for col in SYMBOL_COLS:
            assert result[col][0] is None
            assert result[col][1] == pytest.approx(
                panel[col][1] - panel[col][0], rel=1e-12
            )

    def test_diff_n2_first_two_rows_null(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.diff(panel, n=2)
        for col in SYMBOL_COLS:
            assert result[col][0] is None
            assert result[col][1] is None
            assert result[col][2] == pytest.approx(
                panel[col][2] - panel[col][0], rel=1e-12
            )


# ---------------------------------------------------------------------------
# pct_change
# ---------------------------------------------------------------------------


class TestPctChange:
    def test_pct_change_default(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.pct_change(panel)
        for col in SYMBOL_COLS:
            assert result[col][0] is None
            expected = (panel[col][1] - panel[col][0]) / panel[col][0]
            assert result[col][1] == pytest.approx(expected, rel=1e-12)

    def test_pct_change_n3(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.pct_change(panel, n=3)
        for col in SYMBOL_COLS:
            assert result[col][0] is None
            assert result[col][1] is None
            assert result[col][2] is None
            expected = (panel[col][3] - panel[col][0]) / panel[col][0]
            assert result[col][3] == pytest.approx(expected, rel=1e-12)


# ---------------------------------------------------------------------------
# ewm
# ---------------------------------------------------------------------------


class TestEwm:
    def test_ewm_span_mean(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.ewm(panel, span=3, op="mean")
        # First row of ewm(span) equals the input.
        for col in SYMBOL_COLS:
            assert result[col][0] == pytest.approx(panel[col][0], rel=1e-12)

    def test_ewm_alpha_mean(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.ewm(panel, alpha=0.5, op="mean")
        for col in SYMBOL_COLS:
            assert result[col][0] == pytest.approx(panel[col][0], rel=1e-12)

    def test_ewm_both_span_and_alpha_raises(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        with pytest.raises(ValueError, match="Exactly one"):
            backend.ewm(panel, span=3, alpha=0.5)

    def test_ewm_neither_raises(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        with pytest.raises(ValueError, match="Exactly one"):
            backend.ewm(panel)

    def test_ewm_invalid_op_raises(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        with pytest.raises(ValueError, match="Unsupported ewm op"):
            backend.ewm(panel, span=3, op="sum")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# rank — cross-sectional (axis=0) and time-series (axis=1)
# ---------------------------------------------------------------------------


class TestRankCrossSectional:
    """``axis=0`` ranks across symbols at each timestamp.

    For our deterministic fixture every row is strictly increasing in
    ``j`` (the symbol index), so the cross-sectional rank at every row
    is exactly ``[1, 2, 3, 4, 5]`` (unscaled) or ``[0.2, 0.4, 0.6, 0.8,
    1.0]`` (pct=True).
    """

    def test_rank_cross_section_pct_values_in_unit_interval(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.rank(panel, axis=0, pct=True)
        for col in SYMBOL_COLS:
            for v in result[col].to_list():
                assert 0.0 < v <= 1.0

    def test_rank_cross_section_pct_value_correctness(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        """Per-row rank pct must equal ``(j+1)/N`` for our monotone fixture."""
        result = backend.rank(panel, axis=0, pct=True)
        for j, col in enumerate(SYMBOL_COLS, start=1):
            expected = j / N_SYMBOLS
            for v in result[col].to_list():
                assert v == pytest.approx(expected, rel=1e-12)

    def test_rank_cross_section_no_pct_integer_ranks(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.rank(panel, axis=0, pct=False)
        for j, col in enumerate(SYMBOL_COLS, start=1):
            for v in result[col].to_list():
                assert v == pytest.approx(float(j), rel=1e-12)

    def test_rank_cross_section_null_propagates(
        self,
        backend: PolarsBackend,
        panel_with_null: pl.DataFrame,
    ) -> None:
        result = backend.rank(panel_with_null, axis=0, pct=True)
        # ETH row 0 was nulled — must remain null.
        assert result["ETH"][0] is None
        # SOL row 5 was nulled — must remain null.
        assert result["SOL"][5] is None
        # Other cells remain valid.
        assert result["BTC"][0] is not None


class TestRankTimeSeries:
    """``axis=1`` ranks each symbol's history independently.

    The fixture is strictly increasing along time within each symbol, so
    the time-series rank at row *i* is ``i+1`` (or ``(i+1)/T`` in pct mode).
    """

    def test_rank_time_series_pct_first_row_lowest(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.rank(panel, axis=1, pct=True)
        for col in SYMBOL_COLS:
            assert result[col][0] == pytest.approx(1.0 / T_BARS, rel=1e-12)
            assert result[col][-1] == pytest.approx(1.0, rel=1e-12)

    def test_rank_time_series_pct_value_correctness(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.rank(panel, axis=1, pct=True)
        for col in SYMBOL_COLS:
            for i, v in enumerate(result[col].to_list()):
                assert v == pytest.approx((i + 1) / T_BARS, rel=1e-12)

    def test_rank_time_series_distinct_from_cross_section(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        """Sanity check that ``axis=0`` and ``axis=1`` produce different
        results — guards against silent axis swaps in the implementation."""
        cross = backend.rank(panel, axis=0, pct=True)
        time = backend.rank(panel, axis=1, pct=True)
        assert not cross.equals(time)


class TestRankInvalidAxis:
    def test_rank_axis_must_be_0_or_1(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        with pytest.raises(ValueError, match="axis must be 0 or 1"):
            backend.rank(panel, axis=2)


# ---------------------------------------------------------------------------
# zscore — cross-sectional (axis=0) and time-series (axis=1)
# ---------------------------------------------------------------------------


class TestZscoreCrossSectional:
    def test_zscore_cross_section_row_means_near_zero(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.zscore(panel, axis=0)
        # Each row's symbols must average to ~0.
        for i in range(T_BARS):
            row_vals = [result[col][i] for col in SYMBOL_COLS]
            row_mean = sum(row_vals) / len(row_vals)
            assert row_mean == pytest.approx(0.0, abs=1e-10)

    def test_zscore_cross_section_row_stds_near_one(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.zscore(panel, axis=0)
        for i in range(T_BARS):
            row_vals = [result[col][i] for col in SYMBOL_COLS]
            mean = sum(row_vals) / len(row_vals)
            # Sample std (n-1 denominator), matching polars' default.
            var = sum((v - mean) ** 2 for v in row_vals) / (len(row_vals) - 1)
            std = math.sqrt(var)
            assert std == pytest.approx(1.0, rel=1e-10)


class TestZscoreTimeSeries:
    def test_zscore_time_series_col_means_near_zero(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.zscore(panel, axis=1)
        for col in SYMBOL_COLS:
            vals = result[col].to_list()
            assert sum(vals) / len(vals) == pytest.approx(0.0, abs=1e-10)

    def test_zscore_time_series_col_stds_near_one(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.zscore(panel, axis=1)
        for col in SYMBOL_COLS:
            vals = result[col].to_list()
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
            std = math.sqrt(var)
            assert std == pytest.approx(1.0, rel=1e-10)


class TestZscoreAxisSemantics:
    def test_zscore_axis0_and_axis1_differ(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        cross = backend.zscore(panel, axis=0)
        time = backend.zscore(panel, axis=1)
        assert not cross.equals(time)

    def test_zscore_invalid_axis_raises(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        with pytest.raises(ValueError, match="axis must be 0 or 1"):
            backend.zscore(panel, axis=2)


# ---------------------------------------------------------------------------
# clip
# ---------------------------------------------------------------------------


class TestClip:
    def test_clip_both_bounds(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.clip(panel, low=110.0, high=140.0)
        for col in SYMBOL_COLS:
            for v in result[col].to_list():
                assert 110.0 <= v <= 140.0

    def test_clip_low_only(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.clip(panel, low=110.0, high=None)
        for col in SYMBOL_COLS:
            for v in result[col].to_list():
                assert v >= 110.0

    def test_clip_high_only(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.clip(panel, low=None, high=120.0)
        for col in SYMBOL_COLS:
            for v in result[col].to_list():
                assert v <= 120.0

    def test_clip_no_bounds_identity(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.clip(panel, low=None, high=None)
        assert result.equals(panel)


# ---------------------------------------------------------------------------
# log / abs
# ---------------------------------------------------------------------------


class TestLog:
    def test_log_positive_values(
        self, backend: PolarsBackend, panel: pl.DataFrame
    ) -> None:
        result = backend.log(panel)
        for col in SYMBOL_COLS:
            for x_in, x_out in zip(panel[col].to_list(), result[col].to_list()):
                assert x_out == pytest.approx(math.log(x_in), rel=1e-12)


class TestAbs:
    def test_abs_makes_negative_positive(
        self, backend: PolarsBackend
    ) -> None:
        ts = pl.datetime_range(
            start=dt.datetime(2024, 1, 1),
            end=dt.datetime(2024, 1, 3),
            interval="1d",
            eager=True,
        )
        df = pl.DataFrame({"ts": ts.to_list(), "BTC": [-1.0, 0.0, 2.0]})
        result = PolarsBackend().abs(df)
        assert result["BTC"].to_list() == [1.0, 0.0, 2.0]


# ---------------------------------------------------------------------------
# fillna
# ---------------------------------------------------------------------------


class TestFillna:
    def test_fillna_replaces_nulls_with_value(
        self, backend: PolarsBackend, panel_with_null: pl.DataFrame
    ) -> None:
        result = backend.fillna(panel_with_null, value=0.0)
        # No nulls remain.
        for col in SYMBOL_COLS:
            assert result[col].null_count() == 0
        # Original null cells now hold the fill value.
        assert result["ETH"][0] == pytest.approx(0.0, abs=1e-12)
        assert result["SOL"][5] == pytest.approx(0.0, abs=1e-12)

    def test_fillna_preserves_non_null_values(
        self,
        backend: PolarsBackend,
        panel_with_null: pl.DataFrame,
    ) -> None:
        result = backend.fillna(panel_with_null, value=-999.0)
        # BTC has no nulls in the fixture — must be unchanged.
        assert result["BTC"].to_list() == panel_with_null["BTC"].to_list()


# ---------------------------------------------------------------------------
# Empty value-column edge case
# ---------------------------------------------------------------------------


class TestEmptyValueColumns:
    def test_shift_with_only_ts_column_returns_clone(
        self, backend: PolarsBackend
    ) -> None:
        ts = pl.datetime_range(
            start=dt.datetime(2024, 1, 1),
            end=dt.datetime(2024, 1, 3),
            interval="1d",
            eager=True,
        )
        df = pl.DataFrame({"ts": ts.to_list()})
        result = backend.shift(df, 1)
        assert result.equals(df)

    def test_missing_ts_column_raises(
        self, backend: PolarsBackend
    ) -> None:
        df = pl.DataFrame({"BTC": [1.0, 2.0, 3.0]})
        with pytest.raises(ValueError, match="expects a column named 'ts'"):
            backend.shift(df, 1)
