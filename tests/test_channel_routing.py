"""Tests for the triple-channel Discord routing.

Sandbox and live deployments share one notifier pod, plus a third
"logging" channel for cross-cutting noise (system/account events,
daily summary, future notifier-internal errors). The router decides
which channel id to use based on the strategy registry built from
announce + fallback-scan.

Invariants:
  1. Live strategies → live channel
  2. Sandbox strategies → sandbox channel
  3. Unknown strategies → sandbox (safer default; a stray live event
     mis-routed to live would be alarming, but a stray sandbox event
     in sandbox is harmless)
  4. Topics without a strategy scope (events.system.*, events.account.*,
     tinohelm.*) → logging channel — they're operational, not trade-flow
"""

from __future__ import annotations

from tinohelm.notifier.runner import route_channel

SANDBOX = 11111
LIVE = 22222
LOGGING = 33333


def test_routes_live_strategy_to_live_channel() -> None:
    registry = {"BAR-001": "live"}
    assert route_channel("BAR-001", registry, sandbox=SANDBOX, live=LIVE, logging_channel_id=LOGGING) == LIVE


def test_routes_sandbox_strategy_to_sandbox_channel() -> None:
    registry = {"FOO-001": "sandbox"}
    assert (
        route_channel("FOO-001", registry, sandbox=SANDBOX, live=LIVE, logging_channel_id=LOGGING) == SANDBOX
    )


def test_unknown_strategy_defaults_to_sandbox_channel() -> None:
    registry: dict[str, str] = {}
    assert (
        route_channel("GHOST-001", registry, sandbox=SANDBOX, live=LIVE, logging_channel_id=LOGGING) == SANDBOX
    )


def test_unrecognized_mode_defaults_to_sandbox_channel() -> None:
    """Defensive: a future ``mode = "paper"`` etc. should not crash routing."""

    registry = {"WEIRD-001": "paper"}
    assert (
        route_channel("WEIRD-001", registry, sandbox=SANDBOX, live=LIVE, logging_channel_id=LOGGING) == SANDBOX
    )


def test_unscoped_topic_routes_to_logging_channel() -> None:
    """Topics like ``events.system.*`` carry no strategy_id; the caller passes
    an empty string. They must NOT pollute the trade-flow channels — operators
    treat sandbox/live as "filled / not filled" feeds and want operational
    chatter (component state, account snapshots, daily summary) elsewhere.
    """

    registry: dict[str, str] = {"FOO-001": "live"}
    assert route_channel("", registry, sandbox=SANDBOX, live=LIVE, logging_channel_id=LOGGING) == LOGGING
