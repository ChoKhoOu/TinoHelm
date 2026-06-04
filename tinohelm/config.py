"""TOML → NautilusTrader config assembly.

Strategy pod (:mod:`tinohelm.strategy_runner`) and notifier pod
(:mod:`tinohelm.notifier.runner`) both consume TOML files produced by users.
We never re-implement NT's config types — just deserialize TOML and hand the
fields straight to ``MessageBusConfig`` / ``CacheConfig`` / ``TradingNodeConfig``
/ ``ImportableStrategyConfig`` etc.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.common.config import (
    DatabaseConfig,
    ImportableActorConfig,
    LoggingConfig,
    MessageBusConfig,
)
from nautilus_trader.live.config import (
    LiveDataEngineConfig,
    LiveExecEngineConfig,
    LiveRiskEngineConfig,
    TradingNodeConfig,
)
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.trading.config import ImportableStrategyConfig

DEFAULT_STREAMS_PREFIX = "stream"
DEFAULT_ENCODING = "msgpack"


@dataclass
class TinoStrategyFile:
    """Parsed strategy TOML — kept around so the runner can swap modes."""

    raw: dict[str, Any]
    path: Path

    # Derived shortcuts, populated in :meth:`load`.
    strategy_id: str = ""
    trader_id: str = ""
    mode: str = "live"  # "live" | "sandbox"

    # Discord-routed command topic for this strategy.
    command_topic: str = ""

    @classmethod
    def load_for_id(
        cls,
        strategy_id: str,
        *,
        search_root: str | os.PathLike[str] | None = None,
    ) -> TinoStrategyFile:
        root = Path(search_root) if search_root else Path.cwd()
        toml_path = root / "strategies" / strategy_id / "tinohelm.toml"
        if not toml_path.is_file():
            raise FileNotFoundError(
                f"strategies/{strategy_id}/tinohelm.toml not found under {root}",
            )
        return cls.load(toml_path)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> TinoStrategyFile:
        path = Path(path).expanduser().resolve()
        with path.open("rb") as fp:
            raw = tomllib.load(fp)

        strategy_section = raw.get("strategy", {})
        strategy_id = _required_str(strategy_section, "id", path)
        trader_id = strategy_section.get("trader_id") or os.environ.get(
            "TINO_TRADER_ID",
            "TINO-001",
        )
        mode = os.environ.get("TINO_MODE") or strategy_section.get("mode", "live")
        if mode not in {"live", "sandbox"}:
            raise ValueError(f"{path}: strategy.mode must be 'live' or 'sandbox', got {mode!r}")

        return cls(
            raw=raw,
            path=path,
            strategy_id=strategy_id,
            trader_id=trader_id,
            mode=mode,
            command_topic=f"commands.tinohelm.{strategy_id}",
        )


@dataclass
class TinoNotifierFile:
    """Parsed notifier config — from TOML or pure environment variables."""

    raw: dict[str, Any]
    path: Path | None
    trader_id: str = "TINO-NOTIFIER-001"
    discord_token_env: str = "DISCORD_BOT_TOKEN"
    discord_channel_id_sandbox_env: str = "DISCORD_CHANNEL_ID_SANDBOX"
    discord_channel_id_live_env: str = "DISCORD_CHANNEL_ID_LIVE"
    discord_channel_id_logging_env: str = "DISCORD_CHANNEL_ID_LOGGING"
    discord_guild_id_env: str = "DISCORD_GUILD_ID"
    daily_summary_utc: str = "14:00"

    @classmethod
    def from_env(cls) -> TinoNotifierFile:
        """Build config purely from environment variables (no TOML needed).

        Provides the same defaults that the TOML file would: msgpack encoding,
        Redis cache enabled, stream_per_topic=True.
        """

        return cls(
            raw={
                "message_bus": {
                    "encoding": "msgpack",
                    "streams_prefix": "stream",
                    "stream_per_topic": True,
                },
                "cache": {
                    "encoding": "msgpack",
                },
            },
            path=None,
            trader_id=os.environ.get("TINO_NOTIFIER_TRADER_ID", "TINO-NOTIFIER-001"),
            daily_summary_utc=os.environ.get("TINO_DAILY_SUMMARY_UTC", "14:00"),
        )

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> TinoNotifierFile:
        path = Path(path).expanduser().resolve()
        with path.open("rb") as fp:
            raw = tomllib.load(fp)
        notifier_section = raw.get("notifier", {})
        return cls(
            raw=raw,
            path=path,
            trader_id=notifier_section.get("trader_id", "TINO-NOTIFIER-001"),
            discord_token_env=notifier_section.get("discord_token_env", "DISCORD_BOT_TOKEN"),
            discord_channel_id_sandbox_env=notifier_section.get(
                "discord_channel_id_sandbox_env",
                "DISCORD_CHANNEL_ID_SANDBOX",
            ),
            discord_channel_id_live_env=notifier_section.get(
                "discord_channel_id_live_env",
                "DISCORD_CHANNEL_ID_LIVE",
            ),
            discord_channel_id_logging_env=notifier_section.get(
                "discord_channel_id_logging_env",
                "DISCORD_CHANNEL_ID_LOGGING",
            ),
            discord_guild_id_env=notifier_section.get("discord_guild_id_env", "DISCORD_GUILD_ID"),
            daily_summary_utc=notifier_section.get(
                "daily_summary_utc",
                os.environ.get("TINO_DAILY_SUMMARY_UTC", "14:00"),
            ),
        )


# ─── builders ─────────────────────────────────────────────────────────────────


def build_message_bus_config(
    raw: dict[str, Any],
    *,
    external_streams: list[str] | None = None,
) -> MessageBusConfig:
    """Build :class:`MessageBusConfig` from the ``[message_bus]`` TOML section."""

    section = raw.get("message_bus", {})
    redis_url = os.environ.get("REDIS_URL") or section.get("redis_url", "redis://redis:6379/0")
    db = _database_config_from_url(redis_url)

    return MessageBusConfig(
        database=db,
        encoding=section.get("encoding", DEFAULT_ENCODING),
        timestamps_as_iso8601=section.get("timestamps_as_iso8601", True),
        buffer_interval_ms=section.get("buffer_interval_ms", 100),
        autotrim_mins=section.get("autotrim_mins", 60),
        use_trader_prefix=section.get("use_trader_prefix", True),
        use_trader_id=section.get("use_trader_id", True),
        use_instance_id=section.get("use_instance_id", False),
        streams_prefix=section.get("streams_prefix", DEFAULT_STREAMS_PREFIX),
        stream_per_topic=section.get("stream_per_topic", True),
        external_streams=external_streams
        if external_streams is not None
        else section.get("external_streams"),
        types_filter=section.get("types_filter"),
    )


def build_cache_config(raw: dict[str, Any]) -> CacheConfig | None:
    """Optional ``[cache]`` section. If omitted, NT runs in pure in-memory mode."""

    section = raw.get("cache")
    if not section:
        return None
    redis_url = os.environ.get("REDIS_URL") or section.get("redis_url", "redis://redis:6379/0")
    return CacheConfig(
        database=_database_config_from_url(redis_url),
        encoding=section.get("encoding", DEFAULT_ENCODING),
        timestamps_as_iso8601=section.get("timestamps_as_iso8601", True),
        buffer_interval_ms=section.get("buffer_interval_ms", 100),
        flush_on_start=section.get("flush_on_start", False),
    )


def build_exec_engine_config(raw: dict[str, Any], *, mode: str = "live") -> LiveExecEngineConfig:
    """Build :class:`LiveExecEngineConfig` from the optional ``[exec]`` section.

    Every field passes straight through to NT — we never reinterpret it. We
    flip exactly one TinoHelm default away from NT's:

    * **Continuous reconciliation polling (default ON).**
      ``open_check_interval_secs`` / ``position_check_interval_secs`` are
      ``None`` in NT (startup-only reconciliation). We default them to 10s /
      60s — NT's recommended range (5-10s open orders, 30-60s positions) — so a
      pod keeps catching venue drift (missed fills/cancels) after boot without
      exhausting API rate limits. Set either to the string ``"none"`` to fall
      back to NT's startup-only mode (TOML has no null literal).

    We keep NT's defaults for everything else, notably:

    * **Order/position state snapshots (default OFF).** ``snapshot_orders`` /
      ``snapshot_positions`` write an append-only audit trail to the
      ``snapshots:orders`` / ``snapshots:positions`` Redis lists. NT never
      auto-trims them (unbounded growth) and they play no part in restart
      recovery, so they stay opt-in: set ``snapshot_* = true`` under ``[exec]``
      on a strategy whose operator wants the full history.

    **Sandbox mode forces ``reconciliation=False`` (mode-aware, not a user
    knob).** The ``SandboxExecutionClient`` runs an in-process simulated
    exchange that returns EMPTY mass-status reports — startup reconciliation
    against it produces spurious position-discrepancy noise and no value (there
    is no real venue state to reconcile). NT's own sandbox examples disable it,
    so we do too whenever ``mode == "sandbox"``. Live mode keeps NT's default
    (reconciliation ON), so this is a safe default with no operator footgun.
    """

    section = raw.get("exec", {})
    return LiveExecEngineConfig(
        reconciliation=False if mode == "sandbox" else True,
        open_check_interval_secs=_optional_interval(section.get("open_check_interval_secs", 10.0)),
        position_check_interval_secs=_optional_interval(
            section.get("position_check_interval_secs", 60.0),
        ),
        snapshot_orders=section.get("snapshot_orders", False),
        snapshot_positions=section.get("snapshot_positions", False),
    )


def build_logging_config(raw: dict[str, Any]) -> LoggingConfig:
    section = raw.get("logging", {})
    return LoggingConfig(
        log_level=os.environ.get("TINO_LOG_LEVEL") or section.get("log_level", "INFO"),
        log_colors=section.get("log_colors", True),
        bypass_logging=section.get("bypass_logging", False),
    )


def build_strategy_imports(file: TinoStrategyFile) -> list[ImportableStrategyConfig]:
    """Translate the ``[strategy]`` block into NT's importable form.

    NT will lazy-import ``strategy.path`` (``module:Class``) at build time. We
    don't import the strategy class ourselves — that happens inside the kernel.

    **We inject ``order_id_tag``, NOT ``strategy_id``.** NT's ``StrategyConfig``
    types ``strategy_id`` as ``StrategyId | None``, so passing a *string* there
    makes msgspec coerce it into a ``StrategyId`` OBJECT during
    ``StrategyFactory.create``. ``Strategy.__init__`` then does
    ``component_id = config.strategy_id`` (now an object, not a str) and calls
    ``Logger(name=component_id)`` — which requires ``str`` and raises
    ``TypeError: Argument 'name' has incorrect type`` at ``node.build()``. NT
    instead expects ``strategy_id`` to be left ``None`` so it derives the
    component id from the class name, and builds the full id as
    ``StrategyId(f"{ClassName}-{order_id_tag}")``. So we feed our control-plane
    handle in as ``order_id_tag`` (a plain ``str | None`` field): the NT
    ``StrategyId`` becomes ``"{ClassName}-{file.strategy_id}"`` (hyphen supplied
    by the join, satisfying NT's hyphen requirement), while ``file.strategy_id``
    stays the directory-name handle the control plane (CLI / BridgeActor /
    Redis stream) uses. An explicit ``order_id_tag`` in ``[strategy]`` wins;
    otherwise we default it to the strategy id.
    """

    section = file.raw.get("strategy", {})
    params = dict(section.get("params", {}))
    # Caller-provided order_id_tag (if any) takes precedence; else use the id handle.
    params.setdefault("order_id_tag", section.get("order_id_tag", file.strategy_id))
    return [
        ImportableStrategyConfig(
            strategy_path=_required_str(section, "class", file.path),
            config_path=section.get(
                "config_class",
                "nautilus_trader.trading.config:StrategyConfig",
            ),
            config=params,
        ),
    ]


def build_actor_imports(file: TinoStrategyFile) -> list[ImportableActorConfig]:
    """Inject the bridge actor + (by default) the reporting actor; users may add more under ``[[actors]]``.

    ``[reporting] enabled = false`` skips the reporting actor — it's the only
    moving part operators commonly want off during early development.
    """

    actors: list[ImportableActorConfig] = [
        ImportableActorConfig(
            actor_path="tinohelm.bridge_actor:BridgeActor",
            config_path="tinohelm.bridge_actor:BridgeActorConfig",
            config={
                "strategy_id": file.strategy_id,
                "command_topic": file.command_topic,
            },
        ),
    ]

    reporting_section = file.raw.get("reporting", {})
    if reporting_section.get("enabled", True):
        actors.append(
            ImportableActorConfig(
                actor_path="tinohelm.reporting_actor:ReportingActor",
                config_path="tinohelm.reporting_actor:ReportingActorConfig",
                config={
                    "strategy_id": file.strategy_id,
                    "interval_minutes": reporting_section.get("interval_minutes", 30),
                    "enabled": True,
                },
            ),
        )

    for extra in file.raw.get("actors", []):
        actors.append(
            ImportableActorConfig(
                actor_path=extra["class"],
                config_path=extra.get("config_class", "nautilus_trader.common.config:ActorConfig"),
                config=extra.get("params", {}),
            ),
        )
    return actors


def build_data_clients(file: TinoStrategyFile) -> dict[str, Any]:
    """Pass venue ``data_clients`` straight through (dict form NT accepts)."""

    clients: dict[str, Any] = {}
    for venue, payload in file.raw.get("data_clients", {}).items():
        clients[venue] = _resolve_env_refs(payload)
    return clients


def build_exec_clients(file: TinoStrategyFile) -> dict[str, Any]:
    """In sandbox mode, route every venue through ``SandboxExecutionClientConfig``."""

    raw_clients = file.raw.get("exec_clients", {})
    if file.mode == "live":
        return {venue: _resolve_env_refs(payload) for venue, payload in raw_clients.items()}

    sandbox_section = file.raw.get("sandbox", {})
    sandbox_clients: dict[str, Any] = {}
    for venue, payload in raw_clients.items():
        sandbox_clients[venue] = {
            "path": "nautilus_trader.adapters.sandbox.config:SandboxExecutionClientConfig",
            "config": {
                "venue": venue,
                "starting_balances": sandbox_section.get(
                    "starting_balances",
                    payload.get("starting_balances", ["100000 USDT"]),
                ),
                "base_currency": sandbox_section.get("base_currency"),
                "oms_type": sandbox_section.get("oms_type", "NETTING"),
                "account_type": sandbox_section.get("account_type", "MARGIN"),
                "book_type": sandbox_section.get("book_type", "L1_MBP"),
                "default_leverage": sandbox_section.get("default_leverage", 1.0),
                # Execution-matching knobs (NT defaults bar_execution/trade_execution/
                # use_reduce_only all True). Passed through so a bar-signal strategy fed
                # by a quote-only feeder can set trade_execution=false (avoid the
                # "Skipping stale trade" flood) — generic, any sandbox strategy benefits.
                "bar_execution": sandbox_section.get("bar_execution", True),
                "trade_execution": sandbox_section.get("trade_execution", True),
                "use_reduce_only": sandbox_section.get("use_reduce_only", True),
                "instrument_provider": payload.get("instrument_provider"),
            },
        }
    return sandbox_clients


def build_trading_node_config(file: TinoStrategyFile) -> TradingNodeConfig:
    """Top-level assembler used by :mod:`tinohelm.strategy_runner`."""

    from tinohelm import control_stream_key

    # Always inject our control stream so the BridgeActor can hear CLI/Discord
    # commands. Users can list additional streams under [message_bus].external_streams.
    user_streams = list(file.raw.get("message_bus", {}).get("external_streams") or [])
    extra = control_stream_key(file.strategy_id)
    if extra not in user_streams:
        user_streams.append(extra)

    # NT's kernel persists Trader state to the cache on stop and reloads it on
    # start when these flags are True (system/kernel.py:534 and :1049). Default
    # them on so a strategy pod redeploy doesn't silently drop on_save() state
    # or in-flight bracket-order tracking. Explicit opt-out via [recovery].
    recovery_enabled = file.raw.get("recovery", {}).get("enabled", True)

    return TradingNodeConfig(
        trader_id=TraderId(file.trader_id),
        message_bus=build_message_bus_config(file.raw, external_streams=user_streams),
        cache=build_cache_config(file.raw),
        logging=build_logging_config(file.raw),
        data_engine=LiveDataEngineConfig(),
        risk_engine=LiveRiskEngineConfig(),
        exec_engine=build_exec_engine_config(file.raw, mode=file.mode),
        actors=build_actor_imports(file),
        strategies=build_strategy_imports(file),
        data_clients=build_data_clients(file),
        exec_clients=build_exec_clients(file),
        load_state=recovery_enabled,
        save_state=recovery_enabled,
    )


def build_notifier_node_config(
    notifier: TinoNotifierFile,
    *,
    external_streams: list[str],
) -> TradingNodeConfig:
    """Notifier pod = TradingNode without exec clients, only msgbus subscriber."""

    return TradingNodeConfig(
        trader_id=TraderId(notifier.trader_id),
        message_bus=build_message_bus_config(
            notifier.raw,
            external_streams=external_streams,
        ),
        cache=build_cache_config(notifier.raw),
        logging=build_logging_config(notifier.raw),
        data_engine=LiveDataEngineConfig(),
        risk_engine=LiveRiskEngineConfig(),
        exec_engine=LiveExecEngineConfig(),
        # The notifier actor itself is added by the runner via add_actor().
    )


# ─── helpers ──────────────────────────────────────────────────────────────────


def _optional_interval(value: Any) -> float | None:
    """Map a TOML interval value to NT's ``float | None``.

    TOML has no null literal, so an operator disables a polling interval by
    writing the string ``"none"`` (case-insensitive). Any other value is passed
    through verbatim for NT to validate.
    """

    if isinstance(value, str) and value.strip().lower() == "none":
        return None
    return value


def _required_str(section: dict[str, Any], key: str, path: Path) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path}: required string field [strategy].{key} missing")
    return value


def _database_config_from_url(redis_url: str) -> DatabaseConfig:
    parsed = urlparse(redis_url)
    if parsed.scheme not in {"redis", "rediss"}:
        raise ValueError(f"unsupported redis URL scheme: {redis_url!r}")
    return DatabaseConfig(
        type="redis",
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        username=parsed.username,
        password=parsed.password,
        ssl=parsed.scheme == "rediss",
    )


def _resolve_env_refs(payload: Any) -> Any:
    """Recursively expand ``"$ENV:NAME"`` and ``"${NAME}"`` references in TOML values."""

    if isinstance(payload, dict):
        return {k: _resolve_env_refs(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_resolve_env_refs(v) for v in payload]
    if isinstance(payload, str):
        if payload.startswith("$ENV:"):
            return os.environ.get(payload[5:], "")
        return os.path.expandvars(payload)
    return payload
