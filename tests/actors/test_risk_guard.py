"""Tests for RiskGuardActor — risk monitoring logic.

Since NT Actor is a Cython extension class that can't be easily instantiated
in isolation, we test the risk-checking logic using a lightweight stand-in
that replicates the same attributes and methods.
"""
from __future__ import annotations

from unittest.mock import MagicMock
from datetime import datetime, timezone

from tinohelm.actors.risk_guard import BreachAction
from tinohelm.node.topics import RISK_GUARD_FLATTEN, RISK_GUARD_STATE


class _RiskGuardStub:
    """Lightweight stand-in for RiskGuardActor that replicates risk logic.

    Copies the exact same _check_risks / on_bar logic from risk_guard.py
    but without requiring the NT Actor Cython base class.
    """

    def __init__(
        self,
        daily_stop_loss_pct=None,
        max_drawdown_pct=None,
        max_total_exposure=None,
        max_positions=None,
        breach_action="reduce_only",
        starting_balance=10000.0,
    ):
        self._daily_stop_loss_pct = daily_stop_loss_pct
        self._max_drawdown_pct = max_drawdown_pct
        self._max_total_exposure = max_total_exposure
        self._max_positions = max_positions
        self._breach_action = BreachAction(breach_action)
        self._starting_balance = starting_balance
        self._peak_equity = starting_balance
        self._day_start_equity = starting_balance
        self._current_day = None
        self._breached = False
        self._breach_reason = ""
        self._equity_value = starting_balance

        # Mock NT methods
        self.portfolio = MagicMock()
        self.msgbus = MagicMock()
        self.cache = MagicMock()

        # By default, net_exposures returns None (triggers fallback)
        self.portfolio.net_exposures.return_value = None

    def set_equity(self, value: float):
        self._equity_value = value

    def _get_equity(self) -> float:
        return self._equity_value

    def on_event(self, event):
        """Mirrors production: timer-driven risk checks."""
        event_ns = event.ts_event
        event_date = datetime.fromtimestamp(event_ns / 1e9, tz=timezone.utc)
        day_of_year = event_date.timetuple().tm_yday + event_date.year * 1000

        if self._current_day is None:
            self._current_day = day_of_year
        elif day_of_year != self._current_day:
            self._current_day = day_of_year
            equity = self._get_equity()
            if equity > 0:
                self._day_start_equity = equity
            if self._breached and self._breach_reason == "daily_pnl":
                self._breached = False
                self._breach_reason = ""

        self._check_risks()

    # Backward compat alias for tests that use on_bar()
    def on_bar(self, bar):
        self.on_event(bar)

    def _check_risks(self):
        equity = self._get_equity()
        if equity <= 0:
            return

        # Update HWM (always, even after breach — matches production)
        if equity > self._peak_equity:
            self._peak_equity = equity

        if self._breached:
            return

        if self._daily_stop_loss_pct is not None and self._day_start_equity > 0:
            daily_return = (equity - self._day_start_equity) / self._day_start_equity
            if daily_return <= self._daily_stop_loss_pct:
                self._trigger_breach("daily_pnl")
                return

        if self._max_drawdown_pct is not None and self._peak_equity > 0:
            drawdown = (equity - self._peak_equity) / self._peak_equity
            if drawdown <= self._max_drawdown_pct:
                self._trigger_breach("max_drawdown")
                return

        if self._max_total_exposure is not None:
            total_exposure = self._calc_total_exposure()
            if total_exposure > self._max_total_exposure:
                self._trigger_breach("total_exposure")
                return

        if self._max_positions is not None:
            position_count = len(self.cache.positions_open())
            if position_count > self._max_positions:
                self._trigger_breach("position_count")
                return

    def _calc_total_exposure(self) -> float:
        """Mirrors production: uses NT net_exposures() API."""
        exposures = self.portfolio.net_exposures(None)  # venue arg (stub ignores it)
        if not exposures:
            return 0.0
        total = 0.0
        for _currency, money in exposures.items():
            total += abs(float(money.as_double()))
        return total

    def _trigger_breach(self, reason: str):
        self._breached = True
        self._breach_reason = reason
        action = self._breach_action.value
        self.msgbus.publish(RISK_GUARD_STATE, action)
        if self._breach_action == BreachAction.FLATTEN_ALL:
            self._publish_flatten_all()

    def _publish_flatten_all(self):
        for position in self.cache.positions_open():
            instrument_id = str(position.instrument_id)
            self.msgbus.publish(RISK_GUARD_FLATTEN, instrument_id)


class TestDailyPnL:
    """Test daily PnL monitoring with UTC 00:00 boundary."""

    def test_daily_loss_breach(self):
        actor = _RiskGuardStub(daily_stop_loss_pct=-0.02, starting_balance=10000)
        actor._day_start_equity = 10000
        actor.set_equity(9750)  # -2.5% daily loss

        actor._check_risks()

        assert actor._breached is True
        actor.msgbus.publish.assert_any_call(RISK_GUARD_STATE, "reduce_only")

    def test_daily_loss_within_limit(self):
        actor = _RiskGuardStub(daily_stop_loss_pct=-0.02, starting_balance=10000)
        actor._day_start_equity = 10000
        actor.set_equity(9850)  # -1.5%, within limit

        actor._check_risks()

        assert actor._breached is False
        actor.msgbus.publish.assert_not_called()

    def test_day_boundary_resets_tracking(self):
        actor = _RiskGuardStub(daily_stop_loss_pct=-0.02, starting_balance=10000)
        actor.set_equity(9500)

        # Simulate first bar on day 1
        bar1 = MagicMock()
        bar1.ts_event = int(datetime(2025, 2, 14, 23, 59, tzinfo=timezone.utc).timestamp() * 1e9)
        actor.on_bar(bar1)

        day1 = actor._current_day

        # Simulate bar on day 2 — equity recovered to 10200
        actor.set_equity(10200)
        bar2 = MagicMock()
        bar2.ts_event = int(datetime(2025, 2, 15, 0, 1, tzinfo=timezone.utc).timestamp() * 1e9)
        actor.on_bar(bar2)

        assert actor._current_day != day1
        assert actor._day_start_equity == 10200


class TestMaxDrawdown:
    """Test max drawdown monitoring with HWM."""

    def test_drawdown_breach(self):
        actor = _RiskGuardStub(max_drawdown_pct=-0.09, starting_balance=12000)
        actor._peak_equity = 12000
        actor.set_equity(10800)  # -10% from peak

        actor._check_risks()

        assert actor._breached is True
        actor.msgbus.publish.assert_any_call(RISK_GUARD_STATE, "reduce_only")

    def test_drawdown_within_limit(self):
        actor = _RiskGuardStub(max_drawdown_pct=-0.10, starting_balance=10000)
        actor._peak_equity = 10000
        actor.set_equity(9100)  # -9%, within -10% limit

        actor._check_risks()

        assert actor._breached is False

    def test_hwm_updates_on_new_peak(self):
        actor = _RiskGuardStub(max_drawdown_pct=-0.10, starting_balance=10000)
        actor._peak_equity = 10000
        actor.set_equity(11000)

        actor._check_risks()

        assert actor._peak_equity == 11000
        assert actor._breached is False


class TestTotalExposure:
    """Test total exposure monitoring."""

    def test_exposure_breach(self):
        actor = _RiskGuardStub(max_total_exposure=100000, starting_balance=10000)
        actor.set_equity(10000)

        # Mock NT net_exposures() returning {Currency: Money}
        usdt_money = MagicMock()
        usdt_money.as_double.return_value = 110000.0  # 110k > 100k limit
        actor.portfolio.net_exposures.return_value = {"USDT": usdt_money}

        actor._check_risks()

        assert actor._breached is True

    def test_exposure_within_limit(self):
        actor = _RiskGuardStub(max_total_exposure=200000, starting_balance=10000)
        actor.set_equity(10000)

        usdt_money = MagicMock()
        usdt_money.as_double.return_value = 60000.0  # 60k < 200k limit
        actor.portfolio.net_exposures.return_value = {"USDT": usdt_money}

        actor._check_risks()

        assert actor._breached is False


class TestPositionCount:
    """Test position count monitoring."""

    def test_position_count_breach(self):
        actor = _RiskGuardStub(max_positions=10, starting_balance=10000)
        actor.set_equity(10000)

        actor.cache.positions_open.return_value = [MagicMock()] * 11  # > 10

        actor._check_risks()

        assert actor._breached is True

    def test_position_count_at_limit_no_breach(self):
        """Exactly at max_positions should NOT breach (only exceeding does)."""
        actor = _RiskGuardStub(max_positions=10, starting_balance=10000)
        actor.set_equity(10000)

        actor.cache.positions_open.return_value = [MagicMock()] * 10

        actor._check_risks()

        assert actor._breached is False

    def test_position_count_within_limit(self):
        actor = _RiskGuardStub(max_positions=10, starting_balance=10000)
        actor.set_equity(10000)

        actor.cache.positions_open.return_value = [MagicMock()] * 5

        actor._check_risks()

        assert actor._breached is False


class TestBreachActions:
    """Test configurable breach actions."""

    def test_reduce_only_action(self):
        actor = _RiskGuardStub(max_drawdown_pct=-0.05, breach_action="reduce_only", starting_balance=10000)
        actor._peak_equity = 10000
        actor.set_equity(9400)

        actor._check_risks()

        actor.msgbus.publish.assert_called_once_with(RISK_GUARD_STATE, "reduce_only")

    def test_halt_new_action(self):
        actor = _RiskGuardStub(max_drawdown_pct=-0.05, breach_action="halt_new", starting_balance=10000)
        actor._peak_equity = 10000
        actor.set_equity(9400)

        actor._check_risks()

        actor.msgbus.publish.assert_called_once_with(RISK_GUARD_STATE, "halt_new")

    def test_flatten_all_action(self):
        actor = _RiskGuardStub(max_drawdown_pct=-0.05, breach_action="flatten_all", starting_balance=10000)
        actor._peak_equity = 10000
        actor.set_equity(9400)

        pos1 = MagicMock()
        pos1.instrument_id = "BTCUSDT-PERP.BINANCE"
        pos2 = MagicMock()
        pos2.instrument_id = "ETHUSDT-PERP.BINANCE"
        actor.cache.positions_open.return_value = [pos1, pos2]

        actor._check_risks()

        calls = actor.msgbus.publish.call_args_list
        state_calls = [c for c in calls if c == ((RISK_GUARD_STATE, "flatten_all"), {})]
        flatten_calls = [c for c in calls if c[0][0] == RISK_GUARD_FLATTEN]

        assert len(state_calls) == 1
        assert len(flatten_calls) == 2

    def test_no_double_publish_after_breach(self):
        actor = _RiskGuardStub(max_drawdown_pct=-0.05, breach_action="reduce_only", starting_balance=10000)
        actor._peak_equity = 10000
        actor.set_equity(9400)

        actor._check_risks()
        actor._check_risks()  # Second call should be no-op

        assert actor.msgbus.publish.call_count == 1
