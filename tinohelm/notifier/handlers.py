"""Convert NT events / bytes payloads into Discord embeds.

Discord embeds are written in 简体中文 for the operational topics
(``tinohelm.*``: positions snapshot, daily summary, /positions response).
NT-native event types still render with their English field names — those
strings come straight from NT and translating them in flight would just
hide what the upstream actually emitted.


External-stream messages arriving from Redis are delivered as raw ``bytes``
(see ``crates/infrastructure/src/redis/msgbus.rs::decode_bus_message``). NT
needs an explicit ``Serializer`` registered to inflate them back into typed
events; we deliberately skip that registration in the notifier — we want the
notifier to be schema-tolerant: a new event type added in NT shouldn't crash
the bot, it should just show up in Discord with whatever JSON arrived. So we
parse opportunistically: try msgpack then JSON, fall back to a hex preview.

In-process events (NT publishes them as Python objects, not bytes — see
``events.system.*`` ComponentStateChanged) get fed straight into our
handlers; we recognise them by the ``to_dict`` static method that every
NT event class exposes and convert that way instead of stringifying.
"""

from __future__ import annotations

import csv
import io
import json
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import discord
import msgspec

_msgpack_decoder = msgspec.msgpack.Decoder()
_json_decoder = msgspec.json.Decoder()


@dataclass(frozen=True)
class EventEnvelope:
    topic: str
    body: dict[str, Any] | str
    received_at: datetime


def _try_nt_event_to_dict(raw: Any) -> dict[str, Any] | None:
    # NT event classes expose ``to_dict`` as a @staticmethod on the class
    # (cdef classes), so look it up on ``type(raw)``. Best-effort: any
    # exception falls through to the generic str fallback.
    to_dict = getattr(type(raw), "to_dict", None)
    if not callable(to_dict):
        return None
    try:
        result = to_dict(raw)
    except Exception:
        return None
    return result if isinstance(result, dict) else None


def parse_payload(raw: Any) -> dict[str, Any] | str:
    """Best-effort decode of an NT BusMessage payload."""

    if raw is None:
        return ""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return _json_decoder.decode(raw.encode())
        except (msgspec.DecodeError, ValueError):
            return raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            return _msgpack_decoder.decode(raw)
        except msgspec.DecodeError:
            try:
                return _json_decoder.decode(bytes(raw))
            except msgspec.DecodeError:
                return raw.hex()[:200]
    nt_dict = _try_nt_event_to_dict(raw)
    if nt_dict is not None:
        return nt_dict
    return str(raw)


def envelope_for(topic: str, raw: Any) -> EventEnvelope:
    return EventEnvelope(
        topic=topic,
        body=parse_payload(raw),
        received_at=datetime.now(tz=UTC),
    )


# ─── Discord embeds ──────────────────────────────────────────────────────────

_COLOR_MAP = {
    "OrderFilled": 0x2ECC71,
    "OrderPartiallyFilled": 0x27AE60,
    "OrderAccepted": 0x3498DB,
    "OrderSubmitted": 0x95A5A6,
    "OrderCanceled": 0xF39C12,
    "OrderRejected": 0xE74C3C,
    # Deeper red than OrderRejected: a denial means the order never left the
    # engine (risk/instrument/position-id check failed), so it's the most
    # likely to be mistaken for "nothing happened".
    "OrderDenied": 0xC0392B,
    "OrderExpired": 0xE67E22,
    "OrderModified": 0x9B59B6,
    "PositionOpened": 0x1ABC9C,
    "PositionChanged": 0x16A085,
    "PositionClosed": 0xE74C3C,
    "AccountState": 0x34495E,
    "Signal": 0xE91E63,
    "ComponentStateChanged": 0x7F8C8D,
}

# Emoji per NT lifecycle state — picked so green/red are reserved for the
# stable resting states (RUNNING / STOPPED) and amber for transitions.
_COMPONENT_STATE_EMOJI = {
    "PRE_INITIALIZED": "⚪",
    "READY": "⚪",
    "INITIALIZED": "⚪",
    "STARTING": "🟡",
    "RUNNING": "🟢",
    "STOPPING": "🟠",
    "STOPPED": "🔴",
    "RESUMING": "🟡",
    "RESETTING": "🟠",
    "DISPOSING": "🟠",
    "DISPOSED": "⚫",
    "DEGRADING": "🟠",
    "DEGRADED": "🟠",
    "FAULTING": "🔴",
    "FAULTED": "🔴",
}


# Order events that mean "this order will NOT execute" — surfaced prominently
# because the failure is otherwise easy to miss in a stream of normal events.
# OrderDenied = engine-side refusal (risk check, instrument not in cache,
# NETTING position-id mismatch); OrderRejected = venue-side refusal.
_REJECTION_EVENTS = frozenset({"OrderDenied", "OrderRejected"})


def render_embed(env: EventEnvelope, *, source_pod: str | None = None) -> discord.Embed:
    body = env.body if isinstance(env.body, dict) else {"raw": env.body}
    event_type = _guess_event_type(env.topic, body)

    title = f"[{event_type}]"
    description_lines: list[str] = []

    # Route on event_type first (the body's own self-description), then fall
    # back to topic prefixes for payloads that don't carry one. This matters
    # for in-process events on ``events.system.*`` where the topic only tells
    # us "system" but the body says "ComponentStateChanged".
    if env.topic == "tinohelm.message":
        return _render_custom_message(body, received_at=env.received_at, source_pod=source_pod)
    elif event_type in _REJECTION_EVENTS:
        # Route on event_type (not the events.order.* prefix) so denials stand
        # out from normal order flow: a ⚠️ 中文 title + the deny reason. These
        # are the 'looks placed, never reached the venue' events.
        title = f"⚠️ [订单被拒 · {event_type}]"
        description_lines.append(_fmt_rejection(body))
    elif event_type == "ComponentStateChanged":
        description_lines.append(_fmt_component(body))
    elif env.topic == "tinohelm.report.positions":
        title = "[持仓快照]"
        description_lines.append(_fmt_positions_report(body))
    elif env.topic == "tinohelm.daily_summary":
        title = "[每日摘要]"
        description_lines.append(_fmt_daily_summary(body))
    elif env.topic.startswith("events.order."):
        description_lines.append(_fmt_order(body))
    elif env.topic.startswith("events.position."):
        description_lines.append(_fmt_position(body))
    elif env.topic.startswith("events.account."):
        description_lines.append(_fmt_account(body))
    elif env.topic.startswith("data.Signal"):
        description_lines.append(_fmt_signal(body))
    else:
        description_lines.append(f"```json\n{json.dumps(_drop_noisy_keys(body), indent=2)[:1500]}\n```")

    if source_pod:
        description_lines.append(f"_pod: `{source_pod}`_")

    embed = discord.Embed(
        title=title,
        description="\n".join(description_lines),
        color=_COLOR_MAP.get(event_type, 0x99AAB5),
        timestamp=env.received_at,
    )
    embed.set_footer(text=env.topic)
    return embed


_DEFAULT_CUSTOM_COLOR = 0x5865F2


def _render_custom_message(
    body: dict[str, Any] | str,
    *,
    received_at: datetime,
    source_pod: str | None,
) -> discord.Embed:
    if isinstance(body, dict):
        title = str(body.get("title", "通知"))
        text = str(body.get("text", ""))
        color = body.get("color", _DEFAULT_CUSTOM_COLOR)
    else:
        title = "通知"
        text = str(body)
        color = _DEFAULT_CUSTOM_COLOR

    description = text[:4000]
    if source_pod:
        description += f"\n_pod: `{source_pod}`_"

    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=received_at,
    )


def _guess_event_type(topic: str, body: dict[str, Any]) -> str:
    if isinstance(body, dict) and (t := body.get("type")):
        return str(t)
    return topic.rsplit(".", 1)[-1]


def _drop_noisy_keys(body: dict[str, Any]) -> dict[str, Any]:
    # ``config`` on lifecycle events is just the actor's startup config snapshot,
    # repeated identically every transition; ``event_id`` / ``ts_init`` are NT
    # plumbing. None of it helps a human read the message.
    noisy = {"config", "event_id", "ts_init", "ts_event"}
    return {k: v for k, v in body.items() if k not in noisy}


def _fmt_order(body: dict[str, Any]) -> str:
    keys = (
        "strategy_id",
        "instrument_id",
        "side",
        "order_type",
        "quantity",
        "price",
        "last_qty",
        "last_px",
        "status",
    )
    rows = [f"**{k}**: `{body[k]}`" for k in keys if k in body]
    if "client_order_id" in body:
        rows.append(f"**id**: `{body['client_order_id']}`")
    return "\n".join(rows) or f"```json\n{json.dumps(body, indent=2)[:1200]}\n```"


def _fmt_rejection(body: dict[str, Any]) -> str:
    """Render OrderDenied / OrderRejected with the reason front-and-centre.

    The ``reason`` is the whole point — it tells the operator *why* the order
    won't execute (instrument not found, NETTING position-id mismatch,
    insufficient balance, rate limit, …). Identifying fields follow so they
    can trace which order/instrument/strategy it was.
    """

    reason = body.get("reason")
    lines = [f"**原因**: `{reason}`"] if reason else []
    keys = ("strategy_id", "instrument_id", "side", "order_type", "quantity", "price")
    lines.extend(f"**{k}**: `{body[k]}`" for k in keys if k in body)
    if "client_order_id" in body:
        lines.append(f"**id**: `{body['client_order_id']}`")
    return "\n".join(lines) or f"```json\n{json.dumps(body, indent=2)[:1200]}\n```"


def _fmt_position(body: dict[str, Any]) -> str:
    keys = (
        "strategy_id",
        "instrument_id",
        "side",
        "quantity",
        "avg_px_open",
        "avg_px_close",
        "realized_pnl",
        "unrealized_pnl",
    )
    rows = [f"**{k}**: `{body[k]}`" for k in keys if k in body]
    return "\n".join(rows) or f"```json\n{json.dumps(body, indent=2)[:1200]}\n```"


def _fmt_account(body: dict[str, Any]) -> str:
    if "balances" in body:
        rows = ["**balances**"]
        for bal in body["balances"][:8]:
            rows.append(
                f"- `{bal.get('currency', '?')}` total=`{bal.get('total', '?')}` "
                f"locked=`{bal.get('locked', '?')}` free=`{bal.get('free', '?')}`",
            )
        return "\n".join(rows)
    return f"```json\n{json.dumps(body, indent=2)[:1200]}\n```"


def _fmt_signal(body: dict[str, Any]) -> str:
    if isinstance(body, dict):
        name = body.get("name", "?")
        value = body.get("value", body.get("data", "?"))
        return f"**name**: `{name}`\n**value**: `{value}`"
    return f"`{body}`"


def _fmt_component(body: dict[str, Any]) -> str:
    state = str(body.get("state", "?"))
    component_id = body.get("component_id", "?")
    component_type = body.get("component_type", "?")
    trader_id = body.get("trader_id")
    emoji = _COMPONENT_STATE_EMOJI.get(state, "▫️")

    # The component_id is often the same string as component_type (e.g. an
    # actor whose id wasn't overridden). In that case showing both is just
    # noise — collapse to one.
    if component_id == component_type:
        head = f"{emoji} **{component_id}** → `{state}`"
    else:
        head = f"{emoji} **{component_id}** ({component_type}) → `{state}`"
    if trader_id:
        return f"{head}\n_trader: `{trader_id}`_"
    return head


# ─── operational topic formatters (中文) ────────────────────────────────────


# Discord embed description caps at 4096 chars, but a code block becomes hard
# to scan well before that. Cap row count so the operator sees the most
# recent positions and gets a "...还有 N 条未显示" tail rather than a
# silently-truncated table.
_POSITIONS_TABLE_MAX_ROWS = 12


def _fmt_positions_report(body: dict[str, Any]) -> str:
    """Render ``tinohelm.report.positions`` as a Markdown table in 中文.

    The wire format is ``{"strategy_id": ..., "row_count": N, "csv": ...}``
    (see :func:`tinohelm.reporting_actor.build_positions_report_payload`).
    CSV beats JSON on the wire because pandas writes it natively and it
    compresses well; we pay the parse cost here so the operator gets a
    table instead of a JSON dump.
    """

    strategy_id = body.get("strategy_id", "?")
    row_count = body.get("row_count", 0)
    csv_text = body.get("csv", "") or ""

    header = f"**策略**: `{strategy_id}` · **持仓**: `{row_count}` 条"
    if not csv_text or row_count == 0:
        return f"{header}\n_当前无持仓_"

    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        return f"{header}\n_无法解析持仓数据_"

    columns = rows[0]
    data_rows = rows[1:]

    # Pick the columns operators actually skim for. Anything missing from
    # the snapshot just gets dropped from the table — NT's column set has
    # been stable across versions, but if it ever drifts we'd rather show
    # fewer columns than render a malformed table.
    preferred = (
        "instrument_id",
        "side",
        "quantity",
        "avg_px_open",
        "avg_px_close",
        "realized_pnl",
        "unrealized_pnl",
    )
    label_zh = {
        "instrument_id": "标的",
        "side": "方向",
        "quantity": "数量",
        "avg_px_open": "开仓均价",
        "avg_px_close": "平仓均价",
        "realized_pnl": "已实现",
        "unrealized_pnl": "浮动",
    }
    indices = [(label_zh[c], columns.index(c)) for c in preferred if c in columns]
    if not indices:
        # NT changed the column set under us — fall back to a count-only line
        # so the operator at least sees the snapshot landed.
        return f"{header}\n_(列名不识别，原始 CSV 见 stream)_"

    headers = [zh for zh, _ in indices]
    body_rows = [[r[i] if i < len(r) else "" for _, i in indices] for r in data_rows]
    truncated = len(body_rows) > _POSITIONS_TABLE_MAX_ROWS
    body_rows = body_rows[:_POSITIONS_TABLE_MAX_ROWS]

    table = _markdown_table(headers, body_rows)
    tail = f"\n_…还有 {len(data_rows) - _POSITIONS_TABLE_MAX_ROWS} 条未显示_" if truncated else ""
    return f"{header}\n```\n{table}\n```{tail}"


def _fmt_daily_summary(body: dict[str, Any]) -> str:
    """Render ``tinohelm.daily_summary`` with 中文 field labels."""

    if "note" in body:
        # Notifier ran without ``[cache]`` section — the position counts
        # aren't available, so just surface the note.
        return f"_{body['note']}_"

    open_n = body.get("positions_open", 0)
    closed_n = body.get("positions_closed", 0)
    orders_n = body.get("orders_open", 0)
    streams_n = body.get("redis_streams_seen", 0)

    return (
        f"📊 **每日摘要**\n"
        f"持仓中: **{open_n}** · 已平: **{closed_n}** · 挂单: **{orders_n}**\n"
        f"_Redis 流数: {streams_n}_"
    )


def _display_width(text: str) -> int:
    # CJK / fullwidth characters render as two columns in Discord's mono
    # font. Counting code points (``len(s)``) under-allocates by half on
    # those, so the separator line ends up shorter than the header above
    # it — operators see a visibly skewed table. Use ``east_asian_width``:
    # ``W`` (Wide) and ``F`` (Fullwidth) are the two-column buckets.
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def _pad(text: str, width: int) -> str:
    pad = width - _display_width(text)
    return text + " " * max(pad, 0)


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    # Hand-rolled because Discord doesn't render real Markdown tables — we
    # use a fixed-width text table inside a code block, which is the
    # convention everyone already knows from CLI tools.
    widths = [_display_width(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            if i < len(widths):
                widths[i] = max(widths[i], _display_width(str(cell)))

    def _row(cells: list[str]) -> str:
        return "  ".join(_pad(str(c), widths[i]) for i, c in enumerate(cells))

    # ``─`` (U+2500) is East-Asian-width "A" (ambiguous), which our
    # _display_width treats as 1 column — so repeating it ``width`` times
    # matches the header above it on Discord's mono font. If we ever need
    # to render in a CJK-default-wide environment, swap to a single-column
    # ASCII ``-`` instead.
    lines = [_row(headers), _row(["─" * w for w in widths])]
    lines.extend(_row(r) for r in rows)
    return "\n".join(lines)
