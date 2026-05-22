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
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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

    def test_legacy_runner_entrypoints_are_deleted(self):
        assert not hasattr(BacktestRunner, "_setup_engine")
        assert not hasattr(BacktestRunner, "prepare_engine")
        assert not hasattr(BacktestRunner, "run_trial")
        assert not hasattr(BacktestRunner, "_submit_and_wait_fetch")
        assert not hasattr(BacktestRunner, "_download_bars")
        assert not hasattr(BacktestRunner, "_resolve_bars")
        assert not hasattr(BacktestRunner, "_resolve_bar_stream")


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

    def test_run_uses_backtest_node_even_when_legacy_interval_is_present(self, monkeypatch):
        runner = BacktestRunner(
            strategy_path="fake/strat.py:FakeStrategy",
            config_path="fake/strat.py:FakeStrategyConfig",
            symbol="BTCUSDT-PERP",
            interval="1m",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        monkeypatch.setattr(
            runner,
            "_run_via_backtest_node",
            MagicMock(return_value={"statistics": {}, "trade_log": [], "equity_curve": []}),
            raising=False,
        )
        monkeypatch.setattr(
            runner,
            "_setup_engine",
            AsyncMock(side_effect=AssertionError("legacy _setup_engine path must be dead")),
            raising=False,
        )

        result = asyncio.run(runner.run())

        assert result["statistics"] == {}
        assert runner._run_via_backtest_node.call_count == 1

    def test_builds_node_run_config_with_strategy_and_1m_source_data(self, monkeypatch):
        runner = BacktestRunner(
            strategy_path="fake/strat.py:FakeStrategy",
            config_path="fake/strat.py:FakeStrategyConfig",
            symbol="BTCUSDT-PERP",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        captured = {}

        class FakeNode:
            def __init__(self, configs):
                captured["configs"] = configs

            def build(self):
                return None

            def run(self):
                return [object()]

            def get_engine(self, run_config_id):
                captured["run_config_id"] = run_config_id
                return MagicMock()

        monkeypatch.setattr(
            "nautilus_trader.backtest.node.BacktestNode",
            FakeNode,
        )
        monkeypatch.setattr(runner, "_extract_results", MagicMock(return_value={"statistics": {}, "trade_log": [], "equity_curve": []}))

        result = runner._run_via_backtest_node()

        cfg = captured["configs"][0]
        assert result["statistics"] == {}
        assert len(cfg.engine.strategies) == 1
        assert cfg.engine.strategies[0].strategy_path == "fake/strat.py:FakeStrategy"
        assert cfg.engine.strategies[0].config_path == "fake/strat.py:FakeStrategyConfig"
        assert cfg.data[0].bar_spec == "1-MINUTE-LAST"
        assert cfg.data[0].instrument_ids == ["BTCUSDT-PERP.BINANCE"]

    def test_node_path_extracts_results_from_node_engine(self, monkeypatch):
        runner = BacktestRunner(
            strategy_path="fake/strat.py:FakeStrategy",
            config_path="fake/strat.py:FakeStrategyConfig",
            symbol="BTCUSDT-PERP",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        engine = MagicMock()

        class FakeNode:
            def __init__(self, configs):
                self._configs = configs

            def build(self):
                return None

            def run(self):
                return [object()]

            def get_engine(self, run_config_id):
                return engine

        monkeypatch.setattr(
            "nautilus_trader.backtest.node.BacktestNode",
            FakeNode,
        )
        monkeypatch.setattr(runner, "_extract_results", MagicMock(return_value={"statistics": {"total_pnl": 1.0}, "trade_log": [], "equity_curve": []}))

        result = runner._run_via_backtest_node()

        assert result["statistics"]["total_pnl"] == 1.0
        assert runner._extract_results.call_count == 1
        assert runner._extract_results.call_args.args == (engine, 10000.0)

    def test_node_path_preserves_artifact_export_chain(self, monkeypatch, tmp_path):
        runner = BacktestRunner(
            strategy_path="fake/strat.py:FakeStrategy",
            config_path="fake/strat.py:FakeStrategyConfig",
            symbol="BTCUSDT-PERP",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        runner.artifacts_dir = tmp_path
        events = []
        engine = MagicMock()

        class FakeNode:
            def __init__(self, configs):
                self._configs = configs

            def build(self):
                return None

            def run(self):
                return [object()]

            def get_engine(self, run_config_id):
                return engine

        monkeypatch.setattr(
            "nautilus_trader.backtest.node.BacktestNode",
            FakeNode,
        )
        monkeypatch.setattr(runner, "_extract_results", MagicMock(return_value={"statistics": {}}))
        runner._before_artifact_export = lambda: events.append("terminalizing")
        monkeypatch.setattr(runner, "_export_reports", MagicMock(side_effect=lambda _engine: events.append("reports")))
        monkeypatch.setattr(runner, "_generate_tearsheet", MagicMock(side_effect=lambda _engine, _bars: events.append("tearsheet")))
        monkeypatch.setattr(
            "tinohelm.backtest.tearsheet.enhance_tearsheet",
            lambda _artifact_dir, _results: events.append("enhance"),
        )

        result = runner._run_via_backtest_node()

        assert result == {"statistics": {}}
        assert events == ["terminalizing", "reports", "tearsheet", "enhance"]

    def test_node_path_registers_builtin_and_custom_statistics_before_run(self, monkeypatch):
        runner = BacktestRunner(
            strategy_path="fake/strat.py:FakeStrategy",
            config_path="fake/strat.py:FakeStrategyConfig",
            symbol="BTCUSDT-PERP",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        analyzer = MagicMock()
        engine = MagicMock()
        engine.portfolio.analyzer = analyzer
        builtin_calls = []
        custom_register = MagicMock(return_value=17)

        class FakeStat:
            def __init__(self, name):
                self.name = name

        class FakeNode:
            def __init__(self, configs):
                self._configs = configs
                self._engine = engine

            def build(self):
                return None

            def run(self):
                return [object()]

            def get_engine(self, run_config_id):
                return self._engine

        monkeypatch.setattr("nautilus_trader.backtest.node.BacktestNode", FakeNode)
        monkeypatch.setattr(
            "nautilus_trader.analysis.MaxDrawdown",
            lambda: builtin_calls.append("MaxDrawdown") or FakeStat("MaxDrawdown"),
        )
        monkeypatch.setattr(
            "nautilus_trader.analysis.CalmarRatio",
            lambda: builtin_calls.append("CalmarRatio") or FakeStat("CalmarRatio"),
        )
        monkeypatch.setattr(
            "nautilus_trader.analysis.CAGR",
            lambda: builtin_calls.append("CAGR") or FakeStat("CAGR"),
        )
        monkeypatch.setattr(
            "nautilus_trader.analysis.ProfitFactor",
            lambda: builtin_calls.append("ProfitFactor") or FakeStat("ProfitFactor"),
        )
        monkeypatch.setattr(
            "tinohelm.backtest.custom_statistics.register_custom_statistics",
            custom_register,
        )
        monkeypatch.setattr(
            runner,
            "_extract_results",
            MagicMock(return_value={"statistics": {"total_pnl": 1.0}, "trade_log": [], "equity_curve": []}),
        )
        monkeypatch.setattr(runner, "_prepare_funding_tracker", MagicMock(return_value=None), raising=False)
        monkeypatch.setattr(runner, "_merge_funding_results", MagicMock(side_effect=lambda results: results), raising=False)

        result = runner._run_via_backtest_node()

        assert result["statistics"]["total_pnl"] == 1.0
        assert builtin_calls == ["MaxDrawdown", "CalmarRatio", "CAGR", "ProfitFactor"]
        assert analyzer.register_statistic.call_count == 4
        assert [c.args[0].name for c in analyzer.register_statistic.call_args_list] == [
            "MaxDrawdown",
            "CalmarRatio",
            "CAGR",
            "ProfitFactor",
        ]
        custom_register.assert_called_once_with(analyzer)

    def test_node_path_merges_funding_results_into_statistics(self, monkeypatch):
        runner = BacktestRunner(
            strategy_path="fake/strat.py:FakeStrategy",
            config_path="fake/strat.py:FakeStrategyConfig",
            symbol="BTCUSDT-PERP",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        engine = MagicMock()
        engine.portfolio.analyzer = MagicMock()
        tracker = MagicMock()
        tracker.get_results.return_value = {
            "total_funding_cost": 12.3456,
            "funding_event_count": 2,
            "per_symbol_funding": {"BTCUSDT-PERP.BINANCE": 12.3456},
            "funding_records": [{"timestamp": "t1"}, {"timestamp": "t2"}],
        }

        class FakeNode:
            def __init__(self, configs):
                self._configs = configs
                self._engine = engine

            def build(self):
                return None

            def run(self):
                return [object()]

            def get_engine(self, run_config_id):
                return self._engine

        monkeypatch.setattr("nautilus_trader.backtest.node.BacktestNode", FakeNode)
        monkeypatch.setattr(
            runner,
            "_extract_results",
            MagicMock(return_value={"statistics": {"total_pnl": 100.0}, "trade_log": [], "equity_curve": []}),
        )
        monkeypatch.setattr(runner, "_register_analyzer_statistics", MagicMock(), raising=False)
        monkeypatch.setattr(runner, "_prepare_funding_tracker", MagicMock(return_value=tracker), raising=False)

        result = runner._run_via_backtest_node()

        assert result["funding"]["total_funding_cost"] == 12.3456
        assert result["statistics"]["total_funding_cost"] == 12.3456
        assert result["statistics"]["pnl_after_funding"] == 87.6544

# ---------------------------------------------------------------------------
# T3: funding event assembly
# ---------------------------------------------------------------------------

class TestFundingEventAssembly:
    def test_build_funding_events_uses_latest_mark_price_at_or_before_funding(self):
        funding_rows = [
            SimpleNamespace(ts_event=2_000_000_000, rate=0.0001, interval=480),
            SimpleNamespace(ts_event=5_000_000_000, rate=-0.0002, interval=480),
        ]
        mark_rows = [
            SimpleNamespace(ts_event=1_000_000_000, value=100.0),
            SimpleNamespace(ts_event=4_000_000_000, value=105.0),
            SimpleNamespace(ts_event=6_000_000_000, value=110.0),
        ]

        events = BacktestRunner._build_funding_events("BTCUSDT-PERP.BINANCE", funding_rows, mark_rows)

        assert len(events) == 2
        assert events[0]["mark_price"] == 100.0
        assert events[1]["mark_price"] == 105.0
        assert events[0]["funding_interval_minutes"] == 480
        assert events[1]["rate"] == -0.0002

# ---------------------------------------------------------------------------
# T4: static source-code guard
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
