"""Tests for slash-command channel isolation.

Once we split Discord into #sandbox and #live channels, we need a guard
so an operator can't accidentally ``/pause LIVE-001`` from #sandbox
(or vice versa) and stop a strategy in the wrong environment.

The rule, in plain English: the channel you're standing in must match
the strategy's mode. Unknowns are treated as sandbox (mirroring the
event-routing default) — operators can still pause-stop a not-yet-
announced sandbox-ish strategy from #sandbox without ceremony.
"""

from __future__ import annotations

import pytest

from tinohelm.notifier.runner import ChannelMismatch, validate_command_channel


def test_live_channel_can_command_live_strategy() -> None:
    validate_command_channel("LIVE-001", channel_mode="live", registry={"LIVE-001": "live"})


def test_sandbox_channel_can_command_sandbox_strategy() -> None:
    validate_command_channel(
        "SBX-001",
        channel_mode="sandbox",
        registry={"SBX-001": "sandbox"},
    )


def test_live_channel_rejects_sandbox_strategy() -> None:
    """The most dangerous misclick: pausing a paper strategy from the live
    channel. The error message must name both sides so the operator knows
    where to retry.
    """

    with pytest.raises(ChannelMismatch, match=r"FOO-001.*sandbox.*live"):
        validate_command_channel(
            "FOO-001",
            channel_mode="live",
            registry={"FOO-001": "sandbox"},
        )


def test_sandbox_channel_rejects_live_strategy() -> None:
    with pytest.raises(ChannelMismatch, match=r"BAR-001.*live.*sandbox"):
        validate_command_channel(
            "BAR-001",
            channel_mode="sandbox",
            registry={"BAR-001": "live"},
        )


def test_unknown_strategy_treated_as_sandbox() -> None:
    """Same default as event routing — keeps the rule consistent and
    avoids a confusing UX where ``/pause`` works but no events show up."""

    # From sandbox channel: OK
    validate_command_channel("GHOST-001", channel_mode="sandbox", registry={})
    # From live channel: rejected
    with pytest.raises(ChannelMismatch):
        validate_command_channel("GHOST-001", channel_mode="live", registry={})
