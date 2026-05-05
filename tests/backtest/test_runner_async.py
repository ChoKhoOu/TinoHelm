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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
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


class TestTerminalizationCallbackBeforeArtifactExport:
    def test_callback_runs_before_artifact_writes(self, monkeypatch, tmp_path):
        """Queue-mode terminalization marker must start before runner artifact I/O."""
        runner = _mk_minimal_runner()
        runner.artifacts_dir = tmp_path
        events: list[str] = []

        mock_engine = MagicMock()
        mock_bundle = MagicMock()
        mock_bundle.actors = []

        async def _fake_setup_engine():
            runner._nt_symbols = ["BTCUSDT-PERP.BINANCE"]
            runner._total_bar_count = 0
            runner._loaded_bar_type_strs = []
            runner._all_bar_type_strs = []
            runner._benchmark_daily_closes = {}
            return mock_engine, mock_bundle, 10_000.0

        def _before_artifact_export() -> None:
            events.append("terminalizing")

        runner._before_artifact_export = _before_artifact_export
        monkeypatch.setattr(runner, "_setup_engine", _fake_setup_engine)
        monkeypatch.setattr(runner, "_load_funding_rates", AsyncMock(return_value=[]))
        monkeypatch.setattr(runner, "_load_auxiliary_price_data", AsyncMock(return_value=None))
        monkeypatch.setattr(runner, "_extract_results", MagicMock(return_value={"statistics": {}}))
        monkeypatch.setattr(runner, "_export_reports", MagicMock(side_effect=lambda _engine: events.append("reports")))
        monkeypatch.setattr(
            runner,
            "_generate_tearsheet",
            MagicMock(side_effect=lambda _engine, _bars: events.append("tearsheet")),
        )

        import tinohelm.backtest.runner as runner_module
        monkeypatch.setattr(runner_module, "create_strategies", MagicMock(return_value=[]))
        monkeypatch.setattr(runner_module, "create_actors", MagicMock(return_value=[]))
        monkeypatch.setattr(
            "tinohelm.backtest.tearsheet.enhance_tearsheet",
            lambda _artifact_dir, _results: events.append("enhance"),
        )

        result = asyncio.run(runner.run())

        assert result == {"statistics": {}}
        assert events == ["terminalizing", "reports", "tearsheet", "enhance"]
        mock_engine.dispose.assert_called_once()


class TestStreamingBenchmarkDailyCloses:
    def test_bar_data_iterator_updates_daily_closes_for_benchmark(self, monkeypatch):
        runner = _mk_minimal_runner()
        runner.start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        runner.end = runner.start + timedelta(days=2)
        runner.stream_batch_size = 1

        t0 = int(runner.start.timestamp() * 1_000_000_000)
        one_min = 60 * 1_000_000_000
        bars_by_window = [
            [
                SimpleNamespace(ts_init=t0, close=100.0),
                SimpleNamespace(ts_init=t0 + one_min, close=101.0),
            ],
            [
                SimpleNamespace(ts_init=t0 + 24 * 60 * one_min, close=110.0),
                SimpleNamespace(ts_init=t0 + 24 * 60 * one_min + one_min, close=111.0),
            ],
        ]

        class Catalog:
            def __init__(self):
                self.calls = 0

            def bars(self, **kwargs):
                out = bars_by_window[self.calls]
                self.calls += 1
                return out

        catalog = Catalog()
        monkeypatch.setattr(runner, "_catalog_for_path", lambda catalog_path: catalog)
        daily_closes: dict[str, float] = {}
        batches = list(runner._bar_data_iterator(
            Path("/catalog"),
            "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "1d",
            benchmark_daily_closes=daily_closes,
        ))

        assert batches == bars_by_window
        assert daily_closes == {"2026-01-01": 101.0, "2026-01-02": 111.0}


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
