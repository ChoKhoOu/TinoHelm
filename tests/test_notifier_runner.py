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
    """``events.system.*`` events have no ``strategy_id`` field in their body
    (they're cross-cutting: component lifecycle, not per-strategy). They must
    land in the logging channel so trade-flow channels stay clean.

    We exercise the public path: build the actor, fire a handler whose
    ``pattern`` is the wildcard string NT actually passes to subscribe(),
    feed it a serialized body with no ``strategy_id`` key, and assert the
    forwarder was asked to use the logging channel.
    """

    import json

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

    # ``events.system.*`` is the actual subscription pattern.
    # Component-state-changed bodies have no strategy_id.
    handler = actor._make_handler("events.system.*")
    handler(json.dumps({"component_id": "TraderId-FOO", "state": "STARTED"}).encode())

    assert sent == [33]


def test_notifier_actor_routes_signal_to_strategy_channel() -> None:
    """Regression for the bug Greptile flagged: ``data.Signal*`` events used
    to fall through to logging because the actor was reading the subscription
    pattern instead of the message body's ``strategy_id`` field.

    Signals are per-strategy by design (a strategy emits them via
    ``self.publish_signal()``); they must reach the live or sandbox channel
    just like order/position events.
    """

    import json

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

    handler = actor._make_handler("data.Signal*")
    handler(json.dumps({"strategy_id": "FOO-001", "name": "buy", "value": 1}).encode())

    assert sent == [22]  # FOO-001 is live → live channel


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


def test_notifier_actor_routes_order_event_to_live_channel() -> None:
    """An order event for a live strategy must reach the live channel.

    NT's msgbus passes the subscription pattern (``events.order.*``) into
    the handler closure, never the resolved topic. The strategy_id has to
    come from the serialized body, where NT writes ``"strategy_id"`` for
    every OrderEvent / PositionEvent / Signal.
    """

    import json

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

    handler = actor._make_handler("events.order.*")
    handler(
        json.dumps({"strategy_id": "FOO-001", "type": "OrderFilled"}).encode(),
    )

    assert sent == [22]


def test_notifier_actor_routes_account_event_to_logging() -> None:
    """``events.account.*`` is keyed by ``account_id``, not strategy_id —
    it's cross-cutting (one account funds many strategies). These belong in
    the logging channel.
    """

    import json

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

    handler = actor._make_handler("events.account.*")
    handler(json.dumps({"account_id": "BYBIT-001", "balances": []}).encode())

    assert sent == [33]
