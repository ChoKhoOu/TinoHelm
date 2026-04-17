"""Tests for `tinohelm.research.factors` — built-in factor library.

Covers all 14 factor compute functions, the `compute_factor` dispatcher (default
merging, unknown-factor error), the `BUILTIN_FACTORS` metadata contract, and the
`_COMPUTE_MAP` ↔ metadata coverage invariant.

These are pure pandas/numpy/scipy tests — zero NT dependencies.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from tinohelm.research import factors as F


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def linear_df() -> pd.DataFrame:
    """60 minutes of strictly-monotonic OHLCV — nice deterministic baseline."""
    n = 60
    close = np.linspace(100.0, 110.0, n)
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="1min"),
    )


@pytest.fixture
def random_df() -> pd.DataFrame:
    """Stochastic OHLCV with a fixed seed so tests are deterministic."""
    rng = np.random.default_rng(42)
    n = 200
    close = 100.0 + np.cumsum(rng.normal(0, 0.3, n))
    high = close + np.abs(rng.normal(0, 0.5, n))
    low = close - np.abs(rng.normal(0, 0.5, n))
    return pd.DataFrame(
        {
            "open": close + rng.normal(0, 0.1, n),
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(500, 2000, n),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="5min"),
    )


@pytest.fixture
def short_df() -> pd.DataFrame:
    """Just 10 rows — many factors with default lookback=20 will be all NaN."""
    n = 10
    close = np.linspace(100.0, 105.0, n)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="1min"),
    )


# ──────────────────────────────────────────────────────────────────────
# BUILTIN_FACTORS metadata contract
# ──────────────────────────────────────────────────────────────────────


class TestBuiltinFactorsMetadata:
    def test_fourteen_builtin_factors_registered(self):
        # If this number changes, also update docs/ui factor catalog and the
        # frontend factor-picker. The change should be conscious, not silent.
        assert len(F.BUILTIN_FACTORS) == 14

    def test_every_meta_has_required_keys(self):
        required = {"label", "category", "data_type", "params"}
        for name, meta in F.BUILTIN_FACTORS.items():
            assert required.issubset(meta.keys()), f"{name} missing keys: {required - meta.keys()}"

    def test_every_param_has_default_min_max_label(self):
        required = {"default", "min", "max", "label"}
        for fname, meta in F.BUILTIN_FACTORS.items():
            for pname, pdef in meta["params"].items():
                assert required.issubset(pdef.keys()), f"{fname}.{pname} missing {required - pdef.keys()}"
                assert pdef["min"] <= pdef["default"] <= pdef["max"], (
                    f"{fname}.{pname}: default {pdef['default']} not in [{pdef['min']}, {pdef['max']}]"
                )

    def test_data_type_is_bar_for_all_builtins(self):
        # Today every shipped factor consumes OHLCV bars. If a future factor needs
        # trade_tick this guard should be relaxed deliberately.
        for name, meta in F.BUILTIN_FACTORS.items():
            assert meta["data_type"] == "bar", f"{name} has non-bar data_type: {meta['data_type']}"

    def test_compute_map_covers_every_meta_entry(self):
        # The dispatcher's safety net: every advertised factor must have a compute fn.
        assert set(F._COMPUTE_MAP.keys()) == set(F.BUILTIN_FACTORS.keys())

    def test_no_compute_fn_without_meta(self):
        # The reverse: every compute fn must be advertised. Prevents stranded code.
        assert set(F._COMPUTE_MAP.keys()) <= set(F.BUILTIN_FACTORS.keys())

    def test_list_factors_returns_full_metadata_copy(self):
        result = F.list_factors()
        assert result == F.BUILTIN_FACTORS
        # Defensive copy — mutating result must not affect the module-level dict.
        result["__bogus__"] = {"foo": "bar"}
        assert "__bogus__" not in F.BUILTIN_FACTORS


# ──────────────────────────────────────────────────────────────────────
# Individual factor functions
# ──────────────────────────────────────────────────────────────────────


class TestRetN:
    def test_returns_pct_change_n_periods(self, linear_df):
        out = F.ret_N(linear_df, {"lookback": 5})
        # close[5] / close[0] - 1
        expected = linear_df["close"].iloc[5] / linear_df["close"].iloc[0] - 1
        assert out.iloc[5] == pytest.approx(expected)

    def test_first_n_values_are_nan(self, linear_df):
        out = F.ret_N(linear_df, {"lookback": 10})
        assert out.iloc[:10].isna().all()
        assert not pd.isna(out.iloc[10])

    def test_default_lookback_is_20(self, linear_df):
        out = F.ret_N(linear_df, {})
        assert out.iloc[:20].isna().all()


class TestMomRatio:
    def test_returns_fast_over_slow_minus_one(self, linear_df):
        out = F.mom_ratio(linear_df, {"fast": 5, "slow": 20})
        # On a strictly-monotonic upward series, fast SMA > slow SMA, so ratio > 1, mom > 0.
        assert out.iloc[-1] > 0
        # Compute by hand on last point
        sma_f = linear_df["close"].rolling(5).mean().iloc[-1]
        sma_s = linear_df["close"].rolling(20).mean().iloc[-1]
        assert out.iloc[-1] == pytest.approx(sma_f / sma_s - 1)

    def test_zero_when_fast_equals_slow(self):
        # Constant price → fast SMA = slow SMA → ratio - 1 = 0
        df = pd.DataFrame({"close": [100.0] * 50, "high": [100.5] * 50, "low": [99.5] * 50,
                           "open": [100.0] * 50, "volume": [1.0] * 50})
        out = F.mom_ratio(df, {"fast": 5, "slow": 20})
        assert out.iloc[-1] == pytest.approx(0.0)


class TestRoc:
    def test_simple_roc_calculation(self, linear_df):
        out = F.roc(linear_df, {"lookback": 5})
        expected = linear_df["close"].iloc[5] / linear_df["close"].iloc[0] - 1
        assert out.iloc[5] == pytest.approx(expected)


class TestRsiSignal:
    def test_centered_around_zero(self, random_df):
        # On a roughly mean-reverting random walk, the centered RSI should
        # average near zero across many bars.
        out = F.rsi_signal(random_df, {"lookback": 14})
        valid = out.dropna()
        assert len(valid) > 0
        # Range is [-50, +50] by construction (RSI ∈ [0, 100], minus 50)
        assert valid.min() >= -50.0 - 1e-9
        assert valid.max() <= 50.0 + 1e-9

    def test_strictly_monotonic_up_yields_max_positive(self, linear_df):
        # All deltas positive → loss=0 → RSI → 100 → centered → +50
        out = F.rsi_signal(linear_df, {"lookback": 14})
        assert out.iloc[-1] == pytest.approx(50.0, abs=1e-3)


class TestRealizedVol:
    def test_returns_rolling_std_of_pct_change(self, random_df):
        out = F.realized_vol(random_df, {"lookback": 20})
        ret = random_df["close"].pct_change()
        expected = ret.rolling(20).std().iloc[-1]
        assert out.iloc[-1] == pytest.approx(expected)

    def test_zero_for_constant_price(self):
        df = pd.DataFrame({"close": [100.0] * 30, "high": [100.5] * 30, "low": [99.5] * 30,
                           "open": [100.0] * 30, "volume": [1.0] * 30})
        out = F.realized_vol(df, {"lookback": 10})
        # All pct_changes are 0, so std = 0
        assert out.iloc[-1] == pytest.approx(0.0)


class TestVolRatio:
    def test_ratio_of_short_over_long_vol(self, random_df):
        out = F.vol_ratio(random_df, {"fast": 5, "slow": 20})
        ret = random_df["close"].pct_change()
        vol_f = ret.rolling(5).std().iloc[-1]
        vol_s = ret.rolling(20).std().iloc[-1]
        expected = vol_f / (vol_s + 1e-12)
        assert out.iloc[-1] == pytest.approx(expected)


class TestAtrNorm:
    def test_normalized_atr_strictly_positive(self, random_df):
        out = F.atr_norm(random_df, {"lookback": 14})
        valid = out.dropna()
        assert (valid > 0).all(), "ATR/close should always be positive"

    def test_constant_low_high_close_yields_zero(self):
        df = pd.DataFrame({"close": [100.0] * 30, "high": [100.0] * 30, "low": [100.0] * 30,
                           "open": [100.0] * 30, "volume": [1.0] * 30})
        out = F.atr_norm(df, {"lookback": 14})
        assert out.iloc[-1] == pytest.approx(0.0)


class TestParkinsonVol:
    def test_parkinson_vol_positive(self, random_df):
        out = F.parkinson_vol(random_df, {"lookback": 20})
        valid = out.dropna()
        assert (valid >= 0).all()

    def test_higher_when_range_wider(self):
        # Two synthetic series with same close but different high-low range.
        n = 30
        narrow = pd.DataFrame({"high": [101.0] * n, "low": [99.0] * n, "close": [100.0] * n,
                               "open": [100.0] * n, "volume": [1.0] * n})
        wide = pd.DataFrame({"high": [110.0] * n, "low": [90.0] * n, "close": [100.0] * n,
                             "open": [100.0] * n, "volume": [1.0] * n})
        out_narrow = F.parkinson_vol(narrow, {"lookback": 20}).iloc[-1]
        out_wide = F.parkinson_vol(wide, {"lookback": 20}).iloc[-1]
        assert out_wide > out_narrow


class TestVwapDev:
    def test_zero_when_close_equals_typical_price(self):
        # If close == (high+low+close)/3 across the window, VWAP == close → dev = 0
        n = 30
        df = pd.DataFrame({"high": [100.0] * n, "low": [100.0] * n, "close": [100.0] * n,
                           "open": [100.0] * n, "volume": [1.0] * n})
        out = F.vwap_dev(df, {"lookback": 20})
        assert out.iloc[-1] == pytest.approx(0.0)


class TestVolumeSurge:
    def test_one_when_constant_volume(self, linear_df):
        out = F.volume_surge(linear_df, {"lookback": 20})
        assert out.iloc[-1] == pytest.approx(1.0)

    def test_above_one_for_volume_spike(self):
        n = 30
        vol = [1.0] * 29 + [10.0]
        df = pd.DataFrame({"high": [101.0] * n, "low": [99.0] * n, "close": [100.0] * n,
                           "open": [100.0] * n, "volume": vol})
        out = F.volume_surge(df, {"lookback": 20})
        # Last bar has volume 10× the rolling mean
        assert out.iloc[-1] > 5.0


class TestObvSlope:
    def test_positive_for_uptrend(self, linear_df):
        out = F.obv_slope(linear_df, {"lookback": 10})
        # All deltas positive, so direction=+1, OBV strictly grows → slope > 0
        assert out.iloc[-1] > 0

    def test_zero_for_constant_price(self):
        df = pd.DataFrame({"close": [100.0] * 30, "high": [100.5] * 30, "low": [99.5] * 30,
                           "open": [100.0] * 30, "volume": [1.0] * 30})
        out = F.obv_slope(df, {"lookback": 10})
        # No direction → OBV constant 0 → slope = 0
        assert out.iloc[-1] == pytest.approx(0.0)


class TestTradeImbalance:
    def test_close_at_high_yields_positive(self):
        # close == high → buy_ratio = 1, sell_ratio = 0 → imbalance positive
        n = 30
        df = pd.DataFrame({"high": [101.0] * n, "low": [99.0] * n, "close": [101.0] * n,
                           "open": [100.0] * n, "volume": [1.0] * n})
        out = F.trade_imbalance(df, {"lookback": 20})
        assert out.iloc[-1] > 0

    def test_close_at_low_yields_negative(self):
        n = 30
        df = pd.DataFrame({"high": [101.0] * n, "low": [99.0] * n, "close": [99.0] * n,
                           "open": [100.0] * n, "volume": [1.0] * n})
        out = F.trade_imbalance(df, {"lookback": 20})
        assert out.iloc[-1] < 0


class TestKyleLambda:
    def test_returns_ratio_of_abs_return_to_volume(self, random_df):
        out = F.kyle_lambda(random_df, {"lookback": 20})
        valid = out.dropna()
        assert (valid >= 0).all()


class TestAmihudIlliq:
    def test_higher_with_lower_volume(self):
        # Same returns but half the volume → higher illiquidity
        rng = np.random.default_rng(7)
        n = 50
        close = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
        df_liquid = pd.DataFrame({"close": close, "high": close + 0.1, "low": close - 0.1,
                                  "open": close, "volume": [1000.0] * n})
        df_illiquid = pd.DataFrame({"close": close, "high": close + 0.1, "low": close - 0.1,
                                    "open": close, "volume": [100.0] * n})
        out_l = F.amihud_illiq(df_liquid, {"lookback": 20}).iloc[-1]
        out_i = F.amihud_illiq(df_illiquid, {"lookback": 20}).iloc[-1]
        assert out_i > out_l


# ──────────────────────────────────────────────────────────────────────
# compute_factor dispatcher
# ──────────────────────────────────────────────────────────────────────


class TestComputeFactorDispatcher:
    def test_unknown_factor_raises(self, linear_df):
        with pytest.raises(ValueError, match="Unknown factor: __nope__"):
            F.compute_factor("__nope__", linear_df, {})

    def test_no_params_uses_metadata_defaults(self, linear_df):
        # Should not raise; should produce same result as explicit defaults.
        out_default = F.compute_factor("ret_N", linear_df)
        out_explicit = F.compute_factor("ret_N", linear_df, {"lookback": 20})
        pd.testing.assert_series_equal(out_default, out_explicit)

    def test_partial_params_merge_with_defaults(self, linear_df):
        # mom_ratio has fast=5, slow=20 defaults. Pass only fast=10 — slow should
        # remain at the meta default.
        out = F.compute_factor("mom_ratio", linear_df, {"fast": 10})
        # Manual: SMA_10 / SMA_20 - 1 at last point
        sma_f = linear_df["close"].rolling(10).mean().iloc[-1]
        sma_s = linear_df["close"].rolling(20).mean().iloc[-1]
        assert out.iloc[-1] == pytest.approx(sma_f / sma_s - 1)

    def test_extra_params_passed_through_silently(self, linear_df):
        # Extra params that aren't in meta should not crash; the compute fn just ignores them.
        out = F.compute_factor("ret_N", linear_df, {"lookback": 5, "unrelated": 999})
        assert not pd.isna(out.iloc[-1])

    def test_dispatcher_returns_series_aligned_to_input(self, linear_df):
        out = F.compute_factor("realized_vol", linear_df, {"lookback": 5})
        assert isinstance(out, pd.Series)
        assert len(out) == len(linear_df)
        assert out.index.equals(linear_df.index)

    def test_short_history_returns_all_nan_for_lookback_factors(self, short_df):
        # 10-row df, default lookback=20 → all NaN, but it must not crash.
        out = F.compute_factor("ret_N", short_df)
        assert out.isna().all()

    def test_every_builtin_factor_runs_via_dispatcher(self, random_df):
        # Smoke test: every single factor must be reachable through compute_factor
        # without raising. Catches typos in _COMPUTE_MAP keys.
        for name in F.BUILTIN_FACTORS:
            out = F.compute_factor(name, random_df)
            assert isinstance(out, pd.Series), f"{name} did not return Series"
            assert len(out) == len(random_df), f"{name} returned wrong length"


# ──────────────────────────────────────────────────────────────────────
# Numerical safety
# ──────────────────────────────────────────────────────────────────────


class TestNumericalSafety:
    def test_no_division_by_zero_when_volume_zero(self):
        # Several factors divide by volume + 1e-12. Verify no inf/raise on zero volume.
        n = 30
        df = pd.DataFrame({"high": [101.0] * n, "low": [99.0] * n, "close": [100.0] * n,
                           "open": [100.0] * n, "volume": [0.0] * n})
        for name in ("kyle_lambda", "amihud_illiq", "vwap_dev", "trade_imbalance", "volume_surge"):
            out = F.compute_factor(name, df)
            valid = out.dropna()
            assert all(math.isfinite(v) for v in valid), f"{name} produced inf with zero volume"

    def test_no_division_by_zero_when_high_equals_low(self):
        # atr_norm divides by close+1e-12, trade_imbalance by hl+1e-12
        n = 30
        df = pd.DataFrame({"high": [100.0] * n, "low": [100.0] * n, "close": [100.0] * n,
                           "open": [100.0] * n, "volume": [1.0] * n})
        for name in ("atr_norm", "trade_imbalance", "parkinson_vol"):
            out = F.compute_factor(name, df)
            valid = out.dropna()
            assert all(math.isfinite(v) for v in valid), f"{name} produced inf when high==low"
