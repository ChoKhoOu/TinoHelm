"""Unit tests for ``tinohelm.factor.builtins``.

Coverage
--------
- Each factor has a valid ``__factor_spec__`` (category, lookback, non-None).
- All factors produce output Panel with shape == input shape (100 × 10).
- Crypto/funding/OI factors: shape + finite rate.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# ── Import new declarative factors ───────────────────────────────────────────
from tinohelm.factor.builtins.momentum import ret_N, rsi_signal
from tinohelm.factor.builtins.volatility import parkinson_vol, vol_ratio
from tinohelm.factor.builtins.volume import obv_slope, vwap_dev
from tinohelm.factor.builtins.microstructure import trade_imbalance, amihud_illiq
from tinohelm.factor.builtins.crypto_funding import funding_rate_level, funding_rate_mom
from tinohelm.factor.builtins.crypto_data import oi_change, orderbook_imbalance_L1
from tinohelm.factor.types import FactorSpec


# ── Synthetic data fixture ────────────────────────────────────────────────────

N_ROWS = 100
N_COLS = 10
SYMBOLS = [f"SYM{i:02d}" for i in range(N_COLS)]
IDX = pd.date_range("2024-01-01", periods=N_ROWS, freq="1h")

RNG = np.random.default_rng(42)


def _make_prices(base: float = 100.0) -> pd.DataFrame:
    """Random walk prices, all positive."""
    log_ret = RNG.normal(0, 0.01, size=(N_ROWS, N_COLS))
    prices = base * np.exp(np.cumsum(log_ret, axis=0))
    return pd.DataFrame(prices, index=IDX, columns=SYMBOLS)


def _make_volume() -> pd.DataFrame:
    """Strictly positive volume."""
    vol = RNG.lognormal(10, 0.5, size=(N_ROWS, N_COLS))
    return pd.DataFrame(vol, index=IDX, columns=SYMBOLS)


# Deterministic panels shared across all tests
CLOSE = _make_prices(100.0)
HIGH = CLOSE * (1 + RNG.uniform(0, 0.02, size=CLOSE.shape))
LOW = CLOSE * (1 - RNG.uniform(0, 0.02, size=CLOSE.shape))
OPEN = _make_prices(100.0)
VOLUME = _make_volume()

# Ensure HIGH >= CLOSE >= LOW (can be violated by independent random walks)
HIGH = pd.DataFrame(
    np.maximum(HIGH.values, CLOSE.values), index=IDX, columns=SYMBOLS
)
LOW = pd.DataFrame(
    np.minimum(LOW.values, CLOSE.values), index=IDX, columns=SYMBOLS
)

# Non-bar data panels
FUNDING_RATE = pd.DataFrame(
    RNG.normal(0, 0.001, size=(N_ROWS, N_COLS)), index=IDX, columns=SYMBOLS
)
OPEN_INTEREST = pd.DataFrame(
    RNG.lognormal(15, 0.3, size=(N_ROWS, N_COLS)), index=IDX, columns=SYMBOLS
)
ORDERBOOK_IMBALANCE = pd.DataFrame(
    RNG.uniform(-1, 1, size=(N_ROWS, N_COLS)), index=IDX, columns=SYMBOLS
)


def _make_df_for_legacy(symbol: str | None = None) -> pd.DataFrame:
    """Construct a single-symbol DataFrame matching the legacy API."""
    if symbol is None:
        # Use first column for single-symbol tests
        symbol = SYMBOLS[0]
    idx = SYMBOLS.index(symbol)
    return pd.DataFrame(
        {
            "open": OPEN.iloc[:, idx].values,
            "high": HIGH.iloc[:, idx].values,
            "low": LOW.iloc[:, idx].values,
            "close": CLOSE.iloc[:, idx].values,
            "volume": VOLUME.iloc[:, idx].values,
        },
        index=IDX,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _max_abs_diff(new_result: pd.Series, legacy_result: pd.Series) -> float:
    """Maximum absolute element-wise difference, ignoring NaN positions."""
    diff = (new_result - legacy_result).abs()
    valid = diff.dropna()
    if valid.empty:
        return 0.0
    return float(valid.max())


def _run_new_single_symbol(factor_fn, symbol: str, params: dict) -> pd.Series:
    """Run new @factor function for a single symbol, extract that symbol's column."""
    # Build single-column Panels for this symbol
    col = SYMBOLS.index(symbol)
    close_p = CLOSE.iloc[:, [col]]
    high_p = HIGH.iloc[:, [col]]
    low_p = LOW.iloc[:, [col]]
    volume_p = VOLUME.iloc[:, [col]]
    funding_p = FUNDING_RATE.iloc[:, [col]]
    oi_p = OPEN_INTEREST.iloc[:, [col]]
    ob_p = ORDERBOOK_IMBALANCE.iloc[:, [col]]

    name = factor_fn.__name__

    if name == "ret_N":
        result = factor_fn(close_p, params=params)
    elif name == "rsi_signal":
        result = factor_fn(close_p, params=params)
    elif name == "parkinson_vol":
        result = factor_fn(high_p, low_p, params=params)
    elif name == "vol_ratio":
        result = factor_fn(close_p, params=params)
    elif name == "obv_slope":
        result = factor_fn(close_p, volume_p, params=params)
    elif name == "vwap_dev":
        result = factor_fn(high_p, low_p, close_p, volume_p, params=params)
    elif name == "trade_imbalance":
        result = factor_fn(high_p, low_p, close_p, volume_p, params=params)
    elif name == "amihud_illiq":
        result = factor_fn(close_p, volume_p, params=params)
    elif name == "funding_rate_level":
        result = factor_fn(funding_p, params=params)
    elif name == "funding_rate_mom":
        result = factor_fn(funding_p, params=params)
    elif name == "oi_change":
        result = factor_fn(oi_p, params=params)
    elif name == "orderbook_imbalance_L1":
        result = factor_fn(ob_p, params=params)
    else:
        raise ValueError(f"Unknown factor: {name}")

    # result is a DataFrame; extract the single column as Series
    return result.iloc[:, 0]


# ── AC: __factor_spec__ validation ───────────────────────────────────────────

ALL_FACTORS = [
    ret_N, rsi_signal,
    parkinson_vol, vol_ratio,
    obv_slope, vwap_dev,
    trade_imbalance, amihud_illiq,
    funding_rate_level, funding_rate_mom,
    oi_change, orderbook_imbalance_L1,
]

EXPECTED_CATEGORIES = {
    "ret_N": "动量",
    "rsi_signal": "动量",
    "parkinson_vol": "波动",
    "vol_ratio": "波动",
    "obv_slope": "成交量",
    "vwap_dev": "成交量",
    "trade_imbalance": "微观结构",
    "amihud_illiq": "微观结构",
    "funding_rate_level": "资金费率",
    "funding_rate_mom": "资金费率",
    "oi_change": "链上数据",
    "orderbook_imbalance_L1": "链上数据",
}

EXPECTED_LOOKBACKS = {
    "ret_N": 20,
    "rsi_signal": 14,
    "parkinson_vol": 20,
    "vol_ratio": 20,
    "obv_slope": 20,
    "vwap_dev": 20,
    "trade_imbalance": 20,
    "amihud_illiq": 20,
    # crypto/funding: at least 1
    "funding_rate_level": 1,
    "funding_rate_mom": 2,
    "oi_change": 2,
    "orderbook_imbalance_L1": 1,
}


@pytest.mark.parametrize("fn", ALL_FACTORS, ids=[f.__name__ for f in ALL_FACTORS])
def test_factor_spec_not_none(fn):
    assert hasattr(fn, "__factor_spec__")
    assert fn.__factor_spec__ is not None
    assert isinstance(fn.__factor_spec__, FactorSpec)


@pytest.mark.parametrize("fn", ALL_FACTORS, ids=[f.__name__ for f in ALL_FACTORS])
def test_factor_spec_category(fn):
    name = fn.__name__
    assert fn.__factor_spec__.category == EXPECTED_CATEGORIES[name], (
        f"{name}: expected category {EXPECTED_CATEGORIES[name]!r}, "
        f"got {fn.__factor_spec__.category!r}"
    )


@pytest.mark.parametrize("fn", ALL_FACTORS, ids=[f.__name__ for f in ALL_FACTORS])
def test_factor_spec_lookback(fn):
    name = fn.__name__
    expected = EXPECTED_LOOKBACKS[name]
    actual = fn.__factor_spec__.lookback
    assert actual == expected, (
        f"{name}: expected lookback {expected}, got {actual}"
    )


# ── AC: Shape correctness (100 × 10 input → 100 × 10 output) ─────────────────

def _full_panel_output(factor_fn) -> pd.DataFrame:
    """Run a factor on the full 100 × 10 panels and return the output."""
    name = factor_fn.__name__
    params20 = {"lookback": 20}
    params14 = {"lookback": 14}
    params_vr = {"fast": 5, "slow": 20}

    if name == "ret_N":
        return factor_fn(CLOSE, params=params20)
    if name == "rsi_signal":
        return factor_fn(CLOSE, params=params14)
    if name == "parkinson_vol":
        return factor_fn(HIGH, LOW, params=params20)
    if name == "vol_ratio":
        return factor_fn(CLOSE, params=params_vr)
    if name == "obv_slope":
        return factor_fn(CLOSE, VOLUME, params=params20)
    if name == "vwap_dev":
        return factor_fn(HIGH, LOW, CLOSE, VOLUME, params=params20)
    if name == "trade_imbalance":
        # trade_imbalance requires trade_tick DataLayer support (not yet implemented).
        # The function raises NotImplementedError; skip execution here.
        pytest.skip("trade_imbalance pending trade_tick DataLayer support")
    if name == "amihud_illiq":
        return factor_fn(CLOSE, VOLUME, params=params20)
    if name == "funding_rate_level":
        return factor_fn(FUNDING_RATE, params={})
    if name == "funding_rate_mom":
        return factor_fn(FUNDING_RATE, params={"lookback": 1})
    if name == "oi_change":
        return factor_fn(OPEN_INTEREST, params={"lookback": 1})
    if name == "orderbook_imbalance_L1":
        return factor_fn(ORDERBOOK_IMBALANCE, params={})
    raise ValueError(f"Unknown factor: {name}")


@pytest.mark.parametrize("fn", ALL_FACTORS, ids=[f.__name__ for f in ALL_FACTORS])
def test_output_shape(fn):
    result = _full_panel_output(fn)
    assert isinstance(result, pd.DataFrame), f"{fn.__name__}: output is not a DataFrame"
    assert result.shape == (N_ROWS, N_COLS), (
        f"{fn.__name__}: expected shape ({N_ROWS}, {N_COLS}), got {result.shape}"
    )


# ── Shape + finite rate for crypto / funding factors ─────────────────────────

CRYPTO_FACTORS = [
    (funding_rate_level, FUNDING_RATE, {}),
    (funding_rate_mom, FUNDING_RATE, {"lookback": 1}),
    (oi_change, OPEN_INTEREST, {"lookback": 1}),
    (orderbook_imbalance_L1, ORDERBOOK_IMBALANCE, {}),
]


@pytest.mark.parametrize(
    "fn, panel, params",
    CRYPTO_FACTORS,
    ids=[c[0].__name__ for c in CRYPTO_FACTORS],
)
def test_crypto_factor_shape(fn, panel, params):
    result = fn(panel, params=params)
    assert result.shape == (N_ROWS, N_COLS)


@pytest.mark.parametrize(
    "fn, panel, params",
    CRYPTO_FACTORS,
    ids=[c[0].__name__ for c in CRYPTO_FACTORS],
)
def test_crypto_factor_finite_rate(fn, panel, params):
    """At least 80% of non-NaN values should be finite after warmup."""
    result = fn(panel, params=params)
    # Skip first few rows (warmup NaNs)
    tail = result.iloc[5:]
    finite_mask = np.isfinite(tail.values)
    total = finite_mask.size
    finite_count = finite_mask.sum()
    rate = finite_count / total
    assert rate >= 0.8, (
        f"{fn.__name__}: finite rate {rate:.1%} < 80%. "
        "Too many non-finite values in output."
    )


# ── Import smoke test: all sub-modules importable ─────────────────────────────

def test_builtins_package_import():
    """Sub-modules are importable without ImportError."""
    from tinohelm.factor.builtins import (  # noqa: F401
        momentum,
        volatility,
        volume,
        microstructure,
        crypto_funding,
        crypto_data,
    )


def test_all_factors_have_name_in_spec():
    """Each factor's __factor_spec__.name matches the function name."""
    for fn in ALL_FACTORS:
        assert fn.__factor_spec__.name == fn.__name__, (
            f"{fn.__name__}: spec.name = {fn.__factor_spec__.name!r}"
        )
