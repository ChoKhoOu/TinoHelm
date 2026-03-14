"""Tests for BridgeActor command handling logic.

Since NT Actor is a Cython extension class that can't be easily instantiated
in isolation, we test the command-handling logic using a lightweight stand-in
that replicates the same attributes and methods.
"""
from __future__ import annotations

import json
import os
import signal
from unittest.mock import MagicMock, patch

import pytest


class _BridgeActorStub:
    """Lightweight stand-in for BridgeActor that replicates command-handling logic.

    Copies the exact same _handle_command / on_event logic from bridge_actor.py
    but without requiring the NT Actor Cython base class.
    """

    def __init__(self, node_type: str = "sandbox"):
        self._node_type = node_type
        self._flatten_requested = False
        self._running = True

        # Mock NT and Redis surfaces
        self.log = MagicMock()
        self.msgbus = MagicMock()

        # Track Redis publishes: list of (channel_suffix, payload_dict)
        self._published: list[tuple[str, dict]] = []

    # --- Mirrors BridgeActor._publish ---

    def _publish(self, channel_suffix: str, data: dict) -> None:
        self._published.append((channel_suffix, data))

    # --- Mirrors BridgeActor._handle_command ---

    def _handle_command(self, cmd: dict) -> None:
        action = cmd.get("cmd")
        self.log.info(f"Received command: {action}")

        if action == "pause":
            self._publish("commands_ack", {"cmd": "pause", "status": "received"})

        elif action == "flatten":
            self.log.warning("FLATTEN command received - scheduling position exit")
            self._flatten_requested = True
            self._publish("commands_ack", {"cmd": "flatten", "status": "scheduled"})

        elif action == "shutdown":
            self.log.warning("SHUTDOWN command received - initiating node shutdown")
            self._publish("commands_ack", {"cmd": "shutdown", "status": "received"})
            os.kill(os.getpid(), signal.SIGTERM)

        elif action == "stop":
            self.log.info("STOP command received")
            self._publish("commands_ack", {"cmd": "stop", "status": "received"})

    # --- Mirrors BridgeActor.on_event (timer callback portion only) ---

    def _dispatch_flatten_if_requested(self) -> None:
        """Simulates the bridge_cmd_dispatch timer callback in on_event()."""
        if self._flatten_requested:
            self._flatten_requested = False
            self.log.warning("Executing flatten — closing all positions via msgbus")
            self.msgbus.publish("risk.flatten", "flatten_all")


# ---------------------------------------------------------------------------
# Shutdown command tests
# ---------------------------------------------------------------------------

class TestShutdownCommand:
    """Test the 'shutdown' command path."""

    def test_shutdown_command_sends_sigterm(self):
        stub = _BridgeActorStub()
        with patch("os.kill") as mock_kill:
            stub._handle_command({"cmd": "shutdown"})
        mock_kill.assert_called_once_with(os.getpid(), signal.SIGTERM)

    def test_shutdown_command_publishes_ack_before_sigterm(self):
        """Ack must be published before os.kill() is called."""
        stub = _BridgeActorStub()
        publish_calls: list[tuple[str, dict]] = []
        kill_called_after_publish = False

        original_publish = stub._publish

        def tracking_publish(suffix, data):
            publish_calls.append((suffix, data))
            original_publish(suffix, data)

        stub._publish = tracking_publish

        with patch("os.kill") as mock_kill:
            def assert_ack_published(*args, **kwargs):
                # By the time os.kill is called, the ack must already be recorded
                assert len(publish_calls) == 1
                assert publish_calls[0] == (
                    "commands_ack",
                    {"cmd": "shutdown", "status": "received"},
                )

            mock_kill.side_effect = assert_ack_published
            stub._handle_command({"cmd": "shutdown"})

        mock_kill.assert_called_once()
        # Confirm ack is present in published list
        assert any(
            suffix == "commands_ack" and data.get("cmd") == "shutdown"
            for suffix, data in stub._published
        )


# ---------------------------------------------------------------------------
# Flatten command tests
# ---------------------------------------------------------------------------

class TestFlattenCommand:
    """Test the 'flatten' command path."""

    def test_flatten_command_sets_flag(self):
        stub = _BridgeActorStub()
        assert stub._flatten_requested is False
        stub._handle_command({"cmd": "flatten"})
        assert stub._flatten_requested is True

    def test_flatten_command_publishes_ack_with_scheduled_status(self):
        stub = _BridgeActorStub()
        stub._handle_command({"cmd": "flatten"})

        ack_entries = [
            data for suffix, data in stub._published if suffix == "commands_ack"
        ]
        assert len(ack_entries) == 1
        assert ack_entries[0] == {"cmd": "flatten", "status": "scheduled"}


# ---------------------------------------------------------------------------
# Timer dispatch tests (on_event / bridge_cmd_dispatch)
# ---------------------------------------------------------------------------

class TestFlattenDispatch:
    """Test the timer callback that dispatches flatten to the NT event loop."""

    def test_flatten_dispatch_publishes_to_risk_flatten(self):
        stub = _BridgeActorStub()
        stub._flatten_requested = True

        stub._dispatch_flatten_if_requested()

        stub.msgbus.publish.assert_called_once_with("risk.flatten", "flatten_all")

    def test_flatten_dispatch_resets_flag_after_publish(self):
        stub = _BridgeActorStub()
        stub._flatten_requested = True

        stub._dispatch_flatten_if_requested()

        assert stub._flatten_requested is False

    def test_flatten_dispatch_noop_when_flag_is_false(self):
        stub = _BridgeActorStub()
        stub._flatten_requested = False

        stub._dispatch_flatten_if_requested()

        stub.msgbus.publish.assert_not_called()
