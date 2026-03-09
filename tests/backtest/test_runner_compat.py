"""Tests for BacktestRunner backward compatibility (Task 9.4).

Verifies that the legacy single-file constructor interface still works
after the portfolio refactor.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestRunnerBackwardCompat:
    """BacktestRunner should accept legacy params and auto-wrap into PortfolioConfig."""

    def test_legacy_single_symbol_string(self):
        """Single symbol as string should work."""
        from tinohelm.backtest.runner import BacktestRunner

        runner = BacktestRunner(
            strategy_path="strategies/my_strat.py:MyStrategy",
            config_path="strategies/my_strat.py:MyStrategyConfig",
            symbol="BTCUSDT-PERP",
            interval="5m",
        )
        assert runner.symbols == ["BTCUSDT-PERP"]
        assert runner.intervals == ["5m"]
        assert runner.symbol == "BTCUSDT-PERP"
        assert runner.interval == "5m"

    def test_legacy_symbols_list(self):
        """symbols= keyword should work."""
        from tinohelm.backtest.runner import BacktestRunner

        runner = BacktestRunner(
            strategy_path="strategies/my_strat.py:MyStrategy",
            config_path="strategies/my_strat.py:MyStrategyConfig",
            symbols=["BTCUSDT-PERP", "ETHUSDT-PERP"],
            intervals=["5m"],
        )
        assert runner.symbols == ["BTCUSDT-PERP", "ETHUSDT-PERP"]
        assert runner.symbol == "BTCUSDT-PERP"  # backward compat alias

    def test_legacy_symbol_list(self):
        """symbol= as list should also work."""
        from tinohelm.backtest.runner import BacktestRunner

        runner = BacktestRunner(
            strategy_path="strategies/my_strat.py:MyStrategy",
            config_path="strategies/my_strat.py:MyStrategyConfig",
            symbol=["BTCUSDT-PERP", "ETHUSDT-PERP"],
            interval="1m",
        )
        assert runner.symbols == ["BTCUSDT-PERP", "ETHUSDT-PERP"]

    def test_auto_wrap_portfolio_config(self):
        """Legacy params should auto-wrap into PortfolioConfig."""
        from tinohelm.backtest.runner import BacktestRunner

        runner = BacktestRunner(
            strategy_path="strategies/my_strat.py:MyStrategy",
            config_path="strategies/my_strat.py:MyStrategyConfig",
            symbol="BTCUSDT-PERP",
            interval="5m",
            strategy_params={"starting_balance": 20000, "leverage": 3},
        )

        cfg = runner._build_portfolio_config()
        assert cfg.symbols == ["BTCUSDT-PERP"]
        assert cfg.interval == "5m"
        assert cfg.account.starting_balance == 20000
        assert cfg.account.leverage == 3
        assert cfg.implicit is True

    def test_explicit_portfolio_config_takes_precedence(self):
        """If portfolio_config is passed, it should be used directly."""
        from tinohelm.backtest.runner import BacktestRunner
        from tinohelm.portfolio.config import PortfolioConfig, AccountSettings

        explicit_cfg = PortfolioConfig(
            strategy_class="strat.py:X",
            config_class="strat.py:XConfig",
            symbols=["XRPUSDT-PERP"],
            interval="1h",
            params={},
            actors=[],
            account=AccountSettings(starting_balance=50000),
        )

        runner = BacktestRunner(
            strategy_path="ignored",
            config_path="ignored",
            symbol="BTCUSDT-PERP",
            interval="5m",
            portfolio_config=explicit_cfg,
        )

        cfg = runner._build_portfolio_config()
        assert cfg is explicit_cfg
        assert cfg.symbols == ["XRPUSDT-PERP"]

    def test_empty_symbols_intervals(self):
        """No symbols/intervals should result in empty lists."""
        from tinohelm.backtest.runner import BacktestRunner

        runner = BacktestRunner(
            strategy_path="strat.py:X",
            config_path="strat.py:XConfig",
        )
        assert runner.symbols == []
        assert runner.intervals == []
        assert runner.symbol == ""
        assert runner.interval == ""
