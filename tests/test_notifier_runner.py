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


def test_notifier_actor_routes_unscoped_topic_to_logging() -> None:
    """The actor's per-pattern handler must pass ``logging_channel_id`` to
    :func:`route_channel`. Without this wiring the new ``logging`` kwarg
    would default-trip and unscoped topics (events.system.*, etc.) would
    keep landing in sandbox.

    We exercise the public path: build the actor, fire a handler with a
    payload from an unscoped topic, and observe which channel id the
    forwarder was asked to use. The actor's interaction with NT's msgbus
    is the only thing we mock — that needs a running TradingNode.
    """

    from tinohelm.notifier.runner import (
        NotifierActor,
        NotifierActorConfig,
    )

    sent: list[int] = []

    class _FakeForwarder:
        def enqueue(self, env, *, channel_id: int) -> None:
            sent.append(channel_id)

    actor = NotifierActor(
        NotifierActorConfig(
            sandbox_channel_id=11,
            live_channel_id=22,
            logging_channel_id=33,
        ),
        forwarder=_FakeForwarder(),
        registry={"FOO-001": "live"},
    )

    # ``events.system.component_state_changed`` carries no strategy_id
    handler = actor._make_handler("events.system.component_state_changed")
    handler(b"{}")

    assert sent == [33]


def test_build_daily_summary_envelope_uses_logging_channel() -> None:
    """The daily summary is operational chatter, not trade flow. After the
    triple-channel split it goes to logging only — mirroring it to
    sandbox+live would defeat the whole point of the split.

    The envelope is constructed from the cache snapshot + a stream count;
    we drive the helper with simple stand-ins so this test stays a
    pure-function check (no asyncio sleep, no real cache).
    """

    from tinohelm.notifier.runner import build_daily_summary

    class _FakeCache:
        def positions_open(self) -> list:
            return [object(), object()]

        def positions_closed(self) -> list:
            return [object()]

        def orders_open(self) -> list:
            return []

    envelope, channel_id = build_daily_summary(
        cache=_FakeCache(),
        redis_stream_count=7,
        logging_channel_id=33,
    )

    assert channel_id == 33
    assert envelope.topic == "tinohelm.daily_summary"
    assert envelope.body == {
        "positions_open": 2,
        "positions_closed": 1,
        "orders_open": 0,
        "redis_streams_seen": 7,
    }


def test_build_daily_summary_handles_missing_cache() -> None:
    """The notifier may run without a [cache] section — in which case the
    summary should note the absence rather than crash. Same logging channel.
    """

    from tinohelm.notifier.runner import build_daily_summary

    envelope, channel_id = build_daily_summary(
        cache=None,
        redis_stream_count=0,
        logging_channel_id=33,
    )

    assert channel_id == 33
    assert envelope.topic == "tinohelm.daily_summary"
    assert "note" in envelope.body
    assert envelope.body["redis_streams_seen"] == 0


def test_notifier_actor_still_routes_scoped_events_to_strategy_channel() -> None:
    """Regression guard: live-strategy events must still reach the live
    channel, not get swept into logging by the new wiring.
    """

    from tinohelm.notifier.runner import (
        NotifierActor,
        NotifierActorConfig,
    )

    sent: list[int] = []

    class _FakeForwarder:
        def enqueue(self, env, *, channel_id: int) -> None:
            sent.append(channel_id)

    actor = NotifierActor(
        NotifierActorConfig(
            sandbox_channel_id=11,
            live_channel_id=22,
            logging_channel_id=33,
        ),
        forwarder=_FakeForwarder(),
        registry={"FOO-001": "live"},
    )

    # Topic carries strategy_id "FOO-001", which is registered as live.
    handler = actor._make_handler("events.order.FOO-001")
    handler(b"{}")

    assert sent == [22]
