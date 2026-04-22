"""Unit tests for ``tinohelm.factor.cache.FactorCache``.

Coverage matrix
---------------
1. Full miss → store (values + eval) → full hit on second lookup.
2. Key changes when ``code_hash`` changes → miss.
3. Partial hit: store only values → lookup returns factor_values_hit=True, eval_hit=False.
4. ``invalidate("factor_name")`` removes all keys for that factor, leaves others intact.
5. ``invalidate()`` removes everything.
6. NaN/Inf inside ``EvalResult`` are scrubbed to ``None`` after round-trip.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tinohelm.factor.cache import CacheHit, FactorCache
from tinohelm.factor.types import EvalConfig, EvalResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def config() -> EvalConfig:
    return EvalConfig(
        universe=("BTCUSDT-PERP", "ETHUSDT-PERP"),
        start="2024-01-01",
        end="2024-03-01",
        forward_period=5,
        quantiles=5,
        cost_bps=4.0,
        ic_freq="D",
        log_ret=False,
        params={},
    )


@pytest.fixture()
def sample_panel() -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=30, freq="D")
    rng = np.random.default_rng(42)
    return pd.DataFrame(
        rng.standard_normal((30, 2)),
        index=idx,
        columns=["BTCUSDT-PERP", "ETHUSDT-PERP"],
    )


@pytest.fixture()
def sample_eval_result() -> EvalResult:
    return EvalResult(
        ic_mean=0.05,
        ic_std=0.10,
        ir=0.50,
        ic_tstat=1.23,
        ic_positive_pct=0.55,
        ic_max_abs=0.30,
        half_life=10,
        quantile_pnl={"Q1": 0.01, "Q5": 0.04},
        is_monotonic=True,
        turnover=0.20,
        turnover_annualized=50.0,
        fee_drag_monthly=0.002,
        rating=2,
        ic_series=[{"date": "2024-01-15", "ic": 0.06}],
        ic_decay=[{"lag": 1, "ic": 0.07}],
    )


@pytest.fixture()
def cache(tmp_path: Path) -> FactorCache:
    return FactorCache(cache_root=tmp_path / "cache")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _key(
    cache: FactorCache,
    config: EvalConfig,
    *,
    name: str = "momentum",
    code_hash: str = "abc123",
    data_range: tuple = ("2024-01-01", "2024-03-01"),
) -> str:
    return FactorCache.build_key(name, code_hash, config, data_range)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBuildKey:
    def test_same_inputs_same_key(self, config: EvalConfig) -> None:
        k1 = FactorCache.build_key("ret_5", "hash1", config, ("2024-01-01", "2024-03-01"))
        k2 = FactorCache.build_key("ret_5", "hash1", config, ("2024-01-01", "2024-03-01"))
        assert k1 == k2

    def test_different_code_hash_different_key(self, config: EvalConfig) -> None:
        k1 = FactorCache.build_key("ret_5", "hash1", config, ("2024-01-01", "2024-03-01"))
        k2 = FactorCache.build_key("ret_5", "hash2", config, ("2024-01-01", "2024-03-01"))
        assert k1 != k2

    def test_different_name_different_key(self, config: EvalConfig) -> None:
        k1 = FactorCache.build_key("factor_a", "hash1", config, ("2024-01-01", "2024-03-01"))
        k2 = FactorCache.build_key("factor_b", "hash1", config, ("2024-01-01", "2024-03-01"))
        assert k1 != k2

    def test_key_is_hex_string(self, config: EvalConfig) -> None:
        k = FactorCache.build_key("ret_5", "hash1", config, ("2024-01-01", "2024-03-01"))
        assert len(k) == 64
        int(k, 16)  # raises if not valid hex


class TestFullMissThenHit:
    def test_lookup_miss_before_store(self, cache: FactorCache, config: EvalConfig) -> None:
        key = _key(cache, config)
        assert cache.lookup(key) is None

    def test_store_then_full_hit(
        self,
        cache: FactorCache,
        config: EvalConfig,
        sample_panel: pd.DataFrame,
        sample_eval_result: EvalResult,
    ) -> None:
        key = _key(cache, config)

        cache.store(
            key,
            factor_name="momentum",
            code_hash="abc123",
            factor_values=sample_panel,
            eval_result=sample_eval_result,
        )

        hit = cache.lookup(key)
        assert hit is not None
        assert hit.factor_values_hit is True
        assert hit.eval_hit is True
        assert isinstance(hit.factor_values, pd.DataFrame)
        assert hit.factor_values.shape == sample_panel.shape
        assert isinstance(hit.eval_result, EvalResult)
        assert hit.eval_result.ic_mean == pytest.approx(0.05, rel=1e-6)
        assert hit.eval_result.ir == pytest.approx(0.50, rel=1e-6)
        assert hit.eval_result.is_monotonic is True


class TestCodeHashChangeInvalidatesKey:
    def test_new_hash_yields_miss(
        self,
        cache: FactorCache,
        config: EvalConfig,
        sample_panel: pd.DataFrame,
        sample_eval_result: EvalResult,
    ) -> None:
        key_v1 = FactorCache.build_key("ret_5", "hash_v1", config, ("2024-01-01", "2024-03-01"))
        cache.store(
            key_v1,
            factor_name="ret_5",
            code_hash="hash_v1",
            factor_values=sample_panel,
            eval_result=sample_eval_result,
        )

        key_v2 = FactorCache.build_key("ret_5", "hash_v2", config, ("2024-01-01", "2024-03-01"))
        # Different key — must miss
        assert cache.lookup(key_v2) is None
        # Original key still hits
        assert cache.lookup(key_v1) is not None


class TestPartialHit:
    def test_values_only_store(
        self,
        cache: FactorCache,
        config: EvalConfig,
        sample_panel: pd.DataFrame,
    ) -> None:
        key = _key(cache, config)
        cache.store(
            key,
            factor_name="momentum",
            code_hash="abc123",
            factor_values=sample_panel,
            eval_result=None,
        )

        hit = cache.lookup(key)
        assert hit is not None
        assert hit.factor_values_hit is True
        assert hit.eval_hit is False
        assert hit.factor_values is not None
        assert hit.eval_result is None

    def test_eval_only_store(
        self,
        cache: FactorCache,
        config: EvalConfig,
        sample_eval_result: EvalResult,
    ) -> None:
        key = _key(cache, config)
        cache.store(
            key,
            factor_name="momentum",
            code_hash="abc123",
            factor_values=None,
            eval_result=sample_eval_result,
        )

        hit = cache.lookup(key)
        assert hit is not None
        assert hit.factor_values_hit is False
        assert hit.eval_hit is True
        assert hit.factor_values is None
        assert hit.eval_result is not None


class TestInvalidate:
    def _store_factor(
        self,
        cache: FactorCache,
        config: EvalConfig,
        factor_name: str,
        code_hash: str,
        panel: pd.DataFrame,
        result: EvalResult,
    ) -> str:
        key = FactorCache.build_key(factor_name, code_hash, config, ("2024-01-01", "2024-03-01"))
        cache.store(key, factor_name=factor_name, code_hash=code_hash,
                    factor_values=panel, eval_result=result)
        return key

    def test_invalidate_by_name_removes_only_that_factor(
        self,
        cache: FactorCache,
        config: EvalConfig,
        sample_panel: pd.DataFrame,
        sample_eval_result: EvalResult,
    ) -> None:
        key_a1 = self._store_factor(cache, config, "factor_a", "h1", sample_panel, sample_eval_result)
        key_a2 = self._store_factor(cache, config, "factor_a", "h2", sample_panel, sample_eval_result)
        key_b = self._store_factor(cache, config, "factor_b", "h1", sample_panel, sample_eval_result)

        removed = cache.invalidate("factor_a")
        assert removed == 2
        assert cache.lookup(key_a1) is None
        assert cache.lookup(key_a2) is None
        # factor_b must still be present
        assert cache.lookup(key_b) is not None

    def test_invalidate_all(
        self,
        cache: FactorCache,
        config: EvalConfig,
        sample_panel: pd.DataFrame,
        sample_eval_result: EvalResult,
    ) -> None:
        key_a = self._store_factor(cache, config, "factor_a", "h1", sample_panel, sample_eval_result)
        key_b = self._store_factor(cache, config, "factor_b", "h1", sample_panel, sample_eval_result)

        removed = cache.invalidate()
        assert removed == 2
        assert cache.lookup(key_a) is None
        assert cache.lookup(key_b) is None

    def test_invalidate_returns_zero_when_no_match(
        self,
        cache: FactorCache,
        config: EvalConfig,
        sample_panel: pd.DataFrame,
        sample_eval_result: EvalResult,
    ) -> None:
        self._store_factor(cache, config, "factor_a", "h1", sample_panel, sample_eval_result)
        removed = cache.invalidate("nonexistent")
        assert removed == 0


class TestNaNInfScrubbing:
    def test_nan_inf_scrubbed_in_eval_result(
        self,
        cache: FactorCache,
        config: EvalConfig,
    ) -> None:
        dirty = EvalResult(
            ic_mean=float("nan"),
            ic_std=float("inf"),
            ir=float("-inf"),
            ic_tstat=1.5,
            ic_positive_pct=0.6,
            ic_max_abs=0.4,
            half_life=None,
            quantile_pnl={"Q1": float("nan"), "Q5": 0.03},
            distribution_stats={"mean": float("nan"), "std": 1.0},
        )

        key = FactorCache.build_key("dirty_factor", "hx", config, ("2024-01-01", "2024-03-01"))
        cache.store(key, factor_name="dirty_factor", code_hash="hx", eval_result=dirty)

        hit = cache.lookup(key)
        assert hit is not None
        assert hit.eval_hit is True
        r = hit.eval_result
        assert r is not None

        # NaN/Inf scalar fields must be None after round-trip
        assert r.ic_mean is None
        assert r.ic_std is None
        assert r.ir is None

        # Non-NaN fields preserved
        assert r.ic_tstat == pytest.approx(1.5, rel=1e-6)

        # Dict values scrubbed
        assert r.quantile_pnl["Q1"] is None
        assert r.quantile_pnl["Q5"] == pytest.approx(0.03, rel=1e-6)
        assert r.distribution_stats["mean"] is None
        assert r.distribution_stats["std"] == pytest.approx(1.0, rel=1e-6)

    def test_valid_floats_preserved(
        self,
        cache: FactorCache,
        config: EvalConfig,
        sample_eval_result: EvalResult,
    ) -> None:
        key = FactorCache.build_key("clean_factor", "h0", config, ("2024-01-01", "2024-03-01"))
        cache.store(key, factor_name="clean_factor", code_hash="h0", eval_result=sample_eval_result)

        hit = cache.lookup(key)
        assert hit is not None
        r = hit.eval_result
        assert r is not None
        assert r.ic_mean == pytest.approx(0.05, rel=1e-6)
        assert r.ic_std == pytest.approx(0.10, rel=1e-6)
        assert r.rating == 2
        assert r.half_life == 10
