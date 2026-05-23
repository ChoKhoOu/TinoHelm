"""TinoHelm CLI — pause / resume / flatten / status, and Redis publish helpers.

The CLI never imports ``nautilus_trader`` (so it runs fast and can be invoked
from inside any container without booting a TradingNode). It speaks the same
Redis Streams wire format that NT's ``RedisMessageBusDatabase`` uses on the
read side: ``XADD <stream_key> * topic <topic> payload <bytes>``.

Decoded by ``crates/infrastructure/src/redis/msgbus.rs::decode_bus_message``
and replayed onto the in-process msgbus, so the strategy pod's BridgeActor
sees the command via the normal subscribe path.
"""

from __future__ import annotations

import json
import os
import sys
import time

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
    body = json.dumps(payload).encode("utf-8")

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


@app.command()
def positions(
    strategy_id: str | None = typer.Option(None, "--strategy-id", "-s"),
) -> None:
    """Trigger an on-demand positions snapshot.

    Without ``-s``, fan out to every strategy that ever announced itself —
    each pod publishes its own ``tinohelm.report.positions`` envelope and
    the notifier renders them into #logging. The CLI itself doesn't wait
    for the response (no event loop here); operators see the result in
    Discord. For a synchronous wait use the ``/positions`` slash command.
    """

    if strategy_id:
        _publish_command(strategy_id, "report")
        return

    # Fan out via the announce stream. We deliberately don't reach into
    # ``tinohelm:control:*`` keys (which the notifier uses as a fallback)
    # because those are merely a side-effect of past commands — a strategy
    # that's never been controlled won't have one.
    from tinohelm.strategy_runner import ANNOUNCE_STREAM

    seen: dict[str, None] = {}
    for _entry_id, fields in _client().xrange(ANNOUNCE_STREAM):
        sid = fields.get(b"strategy_id")
        if sid is None:
            continue
        decoded = sid.decode() if isinstance(sid, bytes) else str(sid)
        seen.setdefault(decoded, None)

    if not seen:
        typer.echo("no strategies known yet (have any pods announced themselves?)")
        return

    for sid in seen:
        _publish_command(sid, "report")


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
