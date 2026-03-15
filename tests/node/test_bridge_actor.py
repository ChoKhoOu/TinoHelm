"""Tests for BridgeActor command handling logic.

Since NT Actor is a Cython extension class that can't be easily instantiated
in isolation, we test the command-handling logic using a lightweight stand-in
that replicates the same attributes and methods.

Updated for the command-queue pattern: _handle_command() appends to
_pending_commands deque, _drain_pending_commands() dispatches on NT thread.
"""
from __future__ import annotations

import collections
import json
import os
import signal
from unittest.mock import MagicMock, patch, call

import pytest


class _BridgeActorStub:
    """Lightweight stand-in for BridgeActor that replicates command-handling logic.

    Mirrors the deque-based command queue from bridge_actor.py.
    """

    def __init__(self, node_type: str = "sandbox"):
        self._node_type = node_type
        self._running = True
        self._pending_commands: collections.deque = collections.deque()

        # Mock NT and Redis surfaces
        self.log = MagicMock()
        self.msgbus = MagicMock()

        # LifecycleController mock (None = not initialized)
        self._lifecycle = None

        # Track Redis publishes: list of (channel_suffix, payload_dict)
        self._published: list[tuple[str, dict]] = []

    def _publish(self, channel_suffix: str, data: dict) -> None:
        self._published.append((channel_suffix, data))

    def _handle_command(self, cmd: dict) -> None:
        """Mirrors BridgeActor._handle_command — enqueues for NT thread dispatch."""
        action = cmd.get("cmd")
        self.log.info(f"Received command: {action}")
        self._pending_commands.append(cmd)

    def _drain_pending_commands(self) -> None:
        """Mirrors BridgeActor._drain_pending_commands."""
        while self._pending_commands:
            cmd = self._pending_commands.popleft()
            action = cmd.get("cmd")
            strategy_id = cmd.get("strategy_id")

            if self._lifecycle is None:
                self.log.warning(f"Command '{action}' ignored: LifecycleController not initialized")
                self._publish("commands_ack", {"cmd": action, "status": "error", "reason": "no_lifecycle"})
                continue

            try:
                if action == "pause":
                    self._lifecycle.pause_strategy(strategy_id or "")
                elif action == "resume":
                    self._lifecycle.resume_strategy(strategy_id or "")
                elif action == "flatten":
                    self._lifecycle.flatten(strategy_id)
                elif action == "halt":
                    self._lifecycle.halt()
                elif action == "unhalt":
                    self._lifecycle.unhalt()
                elif action == "shutdown":
                    self._lifecycle.shutdown()
                else:
                    self.log.warning(f"Unknown command: {action}")
            except ValueError as e:
                self.log.error(f"Command '{action}' failed: {e}")
                self._publish("commands_ack", {"cmd": action, "status": "error", "reason": str(e)})
            except Exception as e:
                self.log.error(f"Command '{action}' error: {e}")
                self._publish("commands_ack", {"cmd": action, "status": "error", "reason": str(e)})


# ---------------------------------------------------------------------------
# Command enqueue tests
# ---------------------------------------------------------------------------

class TestCommandEnqueue:
    """Test that _handle_command appends to deque."""

    def test_handle_command_enqueues(self):
        stub = _BridgeActorStub()
        stub._handle_command({"cmd": "pause", "strategy_id": "MyStrategy-000"})
        assert len(stub._pending_commands) == 1
        assert stub._pending_commands[0] == {"cmd": "pause", "strategy_id": "MyStrategy-000"}

    def test_multiple_commands_enqueued_in_order(self):
        stub = _BridgeActorStub()
        stub._handle_command({"cmd": "pause", "strategy_id": "S-000"})
        stub._handle_command({"cmd": "flatten"})
        stub._handle_command({"cmd": "halt"})
        assert len(stub._pending_commands) == 3
        assert [c["cmd"] for c in stub._pending_commands] == ["pause", "flatten", "halt"]


# ---------------------------------------------------------------------------
# Command dispatch tests (with LifecycleController)
# ---------------------------------------------------------------------------

class TestCommandDispatch:
    """Test _drain_pending_commands delegates to LifecycleController."""

    def _make_stub_with_lifecycle(self):
        stub = _BridgeActorStub()
        stub._lifecycle = MagicMock()
        return stub

    def test_pause_dispatches_to_lifecycle(self):
        stub = self._make_stub_with_lifecycle()
        stub._handle_command({"cmd": "pause", "strategy_id": "MyStrategy-000"})
        stub._drain_pending_commands()
        stub._lifecycle.pause_strategy.assert_called_once_with("MyStrategy-000")

    def test_resume_dispatches_to_lifecycle(self):
        stub = self._make_stub_with_lifecycle()
        stub._handle_command({"cmd": "resume", "strategy_id": "MyStrategy-000"})
        stub._drain_pending_commands()
        stub._lifecycle.resume_strategy.assert_called_once_with("MyStrategy-000")

    def test_flatten_dispatches_to_lifecycle(self):
        stub = self._make_stub_with_lifecycle()
        stub._handle_command({"cmd": "flatten"})
        stub._drain_pending_commands()
        stub._lifecycle.flatten.assert_called_once_with(None)

    def test_flatten_single_strategy(self):
        stub = self._make_stub_with_lifecycle()
        stub._handle_command({"cmd": "flatten", "strategy_id": "S-001"})
        stub._drain_pending_commands()
        stub._lifecycle.flatten.assert_called_once_with("S-001")

    def test_halt_dispatches_to_lifecycle(self):
        stub = self._make_stub_with_lifecycle()
        stub._handle_command({"cmd": "halt"})
        stub._drain_pending_commands()
        stub._lifecycle.halt.assert_called_once()

    def test_unhalt_dispatches_to_lifecycle(self):
        stub = self._make_stub_with_lifecycle()
        stub._handle_command({"cmd": "unhalt"})
        stub._drain_pending_commands()
        stub._lifecycle.unhalt.assert_called_once()

    def test_shutdown_dispatches_to_lifecycle(self):
        stub = self._make_stub_with_lifecycle()
        stub._handle_command({"cmd": "shutdown"})
        stub._drain_pending_commands()
        stub._lifecycle.shutdown.assert_called_once()

    def test_multiple_commands_dispatched_in_order(self):
        stub = self._make_stub_with_lifecycle()
        stub._handle_command({"cmd": "pause", "strategy_id": "S-000"})
        stub._handle_command({"cmd": "flatten"})
        stub._handle_command({"cmd": "halt"})

        stub._drain_pending_commands()

        # Verify call order
        expected_calls = [
            call.pause_strategy("S-000"),
            call.flatten(None),
            call.halt(),
        ]
        stub._lifecycle.assert_has_calls(expected_calls)

    def test_queue_drained_after_dispatch(self):
        stub = self._make_stub_with_lifecycle()
        stub._handle_command({"cmd": "halt"})
        stub._drain_pending_commands()
        assert len(stub._pending_commands) == 0


# ---------------------------------------------------------------------------
# No lifecycle fallback tests
# ---------------------------------------------------------------------------

class TestNoLifecycleFallback:
    """Test behavior when LifecycleController is not initialized."""

    def test_command_ignored_without_lifecycle(self):
        stub = _BridgeActorStub()
        assert stub._lifecycle is None
        stub._handle_command({"cmd": "halt"})
        stub._drain_pending_commands()

        # Should publish error ack
        assert any(
            suffix == "commands_ack" and data.get("status") == "error"
            for suffix, data in stub._published
        )

    def test_error_ack_includes_reason(self):
        stub = _BridgeActorStub()
        stub._handle_command({"cmd": "pause", "strategy_id": "S-000"})
        stub._drain_pending_commands()

        ack = next(
            data for suffix, data in stub._published
            if suffix == "commands_ack"
        )
        assert ack["reason"] == "no_lifecycle"


# ---------------------------------------------------------------------------
# ValueError handling tests
# ---------------------------------------------------------------------------

class TestValueErrorHandling:
    """Test that ValueError from LifecycleController is caught and reported."""

    def test_value_error_publishes_error_ack(self):
        stub = _BridgeActorStub()
        stub._lifecycle = MagicMock()
        stub._lifecycle.pause_strategy.side_effect = ValueError("Strategy 'bad' not found")

        stub._handle_command({"cmd": "pause", "strategy_id": "bad"})
        stub._drain_pending_commands()

        ack = next(
            data for suffix, data in stub._published
            if suffix == "commands_ack"
        )
        assert ack["status"] == "error"
        assert "not found" in ack["reason"]
