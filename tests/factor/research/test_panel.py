import numpy as np
import polars as pl
import pytest

from tinohelm.factor.research.panel import (
    MatrixPanel,
    assert_unique_ts_symbol,
    canonicalize_long_bars,
    long_to_wide_panels,
    matrix_to_wide,
    wide_to_matrix,
)


def test_duplicate_ts_symbol_raises():
    frame = pl.DataFrame({
        "ts": ["2024-01-01", "2024-01-01"],
        "symbol": ["BTCUSDT", "BTCUSDT"],
        "close": [1.0, 2.0],
    }).with_columns(pl.col("ts").str.to_datetime())

    with pytest.raises(ValueError, match="duplicate"):
        assert_unique_ts_symbol(frame)


def test_canonicalize_sorts_by_ts_symbol():
    frame = pl.DataFrame({
        "ts": ["2024-01-02", "2024-01-01", "2024-01-01"],
        "symbol": ["ETHUSDT", "ETHUSDT", "BTCUSDT"],
        "close": [3, 2, 1],
    }).with_columns(pl.col("ts").str.to_datetime())

    bars = canonicalize_long_bars(frame, ["close"], "klines", "1m")

    assert bars.frame.select("symbol").to_series().to_list() == ["BTCUSDT", "ETHUSDT", "ETHUSDT"]
    assert bars.frame.select("close").to_series().to_list() == [1.0, 2.0, 3.0]


def test_wide_matrix_roundtrip_preserves_axes_and_nan():
    wide = pl.DataFrame({
        "ts": ["2024-01-01", "2024-01-02"],
        "ETHUSDT": [None, 4.0],
        "BTCUSDT": [1.0, 2.0],
    }).with_columns(pl.col("ts").str.to_datetime())

    matrix = wide_to_matrix(wide)
    roundtrip = matrix_to_wide(matrix)

    assert matrix.symbols == ("ETHUSDT", "BTCUSDT")
    assert np.isnan(matrix.values[0, 0])
    assert roundtrip.columns == wide.columns
    np.testing.assert_allclose(roundtrip.select(["ETHUSDT", "BTCUSDT"]).to_numpy(), matrix.values)


def test_matrix_validate_rejects_shape_mismatch_non_monotonic_ts_and_duplicate_symbols():
    ts = np.array(["2024-01-01", "2024-01-02"], dtype="datetime64[ns]")
    with pytest.raises(ValueError, match="shape"):
        MatrixPanel(ts, ("BTCUSDT",), np.ones((2, 2))).validate()

    bad_ts = np.array(["2024-01-02", "2024-01-01"], dtype="datetime64[ns]")
    with pytest.raises(ValueError, match="increasing"):
        MatrixPanel(bad_ts, ("BTCUSDT",), np.ones((2, 1))).validate()

    with pytest.raises(ValueError, match="duplicate symbols"):
        MatrixPanel(ts, ("BTCUSDT", "BTCUSDT"), np.ones((2, 2))).validate()

    with pytest.raises(ValueError, match="NaT"):
        MatrixPanel(
            np.array(["NaT"], dtype="datetime64[ns]"),
            ("BTCUSDT",),
            np.ones((1, 1)),
        ).validate()

    with pytest.raises(ValueError, match="reserved"):
        MatrixPanel(ts[:1], ("ts",), np.ones((1, 1))).validate()


def test_long_to_wide_panels_outputs_field_panels():
    bars = canonicalize_long_bars(
        pl.DataFrame({
            "ts": ["2024-01-01", "2024-01-01", "2024-01-02"],
            "symbol": ["BTCUSDT", "ETHUSDT", "BTCUSDT"],
            "close": [1, 2, 3],
        }).with_columns(pl.col("ts").str.to_datetime()),
        ["close"],
        "klines",
        "1m",
    )

    panels = long_to_wide_panels(bars, ["close"])

    assert panels["close"].columns == ["ts", "BTCUSDT", "ETHUSDT"]
    assert panels["close"]["BTCUSDT"].to_list() == [1.0, 3.0]


def test_long_to_wide_panels_preserves_requested_missing_symbols_as_null_columns():
    bars = canonicalize_long_bars(
        pl.DataFrame({
            "ts": ["2024-01-01"],
            "symbol": ["BTCUSDT"],
            "close": [1],
        }).with_columns(pl.col("ts").str.to_datetime()),
        ["close"],
        "klines",
        "1m",
        symbols=("BTCUSDT", "ETHUSDT"),
    )

    panels = long_to_wide_panels(bars, ["close"])

    assert panels["close"].columns == ["ts", "BTCUSDT", "ETHUSDT"]
    assert panels["close"]["ETHUSDT"].to_list() == [None]


def test_canonicalize_rejects_duplicate_requested_symbols():
    frame = pl.DataFrame({
        "ts": ["2024-01-01"],
        "symbol": ["BTCUSDT"],
        "close": [1],
    }).with_columns(pl.col("ts").str.to_datetime())

    with pytest.raises(ValueError, match="duplicate symbols"):
        canonicalize_long_bars(frame, ["close"], "klines", "1m", symbols=("BTCUSDT", "BTCUSDT"))

    with pytest.raises(ValueError, match="reserved"):
        canonicalize_long_bars(frame, ["close"], "klines", "1m", symbols=("ts",))
