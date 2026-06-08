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
from collections import OrderedDict
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


# ─── order fill-progress aggregation (IOC / partial fills) ───────────────────


def _to_float(value: Any) -> float | None:
    """Best-effort float of an NT serialized quantity/price (str or number).

    NT serializes ``Quantity`` / ``Price`` to *strings* (``"8186.6"``) over the
    wire — that's why we parse rather than assume numeric. Returns ``None`` on
    anything unparseable so callers can degrade gracefully instead of crashing.
    """

    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class OrderProgress:
    """Aggregated fill state for one ``client_order_id``.

    Built up across the order's event stream because no single NT event carries
    it: OrderInitialized has the total (``quantity``) but no fills, OrderFilled
    has only ``last_qty`` (this fill), OrderCanceled is nearly empty. We stitch
    them together so the embed can show 'filled X / total Y (Z%)' and the
    unfilled remainder on cancel.
    """

    qty_total: float | None = None
    qty_filled: float = 0.0
    time_in_force: str | None = None
    # Captured off OrderInitialized so the near-empty terminal events
    # (OrderCanceled especially) can still show direction + type — NT strips
    # those fields from the cancel/expire events, but the operator still wants
    # to know "which order, which way, what kind" on the event they actually see.
    order_side: str | None = None
    order_type: str | None = None

    @property
    def pct(self) -> float | None:
        if not self.qty_total:
            return None
        return self.qty_filled / self.qty_total * 100.0

    @property
    def remainder(self) -> float | None:
        if self.qty_total is None:
            return None
        return self.qty_total - self.qty_filled


# Terminal order events — once one of these renders, the slot can be released:
# the order will emit no further events. (A fully-filled OrderFilled is also
# terminal; handled explicitly in ``forget_if_terminal``.)
_TERMINAL_ORDER_EVENTS = frozenset(
    {"OrderCanceled", "OrderRejected", "OrderDenied", "OrderExpired"},
)


class OrderProgressTracker:
    """Remembers per-order fill progress so embeds can show IOC/partial results.

    Stateful by necessity (NT spreads the total and the fills across separate
    events), but the state is small, in-memory, and disposable: a notifier
    restart simply starts fresh — new orders get tracked, in-flight ones lose
    their pre-restart total and fall back to showing just ``last_qty``. That's
    an accepted trade-off (no persistence) since the alternative is a database
    for cosmetic Discord progress lines.

    Bounded by an LRU cap so a runaway order stream (or a crash between the last
    fill and the terminal event, which would skip ``forget``) can't leak memory.
    """

    def __init__(self, max_orders: int = 2_000) -> None:
        self._orders: OrderedDict[str, OrderProgress] = OrderedDict()
        self._max_orders = max_orders

    def observe(self, body: Any) -> None:
        """Feed one order-event body in. Safe to call for every order event —
        non-order bodies and bodies without a client_order_id are ignored.

        Must be called for the *suppressed* mid-flight events too (especially
        OrderInitialized — the only event carrying the total quantity), so the
        denoise gate must run AFTER this, never before.
        """

        if not isinstance(body, dict):
            return
        coid = body.get("client_order_id")
        if not coid:
            return
        event_type = body.get("type")
        prog = self._orders.get(coid)
        if prog is None:
            prog = OrderProgress()
            self._orders[coid] = prog
        self._orders.move_to_end(coid)

        # Capture identity fields whenever an event carries them (Initialized
        # has all of them; fills carry side/type too). Latch — never overwrite a
        # known value with a later event that happens to omit it.
        if prog.order_side is None and (side := body.get("order_side")):
            prog.order_side = str(side)
        if prog.order_type is None and (otype := body.get("order_type")):
            prog.order_type = str(otype)

        if event_type == "OrderInitialized":
            total = _to_float(body.get("quantity"))
            if total is not None:
                prog.qty_total = total
            tif = body.get("time_in_force")
            if tif:
                prog.time_in_force = str(tif)
        elif event_type == "OrderFilled":
            last = _to_float(body.get("last_qty"))
            if last is not None:
                prog.qty_filled += last

        # Evict oldest beyond the cap (move_to_end above keeps live orders warm).
        while len(self._orders) > self._max_orders:
            self._orders.popitem(last=False)

    def snapshot(self, client_order_id: str) -> OrderProgress | None:
        return self._orders.get(client_order_id)

    def forget_if_terminal(self, body: Any) -> None:
        """Release the slot once the order reaches a terminal state.

        Called after the terminal event's embed has been rendered (the renderer
        still needs the snapshot to show final progress), so the read happens
        before the free.
        """

        if not isinstance(body, dict):
            return
        coid = body.get("client_order_id")
        if not coid:
            return
        event_type = body.get("type")
        terminal = event_type in _TERMINAL_ORDER_EVENTS
        if event_type == "OrderFilled":
            prog = self._orders.get(coid)
            if prog and prog.remainder is not None and prog.remainder <= 0:
                terminal = True
        if terminal:
            self._orders.pop(coid, None)

    def size(self) -> int:
        return len(self._orders)


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


def render_embed(
    env: EventEnvelope,
    *,
    source_pod: str | None = None,
    tracker: OrderProgressTracker | None = None,
) -> discord.Embed:
    body = env.body if isinstance(env.body, dict) else {"raw": env.body}
    event_type = _guess_event_type(env.topic, body)
    progress = None
    if tracker is not None and isinstance(body, dict):
        coid = body.get("client_order_id")
        if coid:
            progress = tracker.snapshot(coid)

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
        description_lines.append(_fmt_order(body, progress=progress))
    elif env.topic.startswith("events.position."):
        description_lines.append(_fmt_position(body))
    elif env.topic.startswith("events.account."):
        description_lines.append(_fmt_account(body))
    elif env.topic.startswith("data.Signal"):
        description_lines.append(_fmt_signal(body))
    else:
        description_lines.append(
            f"```json\n{json.dumps(_drop_noisy_keys(body), indent=2)[:1500]}\n```"
        )

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


def _order_side(body: dict[str, Any], progress: OrderProgress | None = None) -> str | None:
    # Order events serialize the direction as ``order_side`` (NT to_dict);
    # ``side`` is the position-event spelling. Fall back to it so a future
    # rename or a position-shaped body still surfaces a direction. Last resort:
    # the tracker, so a near-empty OrderCanceled still shows BUY/SELL.
    side = body.get("order_side") or body.get("side")
    if not side and progress:
        side = progress.order_side
    return str(side) if side else None


def _order_options(body: dict[str, Any]) -> dict[str, Any]:
    # NT nests the type-specific params (price for LIMIT, trigger_price /
    # trigger_type for STOP_*) inside an ``options`` dict on OrderInitialized —
    # they are NOT top-level. Return it (or {}) so callers can drill safely.
    opts = body.get("options")
    return opts if isinstance(opts, dict) else {}


def _fmt_instruction(
    body: dict[str, Any],
    *,
    progress: OrderProgress | None = None,
) -> str | None:
    """Build the 指令 line: order_type · TIF · post_only · reduce_only.

    These flags live on OrderInitialized only (NT 1.227.0 to_dict). Since the
    denoise gate suppresses OrderInitialized, the TIF would vanish from the
    *kept* events (Filled/Canceled) — exactly the IOC-vs-GTC visibility the
    operator asked for. So we backfill ``time_in_force`` from the tracker (it
    captured it off the suppressed Initialized) when the event body lacks it.
    post_only / reduce_only aren't tracked (they're on Initialized only and
    less critical post-fill) — shown when present, omitted otherwise.
    """

    parts: list[str] = []
    order_type = body.get("order_type") or (progress.order_type if progress else None)
    if order_type:
        parts.append(str(order_type))
    tif = body.get("time_in_force") or (progress.time_in_force if progress else None)
    if tif:
        parts.append(str(tif))
    if body.get("post_only"):
        parts.append("只挂单")
    if body.get("reduce_only"):
        parts.append("平仓")
    if not parts:
        return None
    return "**指令**: " + " · ".join(f"`{p}`" for p in parts)


def _fmt_prices(body: dict[str, Any]) -> list[str]:
    """Limit price + stop trigger, drilling into ``options`` where NT hides them."""

    opts = _order_options(body)
    rows: list[str] = []
    # Limit price: top-level for filled events, options.price on Initialized.
    price = body.get("price") or opts.get("price")
    if price is not None:
        rows.append(f"**限价**: `{price}`")
    trigger = opts.get("trigger_price")
    if trigger is not None:
        trigger_type = opts.get("trigger_type")
        suffix = f" ({trigger_type})" if trigger_type else ""
        rows.append(f"**止损触发**: `{trigger}`{suffix}")
    return rows


def _fmt_progress(progress: OrderProgress | None, event_type: str | None) -> str | None:
    """Render cumulative fill progress for OrderFilled / OrderCanceled.

    NT's single events can't express this (Filled has only ``last_qty``, Cancel
    is empty) — the tracker stitched it together. Shown only when we actually
    know the total, else we'd print a misleading 'X/None'.

    Gated to fill/cancel events: an OrderTriggered or other kept node with zero
    fills would otherwise render a noisy '0 / N (0%)'. A cancel with zero fills
    still shows (the whole order was canceled — that's information).
    """

    if progress is None or progress.qty_total is None:
        return None
    if event_type == "OrderCanceled":
        pct = progress.pct
        pct_str = f" ({pct:.1f}%)" if pct is not None else ""
        filled = _fmt_qty(progress.qty_filled)
        total = _fmt_qty(progress.qty_total)
        remainder = progress.remainder
        rem_str = _fmt_qty(remainder) if remainder is not None else "?"
        return f"**最终成交**: `{filled}` / `{total}`{pct_str}\n**未成交(已撤)**: `{rem_str}`"
    if event_type == "OrderFilled" and progress.qty_filled > 0:
        pct = progress.pct
        pct_str = f" ({pct:.1f}%)" if pct is not None else ""
        filled = _fmt_qty(progress.qty_filled)
        total = _fmt_qty(progress.qty_total)
        return f"**累计成交**: `{filled}` / `{total}`{pct_str}"
    return None


def _fmt_qty(value: float) -> str:
    # Trim the float so 8186.6 doesn't render as 8186.599999999999; keep it
    # integer-clean when whole (4000.0 → 4000).
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def _fmt_order(body: dict[str, Any], *, progress: OrderProgress | None = None) -> str:
    rows: list[str] = []
    for k in ("strategy_id", "instrument_id"):
        if k in body:
            rows.append(f"**{k}**: `{body[k]}`")
    if side := _order_side(body, progress):
        rows.append(f"**方向**: `{side}`")
    if instruction := _fmt_instruction(body, progress=progress):
        rows.append(instruction)
    rows.extend(_fmt_prices(body))
    for k in ("quantity", "last_qty", "last_px", "status"):
        if k in body:
            rows.append(f"**{k}**: `{body[k]}`")
    if progress_line := _fmt_progress(progress, body.get("type")):
        rows.append(progress_line)
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
    pnl_block = _fmt_account_pnl(body.get("account_pnl"))

    header = f"**策略**: `{strategy_id}` · **持仓**: `{row_count}` 条"
    if not csv_text or row_count == 0:
        return f"{header}\n_当前无持仓_{pnl_block}"

    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        return f"{header}\n_无法解析持仓数据_{pnl_block}"

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
        return f"{header}\n_(列名不识别，原始 CSV 见 stream)_{pnl_block}"

    headers = [zh for zh, _ in indices]
    body_rows = [[r[i] if i < len(r) else "" for _, i in indices] for r in data_rows]
    truncated = len(body_rows) > _POSITIONS_TABLE_MAX_ROWS
    body_rows = body_rows[:_POSITIONS_TABLE_MAX_ROWS]

    table = _markdown_table(headers, body_rows)
    tail = f"\n_…还有 {len(data_rows) - _POSITIONS_TABLE_MAX_ROWS} 条未显示_" if truncated else ""
    return f"{header}\n```\n{table}\n```{tail}{pnl_block}"


def _fmt_account_pnl(account_pnl: Any) -> str:
    """Render the account-level PnL summary appended to a positions report.

    ``account_pnl`` is ``{venue: {realized|unrealized|net_exposure: {ccy: str}}}``
    (see :func:`tinohelm.reporting_actor._account_pnl`). Returns an empty string
    when absent so the positions table renders unchanged — this keeps the /pnl
    view strictly additive to the existing /positions reply.
    """

    if not isinstance(account_pnl, dict) or not account_pnl:
        return ""

    def _amounts(by_ccy: Any) -> str:
        if not isinstance(by_ccy, dict) or not by_ccy:
            return "`0`"
        return " · ".join(f"`{v}`" for v in by_ccy.values())

    lines = ["\n── 账户汇总 ──"]
    for venue, blocks in account_pnl.items():
        if not isinstance(blocks, dict):
            continue
        lines.append(f"**{venue}**")
        lines.append(f"已实现: {_amounts(blocks.get('realized'))}")
        lines.append(f"浮动: {_amounts(blocks.get('unrealized'))}")
        lines.append(f"净敞口: {_amounts(blocks.get('net_exposure'))}")
    return "\n".join(lines)


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
