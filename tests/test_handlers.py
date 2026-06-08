"""Tests for tinohelm.notifier.handlers — payload parsing + embed rendering."""

from __future__ import annotations

import json

import msgspec
import pytest

discord = pytest.importorskip("discord")

from tinohelm.notifier.handlers import (  # noqa: E402
    OrderProgressTracker,
    envelope_for,
    parse_payload,
    render_embed,
)


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


def test_order_event_shows_buy_sell_side_from_order_side_field() -> None:
    """NT order events serialize the direction as ``order_side`` (not ``side``
    — that key is position-only). The old formatter read ``side``, so order
    embeds never showed BUY/SELL at all. Verify we read ``order_side`` and
    surface the direction.

    Fields mirror NT 1.227.0's ``OrderFilled.to_dict()`` exactly.
    """

    body = msgspec.msgpack.encode(
        {
            "type": "OrderFilled",
            "strategy_id": "FOO-001",
            "instrument_id": "OPUSDT-PERP.BINANCE",
            "order_side": "SELL",
            "order_type": "MARKET",
            "last_qty": "8186.6",
            "last_px": "0.0946",
        },
    )
    env = envelope_for("events.order.FOO-001", body)
    description = render_embed(env).description or ""
    assert "SELL" in description


def test_order_event_shows_instruction_line_tif_postonly_reduceonly() -> None:
    """The whole point of issue #1: an operator must be able to tell IOC from
    GTC, and spot post_only / reduce_only, straight from the embed. These live
    on OrderInitialized only (NT 1.227.0 — confirmed via to_dict), so the
    instruction line renders whenever the body carries them.
    """

    body = msgspec.msgpack.encode(
        {
            "type": "OrderInitialized",
            "strategy_id": "FOO-001",
            "instrument_id": "OPUSDT-PERP.BINANCE",
            "order_side": "SELL",
            "order_type": "MARKET",
            "quantity": "8186.6",
            "time_in_force": "IOC",
            "post_only": False,
            "reduce_only": True,
        },
    )
    env = envelope_for("events.order.FOO-001", body)
    description = render_embed(env).description or ""
    assert "IOC" in description
    # reduce_only=True must be visible (it's a 平仓 order); post_only=False
    # should NOT add noise.
    assert "平仓" in description or "reduce" in description.lower()


def test_order_event_shows_stop_trigger_price_from_options() -> None:
    """Issue #2: the 止损 trigger price lives in the nested ``options`` dict on
    OrderInitialized (``options.trigger_price``), NOT at the top level. The old
    formatter only read top-level keys, so stop prices were invisible. Verify
    we drill into options.
    """

    body = msgspec.msgpack.encode(
        {
            "type": "OrderInitialized",
            "strategy_id": "FOO-001",
            "instrument_id": "ETHUSDT.BINANCE",
            "order_side": "SELL",
            "order_type": "STOP_MARKET",
            "quantity": "1",
            "time_in_force": "GTC",
            "reduce_only": True,
            "options": {"trigger_price": "0.95", "trigger_type": "LAST_PRICE"},
        },
    )
    env = envelope_for("events.order.FOO-001", body)
    description = render_embed(env).description or ""
    assert "0.95" in description


def test_order_event_shows_limit_price_from_options() -> None:
    """A LIMIT order's price lives in ``options.price`` on OrderInitialized
    (top-level ``price`` is null there). Verify the limit price surfaces.
    """

    body = msgspec.msgpack.encode(
        {
            "type": "OrderInitialized",
            "strategy_id": "FOO-001",
            "instrument_id": "ETHUSDT.BINANCE",
            "order_side": "BUY",
            "order_type": "LIMIT",
            "quantity": "1",
            "time_in_force": "GTC",
            "post_only": True,
            "options": {"price": "55.00"},
        },
    )
    env = envelope_for("events.order.FOO-001", body)
    description = render_embed(env).description or ""
    assert "55.00" in description
    assert "GTC" in description


# ─── OrderProgressTracker (IOC / partial-fill aggregation) ───────────────────


def test_tracker_aggregates_fill_progress_against_initialized_total() -> None:
    """Issue #3: NT's OrderFilled carries only ``last_qty`` (this fill), never
    the order total or cumulative filled. To show 'filled X/Y (Z%)' the tracker
    must remember the total from OrderInitialized and sum fills by
    client_order_id.
    """

    tracker = OrderProgressTracker()
    coid = "O-20260607-061503-001-oi_momentum_lowvol-2"

    tracker.observe(
        {
            "type": "OrderInitialized",
            "client_order_id": coid,
            "quantity": "8186.6",
            "time_in_force": "IOC",
        },
    )
    tracker.observe(
        {"type": "OrderFilled", "client_order_id": coid, "last_qty": "4000"},
    )

    snap = tracker.snapshot(coid)
    assert snap is not None
    assert snap.qty_total == 8186.6
    assert snap.qty_filled == 4000.0
    # a second fill accumulates
    tracker.observe(
        {"type": "OrderFilled", "client_order_id": coid, "last_qty": "186.6"},
    )
    assert tracker.snapshot(coid).qty_filled == 4186.6


def test_fill_embed_backfills_tif_from_tracker_after_denoise() -> None:
    """The crux of issue #1 surviving issue #4: time_in_force lives only on
    OrderInitialized, which the denoise gate suppresses. So the *kept*
    OrderFilled embed must backfill IOC/GTC from the tracker (which observed the
    suppressed Initialized) — otherwise the operator still can't tell IOC from
    GTC on the one event they actually see.
    """

    tracker = OrderProgressTracker()
    coid = "O-tif"
    tracker.observe(
        {
            "type": "OrderInitialized",
            "client_order_id": coid,
            "quantity": "100",
            "time_in_force": "IOC",
        },
    )
    fill = {
        "type": "OrderFilled",
        "strategy_id": "FOO-001",
        "instrument_id": "OPUSDT-PERP.BINANCE",
        "client_order_id": coid,
        "order_side": "SELL",
        "order_type": "MARKET",
        "last_qty": "100",
        "last_px": "0.1",
    }
    tracker.observe(fill)
    # the fill body itself has NO time_in_force (NT doesn't put it there)
    assert "time_in_force" not in fill
    env = envelope_for("events.order.FOO-001", fill)
    description = render_embed(env, tracker=tracker).description or ""
    assert "IOC" in description  # backfilled from tracker


def test_non_fill_kept_event_does_not_render_zero_progress() -> None:
    """A kept node with zero fills (e.g. OrderTriggered) must NOT render a noisy
    '0 / N (0%)' progress line — that's meaningless clutter. Only fills (with
    qty>0) and cancels carry a progress line.
    """

    tracker = OrderProgressTracker()
    coid = "O-trig"
    tracker.observe(
        {"type": "OrderInitialized", "client_order_id": coid, "quantity": "5"},
    )
    body = {
        "type": "OrderTriggered",
        "strategy_id": "FOO-001",
        "instrument_id": "ETHUSDT.BINANCE",
        "client_order_id": coid,
        "order_side": "SELL",
    }
    env = envelope_for("events.order.FOO-001", body)
    description = render_embed(env, tracker=tracker).description or ""
    assert "累计成交" not in description
    assert "0.0%" not in description


def test_partial_fill_embed_shows_cumulative_progress() -> None:
    """A partially-filled IOC order's OrderFilled embed must show cumulative
    progress (filled / total + percent), not just this fill's last_qty.
    """

    tracker = OrderProgressTracker()
    coid = "O-1"
    tracker.observe(
        {"type": "OrderInitialized", "client_order_id": coid, "quantity": "8186.6"},
    )
    body = {
        "type": "OrderFilled",
        "strategy_id": "FOO-001",
        "instrument_id": "OPUSDT-PERP.BINANCE",
        "client_order_id": coid,
        "order_side": "SELL",
        "order_type": "MARKET",
        "last_qty": "4000",
        "last_px": "0.0946",
    }
    tracker.observe(body)
    env = envelope_for("events.order.FOO-001", body)
    description = render_embed(env, tracker=tracker).description or ""

    # total and cumulative both visible; percentage somewhere
    assert "8186.6" in description
    assert "4000" in description
    assert "%" in description


def test_canceled_ioc_embed_shows_unfilled_remainder() -> None:
    """When an IOC order is canceled after a partial fill, the OrderCanceled
    embed (which NT sends nearly empty — no quantities) must show how much
    filled vs how much was canceled. Remainder = total − cumulative filled.
    """

    tracker = OrderProgressTracker()
    coid = "O-2"
    tracker.observe(
        {
            "type": "OrderInitialized",
            "client_order_id": coid,
            "quantity": "8186.6",
            "order_side": "SELL",
            "order_type": "MARKET",
        },
    )
    tracker.observe(
        {"type": "OrderFilled", "client_order_id": coid, "last_qty": "4000"},
    )
    cancel_body = {
        "type": "OrderCanceled",
        "strategy_id": "FOO-001",
        "instrument_id": "OPUSDT-PERP.BINANCE",
        "client_order_id": coid,
    }
    tracker.observe(cancel_body)
    env = envelope_for("events.order.FOO-001", cancel_body)
    description = render_embed(env, tracker=tracker).description or ""

    # unfilled remainder 8186.6 - 4000 = 4186.6 must be shown
    assert "4186.6" in description
    assert "4000" in description
    # direction + type backfilled from the tracker — NT's OrderCanceled carries
    # neither, but the operator still wants 'which way, what kind' on the event.
    assert "SELL" in description
    assert "MARKET" in description


def test_tracker_forgets_terminal_orders_to_bound_memory() -> None:
    """Terminal events (Canceled/Rejected/Expired/Denied, or fully filled) must
    let the tracker release the order's slot so a long-running notifier doesn't
    leak one entry per order forever.
    """

    tracker = OrderProgressTracker()
    coid = "O-3"
    tracker.observe(
        {"type": "OrderInitialized", "client_order_id": coid, "quantity": "10"},
    )
    tracker.observe(
        {"type": "OrderFilled", "client_order_id": coid, "last_qty": "10"},
    )
    # fully filled -> snapshot still readable for the fill embed
    assert tracker.snapshot(coid) is not None
    tracker.forget_if_terminal(
        {"type": "OrderFilled", "client_order_id": coid, "last_qty": "10"},
    )
    assert tracker.snapshot(coid) is None


def test_tracker_is_bounded_under_runaway_order_flow() -> None:
    """Defensive: even if forget is never reached (crash between fill and
    terminal), the tracker must not grow without bound. Verify an LRU-style cap.
    """

    tracker = OrderProgressTracker(max_orders=50)
    for i in range(200):
        tracker.observe(
            {"type": "OrderInitialized", "client_order_id": f"O-{i}", "quantity": "1"},
        )
    # never exceeds the cap
    assert tracker.size() <= 50
    # most-recent survive, oldest evicted
    assert tracker.snapshot("O-199") is not None
    assert tracker.snapshot("O-0") is None


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
