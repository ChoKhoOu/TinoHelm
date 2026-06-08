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

from tinohelm.notifier.runner import (
    _parse_hh_mm,
    register_streaming_types,
    strategies_for_channel,
)


class _MsgbusSpy:
    """Records add_streaming_type calls without a real TradingNode/msgbus.

    Mirrors NT's ``MessageBus.add_streaming_type`` / ``is_streaming_type`` pair
    so we can assert the notifier whitelists the right types for replay.
    """

    def __init__(self) -> None:
        self.registered: set[type] = set()

    def add_streaming_type(self, type_: type) -> None:
        self.registered.add(type_)

    def is_streaming_type(self, type_: type) -> bool:
        return type_ in self.registered


def test_register_streaming_types_whitelists_nt_order_and_position_events() -> None:
    """Without registration, NT's publish_bus_message drops every external
    event (is_streaming_type → False), so the notifier stays silent even with a
    correct external_streams. Assert the trade-flow event types get registered.
    """

    from nautilus_trader.model.events import OrderFilled, PositionOpened

    spy = _MsgbusSpy()
    registered = register_streaming_types(spy)

    assert OrderFilled in spy.registered
    assert PositionOpened in spy.registered
    # is_streaming_type (the gate NT actually checks) now passes for them
    assert spy.is_streaming_type(OrderFilled)
    assert spy.is_streaming_type(PositionOpened)
    assert OrderFilled in registered


def test_register_streaming_types_includes_account_and_system_events() -> None:
    """The logging channel relies on account/system events too (AccountState,
    ComponentStateChanged); these live in NT's common.events, not model.events,
    so the reflection must span both modules."""

    from nautilus_trader.common.events import ComponentStateChanged
    from nautilus_trader.model.events import AccountState

    spy = _MsgbusSpy()
    register_streaming_types(spy)

    assert AccountState in spy.registered
    assert ComponentStateChanged in spy.registered


def test_register_streaming_types_is_schema_tolerant_reflection() -> None:
    """Types come from NT's public ``__all__`` by reflection, not a hard-coded
    list — so an NT upgrade adding/renaming an event class is picked up with no
    code change (and no version pin). Assert we registered a plausible bulk,
    and only actual classes (no stray non-type names)."""

    spy = _MsgbusSpy()
    registered = register_streaming_types(spy)

    assert len(registered) >= 15  # NT exposes ~20+ event classes
    assert all(isinstance(t, type) for t in registered)


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


def test_strategies_for_channel_sandbox_excludes_non_sandbox_modes() -> None:
    """Sandbox channel must match ``"sandbox"`` exactly — not "anything
    that isn't live". Otherwise a registry entry with a mode value the
    notifier doesn't recognise (e.g. a future ``"logging"`` mode, or a
    typo'd announce) would get fan-out from the sandbox channel even
    though the operator never asked for it.
    """

    registry = {
        "FOO-001": "sandbox",
        "BAR-002": "live",
        "BAZ-003": "logging",  # unrecognised mode — must NOT leak into sandbox
        "QUX-004": "",  # blank mode — same, must NOT leak
    }

    assert strategies_for_channel("sandbox", registry) == ["FOO-001"]


def test_strategies_for_channel_live_excludes_sandbox() -> None:
    registry = {"FOO-001": "sandbox", "BAR-002": "live"}
    assert strategies_for_channel("live", registry) == ["BAR-002"]


def test_strategies_for_channel_logging_returns_everything() -> None:
    registry = {"FOO-001": "sandbox", "BAR-002": "live", "BAZ-003": "logging"}
    assert sorted(strategies_for_channel("logging", registry)) == sorted(registry.keys())


def test_forwarder_watch_positions_resolves_on_matching_envelope() -> None:
    """``/positions`` registers a future via :meth:`watch_positions_report`;
    the next ``tinohelm.report.positions`` envelope passing through enqueue
    must satisfy it. We exercise the loop-thread dispatch path so we know
    the futures wire up correctly across the NT-handler / asyncio boundary.
    """

    import asyncio

    from tinohelm.notifier.handlers import envelope_for
    from tinohelm.notifier.runner import DiscordForwarder

    async def _run() -> Any:
        loop = asyncio.get_running_loop()
        forwarder = DiscordForwarder(loop=loop, client=None)
        future = forwarder.watch_positions_report("FOO-001")

        env = envelope_for(
            "tinohelm.report.positions",
            {"strategy_id": "FOO-001", "row_count": 0, "csv": ""},
        )
        forwarder.enqueue(env, channel_id=123)
        return await asyncio.wait_for(future, timeout=2.0)

    result = asyncio.run(_run())
    assert result.body["strategy_id"] == "FOO-001"


def test_forwarder_drop_position_listener_clears_dead_pod_slot() -> None:
    """``/positions`` cancels its future on timeout and calls
    ``drop_position_listener``; the forwarder's internal list must shed
    the entry so a pod that's permanently gone doesn't accumulate
    cancelled futures forever.
    """

    import asyncio

    from tinohelm.notifier.runner import DiscordForwarder

    async def _run() -> dict:
        loop = asyncio.get_running_loop()
        forwarder = DiscordForwarder(loop=loop, client=None)
        future = forwarder.watch_positions_report("FOO-001")
        # /positions timeout path: cancel + drop.
        future.cancel()
        forwarder.drop_position_listener("FOO-001", future)
        # Reach in for the test — this is the only place we want to inspect
        # the leak directly. Public surface is just the methods above.
        return forwarder._position_listeners

    listeners = asyncio.run(_run())
    assert "FOO-001" not in listeners


def test_forwarder_watch_positions_ignores_other_strategies() -> None:
    """A snapshot from BAR must not satisfy a future waiting on FOO."""

    import asyncio

    from tinohelm.notifier.handlers import envelope_for
    from tinohelm.notifier.runner import DiscordForwarder

    async def _run() -> bool:
        loop = asyncio.get_running_loop()
        forwarder = DiscordForwarder(loop=loop, client=None)
        future = forwarder.watch_positions_report("FOO-001")

        env = envelope_for(
            "tinohelm.report.positions",
            {"strategy_id": "BAR-002", "row_count": 0, "csv": ""},
        )
        forwarder.enqueue(env, channel_id=123)
        # Give the loop a tick to deliver any (incorrect) dispatch.
        await asyncio.sleep(0.05)
        return future.done()

    assert asyncio.run(_run()) is False


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


def test_route_channel_resolves_nt_strategy_id_to_control_handle() -> None:
    """NT native events carry the *NT StrategyId* (``{ClassName}-{handle}``) in
    their body, but the registry is keyed by the *control handle* (the strategy
    directory name). ``route_channel`` must reconcile the two or every live fill
    misroutes to sandbox.

    The body NT serializes is ``"OIMomentum-oi_momentum_lowvol"`` while the
    announce-built registry key is the bare handle ``"oi_momentum_lowvol"``. A
    direct ``registry.get`` misses → sandbox. Suffix-matching the NT id back to
    the control handle is the fix.
    """

    from tinohelm.notifier.runner import route_channel

    channel_id = route_channel(
        "OIMomentum-oi_momentum_lowvol",
        {"oi_momentum_lowvol": "live"},
        sandbox=11,
        live=22,
        logging_channel_id=33,
    )
    assert channel_id == 22


def test_route_channel_resolves_handle_containing_hyphen() -> None:
    """A control handle with its own hyphen (``FOO-001``) means the NT id is
    ``OIMomentum-FOO-001``. ``StrategyId.get_tag()`` (identifiers.pyx:843)
    splits on the *last* hyphen and would wrongly yield ``001`` — which is why
    we suffix-match against the registry's authoritative handles instead.
    """

    from tinohelm.notifier.runner import route_channel

    channel_id = route_channel(
        "OIMomentum-FOO-001",
        {"FOO-001": "live"},
        sandbox=11,
        live=22,
        logging_channel_id=33,
    )
    assert channel_id == 22


def test_route_channel_idempotent_when_body_already_control_handle() -> None:
    """When the body already carries a control handle (registry hit on the raw
    id), routing must be unchanged — the resolution step is a no-op so we never
    regress the paths that already pass a handle straight through.
    """

    from tinohelm.notifier.runner import route_channel

    channel_id = route_channel(
        "oi_momentum_lowvol",
        {"oi_momentum_lowvol": "live"},
        sandbox=11,
        live=22,
        logging_channel_id=33,
    )
    assert channel_id == 22


def test_route_channel_unknown_strategy_falls_back_to_sandbox() -> None:
    """An id that is neither a registry key nor a suffix match keeps the
    existing safe default: sandbox. No new pod gets surfaced into the live
    channel by accident.
    """

    from tinohelm.notifier.runner import route_channel

    channel_id = route_channel(
        "Ghost-never_announced",
        {"oi_momentum_lowvol": "live"},
        sandbox=11,
        live=22,
        logging_channel_id=33,
    )
    assert channel_id == 11


def test_route_channel_empty_strategy_id_goes_to_logging() -> None:
    """Empty strategy_id (unscoped topic / forced ``""`` for ``tinohelm.*``)
    short-circuits to logging *before* any resolution — that semantics must be
    preserved exactly.
    """

    from tinohelm.notifier.runner import route_channel

    channel_id = route_channel(
        "",
        {"oi_momentum_lowvol": "live"},
        sandbox=11,
        live=22,
        logging_channel_id=33,
    )
    assert channel_id == 33


def test_route_channel_picks_most_specific_handle_on_overlap() -> None:
    """If two registry handles could both suffix-match, the longer (most
    specific) one wins so an ambiguous shorter handle can't shadow it.

    ``Strat-a-b`` ends with both ``-b`` and ``-a-b``; the ``a-b`` handle is the
    correct, more-specific control handle.
    """

    from tinohelm.notifier.runner import route_channel

    channel_id = route_channel(
        "Strat-a-b",
        {"b": "sandbox", "a-b": "live"},
        sandbox=11,
        live=22,
        logging_channel_id=33,
    )
    assert channel_id == 22


def test_notifier_actor_routes_nt_native_order_event_to_live_channel() -> None:
    """End-to-end through the in-process handler: an NT-native ``OrderFilled``
    whose body strategy_id is the NT StrategyId must reach the live channel of
    the strategy whose control handle is the registry key.

    This is the real bug — live fills were landing in sandbox because the body's
    ``OIMomentum-oi_momentum_lowvol`` missed the ``oi_momentum_lowvol`` key.
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
        registry={"oi_momentum_lowvol": "live"},
    )

    handler = actor._make_handler("events.order.*")
    handler(
        json.dumps(
            {"strategy_id": "OIMomentum-oi_momentum_lowvol", "type": "OrderFilled"},
        ).encode(),
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
        env.topic == "tinohelm.protocol_mismatch" and channel_id == 33 for env, channel_id in sent
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


def test_notifier_actor_subscribes_to_tinohelm_namespace() -> None:
    """``tinohelm.*`` carries operational chatter (ReportingActor reports,
    daily summary). Without subscribing to it the notifier silently drops
    everything published on that namespace — exactly what would happen to
    ReportingActor's per-30m positions report if this pattern were absent.
    """

    from tinohelm.notifier.runner import NotifierActor

    assert "tinohelm.*" in NotifierActor.SUBSCRIBE_PATTERNS


def test_notifier_actor_routes_tinohelm_report_to_logging_channel() -> None:
    """A ``tinohelm.report.positions`` event carries a ``strategy_id`` in its
    body (the report belongs to a single strategy). But the *channel* it
    lands in must be logging, not the strategy's sandbox/live channel —
    reports are operational chatter, not trade flow, and mirroring them to
    the trade-flow channels would defeat the triple-channel split.

    The actor must therefore treat ``tinohelm.*`` as unscoped regardless of
    what the body contains.
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

    handler = actor._make_handler("tinohelm.*")
    handler(
        json.dumps(
            {"strategy_id": "FOO-001", "row_count": 7, "csv": "..."},
        ).encode(),
    )

    assert sent == [33]


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


# ─── standalone event-stream consumer ──────────────────────────────────────────
#
# These cover the announce-independent XREAD loop that owns the notifier's event
# intake — the fix for the silent-notifier bug (external_streams froze to [] when
# the announce stream was empty at boot, so the notifier received zero pod events).


def test_should_forward_topic_keeps_trade_flow_and_drops_market_data() -> None:
    """The loop reads the entry's plaintext topic and must keep trade-flow /
    operational topics while dropping the ~99% ``data.quotes.*`` market-data
    volume *before* paying the msgpack decode cost. A live pod funnels every
    tick into the same stream, so a leaky filter would swamp Discord.
    """

    from tinohelm.notifier.runner import should_forward_topic

    assert should_forward_topic("events.order.BINANCE")
    assert should_forward_topic("events.position.BINANCE")
    assert should_forward_topic("events.account.BINANCE")
    assert should_forward_topic("events.system.DataEngine")
    assert should_forward_topic("data.SignalCustom")
    assert should_forward_topic("tinohelm.report.positions")
    # Market data — the noise we must never forward.
    assert not should_forward_topic("data.quotes.BINANCE.LABUSDT-PERP")
    assert not should_forward_topic("data.trades.BINANCE.SOLUSDT-PERP")
    assert not should_forward_topic("data.bars.BINANCE.DOGEUSDT-PERP")


def test_discover_event_streams_globs_only_aggregate_stream_keys() -> None:
    """``trader-*:stream`` must match each pod's single aggregate stream but
    NOT the per-topic streams (``...:stream:events.*``) or the cache keys NT
    also writes (``...:instruments:*`` / ``...:currencies:*`` / ``...:accounts:*``).
    Mis-matching those would feed the loop garbage keys to XREAD.
    """

    import fakeredis

    from tinohelm.notifier.runner import discover_event_streams

    rc = fakeredis.FakeRedis(decode_responses=False)
    # Aggregate event streams — these are what we want.
    rc.xadd("trader-TINO-001:stream", {"topic": "events.system.X", "payload": b"\x80"})
    rc.xadd("trader-TINO-002:stream", {"topic": "events.system.X", "payload": b"\x80"})
    # Cache keys NT writes under the same trader prefix — must NOT match.
    rc.xadd("trader-TINO-001:instruments:BTCUSDT-PERP.BINANCE", {"x": b"1"})
    rc.set("trader-TINO-001:currencies:USDT", b"1")
    rc.xadd("trader-TINO-001:accounts:BINANCE-001", {"x": b"1"})

    assert discover_event_streams(rc) == [
        "trader-TINO-001:stream",
        "trader-TINO-002:stream",
    ]


def test_discover_event_streams_skips_nt_owned_keys() -> None:
    """Keys NT already XREADs via external_streams (operator-pinned) must be
    excluded so the standalone loop never double-delivers the same event.
    """

    import fakeredis

    from tinohelm.notifier.runner import discover_event_streams

    rc = fakeredis.FakeRedis(decode_responses=False)
    rc.xadd("trader-TINO-001:stream", {"topic": "events.system.X", "payload": b"\x80"})
    rc.xadd("trader-TINO-002:stream", {"topic": "events.system.X", "payload": b"\x80"})

    out = discover_event_streams(rc, skip={"trader-TINO-001:stream"})
    assert out == ["trader-TINO-002:stream"]


def test_route_event_uses_resolved_topic_for_strategy_channel() -> None:
    """Unlike the in-process actor (which only sees the subscribe *pattern*),
    the loop has the real topic from the Redis entry. An order event for a live
    strategy must route to the live channel via the body's ``strategy_id``.
    """

    import msgspec.msgpack

    from tinohelm.notifier.runner import route_event

    payload = msgspec.msgpack.encode({"strategy_id": "FOO-001", "type": "OrderFilled"})
    env, channel_id = route_event(
        "events.order.BINANCE",
        payload,
        {"FOO-001": "live"},
        sandbox_channel_id=11,
        live_channel_id=22,
        logging_channel_id=33,
    )
    assert channel_id == 22
    assert env.body["strategy_id"] == "FOO-001"


def test_route_event_forces_tinohelm_namespace_to_logging() -> None:
    """``tinohelm.*`` carries a strategy_id (a positions report belongs to one
    strategy) but is operational chatter — it must land in logging, never the
    strategy's trade-flow channel.
    """

    import msgspec.msgpack

    from tinohelm.notifier.runner import route_event

    payload = msgspec.msgpack.encode({"strategy_id": "FOO-001", "row_count": 3})
    _env, channel_id = route_event(
        "tinohelm.report.positions",
        payload,
        {"FOO-001": "live"},
        sandbox_channel_id=11,
        live_channel_id=22,
        logging_channel_id=33,
    )
    assert channel_id == 33


def test_forward_stream_entry_drops_market_data_without_enqueue() -> None:
    """A ``data.quotes.*`` entry must be dropped at the topic gate — the
    forwarder is never touched (and the payload never decoded).
    """

    import msgspec.msgpack

    from tinohelm.notifier.runner import _forward_stream_entry

    sent: list[int] = []

    class _FakeForwarder:
        def enqueue(self, env, *, channel_id: int) -> None:
            sent.append(channel_id)

    fields = {
        b"topic": b"data.quotes.BINANCE.LABUSDT-PERP",
        b"payload": msgspec.msgpack.encode({"bid": "1"}),
    }
    forwarded = _forward_stream_entry(
        fields,
        _FakeForwarder(),
        {},
        sandbox_channel_id=11,
        live_channel_id=22,
        logging_channel_id=33,
    )
    assert forwarded is False
    assert sent == []


def test_forward_stream_entry_forwards_position_event() -> None:
    """A PositionOpened entry (the exact event the operator was missing) must
    decode and reach the strategy's channel via the shared router.
    """

    import msgspec.msgpack

    from tinohelm.notifier.runner import _forward_stream_entry

    sent: list[tuple[Any, int]] = []

    class _FakeForwarder:
        def enqueue(self, env, *, channel_id: int) -> None:
            sent.append((env, channel_id))

    fields = {
        b"topic": b"events.position.BINANCE",
        b"payload": msgspec.msgpack.encode(
            {"strategy_id": "FOO-001", "type": "PositionOpened"},
        ),
    }
    forwarded = _forward_stream_entry(
        fields,
        _FakeForwarder(),
        {"FOO-001": "sandbox"},
        sandbox_channel_id=11,
        live_channel_id=22,
        logging_channel_id=33,
    )
    assert forwarded is True
    assert len(sent) == 1
    env, channel_id = sent[0]
    assert channel_id == 11  # FOO-001 is sandbox
    assert env.body["type"] == "PositionOpened"


def test_event_stream_loop_delivers_new_pod_events_end_to_end() -> None:
    """The whole point, exercised against fakeredis: a pod whose stream exists
    but was never in the announce stream still gets its events delivered.

    We seed the stream with one pre-existing entry (which the loop must SKIP —
    it starts from the tail), start the loop, write a fresh OrderFilled, and
    assert it lands on the right channel. This is the regression for
    'external_streams=[] → notifier silent'.
    """

    import asyncio

    import fakeredis
    import msgspec.msgpack

    from tinohelm.notifier.runner import _event_stream_loop

    sent: list[tuple[str, int]] = []

    class _FakeForwarder:
        def enqueue(self, env, *, channel_id: int) -> None:
            sent.append((env.topic, channel_id))

    async def _run() -> None:
        rc = fakeredis.FakeRedis(decode_responses=False)
        # Pre-existing backlog entry — must be skipped (loop reads from tail).
        rc.xadd(
            "trader-FOO-001:stream",
            {
                "topic": b"events.order.BINANCE",
                "payload": msgspec.msgpack.encode(
                    {"strategy_id": "FOO-001", "type": "OrderAccepted"},
                ),
            },
        )
        task = asyncio.create_task(
            _event_stream_loop(
                rc,
                _FakeForwarder(),
                {"FOO-001": "live"},
                sandbox_channel_id=11,
                live_channel_id=22,
                logging_channel_id=33,
                block_ms=50,
                rediscover_interval_s=999.0,
            ),
        )
        # Let discovery + tail-cursor seeding happen.
        await asyncio.sleep(0.2)
        # A fresh event after the notifier started watching.
        rc.xadd(
            "trader-FOO-001:stream",
            {
                "topic": b"events.order.BINANCE",
                "payload": msgspec.msgpack.encode(
                    {"strategy_id": "FOO-001", "type": "OrderFilled"},
                ),
            },
        )
        await asyncio.sleep(0.3)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run())

    # Exactly the post-watch OrderFilled was delivered, to the live channel.
    # The pre-existing OrderAccepted backlog entry was skipped.
    assert ("events.order.BINANCE", 22) in sent
    assert len(sent) == 1


# ─── startup-noise gate: drop transient ComponentStateChanged events ───────────
#
# Every pod boot emits a ComponentStateChanged for *each* NT component
# (DataEngine / ExecEngine / RiskEngine / Portfolio / every actor / strategy /
# data&exec clients) as it walks PRE_INITIALIZED → READY → STARTING → RUNNING.
# With several pods + the notifier coming up under ``make up`` that's dozens of
# embeds slammed at the one logging channel in a second → Discord 429s the bot,
# which then also stalls slash-command followups (the "正在响应……" hang). We keep
# only the resting/abnormal states an operator actually cares about.


def test_should_forward_component_state_keeps_resting_and_abnormal_states() -> None:
    """RUNNING / STOPPED tell the operator a component settled; DEGRADED /
    FAULTED are failures they must see. These survive the gate.
    """

    from tinohelm.notifier.runner import should_forward_component_state

    assert should_forward_component_state("RUNNING")
    assert should_forward_component_state("STOPPED")
    assert should_forward_component_state("DEGRADED")
    assert should_forward_component_state("FAULTED")


def test_should_forward_component_state_drops_transient_boot_states() -> None:
    """The transient walk-up states (PRE_INITIALIZED / READY / INITIALIZED /
    STARTING / STOPPING / RESUMING / RESETTING / DISPOSING / DEGRADING /
    FAULTING) are pure boot/shutdown chatter — dropping them is the bulk of the
    429-flood fix.
    """

    from tinohelm.notifier.runner import should_forward_component_state

    for transient in (
        "PRE_INITIALIZED",
        "READY",
        "INITIALIZED",
        "STARTING",
        "STOPPING",
        "RESUMING",
        "RESETTING",
        "DISPOSING",
        "DEGRADING",
        "FAULTING",
        "DISPOSED",
    ):
        assert not should_forward_component_state(transient), transient


def test_should_forward_component_state_unknown_state_is_kept() -> None:
    """Schema-tolerant: a future NT state spelling we don't recognise is kept,
    not silently dropped — we'd rather over-report a new state than hide it.
    """

    from tinohelm.notifier.runner import should_forward_component_state

    assert should_forward_component_state("SOME_FUTURE_STATE")


def test_notifier_actor_drops_transient_component_state_event() -> None:
    """End-to-end through the in-process actor path: a STARTING
    ComponentStateChanged must not reach the forwarder at all (it's the boot
    noise that floods #logging), while a RUNNING one still does.
    """

    import json

    from tinohelm.notifier.runner import NotifierActor, NotifierActorConfig

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

    handler = actor._make_handler("events.system.*")
    handler(
        json.dumps(
            {"type": "ComponentStateChanged", "component_id": "RiskEngine", "state": "STARTING"},
        ).encode(),
    )
    assert sent == []  # transient boot state dropped

    handler(
        json.dumps(
            {"type": "ComponentStateChanged", "component_id": "RiskEngine", "state": "RUNNING"},
        ).encode(),
    )
    assert sent == [33]  # resting state still forwarded to logging


def test_forward_stream_entry_drops_transient_component_state() -> None:
    """Same gate on the standalone XREAD path: a STARTING ComponentStateChanged
    arriving as a msgpack stream entry is dropped before enqueue. This is the
    path that actually carries the boot flood from strategy pods.
    """

    import msgspec.msgpack

    from tinohelm.notifier.runner import _forward_stream_entry

    sent: list[int] = []

    class _FakeForwarder:
        def enqueue(self, env, *, channel_id: int) -> None:
            sent.append(channel_id)

    fields = {
        b"topic": b"events.system.DataEngine",
        b"payload": msgspec.msgpack.encode(
            {"type": "ComponentStateChanged", "component_id": "DataEngine", "state": "READY"},
        ),
    }
    forwarded = _forward_stream_entry(
        fields,
        _FakeForwarder(),
        {},
        sandbox_channel_id=11,
        live_channel_id=22,
        logging_channel_id=33,
    )
    assert forwarded is False
    assert sent == []


# ─── on-demand /positions reply goes to the triggering channel ─────────────────
#
# The periodic 30-min snapshot stays on #logging (operational chatter). But when
# an operator runs /positions in #sandbox, the reply must surface in #sandbox —
# routed via the future the command registers, NOT mirrored to #logging too.


def test_enqueue_on_demand_snapshot_skips_logging_when_listener_waiting() -> None:
    """A ``tinohelm.report.positions`` envelope that satisfies a pending
    /positions listener is the *on-demand* reply — the command handler will
    post it to the triggering channel itself, so enqueue must NOT also mirror
    it to #logging (the ``channel_id`` it was handed). Otherwise every
    /positions double-posts: once in the command channel, once in #logging.
    """

    import asyncio

    from tinohelm.notifier.handlers import envelope_for
    from tinohelm.notifier.runner import DiscordForwarder

    sent: list[int] = []

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        forwarder = DiscordForwarder(loop=loop, client=None)

        async def _fake_send(env, channel_id: int) -> None:
            sent.append(channel_id)

        forwarder._send = _fake_send  # type: ignore[assignment]

        future = forwarder.watch_positions_report("FOO-001")
        env = envelope_for(
            "tinohelm.report.positions",
            {"strategy_id": "FOO-001", "row_count": 0, "csv": ""},
        )
        # logging channel id handed in (the periodic route), but a listener is
        # waiting → this is on-demand → must not mirror to logging.
        forwarder.enqueue(env, channel_id=33)
        await asyncio.wait_for(future, timeout=2.0)
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    assert sent == []  # on-demand snapshot was NOT sent to logging by enqueue


def test_enqueue_periodic_snapshot_still_reaches_logging() -> None:
    """No listener waiting → this is the periodic 30-min report → it must still
    reach #logging exactly as before. The on-demand skip must not regress the
    periodic path.
    """

    import asyncio

    from tinohelm.notifier.handlers import envelope_for
    from tinohelm.notifier.runner import DiscordForwarder

    sent: list[int] = []

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        forwarder = DiscordForwarder(loop=loop, client=None)

        async def _fake_send(env, channel_id: int) -> None:
            sent.append(channel_id)

        forwarder._send = _fake_send  # type: ignore[assignment]

        env = envelope_for(
            "tinohelm.report.positions",
            {"strategy_id": "FOO-001", "row_count": 0, "csv": ""},
        )
        forwarder.enqueue(env, channel_id=33)
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    assert sent == [33]  # periodic snapshot still lands in logging


# ─── order-event denoise (issue #4) ─────────────────────────────────────────


def test_should_forward_order_event_suppresses_transient_states() -> None:
    """Issue #4: the mid-flight order states (Initialized/Submitted/Accepted/
    Pending*) are the spam — a single market order emits 3-4 of them before the
    fill. They must be suppressed so the operator sees only the meaningful
    nodes.
    """

    from tinohelm.notifier.runner import should_forward_order_event

    for transient in (
        "OrderInitialized",
        "OrderSubmitted",
        "OrderAccepted",
        "OrderPendingUpdate",
        "OrderPendingCancel",
        "OrderReleased",
        "OrderEmulated",
    ):
        assert should_forward_order_event({"type": transient}) is False, transient


def test_should_forward_order_event_keeps_meaningful_nodes() -> None:
    """Fill / cancel / rejection / expiry / trigger / amend are the events worth
    a Discord ping — each is a state the operator must not miss.
    """

    from tinohelm.notifier.runner import should_forward_order_event

    for keep in (
        "OrderFilled",
        "OrderCanceled",
        "OrderRejected",
        "OrderDenied",
        "OrderExpired",
        "OrderUpdated",
        "OrderTriggered",
    ):
        assert should_forward_order_event({"type": keep}) is True, keep


def test_should_forward_order_event_passes_non_order_and_unknown() -> None:
    """Non-order bodies (positions, signals, account) and unknown/future order
    types pass through untouched — schema-tolerant against NT enum drift, same
    posture as the component-state gate.
    """

    from tinohelm.notifier.runner import should_forward_order_event

    assert should_forward_order_event({"type": "PositionOpened"}) is True
    assert should_forward_order_event({"type": "OrderBrandNewFutureState"}) is True
    assert should_forward_order_event({"name": "buy", "value": 1}) is True
    assert should_forward_order_event("not-a-dict") is True


def test_notifier_actor_suppresses_transient_order_event_but_tracks_total() -> None:
    """The two intake paths must behave identically: an OrderInitialized is
    suppressed from Discord (no enqueue) BUT its total quantity is still fed to
    the tracker first — otherwise the later OrderFilled embed couldn't show
    'filled X / total Y'.
    """

    import json

    from tinohelm.notifier.runner import NotifierActor, NotifierActorConfig

    sent: list[Any] = []

    class _FakeForwarder:
        def __init__(self) -> None:
            from tinohelm.notifier.handlers import OrderProgressTracker

            self.tracker = OrderProgressTracker()

        def enqueue(self, env, *, channel_id: int) -> None:
            sent.append(env)

    forwarder = _FakeForwarder()
    actor = NotifierActor(
        NotifierActorConfig(
            sandbox_channel_id=11,
            live_channel_id=22,
            logging_channel_id=33,
        ),
        forwarder=forwarder,
        registry={"FOO-001": "live"},
    )

    handler = actor._make_handler("events.order.*")
    handler(
        json.dumps(
            {
                "type": "OrderInitialized",
                "strategy_id": "FOO-001",
                "client_order_id": "O-1",
                "quantity": "8186.6",
                "time_in_force": "IOC",
            },
        ).encode(),
    )

    # suppressed from Discord ...
    assert sent == []
    # ... but the total was captured for later fill aggregation
    snap = forwarder.tracker.snapshot("O-1")
    assert snap is not None
    assert snap.qty_total == 8186.6


def test_notifier_actor_forwards_fill_with_progress() -> None:
    """An OrderFilled is forwarded (not suppressed) and the embed reflects the
    aggregated progress the tracker accumulated across the suppressed
    Initialized + this fill.
    """

    import json

    from tinohelm.notifier.handlers import render_embed
    from tinohelm.notifier.runner import NotifierActor, NotifierActorConfig

    sent: list[Any] = []

    class _FakeForwarder:
        def __init__(self) -> None:
            from tinohelm.notifier.handlers import OrderProgressTracker

            self.tracker = OrderProgressTracker()

        def enqueue(self, env, *, channel_id: int) -> None:
            sent.append(env)

    forwarder = _FakeForwarder()
    actor = NotifierActor(
        NotifierActorConfig(
            sandbox_channel_id=11,
            live_channel_id=22,
            logging_channel_id=33,
        ),
        forwarder=forwarder,
        registry={"FOO-001": "live"},
    )

    handler = actor._make_handler("events.order.*")
    handler(
        json.dumps(
            {
                "type": "OrderInitialized",
                "strategy_id": "FOO-001",
                "client_order_id": "O-1",
                "quantity": "8186.6",
            },
        ).encode(),
    )
    handler(
        json.dumps(
            {
                "type": "OrderFilled",
                "strategy_id": "FOO-001",
                "client_order_id": "O-1",
                "order_side": "SELL",
                "last_qty": "8186.6",
                "last_px": "0.0946",
            },
        ).encode(),
    )

    assert len(sent) == 1  # only the fill reached Discord
    description = render_embed(sent[0], tracker=forwarder.tracker).description or ""
    assert "8186.6" in description
    assert "100" in description  # 100% filled
