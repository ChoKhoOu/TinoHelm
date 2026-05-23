"""Behavioral tests for tinohelm.notifier.runner internals.

These avoid spinning up a real Discord client / TradingNode. We test the
small, pure helpers — schedule parsing, forwarder graceful-degrade — that
would silently break the daily summary or event delivery if regressed.
"""

from __future__ import annotations

import contextlib
from datetime import UTC
from datetime import time as dtime
from typing import Any

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


def test_detect_protocol_drift_warns_when_strategy_proto_differs() -> None:
    """A strategy pod that announces a tino_protocol_version different from
    what the notifier was built against indicates the wire format may be
    drifting. The notifier must surface this to the logging channel — a
    silent disagreement would mean events appear to flow but routing /
    control-stream payloads might have subtly diverged.

    Forward-compat is the goal: the notifier still processes events. It
    just emits a one-shot envelope so an operator notices and decides
    whether to upgrade the notifier.
    """

    from tinohelm.notifier.runner import detect_protocol_drift

    announces = [
        ("FOO-001", "sandbox", "1.226.0", "2"),  # newer proto than us
        ("BAR-001", "live", "1.226.0", "1"),  # matches
    ]

    envelopes = detect_protocol_drift(
        announces,
        expected_proto="1",
        expected_nt_version="1.226.0",
    )

    assert len(envelopes) == 1
    env = envelopes[0]
    assert env.topic == "tinohelm.protocol_mismatch"
    assert env.body["strategy_id"] == "FOO-001"
    assert env.body["pod_proto"] == "2"
    assert env.body["notifier_proto"] == "1"


def test_detect_protocol_drift_warns_on_nt_version_skew() -> None:
    """Different NT versions carry a real risk of msgpack schema changes
    silently mis-decoding fields. Surface this even when proto matches.
    """

    from tinohelm.notifier.runner import detect_protocol_drift

    envelopes = detect_protocol_drift(
        [("FOO-001", "live", "1.227.0", "1")],
        expected_proto="1",
        expected_nt_version="1.226.0",
    )

    assert len(envelopes) == 1
    assert envelopes[0].body["pod_nt_version"] == "1.227.0"
    assert envelopes[0].body["notifier_nt_version"] == "1.226.0"


def test_detect_protocol_drift_silent_when_versions_match() -> None:
    """No spam when everything agrees — the channel is read-only and we don't
    want a noisy warning every minute for normal operation.
    """

    from tinohelm.notifier.runner import detect_protocol_drift

    envelopes = detect_protocol_drift(
        [("FOO-001", "live", "1.226.0", "1")],
        expected_proto="1",
        expected_nt_version="1.226.0",
    )

    assert envelopes == []


def test_autodiscover_emits_drift_envelope_to_logging_channel() -> None:
    """The discovery loop must hand drift envelopes to the forwarder pointed
    at the logging channel id — wiring the pure detection function through to
    Discord without that, the warning never reaches an operator.
    """

    import asyncio

    import fakeredis

    from tinohelm.notifier.runner import _autodiscover_loop

    server = fakeredis.FakeServer()
    rc = fakeredis.FakeRedis(server=server, decode_responses=False)
    rc.xadd(
        "tinohelm:announce",
        {
            "strategy_id": "FOO-001",
            "mode": "live",
            "trader_id": "TINO-001",
            "ts": "1",
            "nt_version": "1.227.0",  # drift vs notifier
            "tino_protocol_version": "1",
        },
    )

    sent: list[tuple[Any, int]] = []

    class _FakeForwarder:
        def enqueue(self, env, *, channel_id: int) -> None:
            sent.append((env, channel_id))

    registry: dict[str, str] = {}

    async def _run_once():
        task = asyncio.create_task(
            _autodiscover_loop(
                rc,
                registry,
                "0",
                forwarder=_FakeForwarder(),
                logging_channel_id=33,
                expected_proto="1",
                expected_nt_version="1.226.0",
                fallback_interval_s=999.0,  # don't run fallback path
            ),
        )
        # one tick — XREAD returns immediately, drift envelope queued, then
        # we cancel before the next iteration so the test stays bounded.
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run_once())

    assert any(
        env.topic == "tinohelm.protocol_mismatch" and channel_id == 33
        for env, channel_id in sent
    )


def test_detect_protocol_drift_treats_pre_versioned_pods_as_skew() -> None:
    """A pod that came up before the version-handshake change writes empty
    strings for the version fields. Treat that as a drift signal too — those
    pods literally don't know about TINO_PROTOCOL_VERSION yet.
    """

    from tinohelm.notifier.runner import detect_protocol_drift

    envelopes = detect_protocol_drift(
        [("FOO-001", "live", "", "")],
        expected_proto="1",
        expected_nt_version="1.226.0",
    )

    assert len(envelopes) == 1
    assert envelopes[0].body["strategy_id"] == "FOO-001"


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
