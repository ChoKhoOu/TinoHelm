"""Convert NT events / bytes payloads into Discord embeds.

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


def render_embed(env: EventEnvelope, *, source_pod: str | None = None) -> discord.Embed:
    body = env.body if isinstance(env.body, dict) else {"raw": env.body}
    event_type = _guess_event_type(env.topic, body)

    title = f"[{event_type}]"
    description_lines: list[str] = []

    # Route on event_type first (the body's own self-description), then fall
    # back to topic prefixes for payloads that don't carry one. This matters
    # for in-process events on ``events.system.*`` where the topic only tells
    # us "system" but the body says "ComponentStateChanged".
    if event_type == "ComponentStateChanged":
        description_lines.append(_fmt_component(body))
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
