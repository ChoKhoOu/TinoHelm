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

__version__ = "0.1.0"

# Topic prefix reserved for TinoHelm cross-process control messages.
# Format: ``commands.tinohelm.{strategy_id}.{action}``.
COMMAND_TOPIC_PREFIX = "commands.tinohelm"


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
