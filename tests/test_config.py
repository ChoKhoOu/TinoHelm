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


def test_live_strategy_pod_load_and_save_state_default_on(tmp_path: Path) -> None:
    """A LIVE strategy pod should recover from restart by default.

    NT's kernel saves trader state to the cache when the kernel stops
    (kernel.py:1049 → _trader.save()) and reloads it on startup
    (kernel.py:534 → _trader.load()), but only if the TradingNodeConfig has
    load_state/save_state set to True. We had them hardcoded False, which
    silently disabled NT's restart-recovery — a strategy pod redeploy would
    lose its on_save() state and any in-flight bracket order tracking.

    The default must be True in live/DEMO so operators get recovery without
    ceremony (the real venue is the source of truth across restarts); the
    [recovery] TOML section is the explicit opt-out. Sandbox flips the default
    the other way — see test_sandbox_strategy_pod_is_ephemeral_by_default.
    """

    body = textwrap.dedent(
        """
        [strategy]
        id = "LIVE-001"
        trader_id = "TINO-001"
        mode = "live"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [cache]
        redis_url = "redis://redis:6379/0"

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "live.toml"
    path.write_text(body)

    file = TinoStrategyFile.load(path)
    config = build_trading_node_config(file)
    assert config.load_state is True
    assert config.save_state is True
    # Persistent cache too: live must NOT flush on boot (recovery replays it).
    assert config.cache is not None
    assert config.cache.flush_on_start is False


def test_sandbox_strategy_pod_is_ephemeral_by_default(tmp_path: Path) -> None:
    """A sandbox pod defaults to clean-slate: no state reload, cache flushed.

    The in-process ``SimulatedExchange`` ``initialize_account()``s fresh from
    ``starting_balances`` every boot and its ``generate_*_reports`` return empty,
    so reloading a persisted Trader/cache would only desync against a clean sim
    account. Mode-aware defaults make ``mode=sandbox`` ephemeral with zero TOML —
    the mirror image of the live default above (and the same pattern as
    ``reconciliation = mode != "sandbox"``). The TOML sets no ``flush_on_start`` /
    ``[recovery]``, so this exercises the pure mode-driven defaults.
    """

    body = textwrap.dedent(
        """
        [strategy]
        id = "SBX-001"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [cache]
        redis_url = "redis://redis:6379/0"

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "sandbox.toml"
    path.write_text(body)

    file = TinoStrategyFile.load(path)
    assert file.mode == "sandbox"
    config = build_trading_node_config(file)
    assert config.load_state is False
    assert config.save_state is False
    assert config.cache is not None
    assert config.cache.flush_on_start is True


def test_explicit_flush_on_start_overrides_mode_default() -> None:
    """An explicit ``[cache] flush_on_start`` always wins over the mode default.

    The mode-aware default (sandbox→True, live→False) is only the fallback when
    the key is absent. An operator can force a sandbox to KEEP its cache
    (flush=false) or force a live pod to wipe it (flush=true) — both directions
    must be honoured. Tested at the ``build_cache_config`` seam where the rule lives.
    """

    from tinohelm.config import build_cache_config

    raw_keep = {"cache": {"redis_url": "redis://r:6379/0", "flush_on_start": False}}
    raw_wipe = {"cache": {"redis_url": "redis://r:6379/0", "flush_on_start": True}}

    # sandbox default is True, but explicit False sticks.
    assert build_cache_config(raw_keep, mode="sandbox").flush_on_start is False
    # live default is False, but explicit True sticks.
    assert build_cache_config(raw_wipe, mode="live").flush_on_start is True
    # And the bare defaults remain mode-driven.
    bare = {"cache": {"redis_url": "redis://r:6379/0"}}
    assert build_cache_config(bare, mode="sandbox").flush_on_start is True
    assert build_cache_config(bare, mode="live").flush_on_start is False


def test_exec_engine_enables_continuous_reconciliation_in_live(
    strategy_toml: Path,
    monkeypatch,
) -> None:
    """A LIVE strategy pod should keep reconciling against the venue after startup.

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

    monkeypatch.setenv("TINO_MODE", "live")  # fixture toml is sandbox; force live
    file = TinoStrategyFile.load(strategy_toml)
    config = build_trading_node_config(file)
    exec_cfg = config.exec_engine
    assert exec_cfg is not None
    assert exec_cfg.reconciliation is True  # live default ON (NT default), asserted to pin it
    assert exec_cfg.open_check_interval_secs == 10.0
    assert exec_cfg.position_check_interval_secs == 60.0
    # Snapshots are an opt-in audit feature, not a restart-recovery one.
    assert exec_cfg.snapshot_orders is False
    assert exec_cfg.snapshot_positions is False


def test_exec_engine_disables_reconciliation_in_sandbox(
    strategy_toml: Path,
    monkeypatch,
) -> None:
    """A SANDBOX pod must DISABLE ALL reconciliation — startup AND continuous.

    The SandboxExecutionClient runs an in-process simulated exchange that returns
    EMPTY mass-status / position-status reports — there is no real venue state to
    reconcile against. ``reconciliation=False`` alone is NOT enough: NT's
    continuous reconciliation loop is launched whenever a polling interval is set
    (its launch gate ignores ``reconciliation`` — see build_exec_engine_config
    docstring, NT ``live/execution_engine.py:382-391`` pinned 1.227.0). Left at
    10s/60s the loop runs against the empty sim venue, declares a spurious
    position discrepancy, and flattens the live sim position via an inferred
    ``-EXTERNAL`` fill seconds after it opens. So sandbox must also null BOTH
    intervals so the loop is never created.
    """

    monkeypatch.setenv("TINO_MODE", "sandbox")  # fixture toml is already sandbox; pin it
    file = TinoStrategyFile.load(strategy_toml)
    assert file.mode == "sandbox"
    config = build_trading_node_config(file)
    exec_cfg = config.exec_engine
    assert exec_cfg is not None
    assert exec_cfg.reconciliation is False  # sandbox override: startup pass off
    # Continuous polling loop must NOT start in sandbox — both intervals nulled
    # so NT's launch gate (live/execution_engine.py:382-391) stays false.
    assert exec_cfg.open_check_interval_secs is None
    assert exec_cfg.position_check_interval_secs is None
    assert exec_cfg.snapshot_orders is False
    assert exec_cfg.snapshot_positions is False


def test_exec_section_overrides_defaults_both_directions(tmp_path: Path) -> None:
    """The ``[exec]`` TOML section tunes every exposed flag, both ways.

    Exercised in ``mode="live"`` ON PURPOSE: sandbox now nulls both intervals
    unconditionally (the continuous-reconciliation loop must never start against
    the empty sim venue — see ``test_exec_engine_disables_reconciliation_in_sandbox``),
    so a sandbox toml could not prove the ``"none"`` override actually takes
    effect (the assertion would pass vacuously). Live is where the operator's
    interval override is honoured, so that is where we pin it.

    Two independent overrides exercised here:

    * Opt OUT of continuous reconciliation (``..._interval_secs = "none"`` →
      None, NT's startup-only mode) — for a venue with tight API rate limits.
    * Opt IN to the snapshot audit stream — for a strategy whose operator wants
      a full order/position history in Redis, accepting the unbounded growth.
    """

    from tinohelm.config import build_exec_engine_config

    body = textwrap.dedent(
        """
        [strategy]
        id = "AUDIT-001"
        trader_id = "TINO-001"
        mode = "live"
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
    assert file.mode == "live"
    exec_cfg = build_exec_engine_config(file.raw, mode=file.mode)
    assert exec_cfg.open_check_interval_secs is None
    assert exec_cfg.position_check_interval_secs is None
    assert exec_cfg.snapshot_orders is True
    assert exec_cfg.snapshot_positions is True


def test_strategy_pod_includes_reporting_actor_by_default(strategy_toml: Path) -> None:
    """A live pod should ship periodic positions reports out of the box.

    The ReportingActor reads NT's Cache via ReportProvider (an Actor has
    self.cache but not self.trader) and publishes on tinohelm.report.positions,
    which the notifier routes to the logging channel. Operators get visibility
    without writing any glue.
    """

    file = TinoStrategyFile.load(strategy_toml)
    actors = [
        a.actor_path
        for a in __import__(
            "tinohelm.config",
            fromlist=["build_actor_imports"],
        ).build_actor_imports(file)
    ]
    assert "tinohelm.reporting_actor:ReportingActor" in actors


def test_bridge_controller_wired_via_controller_field(strategy_toml: Path) -> None:
    """The bridge MUST ride TradingNodeConfig.controller, NOT [[actors]].

    Regression for the AttributeError storm: BridgeActor subclasses NT's
    Controller and needs a trader ref. Only the controller seam supplies one
    (kernel: ControllerFactory.create(config, trader)); a plain [[actors]] entry
    goes through trader.add_actor and never gets a trader, so self._trader (and
    every *_from_id call) explodes. Pin both: bridge present on controller, and
    absent from the actors list.
    """

    file = TinoStrategyFile.load(strategy_toml)
    config = build_trading_node_config(file)

    assert config.controller is not None
    assert config.controller.controller_path == "tinohelm.bridge_actor:BridgeActor"
    assert config.controller.config["strategy_id"] == "FOO-001"
    assert config.controller.config["command_topic"] == "commands.tinohelm.FOO-001"

    actor_paths = [a.actor_path for a in config.actors]
    assert "tinohelm.bridge_actor:BridgeActor" not in actor_paths


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


# ─── Sandbox fill-fuel auto-injection ───────────────────────────────────────
#
# The sim exchange has no data client; it only consumes whatever matches its
# msgbus pattern data.*.{venue}.*. A bar-signal strategy's bar topic does NOT
# match (venue token is mid-token), so without a matching feed every order is
# rejected "no market". The SandboxBookFeeder publishes an L2 delta feed that
# DOES match — a GENERAL sandbox-mode fix, so it rides the shared assembly layer
# and is injected automatically whenever mode==sandbox (not declared per strategy).

_BOOK_FEEDER = "tinohelm.sandbox_book_feeder:SandboxBookFeeder"


def test_sandbox_mode_auto_injects_book_feeder(strategy_toml: Path) -> None:
    """A sandbox pod gets the shared fill-fuel feeder with zero strategy config.

    strategy_toml is mode=sandbox and declares NO [[actors]] — the feeder must
    appear purely because the mode demands it.
    """

    from tinohelm.config import build_actor_imports

    file = TinoStrategyFile.load(strategy_toml)
    actors = [a.actor_path for a in build_actor_imports(file)]
    assert _BOOK_FEEDER in actors


def test_live_mode_does_not_inject_book_feeder(tmp_path: Path) -> None:
    """Live pods route to a real venue — the sim feeder must NEVER be injected
    (it would open a redundant live L2 delta subscription for nothing)."""

    body = textwrap.dedent(
        """
        [strategy]
        id = "LIVE-001"
        trader_id = "TINO-001"
        mode = "live"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "live.toml"
    path.write_text(body)

    from tinohelm.config import build_actor_imports

    file = TinoStrategyFile.load(path)
    actors = [a.actor_path for a in build_actor_imports(file)]
    assert _BOOK_FEEDER not in actors


def test_sandbox_fill_fuel_opt_out_skips_feeder(tmp_path: Path) -> None:
    """A strategy that already subscribes quotes/book for its signal keeps the
    sim book alive on its own; ``[sandbox] fill_fuel = false`` opts out of the
    injected feeder so there's no duplicate subscription."""

    body = textwrap.dedent(
        """
        [strategy]
        id = "SELFFED-001"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [factories.data]
        [factories.exec]

        [sandbox]
        fill_fuel = false
        """,
    ).strip()
    path = tmp_path / "selffed.toml"
    path.write_text(body)

    from tinohelm.config import build_actor_imports

    file = TinoStrategyFile.load(path)
    actors = [a.actor_path for a in build_actor_imports(file)]
    assert _BOOK_FEEDER not in actors


def test_sandbox_fill_fuel_filters_l2_from_external_redis_stream(
    strategy_toml: Path,
) -> None:
    """OOM GUARD (companion to the book feeder): when the fill-fuel feeder is
    injected it subscribes 28 symbols' L2 deltas @500ms. Those MUST be excluded
    from EXTERNAL Redis publication via MessageBusConfig.types_filter — nothing
    consumes them cross-pod (no external_streams), so externalising every delta
    OOM'd the box. types_filter is list[type] (TOML can't express it), so the
    assembler injects it in Python whenever fill_fuel is on. In-process delivery
    to the sim is unaffected (component.pyx dispatches subscribers before the
    external-publish gate), so fills + parity hold."""

    from nautilus_trader.model.data import (
        OrderBookDelta,
        OrderBookDeltas,
    )

    os.environ.setdefault("BYBIT_API_KEY", "test-key")
    file = TinoStrategyFile.load(strategy_toml)  # mode=sandbox, fill_fuel default on
    config = build_trading_node_config(file)

    tf = config.message_bus.types_filter or []
    assert OrderBookDelta in tf, "L2 deltas must be filtered from external Redis"
    assert OrderBookDeltas in tf, "batched L2 deltas must be filtered too"


def test_sandbox_fill_fuel_off_does_not_filter_l2(tmp_path: Path) -> None:
    """Negative control: with fill_fuel off there is no injected L2 feed, so the
    assembler must NOT silently inject the filter (a strategy feeding its own
    book may legitimately want its deltas externalised)."""

    body = textwrap.dedent(
        """
        [strategy]
        id = "NOFILTER-001"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [factories.data]
        [factories.exec]

        [sandbox]
        fill_fuel = false
        """,
    ).strip()
    path = tmp_path / "nofilter.toml"
    path.write_text(body)

    file = TinoStrategyFile.load(path)
    config = build_trading_node_config(file)

    assert not (config.message_bus.types_filter or [])


def test_live_recovery_can_be_disabled_via_toml(tmp_path: Path) -> None:
    """A LIVE operator who genuinely wants a clean-slate boot — typical for the
    first run of a new strategy, or after ``flush_on_start`` — sets
    ``[recovery] enabled = false``. This must override the mode-driven True
    default (in live, recovery defaults ON, so the opt-out is meaningful here).
    """

    body = textwrap.dedent(
        """
        [strategy]
        id = "FRESH-001"
        trader_id = "TINO-001"
        mode = "live"
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


def test_sandbox_recovery_can_be_forced_on_via_toml(tmp_path: Path) -> None:
    """Explicit ``[recovery] enabled = true`` overrides the sandbox False
    default — the mirror of the live opt-out. Proves the mode-aware default is
    only a default: an operator who deliberately wants a sandbox to reload
    Trader state can still force it. ``flush_on_start`` is independently still
    its sandbox default (True) since this TOML sets no ``[cache] flush_on_start``.
    """

    body = textwrap.dedent(
        """
        [strategy]
        id = "SBX-PERSIST-001"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [cache]
        redis_url = "redis://redis:6379/0"

        [recovery]
        enabled = true

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "sbx-persist.toml"
    path.write_text(body)

    file = TinoStrategyFile.load(path)
    config = build_trading_node_config(file)
    assert config.load_state is True
    assert config.save_state is True


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


# ─── instrument_provider subclass assembly ─────────────────────────────────────
#
# NT's ``BinanceDataClientConfig.instrument_provider`` field is *declared* as the
# base ``InstrumentProviderConfig`` (adapters/binance/config.py:72), so msgspec
# decodes a TOML ``instrument_provider`` dict against the base class and rejects
# venue-specific fields like ``query_commission_rates`` with a ValidationError at
# ``node.build()``. The fix lives in the config-assembly glue: when an
# ``instrument_provider`` section carries a ``path`` pointing at the concrete
# subclass FQN, we construct the whole venue client config as a NT *instance*
# (provider subclass already built), which NT's
# ``TradingNodeConfig._parse_client_config`` (live/config.py:342) passes through
# untouched (verified in venv 1.227.0: ``parsed is client`` is True). No
# ``path`` → unchanged dict passthrough, so venues without venue-specific
# provider fields are unaffected.

_BINANCE_IP_PATH = "nautilus_trader.adapters.binance.config:BinanceInstrumentProviderConfig"
_BINANCE_DC_PATH = "nautilus_trader.adapters.binance.config:BinanceDataClientConfig"
_BINANCE_EXEC_PATH = "nautilus_trader.adapters.binance.config:BinanceExecClientConfig"


def _binance_provider_toml(
    *,
    mode: str = "live",
    provider_path: str | None = _BINANCE_IP_PATH,
    environment: str = "LIVE",
) -> str:
    """A strategy TOML whose BINANCE data client carries a venue-specific
    ``instrument_provider`` (``query_commission_rates`` + ``load_ids``).

    ``provider_path`` controls the ``path`` hint on the provider section:
    a value enables the subclass upgrade; ``None`` omits it (legacy dict
    passthrough). ``mode`` flips between the live and sandbox assembly paths.
    ``environment`` is written verbatim into both the data and exec client
    ``config`` sections (TinoHelm just transports the string; NT's
    ``BinanceEnvironment`` enum coerces and routes it) — used to exercise the
    DEMO switch where both clients route to ``demo-fapi``.
    """

    provider_path_line = f'path = "{provider_path}"\n' if provider_path else ""
    return textwrap.dedent(
        f"""
        [strategy]
        id = "OI-001"
        trader_id = "TINO-001"
        mode = "{mode}"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [factories.data]
        BINANCE = "nautilus_trader.adapters.binance.factories:BinanceLiveDataClientFactory"

        [factories.exec]
        BINANCE = "nautilus_trader.adapters.binance.factories:BinanceLiveExecClientFactory"

        [data_clients.BINANCE]
        path = "{_BINANCE_DC_PATH}"
        [data_clients.BINANCE.config]
        api_key = "$ENV:BINANCE_API_KEY"
        api_secret = "$ENV:BINANCE_API_SECRET"
        account_type = "USDT_FUTURES"
        environment = "{environment}"
        [data_clients.BINANCE.config.instrument_provider]
        {provider_path_line}query_commission_rates = true
        load_ids = ["DOGEUSDT-PERP.BINANCE", "BTCUSDT-PERP.BINANCE"]

        [exec_clients.BINANCE]
        path = "{_BINANCE_EXEC_PATH}"
        [exec_clients.BINANCE.config]
        api_key = "$ENV:BINANCE_API_KEY"
        api_secret = "$ENV:BINANCE_API_SECRET"
        account_type = "USDT_FUTURES"
        environment = "{environment}"
        """,
    ).strip()


@pytest.fixture(autouse=True)
def _binance_keys(monkeypatch) -> None:
    # NT validates that API key strings exist when constructing the config.
    monkeypatch.setenv("BINANCE_API_KEY", "binance-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "binance-secret")


def test_data_client_provider_path_builds_subclass_instance(tmp_path: Path) -> None:
    """A provider section with ``path`` must yield a constructed NT subclass instance.

    This is the crux: the venue client config comes back as a
    ``BinanceDataClientConfig`` *instance* (not a dict), and its
    ``.instrument_provider`` is the ``BinanceInstrumentProviderConfig`` subclass
    carrying ``query_commission_rates`` — the field that crashed msgspec base-class
    decoding. ``load_ids`` are real ``InstrumentId`` objects, not strings (we do
    the str→InstrumentId conversion NT's dec_hook would otherwise do).
    """

    from nautilus_trader.adapters.binance.common.enums import BinanceAccountType
    from nautilus_trader.adapters.binance.config import (
        BinanceDataClientConfig,
        BinanceInstrumentProviderConfig,
    )
    from nautilus_trader.model.identifiers import InstrumentId

    from tinohelm.config import build_data_clients

    path = tmp_path / "oi.toml"
    path.write_text(_binance_provider_toml())
    file = TinoStrategyFile.load(path)

    clients = build_data_clients(file)
    dc = clients["BINANCE"]
    assert isinstance(dc, BinanceDataClientConfig)
    assert isinstance(dc.instrument_provider, BinanceInstrumentProviderConfig)
    assert dc.instrument_provider.query_commission_rates is True
    assert dc.instrument_provider.load_ids == frozenset(
        {
            InstrumentId.from_str("DOGEUSDT-PERP.BINANCE"),
            InstrumentId.from_str("BTCUSDT-PERP.BINANCE"),
        },
    )
    # Regression: building the instance via msgspec.convert (not a bare
    # constructor) must coerce the ``account_type`` TOML string to the
    # ``BinanceAccountType`` enum. A bare ``BinanceDataClientConfig(**kwargs)``
    # would leave it a raw str, and NT later does ``account_type.is_spot``
    # (binance/common/urls.py) → ``AttributeError: 'str' object has no
    # attribute 'is_spot'`` at node.build(). Assert it's the real enum.
    assert isinstance(dc.account_type, BinanceAccountType)
    assert dc.account_type is BinanceAccountType.USDT_FUTURES


def test_provider_instance_passes_through_nt_parse_unchanged(tmp_path: Path) -> None:
    """Regression guard + NT-upgrade canary: the constructed instance must survive
    NT's ``_parse_client_config`` untouched.

    NT routes a constructed ``LiveDataClientConfig`` instance through岔路1 —
    returned as-is, zero decoding (live/config.py:347-351). Asserting
    ``parsed is dc`` pins the dict-vs-instance success point: if a future NT
    changes instance passthrough, this breaks loudly here rather than at a pod's
    ``node.build()``.
    """

    from nautilus_trader.live.config import LiveDataClientConfig, TradingNodeConfig

    from tinohelm.config import build_data_clients

    path = tmp_path / "oi.toml"
    path.write_text(_binance_provider_toml())
    file = TinoStrategyFile.load(path)

    dc = build_data_clients(file)["BINANCE"]
    parsed = TradingNodeConfig._parse_client_config(dc, LiveDataClientConfig)
    assert parsed is dc
    assert parsed.instrument_provider.query_commission_rates is True


def test_data_client_without_provider_path_stays_dict(tmp_path: Path) -> None:
    """No ``path`` on the provider section → unchanged dict passthrough.

    Venues with no venue-specific provider fields (the common case) must be
    untouched: NT decodes their provider dict against the base class as before.
    The upgrade is purely additive — only a ``path`` hint opts in. ``$ENV:`` refs
    are still expanded in the passthrough dict.
    """

    from tinohelm.config import build_data_clients

    path = tmp_path / "plain.toml"
    path.write_text(_binance_provider_toml(provider_path=None))
    file = TinoStrategyFile.load(path)

    dc = build_data_clients(file)["BINANCE"]
    assert isinstance(dc, dict)
    serialized = repr(dc)
    assert "binance-key" in serialized  # $ENV expanded
    assert "$ENV:" not in serialized


def test_sandbox_exec_client_receives_data_clients_provider(tmp_path: Path) -> None:
    """Sandbox path: the provider declared under ``data_clients`` must flow into
    the ``SandboxExecutionClientConfig`` instance.

    This also fixes a pre-existing blind spot: ``build_exec_clients`` used to read
    ``instrument_provider`` from the *exec_clients* section, where it never lives
    (the universe's single source of truth is the data_clients section), so the
    sandbox sim never received the per-symbol commission provider. The sandbox
    venue is now a constructed ``SandboxExecutionClientConfig`` instance whose
    ``.instrument_provider`` is the Binance subclass with
    ``query_commission_rates``.
    """

    from nautilus_trader.adapters.binance.config import BinanceInstrumentProviderConfig
    from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig

    from tinohelm.config import build_exec_clients

    path = tmp_path / "oi-sandbox.toml"
    path.write_text(_binance_provider_toml(mode="sandbox"))
    file = TinoStrategyFile.load(path)
    assert file.mode == "sandbox"

    clients = build_exec_clients(file)
    sb = clients["BINANCE"]
    assert isinstance(sb, SandboxExecutionClientConfig)
    assert isinstance(sb.instrument_provider, BinanceInstrumentProviderConfig)
    assert sb.instrument_provider.query_commission_rates is True


def test_provider_path_pointing_at_non_provider_raises(tmp_path: Path) -> None:
    """A ``path`` that does not resolve to an ``InstrumentProviderConfig`` subclass
    must fail loudly, naming the path.

    This locks the zero-hardcoded-venue-table contract: every venue goes through
    the same resolve-and-validate path, with an ``issubclass`` guard mirroring how
    the runner validates factory class paths. A typo'd or wrong FQN crashes at
    assembly with a clear message, not at an opaque NT decode.
    """

    from tinohelm.config import build_data_clients

    bogus = "nautilus_trader.model.identifiers:InstrumentId"  # real class, wrong base
    path = tmp_path / "bogus.toml"
    path.write_text(_binance_provider_toml(provider_path=bogus))
    file = TinoStrategyFile.load(path)

    with pytest.raises(TypeError, match=bogus):
        build_data_clients(file)


# ─── Binance DEMO (testnet real matching) assembly ─────────────────────────────
#
# Switching oi_momentum_lowvol from sandbox to Binance DEMO is a config-assembly
# concern, not a code one: NT's BinanceEnvironment enum already has a DEMO member
# (adapters/binance/common/enums.py) that routes the HTTP/WS base URLs to
# demo-fapi.binance.com (adapters/binance/common/urls.py), and DEMO runs the same
# real BinanceLiveExecClientFactory / BinanceFuturesExecutionClient as LIVE — only
# the base_url differs. So in TinoHelm terms DEMO == ``mode=live`` (real exec
# client + reconciliation ON + load_state/save_state ON) plus a TOML
# ``environment="DEMO"`` string that TinoHelm only TRANSPORTS — it never reads,
# imports, or branches on the value (NT owns the routing). These tests pin that
# the existing mode=live assembly already produces the DEMO shape, with NT doing
# the enum coercion. They never start a real TradingNode and never reach the
# network: NT's decode is simulated via ``msgspec.convert`` exactly as
# ``TradingNodeConfig._parse_client_config`` would do at ``node.build()``.
#
# Note the asymmetric seam (design §2.1 vs §2.2): the exec_clients section has no
# ``instrument_provider.path`` so ``_upgrade_client_config`` returns the dict
# untouched (config.py:617) — NT decodes it later. The data_clients section DOES
# carry a provider ``path`` so it is upgraded to a constructed instance here. Both
# end up coercing ``environment="DEMO"`` through msgspec, but via different paths,
# so the exec and data cases are asserted separately (test 1 vs test 4).


def test_demo_exec_client_is_real_binance_pointed_at_demo(tmp_path: Path) -> None:
    """mode=live + environment=DEMO assembles a real BinanceExecClientConfig (NOT
    sandbox) whose environment coerces to ``BinanceEnvironment.DEMO``.

    This is the core contract of the sandbox→DEMO switch: the exec client must be
    the *real* one (so orders hit the demo venue and reconciliation has a backend
    to replay), and its ``environment`` must route to demo-fapi. The exec section
    carries no ``instrument_provider.path``, so ``build_exec_clients`` hands NT the
    untouched dict (design §2.1); we simulate NT's decode with ``msgspec.convert``
    — the same coercion ``_parse_client_config`` performs at ``node.build()`` —
    and assert the enum lands on DEMO with ``is_live`` False (DEMO is a non-live
    environment per enums.py, yet still a real exec client). The mirror image of
    ``test_sandbox_mode_swaps_every_venue_exec_client``: there the venue becomes
    Sandbox, here it must NOT.
    """

    import msgspec
    from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
    from nautilus_trader.adapters.binance.config import BinanceExecClientConfig
    from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig

    from tinohelm.config import build_exec_clients

    path = tmp_path / "oi-demo.toml"
    path.write_text(_binance_provider_toml(mode="live", environment="DEMO"))
    file = TinoStrategyFile.load(path)
    assert file.mode == "live"

    exec_client = build_exec_clients(file)["BINANCE"]
    # mode=live must NOT swap the venue to the in-process sim.
    assert not isinstance(exec_client, SandboxExecutionClientConfig)
    assert "Sandbox" not in repr(exec_client)
    # exec section has no provider path → dict passthrough (design §2.1).
    assert isinstance(exec_client, dict)
    assert exec_client["config"]["environment"] == "DEMO"

    # Simulate NT's decode (no TradingNode, no network): the string coerces to the
    # DEMO enum member, and the config type is the real exec client.
    decoded = msgspec.convert(exec_client["config"], type=BinanceExecClientConfig)
    assert decoded.environment is BinanceEnvironment.DEMO
    assert decoded.environment.is_live is False  # DEMO is a non-live environment


def test_demo_runs_with_reconciliation_on(tmp_path: Path) -> None:
    """mode=live (which DEMO reuses) keeps continuous reconciliation ON.

    Reconciliation against the demo venue is the whole point of the switch —
    it is what replays the real account/positions/orders after a pod restart
    (the sandbox in-process sim returns empty reports, so it cannot). The gate
    is ``reconciliation = mode != "sandbox"`` (config.py), so DEMO=mode=live
    yields True with no extra logic — the exact opposite of
    ``test_exec_engine_disables_reconciliation_in_sandbox``.
    """

    from tinohelm.config import build_exec_engine_config

    path = tmp_path / "oi-demo.toml"
    path.write_text(_binance_provider_toml(mode="live", environment="DEMO"))
    file = TinoStrategyFile.load(path)

    exec_cfg = build_exec_engine_config(file.raw, mode=file.mode)
    assert exec_cfg.reconciliation is True


def test_demo_persists_state_with_zero_toml_ceremony(tmp_path: Path) -> None:
    """DEMO must persist state across restarts WITH NO extra TOML.

    This is the core of the mode-aware fix. DEMO == ``mode=live``, and a live pod
    must keep its Redis cache (flush_on_start OFF) so reconciliation can replay
    the real account/positions/orders, and reload the Trader's own state
    (load/save ON, in-flight bracket tracking etc.). Previously this only held if
    the operator hand-edited ``[cache] flush_on_start=false`` + ``[recovery]
    enabled=true`` — a static TOML that ``make deploy MODE=live`` does NOT rewrite,
    so a strategy left at the sandbox-ephemeral values would silently drop state
    on every DEMO restart. Now the defaults follow ``mode`` (sandbox→ephemeral,
    live→persistent), so this TOML sets NEITHER switch and still persists —
    proving the redeploy footgun is gone. ``flush_on_start=true`` would make NT
    skip ``load_cache()`` and wipe the cache on boot, defeating recovery.
    """

    from tinohelm.config import build_cache_config, build_trading_node_config

    body = textwrap.dedent(
        """
        [strategy]
        id = "OI-DEMO-001"
        trader_id = "TINO-001"
        mode = "live"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [cache]
        redis_url = "redis://redis:6379/0"

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "oi-demo-persist.toml"
    path.write_text(body)
    file = TinoStrategyFile.load(path)

    # No [cache] flush_on_start, no [recovery] — pure mode-driven persistence.
    cache_cfg = build_cache_config(file.raw, mode=file.mode)
    assert cache_cfg is not None
    assert cache_cfg.flush_on_start is False

    node_cfg = build_trading_node_config(file)
    assert node_cfg.load_state is True
    assert node_cfg.save_state is True


def test_demo_data_client_coerces_environment_via_instance_upgrade(tmp_path: Path) -> None:
    """data side: environment=DEMO coerces through the instance-upgrade path.

    Unlike exec (dict passthrough), the data_clients section carries an
    ``instrument_provider.path``, so ``build_data_clients`` builds a constructed
    ``BinanceDataClientConfig`` instance (design §2.2/§2.3). The data side MUST
    also switch to DEMO: NT's ``load_ids_async`` unconditionally hits the signed
    ``/fapi/v2/account`` endpoint, so leaving data on LIVE with the demo key (or a
    mainnet key the switch is meant to escape) would fail at universe load. Assert
    the constructed instance's ``environment`` is the DEMO enum and the provider
    subclass survives the upgrade.
    """

    from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
    from nautilus_trader.adapters.binance.config import (
        BinanceDataClientConfig,
        BinanceInstrumentProviderConfig,
    )

    from tinohelm.config import build_data_clients

    path = tmp_path / "oi-demo.toml"
    path.write_text(_binance_provider_toml(mode="live", environment="DEMO"))
    file = TinoStrategyFile.load(path)

    dc = build_data_clients(file)["BINANCE"]
    assert isinstance(dc, BinanceDataClientConfig)
    assert dc.environment is BinanceEnvironment.DEMO
    assert isinstance(dc.instrument_provider, BinanceInstrumentProviderConfig)
    assert dc.instrument_provider.query_commission_rates is True


# ─── [sandbox] persist — restart recovery opt-in ────────────────────────────


def test_sandbox_persist_keeps_cache_and_enables_recovery(tmp_path: Path) -> None:
    """``[sandbox] persist = true`` flips a sandbox pod from ephemeral to durable.

    Two mode-aware defaults move together so the persisted cache is actually
    usable across a restart:
      * ``flush_on_start`` defaults False (sandbox would normally wipe → True),
        so NT's ``load_cache`` reads back the Account/Order/Position history.
      * ``load_state`` / ``save_state`` default True (sandbox would normally be
        False), so the Trader/strategy on_save/on_load state survives too.
    Neither ``[cache] flush_on_start`` nor ``[recovery] enabled`` is set here, so
    this exercises the pure persist-driven defaults.
    """

    body = textwrap.dedent(
        """
        [strategy]
        id = "SBX-PERSIST-002"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [cache]
        redis_url = "redis://redis:6379/0"

        [sandbox]
        persist = true

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "sbx-persist2.toml"
    path.write_text(body)

    file = TinoStrategyFile.load(path)
    config = build_trading_node_config(file)
    assert config.cache is not None
    assert config.cache.flush_on_start is False  # cache kept across restart
    assert config.load_state is True
    assert config.save_state is True


def test_sandbox_without_persist_stays_ephemeral(tmp_path: Path) -> None:
    """A sandbox pod with no ``[sandbox] persist`` (or persist=false) stays
    ephemeral — the existing default must NOT regress: cache wiped, no
    state reload.
    """

    body = textwrap.dedent(
        """
        [strategy]
        id = "SBX-EPH-001"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [cache]
        redis_url = "redis://redis:6379/0"

        [sandbox]
        persist = false

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "sbx-eph.toml"
    path.write_text(body)

    file = TinoStrategyFile.load(path)
    config = build_trading_node_config(file)
    assert config.cache is not None
    assert config.cache.flush_on_start is True
    assert config.load_state is False
    assert config.save_state is False


def test_build_cache_config_persist_flag_drives_flush_default() -> None:
    """build_cache_config honours sandbox_persist at its own seam.

    sandbox + persist → flush_on_start False (keep cache);
    sandbox + no persist → True (wipe, the existing default);
    an explicit [cache] flush_on_start still wins over both.
    """

    from tinohelm.config import build_cache_config

    bare = {"cache": {"redis_url": "redis://r:6379/0"}}
    assert build_cache_config(bare, mode="sandbox", sandbox_persist=True).flush_on_start is False
    assert build_cache_config(bare, mode="sandbox", sandbox_persist=False).flush_on_start is True
    # live is unaffected by the persist flag.
    assert build_cache_config(bare, mode="live", sandbox_persist=True).flush_on_start is False

    # Explicit flush_on_start beats the persist-driven default both directions.
    forced_wipe = {"cache": {"redis_url": "redis://r:6379/0", "flush_on_start": True}}
    assert (
        build_cache_config(forced_wipe, mode="sandbox", sandbox_persist=True).flush_on_start is True
    )


def test_explicit_recovery_beats_sandbox_persist_default(tmp_path: Path) -> None:
    """An explicit ``[recovery] enabled`` still wins over the persist default.

    persist would default recovery ON, but an operator who sets
    ``[recovery] enabled = false`` must be honoured (the persist flag is only a
    default-flipper, never an override of explicit config).
    """

    body = textwrap.dedent(
        """
        [strategy]
        id = "SBX-PERSIST-NOREC-001"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [sandbox]
        persist = true

        [recovery]
        enabled = false

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "sbx-persist-norec.toml"
    path.write_text(body)

    file = TinoStrategyFile.load(path)
    config = build_trading_node_config(file)
    assert config.load_state is False
    assert config.save_state is False


def test_controller_import_carries_mode_and_sandbox_persist(tmp_path: Path) -> None:
    """build_controller_import must hand BridgeActor its mode + persist gate.

    The BridgeActor's on_start/on_stop recovery hooks are guarded by
    ``mode=="sandbox" and sandbox_persist``; both values reach it only through
    its ImportableControllerConfig.config, so the assembler must populate them.
    """

    from tinohelm.config import build_controller_import

    body = textwrap.dedent(
        """
        [strategy]
        id = "CTRL-001"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [sandbox]
        persist = true

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "ctrl.toml"
    path.write_text(body)

    file = TinoStrategyFile.load(path)
    controller = build_controller_import(file)
    assert controller.config["mode"] == "sandbox"
    assert controller.config["sandbox_persist"] is True
    # The existing fields are still present and unchanged.
    assert controller.config["strategy_id"] == "CTRL-001"
    assert controller.config["command_topic"] == "commands.tinohelm.CTRL-001"


def test_controller_import_defaults_for_live_pod(tmp_path: Path) -> None:
    """A live pod (no [sandbox] section) carries mode=live + sandbox_persist=False
    so the BridgeActor guard never fires — the live zero-impact contract.
    """

    from tinohelm.config import build_controller_import

    body = textwrap.dedent(
        """
        [strategy]
        id = "CTRL-LIVE-001"
        trader_id = "TINO-001"
        mode = "live"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "ctrl-live.toml"
    path.write_text(body)

    file = TinoStrategyFile.load(path)
    controller = build_controller_import(file)
    assert controller.config["mode"] == "live"
    assert controller.config["sandbox_persist"] is False


def test_sandbox_persist_with_base_currency_warns(tmp_path: Path, caplog) -> None:
    """persist=true + ``[sandbox] base_currency`` → warning (multi-currency needed).

    All-currency restore replays a multi-currency AccountState, which NT's
    base.pyx rejects when the account is single-base-currency. We don't force the
    base_currency away (respect explicit config) but must warn loudly that
    multi-currency recovery will fail.
    """

    import logging

    from tinohelm.config import build_exec_clients

    body = textwrap.dedent(
        """
        [strategy]
        id = "SBX-BASECCY-001"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [sandbox]
        persist = true
        base_currency = "USDT"

        [exec_clients.BINANCE]

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "sbx-baseccy.toml"
    path.write_text(body)

    file = TinoStrategyFile.load(path)
    with caplog.at_level(logging.WARNING, logger="tinohelm.config"):
        build_exec_clients(file)

    assert any("base_currency" in r.message for r in caplog.records)


def test_sandbox_persist_without_base_currency_no_warning(tmp_path: Path, caplog) -> None:
    """persist=true with NO base_currency (the supported multi-currency setup)
    must NOT emit the base_currency warning.
    """

    import logging

    from tinohelm.config import build_exec_clients

    body = textwrap.dedent(
        """
        [strategy]
        id = "SBX-NOBASECCY-001"
        trader_id = "TINO-001"
        mode = "sandbox"
        class = "strategies.example.strategy:ExampleStrategy"
        config_class = "strategies.example.strategy:ExampleStrategyConfig"

        [strategy.params]

        [sandbox]
        persist = true

        [exec_clients.BINANCE]

        [factories.data]
        [factories.exec]
        """,
    ).strip()
    path = tmp_path / "sbx-nobaseccy.toml"
    path.write_text(body)

    file = TinoStrategyFile.load(path)
    with caplog.at_level(logging.WARNING, logger="tinohelm.config"):
        build_exec_clients(file)

    assert not any("base_currency" in r.message for r in caplog.records)
