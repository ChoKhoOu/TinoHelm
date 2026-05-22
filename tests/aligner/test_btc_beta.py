"""Tests for BTCBetaExposure — rolling-window OLS beta against BTC.

All DataLayer calls are mocked so tests run without a real catalog or Binance API.

Mock strategy
-------------
We inject a ``data_layer`` whose ``.load()`` method returns a pandas DataFrame
(keyed on ``"close"``) built from synthetic data constructed inline.  The
``monkeypatch`` fixture patches nothing external — we pass the mock directly
via the ``data_layer=`` constructor parameter.
"""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import polars as pl
import pytest

from tinohelm.aligner.exposure_btc import BTCBetaExposure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_timestamps(n: int, freq: str = "1min") -> pl.Series:
    """Return a Polars Datetime Series of length n at the given pandas freq."""
    idx = pd.date_range("2024-01-01", periods=n, freq=freq)
    # Convert to polars via nanosecond integers
    ns_vals = idx.astype("int64")
    return pl.Series("ts", ns_vals).cast(pl.Datetime("ns"))


def _make_close_panel(
    symbols: list[str],
    n: int,
    freq: str = "1min",
    btc_symbol: str = "BTCUSDT-PERP",
    seed: int = 42,
) -> pd.DataFrame:
    """Synthetic close-price panel (random walk, deterministic seed)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq=freq)
    data: dict[str, list[float]] = {}
    for sym in symbols:
        price = 1000.0 if sym == btc_symbol else 100.0
        returns = rng.standard_normal(n) * 0.01
        closes: list[float] = []
        for r in returns:
            price = price * (1 + r)
            closes.append(price)
        data[sym] = closes
    return pd.DataFrame(data, index=idx)


def _mock_data_layer(close_panel: pd.DataFrame) -> MagicMock:
    """Return a mock DataLayer whose load() returns {'close': close_panel}."""
    dl = MagicMock()
    dl.load.return_value = {"close": close_panel}
    return dl


# ---------------------------------------------------------------------------
# Test 1: Output DataFrame shape
# ---------------------------------------------------------------------------


def test_returns_dataframe_shape() -> None:
    """get_exposure(4 ts, 5 syms) → (4, 6) polars df, column 0 = ts (Datetime)."""
    symbols = [
        "BTCUSDT-PERP",
        "ETHUSDT-PERP",
        "BNBUSDT-PERP",
        "SOLUSDT-PERP",
        "ADAUSDT-PERP",
    ]
    # Use window=3 and supply 10 rows so we have warmup history
    n = 10
    close_panel = _make_close_panel(symbols, n)
    dl = _mock_data_layer(close_panel)

    provider = BTCBetaExposure(window=3, data_layer=dl)
    ts = _make_timestamps(4)

    df = provider.get_exposure(ts, symbols)

    assert isinstance(df, pl.DataFrame)
    assert df.shape == (4, 6), f"expected (4, 6), got {df.shape}"
    assert df.columns[0] == "ts"
    assert df.schema["ts"] == pl.Datetime("ns")
    assert set(df.columns[1:]) == set(symbols)


# ---------------------------------------------------------------------------
# Test 2: BTC self-beta = 1.0
# ---------------------------------------------------------------------------


def test_btc_self_beta_is_one() -> None:
    """BTC's own beta must be exactly 1.0 for every non-warmup row."""
    btc_sym = "BTCUSDT-PERP"
    symbols = [btc_sym, "ETHUSDT-PERP"]
    n = 20
    close_panel = _make_close_panel(symbols, n, btc_symbol=btc_sym)
    dl = _mock_data_layer(close_panel)

    provider = BTCBetaExposure(window=5, btc_symbol=btc_sym, data_layer=dl)
    ts = _make_timestamps(n)

    df = provider.get_exposure(ts, symbols)
    btc_col = df[btc_sym]

    # All values (including warmup) must be 1.0 for BTC
    for val in btc_col.to_list():
        assert val == pytest.approx(1.0), f"Expected 1.0, got {val}"


# ---------------------------------------------------------------------------
# Test 3: BTC data missing → all betas null (no exception)
# ---------------------------------------------------------------------------


def test_btc_missing_returns_null() -> None:
    """When BTC close data is absent, all non-BTC betas should be null (not raise)."""
    symbols = ["ETHUSDT-PERP", "BNBUSDT-PERP"]
    btc_sym = "BTCUSDT-PERP"

    # Return empty close panel — BTC absent
    dl = _mock_data_layer(pd.DataFrame())

    provider = BTCBetaExposure(window=5, btc_symbol=btc_sym, data_layer=dl)
    ts = _make_timestamps(4)

    # Must not raise
    df = provider.get_exposure(ts, symbols)

    assert df.shape == (4, 3)  # ts + 2 symbols
    # All exposure values should be null
    for sym in symbols:
        nulls = df[sym].null_count()
        assert nulls == 4, f"{sym}: expected 4 nulls, got {nulls}"


# ---------------------------------------------------------------------------
# Test 4: Single symbol universe (BTC only)
# ---------------------------------------------------------------------------


def test_single_symbol_universe() -> None:
    """symbols=['BTCUSDT-PERP'] → (T, 2) df, BTC column all 1.0."""
    btc_sym = "BTCUSDT-PERP"
    symbols = [btc_sym]
    n = 8
    close_panel = _make_close_panel(symbols, n, btc_symbol=btc_sym)
    dl = _mock_data_layer(close_panel)

    provider = BTCBetaExposure(window=3, btc_symbol=btc_sym, data_layer=dl)
    ts = _make_timestamps(n)

    df = provider.get_exposure(ts, symbols)

    assert df.shape == (n, 2)  # ts + BTC
    assert df.columns == ["ts", btc_sym]
    for val in df[btc_sym].to_list():
        assert val == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Test 5: Rolling warmup — first window-1 rows for non-BTC symbols are null
# ---------------------------------------------------------------------------


def test_rolling_window_warmup() -> None:
    """First window-1 rows for non-BTC symbols must be null (insufficient history)."""
    btc_sym = "BTCUSDT-PERP"
    eth_sym = "ETHUSDT-PERP"
    symbols = [btc_sym, eth_sym]
    window = 5
    # Use exactly window+2 rows so last 2 have valid betas
    n = window + 2
    close_panel = _make_close_panel(symbols, n, btc_symbol=btc_sym)
    dl = _mock_data_layer(close_panel)

    provider = BTCBetaExposure(window=window, btc_symbol=btc_sym, data_layer=dl)
    # Request timestamps aligned with the close_panel index
    ts_pd = close_panel.index
    ns_vals = ts_pd.astype("int64")
    ts = pl.Series("ts", ns_vals).cast(pl.Datetime("ns"))

    df = provider.get_exposure(ts, symbols)

    eth_col = df[eth_sym].to_list()
    # First pct_change = NaN (row 0), then rolling(5) needs 5 rows → first valid at row 5
    # Rows 0..4 (first 5) should be null
    null_rows = [i for i, v in enumerate(eth_col) if v is None]
    valid_rows = [i for i, v in enumerate(eth_col) if v is not None]

    # At least window-1 warmup nulls for non-BTC
    assert len(null_rows) >= window - 1, (
        f"Expected at least {window-1} null warmup rows, got {len(null_rows)}"
    )
    assert len(valid_rows) >= 1, "Expected at least 1 valid beta row after warmup"


# ---------------------------------------------------------------------------
# Test 6: PIT — beta[t] only uses returns ≤ t (no future leak)
# ---------------------------------------------------------------------------


def test_pit_no_future_leak() -> None:
    """Verify beta is unchanged when future returns are altered.

    Strategy: construct close prices for T+1 timestamps.  Request betas for
    timestamps[0..T-1].  Then corrupt the T-th close (future bar) by setting
    it to an extreme value.  Both calls must produce identical beta values up
    to T-1, proving the computation uses no future data.
    """
    btc_sym = "BTCUSDT-PERP"
    eth_sym = "ETHUSDT-PERP"
    symbols = [btc_sym, eth_sym]
    window = 4
    n = window + 3  # 7 rows total

    close_panel_a = _make_close_panel(symbols, n, btc_symbol=btc_sym, seed=1)
    # Clone and corrupt last row — simulates future data modification
    close_panel_b = close_panel_a.copy()
    close_panel_b.iloc[-1, :] = 99999.0  # extreme future value

    # Request only first n-1 timestamps (exclude the last / future bar)
    target_n = n - 1
    ts_pd = close_panel_a.index[:target_n]
    ns_vals = ts_pd.astype("int64")
    ts = pl.Series("ts", ns_vals).cast(pl.Datetime("ns"))

    dl_a = _mock_data_layer(close_panel_a)
    dl_b = _mock_data_layer(close_panel_b)

    provider_a = BTCBetaExposure(window=window, btc_symbol=btc_sym, data_layer=dl_a)
    provider_b = BTCBetaExposure(window=window, btc_symbol=btc_sym, data_layer=dl_b)

    df_a = provider_a.get_exposure(ts, symbols)
    df_b = provider_b.get_exposure(ts, symbols)

    # ETH betas must be identical — future corruption should not affect past betas
    eth_a = df_a[eth_sym].to_list()
    eth_b = df_b[eth_sym].to_list()

    for i, (a, b) in enumerate(zip(eth_a, eth_b)):
        if a is None and b is None:
            continue
        assert a == pytest.approx(b, abs=1e-10), (
            f"PIT leak detected at row {i}: beta_a={a}, beta_b={b}"
        )
