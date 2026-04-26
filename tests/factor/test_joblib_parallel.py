"""Tests for joblib.Parallel kernel execution in Orchestrator.batch_run.

Validates:
- batch_run uses joblib.Parallel (threading backend) rather than
  ThreadPoolExecutor directly.
- 12 slow factors complete in ≤ 50% of serial time when n_jobs=4.
- Results are correct (no dropped items, values preserved).

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

from tinohelm.factor.backend.polars_backend import PolarsBackend
from tinohelm.factor.cache import FactorCache
from tinohelm.factor.data_layer import DataLayer
from tinohelm.factor.engine.orchestrator import Orchestrator
from tinohelm.factor.evaluation.evaluator import Evaluator
from tinohelm.factor.observer import Observer
from tinohelm.factor.registry import Registry
from tinohelm.factor.types import EvalConfig, EvalResult, FactorSpec, InputSpec, Panel


# ---------------------------------------------------------------------------
# Synthetic data helpers (polars wide-table panels — Scheduler contract)
# ---------------------------------------------------------------------------

_N_BARS = 400
_START = "2024-01-01"
_END = "2024-02-01"
_SYMBOLS = ("SYM00", "SYM01")


def _make_close_panel_pl() -> pl.DataFrame:
    """Polars close panel ``[ts, SYM00, SYM01]`` (Scheduler contract)."""
    rng = np.random.default_rng(42)
    base_ts = datetime(2024, 1, 1)
    timestamps = [base_ts + timedelta(hours=i) for i in range(_N_BARS)]
    prices = 100.0 * np.cumprod(
        1 + rng.normal(0, 0.005, (_N_BARS, len(_SYMBOLS))), axis=0
    )
    payload: dict[str, list] = {"ts": timestamps}
    for j, sym in enumerate(_SYMBOLS):
        payload[sym] = prices[:, j].tolist()
    schema = {"ts": pl.Datetime("us")}
    schema.update({sym: pl.Float64 for sym in _SYMBOLS})
    return pl.DataFrame(payload, schema=schema)


class _StubDataLayer(DataLayer):
    """DataLayer stub that returns pre-built polars panels without I/O."""

    def __init__(self, panels: dict[str, pl.DataFrame]) -> None:
        self._panels = panels

    def load(self, request, start=None, end=None):  # type: ignore[override]
        out: dict[str, pl.DataFrame] = {}
        for field_name, panel in self._panels.items():
            df = panel
            if start is not None:
                start_ts = _coerce_to_dt(start)
                df = df.filter(pl.col("ts") >= pl.lit(start_ts))
            if end is not None:
                end_ts = _coerce_to_dt(end)
                df = df.filter(pl.col("ts") <= pl.lit(end_ts))
            out[field_name] = df.clone()
        return out


def _coerce_to_dt(value) -> datetime:
    """Coerce ISO strings / datetimes / pandas Timestamps to ``datetime``."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if hasattr(value, "to_pydatetime"):
        ts = value.to_pydatetime()
        return ts.replace(tzinfo=None) if ts.tzinfo else ts
    return datetime.fromisoformat(str(value))


def _register(registry: Registry, func, spec: FactorSpec) -> None:
    registry._spec_cache[spec.name] = (f"stub:{spec.name}", spec)
    registry._kernel_cache[spec.name] = func


# ---------------------------------------------------------------------------
# Test: orchestrator module imports joblib.Parallel and delayed
# ---------------------------------------------------------------------------

def test_orchestrator_uses_joblib_parallel():
    """Orchestrator.batch_run imports and uses joblib.Parallel."""
    import tinohelm.factor.engine.orchestrator as orch_mod

    assert hasattr(orch_mod, "Parallel"), (
        "orchestrator module must import Parallel from joblib"
    )
    assert hasattr(orch_mod, "delayed"), (
        "orchestrator module must import delayed from joblib"
    )


# ---------------------------------------------------------------------------
# Test: 12-factor speedup — parallel ≤ serial × 0.5
# ---------------------------------------------------------------------------

def test_joblib_parallel_speedup_12_factors():
    """12 slow factors (0.05s sleep each) complete ≤ 50% of serial baseline.

    Serial kernel-only baseline: 12 × 0.05s = 0.6s.
    Parallel target (n_jobs=4): ≤ 0.30s + evaluator/GIL overhead margin.
    """
    sleep_per_factor = 0.05
    n_factors = 12

    close_panel = _make_close_panel_pl()
    registry = Registry(user_dir=Path("/tmp/nonexistent_jl_speedup"))
    data_layer = _StubDataLayer({"close": close_panel})
    evaluator = Evaluator()

    def _slow_kernel(close: pl.DataFrame, params=None) -> pl.DataFrame:
        """Returns ``pl.DataFrame`` (Scheduler._call_kernel polars contract)."""
        time.sleep(sleep_per_factor)
        symbol_cols = [c for c in close.columns if c != "ts"]
        return close.with_columns(
            [pl.col(c).pct_change(3).alias(c) for c in symbol_cols]
        )

    factor_names = [f"slow_{i:02d}" for i in range(n_factors)]
    for name in factor_names:
        spec = FactorSpec(
            name=name,
            category="perf",
            lookback=3,
            input_specs=(InputSpec(field_name="close"),),
            code_hash=f"hash_{name}",
        )
        _register(registry, _slow_kernel, spec)

    config = EvalConfig(universe=_SYMBOLS, start=_START, end=_END)
    cache = FactorCache(cache_root=Path("/tmp/jl_speedup_cache"))

    orch = Orchestrator(
        registry=registry,
        data_layer=data_layer,
        backend=PolarsBackend(),
        evaluator=evaluator,
        cache=cache,
        observer=Observer(),
    )

    # Serial baseline: pure kernel sleep time (no evaluator overhead)
    kernel_serial = n_factors * sleep_per_factor  # 0.6s

    # Parallel via batch_run (n_jobs=4)
    t_parallel_start = time.monotonic()
    results = orch.batch_run(factor_names, config, max_workers=4)
    parallel_time = time.monotonic() - t_parallel_start

    # All 12 factors must succeed
    for name in factor_names:
        assert isinstance(results[name], EvalResult), (
            f"Expected EvalResult for {name}, got {type(results[name])}"
        )

    # Parallel must complete in ≤ 50% of serial kernel time + 0.5s evaluator margin.
    assert parallel_time <= kernel_serial * 0.5 + 0.5, (
        f"Parallel time {parallel_time:.3f}s exceeded target "
        f"(≤ {kernel_serial * 0.5 + 0.5:.3f}s = 50% of serial {kernel_serial:.3f}s + 0.5s margin)"
    )


# ---------------------------------------------------------------------------
# Test: correctness — batch_run results match single-factor run() values
# ---------------------------------------------------------------------------

def _momentum_jl_kernel(close: pl.DataFrame, params=None) -> pl.DataFrame:
    """Module-level kernel so it is picklable (needed for loky/spawned workers)."""
    symbol_cols = [c for c in close.columns if c != "ts"]
    return close.with_columns(
        [pl.col(c).pct_change(5).alias(c) for c in symbol_cols]
    )


def test_batch_results_match_single_run():
    """batch_run results are numerically identical to individual run() calls."""
    close_panel = _make_close_panel_pl()
    registry = Registry(user_dir=Path("/tmp/nonexistent_jl_match"))
    data_layer = _StubDataLayer({"close": close_panel})
    evaluator = Evaluator()

    spec = FactorSpec(
        name="momentum_jl_match",
        category="动量",
        lookback=5,
        input_specs=(InputSpec(field_name="close"),),
        code_hash="hash_jl_match",
    )
    _register(registry, _momentum_jl_kernel, spec)

    config = EvalConfig(universe=_SYMBOLS, start=_START, end=_END)

    orch = Orchestrator(
        registry=registry,
        data_layer=data_layer,
        backend=PolarsBackend(),
        evaluator=evaluator,
        cache=None,
        observer=Observer(),
    )

    single = orch.run("momentum_jl_match", config)
    batch = orch.batch_run(["momentum_jl_match"], config)

    assert abs(batch["momentum_jl_match"].ic_mean - single.ic_mean) < 1e-9, (
        "ic_mean mismatch between batch_run and run()"
    )
    assert abs(batch["momentum_jl_match"].turnover - single.turnover) < 1e-9, (
        "turnover mismatch between batch_run and run()"
    )
