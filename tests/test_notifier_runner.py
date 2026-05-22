"""Behavioral tests for tinohelm.notifier.runner internals.

These avoid spinning up a real Discord client / TradingNode. We test the
small, pure helpers — schedule parsing, forwarder graceful-degrade — that
would silently break the daily summary or event delivery if regressed.
"""

from __future__ import annotations

from datetime import UTC
from datetime import time as dtime

from tinohelm.notifier.runner import _parse_hh_mm


def test_parse_hh_mm_returns_utc_time_object() -> None:
    """The notifier schedules its daily summary off ``daily_summary_utc``.

    The string format is ``"HH:MM"`` (UTC). If the parser silently mis-parses,
    the summary would either fire at the wrong hour or skip the day entirely.
    """

    parsed = _parse_hh_mm("14:00")
    assert parsed == dtime(14, 0, tzinfo=UTC)


def test_parse_hh_mm_rejects_invalid_format() -> None:
    """Misconfiguration (e.g. ``"14:00:00"``) should crash the pod at boot.

    Boot-time crashes are loud and easy to catch. Silently downgrading would
    schedule the summary at midnight, hiding the misconfig.
    """

    import pytest

    with pytest.raises((ValueError, IndexError)):
        _parse_hh_mm("14:00:00")
    with pytest.raises((ValueError, IndexError)):
        _parse_hh_mm("not-a-time")


def test_forwarder_enqueue_swallows_closed_loop() -> None:
    """During shutdown the asyncio loop closes before NT's actor stops.

    Any in-flight ``msgbus`` callback that tries to forward to Discord on the
    way down would raise ``RuntimeError: Event loop is closed`` and crash the
    whole shutdown if not handled. Verify ``enqueue`` is defensive: it logs
    and drops the event, never propagates.
    """

    import asyncio

    from tinohelm.notifier.handlers import envelope_for
    from tinohelm.notifier.runner import DiscordForwarder

    loop = asyncio.new_event_loop()
    loop.close()

    forwarder = DiscordForwarder(loop=loop, client=None)
    env = envelope_for("events.order.FOO", b"{}")

    # Should not raise
    forwarder.enqueue(env, channel_id=123)
