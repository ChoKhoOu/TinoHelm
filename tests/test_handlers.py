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
    body = {
        "type": "ComponentStateChanged",
        "component_id": "X",
        "component_type": "X",
        "state": "RUNNING",
    }
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


def test_order_denied_renders_prominently_with_reason() -> None:
    """OrderDenied is the silent killer behind 'looks placed, never reached the
    venue' — instrument-not-in-cache and NETTING position-id mismatches both
    surface as OrderDenied. It must stand out from normal order flow: a ⚠️ +
    中文 title, the deny ``reason`` shown verbatim, and a red color (not the
    grey OrderSubmitted/neutral tone) so an operator scanning the channel can't
    miss it.

    Fields come straight from NT's ``OrderDenied.to_dict()`` (type='OrderDenied'
    + a ``reason`` string), confirmed against the installed NT.
    """

    body = msgspec.msgpack.encode(
        {
            "type": "OrderDenied",
            "strategy_id": "FOO-001",
            "instrument_id": "DELISTED-PERP.BYBIT",
            "client_order_id": "O-19700101-000000-001",
            "reason": "Instrument for DELISTED-PERP.BYBIT not found",
        },
    )
    env = envelope_for("events.order.FOO-001", body)
    embed = render_embed(env)

    description = embed.description or ""
    title = embed.title or ""
    # Stands out: warning marker + 中文 so it's not just another order line.
    assert "⚠️" in title
    assert "被拒" in title
    # The reason is the whole point — without it the operator can't tell why.
    assert "Instrument for DELISTED-PERP.BYBIT not found" in description
    assert "FOO-001" in description
    # Red, not the neutral fallback grey.
    assert embed.color is not None
    assert embed.color.value != 0x99AAB5


def test_order_rejected_also_renders_as_rejection() -> None:
    """OrderRejected (venue-side refusal) shares the same 'failed, did you
    notice?' risk as OrderDenied (engine-side), so it gets the same prominent
    treatment and surfaces its reason.
    """

    body = msgspec.msgpack.encode(
        {
            "type": "OrderRejected",
            "strategy_id": "BAR-001",
            "instrument_id": "BTCUSDT-PERP.BYBIT",
            "reason": "INSUFFICIENT_BALANCE",
        },
    )
    env = envelope_for("events.order.BAR-001", body)
    embed = render_embed(env)
    title = embed.title or ""
    description = embed.description or ""
    assert "⚠️" in title
    assert "被拒" in title
    assert "INSUFFICIENT_BALANCE" in description


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


def test_positions_report_renders_account_pnl_summary() -> None:
    """When the snapshot carries an ``account_pnl`` block, the embed must show
    an account-level summary (已实现 / 浮动 / 净敞口) under the positions table,
    in 中文, with the venue and currency-coded amounts. This is the /pnl view
    folded into the existing /positions reply.
    """

    body = {
        "strategy_id": "FOO-001",
        "row_count": 1,
        "csv": "instrument_id,side,quantity\nBTCUSDT.BYBIT,LONG,0.02\n",
        "account_pnl": {
            "BYBIT": {
                "realized": {"USDT": "120.50 USDT"},
                "unrealized": {"USDT": "-15.00 USDT"},
                "net_exposure": {"USDT": "5000.00 USDT"},
            },
        },
    }
    env = envelope_for("tinohelm.report.positions", body)
    description = render_embed(env).description or ""

    assert "账户汇总" in description
    assert "已实现" in description
    assert "浮动" in description
    assert "BYBIT" in description
    assert "120.50 USDT" in description
    assert "-15.00 USDT" in description


def test_positions_report_without_account_pnl_renders_table_only() -> None:
    """No account_pnl key (old payload shape / portfolio unavailable) — render
    the positions table as before, with no empty summary section.
    """

    body = {
        "strategy_id": "FOO-001",
        "row_count": 1,
        "csv": "instrument_id,side,quantity\nBTCUSDT.BYBIT,LONG,0.02\n",
    }
    env = envelope_for("tinohelm.report.positions", body)
    description = render_embed(env).description or ""
    assert "账户汇总" not in description
    assert "BTCUSDT.BYBIT" in description


def test_positions_report_handles_empty_snapshot() -> None:
    body = {"strategy_id": "FOO-001", "row_count": 0, "csv": ""}
    env = envelope_for("tinohelm.report.positions", body)
    description = render_embed(env).description or ""
    assert "FOO-001" in description
    assert "当前无持仓" in description


def test_markdown_table_separator_matches_cjk_header_display_width() -> None:
    """CJK headers like 标的 / 开仓均价 are double-width in mono fonts.

    The previous implementation used ``len()``, so the separator under
    ``开仓均价`` (code-point length 4, display width 8) came up only 4
    dashes long — the table visibly skewed in Discord. Verify each
    column's separator has display width ≥ the header's display width.
    """

    import unicodedata

    from tinohelm.notifier.handlers import _markdown_table

    def _w(s: str) -> int:
        return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)

    headers = ["标的", "方向", "开仓均价"]
    rows = [["BTC", "L", "70000"]]
    rendered = _markdown_table(headers, rows)
    lines = rendered.splitlines()
    assert len(lines) == 3

    header_line, sep_line, _data_line = lines

    # Header line and separator line must render to the same display
    # width — that's the entire promise of an aligned table.
    assert _w(header_line.rstrip()) == _w(sep_line.rstrip())

    # Each separator cell (split on the literal "  " gutter) must be a
    # pure run of dashes whose display width matches its header cell.
    # ``"开仓均价"`` (display width 8) used to come out as 4 dashes under
    # the old ``len()``-based implementation; pin that behaviour gone.
    header_cells = header_line.rstrip().split("  ")
    sep_cells = sep_line.rstrip().split("  ")
    assert header_cells == ["标的", "方向", "开仓均价"]
    for hcell, scell in zip(header_cells, sep_cells, strict=True):
        assert set(scell) <= {"─"}, f"separator must be all dashes, got {scell!r}"
        assert _w(scell) == _w(hcell)


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
