"""Behavior tests for tinohelm.strategy_runner — factory registration by mode.

``_register_factories`` is the seam that decides whether a venue's exec client
is the *real* NT factory (declared in TOML) or NT's in-process
``SandboxLiveExecClientFactory``. The decision is mode-driven
(strategy_runner.py: ``if file.mode == "sandbox"``), so the sandbox→DEMO switch
relies on it: DEMO reuses ``mode=live``, which must take the ``else`` branch and
register the real ``BinanceLiveExecClientFactory`` (so orders reach the demo
venue, not a sim).

We never start a real ``TradingNode`` — registering factories on a live node
would drag in msgbus/cache/clock wiring. Instead a ``_NodeSpy`` records each
``add_*_client_factory(venue, cls)`` call so we can assert on the registered
classes directly (the spy pattern from ``tests/test_bridge_actor.py``).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory

from tinohelm.config import TinoStrategyFile
from tinohelm.strategy_runner import _register_factories


class _NodeSpy:
    """Records factory registrations instead of wiring a real TradingNode."""

    def __init__(self) -> None:
        self.data_factories: list[tuple[str, type]] = []
        self.exec_factories: list[tuple[str, type]] = []

    def add_data_client_factory(self, venue: str, cls: type) -> None:
        self.data_factories.append((venue, cls))

    def add_exec_client_factory(self, venue: str, cls: type) -> None:
        self.exec_factories.append((venue, cls))


def _binance_factory_toml(tmp_path: Path, *, mode: str) -> Path:
    """A minimal TOML declaring BINANCE data + exec factories, varying only mode."""

    body = textwrap.dedent(
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
        """,
    ).strip()
    path = tmp_path / "factory.toml"
    path.write_text(body)
    return path


def test_live_mode_registers_real_binance_exec_factory(tmp_path: Path, monkeypatch) -> None:
    """mode=live (which DEMO reuses) registers the TOML-declared real exec factory.

    The sandbox→DEMO switch depends on this branch: DEMO runs as ``mode=live``,
    so ``_register_factories`` must NOT override the venue with
    ``SandboxLiveExecClientFactory`` — it must register the real
    ``BinanceLiveExecClientFactory`` so orders hit the demo venue. Asserting the
    exact class (not just "not sandbox") pins the resolve-from-TOML path.
    """

    from nautilus_trader.adapters.binance.factories import (
        BinanceLiveDataClientFactory,
        BinanceLiveExecClientFactory,
    )

    monkeypatch.delenv("TINO_MODE", raising=False)
    file = TinoStrategyFile.load(_binance_factory_toml(tmp_path, mode="live"))
    assert file.mode == "live"

    node = _NodeSpy()
    _register_factories(node, file)  # type: ignore[arg-type]  # _NodeSpy is a duck-typed double

    assert node.exec_factories == [("BINANCE", BinanceLiveExecClientFactory)]
    assert node.exec_factories[0][1] is not SandboxLiveExecClientFactory
    assert node.data_factories == [("BINANCE", BinanceLiveDataClientFactory)]


def test_sandbox_mode_overrides_exec_factory_with_sandbox(tmp_path: Path, monkeypatch) -> None:
    """mode=sandbox overrides every venue's exec factory with the NT sim factory.

    The mirror image of the live case — it locks the mode-driven branch so the
    DEMO/live assertion above is meaningful (the override genuinely fires only in
    sandbox). Data factories are never overridden in either mode.
    """

    from nautilus_trader.adapters.binance.factories import BinanceLiveDataClientFactory

    monkeypatch.delenv("TINO_MODE", raising=False)
    file = TinoStrategyFile.load(_binance_factory_toml(tmp_path, mode="sandbox"))
    assert file.mode == "sandbox"

    node = _NodeSpy()
    _register_factories(node, file)  # type: ignore[arg-type]  # _NodeSpy is a duck-typed double

    assert node.exec_factories == [("BINANCE", SandboxLiveExecClientFactory)]
    # Data factory is mode-independent — always the real one.
    assert node.data_factories == [("BINANCE", BinanceLiveDataClientFactory)]
