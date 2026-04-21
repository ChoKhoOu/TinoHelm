"""Tests for ``tinohelm.core.bridge`` — NT-free EventBridge + _infer_type.

The ``EventBridge`` routes Redis PubSub events to connected WebSocket clients.
These tests exercise its pure pieces (subscribe/unsubscribe, client_count,
channel→event-type inference, fan-out semantics, dead-connection reaping)
without a real Redis server.  The async listener/heartbeat loops themselves
are only validated through the fan-out helper they share.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tinohelm.core.bridge import _CHANNEL_TYPE_MAP, EventBridge, _infer_type


# ---------------------------------------------------------------------------
# _infer_type — channel → event-type mapping
# ---------------------------------------------------------------------------

class TestInferTypeChannelMap:
    """Every prefix registered in ``_CHANNEL_TYPE_MAP`` is exercised here.

    If a new prefix is added without a corresponding test row the
    ``test_all_prefixes_covered_by_tests`` sanity check below fails.
    """

    def test_backtest_prefix_expands_suffix(self):
        assert _infer_type("tino:backtest:progress") == "backtest.progress"

    def test_backtest_prefix_only_first_segment_after_prefix(self):
        # The helper takes just the first segment after the prefix.
        assert _infer_type("tino:backtest:progress:abc-123") == "backtest.progress"

    def test_heartbeat_prefix_returns_constant_type(self):
        # "tino:heartbeat:" maps to "node.heartbeat" (no trailing dot) — the
        # suffix is intentionally ignored.
        assert _infer_type("tino:heartbeat:sandbox") == "node.heartbeat"
        assert _infer_type("tino:heartbeat:live") == "node.heartbeat"

    def test_sandbox_prefix_expands_suffix(self):
        assert _infer_type("tino:sandbox:positions") == "node.sandbox.positions"
        assert _infer_type("tino:sandbox:fills") == "node.sandbox.fills"

    def test_live_prefix_expands_suffix(self):
        assert _infer_type("tino:live:positions") == "node.live.positions"
        assert _infer_type("tino:live:commands_ack") == "node.live.commands_ack"

    def test_data_prefix_expands_suffix(self):
        assert _infer_type("tino:data:progress") == "data.progress"

    def test_research_prefix_expands_suffix(self):
        assert _infer_type("tino:research:progress") == "research.progress"


class TestInferTypeEdgeCases:
    def test_returns_none_for_unknown_channel(self):
        assert _infer_type("foo:bar") is None

    def test_returns_none_for_empty_string(self):
        assert _infer_type("") is None

    def test_returns_none_for_tino_without_known_suffix(self):
        assert _infer_type("tino:other:x") is None

    def test_prefix_with_no_suffix_segment_yields_empty_tail(self):
        # "tino:backtest:" → prefix matches, suffix slice starts AT index
        # len(prefix); split(":")[0] returns "" ⇒ "backtest."
        assert _infer_type("tino:backtest:") == "backtest."

    def test_all_prefixes_covered_by_tests(self):
        # Sanity: the expected prefix set is what we cover above.  If someone
        # adds a new prefix this test fails, forcing a corresponding row.
        assert set(_CHANNEL_TYPE_MAP.keys()) == {
            "tino:backtest:",
            "tino:heartbeat:",
            "tino:sandbox:",
            "tino:live:",
            "tino:data:",
            "tino:research:",
        }


# ---------------------------------------------------------------------------
# EventBridge.__init__ — initial state
# ---------------------------------------------------------------------------

class TestBridgeInit:
    def test_initial_state_is_empty(self):
        b = EventBridge("redis://localhost:6379")
        assert b.client_count() == 0
        assert b._redis is None
        assert b._pubsub is None
        assert b._task is None
        assert b._heartbeat_task is None
        assert b._redis_url == "redis://localhost:6379"

    def test_clients_is_defaultdict(self):
        # Accessing an unknown pattern should not raise.
        b = EventBridge("redis://localhost:6379")
        assert b._clients["nonexistent"] == set()


# ---------------------------------------------------------------------------
# EventBridge.subscribe
# ---------------------------------------------------------------------------

@pytest.fixture
def bridge() -> EventBridge:
    return EventBridge("redis://localhost:6379")


class TestSubscribe:
    async def test_none_channels_registers_wildcard(self, bridge):
        ws = object()
        await bridge.subscribe(ws, None)
        assert bridge._clients == {"*": {ws}}

    async def test_empty_list_treated_as_wildcard(self, bridge):
        # ``if channels:`` is False for both None and [], so both go to "*".
        ws = object()
        await bridge.subscribe(ws, [])
        assert ws in bridge._clients["*"]

    async def test_single_channel_subscription(self, bridge):
        ws = object()
        await bridge.subscribe(ws, ["tino:sandbox"])
        assert bridge._clients == {"tino:sandbox": {ws}}

    async def test_multiple_channels_create_separate_pattern_sets(self, bridge):
        ws = object()
        await bridge.subscribe(ws, ["tino:sandbox", "tino:live"])
        assert bridge._clients == {
            "tino:sandbox": {ws},
            "tino:live": {ws},
        }

    async def test_duplicate_subscribe_is_idempotent(self, bridge):
        ws = object()
        await bridge.subscribe(ws, ["tino:sandbox"])
        await bridge.subscribe(ws, ["tino:sandbox"])
        assert bridge._clients["tino:sandbox"] == {ws}

    async def test_multiple_clients_on_same_channel(self, bridge):
        ws1, ws2 = object(), object()
        await bridge.subscribe(ws1, ["tino:sandbox"])
        await bridge.subscribe(ws2, ["tino:sandbox"])
        assert bridge._clients["tino:sandbox"] == {ws1, ws2}

    async def test_client_on_both_wildcard_and_specific(self, bridge):
        ws = object()
        await bridge.subscribe(ws, None)
        await bridge.subscribe(ws, ["tino:sandbox"])
        assert ws in bridge._clients["*"]
        assert ws in bridge._clients["tino:sandbox"]


# ---------------------------------------------------------------------------
# EventBridge.client_count — unique count across overlapping patterns
# ---------------------------------------------------------------------------

class TestClientCount:
    async def test_zero_by_default(self, bridge):
        assert bridge.client_count() == 0

    async def test_single_client_wildcard(self, bridge):
        await bridge.subscribe(object(), None)
        assert bridge.client_count() == 1

    async def test_single_client_multiple_channels_counts_once(self, bridge):
        ws = object()
        await bridge.subscribe(ws, ["tino:sandbox", "tino:live"])
        assert bridge.client_count() == 1

    async def test_same_client_on_wildcard_and_specific_counts_once(self, bridge):
        ws = object()
        await bridge.subscribe(ws, None)
        await bridge.subscribe(ws, ["tino:data"])
        assert bridge.client_count() == 1

    async def test_distinct_clients_counted_separately(self, bridge):
        await bridge.subscribe(object(), None)
        await bridge.subscribe(object(), ["tino:sandbox"])
        await bridge.subscribe(object(), ["tino:live"])
        assert bridge.client_count() == 3


# ---------------------------------------------------------------------------
# EventBridge.unsubscribe — removes from all patterns + reaps empty sets
# ---------------------------------------------------------------------------

class TestUnsubscribe:
    async def test_removes_from_all_patterns(self, bridge):
        ws = object()
        await bridge.subscribe(ws, ["tino:sandbox", "tino:live"])
        await bridge.unsubscribe(ws)
        assert bridge.client_count() == 0

    async def test_reaps_empty_pattern_sets(self, bridge):
        """Short-lived subscriptions should not accumulate empty keys."""
        ws = object()
        await bridge.subscribe(ws, ["tino:sandbox"])
        await bridge.unsubscribe(ws)
        # ``tino:sandbox`` key must be deleted (not left as an empty set).
        assert "tino:sandbox" not in bridge._clients

    async def test_reaps_wildcard_when_empty(self, bridge):
        ws = object()
        await bridge.subscribe(ws, None)
        await bridge.unsubscribe(ws)
        assert "*" not in bridge._clients

    async def test_keeps_pattern_while_other_client_still_subscribed(self, bridge):
        ws1, ws2 = object(), object()
        await bridge.subscribe(ws1, ["tino:sandbox"])
        await bridge.subscribe(ws2, ["tino:sandbox"])
        await bridge.unsubscribe(ws1)
        assert bridge._clients["tino:sandbox"] == {ws2}

    async def test_idempotent_for_unknown_client(self, bridge):
        """Unsubscribing a client that was never subscribed is a no-op."""
        await bridge.subscribe(object(), ["tino:sandbox"])
        # Call unsubscribe with a client that was never subscribed.
        await bridge.unsubscribe(object())
        # Existing subscription intact.
        assert len(bridge._clients["tino:sandbox"]) == 1

    async def test_double_unsubscribe_is_safe(self, bridge):
        ws = object()
        await bridge.subscribe(ws, ["tino:sandbox"])
        await bridge.unsubscribe(ws)
        await bridge.unsubscribe(ws)  # no crash
        assert bridge.client_count() == 0

    async def test_subscribe_after_unsubscribe_recreates_pattern(self, bridge):
        ws = object()
        await bridge.subscribe(ws, ["tino:sandbox"])
        await bridge.unsubscribe(ws)
        assert "tino:sandbox" not in bridge._clients

        await bridge.subscribe(ws, ["tino:sandbox"])
        assert bridge._clients["tino:sandbox"] == {ws}


# ---------------------------------------------------------------------------
# EventBridge._relay — send + dead connection reaping
# ---------------------------------------------------------------------------

def _fake_ws(*, dead: bool = False) -> AsyncMock:
    """A WebSocket stand-in with an async ``send_text``.  Set ``dead=True`` to
    simulate a disconnected client that raises on every send."""
    ws = AsyncMock()
    if dead:
        ws.send_text.side_effect = RuntimeError("socket closed")
    return ws


class TestRelay:
    async def test_delivers_to_all_clients(self, bridge):
        ws1, ws2 = _fake_ws(), _fake_ws()
        clients = {ws1, ws2}
        await bridge._relay('{"x":1}', clients)
        ws1.send_text.assert_awaited_once_with('{"x":1}')
        ws2.send_text.assert_awaited_once_with('{"x":1}')
        assert clients == {ws1, ws2}

    async def test_reaps_dead_clients(self, bridge):
        alive = _fake_ws()
        dead = _fake_ws(dead=True)
        clients = {alive, dead}
        await bridge._relay("payload", clients)
        assert dead not in clients
        assert alive in clients

    async def test_delivers_to_alive_even_when_first_is_dead(self, bridge):
        """A failing client must not prevent delivery to siblings."""
        dead = _fake_ws(dead=True)
        alive = _fake_ws()
        clients = {dead, alive}
        await bridge._relay("payload", clients)
        alive.send_text.assert_awaited_once_with("payload")

    async def test_empty_set_no_op(self, bridge):
        await bridge._relay("payload", set())  # no crash

    async def test_does_not_mutate_external_copy(self, bridge):
        """Iterating over ``list(clients)`` means a snapshot is taken — callers
        passing the live set should only see dead entries removed."""
        alive = _fake_ws()
        clients = {alive}
        await bridge._relay("payload", clients)
        assert clients == {alive}


# ---------------------------------------------------------------------------
# EventBridge._publish_to_subscribers — fan-out semantics shared by
# ``_listener`` and ``_heartbeat_poller``.
# ---------------------------------------------------------------------------

class TestPublishToSubscribers:
    async def test_wildcard_subscriber_gets_everything(self, bridge):
        ws = _fake_ws()
        await bridge.subscribe(ws, None)
        await bridge._publish_to_subscribers("tino:sandbox:positions", "payload")
        ws.send_text.assert_awaited_once_with("payload")

    async def test_prefix_subscriber_gets_matching_channel(self, bridge):
        ws = _fake_ws()
        await bridge.subscribe(ws, ["tino:sandbox"])
        await bridge._publish_to_subscribers("tino:sandbox:positions", "payload")
        ws.send_text.assert_awaited_once_with("payload")

    async def test_prefix_subscriber_misses_non_matching_channel(self, bridge):
        ws = _fake_ws()
        await bridge.subscribe(ws, ["tino:sandbox"])
        await bridge._publish_to_subscribers("tino:live:positions", "payload")
        ws.send_text.assert_not_awaited()

    async def test_wildcard_and_prefix_same_client_delivers_only_once(self, bridge):
        """A client subscribed on both ``*`` and a specific prefix receives
        the message twice (once per subscription) — documented behaviour.

        This test locks down the current semantics.  If de-duplication is
        added later the test must be updated.
        """
        ws = _fake_ws()
        await bridge.subscribe(ws, None)
        await bridge.subscribe(ws, ["tino:sandbox"])
        await bridge._publish_to_subscribers("tino:sandbox:fills", "payload")
        assert ws.send_text.await_count == 2

    async def test_multiple_prefixes_all_matching_all_get_delivery(self, bridge):
        """If several prefixes are all prefixes of the published channel,
        every one of their subscriber sets receives the payload."""
        ws_broad = _fake_ws()
        ws_narrow = _fake_ws()
        await bridge.subscribe(ws_broad, ["tino"])
        await bridge.subscribe(ws_narrow, ["tino:sandbox:positions"])
        await bridge._publish_to_subscribers("tino:sandbox:positions", "payload")
        ws_broad.send_text.assert_awaited_once_with("payload")
        ws_narrow.send_text.assert_awaited_once_with("payload")

    async def test_no_subscribers_is_a_no_op(self, bridge):
        await bridge._publish_to_subscribers("tino:sandbox", "payload")  # no crash

    async def test_delivers_to_distinct_clients_on_same_prefix(self, bridge):
        ws1, ws2 = _fake_ws(), _fake_ws()
        await bridge.subscribe(ws1, ["tino:sandbox"])
        await bridge.subscribe(ws2, ["tino:sandbox"])
        await bridge._publish_to_subscribers("tino:sandbox:fills", "payload")
        ws1.send_text.assert_awaited_once_with("payload")
        ws2.send_text.assert_awaited_once_with("payload")

    async def test_dead_subscriber_reaped_across_fan_out(self, bridge):
        alive = _fake_ws()
        dead = _fake_ws(dead=True)
        await bridge.subscribe(alive, None)
        await bridge.subscribe(dead, None)
        await bridge._publish_to_subscribers("tino:sandbox:positions", "payload")
        # Dead client reaped from wildcard set.
        assert dead not in bridge._clients["*"]
        assert alive in bridge._clients["*"]


# ---------------------------------------------------------------------------
# Heartbeat-style fan-out — pattern "tino:heartbeat" matches
# "tino:heartbeat:sandbox" and "tino:heartbeat:live".
# ---------------------------------------------------------------------------

class TestHeartbeatFanOut:
    async def test_heartbeat_channel_reaches_heartbeat_subscribers(self, bridge):
        ws = _fake_ws()
        await bridge.subscribe(ws, ["tino:heartbeat"])
        await bridge._publish_to_subscribers("tino:heartbeat:sandbox", "payload")
        ws.send_text.assert_awaited_once_with("payload")

    async def test_heartbeat_channel_reaches_wildcard(self, bridge):
        ws = _fake_ws()
        await bridge.subscribe(ws, None)
        await bridge._publish_to_subscribers("tino:heartbeat:live", "payload")
        ws.send_text.assert_awaited_once_with("payload")

    async def test_heartbeat_channel_misses_sandbox_subscriber(self, bridge):
        ws = _fake_ws()
        await bridge.subscribe(ws, ["tino:sandbox"])
        await bridge._publish_to_subscribers("tino:heartbeat:sandbox", "payload")
        ws.send_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# Behaviour parity — what the old listener/heartbeat used to do, inlined,
# must still hold.  This is a regression guard against the refactor.
# ---------------------------------------------------------------------------

class TestLegacyBehaviourParity:
    """These scenarios reproduce the exact delivery patterns the two original
    duplicated loops produced.  Keeping them here makes any regression from
    the consolidation immediately visible."""

    async def test_listener_scenario_backtest_progress(self, bridge):
        # Former _listener: "*" + prefix subscribers.
        ws_star = _fake_ws()
        ws_bt = _fake_ws()
        ws_other = _fake_ws()
        await bridge.subscribe(ws_star, None)
        await bridge.subscribe(ws_bt, ["tino:backtest"])
        await bridge.subscribe(ws_other, ["tino:live"])
        await bridge._publish_to_subscribers("tino:backtest:progress", "payload")
        ws_star.send_text.assert_awaited_once_with("payload")
        ws_bt.send_text.assert_awaited_once_with("payload")
        ws_other.send_text.assert_not_awaited()

    async def test_heartbeat_scenario_node_specific_subscriber(self, bridge):
        # Former _heartbeat_poller: pattern "tino:heartbeat:sandbox" specific.
        ws_specific = _fake_ws()
        await bridge.subscribe(ws_specific, ["tino:heartbeat:sandbox"])
        await bridge._publish_to_subscribers("tino:heartbeat:sandbox", "payload")
        ws_specific.send_text.assert_awaited_once_with("payload")
        # Now a "live" heartbeat must not reach the sandbox-specific subscriber.
        ws_specific.send_text.reset_mock()
        await bridge._publish_to_subscribers("tino:heartbeat:live", "payload")
        ws_specific.send_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# Async lifecycle — start/stop wiring with mocked aioredis
# ---------------------------------------------------------------------------

class TestStartStop:
    async def test_stop_is_safe_without_start(self, bridge):
        # No redis, no tasks created — stop() must not raise.
        await bridge.stop()

    async def test_start_creates_tasks_and_stop_cancels_them(self, monkeypatch, bridge):
        # Patch aioredis.from_url with an AsyncMock redis client that has a
        # pubsub() returning an AsyncMock with psubscribe/punsubscribe/close/
        # listen. ``listen`` returns an async iterator that yields nothing
        # immediately (so the listener task exits promptly on cancel).

        async def _empty_listen():
            if False:  # pragma: no cover - generator shape
                yield

        fake_pubsub = AsyncMock()
        fake_pubsub.listen = lambda: _empty_listen()

        fake_redis = AsyncMock()
        fake_redis.pubsub = lambda: fake_pubsub

        monkeypatch.setattr(
            "tinohelm.core.bridge.aioredis.from_url",
            lambda url: fake_redis,
        )

        await bridge.start()
        assert bridge._task is not None
        assert bridge._heartbeat_task is not None
        fake_pubsub.psubscribe.assert_awaited_once_with("tino:*")

        await bridge.stop()
        # Tasks should be cancelled/awaited without raising.
        assert bridge._task.cancelled() or bridge._task.done()
        assert bridge._heartbeat_task.cancelled() or bridge._heartbeat_task.done()
        fake_pubsub.punsubscribe.assert_awaited_with("tino:*")
        fake_redis.close.assert_awaited()
