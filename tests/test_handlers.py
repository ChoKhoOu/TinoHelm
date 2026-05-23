"""Tests for tinohelm.notifier.handlers — payload parsing + embed rendering."""

from __future__ import annotations

import json

import msgspec
import pytest

discord = pytest.importorskip("discord")

from tinohelm.notifier.handlers import envelope_for, parse_payload, render_embed  # noqa: E402


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


def test_parse_payload_uses_to_dict_for_nt_event_objects() -> None:
    """NT publishes ``events.system.*`` as Python objects, not bytes.

    Without this branch ``parse_payload`` would str() the object and the
    embed would render as a useless ``{"raw": "ComponentStateChanged(...)"}``
    JSON dump. Verify the ``to_dict`` static method on the class is used so
    we end up with a real dict the formatters can read.
    """

    class _FakeNTEvent:
        @staticmethod
        def to_dict(obj):  # signature mirrors NT cdef classes (obj unused)
            return {"type": "ComponentStateChanged", "state": "RUNNING"}

    parsed = parse_payload(_FakeNTEvent())
    assert isinstance(parsed, dict)
    assert parsed["type"] == "ComponentStateChanged"
    assert parsed["state"] == "RUNNING"


def test_render_embed_routes_on_event_type_not_topic() -> None:
    """ComponentStateChanged comes in on ``events.system.*`` — there is no
    topic suffix to switch on. The renderer must look at the body's
    ``type`` field and pick the structured formatter rather than dumping
    JSON.
    """

    body = msgspec.msgpack.encode(
        {
            "type": "ComponentStateChanged",
            "trader_id": "TINO-NOTIFIER-001",
            "component_id": "NotifierActor",
            "component_type": "NotifierActor",
            "state": "RUNNING",
            "config": {"lots": "of", "stuff": "here"},
        },
    )
    env = envelope_for("events.system.*", body)
    embed = render_embed(env)
    description = embed.description or ""
    assert "ComponentStateChanged" in (embed.title or "")
    assert "NotifierActor" in description
    assert "RUNNING" in description
    # Config is noise on lifecycle events — it's a per-startup snapshot that
    # gets repeated identically every transition.
    assert "lots" not in description
    assert "stuff" not in description


def test_component_embed_includes_state_emoji() -> None:
    body = {"type": "ComponentStateChanged", "component_id": "X", "component_type": "X", "state": "RUNNING"}
    env = envelope_for("events.system.*", body)
    description = render_embed(env).description or ""
    # We don't lock the exact emoji here — just that one of the lifecycle
    # markers shows up so the human can scan a stream of events visually.
    assert any(marker in description for marker in ("🟢", "🟡", "🔴", "🟠", "⚪", "⚫", "▫️"))


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


def test_positions_report_renders_chinese_table_not_json() -> None:
    """``tinohelm.report.positions`` must render as a 中文 markdown-ish table.

    Previously this topic landed in the fallback JSON dump branch, so the
    operator saw a JSON-encoded CSV — the worst of both worlds. We pin the
    Chinese labels and the table shape so a refactor that re-routes this
    topic into JSON would fail fast.
    """

    body = {
        "strategy_id": "FOO-001",
        "row_count": 2,
        "csv": (
            "instrument_id,side,quantity,avg_px_open,realized_pnl,unrealized_pnl\n"
            "BTCUSDT.BYBIT,LONG,0.02,70000,12.5,3.0\n"
            "ETHUSDT.BYBIT,SHORT,0.5,3200,0,-5.5\n"
        ),
    }
    env = envelope_for("tinohelm.report.positions", body)
    embed = render_embed(env)

    description = embed.description or ""
    assert "持仓快照" in (embed.title or "")
    assert "FOO-001" in description
    assert "标的" in description
    assert "方向" in description
    assert "BTCUSDT.BYBIT" in description
    assert "ETHUSDT.BYBIT" in description
    assert '"csv"' not in description  # no JSON dump


def test_positions_report_handles_empty_snapshot() -> None:
    body = {"strategy_id": "FOO-001", "row_count": 0, "csv": ""}
    env = envelope_for("tinohelm.report.positions", body)
    description = render_embed(env).description or ""
    assert "FOO-001" in description
    assert "当前无持仓" in description


def test_daily_summary_renders_with_chinese_labels() -> None:
    body = {
        "positions_open": 3,
        "positions_closed": 27,
        "orders_open": 1,
        "redis_streams_seen": 8,
    }
    env = envelope_for("tinohelm.daily_summary", body)
    embed = render_embed(env)
    description = embed.description or ""

    assert "每日摘要" in (embed.title or "")
    assert "持仓中" in description
    assert "已平" in description
    assert "挂单" in description
    # Counts must show up as plain integers, not JSON-encoded.
    assert "3" in description
    assert "27" in description
    assert '"positions_open"' not in description
