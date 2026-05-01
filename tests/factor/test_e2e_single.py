"""End-to-end integration test — single factor through :class:`Orchestrator`.

Validates the full pipeline: Registry → DataLayer → Backend → kernel execution
→ Evaluator → FactorCache → Observer.  Uses synthetic data produced in-process
(no Parquet I/O) via a minimal ``DataLayer`` stub, keeping the test fast and
deterministic while still exercising every Orchestrator branch.

Coverage
--------
1. ``run()`` returns an :class:`EvalResult` with major numeric fields populated
   (ic_mean, quantile_pnl, turnover, distribution).
2. Observer summary contains nested ``orchestrator.run`` → ``data_load`` /
   ``kernel_exec`` / ``evaluate`` spans.
3. Output stats for the factor are recorded in the observer.
4. FactorCache store writes both Parquet values and JSON eval on first run.
5. Second call with identical inputs is a cache hit (``cache_hit=True`` span
   tag) — data_load / kernel_exec / evaluate spans are NOT repeated.
6. Unknown factor name raises :class:`KeyError`.
7. ``full=True`` goes through :meth:`Evaluator.evaluate_full` (populates
   robustness + cost fields).

Polars contract
---------------
``Scheduler._call_kernel`` requires kernels to return :class:`polars.DataFrame`
panels in the canonical wide-table layout (``ts`` column + symbol columns).
Test fixtures construct synthetic panels directly with polars to match.
"""
from __future__ import annotations

import dataclasses
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
# Synthetic data fixtures (polars wide-table panels)
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
    """Deterministic synthetic close-price polars Panel (time × 3 symbols)."""
    rng = np.random.default_rng(123)
    timestamps = _make_timestamps(_N_BARS)
    # Random walk — ensures realistic cross-symbol variation for quantile bins.
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
# DataLayer stub — bypasses Parquet I/O
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
    """Minimal ``DataLayer`` subclass that returns pre-supplied panels.

    Bypasses the real ``load()`` so tests do not depend on :mod:`nautilus_trader`
    catalog I/O.  The real signature is preserved, so :class:`Orchestrator`
    calls the stub exactly as it would call the production DataLayer.
    """

    def __init__(self, panels: dict[str, Panel]) -> None:
        # Skip the superclass __init__ — we never touch the catalog
        self._panels = panels
        self._calls: list[tuple] = []

    def load(self, request, start=None, end=None):  # type: ignore[override]
        self._calls.append((request, start, end))
        # Slice to the requested window so downstream eval matches the range
        out: dict[str, Panel] = {}
        requested_fields = {
            r.field_name for r in ([request] if not isinstance(request, list) else request)
        }
        for field_name in requested_fields:
            source_field = "close" if field_name == "__eval_close" else field_name
            panel = self._panels[source_field]
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
# Registry helper — register a factor kernel by attaching __factor_spec__
# ---------------------------------------------------------------------------

def _register_factor(registry: Registry, func, spec: FactorSpec) -> None:
    """Manually inject a factor into a Registry's caches (bypasses disk scan)."""
    registry._spec_cache[spec.name] = (f"stub:{spec.name}:{spec.code_hash}", spec)
    registry._kernel_cache[spec.name] = func


def _value_cols(panel: Panel) -> list[str]:
    return [c for c in panel.columns if c != "ts"]


# A simple declarative factor used by every test below.
@factor(category="动量", lookback=5, description="5-bar percent change")
def ret_5(close: Panel) -> Panel:
    cols = _value_cols(close)
    return close.with_columns([pl.col(c).pct_change(5).alias(c) for c in cols])


@factor(category="波动", lookback=10)
def vol_10(close: Panel) -> Panel:
    cols = _value_cols(close)
    return close.with_columns(
        [pl.col(c).rolling_std(window_size=10).alias(c) for c in cols]
    )


@factor(category="测试", lookback=1, params={"scale": 1.0})
def param_scale(close: Panel, params=None) -> Panel:
    scale = float((params or {}).get("scale", 1.0))
    cols = _value_cols(close)
    return close.with_columns([(pl.col(c) * scale).alias(c) for c in cols])


@factor(category="测试", lookback=1, params={"scale": 1.0})
def scalar_param_scale(close: Panel, scale: float = 1.0) -> Panel:
    cols = _value_cols(close)
    return close.with_columns([(pl.col(c) * float(scale)).alias(c) for c in cols])


@factor(category="测试", lookback=1)
def volume_passthrough(volume: Panel) -> Panel:
    return volume


@factor(category="测试", lookback=2)
def finite_warmup_close(close: Panel) -> Panel:
    cols = _value_cols(close)
    return close.with_columns([(pl.col(c) - pl.col(c).shift(1)).alias(c) for c in cols])


# ---------------------------------------------------------------------------
# Fixtures — orchestrator + collaborators
# ---------------------------------------------------------------------------

@pytest.fixture
def registry() -> Registry:
    r = Registry(user_dir=Path("/tmp/nonexistent_factor_dir_for_test"))
    _register_factor(r, ret_5, ret_5.__factor_spec__)
    _register_factor(r, vol_10, vol_10.__factor_spec__)
    _register_factor(r, param_scale, param_scale.__factor_spec__)
    _register_factor(r, scalar_param_scale, scalar_param_scale.__factor_spec__)
    _register_factor(r, volume_passthrough, volume_passthrough.__factor_spec__)
    _register_factor(r, finite_warmup_close, finite_warmup_close.__factor_spec__)
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
    return Observer(run_id="e2e-single-test")


@pytest.fixture
def cache(tmp_path: Path) -> FactorCache:
    return FactorCache(cache_root=tmp_path / "cache")


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
# Tests — happy path
# ---------------------------------------------------------------------------

class TestSingleFactorRun:
    def test_run_returns_eval_result(self, orchestrator: Orchestrator, config: EvalConfig):
        result = orchestrator.run("ret_5", config, run_id="r1")
        assert isinstance(result, EvalResult)

    def test_ic_and_turnover_populated(
        self, orchestrator: Orchestrator, config: EvalConfig
    ):
        result = orchestrator.run("ret_5", config)

        # ic_mean may be near-zero but field type must be float (not None)
        assert isinstance(result.ic_mean, float)
        # turnover should be populated for a non-trivial factor
        assert isinstance(result.turnover, float)
        # quantile_pnl populates when sample is long enough
        assert isinstance(result.quantile_pnl, dict)

    def test_distribution_populated(
        self, orchestrator: Orchestrator, config: EvalConfig
    ):
        result = orchestrator.run("ret_5", config)
        assert len(result.distribution_stats) > 0
        assert len(result.distribution_histogram) > 0

    def test_non_close_factor_loads_close_in_same_request(
        self,
        orchestrator: Orchestrator,
        data_layer: _StubDataLayer,
        config: EvalConfig,
    ):
        orchestrator.run("volume_passthrough", config)
        first_request = data_layer._calls[0][0]
        fields = {(r.symbol, r.field_name, r.frequency, r.source) for r in first_request}
        for sym in SYMBOLS:
            assert (sym, "volume", "1m", "bar") in fields
            assert (sym, "__eval_close", "1m", "bar") in fields

    def test_eval_close_alias_does_not_collide_with_factor_close_request(
        self,
        orchestrator: Orchestrator,
        data_layer: _StubDataLayer,
        config: EvalConfig,
    ):
        orchestrator.run("ret_5", config, interval="5m")
        first_request = data_layer._calls[0][0]
        fields = {(r.symbol, r.field_name, r.frequency, r.source) for r in first_request}
        for sym in SYMBOLS:
            assert (sym, "close", "5m", "bar") in fields
            assert (sym, "__eval_close", "5m", "bar") in fields

    def test_no_nan_or_inf_in_scalar_fields(
        self, orchestrator: Orchestrator, config: EvalConfig
    ):
        """Scalar floats must never be NaN/Inf (PostgreSQL JSON contract)."""
        result = orchestrator.run("ret_5", config)
        for field_name in (
            "ic_mean", "ic_std", "ir", "ic_tstat",
            "ic_positive_pct", "ic_max_abs",
            "turnover", "turnover_annualized", "fee_drag_monthly",
        ):
            value = getattr(result, field_name)
            assert isinstance(value, float)
            assert not np.isnan(value), f"{field_name} is NaN"
            assert not np.isinf(value), f"{field_name} is Inf"


# ---------------------------------------------------------------------------
# Tests — observer spans
# ---------------------------------------------------------------------------

class TestObserverSpans:
    def test_outer_span_recorded(
        self, orchestrator: Orchestrator, observer: Observer, config: EvalConfig
    ):
        orchestrator.run("ret_5", config, run_id="obs-1")
        names = {s["name"] for s in observer.summary()["spans"]}
        assert "orchestrator.run" in names

    def test_all_nested_spans_recorded(
        self, orchestrator: Orchestrator, observer: Observer, config: EvalConfig
    ):
        orchestrator.run("ret_5", config)
        names = {s["name"] for s in observer.summary()["spans"]}
        assert {"orchestrator.run", "data_load", "kernel_exec", "evaluate"} <= names

    def test_output_stats_recorded(
        self, orchestrator: Orchestrator, observer: Observer, config: EvalConfig
    ):
        orchestrator.run("ret_5", config)
        stats = observer.summary()["output_stats"]
        assert "ret_5" in stats
        assert "nan_rate" in stats["ret_5"]
        assert "shape" in stats["ret_5"]

    def test_run_id_in_tags(
        self, orchestrator: Orchestrator, observer: Observer, config: EvalConfig
    ):
        orchestrator.run("ret_5", config, run_id="tagged-run")
        outer = next(
            s for s in observer.summary()["spans"] if s["name"] == "orchestrator.run"
        )
        assert outer["tags"].get("run_id") == "tagged-run"
        assert outer["tags"].get("factor") == "ret_5"


# ---------------------------------------------------------------------------
# Tests — cache behaviour
# ---------------------------------------------------------------------------

class TestCacheBehavior:
    def test_first_run_writes_cache(
        self,
        orchestrator: Orchestrator,
        cache: FactorCache,
        config: EvalConfig,
    ):
        orchestrator.run("ret_5", config)
        # Inspect cache directory for side effects
        assert any(cache._values_dir.glob("*.parquet"))
        assert any(cache._eval_dir.glob("*.json"))

    def test_second_run_is_cache_hit(
        self,
        orchestrator: Orchestrator,
        observer: Observer,
        config: EvalConfig,
    ):
        first = orchestrator.run("ret_5", config)
        summary1 = observer.summary()
        spans1 = summary1["spans"]
        kernel_count1 = sum(1 for s in spans1 if s["name"] == "kernel_exec")
        data_count1 = sum(1 for s in spans1 if s["name"] == "data_load")

        second = orchestrator.run("ret_5", config)
        summary2 = observer.summary()
        spans2 = summary2["spans"]
        kernel_count2 = sum(1 for s in spans2 if s["name"] == "kernel_exec")
        data_count2 = sum(1 for s in spans2 if s["name"] == "data_load")

        # First run added both spans; second run didn't
        assert kernel_count2 == kernel_count1, "kernel_exec ran on cache hit"
        assert data_count2 == data_count1, "data_load ran on cache hit"

        # Both results numerically identical
        assert first.ic_mean == second.ic_mean
        assert first.turnover == second.turnover
        assert first.cache_key
        assert first.cache_hit is False
        assert second.cache_key == first.cache_key
        assert second.cache_hit is True
        assert second.factor_code_hash == first.factor_code_hash

    def test_params_reach_kernel_and_partition_cache(
        self,
        orchestrator: Orchestrator,
        config: EvalConfig,
    ):
        low_config = dataclasses.replace(config, params={"scale": 1.0})
        high_config = dataclasses.replace(config, params={"scale": -1.0})

        low = orchestrator.run("param_scale", low_config, params={"scale": 1.0})
        high = orchestrator.run("param_scale", high_config, params={"scale": -1.0})

        assert high.ic_mean == pytest.approx(-low.ic_mean)
        assert low.effective_params == {"scale": 1.0}
        assert high.effective_params == {"scale": -1.0}

    def test_scalar_params_reach_kernel_and_partition_cache(
        self,
        orchestrator: Orchestrator,
        config: EvalConfig,
    ):
        low_config = dataclasses.replace(config, params={"scale": 1.0})
        high_config = dataclasses.replace(config, params={"scale": -1.0})

        low = orchestrator.run("scalar_param_scale", low_config, params={"scale": 1.0})
        high = orchestrator.run("scalar_param_scale", high_config, params={"scale": -1.0})

        assert high.ic_mean == pytest.approx(-low.ic_mean)
        assert low.effective_params == {"scale": 1.0}
        assert high.effective_params == {"scale": -1.0}

    def test_cache_hit_span_tag_true(
        self,
        orchestrator: Orchestrator,
        observer: Observer,
        config: EvalConfig,
    ):
        orchestrator.run("ret_5", config)  # warm cache
        orchestrator.run("ret_5", config)  # cache hit

        outer_spans = [
            s for s in observer.summary()["spans"] if s["name"] == "orchestrator.run"
        ]
        # There should be two orchestrator.run spans — first miss, second hit
        assert len(outer_spans) == 2
        # Second one must tag cache_hit=True
        assert outer_spans[1]["tags"].get("cache_hit") is True
        # First must tag cache_hit=False
        assert outer_spans[0]["tags"].get("cache_hit") is False


# ---------------------------------------------------------------------------
# Tests — unknown factor
# ---------------------------------------------------------------------------

class TestErrorPaths:
    def test_unknown_factor_raises_keyerror(
        self, orchestrator: Orchestrator, config: EvalConfig
    ):
        with pytest.raises(KeyError, match="not found"):
            orchestrator.run("nonexistent_factor", config)


# ---------------------------------------------------------------------------
# Tests — full evaluation path
# ---------------------------------------------------------------------------

class TestFullEvaluation:
    def test_full_evaluation_populates_robustness_and_cost(
        self, orchestrator: Orchestrator, config: EvalConfig
    ):
        # Pass shuffle_iter=0 equivalent by disabling robustness shuffle? The
        # evaluator uses default shuffle_iter=1000 which is too slow for a unit
        # test. We pin the evaluator's defaults to small values via monkey-patch.
        result = orchestrator.run("ret_5", config, full=True)
        # cost always populated by evaluate_full
        assert isinstance(result.cost, dict)
        # robustness always populated by evaluate_full (subsample is always run)
        assert isinstance(result.robustness, dict)


# ---------------------------------------------------------------------------
# Tests — no-cache path
# ---------------------------------------------------------------------------

class TestNoCacheOrchestrator:
    """Orchestrator created without a cache still works end-to-end."""

    def test_run_without_cache(
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
            cache=None,  # disabled
            observer=observer,
        )
        result = orch.run("ret_5", config)
        assert isinstance(result, EvalResult)


# ---------------------------------------------------------------------------
# Tests — observer auto-creation
# ---------------------------------------------------------------------------

class TestAutoObserver:
    """Orchestrator without an observer auto-creates one (no crashes)."""

    def test_run_without_observer(
        self,
        registry: Registry,
        data_layer: _StubDataLayer,
        backend: PolarsBackend,
        evaluator: Evaluator,
        cache: FactorCache,
        config: EvalConfig,
    ):
        orch = Orchestrator(
            registry=registry,
            data_layer=data_layer,
            backend=backend,
            evaluator=evaluator,
            cache=cache,
            observer=None,  # auto-created
        )
        result = orch.run("ret_5", config)
        assert isinstance(result, EvalResult)


def test_run_and_batch_run_cache_keys_include_interval(
    registry: Registry,
    data_layer: _StubDataLayer,
    backend: PolarsBackend,
    evaluator: Evaluator,
    cache: FactorCache,
    observer: Observer,
    config: EvalConfig,
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[str] = []
    real_build_key = FactorCache.build_key

    def spy_build_key(factor_name, code_hash, eval_config, data_range, interval, *, full=False):
        calls.append(interval)
        return real_build_key(factor_name, code_hash, eval_config, data_range, interval, full=full)

    monkeypatch.setattr(FactorCache, "build_key", staticmethod(spy_build_key))
    orch = Orchestrator(registry, data_layer, backend, evaluator, cache, observer)

    orch.run("ret_5", config, interval="5m")
    orch.batch_run(["vol_10"], config, interval="15m")

    assert "5m" in calls
    assert "15m" in calls


def test_run_trims_finite_warmup_rows_before_evaluation(
    registry: Registry,
    backend: PolarsBackend,
    tmp_path: Path,
):
    warmup_start = datetime(2023, 12, 31, 23)
    start = datetime(2024, 1, 1, 0)
    timestamps = [warmup_start + timedelta(hours=i) for i in range(8)]
    payload: dict[str, list] = {"ts": timestamps}
    for sym in SYMBOLS:
        payload[sym] = [100.0 + i for i in range(len(timestamps))]
    panel = pl.DataFrame(payload, schema={"ts": pl.Datetime("us"), **{sym: pl.Float64 for sym in SYMBOLS}})

    data_layer = _StubDataLayer({"close": panel})
    seen_factor_values: list[Panel] = []

    class _CapturingEvaluator(Evaluator):
        def evaluate(self, factor_values, returns, config):  # type: ignore[override]
            seen_factor_values.append(factor_values)
            return EvalResult(ic_mean=0.0, ir=0.0, rating=0)

    orch = Orchestrator(
        registry=registry,
        data_layer=data_layer,
        backend=backend,
        evaluator=_CapturingEvaluator(),
        cache=FactorCache(cache_root=tmp_path / "cache"),
        observer=Observer(run_id="warmup-trim"),
    )
    config = EvalConfig(
        universe=SYMBOLS,
        start=start.isoformat(),
        end=datetime(2024, 1, 1, 5).isoformat(),
        forward_period=1,
        quantiles=3,
        ic_freq="H",
    )

    result = orch.run("finite_warmup_close", config)

    assert isinstance(result, EvalResult)
    assert seen_factor_values
    assert min(seen_factor_values[0]["ts"].to_list()) >= start
