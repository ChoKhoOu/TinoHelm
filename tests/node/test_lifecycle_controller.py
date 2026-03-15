"""Tests for LifecycleController — strategy and system lifecycle management.

LifecycleController is a plain Python class (not a NT Actor/Strategy Cython
extension), so it can be instantiated directly with MagicMock dependencies.

Tests cover all four lifecycle levels:
    L1 — Soft Pause / Resume  (msgbus publish)
    L2 — Flatten              (trader.market_exit_strategy / strategy.market_exit)
    L3 — Halt / Unhalt        (risk_engine.set_trading_state)
    L4 — Shutdown             (os.kill SIGTERM)

And the supporting internals:
    _resolve_strategy_id      (validation + error formatting)
    _on_risk_guard_breach     (RiskGuard msgbus integration)
    get_state                 (dict snapshot)
"""
from __future__ import annotations

import os
import signal
from unittest.mock import MagicMock, call, patch

import pytest
from nautilus_trader.model.enums import TradingState
from nautilus_trader.model.identifiers import StrategyId

from tinohelm.node.lifecycle_controller import LifecycleController
from tinohelm.node.topics import (
    LIFECYCLE_FLATTEN,
    LIFECYCLE_PAUSE,
    LIFECYCLE_RESUME,
    RISK_GUARD_STATE,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_controller(strategy_ids: list[str] | None = None):
    """Return a LifecycleController wired up with MagicMock collaborators.

    ``strategy_ids`` is the list of strategy ID strings that the mock trader
    will report via ``trader.strategy_ids()``.
    """
    trader = MagicMock()
    risk_engine = MagicMock()
    msgbus = MagicMock()
    log = MagicMock()
    publish_ack = MagicMock()

    if strategy_ids is not None:
        trader.strategy_ids.return_value = [StrategyId(s) for s in strategy_ids]
    else:
        trader.strategy_ids.return_value = []

    ctrl = LifecycleController(
        trader=trader,
        risk_engine=risk_engine,
        msgbus=msgbus,
        log=log,
        publish_ack=publish_ack,
    )
    return ctrl, trader, risk_engine, msgbus, log, publish_ack


# ---------------------------------------------------------------------------
# __init__ — subscription wiring
# ---------------------------------------------------------------------------

class TestInit:
    """LifecycleController subscribes to the RiskGuard topic on construction."""

    def test_subscribes_to_risk_guard_state_on_init(self):
        ctrl, _, _, msgbus, _, _ = _make_controller()
        msgbus.subscribe.assert_called_once_with(
            RISK_GUARD_STATE, ctrl._on_risk_guard_breach
        )


# ---------------------------------------------------------------------------
# L1 — Soft Pause
# ---------------------------------------------------------------------------

class TestPauseStrategy:
    """pause_strategy() publishes L1 msgbus signal and updates internal set."""

    def test_pause_publishes_correct_topic(self):
        ctrl, _, _, msgbus, _, _ = _make_controller(["MyStrategy-000"])
        ctrl.pause_strategy("MyStrategy-000")

        msgbus.publish.assert_called_once_with(
            f"{LIFECYCLE_PAUSE}.MyStrategy-000", "pause"
        )

    def test_pause_adds_strategy_to_paused_set(self):
        ctrl, _, _, _, _, _ = _make_controller(["MyStrategy-000"])
        assert "MyStrategy-000" not in ctrl._paused_strategies

        ctrl.pause_strategy("MyStrategy-000")

        assert "MyStrategy-000" in ctrl._paused_strategies

    def test_pause_calls_publish_ack_with_ok_status(self):
        ctrl, _, _, _, _, publish_ack = _make_controller(["MyStrategy-000"])
        ctrl.pause_strategy("MyStrategy-000")

        publish_ack.assert_called_once_with(
            "commands_ack",
            {"cmd": "pause", "strategy_id": "MyStrategy-000", "status": "ok"},
        )

    def test_pause_unknown_strategy_raises_value_error(self):
        ctrl, _, _, _, _, _ = _make_controller(["MyStrategy-000"])
        with pytest.raises(ValueError, match="MyStrategy-001"):
            ctrl.pause_strategy("MyStrategy-001")


# ---------------------------------------------------------------------------
# L1 — Resume
# ---------------------------------------------------------------------------

class TestResumeStrategy:
    """resume_strategy() publishes L1 resume signal and removes from paused set."""

    def test_resume_publishes_correct_topic(self):
        ctrl, _, _, msgbus, _, _ = _make_controller(["MyStrategy-000"])
        ctrl._paused_strategies.add("MyStrategy-000")

        ctrl.resume_strategy("MyStrategy-000")

        msgbus.publish.assert_called_once_with(
            f"{LIFECYCLE_RESUME}.MyStrategy-000", "resume"
        )

    def test_resume_removes_strategy_from_paused_set(self):
        ctrl, _, _, _, _, _ = _make_controller(["MyStrategy-000"])
        ctrl._paused_strategies.add("MyStrategy-000")

        ctrl.resume_strategy("MyStrategy-000")

        assert "MyStrategy-000" not in ctrl._paused_strategies

    def test_resume_calls_publish_ack_with_ok_status(self):
        ctrl, _, _, _, _, publish_ack = _make_controller(["MyStrategy-000"])
        ctrl._paused_strategies.add("MyStrategy-000")

        ctrl.resume_strategy("MyStrategy-000")

        publish_ack.assert_called_once_with(
            "commands_ack",
            {"cmd": "resume", "strategy_id": "MyStrategy-000", "status": "ok"},
        )

    def test_resume_unknown_strategy_raises_value_error(self):
        ctrl, _, _, _, _, _ = _make_controller(["MyStrategy-000"])
        with pytest.raises(ValueError, match="NotExist-000"):
            ctrl.resume_strategy("NotExist-000")

    def test_resume_strategy_not_in_paused_set_is_idempotent(self):
        """discard() is used so resuming a non-paused strategy doesn't raise."""
        ctrl, _, _, _, _, _ = _make_controller(["MyStrategy-000"])
        # Not in paused set — should not raise
        ctrl.resume_strategy("MyStrategy-000")
        assert "MyStrategy-000" not in ctrl._paused_strategies


# ---------------------------------------------------------------------------
# L2 — Flatten (single)
# ---------------------------------------------------------------------------

class TestFlattenSingle:
    """flatten(strategy_id) delegates to trader.market_exit_strategy."""

    def test_flatten_single_calls_market_exit_strategy(self):
        ctrl, trader, _, _, _, _ = _make_controller(["Alpha-001"])
        ctrl.flatten("Alpha-001")

        trader.market_exit_strategy.assert_called_once_with(StrategyId("Alpha-001"))

    def test_flatten_single_publishes_ack(self):
        ctrl, _, _, _, _, publish_ack = _make_controller(["Alpha-001"])
        ctrl.flatten("Alpha-001")

        publish_ack.assert_called_once_with(
            "commands_ack",
            {"cmd": "flatten", "strategy_id": "Alpha-001", "status": "ok"},
        )

    def test_flatten_single_unknown_strategy_raises(self):
        ctrl, _, _, _, _, _ = _make_controller(["Alpha-001"])
        with pytest.raises(ValueError):
            ctrl.flatten("Ghost-999")


# ---------------------------------------------------------------------------
# L2 — Flatten (all)
# ---------------------------------------------------------------------------

class TestFlattenAll:
    """flatten(None) calls market_exit() on every strategy object."""

    def test_flatten_all_calls_market_exit_on_each_strategy(self):
        strat_a = MagicMock()
        strat_b = MagicMock()

        ctrl, trader, _, _, _, _ = _make_controller()
        trader.strategies.return_value = [strat_a, strat_b]

        ctrl.flatten(None)

        strat_a.market_exit.assert_called_once()
        strat_b.market_exit.assert_called_once()

    def test_flatten_all_publishes_ack_with_all(self):
        ctrl, trader, _, _, _, publish_ack = _make_controller()
        trader.strategies.return_value = []

        ctrl.flatten(None)

        publish_ack.assert_called_once_with(
            "commands_ack",
            {"cmd": "flatten", "strategy_id": "all", "status": "ok"},
        )

    def test_flatten_all_called_with_empty_string_uses_all_path(self):
        """Empty string is falsy, so it follows the 'all' path."""
        ctrl, trader, _, _, _, publish_ack = _make_controller()
        trader.strategies.return_value = []

        ctrl.flatten("")

        publish_ack.assert_called_once_with(
            "commands_ack",
            {"cmd": "flatten", "strategy_id": "all", "status": "ok"},
        )


# ---------------------------------------------------------------------------
# L3 — Halt
# ---------------------------------------------------------------------------

class TestHalt:
    """halt() sets TradingState.HALTED on the risk engine."""

    def test_halt_calls_set_trading_state_halted(self):
        ctrl, _, risk_engine, _, _, _ = _make_controller()
        ctrl.halt()

        risk_engine.set_trading_state.assert_called_once_with(TradingState.HALTED)

    def test_halt_publishes_ack(self):
        ctrl, _, _, _, _, publish_ack = _make_controller()
        ctrl.halt()

        publish_ack.assert_called_once_with(
            "commands_ack", {"cmd": "halt", "status": "ok"}
        )

    def test_halt_fallback_on_error_publishes_to_msgbus(self):
        """When set_trading_state raises, a fallback msgbus publish is used."""
        ctrl, _, risk_engine, msgbus, _, publish_ack = _make_controller()
        risk_engine.set_trading_state.side_effect = RuntimeError("engine unavailable")

        ctrl.halt()

        msgbus.publish.assert_called_with(LIFECYCLE_FLATTEN, "halt_fallback")

    def test_halt_fallback_still_publishes_ack(self):
        """Even after a fallback, the ack must still be sent (with 'fallback' status)."""
        ctrl, _, risk_engine, _, _, publish_ack = _make_controller()
        risk_engine.set_trading_state.side_effect = RuntimeError("boom")

        ctrl.halt()

        publish_ack.assert_called_once_with(
            "commands_ack", {"cmd": "halt", "status": "fallback"}
        )


# ---------------------------------------------------------------------------
# L3 — Unhalt
# ---------------------------------------------------------------------------

class TestUnhalt:
    """unhalt() restores TradingState.ACTIVE."""

    def test_unhalt_calls_set_trading_state_active(self):
        ctrl, _, risk_engine, _, _, _ = _make_controller()
        ctrl.unhalt()

        risk_engine.set_trading_state.assert_called_once_with(TradingState.ACTIVE)

    def test_unhalt_publishes_ack(self):
        ctrl, _, _, _, _, publish_ack = _make_controller()
        ctrl.unhalt()

        publish_ack.assert_called_once_with(
            "commands_ack", {"cmd": "unhalt", "status": "ok"}
        )

    def test_unhalt_still_publishes_ack_on_error(self):
        """If set_trading_state raises, the ack reports error status."""
        ctrl, _, risk_engine, _, _, publish_ack = _make_controller()
        risk_engine.set_trading_state.side_effect = RuntimeError("engine unavailable")

        ctrl.unhalt()

        publish_ack.assert_called_once_with(
            "commands_ack", {"cmd": "unhalt", "status": "error", "reason": "engine unavailable"}
        )


# ---------------------------------------------------------------------------
# L4 — Shutdown
# ---------------------------------------------------------------------------

class TestShutdown:
    """shutdown() publishes ack then sends SIGTERM to the current process."""

    def test_shutdown_calls_os_kill_with_sigterm(self):
        ctrl, _, _, _, _, _ = _make_controller()
        with patch("os.kill") as mock_kill:
            ctrl.shutdown()
        mock_kill.assert_called_once_with(os.getpid(), signal.SIGTERM)

    def test_shutdown_publishes_ack_before_sigterm(self):
        """Ack must be recorded before os.kill fires."""
        ctrl, _, _, _, _, publish_ack = _make_controller()
        ack_calls_at_kill_time: list = []

        with patch("os.kill") as mock_kill:
            def capture(*args, **kwargs):
                ack_calls_at_kill_time.extend(publish_ack.call_args_list)

            mock_kill.side_effect = capture
            ctrl.shutdown()

        assert len(ack_calls_at_kill_time) == 1
        assert ack_calls_at_kill_time[0] == call(
            "commands_ack", {"cmd": "shutdown", "status": "received"}
        )


# ---------------------------------------------------------------------------
# get_state
# ---------------------------------------------------------------------------

class TestGetState:
    """get_state() returns a consistent dict snapshot of the controller state."""

    def test_get_state_returns_expected_keys(self):
        ctrl, _, _, _, _, _ = _make_controller()
        state = ctrl.get_state()

        assert "trading_state" in state
        assert "paused" in state
        assert "strategy_states" in state

    def test_get_state_reflects_paused_strategies(self):
        ctrl, trader, _, _, _, _ = _make_controller(["Strat-000", "Strat-001"])
        ctrl._paused_strategies.add("Strat-000")

        state = ctrl.get_state()

        assert "Strat-000" in state["paused"]
        assert "Strat-001" not in state["paused"]
        assert state["strategy_states"]["Strat-000"] == "paused"
        assert state["strategy_states"]["Strat-001"] == "running"

    def test_get_state_paused_list_is_sorted(self):
        ctrl, trader, _, _, _, _ = _make_controller(["Bravo-001", "Alpha-000"])
        ctrl._paused_strategies.update(["Bravo-001", "Alpha-000"])

        state = ctrl.get_state()

        assert state["paused"] == sorted(["Bravo-001", "Alpha-000"])

    def test_get_state_reflects_trading_state_from_risk_engine(self):
        ctrl, _, risk_engine, _, _, _ = _make_controller()
        ts_mock = MagicMock()
        ts_mock.name = "HALTED"
        risk_engine.trading_state = ts_mock

        state = ctrl.get_state()

        assert state["trading_state"] == "halted"

    def test_get_state_falls_back_to_active_on_risk_engine_error(self):
        ctrl, _, risk_engine, _, _, _ = _make_controller()
        type(risk_engine).trading_state = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("unavailable"))
        )

        state = ctrl.get_state()

        assert state["trading_state"] == "active"

    def test_get_state_empty_paused_when_no_strategies_paused(self):
        ctrl, _, _, _, _, _ = _make_controller(["Strat-000"])
        state = ctrl.get_state()

        assert state["paused"] == []


# ---------------------------------------------------------------------------
# _resolve_strategy_id
# ---------------------------------------------------------------------------

class TestResolveStrategyId:
    """_resolve_strategy_id validates the string and returns a StrategyId."""

    def test_valid_id_returns_strategy_id_object(self):
        ctrl, _, _, _, _, _ = _make_controller(["MyStrategy-000"])
        result = ctrl._resolve_strategy_id("MyStrategy-000")

        assert result == StrategyId("MyStrategy-000")

    def test_mismatch_raises_value_error_with_available_ids(self):
        ctrl, _, _, _, _, _ = _make_controller(["Alpha-000", "Beta-001"])
        with pytest.raises(ValueError) as exc_info:
            ctrl._resolve_strategy_id("Ghost-999")

        msg = str(exc_info.value)
        assert "Ghost-999" in msg
        assert "Alpha-000" in msg
        assert "Beta-001" in msg

    def test_bad_format_raises_value_error(self):
        """StrategyId requires 'ClassName-tag' format; plain strings are invalid."""
        ctrl, _, _, _, _, _ = _make_controller([])
        with pytest.raises(ValueError):
            ctrl._resolve_strategy_id("notavalidid")

    def test_empty_string_raises_value_error(self):
        ctrl, _, _, _, _, _ = _make_controller([])
        with pytest.raises(ValueError):
            ctrl._resolve_strategy_id("")


# ---------------------------------------------------------------------------
# _on_risk_guard_breach
# ---------------------------------------------------------------------------

class TestRiskGuardBreach:
    """_on_risk_guard_breach enforces RiskGuard actions via system controls."""

    def test_halt_new_calls_halt(self):
        ctrl, _, risk_engine, _, _, _ = _make_controller()
        ctrl._on_risk_guard_breach("halt_new")

        risk_engine.set_trading_state.assert_called_once_with(TradingState.HALTED)

    def test_reduce_only_calls_set_trading_state_reducing(self):
        ctrl, _, risk_engine, _, _, _ = _make_controller()
        ctrl._on_risk_guard_breach("reduce_only")

        risk_engine.set_trading_state.assert_called_once_with(TradingState.REDUCING)

    def test_unknown_action_is_ignored(self):
        """No action should be taken for unrecognised breach action strings."""
        ctrl, trader, risk_engine, msgbus, _, publish_ack = _make_controller()
        # Reset call tracking on the subscribe call from __init__
        msgbus.reset_mock()

        ctrl._on_risk_guard_breach("flatten_all")

        risk_engine.set_trading_state.assert_not_called()
        trader.market_exit_strategy.assert_not_called()

    def test_reduce_only_swallows_risk_engine_error(self):
        """If set_trading_state raises for REDUCING, the error is logged, not raised."""
        ctrl, _, risk_engine, _, log, _ = _make_controller()
        risk_engine.set_trading_state.side_effect = RuntimeError("engine gone")

        # Must not propagate
        ctrl._on_risk_guard_breach("reduce_only")

        log.error.assert_called()

    def test_subscription_wired_in_init_calls_handler(self):
        """The subscription registered in __init__ must point to _on_risk_guard_breach."""
        ctrl, _, _, msgbus, _, _ = _make_controller()
        subscribe_call = msgbus.subscribe.call_args
        topic, handler = subscribe_call[0]

        assert topic == RISK_GUARD_STATE
        # Bound methods are recreated on each attribute lookup, so identity
        # comparison with `is` always fails. Compare __func__ instead.
        assert handler.__func__ is ctrl._on_risk_guard_breach.__func__


# ---------------------------------------------------------------------------
# Command queue ordering (multiple sequential commands)
# ---------------------------------------------------------------------------

class TestCommandQueueOrdering:
    """Multiple lifecycle commands dispatched in order produce correct side effects."""

    def test_pause_then_resume_updates_set_correctly(self):
        ctrl, _, _, _, _, _ = _make_controller(["Strat-000"])

        ctrl.pause_strategy("Strat-000")
        assert "Strat-000" in ctrl._paused_strategies

        ctrl.resume_strategy("Strat-000")
        assert "Strat-000" not in ctrl._paused_strategies

    def test_halt_then_unhalt_calls_both_states_in_order(self):
        ctrl, _, risk_engine, _, _, _ = _make_controller()

        ctrl.halt()
        ctrl.unhalt()

        assert risk_engine.set_trading_state.call_count == 2
        first_call, second_call = risk_engine.set_trading_state.call_args_list
        assert first_call == call(TradingState.HALTED)
        assert second_call == call(TradingState.ACTIVE)

    def test_multiple_pauses_accumulate_in_paused_set(self):
        ctrl, _, _, _, _, _ = _make_controller(["Alpha-000", "Beta-001"])

        ctrl.pause_strategy("Alpha-000")
        ctrl.pause_strategy("Beta-001")

        assert ctrl._paused_strategies == {"Alpha-000", "Beta-001"}

    def test_publish_ack_called_for_each_command(self):
        ctrl, _, _, _, _, publish_ack = _make_controller(["Strat-000"])

        ctrl.pause_strategy("Strat-000")
        ctrl.resume_strategy("Strat-000")
        ctrl.halt()
        ctrl.unhalt()

        assert publish_ack.call_count == 4
