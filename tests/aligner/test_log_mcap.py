"""Tests for LogMcapExposure — log(close × circulating_supply) panel.

All external dependencies (DataLayer, fetch_circulating_supply) are mocked so
tests run without a real catalog or Binance API.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import polars as pl
import pytest

from tinohelm.aligner.exposure_logmcap import LogMcapExposure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_timestamps(n: int, freq: str = "1min") -> pl.Series:
    """Return a Polars Datetime Series of length n."""
    idx = pd.date_range("2024-01-01", periods=n, freq=freq)
    ns_vals = idx.astype("int64")
    return pl.Series("ts", ns_vals).cast(pl.Datetime("ns"))


def _make_close_panel(
    symbols: list[str],
    n: int,
    freq: str = "1min",
    base_price: float = 1000.0,
    seed: int = 7,
) -> pd.DataFrame:
    """Synthetic close-price panel."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq=freq)
    data: dict[str, list[float]] = {}
    for sym in symbols:
        price = base_price
        closes: list[float] = []
        for r in rng.standard_normal(n) * 0.005:
            price = price * (1 + r)
            closes.append(price)
        data[sym] = closes
    return pd.DataFrame(data, index=idx)


def _mock_data_layer(close_panel: pd.DataFrame) -> MagicMock:
    """Return a mock DataLayer whose load() returns {'close': close_panel}."""
    dl = MagicMock()
    dl.load.return_value = {"close": close_panel}
    return dl


FAKE_SUPPLIES = {
    "BTCUSDT-PERP": 19_700_000.0,
    "ETHUSDT-PERP": 120_000_000.0,
    "BNBUSDT-PERP": 145_000_000.0,
    "SOLUSDT-PERP": 400_000_000.0,
    "ADAUSDT-PERP": 35_000_000_000.0,
}


def _supply_side_effect(symbol: str) -> float:
    if symbol not in FAKE_SUPPLIES:
        raise RuntimeError(f"No supply data for {symbol}")
    return FAKE_SUPPLIES[symbol]


# ---------------------------------------------------------------------------
# Test 1: Output DataFrame shape
# ---------------------------------------------------------------------------


def test_returns_dataframe_shape() -> None:
    """get_exposure(4 ts, 5 syms) → (4, 6) polars df, column 0 = ts (Datetime)."""
    symbols = list(FAKE_SUPPLIES.keys())[:5]
    n = 4
    close_panel = _make_close_panel(symbols, n)
    dl = _mock_data_layer(close_panel)

    with patch(
        "tinohelm.aligner.exposure_logmcap.fetch_circulating_supply",
        side_effect=_supply_side_effect,
    ):
        provider = LogMcapExposure(data_layer=dl)
        ts = _make_timestamps(n)
        df = provider.get_exposure(ts, symbols)

    assert isinstance(df, pl.DataFrame)
    assert df.shape == (n, len(symbols) + 1), f"expected ({n}, {len(symbols)+1}), got {df.shape}"
    assert df.columns[0] == "ts"
    assert df.schema["ts"] == pl.Datetime("ns")
    assert set(df.columns[1:]) == set(symbols)


# ---------------------------------------------------------------------------
# Test 2: PIT — changing close[t] only affects log_mcap[t], not earlier rows
# ---------------------------------------------------------------------------


def test_pit_close_used() -> None:
    """Altering close[t] must change mcap[t] but leave mcap[t-1] unchanged."""
    sym = "BTCUSDT-PERP"
    symbols = [sym]
    n = 4
    supply = FAKE_SUPPLIES[sym]

    idx = pd.date_range("2024-01-01", periods=n, freq="1min")
    close_vals_a = [100.0, 110.0, 105.0, 120.0]
    close_vals_b = [100.0, 110.0, 105.0, 9999.0]  # only last row differs

    panel_a = pd.DataFrame({sym: close_vals_a}, index=idx)
    panel_b = pd.DataFrame({sym: close_vals_b}, index=idx)

    ns_vals = idx.astype("int64")
    ts = pl.Series("ts", ns_vals).cast(pl.Datetime("ns"))

    with patch(
        "tinohelm.aligner.exposure_logmcap.fetch_circulating_supply",
        return_value=supply,
    ):
        df_a = LogMcapExposure(data_layer=_mock_data_layer(panel_a)).get_exposure(ts, symbols)
        df_b = LogMcapExposure(data_layer=_mock_data_layer(panel_b)).get_exposure(ts, symbols)

    # Rows 0..2 must be identical between the two calls
    for row in range(n - 1):
        va = df_a[sym][row]
        vb = df_b[sym][row]
        assert va == pytest.approx(vb, abs=1e-9), (
            f"PIT leak at row {row}: log_mcap_a={va}, log_mcap_b={vb}"
        )

    # Last row must differ
    assert df_a[sym][n - 1] != pytest.approx(df_b[sym][n - 1]), (
        "Expected last row to differ after close corruption"
    )


# ---------------------------------------------------------------------------
# Test 3: fetch_circulating_supply called exactly once per symbol
# ---------------------------------------------------------------------------


def test_supply_fetch_called() -> None:
    """fetch_circulating_supply must be called exactly once per symbol."""
    symbols = ["BTCUSDT-PERP", "ETHUSDT-PERP"]
    n = 3
    close_panel = _make_close_panel(symbols, n)
    dl = _mock_data_layer(close_panel)
    ts = _make_timestamps(n)

    with patch(
        "tinohelm.aligner.exposure_logmcap.fetch_circulating_supply",
        side_effect=_supply_side_effect,
    ) as mock_fetch:
        provider = LogMcapExposure(data_layer=dl)
        provider.get_exposure(ts, symbols)

    # Must have been called once per symbol, total = len(symbols)
    assert mock_fetch.call_count == len(symbols), (
        f"Expected {len(symbols)} calls, got {mock_fetch.call_count}"
    )
    called_with = {c.args[0] for c in mock_fetch.call_args_list}
    assert called_with == set(symbols)


# ---------------------------------------------------------------------------
# Test 4: Supply fetch failure → that symbol's column is entirely null
# ---------------------------------------------------------------------------


def test_supply_missing_propagates_null() -> None:
    """When fetch_circulating_supply raises for a symbol, that column must be all-null."""
    btc_sym = "BTCUSDT-PERP"
    eth_sym = "ETHUSDT-PERP"
    symbols = [btc_sym, eth_sym]
    n = 4
    close_panel = _make_close_panel(symbols, n)
    dl = _mock_data_layer(close_panel)
    ts = _make_timestamps(n)

    def _failing_supply(symbol: str) -> float:
        if symbol == eth_sym:
            raise RuntimeError("Supply data unavailable")
        return FAKE_SUPPLIES[symbol]

    with patch(
        "tinohelm.aligner.exposure_logmcap.fetch_circulating_supply",
        side_effect=_failing_supply,
    ):
        provider = LogMcapExposure(data_layer=dl)
        df = provider.get_exposure(ts, symbols)

    # BTC column must have valid values
    assert df[btc_sym].null_count() == 0, "BTC should have no nulls"

    # ETH column must be entirely null
    assert df[eth_sym].null_count() == n, (
        f"ETH should be all-null, got {df[eth_sym].null_count()} nulls out of {n}"
    )


# ---------------------------------------------------------------------------
# Test 5: log transform is correctly applied
# ---------------------------------------------------------------------------


def test_log_transform_applied() -> None:
    """mcap = close × supply = 100 × 1.0 → log_mcap = ln(100) ≈ 4.6051."""
    sym = "BTCUSDT-PERP"
    symbols = [sym]
    supply = 1.0  # unit supply so mcap = close

    idx = pd.date_range("2024-01-01", periods=1, freq="1min")
    close_val = 100.0
    panel = pd.DataFrame({sym: [close_val]}, index=idx)
    dl = _mock_data_layer(panel)

    ns_vals = idx.astype("int64")
    ts = pl.Series("ts", ns_vals).cast(pl.Datetime("ns"))

    with patch(
        "tinohelm.aligner.exposure_logmcap.fetch_circulating_supply",
        return_value=supply,
    ):
        provider = LogMcapExposure(data_layer=dl)
        df = provider.get_exposure(ts, symbols)

    result = df[sym][0]
    expected = math.log(close_val * supply)  # = ln(100) ≈ 4.6051
    assert result == pytest.approx(expected, abs=1e-9), (
        f"Expected log_mcap={expected:.6f}, got {result}"
    )
