"""Convert NT events / bytes payloads into Discord embeds.

External-stream messages arriving from Redis are delivered as raw ``bytes``
(see ``crates/infrastructure/src/redis/msgbus.rs::decode_bus_message``). NT
needs an explicit ``Serializer`` registered to inflate them back into typed
events; we deliberately skip that registration in the notifier — we want the
notifier to be schema-tolerant: a new event type added in NT shouldn't crash
the bot, it should just show up in Discord with whatever JSON arrived. So we
parse opportunistically: try msgpack then JSON, fall back to a hex preview.
"""

from __future__ import annotations

import json
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
    "OrderExpired": 0xE67E22,
    "OrderModified": 0x9B59B6,
    "PositionOpened": 0x1ABC9C,
    "PositionChanged": 0x16A085,
    "PositionClosed": 0xE74C3C,
    "AccountState": 0x34495E,
    "Signal": 0xE91E63,
    "ComponentStateChanged": 0x7F8C8D,
}


def render_embed(env: EventEnvelope, *, source_pod: str | None = None) -> discord.Embed:
    body = env.body if isinstance(env.body, dict) else {"raw": env.body}
    event_type = _guess_event_type(env.topic, body)

    title = f"[{event_type}]"
    description_lines: list[str] = []

    if env.topic.startswith("events.order."):
        description_lines.append(_fmt_order(body))
    elif env.topic.startswith("events.position."):
        description_lines.append(_fmt_position(body))
    elif env.topic.startswith("events.account."):
        description_lines.append(_fmt_account(body))
    elif env.topic.startswith("data.Signal"):
        description_lines.append(_fmt_signal(body))
    elif env.topic.endswith("component_state_changed") or "component_state_changed" in env.topic:
        description_lines.append(_fmt_component(body))
    else:
        description_lines.append(f"```json\n{json.dumps(body, indent=2)[:1500]}\n```")

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


def _guess_event_type(topic: str, body: dict[str, Any]) -> str:
    if isinstance(body, dict) and (t := body.get("type")):
        return str(t)
    return topic.rsplit(".", 1)[-1]


def _fmt_order(body: dict[str, Any]) -> str:
    keys = ("strategy_id", "instrument_id", "side", "order_type", "quantity", "price", "last_qty", "last_px", "status")
    rows = [f"**{k}**: `{body[k]}`" for k in keys if k in body]
    if "client_order_id" in body:
        rows.append(f"**id**: `{body['client_order_id']}`")
    return "\n".join(rows) or f"```json\n{json.dumps(body, indent=2)[:1200]}\n```"


def _fmt_position(body: dict[str, Any]) -> str:
    keys = ("strategy_id", "instrument_id", "side", "quantity", "avg_px_open", "avg_px_close", "realized_pnl", "unrealized_pnl")
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
    return (
        f"**component**: `{body.get('component_id', '?')}` "
        f"({body.get('component_type', '?')})\n"
        f"**state**: `{body.get('state', '?')}`"
    )
