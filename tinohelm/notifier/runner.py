"""Notifier pod entry point.

A minimal :class:`TradingNode` (no exec clients), wired up so that:

* ``MessageBusConfig.external_streams`` lists every strategy pod's stream key
  prefix, plus their TinoHelm control streams (so the notifier can echo
  ``/pause`` etc. for observability).
* A single :class:`NotifierActor` subscribes to the wildcard event topics NT
  publishes automatically (``events.order.*`` / ``events.position.*`` /
  ``events.account.*`` / ``data.Signal*``) and forwards them to Discord.
* A small ``discord.py`` client is spun up alongside, sharing the asyncio loop
  with NT, and translates slash commands into ``XADD`` on the strategy pod's
  control stream (re-using :func:`tinohelm.cli._publish_command`).
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
from datetime import datetime, time as dtime, timezone
from typing import Any

import discord
import redis
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.live.node import TradingNode

from tinohelm import COMMAND_TOPIC_PREFIX, control_stream_key
from tinohelm.config import TinoNotifierFile, build_notifier_node_config
from tinohelm.notifier.handlers import envelope_for, render_embed

logger = logging.getLogger("tinohelm.notifier")


# ─── notifier actor ──────────────────────────────────────────────────────────


class NotifierActorConfig(ActorConfig, frozen=True):
    discord_channel_id: int
    watched_strategies: list[str]


class NotifierActor(Actor):
    """NT actor that funnels msgbus events into a Discord client."""

    SUBSCRIBE_PATTERNS = (
        "events.order.*",
        "events.position.*",
        "events.account.*",
        "data.Signal*",
        "events.system.*",
    )

    def __init__(
        self,
        config: NotifierActorConfig,
        *,
        forwarder: "DiscordForwarder",
    ) -> None:
        super().__init__(config=config)
        self._forwarder = forwarder
        self._channel_id = config.discord_channel_id

    def on_start(self) -> None:
        for pattern in self.SUBSCRIBE_PATTERNS:
            self.msgbus.subscribe(topic=pattern, handler=self._make_handler(pattern))
        self.log.info(
            f"NotifierActor subscribed to {self.SUBSCRIBE_PATTERNS} -> channel={self._channel_id}",
        )

    def _make_handler(self, pattern: str):  # noqa: ANN202 — closure
        def _handle(msg: Any) -> None:
            try:
                env = envelope_for(pattern, msg)
                self._forwarder.enqueue(env)
            except Exception as exc:  # pragma: no cover — defensive
                self.log.error(f"NotifierActor failed on {pattern}: {exc}")

        return _handle


# ─── discord forwarder + slash commands ──────────────────────────────────────


class DiscordForwarder:
    """Cross-thread bridge between NT (sync handlers) and discord.py (async)."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client: discord.Client,
        channel_id: int,
        rate_limit: int = 5,
    ) -> None:
        self._loop = loop
        self._client = client
        self._channel_id = channel_id
        self._semaphore = asyncio.Semaphore(rate_limit)

    def enqueue(self, env) -> None:  # noqa: ANN001
        # NT handlers run on the asyncio loop already (ActorExecutor), but to
        # stay safe we go through ``run_coroutine_threadsafe`` — it's a no-op
        # when called from the same loop. We check the loop is open *before*
        # building the coroutine so we don't leak an un-awaited coroutine
        # object on the shutdown path.
        if self._loop.is_closed():
            logger.warning("event loop closed; dropping event")
            return
        try:
            asyncio.run_coroutine_threadsafe(self._send(env), self._loop)
        except RuntimeError:
            logger.warning("event loop closed; dropping event")

    async def _send(self, env) -> None:  # noqa: ANN001
        channel = self._client.get_channel(self._channel_id)
        if channel is None:
            try:
                channel = await self._client.fetch_channel(self._channel_id)
            except discord.HTTPException as exc:
                logger.error(f"discord fetch_channel({self._channel_id}) failed: {exc}")
                return
        async with self._semaphore:
            try:
                await channel.send(embed=render_embed(env))
            except discord.HTTPException as exc:
                logger.warning(f"discord send failed: {exc}")


def _build_discord_client(
    token: str,
    guild_id: int | None,
    notifier_cfg: TinoNotifierFile,
    redis_client: redis.Redis,
) -> tuple[discord.Client, discord.app_commands.CommandTree]:
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = discord.app_commands.CommandTree(client)
    guild_obj = discord.Object(id=guild_id) if guild_id else None

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

    @tree.command(name="pause", description="Stop a strategy without exiting positions")
    @discord.app_commands.describe(strategy="Strategy ID, e.g. FOO-001")
    async def pause_cmd(interaction: discord.Interaction, strategy: str) -> None:
        topic = await asyncio.to_thread(_publish, strategy, "pause")
        await interaction.response.send_message(f"sent `{topic}`", ephemeral=True)

    @tree.command(name="resume", description="Restart a previously paused strategy")
    async def resume_cmd(interaction: discord.Interaction, strategy: str) -> None:
        topic = await asyncio.to_thread(_publish, strategy, "resume")
        await interaction.response.send_message(f"sent `{topic}`", ephemeral=True)

    @tree.command(name="flatten", description="Market-exit and stop a strategy")
    async def flatten_cmd(
        interaction: discord.Interaction,
        strategy: str,
        reason: str | None = None,
    ) -> None:
        topic = await asyncio.to_thread(_publish, strategy, "flatten", reason)
        await interaction.response.send_message(f"sent `{topic}`", ephemeral=True)

    @tree.command(name="ping", description="Round-trip check via the bridge actor")
    async def ping_cmd(interaction: discord.Interaction, strategy: str) -> None:
        topic = await asyncio.to_thread(_publish, strategy, "ping")
        await interaction.response.send_message(f"sent `{topic}`", ephemeral=True)

    @tree.command(name="status", description="List known TinoHelm streams in Redis")
    async def status_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        keys: list[str] = []
        for pattern in ("trader-*:stream:events.*", "tinohelm:control:*"):
            for key in await asyncio.to_thread(redis_client.keys, pattern):
                keys.append(key.decode() if isinstance(key, bytes) else str(key))
        if not keys:
            await interaction.followup.send("no streams found", ephemeral=True)
            return
        lines: list[str] = []
        for key in sorted(keys):
            length = await asyncio.to_thread(redis_client.xlen, key)
            lines.append(f"`{key}` XLEN={length}")
        await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)

    @client.event
    async def on_ready() -> None:  # noqa: D401 — discord.py event
        if guild_obj:
            await tree.sync(guild=guild_obj)
        else:
            await tree.sync()
        logger.info(f"discord bot ready as {client.user} channel={notifier_cfg.discord_channel_id_env}")

    return client, tree


# ─── daily summary ────────────────────────────────────────────────────────────


def _parse_hh_mm(value: str) -> dtime:
    h, m = value.split(":", 1)
    return dtime(int(h), int(m), tzinfo=timezone.utc)


async def _daily_summary_loop(
    forwarder: DiscordForwarder,
    when_utc: dtime,
    cache_provider,  # callable returning current Cache snapshot or None
    redis_client: redis.Redis,
) -> None:
    while True:
        now = datetime.now(tz=timezone.utc)
        target = datetime.combine(now.date(), when_utc, tzinfo=timezone.utc)
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
            from tinohelm.notifier.handlers import envelope_for as _envelope_for

            summary: dict[str, Any] = {}
            if cache is not None:
                summary["positions_open"] = len(cache.positions_open() or [])
                summary["positions_closed"] = len(cache.positions_closed() or [])
                summary["orders_open"] = len(cache.orders_open() or [])
            else:
                summary["note"] = "Cache unavailable (notifier ran without [cache] section)"
            stream_count = sum(
                len(redis_client.keys(p))
                for p in ("trader-*:stream:events.*", "tinohelm:control:*")
            )
            summary["redis_streams_seen"] = stream_count

            env = _envelope_for("tinohelm.daily_summary", summary)
            forwarder.enqueue(env)
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


def main(argv: list[str] | None = None) -> int:
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
    channel_id = _resolve_int_env(notifier_cfg.discord_channel_id_env)
    guild_id = _resolve_int_env(notifier_cfg.discord_guild_id_env, allow_none=True)
    when_utc = _parse_hh_mm(notifier_cfg.daily_summary_utc)

    # external_streams: every strategy's NT event stream key prefix + its
    # control stream. NT writes per-topic streams when stream_per_topic=True
    # (see crates/infrastructure/src/redis/msgbus.rs:382), so callers may have
    # to expand explicit topics; here we list the base prefixes and rely on
    # users adding more under [notifier].external_streams if needed.
    external_streams: list[str] = []
    for sid in notifier_cfg.strategies:
        external_streams.append(control_stream_key(sid))
    extra = notifier_cfg.raw.get("notifier", {}).get("external_streams") or []
    for stream in extra:
        if stream not in external_streams:
            external_streams.append(stream)

    config = build_notifier_node_config(notifier_cfg, external_streams=external_streams)
    node = TradingNode(config=config)
    node.build()

    loop = node.kernel.loop
    redis_client = redis.Redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=False,
    )
    discord_client, _tree = _build_discord_client(discord_token, guild_id, notifier_cfg, redis_client)

    forwarder = DiscordForwarder(loop=loop, client=discord_client, channel_id=channel_id)
    actor = NotifierActor(
        NotifierActorConfig(
            discord_channel_id=channel_id,
            watched_strategies=notifier_cfg.strategies,
        ),
        forwarder=forwarder,
    )
    node.trader.add_actor(actor)

    discord_task = loop.create_task(discord_client.start(discord_token))
    summary_task = loop.create_task(
        _daily_summary_loop(
            forwarder,
            when_utc,
            cache_provider=lambda: node.kernel.cache,
            redis_client=redis_client,
        ),
    )

    def _handle_sig(signum: int, _frame: Any) -> None:
        logger.info(f"received {signal.Signals(signum).name}")
        for task in (discord_task, summary_task):
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
