"""Unit tests for ``tinohelm.factor.engine.scheduler``.

Coverage
--------
- Parallel execution: 3 independent factors each sleeping 0.2s complete in
  less than 0.5s total (proves ThreadPoolExecutor concurrency).
- Single factor failure does not block other factors.
- "errors" key is always present in result dict.
- "errors" is empty dict when all factors succeed.
- Missing required input raises and is recorded as error for that factor.
- Missing optional input does not raise.
- Kernel returning non-DataFrame raises TypeError recorded as error.
- Result dict contains output for each successful factor.
- Multi-layer plan: layer 1 executes after layer 0.
- kernel_map parameter routes to the correct callable.
"""
from __future__ import annotations

import time
from typing import Any

import pandas as pd
import pytest

from tinohelm.factor.backend.pandas_backend import PandasBackend
from tinohelm.factor.engine.planner import Plan, Planner
from tinohelm.factor.engine.scheduler import Scheduler
from tinohelm.factor.types import DataRequest, FactorSpec, InputSpec, Panel


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

SYMBOLS = ["BTCUSDT-PERP", "ETHUSDT-PERP"]
FREQ = "1-MINUTE"


def _make_panel(n_rows: int = 10, symbols: list[str] | None = None) -> Panel:
    """Create a minimal time × symbol DataFrame for testing."""
    syms = symbols or SYMBOLS
    idx = pd.date_range("2025-01-01", periods=n_rows, freq="1min")
    return pd.DataFrame(1.0, index=idx, columns=syms)


def _make_spec(
    name: str,
    field_names: list[str],
    lookback: int = 1,
    *,
    required: bool = True,
) -> FactorSpec:
    input_specs = tuple(InputSpec(field_name=f, required=required) for f in field_names)
    return FactorSpec(
        name=name,
        category="test",
        lookback=lookback,
        input_specs=input_specs,
    )


def _make_plan(specs: list[FactorSpec]) -> Plan:
    """Build a simple single-layer plan from specs."""
    return Plan(
        data_requests=[],
        layers=[specs] if specs else [],
    )


# ---------------------------------------------------------------------------
# Parallel execution timing test (AC requirement)
# ---------------------------------------------------------------------------

class TestParallelExecution:
    def test_three_factors_run_in_parallel(self):
        """3 factors each sleep 0.2 s → total < 0.5 s (not 0.6 s serial)."""
        backend = PandasBackend()
        scheduler = Scheduler()

        close_panel = _make_panel()
        data = {"close": close_panel}

        specs = [
            _make_spec("factor_a", ["close"]),
            _make_spec("factor_b", ["close"]),
            _make_spec("factor_c", ["close"]),
        ]
        plan = _make_plan(specs)

        def slow_kernel(close: Panel) -> Panel:
            time.sleep(0.2)
            return close * 1.0

        kernel_map = {
            "factor_a": slow_kernel,
            "factor_b": slow_kernel,
            "factor_c": slow_kernel,
        }

        start = time.monotonic()
        results = scheduler.execute(plan, data=data, backend=backend, kernel_map=kernel_map)
        elapsed = time.monotonic() - start

        # All three must succeed
        assert "factor_a" in results
        assert "factor_b" in results
        assert "factor_c" in results
        assert results["errors"] == {}

        # Must complete faster than serial (3 × 0.2 = 0.6 s)
        assert elapsed < 0.5, (
            f"Parallel execution took {elapsed:.3f}s, expected < 0.5s. "
            "Scheduler may be running serially."
        )


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------

class TestFailureIsolation:
    def setup_method(self):
        self.backend = PandasBackend()
        self.scheduler = Scheduler()
        self.data = {"close": _make_panel(), "volume": _make_panel()}

    def test_single_failure_does_not_block_others(self):
        """One factor raising does not prevent other factors from completing."""
        specs = [
            _make_spec("good_factor", ["close"]),
            _make_spec("bad_factor", ["close"]),
        ]
        plan = _make_plan(specs)

        def good_kernel(close: Panel) -> Panel:
            return close * 2.0

        def bad_kernel(close: Panel) -> Panel:
            raise RuntimeError("intentional test failure")

        kernel_map = {
            "good_factor": good_kernel,
            "bad_factor": bad_kernel,
        }

        results = self.scheduler.execute(
            plan, data=self.data, backend=self.backend, kernel_map=kernel_map
        )

        assert "good_factor" in results
        assert isinstance(results["good_factor"], pd.DataFrame)
        assert "bad_factor" in results["errors"]
        assert "RuntimeError" in results["errors"]["bad_factor"]

    def test_errors_key_always_present(self):
        spec = _make_spec("ok", ["close"])
        plan = _make_plan([spec])
        results = self.scheduler.execute(
            plan,
            data={"close": _make_panel()},
            backend=self.backend,
            kernel_map={"ok": lambda close: close},
        )
        assert "errors" in results

    def test_errors_empty_when_all_succeed(self):
        spec = _make_spec("ok", ["close"])
        plan = _make_plan([spec])
        results = self.scheduler.execute(
            plan,
            data={"close": _make_panel()},
            backend=self.backend,
            kernel_map={"ok": lambda close: close * 1.0},
        )
        assert results["errors"] == {}

    def test_missing_required_input_recorded_as_error(self):
        """Factor needing 'volume' but data has only 'close' → error."""
        spec = _make_spec("needs_volume", ["volume"], required=True)
        plan = _make_plan([spec])

        def kernel(volume: Panel) -> Panel:
            return volume

        results = self.scheduler.execute(
            plan,
            data={"close": _make_panel()},  # volume missing
            backend=self.backend,
            kernel_map={"needs_volume": kernel},
        )

        assert "needs_volume" in results["errors"]
        assert "volume" in results["errors"]["needs_volume"]

    def test_missing_optional_input_not_error(self):
        """Factor with optional 'volume' (required=False) and data missing it succeeds."""
        spec = _make_spec("optional_vol", ["close", "volume"], required=False)
        plan = _make_plan([spec])

        # Kernel only uses close; volume not passed but that's fine for optional
        def kernel(close: Panel) -> Panel:
            return close

        results = self.scheduler.execute(
            plan,
            data={"close": _make_panel()},
            backend=self.backend,
            kernel_map={"optional_vol": kernel},
        )

        assert "optional_vol" in results
        assert results["errors"].get("optional_vol") is None

    def test_non_dataframe_return_is_error(self):
        """Kernel returning non-DataFrame is caught and recorded."""
        spec = _make_spec("bad_return", ["close"])
        plan = _make_plan([spec])

        def bad_kernel(close: Panel):
            return 42  # wrong type

        results = self.scheduler.execute(
            plan,
            data={"close": _make_panel()},
            backend=self.backend,
            kernel_map={"bad_return": bad_kernel},
        )

        assert "bad_return" in results["errors"]
        assert "TypeError" in results["errors"]["bad_return"]


# ---------------------------------------------------------------------------
# Missing kernel
# ---------------------------------------------------------------------------

class TestMissingKernel:
    def test_no_kernel_recorded_as_error(self):
        """Factor with no kernel in kernel_map (and no __factor_func__) → error."""
        backend = PandasBackend()
        scheduler = Scheduler()
        spec = _make_spec("orphan_factor", ["close"])
        plan = _make_plan([spec])

        results = scheduler.execute(
            plan,
            data={"close": _make_panel()},
            backend=backend,
            kernel_map={},  # empty — no kernel provided
        )

        assert "orphan_factor" in results["errors"]


# ---------------------------------------------------------------------------
# Multi-layer execution
# ---------------------------------------------------------------------------

class TestMultiLayerExecution:
    def test_two_layers_execute_in_order(self):
        """Layer 0 results appear before layer 1 is executed."""
        backend = PandasBackend()
        scheduler = Scheduler()

        execution_order: list[str] = []

        close_panel = _make_panel()
        data = {"close": close_panel}

        spec_a = _make_spec("factor_a", ["close"])
        spec_b = _make_spec("factor_b", ["close"])

        plan = Plan(
            data_requests=[],
            layers=[[spec_a], [spec_b]],  # two sequential layers
        )

        def make_recording_kernel(name: str):
            def kernel(close: Panel) -> Panel:
                execution_order.append(name)
                return close * 1.0
            return kernel

        kernel_map = {
            "factor_a": make_recording_kernel("factor_a"),
            "factor_b": make_recording_kernel("factor_b"),
        }

        results = scheduler.execute(plan, data=data, backend=backend, kernel_map=kernel_map)

        assert results["errors"] == {}
        assert "factor_a" in results
        assert "factor_b" in results
        # factor_a must have completed before factor_b started
        assert execution_order.index("factor_a") < execution_order.index("factor_b")


# ---------------------------------------------------------------------------
# kernel_map routing
# ---------------------------------------------------------------------------

class TestKernelMap:
    def test_kernel_map_routes_correctly(self):
        backend = PandasBackend()
        scheduler = Scheduler()
        close_panel = _make_panel()

        spec = _make_spec("my_factor", ["close"])
        plan = _make_plan([spec])

        sentinel = [False]

        def my_kernel(close: Panel) -> Panel:
            sentinel[0] = True
            return close

        results = scheduler.execute(
            plan,
            data={"close": close_panel},
            backend=backend,
            kernel_map={"my_factor": my_kernel},
        )

        assert sentinel[0], "kernel_map kernel was not called"
        assert "my_factor" in results

    def test_result_is_dataframe(self):
        backend = PandasBackend()
        scheduler = Scheduler()
        close_panel = _make_panel()

        spec = _make_spec("factor_x", ["close"])
        plan = _make_plan([spec])

        results = scheduler.execute(
            plan,
            data={"close": close_panel},
            backend=backend,
            kernel_map={"factor_x": lambda close: close * 2.0},
        )

        assert isinstance(results["factor_x"], pd.DataFrame)


# ---------------------------------------------------------------------------
# Empty plan
# ---------------------------------------------------------------------------

class TestEmptyPlan:
    def test_empty_plan_returns_only_errors_key(self):
        backend = PandasBackend()
        scheduler = Scheduler()
        plan = Plan(data_requests=[], layers=[])

        results = scheduler.execute(plan, data={}, backend=backend, kernel_map={})
        assert results == {"errors": {}}
