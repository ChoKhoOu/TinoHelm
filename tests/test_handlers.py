"""Tests for tinohelm.notifier.handlers — payload parsing + embed rendering."""

from __future__ import annotations

import json

import msgspec
import pytest

discord = pytest.importorskip("discord")

from tinohelm.notifier.handlers import envelope_for, parse_payload, render_embed


def test_parse_msgpack_payload() -> None:
    body = msgspec.msgpack.encode({"type": "OrderFilled", "instrument_id": "BTCUSDT.BYBIT"})
    parsed = parse_payload(body)
    assert isinstance(parsed, dict)
    assert parsed["type"] == "OrderFilled"


def test_parse_json_payload() -> None:
    body = json.dumps({"type": "PositionOpened", "side": "LONG"}).encode()
    parsed = parse_payload(body)
    assert isinstance(parsed, dict)
    assert parsed["side"] == "LONG"


def test_parse_string_passthrough() -> None:
    assert parse_payload("hello") == "hello"


def test_render_embed_for_unknown_topic_falls_back_gracefully() -> None:
    """An NT release that adds a new topic shouldn't break the bot.

    We don't want to maintain an exhaustive switch on event types — when a
    topic doesn't match any of the known prefixes (events.order/position/
    account, data.Signal, component_state_changed), the embed should still
    render with the body shown as JSON so the operator can see *something*.
    """

    body = msgspec.msgpack.encode({"some_new_field": "some_new_value"})
    env = envelope_for("events.brand_new_topic", body)
    embed = render_embed(env)
    assert embed.title is not None
    assert embed.description is not None
    assert "some_new_value" in embed.description


def test_parse_payload_does_not_crash_on_garbage_bytes() -> None:
    """A malformed payload must downgrade gracefully, not raise.

    The notifier subscribes to wildcard topics — a corrupted entry in any
    upstream stream would otherwise tank the whole bot. We accept whatever
    fallback ``parse_payload`` returns (hex preview, raw str, etc.) so long
    as the call doesn't propagate.
    """

    result = parse_payload(b"\x00\x01\x02\x03not-msgpack-not-json")
    assert result is not None  # any non-raising return is acceptable here


def test_render_embed_for_order_event() -> None:
    body = msgspec.msgpack.encode(
        {
            "type": "OrderFilled",
            "strategy_id": "FOO-001",
            "instrument_id": "BTCUSDT.BYBIT",
            "side": "BUY",
            "quantity": "0.01",
            "last_qty": "0.01",
            "last_px": "70000",
        },
    )
    env = envelope_for("events.order.FOO-001", body)
    embed = render_embed(env)
    assert "OrderFilled" in embed.title
    assert "FOO-001" in (embed.description or "")
