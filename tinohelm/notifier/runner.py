"""Notifier pod entry point.

A minimal :class:`TradingNode` (no exec clients), wired up so that:

* The strategy registry is built at startup from the ``tinohelm:announce``
  Redis stream (see :func:`load_announce_history`). New pods are picked up
  on the fly via ``XREAD $`` (see :func:`read_new_announces`); a 60s
  fallback also scans ``tinohelm:control:*`` for pods that came up
  without an announce.
* :class:`NotifierActor` subscribes to NT's wildcard event topics
  (``events.order.*`` / ``events.position.*`` / ``events.account.*`` /
  ``data.Signal*`` / ``events.system.*``) and routes each event to one
  of three Discord channels:

  - per-strategy events (``events.order.*`` / ``events.position.*`` /
    ``data.Signal*``) → sandbox or live based on the registry
  - cross-cutting topics with no strategy scope (``events.system.*``,
    ``events.account.*``, ``tinohelm.*`` such as the daily summary) →
    logging channel, so trade-flow channels stay clean
* A ``discord.py`` client provides slash commands (``/pause`` etc.)
  that are channel-scoped: a command issued from #live cannot move a
  sandbox strategy, and vice versa; the logging channel is read-only
  and rejects all commands (see :func:`validate_command_channel`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import UTC, datetime
from datetime import time as dtime
from typing import Any

import discord
import nautilus_trader
import redis
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.live.node import TradingNode

from tinohelm import COMMAND_TOPIC_PREFIX, control_stream_key
from tinohelm.config import TinoNotifierFile, build_notifier_node_config
from tinohelm.notifier.handlers import envelope_for, render_embed
from tinohelm.strategy_runner import ANNOUNCE_STREAM, TINO_PROTOCOL_VERSION

logger = logging.getLogger("tinohelm.notifier")


# ─── strategy registry (announce stream → mode lookup) ─────────────────────


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def read_new_announces(
    redis_client: Any,
    cursor: str,
    *,
    block_ms: int = 1_000,
) -> tuple[list[tuple[str, str, str, str]], str]:
    """Read announces written after ``cursor``; return ``(entries, new_cursor)``.

    Each entry is ``(strategy_id, mode, nt_version, tino_protocol_version)``.
    Pre-versioned announces (older pods) get ``""`` for the missing version
    fields so the notifier loop stays backward-compatible — it can decide to
    log a one-shot warning rather than crash.

    Use ``cursor="$"`` on the first call to skip pre-existing history (the
    notifier already pulled that via :func:`load_announce_history`).
    Subsequent calls pass the returned cursor to advance.

    Returns an empty list when ``XREAD`` times out — the loop simply calls
    again. ``block_ms`` is exposed mainly so tests don't hang.
    """

    response = redis_client.xread({ANNOUNCE_STREAM: cursor}, block=block_ms)
    if not response:
        return [], cursor
    new_entries: list[tuple[str, str, str, str]] = []
    new_cursor = cursor
    for _stream_name, batch in response:
        for entry_id, fields in batch:
            sid = fields.get(b"strategy_id")
            mode = fields.get(b"mode")
            if sid is not None and mode is not None:
                nt_v = fields.get(b"nt_version", b"")
                proto_v = fields.get(b"tino_protocol_version", b"")
                new_entries.append(
                    (
                        _decode(sid),
                        _decode(mode),
                        _decode(nt_v),
                        _decode(proto_v),
                    ),
                )
            new_cursor = _decode(entry_id)
    return new_entries, new_cursor


def extract_strategy_id_from_body(body: Any) -> str | None:
    """Pull a strategy_id out of a decoded NT event body.

    NT serializes ``"strategy_id"`` as a top-level field on every
    ``OrderEvent``, ``PositionEvent`` and published ``Signal``. Cross-cutting
    events (``AccountState``, ``ComponentStateChanged``) don't carry one,
    and ``parse_payload`` may also fall back to a string/hex preview when
    decoding fails — in either case we return ``None`` so the caller routes
    to the logging channel.

    We can't pull this off the topic the way an outsider might assume, because
    NT's ``MessageBus.subscribe(topic=pattern, handler=...)`` only passes the
    decoded message to the handler; the topic itself never reaches us.
    """

    if not isinstance(body, dict):
        return None
    sid = body.get("strategy_id")
    if isinstance(sid, str) and sid:
        return sid
    return None


def detect_protocol_drift(
    announces: list[tuple[str, str, str, str]],
    *,
    expected_proto: str,
    expected_nt_version: str,
) -> list[Any]:
    """Return one envelope per pod whose announce versions disagree with us.

    Two ways a pod is considered drifting:
      * its ``tino_protocol_version`` differs from ours (or is empty — that
        means a pre-versioned pod from before the handshake was added);
      * its ``nt_version`` differs (NT field-level schema changes can
        silently mis-decode events even when the announce wire is fine).

    Forward-compat by design: this is *information*, not enforcement. The
    notifier still routes events normally; the operator decides whether to
    upgrade. The envelopes use ``topic="tinohelm.protocol_mismatch"`` so
    NotifierActor's existing ``tinohelm.*`` route lands them in #logging.
    """

    drifted: list[Any] = []
    for sid, _mode, nt_v, proto_v in announces:
        proto_skew = proto_v != expected_proto
        nt_skew = nt_v != expected_nt_version
        if not (proto_skew or nt_skew):
            continue
        drifted.append(
            envelope_for(
                "tinohelm.protocol_mismatch",
                {
                    "strategy_id": sid,
                    "pod_proto": proto_v,
                    "notifier_proto": expected_proto,
                    "pod_nt_version": nt_v,
                    "notifier_nt_version": expected_nt_version,
                },
            ),
        )
    return drifted


class ChannelMismatch(Exception):
    """Raised when a slash command is issued from the wrong Discord channel."""


def validate_command_channel(
    strategy_id: str,
    *,
    channel_mode: str,
    registry: dict[str, str],
) -> None:
    """Reject slash commands that cross the sandbox/live boundary.

    ``channel_mode`` is the mode the channel represents (``"live"``,
    ``"sandbox"``, or ``"logging"``). The logging channel is read-only;
    every command from there is rejected regardless of strategy mode.
    Unknown strategies are treated as sandbox to match
    :func:`route_channel`'s default.
    """

    if channel_mode == "logging":
        raise ChannelMismatch(
            f"the logging channel is read-only; send commands from the "
            f"sandbox or live channel (command for {strategy_id} was sent "
            f"from logging)",
        )
    strategy_mode = registry.get(strategy_id, "sandbox")
    effective = "live" if strategy_mode == "live" else "sandbox"
    if effective != channel_mode:
        raise ChannelMismatch(
            f"{strategy_id} is {effective}; switch to the {effective} channel "
            f"(command was sent from {channel_mode})",
        )


def route_channel(
    strategy_id: str,
    registry: dict[str, str],
    *,
    sandbox: int,
    live: int,
    logging_channel_id: int,
) -> int:
    """Pick the Discord channel id for an event from ``strategy_id``.

    Empty ``strategy_id`` means the topic carries no strategy scope
    (events.system.*, events.account.*, tinohelm.*) — those go to the
    logging channel so trade-flow channels stay clean.

    For scoped topics, defaults to ``sandbox`` when the strategy isn't
    in the registry yet or when ``mode`` isn't recognized.

    The kwarg is named ``logging_channel_id`` (not ``logging``) so it
    can't shadow the module-level ``import logging`` if someone later
    adds a ``logging.debug(...)`` call in here.
    """

    if not strategy_id:
        return logging_channel_id
    return live if registry.get(strategy_id) == "live" else sandbox


def apply_fallback_scan(redis_client: Any, registry: dict[str, str]) -> None:
    """Scan ``tinohelm:control:*`` and add any unknown strategies to ``registry``.

    The :data:`ANNOUNCE_STREAM` is the source of truth for ``mode``, but a
    pod can come up under odd conditions (Redis flush wiped announces but
    not the control stream that the pod recreates on each command). For
    those, default the mode to ``"sandbox"`` — operators flip a strategy
    from sandbox to live consciously, never the reverse, so this default
    is the safer side of the bet.
    """

    for key in redis_client.keys("tinohelm:control:*"):
        decoded = key.decode() if isinstance(key, bytes) else str(key)
        sid = decoded.rsplit(":", 1)[-1]
        registry.setdefault(sid, "sandbox")


def load_announce_history(redis_client: Any) -> tuple[dict[str, str], str]:
    """Replay :data:`ANNOUNCE_STREAM`; return ``(registry, last_entry_id)``.

    Used at notifier startup to recover the channel-routing registry so a
    notifier restart doesn't lose context until each pod re-announces.
    Newer entries win on duplicate ``strategy_id``.

    The returned cursor seeds :func:`read_new_announces` so the notifier
    loop only sees entries written *after* startup.
    """

    registry: dict[str, str] = {}
    last_id = "0-0"
    for entry_id, fields in redis_client.xrange(ANNOUNCE_STREAM):
        sid = fields.get(b"strategy_id")
        mode = fields.get(b"mode")
        last_id = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
        if sid is None or mode is None:
            continue
        registry[sid.decode() if isinstance(sid, bytes) else str(sid)] = (
            mode.decode() if isinstance(mode, bytes) else str(mode)
        )
    return registry, last_id


# ─── notifier actor ──────────────────────────────────────────────────────────


class NotifierActorConfig(ActorConfig, frozen=True):
    sandbox_channel_id: int
    live_channel_id: int
    logging_channel_id: int


class NotifierActor(Actor):
    """NT actor that funnels msgbus events into a Discord client.

    Events get routed to either the sandbox, live, or logging channel
    based on the shared ``registry`` mapping (built from the announce
    stream). Topics without a strategy scope (``events.account.*`` /
    ``events.system.*`` / ``tinohelm.*``) go to the logging channel so
    they don't drown out trade-flow signals in sandbox/live.
    """

    SUBSCRIBE_PATTERNS = (
        "events.order.*",
        "events.position.*",
        "events.account.*",
        "data.Signal*",
        "events.system.*",
        # Operational chatter (ReportingActor positions snapshots, daily
        # summary, protocol-mismatch envelopes that are published rather
        # than enqueued directly). Always logging — never trade flow.
        "tinohelm.*",
    )

    def __init__(
        self,
        config: NotifierActorConfig,
        *,
        forwarder: DiscordForwarder,
        registry: dict[str, str],
    ) -> None:
        super().__init__(config=config)
        self._forwarder = forwarder
        self._registry = registry
        self._sandbox_channel_id = config.sandbox_channel_id
        self._live_channel_id = config.live_channel_id
        self._logging_channel_id = config.logging_channel_id

    def on_start(self) -> None:
        for pattern in self.SUBSCRIBE_PATTERNS:
            self.msgbus.subscribe(topic=pattern, handler=self._make_handler(pattern))
        self.log.info(
            f"NotifierActor subscribed to {self.SUBSCRIBE_PATTERNS} -> "
            f"sandbox={self._sandbox_channel_id} live={self._live_channel_id} "
            f"logging={self._logging_channel_id}",
        )

    def _make_handler(self, pattern: str):
        def _handle(msg: Any) -> None:
            try:
                # NT's msgbus only passes the message body; the resolved
                # topic never reaches us. We label the envelope with the
                # subscription pattern (best we have for display/debug) and
                # pull the strategy_id off the body where NT puts it.
                env = envelope_for(pattern, msg)
                # tinohelm.* is operational chatter (reports, summaries,
                # drift warnings). Even if the body carries a strategy_id
                # — the positions report does — these belong in logging,
                # not the strategy's sandbox/live channel.
                if pattern.startswith("tinohelm."):
                    strategy_id = ""
                else:
                    strategy_id = extract_strategy_id_from_body(env.body) or ""
                channel_id = route_channel(
                    strategy_id,
                    self._registry,
                    sandbox=self._sandbox_channel_id,
                    live=self._live_channel_id,
                    logging_channel_id=self._logging_channel_id,
                )
                self._forwarder.enqueue(env, channel_id=channel_id)
            except Exception as exc:  # pragma: no cover — defensive
                self.log.error(f"NotifierActor failed on {pattern}: {exc}")

        return _handle


# ─── discord forwarder + slash commands ──────────────────────────────────────


class DiscordForwarder:
    """Cross-thread bridge between NT (sync handlers) and discord.py (async).

    Caller decides which channel id each event goes to (the routing
    decision lives in :class:`NotifierActor` so this class stays
    Discord-only). Rate-limit is per-channel so a noisy live channel
    doesn't starve sandbox.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client: discord.Client | None,
        rate_limit: int = 5,
    ) -> None:
        self._loop = loop
        self._client = client
        self._rate_limit = rate_limit
        self._semaphores: dict[int, asyncio.Semaphore] = {}
        # Strategy id → list of futures awaiting the next ``tinohelm.report.
        # positions`` envelope from that pod. Used by the ``/positions``
        # slash command to turn the existing fire-and-forget snapshot into
        # a request/response. Multiple operators may run /positions at the
        # same time — we keep a list so each gets satisfied.
        self._position_listeners: dict[str, list[asyncio.Future]] = {}

    def attach_client(self, client: discord.Client) -> None:
        """Late-bind the discord client.

        We construct the forwarder before the client because ``/positions``
        needs a forwarder reference at command-tree build time. The client
        is wired in here after :func:`_build_discord_client` returns.
        """

        self._client = client

    def _semaphore_for(self, channel_id: int) -> asyncio.Semaphore:
        sem = self._semaphores.get(channel_id)
        if sem is None:
            sem = asyncio.Semaphore(self._rate_limit)
            self._semaphores[channel_id] = sem
        return sem

    def watch_positions_report(self, strategy_id: str) -> asyncio.Future:
        """Return a future that resolves the next time this pod's snapshot
        passes through :meth:`enqueue`.

        Must be called from the asyncio thread because we attach the future
        to ``self._loop``. Each future is single-use; after it resolves the
        caller should drop it.
        """

        future: asyncio.Future = self._loop.create_future()
        self._position_listeners.setdefault(strategy_id, []).append(future)
        return future

    def _dispatch_position_listeners(self, env) -> None:
        # Called from the loop thread (we hop over via call_soon_threadsafe
        # in :meth:`enqueue`). Pulling the whole list avoids an awkward
        # mid-iteration mutation if the listener fires another /positions
        # synchronously inside its callback.
        body = env.body if isinstance(env.body, dict) else None
        if not body:
            return
        sid = body.get("strategy_id")
        if not sid:
            return
        for fut in self._position_listeners.pop(sid, []):
            if not fut.done():
                fut.set_result(env)

    def enqueue(self, env, *, channel_id: int) -> None:
        # NT handlers run on the asyncio loop already (ActorExecutor), but to
        # stay safe we go through ``run_coroutine_threadsafe`` — it's a no-op
        # when called from the same loop. We check the loop is open *before*
        # building the coroutine so we don't leak an un-awaited coroutine
        # object on the shutdown path.
        if self._loop.is_closed():
            logger.warning("event loop closed; dropping event")
            return
        # Snapshot envelopes also satisfy any pending /positions requests.
        # We do this on the loop thread so future state stays single-threaded.
        if getattr(env, "topic", None) == "tinohelm.report.positions":
            self._loop.call_soon_threadsafe(self._dispatch_position_listeners, env)
        try:
            asyncio.run_coroutine_threadsafe(self._send(env, channel_id), self._loop)
        except RuntimeError:
            logger.warning("event loop closed; dropping event")

    async def _send(self, env, channel_id: int) -> None:
        if self._client is None:
            return
        channel = self._client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self._client.fetch_channel(channel_id)
            except discord.HTTPException as exc:
                logger.error(f"discord fetch_channel({channel_id}) failed: {exc}")
                return
        async with self._semaphore_for(channel_id):
            try:
                await channel.send(embed=render_embed(env))
            except discord.HTTPException as exc:
                logger.warning(f"discord send failed: {exc}")


def _build_discord_client(
    token: str,
    guild_id: int | None,
    notifier_cfg: TinoNotifierFile,
    redis_client: redis.Redis,
    sandbox_channel_id: int,
    live_channel_id: int,
    logging_channel_id: int,
    registry: dict[str, str],
    forwarder: DiscordForwarder,
) -> tuple[discord.Client, discord.app_commands.CommandTree]:
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = discord.app_commands.CommandTree(client)
    guild_obj = discord.Object(id=guild_id) if guild_id else None

    def _channel_mode(channel_id: int | None) -> str | None:
        if channel_id == live_channel_id:
            return "live"
        if channel_id == sandbox_channel_id:
            return "sandbox"
        if channel_id == logging_channel_id:
            return "logging"
        return None

    def _publish(strategy: str, action: str, reason: str | None = None) -> str:
        topic = f"{COMMAND_TOPIC_PREFIX}.{strategy}.{action}"
        body = {"action": action, "ts": int(time.time() * 1_000)}
        if reason:
            body["reason"] = reason
        redis_client.xadd(
            control_stream_key(strategy),
            {"topic": topic, "payload": json.dumps(body).encode("utf-8")},
        )
        return topic

    async def _command(
        interaction: discord.Interaction,
        strategy: str,
        action: str,
        reason: str | None = None,
    ) -> None:
        channel_mode = _channel_mode(interaction.channel_id)
        if channel_mode is None:
            await interaction.response.send_message(
                "this channel is not registered as sandbox, live, or logging",
                ephemeral=True,
            )
            return
        try:
            validate_command_channel(strategy, channel_mode=channel_mode, registry=registry)
        except ChannelMismatch as exc:
            await interaction.response.send_message(f"rejected: {exc}", ephemeral=True)
            return
        topic = await asyncio.to_thread(_publish, strategy, action, reason)
        await interaction.response.send_message(f"sent `{topic}`", ephemeral=True)

    def _strategies_for_channel(channel_mode: str | None) -> list[str]:
        """Pick the strategy ids a /positions fan-out should target.

        - From the live channel: only live strategies (we never want to
          surface sandbox flow into a production audit channel).
        - From the sandbox channel: only sandbox strategies.
        - From the logging channel (or an unrecognised channel): every
          strategy in the registry — logging is the read-only overview.
        """

        if channel_mode == "live":
            return [s for s, m in registry.items() if m == "live"]
        if channel_mode == "sandbox":
            return [s for s, m in registry.items() if m != "live"]
        return list(registry.keys())

    async def _positions(
        interaction: discord.Interaction,
        strategy: str | None,
    ) -> None:
        # Defer because we wait up to 120s for the snapshot reply. Discord
        # otherwise marks the interaction as failed at 3s.
        await interaction.response.defer(ephemeral=True)
        channel_mode = _channel_mode(interaction.channel_id)
        if channel_mode is None:
            await interaction.followup.send(
                "this channel is not registered as sandbox, live, or logging",
                ephemeral=True,
            )
            return

        if strategy:
            try:
                validate_command_channel(strategy, channel_mode=channel_mode, registry=registry)
            except ChannelMismatch as exc:
                await interaction.followup.send(f"rejected: {exc}", ephemeral=True)
                return
            targets = [strategy]
        else:
            targets = _strategies_for_channel(channel_mode)
            if not targets:
                await interaction.followup.send(
                    "no strategies known for this channel yet",
                    ephemeral=True,
                )
                return

        # Subscribe before publish — otherwise a fast pod could reply before
        # the future is registered and we'd miss it.
        futures = {sid: forwarder.watch_positions_report(sid) for sid in targets}
        for sid in targets:
            await asyncio.to_thread(_publish, sid, "report")

        try:
            done = await asyncio.wait_for(
                asyncio.gather(*futures.values(), return_exceptions=True),
                timeout=120,
            )
        except TimeoutError:
            done = [
                fut.result() if fut.done() and not fut.cancelled() else None
                for fut in futures.values()
            ]
            for fut in futures.values():
                if not fut.done():
                    fut.cancel()

        replied: list[str] = []
        missing: list[str] = []
        for sid, env in zip(futures.keys(), done, strict=True):
            if env is None or isinstance(env, BaseException):
                missing.append(sid)
                continue
            replied.append(sid)

        head_lines = [f"已收到 {len(replied)}/{len(targets)} 个策略的快照（已发送到 #logging）"]
        if missing:
            head_lines.append(f"超时未响应: {', '.join(f'`{s}`' for s in missing)}")
        await interaction.followup.send("\n".join(head_lines), ephemeral=True)

    @tree.command(name="pause", description="Stop a strategy without exiting positions")
    @discord.app_commands.describe(strategy="Strategy ID, e.g. FOO-001")
    async def pause_cmd(interaction: discord.Interaction, strategy: str) -> None:
        await _command(interaction, strategy, "pause")

    @tree.command(name="resume", description="Restart a previously paused strategy")
    async def resume_cmd(interaction: discord.Interaction, strategy: str) -> None:
        await _command(interaction, strategy, "resume")

    @tree.command(name="flatten", description="Market-exit and stop a strategy")
    async def flatten_cmd(
        interaction: discord.Interaction,
        strategy: str,
        reason: str | None = None,
    ) -> None:
        await _command(interaction, strategy, "flatten", reason)

    @tree.command(name="ping", description="Round-trip check via the bridge actor")
    async def ping_cmd(interaction: discord.Interaction, strategy: str) -> None:
        await _command(interaction, strategy, "ping")

    @tree.command(
        name="positions",
        description="Snapshot current positions (one strategy or all visible from this channel)",
    )
    @discord.app_commands.describe(strategy="Strategy ID; omit to fan-out to every visible strategy")
    async def positions_cmd(
        interaction: discord.Interaction,
        strategy: str | None = None,
    ) -> None:
        await _positions(interaction, strategy)

    @tree.command(name="status", description="List known TinoHelm streams in Redis")
    async def status_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        channel_mode = _channel_mode(interaction.channel_id)
        keys: list[str] = []
        for pattern in ("trader-*:stream:events.*", "tinohelm:control:*"):
            for key in await asyncio.to_thread(redis_client.keys, pattern):
                keys.append(key.decode() if isinstance(key, bytes) else str(key))
        # Narrow by channel mode when the channel maps to a strategy mode.
        # The logging channel doesn't represent a strategy mode, so we list
        # everything there to give operators a single read-only overview.
        if channel_mode in ("sandbox", "live"):
            keys = [
                k for k in keys if registry.get(k.rsplit(":", 1)[-1], "sandbox") == channel_mode
            ]
        if not keys:
            await interaction.followup.send("no streams found", ephemeral=True)
            return
        lines: list[str] = []
        for key in sorted(keys):
            length = await asyncio.to_thread(redis_client.xlen, key)
            lines.append(f"`{key}` XLEN={length}")
        await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)

    @client.event
    async def on_ready() -> None:
        if guild_obj:
            await tree.sync(guild=guild_obj)
        else:
            await tree.sync()
        logger.info(
            f"discord bot ready as {client.user} "
            f"sandbox={sandbox_channel_id} live={live_channel_id} "
            f"logging={logging_channel_id}",
        )

    return client, tree


# ─── daily summary ────────────────────────────────────────────────────────────


def _parse_hh_mm(value: str) -> dtime:
    h, m = value.split(":", 1)
    return dtime(int(h), int(m), tzinfo=UTC)


def build_daily_summary(
    *,
    cache: Any,
    redis_stream_count: int,
    logging_channel_id: int,
) -> tuple[Any, int]:
    """Build the daily-summary envelope and pick its target channel.

    Returns ``(envelope, channel_id)``. Pure function so the loop stays
    thin and the routing decision is testable without asyncio.

    The summary always lands on the logging channel — it's operational
    information, not trade flow, so mirroring it to sandbox/live would
    just dilute the trade-event signal those channels exist for.
    """

    from tinohelm.notifier.handlers import envelope_for as _envelope_for

    summary: dict[str, Any] = {}
    if cache is not None:
        summary["positions_open"] = len(cache.positions_open() or [])
        summary["positions_closed"] = len(cache.positions_closed() or [])
        summary["orders_open"] = len(cache.orders_open() or [])
    else:
        summary["note"] = "Cache unavailable (notifier ran without [cache] section)"
    summary["redis_streams_seen"] = redis_stream_count

    return _envelope_for("tinohelm.daily_summary", summary), logging_channel_id


async def _daily_summary_loop(
    forwarder: DiscordForwarder,
    when_utc: dtime,
    cache_provider,  # callable returning current Cache snapshot or None
    redis_client: redis.Redis,
    logging_channel_id: int,
) -> None:
    while True:
        now = datetime.now(tz=UTC)
        target = datetime.combine(now.date(), when_utc, tzinfo=UTC)
        if target <= now:
            target = target.replace(day=target.day + 1)
        delay = (target - now).total_seconds()
        logger.info(f"daily summary scheduled for {target.isoformat()} (in {delay:.0f}s)")
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        cache = cache_provider() if callable(cache_provider) else None
        try:
            stream_count = sum(
                len(redis_client.keys(p))
                for p in ("trader-*:stream:events.*", "tinohelm:control:*")
            )
            env, channel_id = build_daily_summary(
                cache=cache,
                redis_stream_count=stream_count,
                logging_channel_id=logging_channel_id,
            )
            forwarder.enqueue(env, channel_id=channel_id)
        except Exception as exc:  # pragma: no cover — defensive
            logger.error(f"daily summary failed: {exc}")


# ─── main ────────────────────────────────────────────────────────────────────


def _resolve_int_env(env_name: str, *, allow_none: bool = False) -> int | None:
    value = os.environ.get(env_name)
    if not value:
        if allow_none:
            return None
        raise SystemExit(f"required env var {env_name} is empty")
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(f"env var {env_name} must be int, got {value!r}") from exc


async def _autodiscover_loop(
    redis_client: Any,
    registry: dict[str, str],
    cursor: str,
    *,
    forwarder: Any | None = None,
    logging_channel_id: int | None = None,
    expected_proto: str | None = None,
    expected_nt_version: str | None = None,
    fallback_interval_s: float = 60.0,
) -> None:
    """Keep ``registry`` fresh and surface protocol drift.

    Two paths to the same registry update:
      1. ``read_new_announces`` (fast: reflects pod boots in <1s)
      2. ``apply_fallback_scan`` every 60s (slow: catches pods that came up
         while Redis was wedged or whose announce got trimmed)

    If ``forwarder`` and ``logging_channel_id`` are provided, each new
    announce is also passed through :func:`detect_protocol_drift` and any
    mismatch yields a one-shot envelope on the logging channel. We dedupe
    by ``(strategy_id, pod_proto, pod_nt_version)`` so a re-announcing pod
    doesn't spam Discord every time the autodiscover poll wakes up.
    """

    last_fallback = 0.0
    warned: set[tuple[str, str, str]] = set()
    while True:
        try:
            new_entries, cursor = await asyncio.to_thread(
                read_new_announces,
                redis_client,
                cursor,
                block_ms=500,
            )
            for sid, mode, _nt_v, _proto_v in new_entries:
                registry[sid] = mode

            if (
                forwarder is not None
                and logging_channel_id is not None
                and expected_proto is not None
                and expected_nt_version is not None
                and new_entries
            ):
                drift = detect_protocol_drift(
                    new_entries,
                    expected_proto=expected_proto,
                    expected_nt_version=expected_nt_version,
                )
                for env in drift:
                    key = (
                        env.body["strategy_id"],
                        env.body["pod_proto"],
                        env.body["pod_nt_version"],
                    )
                    if key in warned:
                        continue
                    warned.add(key)
                    forwarder.enqueue(env, channel_id=logging_channel_id)

            now = asyncio.get_running_loop().time()
            if now - last_fallback >= fallback_interval_s:
                await asyncio.to_thread(apply_fallback_scan, redis_client, registry)
                last_fallback = now
        except asyncio.CancelledError:
            return
        except Exception as exc:  # pragma: no cover — defensive
            logger.error(f"autodiscover loop error: {exc}")
            await asyncio.sleep(1.0)


def _configure_python_logging() -> None:
    # NT logs through its own Rust pipeline, but our `logger.info(...)`
    # calls go through the stdlib root logger, which defaults to WARNING
    # — so milestones like "discord bot ready as ..." get silently
    # dropped. Honour TINO_LOG_LEVEL (already used by NT) so both stay
    # in sync.
    level_name = os.environ.get("TINO_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )


def main(argv: list[str] | None = None) -> int:
    _configure_python_logging()
    parser = argparse.ArgumentParser(description="TinoHelm Discord notifier")
    parser.add_argument(
        "--config",
        default=os.environ.get("TINO_NOTIFIER_CONFIG"),
        help="Path to notifier TOML",
    )
    args = parser.parse_args(argv)
    if not args.config:
        parser.error("--config (or $TINO_NOTIFIER_CONFIG) is required")

    notifier_cfg = TinoNotifierFile.load(args.config)
    discord_token = os.environ.get(notifier_cfg.discord_token_env, "")
    if not discord_token:
        raise SystemExit(f"env var {notifier_cfg.discord_token_env} missing")
    sandbox_channel_id = _resolve_int_env(notifier_cfg.discord_channel_id_sandbox_env)
    live_channel_id = _resolve_int_env(notifier_cfg.discord_channel_id_live_env)
    logging_channel_id = _resolve_int_env(notifier_cfg.discord_channel_id_logging_env)
    guild_id = _resolve_int_env(notifier_cfg.discord_guild_id_env, allow_none=True)
    when_utc = _parse_hh_mm(notifier_cfg.daily_summary_utc)

    redis_client = redis.Redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=False,
    )

    # Bootstrap the strategy registry from announce history.
    registry, cursor = load_announce_history(redis_client)
    apply_fallback_scan(redis_client, registry)
    logger.info(f"notifier startup: known strategies = {registry}")

    # NT msgbus subscribes via XREAD on stream-prefix patterns; keep the
    # explicit list empty (NT auto-derives from streams_prefix + topic
    # patterns). Users can pin extra streams under [notifier].external_streams.
    extra = notifier_cfg.raw.get("notifier", {}).get("external_streams") or []
    external_streams = list(extra)

    config = build_notifier_node_config(notifier_cfg, external_streams=external_streams)
    node = TradingNode(config=config)
    node.build()

    loop = node.kernel.loop
    # Forwarder is built first with a None client so /positions can hold a
    # reference to it; we patch the client back in once it's constructed
    # below. ``_send`` only dereferences the client when an event actually
    # arrives, by which time we've assigned it.
    forwarder = DiscordForwarder(loop=loop, client=None)
    discord_client, _tree = _build_discord_client(
        discord_token,
        guild_id,
        notifier_cfg,
        redis_client,
        sandbox_channel_id,
        live_channel_id,
        logging_channel_id,
        registry,
        forwarder=forwarder,
    )
    forwarder.attach_client(discord_client)
    actor = NotifierActor(
        NotifierActorConfig(
            sandbox_channel_id=sandbox_channel_id,
            live_channel_id=live_channel_id,
            logging_channel_id=logging_channel_id,
        ),
        forwarder=forwarder,
        registry=registry,
    )
    node.trader.add_actor(actor)

    discord_task = loop.create_task(discord_client.start(discord_token))

    def _log_discord_task_result(task: asyncio.Task) -> None:
        # discord_client.start() exits silently when the gateway connect
        # fails, leaving the bot HTTP-reachable but offline. Surface the
        # exception so operators can see what went wrong (token wrong,
        # gateway blocked, intents rejected, etc.).
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            logger.info("discord client task exited cleanly")
        else:
            logger.error(
                "discord client task crashed: %s: %s",
                type(exc).__name__,
                exc,
                exc_info=exc,
            )

    discord_task.add_done_callback(_log_discord_task_result)
    autodiscover_task = loop.create_task(
        _autodiscover_loop(
            redis_client,
            registry,
            cursor,
            forwarder=forwarder,
            logging_channel_id=logging_channel_id,
            expected_proto=TINO_PROTOCOL_VERSION,
            expected_nt_version=nautilus_trader.__version__,
        ),
    )
    summary_task = loop.create_task(
        _daily_summary_loop(
            forwarder,
            when_utc,
            cache_provider=lambda: node.kernel.cache,
            redis_client=redis_client,
            logging_channel_id=logging_channel_id,
        ),
    )

    def _handle_sig(signum: int, _frame: Any) -> None:
        logger.info(f"received {signal.Signals(signum).name}")
        for task in (discord_task, autodiscover_task, summary_task):
            if not task.done():
                task.cancel()
        node.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _handle_sig)

    try:
        node.run()
    finally:
        if not discord_client.is_closed():
            asyncio.run_coroutine_threadsafe(discord_client.close(), loop).result(timeout=5)
        node.dispose()

    return 0


if __name__ == "__main__":
    sys.exit(main())
