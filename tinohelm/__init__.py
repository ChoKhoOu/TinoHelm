"""TinoHelm — thin orchestrator around NautilusTrader.

The package owns four pieces of glue code:

* :mod:`tinohelm.config` — TOML → ``TradingNodeConfig`` assembly.
* :mod:`tinohelm.strategy_runner` — entrypoint per strategy pod.
* :mod:`tinohelm.bridge_actor` — relays cross-process control messages
  to the in-process ``Trader``.
* :mod:`tinohelm.notifier` — Discord notifier + slash-command handler pod.
* :mod:`tinohelm.cli` — small Typer CLI: pause / resume / flatten / status.

Everything else is delegated to NautilusTrader's own primitives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nautilus_trader.common.component import MessageBus

__version__ = "0.1.0"

# Topic prefix reserved for TinoHelm cross-process control messages.
# Format: ``commands.tinohelm.{strategy_id}.{action}``.
COMMAND_TOPIC_PREFIX = "commands.tinohelm"

CUSTOM_MESSAGE_TOPIC = "tinohelm.message"


def publish_message(
    msgbus: MessageBus,
    title: str,
    text: str,
    *,
    color: int | None = None,
) -> None:
    """Publish a custom message that the notifier renders as a Discord embed.

    Call from any Strategy or Actor that has access to ``self.msgbus``::

        from tinohelm import publish_message
        publish_message(self.msgbus, "风控警告", "当日回撤超过 **5%**，已暂停下单")

    Parameters
    ----------
    msgbus : MessageBus
        The NT message bus (``self.msgbus`` inside an Actor/Strategy).
    title : str
        Embed title (short, ≤256 chars per Discord limit).
    text : str
        Embed description body — supports Discord-flavoured Markdown.
    color : int, optional
        Hex color for the embed sidebar (e.g. ``0xE74C3C`` for red).
        Defaults to a neutral blue on the notifier side.
    """

    payload: dict[str, str | int] = {"title": title, "text": text}
    if color is not None:
        payload["color"] = color
    msgbus.publish(topic=CUSTOM_MESSAGE_TOPIC, msg=payload)


def control_stream_key(strategy_id: str) -> str:
    """Redis stream key TinoHelm uses for cross-process control commands.

    NT's own event streams follow ``trader-{id}:stream:{topic}`` and are
    managed by ``RedisMessageBusDatabase``. We carve out a separate, fixed
    namespace here so the CLI never needs to know NT's ``MessageBusConfig``
    fields (use_trader_prefix / use_instance_id / streams_prefix). The
    strategy pod adds this key to its ``MessageBusConfig.external_streams``;
    NT's stream task then ``XREAD``s it and replays each entry as a regular
    in-process publish.
    """

    return f"tinohelm:control:{strategy_id}"


def event_stream_key(trader_id: str, *, streams_prefix: str = "stream") -> str:
    """Redis stream key a strategy pod writes its outbound events to.

    This must reproduce NT's ``RedisMessageBusDatabase`` key layout *exactly* —
    the notifier lists this literal key in its ``MessageBusConfig.external_streams``
    so NT's stream task ``XREAD``s it (NT does no key globbing — see
    ``msgbus.rs`` ``stream_messages`` / ``xread_options``; a ``*`` here would
    never match). The Rust ``get_stream_key`` (msgbus core ``mod.rs``) joins
    ``trader-{trader_id}`` + ``{streams_prefix}`` with ``:`` (``REDIS_DELIMITER``)
    when ``use_trader_prefix`` + ``use_trader_id`` are on (TinoHelm's defaults,
    see ``config.build_message_bus_config``) and ``use_instance_id`` is off.

    This is the **aggregate** key used when ``stream_per_topic = false``: the pod
    funnels every outbound event into this single stream. With
    ``stream_per_topic = true`` NT instead appends ``:{topic}`` per message,
    which is unenumerable up front (account_id / signal-name suffixes are only
    known at runtime) — that's why strategy pods run with ``stream_per_topic =
    false`` so the notifier can name one stable key per pod.
    """

    return f"trader-{trader_id}:{streams_prefix}"
