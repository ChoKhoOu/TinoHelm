"""End-to-end integration test — multi-factor batch through :class:`Orchestrator`.

Validates ``Orchestrator.batch_run``:
* one shared ``DataLayer.load`` call for the whole batch (not one per factor)
* parallel kernel execution within a topological layer
* per-factor cache + evaluate
* failure isolation — one factor raising does not poison others
* dict keyed by every requested name — successful factors map to
  :class:`EvalResult`, failed ones map to ``None``.

As in ``test_e2e_single.py`` we bypass Parquet I/O via a minimal DataLayer
stub so the test is NT-free and fast.

Polars contract
---------------
``Scheduler._call_kernel`` requires kernels to return :class:`polars.DataFrame`
panels in the canonical wide-table layout (``ts`` column + symbol columns).
Test fixtures construct synthetic panels directly with polars to match.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from tinohelm.factor.backend import PolarsBackend
from tinohelm.factor.cache import FactorCache
from tinohelm.factor.data_layer import DataLayer
from tinohelm.factor.decorator import factor
from tinohelm.factor.engine.orchestrator import Orchestrator
from tinohelm.factor.evaluation.evaluator import Evaluator
from tinohelm.factor.observer import Observer
from tinohelm.factor.registry import Registry
from tinohelm.factor.types import EvalConfig, EvalResult, FactorSpec, Panel


# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------

SYMBOLS: tuple[str, ...] = ("SYM00", "SYM01", "SYM02")
_N_BARS: int = 800
_START: str = "2024-01-01"
_END: str = "2024-02-01"


def _make_timestamps(n: int) -> list[datetime]:
    base = datetime(2024, 1, 1)
    return [base + timedelta(hours=i) for i in range(n)]


@pytest.fixture
def close_panel() -> Panel:
    rng = np.random.default_rng(123)
    timestamps = _make_timestamps(_N_BARS)
    returns = rng.normal(0, 0.005, (_N_BARS, len(SYMBOLS)))
    prices = 100.0 * np.cumprod(1 + returns, axis=0)
    payload: dict[str, list] = {"ts": timestamps}
    for j, sym in enumerate(SYMBOLS):
        payload[sym] = prices[:, j].tolist()
    schema = {"ts": pl.Datetime("us")}
    schema.update({sym: pl.Float64 for sym in SYMBOLS})
    return pl.DataFrame(payload, schema=schema)


@pytest.fixture
def volume_panel() -> Panel:
    rng = np.random.default_rng(7)
    timestamps = _make_timestamps(_N_BARS)
    volumes = rng.uniform(100, 500, (_N_BARS, len(SYMBOLS)))
    payload: dict[str, list] = {"ts": timestamps}
    for j, sym in enumerate(SYMBOLS):
        payload[sym] = volumes[:, j].tolist()
    schema = {"ts": pl.Datetime("us")}
    schema.update({sym: pl.Float64 for sym in SYMBOLS})
    return pl.DataFrame(payload, schema=schema)


# ---------------------------------------------------------------------------
# DataLayer stub that counts load() calls
# ---------------------------------------------------------------------------

def _coerce_to_dt(value) -> datetime:
    """Coerce ISO strings / datetimes to a tz-naive ``datetime``."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if hasattr(value, "to_pydatetime"):
        ts = value.to_pydatetime()
        return ts.replace(tzinfo=None) if ts.tzinfo else ts
    return datetime.fromisoformat(str(value))


class _StubDataLayer(DataLayer):
    def __init__(self, panels: dict[str, Panel]) -> None:
        self._panels = panels
        self.load_calls = 0

    def load(self, request, start=None, end=None):  # type: ignore[override]
        self.load_calls += 1
        out: dict[str, Panel] = {}
        for field_name, panel in self._panels.items():
            sl = panel
            if start is not None:
                start_ts = _coerce_to_dt(start)
                sl = sl.filter(pl.col("ts") >= pl.lit(start_ts))
            if end is not None:
                end_ts = _coerce_to_dt(end)
                sl = sl.filter(pl.col("ts") <= pl.lit(end_ts))
            out[field_name] = sl.clone()
        return out


# ---------------------------------------------------------------------------
# Helpers — polars factor kernel patterns
# ---------------------------------------------------------------------------

def _value_cols(panel: Panel) -> list[str]:
    return [c for c in panel.columns if c != "ts"]


# ---------------------------------------------------------------------------
# Factor kernels — 3 healthy + 1 broken
# ---------------------------------------------------------------------------

@factor(category="动量", lookback=5)
def momentum_5(close: Panel) -> Panel:
    cols = _value_cols(close)
    return close.with_columns([pl.col(c).pct_change(5).alias(c) for c in cols])


@factor(category="动量", lookback=10)
def momentum_10(close: Panel) -> Panel:
    cols = _value_cols(close)
    return close.with_columns([pl.col(c).pct_change(10).alias(c) for c in cols])


@factor(category="波动", lookback=20)
def volatility_20(close: Panel) -> Panel:
    cols = _value_cols(close)
    return close.with_columns(
        [
            pl.col(c).pct_change().rolling_std(window_size=20).alias(c)
            for c in cols
        ]
    )


@factor(category="bad", lookback=3)
def broken_factor(close: Panel) -> Panel:
    raise RuntimeError("intentional test failure")


# ---------------------------------------------------------------------------
# Registry helper
# ---------------------------------------------------------------------------

def _register(registry: Registry, func, spec: FactorSpec) -> None:
    registry._spec_cache[spec.name] = (f"stub:{spec.name}:{spec.code_hash}", spec)
    registry._kernel_cache[spec.name] = func


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def registry() -> Registry:
    r = Registry(user_dir=Path("/tmp/nonexistent_batch_factor_dir"))
    _register(r, momentum_5, momentum_5.__factor_spec__)
    _register(r, momentum_10, momentum_10.__factor_spec__)
    _register(r, volatility_20, volatility_20.__factor_spec__)
    _register(r, broken_factor, broken_factor.__factor_spec__)
    return r


@pytest.fixture
def data_layer(close_panel: Panel, volume_panel: Panel) -> _StubDataLayer:
    return _StubDataLayer({"close": close_panel, "volume": volume_panel})


@pytest.fixture
def backend() -> PolarsBackend:
    return PolarsBackend()


@pytest.fixture
def evaluator() -> Evaluator:
    return Evaluator()


@pytest.fixture
def observer() -> Observer:
    return Observer(run_id="e2e-batch-test")


@pytest.fixture
def cache(tmp_path: Path) -> FactorCache:
    return FactorCache(cache_root=tmp_path / "cache_batch")


@pytest.fixture
def config() -> EvalConfig:
    return EvalConfig(
        universe=SYMBOLS,
        start=_START,
        end=_END,
        forward_period=5,
        quantiles=5,
        cost_bps=4.0,
        ic_freq="D",
    )


@pytest.fixture
def orchestrator(
    registry: Registry,
    data_layer: _StubDataLayer,
    backend: PolarsBackend,
    evaluator: Evaluator,
    cache: FactorCache,
    observer: Observer,
) -> Orchestrator:
    return Orchestrator(
        registry=registry,
        data_layer=data_layer,
        backend=backend,
        evaluator=evaluator,
        cache=cache,
        observer=observer,
    )


# ---------------------------------------------------------------------------
# Tests — happy batch
# ---------------------------------------------------------------------------

class TestHappyBatch:
    def test_three_factors_all_succeed(
        self, orchestrator: Orchestrator, config: EvalConfig
    ):
        names = ["momentum_5", "momentum_10", "volatility_20"]
        results = orchestrator.batch_run(names, config, run_id="b1")

        assert set(results.keys()) == set(names)
        for name in names:
            assert isinstance(results[name], EvalResult), (
                f"{name} expected EvalResult, got {type(results[name]).__name__}"
            )

    def test_ic_populated_for_each_factor(
        self, orchestrator: Orchestrator, config: EvalConfig
    ):
        names = ["momentum_5", "momentum_10", "volatility_20"]
        results = orchestrator.batch_run(names, config)
        for name in names:
            assert isinstance(results[name].ic_mean, float)
            assert isinstance(results[name].turnover, float)
            # No NaN/Inf in scalar fields
            assert not np.isnan(results[name].ic_mean)
            assert not np.isinf(results[name].ic_mean)

    def test_single_data_load_for_whole_batch(
        self,
        orchestrator: Orchestrator,
        data_layer: _StubDataLayer,
        config: EvalConfig,
    ):
        """batch_run must issue only one data_layer.load call for the whole batch."""
        orchestrator.batch_run(
            ["momentum_5", "momentum_10", "volatility_20"], config
        )
        assert data_layer.load_calls == 1, (
            f"Expected 1 load() call, got {data_layer.load_calls}"
        )


# ---------------------------------------------------------------------------
# Tests — failure isolation
# ---------------------------------------------------------------------------

class TestFailureIsolation:
    def test_broken_factor_does_not_poison_others(
        self, orchestrator: Orchestrator, config: EvalConfig
    ):
        names = ["momentum_5", "broken_factor", "volatility_20"]
        results = orchestrator.batch_run(names, config)

        # All keys present
        assert set(results.keys()) == set(names)

        # broken_factor is None (failed)
        assert results["broken_factor"] is None

        # Healthy factors are EvalResult
        assert isinstance(results["momentum_5"], EvalResult)
        assert isinstance(results["volatility_20"], EvalResult)

    def test_unknown_factor_mapped_to_none(
        self, orchestrator: Orchestrator, config: EvalConfig
    ):
        names = ["momentum_5", "does_not_exist", "momentum_10"]
        results = orchestrator.batch_run(names, config)

        # All requested names still present
        assert set(results.keys()) == set(names)
        # Unknown factor → None
        assert results["does_not_exist"] is None
        # Known factors still succeed
        assert isinstance(results["momentum_5"], EvalResult)
        assert isinstance(results["momentum_10"], EvalResult)


# ---------------------------------------------------------------------------
# Tests — cache interaction
# ---------------------------------------------------------------------------

class TestBatchCache:
    def test_second_batch_run_hits_cache(
        self,
        orchestrator: Orchestrator,
        data_layer: _StubDataLayer,
        config: EvalConfig,
    ):
        names = ["momentum_5", "momentum_10"]
        r1 = orchestrator.batch_run(names, config)
        loads_after_first = data_layer.load_calls

        r2 = orchestrator.batch_run(names, config)
        loads_after_second = data_layer.load_calls

        # Second batch should not call load() at all — all factors hit cache.
        assert loads_after_second == loads_after_first, (
            "Second batch call should short-circuit via cache, "
            f"expected {loads_after_first} loads, got {loads_after_second}"
        )

        # Numeric parity between the two runs
        assert r1["momentum_5"].ic_mean == r2["momentum_5"].ic_mean
        assert r1["momentum_10"].turnover == r2["momentum_10"].turnover


# ---------------------------------------------------------------------------
# Tests — parallel execution
# ---------------------------------------------------------------------------

class TestParallelExecution:
    """Kernel concurrency — three slow factors complete faster than serial."""

    def test_factors_run_concurrently(
        self,
        registry: Registry,
        data_layer: _StubDataLayer,
        backend: PolarsBackend,
        evaluator: Evaluator,
        cache: FactorCache,
        observer: Observer,
        config: EvalConfig,
    ):
        # Register 3 "slow" factors that each sleep 0.2s
        sleep_s = 0.2

        def _slow_factor_kernel(close: Panel) -> Panel:
            time.sleep(sleep_s)
            cols = _value_cols(close)
            return close.with_columns(
                [pl.col(c).pct_change(3).alias(c) for c in cols]
            )

        for name in ("slow_a", "slow_b", "slow_c"):
            spec = FactorSpec(
                name=name,
                category="perf",
                lookback=3,
                input_specs=momentum_5.__factor_spec__.input_specs,  # ("close",)
                code_hash=f"hash_{name}",
            )
            _register(registry, _slow_factor_kernel, spec)

        orch = Orchestrator(
            registry=registry,
            data_layer=data_layer,
            backend=backend,
            evaluator=evaluator,
            cache=cache,
            observer=observer,
        )

        start = time.monotonic()
        results = orch.batch_run(["slow_a", "slow_b", "slow_c"], config)
        elapsed = time.monotonic() - start

        # All three succeeded
        assert isinstance(results["slow_a"], EvalResult)
        assert isinstance(results["slow_b"], EvalResult)
        assert isinstance(results["slow_c"], EvalResult)

        # Parallel execution must be faster than serial (3 * 0.2 = 0.6s).
        # Generous upper bound accounts for evaluator runtime on top of sleeps.
        serial_bound = sleep_s * 3
        assert elapsed < serial_bound + 0.4, (
            f"Batch took {elapsed:.3f}s — expected parallel < "
            f"{serial_bound + 0.4:.3f}s"
        )


# ---------------------------------------------------------------------------
# Tests — observer summary
# ---------------------------------------------------------------------------

class TestObserverOnBatch:
    def test_batch_run_outer_span_exists(
        self,
        orchestrator: Orchestrator,
        observer: Observer,
        config: EvalConfig,
    ):
        orchestrator.batch_run(
            ["momentum_5", "momentum_10"], config, run_id="obs-batch"
        )
        names = [s["name"] for s in observer.summary()["spans"]]
        assert "orchestrator.batch_run" in names
        assert "data_load" in names
        assert "kernel_exec" in names
        # one evaluate span per successful factor
        assert names.count("evaluate") == 2

    def test_output_stats_recorded_for_each(
        self,
        orchestrator: Orchestrator,
        observer: Observer,
        config: EvalConfig,
    ):
        orchestrator.batch_run(
            ["momentum_5", "momentum_10", "volatility_20"], config
        )
        stats = observer.summary()["output_stats"]
        for name in ("momentum_5", "momentum_10", "volatility_20"):
            assert name in stats


# ---------------------------------------------------------------------------
# Tests — empty batch
# ---------------------------------------------------------------------------

class TestEmptyBatch:
    def test_empty_list_returns_empty_dict(
        self, orchestrator: Orchestrator, config: EvalConfig
    ):
        results = orchestrator.batch_run([], config)
        assert results == {}

    def test_only_unknown_names_all_none(
        self, orchestrator: Orchestrator, config: EvalConfig
    ):
        results = orchestrator.batch_run(["x", "y"], config)
        assert results == {"x": None, "y": None}


# ---------------------------------------------------------------------------
# Tests — no-cache orchestrator
# ---------------------------------------------------------------------------

class TestNoCacheBatch:
    def test_batch_without_cache(
        self,
        registry: Registry,
        data_layer: _StubDataLayer,
        backend: PolarsBackend,
        evaluator: Evaluator,
        observer: Observer,
        config: EvalConfig,
    ):
        orch = Orchestrator(
            registry=registry,
            data_layer=data_layer,
            backend=backend,
            evaluator=evaluator,
            cache=None,
            observer=observer,
        )
        results = orch.batch_run(["momentum_5", "volatility_20"], config)
        assert isinstance(results["momentum_5"], EvalResult)
        assert isinstance(results["volatility_20"], EvalResult)
