"""Smoke tests for tinohelm.config — TOML → NT config assembly."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from tinohelm import control_stream_key
from tinohelm.config import TinoStrategyFile, build_trading_node_config


@pytest.fixture(autouse=True)
def _redis_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("TINO_MODE", raising=False)


@pytest.fixture()
def strategy_toml(tmp_path: Path) -> Path:
    body = textwrap.dedent(
        """
        [strategy]
        id = "FOO-001"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]
        instrument_id = "BTCUSDT-PERP.BYBIT"

        [message_bus]
        redis_url = "redis://redis:6379/0"
        streams_prefix = "stream"
        stream_per_topic = true

        [factories.data]
        BYBIT = "nautilus_trader.adapters.bybit.factories:BybitLiveDataClientFactory"

        [factories.exec]
        BYBIT = "nautilus_trader.adapters.bybit.factories:BybitLiveExecClientFactory"

        [data_clients.BYBIT]
        path = "nautilus_trader.adapters.bybit.config:BybitDataClientConfig"
        [data_clients.BYBIT.config]
        api_key = "$ENV:BYBIT_API_KEY"
        api_secret = "$ENV:BYBIT_API_SECRET"
        product_types = ["LINEAR"]

        [exec_clients.BYBIT]
        path = "nautilus_trader.adapters.bybit.config:BybitExecClientConfig"
        [exec_clients.BYBIT.config]
        api_key = "$ENV:BYBIT_API_KEY"
        api_secret = "$ENV:BYBIT_API_SECRET"
        product_types = ["LINEAR"]

        [sandbox]
        starting_balances = ["100_000 USDT"]
        """,
    ).strip()
    path = tmp_path / "foo.toml"
    path.write_text(body)
    return path


def test_load_strategy_file(strategy_toml: Path) -> None:
    file = TinoStrategyFile.load(strategy_toml)
    assert file.strategy_id == "FOO-001"
    assert file.trader_id == "TINO-001"
    assert file.mode == "sandbox"
    assert file.command_topic == "commands.tinohelm.FOO-001"


def test_env_overrides_mode(strategy_toml: Path, monkeypatch) -> None:
    monkeypatch.setenv("TINO_MODE", "live")
    file = TinoStrategyFile.load(strategy_toml)
    assert file.mode == "live"


def test_build_trading_node_config_injects_control_stream(strategy_toml: Path) -> None:
    os.environ.setdefault("BYBIT_API_KEY", "test-key")  # NT validates API key strings exist
    file = TinoStrategyFile.load(strategy_toml)
    config = build_trading_node_config(file)
    bus = config.message_bus
    assert bus is not None
    assert control_stream_key("FOO-001") in (bus.external_streams or [])
    assert bus.streams_prefix == "stream"
    assert bus.database is not None
    assert bus.database.host == "localhost"  # REDIS_URL env var (fixture above)
    # sandbox mode rewrites every venue's exec client
    assert "BYBIT" in config.exec_clients


def test_strategy_pod_load_and_save_state_default_on(strategy_toml: Path) -> None:
    """Strategy pods should recover from restart by default.

    NT's kernel saves trader state to the cache when the kernel stops
    (kernel.py:1049 → _trader.save()) and reloads it on startup
    (kernel.py:534 → _trader.load()), but only if the TradingNodeConfig has
    load_state/save_state set to True. We had them hardcoded False, which
    silently disabled NT's restart-recovery — a strategy pod redeploy would
    lose its on_save() state and any in-flight bracket order tracking.

    The default must be True so operators get recovery without ceremony;
    the [recovery] TOML section is the explicit opt-out.
    """

    file = TinoStrategyFile.load(strategy_toml)
    config = build_trading_node_config(file)
    assert config.load_state is True
    assert config.save_state is True


def test_exec_engine_enables_continuous_reconciliation_by_default(
    strategy_toml: Path,
) -> None:
    """A strategy pod should keep reconciling against the venue after startup.

    NT ships continuous reconciliation polling OFF (open_check_interval_secs /
    position_check_interval_secs default to None — startup-only reconciliation).
    Polling is what keeps a live pod catching venue drift (missed fills/cancels)
    after boot, so TinoHelm turns it on by default — same pattern as
    load_state/save_state. Values pass straight through to NT's
    LiveExecEngineConfig; we never reinterpret their meaning.

    Conservative default intervals (NT docs recommend 5-10s for open orders,
    30-60s for positions) keep venue API rate-limit pressure low.

    Order/position *snapshots* stay at NT's default OFF: they are an
    append-only audit stream NT never auto-trims (Redis grows unbounded) and
    they play no part in restart recovery — opt in per-strategy via [exec].
    """

    file = TinoStrategyFile.load(strategy_toml)
    config = build_trading_node_config(file)
    exec_cfg = config.exec_engine
    assert exec_cfg is not None
    assert exec_cfg.reconciliation is True  # NT default, asserted to pin it
    assert exec_cfg.open_check_interval_secs == 10.0
    assert exec_cfg.position_check_interval_secs == 60.0
    # Snapshots are an opt-in audit feature, not a restart-recovery one.
    assert exec_cfg.snapshot_orders is False
    assert exec_cfg.snapshot_positions is False


def test_exec_section_overrides_defaults_both_directions(tmp_path: Path) -> None:
    """The ``[exec]`` TOML section tunes every exposed flag, both ways.

    Two independent overrides exercised here:

    * Opt OUT of continuous reconciliation (``..._interval_secs = "none"`` →
      None, NT's startup-only mode) — for a venue with tight API rate limits.
    * Opt IN to the snapshot audit stream — for a strategy whose operator wants
      a full order/position history in Redis, accepting the unbounded growth.
    """

    body = textwrap.dedent(
        """
        [strategy]
        id = "AUDIT-001"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [exec]
        open_check_interval_secs = "none"
        position_check_interval_secs = "none"
        snapshot_orders = true
        snapshot_positions = true

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "audit.toml"
    path.write_text(body)

    file = TinoStrategyFile.load(path)
    config = build_trading_node_config(file)
    exec_cfg = config.exec_engine
    assert exec_cfg.open_check_interval_secs is None
    assert exec_cfg.position_check_interval_secs is None
    assert exec_cfg.snapshot_orders is True
    assert exec_cfg.snapshot_positions is True


def test_strategy_pod_includes_reporting_actor_by_default(strategy_toml: Path) -> None:
    """A live pod should ship periodic positions reports out of the box.

    The ReportingActor uses NT's Trader.generate_positions_report() (cache
    snapshot) and publishes on tinohelm.report.positions, which the notifier
    routes to the logging channel. Operators get visibility without writing
    any glue.
    """

    file = TinoStrategyFile.load(strategy_toml)
    actors = [a.actor_path for a in __import__(
        "tinohelm.config", fromlist=["build_actor_imports"],
    ).build_actor_imports(file)]
    assert "tinohelm.reporting_actor:ReportingActor" in actors


def test_strategy_pod_omits_reporting_actor_when_disabled(tmp_path: Path) -> None:
    """``[reporting] enabled = false`` removes the actor entirely — saves
    cycles on a strategy whose author wants minimal moving parts during
    development.
    """

    body = textwrap.dedent(
        """
        [strategy]
        id = "QUIET-001"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [reporting]
        enabled = false

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "quiet.toml"
    path.write_text(body)

    file = TinoStrategyFile.load(path)
    from tinohelm.config import build_actor_imports

    actors = [a.actor_path for a in build_actor_imports(file)]
    assert "tinohelm.reporting_actor:ReportingActor" not in actors


def test_strategy_pod_recovery_can_be_disabled_via_toml(tmp_path: Path) -> None:
    """A user who genuinely wants a clean-slate boot — typical for the
    first run of a new strategy, or after ``flush_on_start`` — sets
    ``[recovery] enabled = false``. This must override the True default.
    """

    body = textwrap.dedent(
        """
        [strategy]
        id = "FRESH-001"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [recovery]
        enabled = false

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "fresh.toml"
    path.write_text(body)

    file = TinoStrategyFile.load(path)
    config = build_trading_node_config(file)
    assert config.load_state is False
    assert config.save_state is False


def test_external_streams_preserves_user_entries_and_dedupes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """User-listed external_streams must survive; control stream auto-injects once.

    Operators may want to subscribe a strategy pod to another pod's events
    (cross-strategy sharing of signals, etc). Our auto-injection of the
    TinoHelm control stream must not clobber that list, and must not double
    up if the user already added it explicitly.
    """

    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")
    body = textwrap.dedent(
        f"""
        [strategy]
        id = "BAR-001"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [message_bus]
        external_streams = ["{control_stream_key("BAR-001")}", "trader-FOO:stream:events.order.FOO-001"]

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "bar.toml"
    path.write_text(body)

    file = TinoStrategyFile.load(path)
    config = build_trading_node_config(file)
    streams = list(config.message_bus.external_streams or [])

    # User entry preserved
    assert "trader-FOO:stream:events.order.FOO-001" in streams
    # Control stream present exactly once
    own = control_stream_key("BAR-001")
    assert streams.count(own) == 1


def test_sandbox_mode_swaps_every_venue_exec_client(strategy_toml: Path, monkeypatch) -> None:
    """In sandbox mode, exec client config must be ``SandboxExecutionClientConfig``.

    This is the contract that lets users flip a live config to sandbox via
    ``TINO_MODE=sandbox`` without editing strategy code. If a venue's exec
    client leaks through unswapped, real orders would hit the live exchange.
    """

    monkeypatch.setenv("BYBIT_API_KEY", "k")
    monkeypatch.setenv("BYBIT_API_SECRET", "s")
    monkeypatch.setenv("TINO_MODE", "sandbox")

    file = TinoStrategyFile.load(strategy_toml)
    config = build_trading_node_config(file)

    bybit_exec = config.exec_clients["BYBIT"]
    serialized = repr(bybit_exec)
    assert "Sandbox" in serialized
    # And data clients must NOT be swapped — sandbox is exec-only.
    bybit_data = config.data_clients["BYBIT"]
    assert "Sandbox" not in repr(bybit_data)


def test_missing_strategy_id_fails_loudly(tmp_path: Path) -> None:
    """A misspelled or missing ``strategy.id`` must crash at load time.

    Silent defaults would let two pods share a strategy_id and stomp each
    other's events. The error message should name the file path so an
    operator can fix it without grep.
    """

    bad = tmp_path / "bad.toml"
    bad.write_text(
        '[strategy]\nclass = "x:Y"\n',  # no id
    )
    with pytest.raises(ValueError, match=r"strategy.id"):
        TinoStrategyFile.load(bad)


def test_notifier_file_exposes_logging_channel_env(tmp_path: Path) -> None:
    """The notifier TOML must surface a logging-channel env var so the runner
    can resolve the third Discord channel at boot. Default name is
    ``DISCORD_CHANNEL_ID_LOGGING`` to match the sandbox/live naming.

    Without this, operators have no place to point the new channel and the
    notifier would have to fall back to mirroring (which we just removed).
    """

    from tinohelm.config import TinoNotifierFile

    body = textwrap.dedent(
        """
        [notifier]
        trader_id = "TINO-NOTIFIER-001"
        """,
    ).strip()
    path = tmp_path / "n.toml"
    path.write_text(body)

    cfg = TinoNotifierFile.load(path)
    assert cfg.discord_channel_id_logging_env == "DISCORD_CHANNEL_ID_LOGGING"


def test_notifier_file_allows_override_of_logging_env_name(tmp_path: Path) -> None:
    """Custom env-var names must override the default. Same pattern as
    ``discord_channel_id_sandbox_env`` already supports.
    """

    from tinohelm.config import TinoNotifierFile

    body = textwrap.dedent(
        """
        [notifier]
        discord_channel_id_logging_env = "MY_LOGGING_CHANNEL"
        """,
    ).strip()
    path = tmp_path / "n.toml"
    path.write_text(body)

    cfg = TinoNotifierFile.load(path)
    assert cfg.discord_channel_id_logging_env == "MY_LOGGING_CHANNEL"


def test_env_refs_in_toml_get_expanded(strategy_toml: Path, monkeypatch) -> None:
    """``"$ENV:NAME"`` in venue config must be replaced by the env var value.

    Operators write ``api_key = "$ENV:BYBIT_API_KEY"`` so secrets stay in
    .env, not in the TOML file. If this expansion regresses, NT would receive
    the literal string ``$ENV:BYBIT_API_KEY`` and authentication would fail
    with an opaque venue error.
    """

    monkeypatch.setenv("BYBIT_API_KEY", "supersecret-12345")
    file = TinoStrategyFile.load(strategy_toml)
    config = build_trading_node_config(file)
    bybit_cfg = config.data_clients["BYBIT"]
    # The build_data_clients passthrough may keep raw dicts or convert to NT
    # ImportableConfig — either way, the resolved value must appear.
    raw = getattr(bybit_cfg, "config", bybit_cfg) if not isinstance(bybit_cfg, dict) else bybit_cfg
    serialized = repr(raw)
    assert "supersecret-12345" in serialized
    assert "$ENV:" not in serialized
