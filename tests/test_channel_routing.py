"""Tests for the dual-channel Discord routing.

Sandbox and live deployments share one notifier pod but post to two
different Discord channels. The router decides which channel id to use
based on the strategy registry built from announce + fallback-scan.

Invariants:
  1. Live strategies → live channel
  2. Sandbox strategies → sandbox channel
  3. Unknown strategies (race window before announce arrives, or stream
     wiped) → sandbox channel — safer default since a stray sandbox
     event in the live channel is just noise, but a stray live event in
     the sandbox channel may be mistaken for a paper-fill and ignored.
"""

from __future__ import annotations

from tinohelm.notifier.runner import route_channel

SANDBOX = 11111
LIVE = 22222


def test_routes_live_strategy_to_live_channel() -> None:
    registry = {"BAR-001": "live"}
    assert route_channel("BAR-001", registry, sandbox=SANDBOX, live=LIVE) == LIVE


def test_routes_sandbox_strategy_to_sandbox_channel() -> None:
    registry = {"FOO-001": "sandbox"}
    assert route_channel("FOO-001", registry, sandbox=SANDBOX, live=LIVE) == SANDBOX


def test_unknown_strategy_defaults_to_sandbox_channel() -> None:
    registry: dict[str, str] = {}
    assert route_channel("GHOST-001", registry, sandbox=SANDBOX, live=LIVE) == SANDBOX


def test_unrecognized_mode_defaults_to_sandbox_channel() -> None:
    """Defensive: a future ``mode = "paper"`` etc. should not crash routing."""

    registry = {"WEIRD-001": "paper"}
    assert route_channel("WEIRD-001", registry, sandbox=SANDBOX, live=LIVE) == SANDBOX
