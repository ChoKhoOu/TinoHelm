"""Regression tests: BacktestRunner.run() is a coroutine callable via asyncio.run().

Contract:
  1. BacktestRunner.run is a coroutine function (inspect.iscoroutinefunction).
  2. asyncio.run(runner.run()) works from a fresh event loop without RuntimeError.
  3. runner.py does not contain asyncio.new_event_loop() — the old event-loop
     management pattern that caused "event loop already running" failures in
     Jupyter/IPython environments.
"""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from tinohelm.backtest.runner import BacktestRunner


# ---------------------------------------------------------------------------
# Helper: minimal BacktestRunner instance (no DB / Redis / catalog needed)
# ---------------------------------------------------------------------------

def _mk_minimal_runner() -> BacktestRunner:
    """Create a BacktestRunner with the minimum constructor params.

    No strategy_path resolution, no catalog access — only the attributes that
    BacktestRunner.__init__ sets unconditionally are populated.
    """
    return BacktestRunner(
        strategy_path="fake/strat.py:FakeStrategy",
        config_path="fake/strat.py:FakeStrategyConfig",
        symbol="BTCUSDT-PERP",
        interval="1m",
    )


# ---------------------------------------------------------------------------
# T1: static structural check
# ---------------------------------------------------------------------------

class TestRunIsCoroutine:
    def test_run_is_coroutine(self):
        """BacktestRunner.run must be declared as async def."""
        assert inspect.iscoroutinefunction(BacktestRunner.run), (
            "BacktestRunner.run is not a coroutine function. "
            "Did s12 async migration get reverted?"
        )


# ---------------------------------------------------------------------------
# T2: fresh-loop smoke test
# ---------------------------------------------------------------------------

class TestAsyncioRunFromFreshLoop:
    """asyncio.run(runner.run()) must complete without RuntimeError.

    Rationale: pytest by default does NOT set up an event loop, so the first
    asyncio.run() call inside this test will create a brand-new loop —
    exactly the Jupyter/IPython cold-start scenario we want to guard.
    Do NOT add @pytest.mark.asyncio here; that would pre-create a loop and
    defeat the purpose of the test.
    """

    def test_asyncio_run_from_fresh_loop(self, monkeypatch):
        runner = _mk_minimal_runner()

        # --- mock _setup_engine (async) -----------------------------------
        # Needs to return (engine, strategy_bundle, starting_balance) and
        # set several instance attributes that run() reads after the await.
        mock_engine = MagicMock()
        mock_bundle = MagicMock()
        mock_bundle.actors = []

        async def _fake_setup_engine():
            # Populate the instance attributes that run() reads post-await.
            runner._nt_symbols = ["BTCUSDT-PERP.BINANCE"]
            runner._total_bar_count = 0
            runner._loaded_bar_type_strs = []
            runner._all_bar_type_strs = []
            runner._benchmark_daily_closes = {}
            return mock_engine, mock_bundle, 10_000.0

        monkeypatch.setattr(runner, "_setup_engine", _fake_setup_engine)

        # --- mock _load_funding_rates (async) --- returns empty list so
        # the funding-cost branch is skipped entirely.
        monkeypatch.setattr(
            runner, "_load_funding_rates",
            AsyncMock(return_value=[]),
        )

        # --- mock _load_auxiliary_price_data (async) --- fire-and-forget
        monkeypatch.setattr(
            runner, "_load_auxiliary_price_data",
            AsyncMock(return_value=None),
        )

        # --- mock create_strategies / create_actors (module-level calls) --
        import tinohelm.backtest.runner as runner_module
        monkeypatch.setattr(runner_module, "create_strategies", MagicMock(return_value=[]))
        monkeypatch.setattr(runner_module, "create_actors", MagicMock(return_value=[]))

        # --- mock _extract_results (sync) ---------------------------------
        fake_results: dict = {"statistics": {"total_pnl": 0.0}, "trade_log": [], "equity_curve": []}
        monkeypatch.setattr(runner, "_extract_results", MagicMock(return_value=fake_results))

        # --- exercise: fresh event loop ----------------------------------
        try:
            result = asyncio.run(runner.run())
        except RuntimeError as exc:
            pytest.fail(
                f"asyncio.run(runner.run()) raised RuntimeError in fresh loop: {exc}"
            )

        # --- assertions --------------------------------------------------
        assert isinstance(result, dict), "run() must return a dict"
        assert "statistics" in result, "result must contain 'statistics' key"
        assert "trade_log" in result, "result must contain 'trade_log' key"


# ---------------------------------------------------------------------------
# T3: static source-code guard
# ---------------------------------------------------------------------------

class TestNoNewEventLoopInRunner:
    """runner.py must not call asyncio.new_event_loop().

    The old pattern created a new loop per-call, which raises
    'This event loop is already running' when called from Jupyter or
    a context where a loop is already active.
    """

    def test_no_new_event_loop_in_runner(self):
        import tinohelm.backtest.runner as runner_module
        src = inspect.getsource(runner_module)
        assert "new_event_loop" not in src, (
            "runner.py must not contain asyncio.new_event_loop(). "
            "The async migration (s12) removed this pattern — "
            "do not re-introduce it."
        )
