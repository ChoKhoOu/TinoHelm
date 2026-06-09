"""TinoHelm CLI — pause / resume / flatten / status, and Redis publish helpers.

The CLI never imports ``nautilus_trader`` (so it runs fast and can be invoked
from inside any container without booting a TradingNode). It speaks the same
Redis Streams wire format that NT's ``RedisMessageBusDatabase`` uses on the
read side: ``XADD <stream_key> * topic <topic> payload <bytes>``.

``payload`` is **msgpack-encoded** (via ``msgspec.msgpack``) so that NT's
``MsgSpecSerializer.deserialize`` (which uses msgpack by default) can decode it
in ``publish_bus_message``. The decoded dict is then published onto the in-process
msgbus where BridgeActor's wildcard subscription delivers it to ``_on_command``.
"""

from __future__ import annotations

import os
import sys
import time

import msgspec.msgpack
import redis
import typer

from tinohelm import COMMAND_TOPIC_PREFIX, control_stream_key

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _client() -> redis.Redis:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url, decode_responses=False)


def _publish_command(strategy_id: str, action: str, *, reason: str | None = None) -> None:
    topic = f"{COMMAND_TOPIC_PREFIX}.{strategy_id}.{action}"
    payload: dict[str, str | int] = {"action": action, "ts": int(time.time() * 1_000)}
    if reason:
        payload["reason"] = reason
    body = msgspec.msgpack.encode(payload)

    stream_key = control_stream_key(strategy_id)
    _client().xadd(stream_key, {"topic": topic, "payload": body})
    typer.echo(f"XADD {stream_key} topic={topic} payload={payload}")


@app.command()
def pause(strategy_id: str = typer.Option(..., "--strategy-id", "-s")) -> None:
    """Tell strategy pod to call ``trader.stop_strategy``."""

    _publish_command(strategy_id, "pause")


@app.command()
def resume(strategy_id: str = typer.Option(..., "--strategy-id", "-s")) -> None:
    """Tell strategy pod to call ``trader.start_strategy``."""

    _publish_command(strategy_id, "resume")


@app.command()
def flatten(
    strategy_id: str = typer.Option(..., "--strategy-id", "-s"),
    reason: str | None = typer.Option(None, "--reason", "-r"),
) -> None:
    """Tell strategy pod to call ``trader.market_exit_strategy``.

    A ``--reason`` is optional but recommended — it travels with the envelope
    and gets logged on the strategy pod, which makes incident postmortems much
    easier than reconstructing intent from timestamps alone.
    """

    _publish_command(strategy_id, "flatten", reason=reason)


@app.command()
def ping(strategy_id: str = typer.Option(..., "--strategy-id", "-s")) -> None:
    """Liveness check via the bridge actor."""

    _publish_command(strategy_id, "ping")


def _fan_out_command(action: str, strategy_id: str | None) -> None:
    """Publish ``action`` to one strategy (``-s``) or fan out to all announced.

    Shared by the fire-and-forget report commands (positions / fills / orders).
    The CLI doesn't wait for the reply (no event loop here) — each pod publishes
    its own ``tinohelm.report.*`` envelope and the notifier renders it into
    #logging. For a synchronous wait use the matching slash command.

    Fan-out reads the announce stream (not ``tinohelm:control:*`` keys, which are
    merely a side-effect of past commands — a never-controlled strategy has none).
    ``xrevrange(..., count=1000)`` reads the *newest* 1000 entries (XRANGE reads
    oldest-first; on a months-old stream that would silently drop a strategy whose
    announce sits past the head). 1000 entries — one per pod boot — comfortably
    covers any realistic active fleet while keeping the read bounded.
    """

    if strategy_id:
        _publish_command(strategy_id, action)
        return

    from tinohelm.strategy_runner import ANNOUNCE_STREAM

    seen: dict[str, None] = {}
    for _entry_id, fields in _client().xrevrange(ANNOUNCE_STREAM, count=1000):
        sid = fields.get(b"strategy_id")
        if sid is None:
            continue
        decoded = sid.decode() if isinstance(sid, bytes) else str(sid)
        seen.setdefault(decoded, None)

    if not seen:
        typer.echo("no strategies known yet (have any pods announced themselves?)")
        return

    for sid in seen:
        _publish_command(sid, action)


@app.command()
def positions(
    strategy_id: str | None = typer.Option(None, "--strategy-id", "-s"),
) -> None:
    """Trigger an on-demand positions snapshot (→ ``tinohelm.report.positions``).

    Without ``-s``, fans out to every announced strategy. The CLI doesn't wait;
    operators see the result in Discord. For a synchronous wait use the
    ``/positions`` slash command.
    """

    _fan_out_command("report", strategy_id)


@app.command()
def fills(
    strategy_id: str | None = typer.Option(None, "--strategy-id", "-s"),
) -> None:
    """Trigger an on-demand fill history (→ ``tinohelm.report.fills``).

    One row per individual fill (price / qty / fee / time), rendered into
    #logging. Fire-and-forget like ``positions``; ``/fills`` is the slash-command
    counterpart that waits for the reply.
    """

    _fan_out_command("fills", strategy_id)


@app.command()
def orders(
    strategy_id: str | None = typer.Option(None, "--strategy-id", "-s"),
) -> None:
    """Trigger an on-demand order history (→ ``tinohelm.report.orders``).

    One row per order (status / filled / avg price), rendered into #logging.
    Fire-and-forget like ``positions``; ``/orders`` is the slash-command
    counterpart that waits for the reply.
    """

    _fan_out_command("orders", strategy_id)


@app.command()
def status(strategy: str | None = typer.Option(None, "--strategy-id", "-s")) -> None:
    """Print pod heartbeats / latest events from Redis (best-effort)."""

    client = _client()
    info = client.info(section="server")
    typer.echo(f"redis_version={info.get('redis_version')} uptime={info.get('uptime_in_seconds')}s")

    patterns = ["trader-*:stream:events.*", "trader-*:stream:data.Signal*", "tinohelm:control:*"]
    seen_any = False
    for pattern in patterns:
        keys = client.keys(pattern)
        for key in sorted(keys):
            key_str = key.decode() if isinstance(key, bytes) else key
            if strategy and strategy not in key_str:
                continue
            seen_any = True
            length = client.xlen(key)
            typer.echo(f"{key_str} XLEN={length}")
    if not seen_any:
        typer.echo("no streams found yet (have you started a strategy?)")


def main() -> int:
    app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
