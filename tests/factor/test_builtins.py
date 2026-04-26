"""Unit tests for ``tinohelm.factor.builtins`` (polars migration).

Coverage
--------
1. **Spec validation** — every factor exposes a ``__factor_spec__`` with the
   expected category, lookback, name, and (where applicable) the
   ``experimental`` / ``deprecated`` flags.
2. **Numerical regression** — for the 9 non-experimental factors the polars
   kernel output is compared against the legacy pandas oracle parquet
   (``tests/factor/_legacy_pandas/regression_oracle.parquet``).  The
   absolute element-wise difference must satisfy ``|polars - pandas| <= 1e-6``.
3. **Experimental gate** — the 3 experimental factors raise
   ``NotImplementedError`` and have ``deprecated=True`` on their spec.
4. **Module import smoke** — ``tinohelm.factor.builtins`` imports cleanly.

The oracle is generated once via
``tests/factor/_legacy_pandas/_build_oracle.py`` (see that file's docstring
for the formulas mirrored from the legacy pandas implementation).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
import pytest

# ── Import declarative factors ────────────────────────────────────────────────
from tinohelm.factor.builtins.crypto_data import oi_change, orderbook_imbalance_L1
from tinohelm.factor.builtins.crypto_funding import (
    funding_rate_level,
    funding_rate_mom,
)
from tinohelm.factor.builtins.microstructure import amihud_illiq, trade_imbalance
from tinohelm.factor.builtins.momentum import ret_N, rsi_signal
from tinohelm.factor.builtins.volatility import parkinson_vol, vol_ratio
from tinohelm.factor.builtins.volume import obv_slope, vwap_dev
from tinohelm.factor.types import FactorSpec


# ---------------------------------------------------------------------------
# Oracle parquet loader
# ---------------------------------------------------------------------------

ORACLE_PATH = Path(__file__).parent / "_legacy_pandas" / "regression_oracle.parquet"
SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP"]


@pytest.fixture(scope="module")
def oracle() -> pd.DataFrame:
    """Load the legacy-pandas oracle parquet (pandas DataFrame indexed by ts)."""
    if not ORACLE_PATH.exists():  # pragma: no cover — dev guard
        raise FileNotFoundError(
            f"Oracle parquet missing at {ORACLE_PATH}. "
            "Run tests/factor/_legacy_pandas/_build_oracle.py first."
        )
    return pd.read_parquet(ORACLE_PATH)


def _input_panel(oracle: pd.DataFrame, field: str) -> pl.DataFrame:
    """Extract a wide polars Panel for ``field`` (close/high/low/volume/funding_rate)."""
    cols = {sym: oracle[f"input_{field}__{sym}"].to_numpy() for sym in SYMBOLS}
    return pl.DataFrame({"ts": oracle["ts"].to_list(), **cols})


def _oracle_factor(oracle: pd.DataFrame, name: str) -> dict[str, np.ndarray]:
    """Per-symbol pandas oracle values for factor ``name``."""
    return {sym: oracle[f"factor_{name}__{sym}"].to_numpy() for sym in SYMBOLS}


def _abs_diff(actual: pl.DataFrame, oracle_vals: dict[str, np.ndarray]) -> float:
    """Maximum element-wise absolute difference, ignoring positions where
    *both* sides are NaN.  Mismatched NaN positions count as a miss
    (returns ``+inf``).
    """
    max_diff = 0.0
    for sym, oracle_arr in oracle_vals.items():
        polars_arr = actual[sym].to_numpy()
        # Coerce polars ``null`` (returned as NaN for float columns) to NaN.
        a = np.asarray(polars_arr, dtype=float)
        e = np.asarray(oracle_arr, dtype=float)
        # Both NaN → match; one NaN → mismatch (return inf so test fails loudly).
        a_nan = np.isnan(a)
        e_nan = np.isnan(e)
        if not np.array_equal(a_nan, e_nan):
            return float("inf")
        valid = ~a_nan
        if not valid.any():
            continue
        diff = float(np.abs(a[valid] - e[valid]).max())
        max_diff = max(max_diff, diff)
    return max_diff


# ---------------------------------------------------------------------------
# Spec validation
# ---------------------------------------------------------------------------

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
    "funding_rate_level": 1,
    "funding_rate_mom": 2,
    "oi_change": 2,
    "orderbook_imbalance_L1": 1,
}

EXPERIMENTAL_NAMES = {
    "trade_imbalance",
    "oi_change",
    "orderbook_imbalance_L1",
}


@pytest.mark.parametrize("fn", ALL_FACTORS, ids=[f.__name__ for f in ALL_FACTORS])
def test_factor_spec_not_none(fn):
    assert hasattr(fn, "__factor_spec__")
    spec = fn.__factor_spec__
    assert isinstance(spec, FactorSpec)


@pytest.mark.parametrize("fn", ALL_FACTORS, ids=[f.__name__ for f in ALL_FACTORS])
def test_factor_spec_category_and_lookback(fn):
    name = fn.__name__
    assert fn.__factor_spec__.category == EXPECTED_CATEGORIES[name]
    assert fn.__factor_spec__.lookback == EXPECTED_LOOKBACKS[name]
    assert fn.__factor_spec__.name == name


@pytest.mark.parametrize(
    "fn",
    [trade_imbalance, oi_change, orderbook_imbalance_L1],
    ids=lambda f: f.__name__,
)
def test_experimental_flag_and_deprecated(fn):
    """The 3 experimental kernels carry both ``experimental=True`` and
    ``deprecated=True`` so they are hidden from the default factor list and
    excluded from automated multi-factor reports until s21 unlocks them.
    """
    spec = fn.__factor_spec__
    assert spec.experimental is True, f"{fn.__name__}: experimental must be True"
    assert spec.deprecated is True, f"{fn.__name__}: deprecated must be True"


@pytest.mark.parametrize(
    "fn",
    [
        ret_N, rsi_signal, parkinson_vol, vol_ratio,
        obv_slope, vwap_dev, amihud_illiq,
        funding_rate_level, funding_rate_mom,
    ],
    ids=lambda f: f.__name__,
)
def test_non_experimental_not_deprecated(fn):
    spec = fn.__factor_spec__
    assert spec.experimental is False
    assert spec.deprecated is False


# ---------------------------------------------------------------------------
# Numerical regression — polars kernel output vs pandas oracle
# ---------------------------------------------------------------------------

# Tolerance: AC #1 is "<= 1e-6" but pct_change/diff/log/sqrt/rolling_mean
# have multiple sources of fp accumulation across pandas/polars/numpy back-ends.
# 1e-9 is what we observe in practice; we use 1e-6 to honour the AC.
ABS_TOL = 1e-6


def test_ret_N_matches_oracle(oracle: pd.DataFrame):
    close = _input_panel(oracle, "close")
    out = ret_N(close, params={"lookback": 20})
    assert out.columns == ["ts", *SYMBOLS]
    assert _abs_diff(out, _oracle_factor(oracle, "ret_N")) <= ABS_TOL


def test_rsi_signal_matches_oracle(oracle: pd.DataFrame):
    close = _input_panel(oracle, "close")
    out = rsi_signal(close, params={"lookback": 14})
    assert out.columns == ["ts", *SYMBOLS]
    assert _abs_diff(out, _oracle_factor(oracle, "rsi_signal")) <= ABS_TOL


def test_parkinson_vol_matches_oracle(oracle: pd.DataFrame):
    high = _input_panel(oracle, "high")
    low = _input_panel(oracle, "low")
    out = parkinson_vol(high, low, params={"lookback": 20})
    assert out.columns == ["ts", *SYMBOLS]
    assert _abs_diff(out, _oracle_factor(oracle, "parkinson_vol")) <= ABS_TOL


def test_vol_ratio_matches_oracle(oracle: pd.DataFrame):
    close = _input_panel(oracle, "close")
    out = vol_ratio(close, params={"fast": 5, "slow": 20})
    assert out.columns == ["ts", *SYMBOLS]
    assert _abs_diff(out, _oracle_factor(oracle, "vol_ratio")) <= ABS_TOL


def test_obv_slope_matches_oracle(oracle: pd.DataFrame):
    close = _input_panel(oracle, "close")
    volume = _input_panel(oracle, "volume")
    out = obv_slope(close, volume, params={"lookback": 20})
    assert out.columns == ["ts", *SYMBOLS]
    assert _abs_diff(out, _oracle_factor(oracle, "obv_slope")) <= ABS_TOL


def test_vwap_dev_matches_oracle(oracle: pd.DataFrame):
    high = _input_panel(oracle, "high")
    low = _input_panel(oracle, "low")
    close = _input_panel(oracle, "close")
    volume = _input_panel(oracle, "volume")
    out = vwap_dev(high, low, close, volume, params={"lookback": 20})
    assert out.columns == ["ts", *SYMBOLS]
    assert _abs_diff(out, _oracle_factor(oracle, "vwap_dev")) <= ABS_TOL


def test_amihud_illiq_matches_oracle(oracle: pd.DataFrame):
    close = _input_panel(oracle, "close")
    volume = _input_panel(oracle, "volume")
    out = amihud_illiq(close, volume, params={"lookback": 20})
    assert out.columns == ["ts", *SYMBOLS]
    assert _abs_diff(out, _oracle_factor(oracle, "amihud_illiq")) <= ABS_TOL


def test_funding_rate_level_matches_oracle(oracle: pd.DataFrame):
    fr = _input_panel(oracle, "funding_rate")
    out = funding_rate_level(fr)
    assert out.columns == ["ts", *SYMBOLS]
    assert _abs_diff(out, _oracle_factor(oracle, "funding_rate_level")) <= ABS_TOL


def test_funding_rate_mom_matches_oracle(oracle: pd.DataFrame):
    fr = _input_panel(oracle, "funding_rate")
    out = funding_rate_mom(fr, params={"lookback": 1})
    assert out.columns == ["ts", *SYMBOLS]
    assert _abs_diff(out, _oracle_factor(oracle, "funding_rate_mom")) <= ABS_TOL


# ---------------------------------------------------------------------------
# Shape preservation — Panel result has ``ts`` + N symbol columns
# ---------------------------------------------------------------------------

NON_EXPERIMENTAL = [
    (ret_N, ("close",), {"lookback": 20}),
    (rsi_signal, ("close",), {"lookback": 14}),
    (parkinson_vol, ("high", "low"), {"lookback": 20}),
    (vol_ratio, ("close",), {"fast": 5, "slow": 20}),
    (obv_slope, ("close", "volume"), {"lookback": 20}),
    (vwap_dev, ("high", "low", "close", "volume"), {"lookback": 20}),
    (amihud_illiq, ("close", "volume"), {"lookback": 20}),
    (funding_rate_level, ("funding_rate",), {}),
    (funding_rate_mom, ("funding_rate",), {"lookback": 1}),
]


@pytest.mark.parametrize(
    "fn, fields, params",
    NON_EXPERIMENTAL,
    ids=lambda v: v.__name__ if callable(v) else "",
)
def test_output_shape_preserves_ts_and_symbols(
    oracle: pd.DataFrame, fn, fields, params
):
    panels = [_input_panel(oracle, f) for f in fields]
    out = fn(*panels, params=params) if params else fn(*panels)
    assert isinstance(out, pl.DataFrame), f"{fn.__name__}: not a polars DataFrame"
    assert out.columns == ["ts", *SYMBOLS]
    assert out.height == len(oracle)


# ---------------------------------------------------------------------------
# Experimental kernels raise — guards against silent empty output regression
# ---------------------------------------------------------------------------


def _dummy_panel(field: str, oracle: pd.DataFrame) -> pl.DataFrame:
    """Build a plausible polars Panel for an experimental input field."""
    # Reuse one of the input fields so shapes are realistic; values don't
    # matter because the kernel raises before touching them.
    base = _input_panel(oracle, "close")
    return base.rename({sym: sym for sym in SYMBOLS})


def test_trade_imbalance_raises(oracle: pd.DataFrame):
    panel = _dummy_panel("volume", oracle)
    side = _dummy_panel("volume", oracle)
    with pytest.raises(NotImplementedError, match="trade_imbalance"):
        trade_imbalance(panel, side, params={"lookback": 20})


def test_oi_change_raises(oracle: pd.DataFrame):
    panel = _dummy_panel("close", oracle)
    with pytest.raises(NotImplementedError, match="oi_change"):
        oi_change(panel, params={"lookback": 1})


def test_orderbook_imbalance_L1_raises(oracle: pd.DataFrame):
    panel = _dummy_panel("close", oracle)
    with pytest.raises(NotImplementedError, match="orderbook_imbalance_L1"):
        orderbook_imbalance_L1(panel)


# ---------------------------------------------------------------------------
# Module import smoke
# ---------------------------------------------------------------------------


def test_builtins_package_import():
    """All sub-modules importable without ImportError."""
    from tinohelm.factor.builtins import (  # noqa: F401
        crypto_data,
        crypto_funding,
        microstructure,
        momentum,
        volatility,
        volume,
    )
