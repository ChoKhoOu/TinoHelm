"""Tests for extract_backtest_results() with multi-instrument data (Task 5.5).

Verifies per-instrument breakdown and combined equity curve work correctly
when multiple strategy instances (one per symbol) are used.
"""
from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch
import pytest


def _make_mock_position(instrument_id: str, pnl: float, side: str = "BUY"):
    """Create a mock NT Position object."""
    pos = MagicMock()
    pos.instrument_id = MagicMock()
    pos.instrument_id.__str__ = MagicMock(return_value=instrument_id)
    pos.entry = MagicMock()
    pos.entry.name = side
    pos.side = MagicMock()
    pos.side.name = "FLAT"
    pos.quantity = MagicMock()
    pos.quantity.__str__ = MagicMock(return_value="1.0")
    pos.peak_qty = MagicMock()
    pos.peak_qty.__str__ = MagicMock(return_value="1.0")
    pos.avg_px_open = 50000.0
    pos.avg_px_close = 50000.0 + pnl
    pos.realized_pnl = MagicMock()
    pos.realized_pnl.as_double = MagicMock(return_value=pnl)
    pos.realized_pnl.__str__ = MagicMock(return_value=f"{pnl} USDT")
    pos.unrealized_pnl = MagicMock(return_value=MagicMock(as_double=MagicMock(return_value=0.0)))
    pos.ts_opened = 1_700_000_000_000_000_000
    pos.ts_closed = 1_700_000_100_000_000_000
    pos.duration_ns = 100_000_000_000
    pos.commissions = MagicMock(return_value={})
    pos.events = []
    return pos


def _make_mock_engine(positions_by_instrument: dict[str, list[float]]):
    """Build a mock BacktestEngine with positions across multiple instruments.

    Args:
        positions_by_instrument: {instrument_id: [pnl1, pnl2, ...]}
    """
    engine = MagicMock()

    # Build position list
    all_positions = []
    for inst_id, pnls in positions_by_instrument.items():
        for pnl in pnls:
            all_positions.append(_make_mock_position(inst_id, pnl))

    # Cache
    engine.cache.positions.return_value = all_positions
    engine.cache.positions_open.return_value = []
    engine.cache.positions_closed.return_value = all_positions
    engine.cache.orders.return_value = []
    engine.cache.orders_open.return_value = []

    # Portfolio analyzer
    analyzer = MagicMock()
    analyzer.returns.return_value = MagicMock(
        tolist=MagicMock(return_value=[0.01, -0.005, 0.02]),
        empty=False,
        cumsum=MagicMock(return_value=MagicMock(tolist=MagicMock(return_value=[0.01, 0.005, 0.025]))),
        mean=MagicMock(return_value=0.008),
        std=MagicMock(return_value=0.01),
    )
    analyzer.get_performance_stats_pnls.return_value = {}
    analyzer.get_performance_stats_returns.return_value = {}
    analyzer.get_performance_stats_general.return_value = {}
    engine.portfolio.analyzer = analyzer

    # Account balance
    account = MagicMock()
    balance = MagicMock()
    balance.as_double.return_value = 10500.0
    account.balance_total.return_value = balance
    engine.portfolio.account.return_value = account

    # Reports (generate_* methods)
    engine.generate_order_fills_report.return_value = MagicMock(
        empty=True, to_dict=MagicMock(return_value={})
    )
    engine.generate_positions_report.return_value = MagicMock(
        empty=True, to_dict=MagicMock(return_value={})
    )
    engine.generate_account_report.return_value = MagicMock(
        empty=True, to_dict=MagicMock(return_value={})
    )

    return engine


class TestExtractMultiInstrument:
    """Verify extract_backtest_results handles multi-instrument engine output."""

    def test_per_instrument_breakdown_has_all_instruments(self):
        """Each instrument should appear in per_instrument dict."""
        from tinohelm.backtest.result import extract_backtest_results

        engine = _make_mock_engine({
            "BTCUSDT-PERP.BINANCE": [100.0, -30.0, 50.0],
            "ETHUSDT-PERP.BINANCE": [20.0, -10.0],
        })

        result = extract_backtest_results(engine, starting_balance=10000)

        per_inst = result.get("per_instrument", {})
        assert "BTCUSDT-PERP.BINANCE" in per_inst
        assert "ETHUSDT-PERP.BINANCE" in per_inst

    def test_per_instrument_trade_counts(self):
        """Trade counts per instrument should match position count."""
        from tinohelm.backtest.result import extract_backtest_results

        engine = _make_mock_engine({
            "BTCUSDT-PERP.BINANCE": [100.0, -30.0, 50.0],
            "ETHUSDT-PERP.BINANCE": [20.0, -10.0],
        })

        result = extract_backtest_results(engine, starting_balance=10000)

        btc = result["per_instrument"]["BTCUSDT-PERP.BINANCE"]
        eth = result["per_instrument"]["ETHUSDT-PERP.BINANCE"]
        assert btc["total_trades"] == 3
        assert eth["total_trades"] == 2

    def test_per_instrument_win_loss_split(self):
        """Win/loss counts should be correct per instrument."""
        from tinohelm.backtest.result import extract_backtest_results

        engine = _make_mock_engine({
            "BTCUSDT-PERP.BINANCE": [100.0, -30.0, 50.0],  # 2W 1L
            "ETHUSDT-PERP.BINANCE": [-20.0, -10.0],          # 0W 2L
        })

        result = extract_backtest_results(engine, starting_balance=10000)

        btc = result["per_instrument"]["BTCUSDT-PERP.BINANCE"]
        assert btc["winning_trades"] == 2
        assert btc["losing_trades"] == 1

        eth = result["per_instrument"]["ETHUSDT-PERP.BINANCE"]
        assert eth["winning_trades"] == 0
        assert eth["losing_trades"] == 2

    def test_combined_statistics_includes_all_trades(self):
        """Top-level statistics should reflect total trades across all instruments."""
        from tinohelm.backtest.result import extract_backtest_results

        engine = _make_mock_engine({
            "BTCUSDT-PERP.BINANCE": [100.0, -30.0],
            "ETHUSDT-PERP.BINANCE": [20.0],
        })

        result = extract_backtest_results(engine, starting_balance=10000)

        stats = result.get("statistics", {})
        assert stats.get("total_trades") == 3

    def test_equity_curve_present(self):
        """Result should include an equity_curve list."""
        from tinohelm.backtest.result import extract_backtest_results

        engine = _make_mock_engine({
            "BTCUSDT-PERP.BINANCE": [100.0],
        })

        result = extract_backtest_results(engine, starting_balance=10000)
        assert "equity_curve" in result
        assert isinstance(result["equity_curve"], list)
