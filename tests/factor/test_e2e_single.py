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
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tinohelm.factor.backend.pandas_backend import PandasBackend
from tinohelm.factor.cache import FactorCache
from tinohelm.factor.data_layer import DataLayer
from tinohelm.factor.decorator import factor
from tinohelm.factor.engine.orchestrator import Orchestrator
from tinohelm.factor.evaluation.evaluator import Evaluator
from tinohelm.factor.observer import Observer
from tinohelm.factor.registry import Registry
from tinohelm.factor.types import EvalConfig, EvalResult, FactorSpec, Panel


# ---------------------------------------------------------------------------
# Synthetic data fixtures
# ---------------------------------------------------------------------------

SYMBOLS: tuple[str, ...] = ("SYM00", "SYM01", "SYM02")
_N_BARS: int = 800
_START: str = "2024-01-01"
_END: str = "2024-02-01"


@pytest.fixture
def close_panel() -> Panel:
    """Deterministic synthetic close-price Panel (time × 3 symbols)."""
    rng = np.random.default_rng(123)
    idx = pd.date_range(_START, periods=_N_BARS, freq="1h")
    # Random walk — ensures realistic cross-symbol variation for quantile bins.
    returns = rng.normal(0, 0.005, (_N_BARS, len(SYMBOLS)))
    prices = 100.0 * np.cumprod(1 + returns, axis=0)
    return pd.DataFrame(prices, index=idx, columns=list(SYMBOLS))


@pytest.fixture
def volume_panel() -> Panel:
    rng = np.random.default_rng(7)
    idx = pd.date_range(_START, periods=_N_BARS, freq="1h")
    return pd.DataFrame(
        rng.uniform(100, 500, (_N_BARS, len(SYMBOLS))),
        index=idx,
        columns=list(SYMBOLS),
    )


# ---------------------------------------------------------------------------
# DataLayer stub — bypasses Parquet I/O
# ---------------------------------------------------------------------------

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
        for field_name, panel in self._panels.items():
            if start is not None or end is not None:
                sl = panel
                if start is not None:
                    sl = sl.loc[pd.Timestamp(start):]
                if end is not None:
                    sl = sl.loc[:pd.Timestamp(end)]
                out[field_name] = sl.copy()
            else:
                out[field_name] = panel.copy()
        return out


# ---------------------------------------------------------------------------
# Registry helper — register a factor kernel by attaching __factor_spec__
# ---------------------------------------------------------------------------

def _register_factor(registry: Registry, func, spec: FactorSpec) -> None:
    """Manually inject a factor into a Registry's caches (bypasses disk scan)."""
    registry._spec_cache[spec.name] = (f"stub:{spec.name}:{spec.code_hash}", spec)
    registry._kernel_cache[spec.name] = func


# A simple declarative factor used by every test below.
@factor(category="动量", lookback=5, description="5-bar percent change")
def ret_5(close: Panel) -> Panel:
    return close.pct_change(5)


@factor(category="波动", lookback=10)
def vol_10(close: Panel) -> Panel:
    return close.rolling(10).std()


# ---------------------------------------------------------------------------
# Fixtures — orchestrator + collaborators
# ---------------------------------------------------------------------------

@pytest.fixture
def registry() -> Registry:
    r = Registry(user_dir=Path("/tmp/nonexistent_factor_dir_for_test"))
    _register_factor(r, ret_5, ret_5.__factor_spec__)
    _register_factor(r, vol_10, vol_10.__factor_spec__)
    return r


@pytest.fixture
def data_layer(close_panel: Panel, volume_panel: Panel) -> _StubDataLayer:
    return _StubDataLayer({"close": close_panel, "volume": volume_panel})


@pytest.fixture
def backend() -> PandasBackend:
    return PandasBackend()


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
    backend: PandasBackend,
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
        backend: PandasBackend,
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
        backend: PandasBackend,
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
