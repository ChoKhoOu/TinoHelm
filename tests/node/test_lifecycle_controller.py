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
    """pause_strategy_id() publishes L1 msgbus signal and updates internal set."""

    def test_pause_publishes_correct_topic(self):
        ctrl, _, _, msgbus, _, _ = _make_controller(["MyStrategy-000"])
        ctrl.pause_strategy_id("MyStrategy-000")

        msgbus.publish.assert_called_once_with(
            f"{LIFECYCLE_PAUSE}.MyStrategy-000", "pause"
        )

    def test_pause_adds_strategy_to_paused_set(self):
        ctrl, _, _, _, _, _ = _make_controller(["MyStrategy-000"])
        assert "MyStrategy-000" not in ctrl._paused_strategies

        ctrl.pause_strategy_id("MyStrategy-000")

        assert "MyStrategy-000" in ctrl._paused_strategies

    def test_pause_calls_publish_ack_with_ok_status(self):
        ctrl, _, _, _, _, publish_ack = _make_controller(["MyStrategy-000"])
        ctrl.pause_strategy_id("MyStrategy-000")

        publish_ack.assert_called_once_with(
            "commands_ack",
            {"cmd": "pause", "strategy_id": "MyStrategy-000", "status": "ok"},
        )

    def test_pause_unknown_strategy_raises_value_error(self):
        ctrl, _, _, _, _, _ = _make_controller(["MyStrategy-000"])
        with pytest.raises(ValueError, match="MyStrategy-001"):
            ctrl.pause_strategy_id("MyStrategy-001")


# ---------------------------------------------------------------------------
# L1 — Resume
# ---------------------------------------------------------------------------

class TestResumeStrategy:
    """resume_strategy_id() publishes L1 resume signal and removes from paused set."""

    def test_resume_publishes_correct_topic(self):
        ctrl, _, _, msgbus, _, _ = _make_controller(["MyStrategy-000"])
        ctrl._paused_strategies.add("MyStrategy-000")

        ctrl.resume_strategy_id("MyStrategy-000")

        msgbus.publish.assert_called_once_with(
            f"{LIFECYCLE_RESUME}.MyStrategy-000", "resume"
        )

    def test_resume_removes_strategy_from_paused_set(self):
        ctrl, _, _, _, _, _ = _make_controller(["MyStrategy-000"])
        ctrl._paused_strategies.add("MyStrategy-000")

        ctrl.resume_strategy_id("MyStrategy-000")

        assert "MyStrategy-000" not in ctrl._paused_strategies

    def test_resume_calls_publish_ack_with_ok_status(self):
        ctrl, _, _, _, _, publish_ack = _make_controller(["MyStrategy-000"])
        ctrl._paused_strategies.add("MyStrategy-000")

        ctrl.resume_strategy_id("MyStrategy-000")

        publish_ack.assert_called_once_with(
            "commands_ack",
            {"cmd": "resume", "strategy_id": "MyStrategy-000", "status": "ok"},
        )

    def test_resume_unknown_strategy_raises_value_error(self):
        ctrl, _, _, _, _, _ = _make_controller(["MyStrategy-000"])
        with pytest.raises(ValueError, match="NotExist-000"):
            ctrl.resume_strategy_id("NotExist-000")

    def test_resume_strategy_not_in_paused_set_is_idempotent(self):
        """discard() is used so resuming a non-paused strategy doesn't raise."""
        ctrl, _, _, _, _, _ = _make_controller(["MyStrategy-000"])
        # Not in paused set — should not raise
        ctrl.resume_strategy_id("MyStrategy-000")
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

        ctrl.pause_strategy_id("Strat-000")
        assert "Strat-000" in ctrl._paused_strategies

        ctrl.resume_strategy_id("Strat-000")
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

        ctrl.pause_strategy_id("Alpha-000")
        ctrl.pause_strategy_id("Beta-001")

        assert ctrl._paused_strategies == {"Alpha-000", "Beta-001"}

    def test_publish_ack_called_for_each_command(self):
        ctrl, _, _, _, _, publish_ack = _make_controller(["Strat-000"])

        ctrl.pause_strategy_id("Strat-000")
        ctrl.resume_strategy_id("Strat-000")
        ctrl.halt()
        ctrl.unhalt()

        assert publish_ack.call_count == 4


# ---------------------------------------------------------------------------
# dispose() — unsubscribe wiring
# ---------------------------------------------------------------------------


class TestDispose:
    """dispose() unsubscribes from RISK_GUARD_STATE and swallows errors."""

    def test_dispose_unsubscribes_from_risk_guard_topic(self):
        ctrl, _, _, msgbus, _, _ = _make_controller()
        ctrl.dispose()

        # Called with the same topic + handler that __init__ registered.
        msgbus.unsubscribe.assert_called_once_with(
            RISK_GUARD_STATE, ctrl._on_risk_guard_breach,
        )

    def test_dispose_swallows_unsubscribe_errors(self):
        """Double-dispose (or bus already torn down) must not raise."""
        ctrl, _, _, msgbus, _, _ = _make_controller()
        msgbus.unsubscribe.side_effect = RuntimeError("bus gone")
        # Must not raise.
        ctrl.dispose()


# ---------------------------------------------------------------------------
# pause_all / resume_all — broadcast variants
# ---------------------------------------------------------------------------


class TestPauseAll:
    """pause_all() iterates trader.strategies() and publishes pause for each."""

    def test_pause_all_publishes_for_every_strategy(self):
        strat_a = MagicMock()
        strat_a.id = StrategyId("Alpha-000")
        strat_b = MagicMock()
        strat_b.id = StrategyId("Beta-001")

        ctrl, trader, _, msgbus, _, _ = _make_controller()
        trader.strategies.return_value = [strat_a, strat_b]

        ctrl.pause_all()

        assert ctrl._paused_strategies == {"Alpha-000", "Beta-001"}
        msgbus.publish.assert_any_call(f"{LIFECYCLE_PAUSE}.Alpha-000", "pause")
        msgbus.publish.assert_any_call(f"{LIFECYCLE_PAUSE}.Beta-001", "pause")

    def test_pause_all_publishes_ack_even_when_no_strategies(self):
        ctrl, trader, _, _, _, publish_ack = _make_controller()
        trader.strategies.return_value = []

        ctrl.pause_all()

        publish_ack.assert_called_once_with(
            "commands_ack",
            {"cmd": "pause", "strategy_id": "all", "status": "ok"},
        )


class TestResumeAll:
    """resume_all() drains _paused_strategies and publishes resume for each."""

    def test_resume_all_publishes_for_each_paused_strategy(self):
        ctrl, _, _, msgbus, _, _ = _make_controller()
        ctrl._paused_strategies.update({"Alpha-000", "Beta-001"})

        ctrl.resume_all()

        # Every paused SID must have received a resume publish.
        published = {
            call_[0][0] for call_ in msgbus.publish.call_args_list
            if call_[0][0].startswith(f"{LIFECYCLE_RESUME}.")
        }
        assert published == {
            f"{LIFECYCLE_RESUME}.Alpha-000",
            f"{LIFECYCLE_RESUME}.Beta-001",
        }

    def test_resume_all_clears_paused_set(self):
        ctrl, _, _, _, _, _ = _make_controller()
        ctrl._paused_strategies.update({"Alpha-000", "Beta-001"})
        ctrl.resume_all()
        assert ctrl._paused_strategies == set()

    def test_resume_all_publishes_ack(self):
        ctrl, _, _, _, _, publish_ack = _make_controller()
        ctrl.resume_all()

        publish_ack.assert_called_once_with(
            "commands_ack",
            {"cmd": "resume", "strategy_id": "all", "status": "ok"},
        )

    def test_resume_all_when_no_paused_strategies_is_still_safe(self):
        ctrl, _, _, msgbus, _, publish_ack = _make_controller()
        ctrl.resume_all()
        # No resume publishes — only the ack path was exercised.
        resume_publishes = [
            c for c in msgbus.publish.call_args_list
            if c[0][0].startswith(f"{LIFECYCLE_RESUME}.")
        ]
        assert resume_publishes == []
        publish_ack.assert_called_once()


# ---------------------------------------------------------------------------
# _pause_strategy_id / _resume_strategy — internal no-ack variants
# ---------------------------------------------------------------------------


class TestInternalPauseResume:
    """Internal variants skip ack publishing and swallow resolve errors."""

    def test_internal_pause_publishes_without_ack(self):
        ctrl, _, _, msgbus, _, publish_ack = _make_controller(["Alpha-000"])
        ctrl._pause_strategy_id("Alpha-000")

        assert "Alpha-000" in ctrl._paused_strategies
        msgbus.publish.assert_called_once_with(
            f"{LIFECYCLE_PAUSE}.Alpha-000", "pause",
        )
        publish_ack.assert_not_called()

    def test_internal_pause_logs_instead_of_raising_on_bad_id(self):
        ctrl, _, _, _, log, publish_ack = _make_controller(["Alpha-000"])
        ctrl._pause_strategy_id("Ghost-999")  # unknown — must not raise

        log.error.assert_called()
        publish_ack.assert_not_called()
        # The paused set must be unchanged.
        assert ctrl._paused_strategies == set()

    def test_internal_resume_publishes_without_ack(self):
        ctrl, _, _, msgbus, _, publish_ack = _make_controller(["Alpha-000"])
        ctrl._paused_strategies.add("Alpha-000")

        ctrl._resume_strategy("Alpha-000")

        assert "Alpha-000" not in ctrl._paused_strategies
        msgbus.publish.assert_called_once_with(
            f"{LIFECYCLE_RESUME}.Alpha-000", "resume",
        )
        publish_ack.assert_not_called()

    def test_internal_resume_swallows_resolve_errors(self):
        ctrl, _, _, _, log, _ = _make_controller(["Alpha-000"])
        # Resuming an unknown strategy ID must not raise.
        ctrl._resume_strategy("Ghost-999")
        log.error.assert_called()


# ---------------------------------------------------------------------------
# get_state with registry attached
# ---------------------------------------------------------------------------


class TestGetStateWithRegistry:
    """When a StrategyRegistry is wired in, get_state() includes 'strategies'."""

    def test_state_contains_strategies_key_when_registry_attached(self):
        ctrl, _, _, _, _, _ = _make_controller()
        registry = MagicMock()
        registry.get_all_states.return_value = {
            "mom": {"state": "running", "strategy_ids": ["Alpha-000"]},
        }
        ctrl._registry = registry

        state = ctrl.get_state()

        assert state["strategies"] == {
            "mom": {"state": "running", "strategy_ids": ["Alpha-000"]},
        }

    def test_state_omits_strategies_key_without_registry(self):
        ctrl, _, _, _, _, _ = _make_controller()
        # _registry defaults to None.
        state = ctrl.get_state()
        assert "strategies" not in state


# ---------------------------------------------------------------------------
# Bundle pause / resume — via StrategyRegistry
# ---------------------------------------------------------------------------


def _make_controller_with_registry(registry_state="running", strategy_ids=None):
    """Build a controller wired up with a fake StrategyRegistry in *state*."""
    strategy_ids = strategy_ids or ["Alpha-000", "Alpha-001"]
    ctrl, trader, risk_engine, msgbus, log, publish_ack = _make_controller(strategy_ids)

    registry = MagicMock()
    registry.get.return_value = MagicMock(
        state=registry_state,
        strategy_ids=list(strategy_ids),
    )
    ctrl._registry = registry
    return ctrl, trader, msgbus, log, publish_ack, registry


class TestPauseStrategyBundle:
    """pause_strategy(name) publishes L1 pause for every member of the bundle."""

    def test_pause_bundle_publishes_for_each_member(self):
        ctrl, _, msgbus, _, _, registry = _make_controller_with_registry()
        ctrl.pause_strategy("mom")

        for sid in ("Alpha-000", "Alpha-001"):
            msgbus.publish.assert_any_call(f"{LIFECYCLE_PAUSE}.{sid}", "pause")
        registry.mark_paused.assert_called_once_with("mom")

    def test_pause_bundle_publishes_ack(self):
        ctrl, _, _, _, publish_ack, _ = _make_controller_with_registry()
        ctrl.pause_strategy("mom")
        publish_ack.assert_called_once_with(
            "commands_ack",
            {"cmd": "pause_strategy", "name": "mom", "status": "ok"},
        )

    def test_pause_bundle_raises_when_registry_missing(self):
        ctrl, _, _, _, _, _ = _make_controller()
        with pytest.raises(ValueError, match="not initialized"):
            ctrl.pause_strategy("mom")

    def test_pause_bundle_raises_when_strategy_unknown(self):
        ctrl, _, _, _, _, _ = _make_controller()
        registry = MagicMock()
        registry.get.return_value = None
        ctrl._registry = registry

        with pytest.raises(ValueError, match="Cannot pause"):
            ctrl.pause_strategy("mom")

    def test_pause_bundle_raises_when_not_running(self):
        ctrl, _, _, _, _, _ = _make_controller_with_registry(
            registry_state="paused",
        )
        with pytest.raises(ValueError, match="Cannot pause"):
            ctrl.pause_strategy("mom")


class TestResumeStrategyBundle:
    """resume_strategy(name) re-enables every member of a paused bundle."""

    def test_resume_bundle_publishes_resume_for_each(self):
        ctrl, _, msgbus, _, _, registry = _make_controller_with_registry(
            registry_state="paused",
        )
        ctrl._paused_strategies.update({"Alpha-000", "Alpha-001"})

        ctrl.resume_strategy("mom")

        for sid in ("Alpha-000", "Alpha-001"):
            msgbus.publish.assert_any_call(f"{LIFECYCLE_RESUME}.{sid}", "resume")
        # Registry must be transitioned back to running with the same ids.
        registry.mark_running.assert_called_once_with(
            "mom", ["Alpha-000", "Alpha-001"],
        )

    def test_resume_bundle_publishes_ack(self):
        ctrl, _, _, _, publish_ack, _ = _make_controller_with_registry(
            registry_state="paused",
        )
        ctrl.resume_strategy("mom")
        publish_ack.assert_called_once_with(
            "commands_ack",
            {"cmd": "resume_strategy", "name": "mom", "status": "ok"},
        )

    def test_resume_bundle_clears_paused_set_for_members(self):
        ctrl, _, _, _, _, _ = _make_controller_with_registry(
            registry_state="paused",
        )
        ctrl._paused_strategies.update({"Alpha-000", "Alpha-001", "Other-999"})
        ctrl.resume_strategy("mom")
        # Bundle members removed; unrelated "Other-999" kept.
        assert ctrl._paused_strategies == {"Other-999"}

    def test_resume_bundle_raises_when_not_paused(self):
        ctrl, _, _, _, _, _ = _make_controller_with_registry(
            registry_state="running",
        )
        with pytest.raises(ValueError, match="Cannot resume"):
            ctrl.resume_strategy("mom")


# ---------------------------------------------------------------------------
# flatten_stop_strategy + check_flatten_stop_completion
# ---------------------------------------------------------------------------


class TestFlattenStopStrategy:
    """Bundle flatten-stop path: resume paused → market_exit → enqueue pending."""

    def test_flatten_stop_calls_market_exit_per_strategy(self):
        ctrl, trader, _, _, _, _ = _make_controller_with_registry()
        strat_a = MagicMock()
        strat_b = MagicMock()
        trader.strategy.side_effect = [strat_a, strat_b]

        ctrl.flatten_stop_strategy("mom")

        strat_a.market_exit.assert_called_once()
        strat_b.market_exit.assert_called_once()

    def test_flatten_stop_enqueues_pending_record(self):
        ctrl, trader, _, _, _, _ = _make_controller_with_registry()
        trader.strategy.return_value = MagicMock()

        before = set(ctrl._flatten_stop_pending)
        ctrl.flatten_stop_strategy("mom")

        assert "mom" in ctrl._flatten_stop_pending
        assert "mom" not in before
        entry = ctrl._flatten_stop_pending["mom"]
        assert entry["strategy_ids"] == ["Alpha-000", "Alpha-001"]
        assert isinstance(entry["start_ts"], float)

    def test_flatten_stop_publishes_flattening_ack(self):
        ctrl, trader, _, _, publish_ack, _ = _make_controller_with_registry()
        trader.strategy.return_value = MagicMock()

        ctrl.flatten_stop_strategy("mom")

        publish_ack.assert_called_once_with(
            "commands_ack",
            {
                "cmd": "flatten_stop_strategy", "name": "mom",
                "status": "flattening",
            },
        )

    def test_flatten_stop_marks_registry_flattening(self):
        ctrl, trader, _, _, _, registry = _make_controller_with_registry()
        trader.strategy.return_value = MagicMock()
        ctrl.flatten_stop_strategy("mom")
        registry.mark_flattening.assert_called_once_with("mom")

    def test_flatten_stop_resumes_paused_members_first(self):
        """A paused strategy must be resumed before market_exit is called."""
        ctrl, trader, msgbus, _, _, registry = _make_controller_with_registry(
            registry_state="paused",
        )
        trader.strategy.return_value = MagicMock()
        ctrl._paused_strategies.update({"Alpha-000", "Alpha-001"})

        ctrl.flatten_stop_strategy("mom")

        # Both members were resumed before flatten fired.
        for sid in ("Alpha-000", "Alpha-001"):
            msgbus.publish.assert_any_call(
                f"{LIFECYCLE_RESUME}.{sid}", "resume",
            )
        assert ctrl._paused_strategies == set()

    def test_flatten_stop_swallows_market_exit_errors(self):
        ctrl, trader, _, log, _, _ = _make_controller_with_registry()
        bad = MagicMock()
        bad.market_exit.side_effect = RuntimeError("venue rejected")
        trader.strategy.return_value = bad

        # Must not raise — errors are logged and the pending record is still
        # enqueued so the completion checker can run cleanup.
        ctrl.flatten_stop_strategy("mom")
        log.error.assert_called()
        assert "mom" in ctrl._flatten_stop_pending

    def test_flatten_stop_raises_without_registry(self):
        ctrl, _, _, _, _, _ = _make_controller()
        with pytest.raises(ValueError, match="not initialized"):
            ctrl.flatten_stop_strategy("mom")

    def test_flatten_stop_raises_for_unknown_strategy(self):
        ctrl, _, _, _, _, _ = _make_controller()
        registry = MagicMock()
        registry.get.return_value = None
        ctrl._registry = registry
        with pytest.raises(ValueError, match="not found"):
            ctrl.flatten_stop_strategy("ghost")

    def test_flatten_stop_raises_when_state_wrong(self):
        ctrl, _, _, _, _, _ = _make_controller_with_registry(
            registry_state="available",
        )
        with pytest.raises(ValueError, match="cannot flatten-stop"):
            ctrl.flatten_stop_strategy("mom")


class TestCheckFlattenStopCompletion:
    """Pending flatten-stop completion: removes strategies on flat, times out at 60s."""

    def _setup_pending(self, ctrl, trader, start_ts, strategy_ids):
        """Seed a pending flatten-stop and the trader.strategy_ids() set."""
        ctrl._flatten_stop_pending["mom"] = {
            "start_ts": start_ts,
            "strategy_ids": list(strategy_ids),
        }
        trader.strategy_ids.return_value = [StrategyId(s) for s in strategy_ids]

    def test_all_positions_flat_removes_strategies_and_cleans_up(self):
        import time as _t
        ctrl, trader, _, _, _, registry = _make_controller_with_registry()
        ctrl._paused_strategies.update({"Alpha-000", "Alpha-001"})
        self._setup_pending(ctrl, trader, _t.time(), ["Alpha-000", "Alpha-001"])
        trader.cache.positions_open.return_value = []

        ctrl.check_flatten_stop_completion()

        # remove_strategy called for each id.
        remove_args = [
            c[0][0] for c in trader.remove_strategy.call_args_list
        ]
        assert remove_args == [
            StrategyId("Alpha-000"), StrategyId("Alpha-001"),
        ]
        # Registry transitioned to stopped.
        registry.mark_stopped.assert_called_once_with("mom")
        # Pending entry cleared.
        assert "mom" not in ctrl._flatten_stop_pending
        # Paused bookkeeping cleared for this bundle.
        assert ctrl._paused_strategies == set()

    def test_all_positions_flat_publishes_ok_ack(self):
        import time as _t
        ctrl, trader, _, _, publish_ack, _ = _make_controller_with_registry()
        self._setup_pending(ctrl, trader, _t.time(), ["Alpha-000"])
        trader.cache.positions_open.return_value = []

        ctrl.check_flatten_stop_completion()

        publish_ack.assert_called_once_with(
            "commands_ack",
            {"cmd": "flatten_stop_strategy", "name": "mom", "status": "ok"},
        )

    def test_open_positions_before_timeout_is_noop(self):
        import time as _t
        ctrl, trader, _, _, publish_ack, registry = _make_controller_with_registry()
        self._setup_pending(ctrl, trader, _t.time(), ["Alpha-000"])
        trader.cache.positions_open.return_value = [MagicMock()]  # still open

        ctrl.check_flatten_stop_completion()

        # No strategy removals, no registry transition, no ack yet.
        trader.remove_strategy.assert_not_called()
        registry.mark_stopped.assert_not_called()
        publish_ack.assert_not_called()
        # Pending entry stays.
        assert "mom" in ctrl._flatten_stop_pending

    def test_timeout_forces_removal_and_publishes_timeout_ack(self):
        import time as _t
        ctrl, trader, _, log, publish_ack, registry = _make_controller_with_registry()
        # start_ts 61s in the past → elapsed > 60.
        self._setup_pending(
            ctrl, trader, _t.time() - 61, ["Alpha-000", "Alpha-001"],
        )
        ctrl._paused_strategies.update({"Alpha-000", "Alpha-001"})
        trader.cache.positions_open.return_value = [MagicMock()]  # still open

        ctrl.check_flatten_stop_completion()

        # Strategies removed despite not flat.
        assert trader.remove_strategy.call_count == 2
        # Paused set cleared.
        assert ctrl._paused_strategies == set()
        registry.mark_stopped.assert_called_once_with("mom")
        assert "mom" not in ctrl._flatten_stop_pending
        publish_ack.assert_called_once()
        ack_args = publish_ack.call_args[0][1]
        assert ack_args["status"] == "timeout"
        assert ack_args["cmd"] == "flatten_stop_strategy"
        assert ack_args["name"] == "mom"
        log.critical.assert_called()

    def test_positions_open_error_before_timeout_keeps_pending(self):
        import time as _t
        ctrl, trader, _, _, publish_ack, _ = _make_controller_with_registry()
        self._setup_pending(ctrl, trader, _t.time(), ["Alpha-000"])
        trader.cache.positions_open.side_effect = RuntimeError("cache gone")

        ctrl.check_flatten_stop_completion()

        # Error treated as 'not flat' — pending remains, no ack.
        assert "mom" in ctrl._flatten_stop_pending
        publish_ack.assert_not_called()

    def test_no_pending_entries_is_noop(self):
        ctrl, trader, _, _, publish_ack, _ = _make_controller_with_registry()
        # Nothing in _flatten_stop_pending.
        ctrl.check_flatten_stop_completion()
        trader.remove_strategy.assert_not_called()
        publish_ack.assert_not_called()

    def test_remove_strategy_errors_are_swallowed(self):
        import time as _t
        ctrl, trader, _, log, _, _ = _make_controller_with_registry()
        self._setup_pending(ctrl, trader, _t.time(), ["Alpha-000"])
        trader.cache.positions_open.return_value = []
        trader.remove_strategy.side_effect = RuntimeError("already gone")

        # Must not raise even if remove_strategy fails.
        ctrl.check_flatten_stop_completion()
        log.error.assert_called()


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------


class TestCancelOrder:
    """cancel_order() routes through the owning strategy via the cache."""

    def _make_order(self, *, is_closed=False, strategy_id_str="Alpha-000"):
        order = MagicMock()
        order.is_closed = is_closed
        order.strategy_id = StrategyId(strategy_id_str)
        order.status = "FILLED"
        return order

    def test_order_not_in_cache_publishes_not_found(self):
        ctrl, trader, _, _, _, publish_ack = _make_controller()
        trader.cache.order.return_value = None

        ctrl.cancel_order("O-1")

        publish_ack.assert_called_once_with(
            "commands_ack",
            {"cmd": "cancel_order", "client_order_id": "O-1", "status": "not_found"},
        )

    def test_closed_order_publishes_already_closed(self):
        ctrl, trader, _, _, _, publish_ack = _make_controller()
        trader.cache.order.return_value = self._make_order(is_closed=True)

        ctrl.cancel_order("O-1")

        publish_ack.assert_called_once_with(
            "commands_ack",
            {
                "cmd": "cancel_order",
                "client_order_id": "O-1",
                "status": "already_closed",
            },
        )

    def test_missing_owner_strategy_publishes_strategy_not_found(self):
        ctrl, trader, _, _, _, publish_ack = _make_controller()
        trader.cache.order.return_value = self._make_order()
        trader.strategy.return_value = None

        ctrl.cancel_order("O-1")

        publish_ack.assert_called_once_with(
            "commands_ack",
            {
                "cmd": "cancel_order",
                "client_order_id": "O-1",
                "status": "strategy_not_found",
            },
        )

    def test_happy_path_delegates_to_strategy_and_acks_submitted(self):
        ctrl, trader, _, _, _, publish_ack = _make_controller()
        order = self._make_order()
        strategy = MagicMock()
        trader.cache.order.return_value = order
        trader.strategy.return_value = strategy

        ctrl.cancel_order("O-1")

        strategy.cancel_order.assert_called_once_with(order)
        publish_ack.assert_called_once_with(
            "commands_ack",
            {
                "cmd": "cancel_order",
                "client_order_id": "O-1",
                "status": "submitted",
            },
        )


# ---------------------------------------------------------------------------
# start_strategy — happy + rollback paths
# ---------------------------------------------------------------------------


def _install_start_strategy_mocks(
    monkeypatch,
    *,
    strategies=None,
    actors=None,
    load_strategy_bundle=None,
    raise_on=None,  # key in {"load", "create_strategies", "create_actors"}
):
    """Patch the three imports that LifecycleController.start_strategy uses.

    Returns the three mocks so the test can assert call args / side effects.
    """
    import tinohelm.portfolio.config as pcfg
    import tinohelm.strategy.loader as loader

    cfg = load_strategy_bundle or MagicMock()

    def _load(name, strategies_dir=None):
        if raise_on == "load":
            raise RuntimeError("bundle load failed")
        return cfg

    def _create_strategies(cfg, order_id_tag):
        if raise_on == "create_strategies":
            raise RuntimeError("create_strategies failed")
        return strategies or []

    def _create_actors(cfg, strategy_name, strategy_tag_prefix):
        if raise_on == "create_actors":
            raise RuntimeError("create_actors failed")
        return actors or []

    monkeypatch.setattr(pcfg, "load_strategy_bundle", _load)
    monkeypatch.setattr(loader, "create_strategies", _create_strategies)
    monkeypatch.setattr(loader, "create_actors", _create_actors)


def _make_registry_for_start(name="mom", *, state="available", prefix="m"):
    registry = MagicMock()
    registry.get.return_value = MagicMock(
        state=state, strategy_ids=[], order_id_tag_prefix=prefix,
    )
    registry.allocate_tags.return_value = ["m000"]
    return registry


def _make_fake_strategy(sid_str):
    strat = MagicMock()
    strat.id = StrategyId(sid_str)
    return strat


class TestStartStrategyHappyPath:
    """Register → allocate → add → start → mark_running."""

    def test_happy_path_adds_strategies_actors_and_marks_running(
        self, monkeypatch,
    ):
        ctrl, trader, _, _, _, publish_ack = _make_controller(strategy_ids=[])
        registry = _make_registry_for_start()
        ctrl._registry = registry

        strat = _make_fake_strategy("MomStrat-m000")
        actor = MagicMock()
        _install_start_strategy_mocks(
            monkeypatch, strategies=[strat], actors=[actor],
        )

        ctrl.start_strategy("mom")

        registry.mark_starting.assert_called_once_with("mom")
        registry.allocate_tags.assert_called_once_with("mom", 1, set())
        trader.add_strategy.assert_called_once_with(strat)
        trader.add_actor.assert_called_once_with(actor)
        trader.start_strategy.assert_called_once_with(strat.id)
        registry.mark_running.assert_called_once_with(
            "mom", ["MomStrat-m000"],
        )

    def test_happy_path_publishes_ok_ack_with_strategy_ids(self, monkeypatch):
        ctrl, _, _, _, _, publish_ack = _make_controller(strategy_ids=[])
        ctrl._registry = _make_registry_for_start()

        strat = _make_fake_strategy("MomStrat-m000")
        _install_start_strategy_mocks(monkeypatch, strategies=[strat])

        ctrl.start_strategy("mom")

        publish_ack.assert_called_once_with(
            "commands_ack",
            {
                "cmd": "start_strategy",
                "name": "mom",
                "status": "ok",
                "strategy_ids": ["MomStrat-m000"],
            },
        )


class TestStartStrategyPreconditions:
    """Guards that fail fast before any side effects."""

    def test_raises_without_registry(self):
        ctrl, _, _, _, _, _ = _make_controller()
        with pytest.raises(ValueError, match="not initialized"):
            ctrl.start_strategy("mom")

    def test_raises_when_unknown_strategy(self):
        ctrl, _, _, _, _, _ = _make_controller()
        registry = MagicMock()
        registry.get.return_value = None
        ctrl._registry = registry
        with pytest.raises(ValueError, match="not found"):
            ctrl.start_strategy("mom")

    def test_raises_when_not_available(self):
        ctrl, _, _, _, _, _ = _make_controller()
        ctrl._registry = _make_registry_for_start(state="running")
        with pytest.raises(ValueError, match="not available"):
            ctrl.start_strategy("mom")


class TestStartStrategyRollback:
    """Every failure mode after mark_starting must publish an error ack and
    leave the registry in a clean 'available' state (mark_stopped)."""

    def test_bundle_load_failure_marks_stopped_and_publishes_error(
        self, monkeypatch,
    ):
        ctrl, _, _, _, _, publish_ack = _make_controller(strategy_ids=[])
        registry = _make_registry_for_start()
        ctrl._registry = registry
        _install_start_strategy_mocks(monkeypatch, raise_on="load")

        ctrl.start_strategy("mom")

        registry.mark_starting.assert_called_once_with("mom")
        registry.mark_stopped.assert_called_once_with("mom")
        call_args = publish_ack.call_args[0][1]
        assert call_args["status"] == "error"
        assert "bundle load failed" in call_args["reason"]

    def test_add_strategy_failure_rolls_back_added_entities(self, monkeypatch):
        ctrl, trader, _, _, _, publish_ack = _make_controller(strategy_ids=[])
        registry = _make_registry_for_start()
        ctrl._registry = registry

        good = _make_fake_strategy("MomStrat-m000")
        bad = _make_fake_strategy("MomStrat-m001")
        # First add succeeds, second raises.
        trader.add_strategy.side_effect = [None, RuntimeError("add failed")]
        _install_start_strategy_mocks(
            monkeypatch, strategies=[good, bad], actors=[],
        )

        ctrl.start_strategy("mom")

        # First successful add must be rolled back via remove_strategy.
        trader.remove_strategy.assert_called_once_with(good.id)
        registry.mark_stopped.assert_called_once_with("mom")
        ack_args = publish_ack.call_args[0][1]
        assert ack_args["status"] == "error"

    def test_start_failure_rolls_back_strategies_and_actors(self, monkeypatch):
        ctrl, trader, _, _, _, publish_ack = _make_controller(strategy_ids=[])
        ctrl._registry = _make_registry_for_start()

        strat = _make_fake_strategy("MomStrat-m000")
        actor = MagicMock()
        actor.id = "actor-1"
        _install_start_strategy_mocks(
            monkeypatch, strategies=[strat], actors=[actor],
        )
        trader.start_strategy.side_effect = RuntimeError("start failed")

        ctrl.start_strategy("mom")

        trader.remove_actor.assert_called_once_with(actor.id)
        trader.remove_strategy.assert_called_once_with(strat.id)
        ack_args = publish_ack.call_args[0][1]
        assert ack_args["status"] == "error"
        assert "start failed" in ack_args["reason"]

    def test_id_collision_is_detected_before_add(self, monkeypatch):
        """If the generated strategy.id already exists on the trader, abort
        with a collision error — do NOT call add_strategy."""
        ctrl, trader, _, _, _, publish_ack = _make_controller(
            strategy_ids=["MomStrat-m000"],  # same id lives on the trader
        )
        ctrl._registry = _make_registry_for_start()

        strat = _make_fake_strategy("MomStrat-m000")
        _install_start_strategy_mocks(monkeypatch, strategies=[strat])

        ctrl.start_strategy("mom")

        trader.add_strategy.assert_not_called()
        ack_args = publish_ack.call_args[0][1]
        assert ack_args["status"] == "error"
        assert "collision" in ack_args["reason"]

    def test_rollback_is_idempotent_if_remove_raises(self, monkeypatch):
        """remove_strategy / remove_actor failures during rollback are
        swallowed — we still publish the outer error ack."""
        ctrl, trader, _, _, _, publish_ack = _make_controller(strategy_ids=[])
        ctrl._registry = _make_registry_for_start()

        strat = _make_fake_strategy("MomStrat-m000")
        actor = MagicMock()
        actor.id = "actor-1"
        _install_start_strategy_mocks(
            monkeypatch, strategies=[strat], actors=[actor],
        )
        trader.start_strategy.side_effect = RuntimeError("start failed")
        trader.remove_actor.side_effect = RuntimeError("actor gone")
        trader.remove_strategy.side_effect = RuntimeError("strat gone")

        # Must not raise — rollback best-effort.
        ctrl.start_strategy("mom")
        ack_args = publish_ack.call_args[0][1]
        assert ack_args["status"] == "error"
