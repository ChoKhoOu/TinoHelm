"""Entry point for a strategy pod.

Reads a TOML, assembles a :class:`TradingNodeConfig`, registers venue factories
(live or sandbox), and runs the node. ``SIGTERM`` triggers graceful shutdown.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from typing import Any

import nautilus_trader
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.live.factories import LiveDataClientFactory, LiveExecClientFactory
from nautilus_trader.live.node import TradingNode

from tinohelm.config import TinoStrategyFile, build_trading_node_config

logger = logging.getLogger("tinohelm.runner")

ANNOUNCE_STREAM = "tinohelm:announce"
ANNOUNCE_MAXLEN = 1000

# TinoHelm's own announce/control wire version — bump when we change the
# announce envelope shape or control-stream payload semantics in a way the
# notifier needs to react to. Independent of NT's version (broadcast as
# ``nt_version`` in the same envelope).
TINO_PROTOCOL_VERSION = "1"


def publish_announce(redis_client: Any, file: TinoStrategyFile) -> None:
    """Broadcast this pod's identity so the notifier can autodiscover it.

    Writes one entry to :data:`ANNOUNCE_STREAM` carrying ``strategy_id``,
    ``mode``, ``trader_id``, a millisecond-epoch ``ts``, plus the version
    handshake (``nt_version`` / ``tino_protocol_version``) so the notifier
    can warn on cross-version drift instead of silently mis-decoding events.
    Caps the stream at :data:`ANNOUNCE_MAXLEN` (approximate) to bound Redis
    memory.
    """

    redis_client.xadd(
        ANNOUNCE_STREAM,
        {
            "strategy_id": file.strategy_id,
            "mode": file.mode,
            "trader_id": file.trader_id,
            "ts": str(int(time.time() * 1_000)),
            "nt_version": nautilus_trader.__version__,
            "tino_protocol_version": TINO_PROTOCOL_VERSION,
        },
        maxlen=ANNOUNCE_MAXLEN,
        approximate=True,
    )


def _load_factory(class_path: str) -> type:
    """Lazily import a factory class via ``"pkg.mod:Class"`` notation."""

    if ":" not in class_path:
        raise ValueError(f"factory class must be in 'pkg.mod:Class' form, got {class_path!r}")
    mod_name, cls_name = class_path.split(":", 1)
    mod = __import__(mod_name, fromlist=[cls_name])
    return getattr(mod, cls_name)


def _register_factories(node: TradingNode, file: TinoStrategyFile) -> None:
    """Resolve factory class paths from TOML and register each on the node.

    TOML expected shape::

        [factories.data]
        BINANCE = "nautilus_trader.adapters.binance.factories:BinanceLiveDataClientFactory"
        [factories.exec]
        BINANCE = "nautilus_trader.adapters.binance.factories:BinanceLiveExecClientFactory"

    In sandbox mode every venue's exec factory is overridden to NT's
    :class:`SandboxLiveExecClientFactory`.
    """

    factories = file.raw.get("factories", {})
    data_factories: dict[str, str] = factories.get("data", {})
    exec_factories: dict[str, str] = factories.get("exec", {})

    for venue, fq in data_factories.items():
        cls = _load_factory(fq)
        if not issubclass(cls, LiveDataClientFactory):
            raise TypeError(f"data factory {fq!r} is not a LiveDataClientFactory subclass")
        node.add_data_client_factory(venue, cls)

    for venue in exec_factories:
        if file.mode == "sandbox":
            node.add_exec_client_factory(venue, SandboxLiveExecClientFactory)
        else:
            cls = _load_factory(exec_factories[venue])
            if not issubclass(cls, LiveExecClientFactory):
                raise TypeError(f"exec factory is not a LiveExecClientFactory subclass: {cls}")
            node.add_exec_client_factory(venue, cls)


def _install_signal_handlers(node: TradingNode) -> None:
    def _handle(signum: int, _frame: Any) -> None:
        name = signal.Signals(signum).name
        logger.info(f"received {name}, stopping TradingNode")
        try:
            node.stop()
        finally:
            # NT calls dispose() inside run() on graceful exit; if a second
            # signal arrives, fall through to the OS default.
            signal.signal(signum, signal.SIG_DFL)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a TinoHelm strategy pod")
    parser.add_argument(
        "--strategy-id",
        default=os.environ.get("TINO_STRATEGY_ID"),
        help="Strategy id under strategies/<id>/ (default: $TINO_STRATEGY_ID)",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("TINO_STRATEGY_CONFIG"),
        help="Explicit path to a strategy TOML — escape hatch for non-standard layouts",
    )
    args = parser.parse_args(argv)

    if args.strategy_id:
        file = TinoStrategyFile.load_for_id(args.strategy_id)
    elif args.config:
        file = TinoStrategyFile.load(args.config)
    else:
        parser.error(
            "one of --strategy-id (or $TINO_STRATEGY_ID) / --config is required",
        )

    logger.info(
        f"strategy={file.strategy_id} trader={file.trader_id} mode={file.mode} config={file.path}",
    )

    # Announce ourselves to the notifier before NT boots — if the pod crashes
    # mid-build, the operator still sees "FOO-001 attempted to come up at <ts>"
    # in the announce stream rather than silent absence.
    try:
        import redis as _redis

        redis_client = _redis.Redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=False,
        )
        publish_announce(redis_client, file)
    except Exception as exc:  # pragma: no cover — best-effort
        logger.warning(f"announce failed (notifier discovery degraded): {exc}")

    config = build_trading_node_config(file)
    node = TradingNode(config=config)
    _register_factories(node, file)
    node.build()
    _install_signal_handlers(node)

    try:
        node.run()
    finally:
        node.dispose()

    return 0


if __name__ == "__main__":
    sys.exit(main())
