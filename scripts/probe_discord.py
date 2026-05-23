"""Diagnose why the Discord bot stays offline.

Run from inside the notifier container:
    docker exec tinohelm-notifier python -u /app/scripts/probe_discord.py

Reads DISCORD_BOT_TOKEN / DISCORD_GUILD_ID / DISCORD_CHANNEL_ID_* from env,
attempts a real login (15s timeout), and prints what fails or succeeds.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback

import discord


def _flush(line: str) -> None:
    print(line, flush=True)


async def _probe() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    guild_id = os.environ.get("DISCORD_GUILD_ID", "")
    channels = {
        "sandbox": os.environ.get("DISCORD_CHANNEL_ID_SANDBOX", ""),
        "live": os.environ.get("DISCORD_CHANNEL_ID_LIVE", ""),
        "logging": os.environ.get("DISCORD_CHANNEL_ID_LOGGING", ""),
    }

    _flush(f"DISCORD_BOT_TOKEN: len={len(token)} prefix={token[:8]!r}")
    _flush(f"DISCORD_GUILD_ID: {guild_id!r}")
    for name, cid in channels.items():
        _flush(f"DISCORD_CHANNEL_ID_{name.upper()}: {cid!r}")

    if not token:
        _flush("FATAL: DISCORD_BOT_TOKEN is empty — .env not loaded into container?")
        return

    if token.startswith(('"', "'")) or token.endswith(('"', "'")):
        _flush("FATAL: DISCORD_BOT_TOKEN has surrounding quotes — remove them in .env")
        return

    client = discord.Client(intents=discord.Intents.default())

    @client.event
    async def on_ready() -> None:
        _flush(f"OK logged in as {client.user} (id={client.user.id})")
        guilds = [(g.id, g.name) for g in client.guilds]
        _flush(f"OK joined guilds: {guilds}")
        if guild_id and not any(str(gid) == guild_id for gid, _ in guilds):
            _flush(f"WARN: bot is not in the configured DISCORD_GUILD_ID={guild_id}")
        for name, cid in channels.items():
            if not cid:
                continue
            try:
                ch = await client.fetch_channel(int(cid))
                _flush(f"OK channel {name}={cid} resolved to '{ch.name}' in guild {ch.guild.id}")
            except Exception as exc:  # noqa: BLE001
                _flush(f"FAIL channel {name}={cid}: {type(exc).__name__}: {exc}")
        await client.close()

    try:
        await asyncio.wait_for(client.start(token), timeout=15)
    except TimeoutError:
        _flush("FAIL: client.start() did not finish within 15s — likely no network to discord.com")
    except discord.LoginFailure as exc:
        _flush(f"FAIL LOGIN: {exc} — token wrong, revoked, or wrapped in quotes in .env")
    except Exception as exc:  # noqa: BLE001
        _flush(f"FAIL UNEXPECTED: {type(exc).__name__}: {exc}")
        traceback.print_exc()


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    asyncio.run(_probe())
    _flush("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
