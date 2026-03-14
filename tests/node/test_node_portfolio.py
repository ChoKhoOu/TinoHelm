"""Tests for sandbox/live node portfolio config integration (Task 8.5).

Verifies that node startup code correctly references portfolio_loader
and wires BridgeActor. These are source-level checks since full node
startup requires a NautilusTrader runtime.

Shared logic (portfolio loading, BridgeActor wiring, signal handling)
lives in ``_common.py``; sandbox/live delegate via ``load_components``
and ``run_with_signals``.
"""
from __future__ import annotations


def _read_source(module) -> str:
    """Read the source code of a module."""
    with open(module.__file__) as f:
        return f.read()


class TestCommonPortfolioIntegration:
    """Verify _common.py has the shared portfolio/bridge wiring logic."""

    def test_common_imports_portfolio_loader(self):
        from tinohelm.node import _common
        source = _read_source(_common)
        assert "create_strategies" in source, "_common.py should call create_strategies"
        assert "create_actors" in source, "_common.py should call create_actors"

    def test_common_imports_bridge_actor(self):
        from tinohelm.node import _common
        source = _read_source(_common)
        assert "BridgeActor" in source, "_common.py should reference BridgeActor"
        assert "BridgeActorConfig" in source, "_common.py should reference BridgeActorConfig"

    def test_common_adds_bridge_to_trader(self):
        from tinohelm.node import _common
        source = _read_source(_common)
        assert "add_actor(bridge_actor)" in source or "add_actor(bridge" in source, \
            "_common.py should add bridge_actor to the trader"


class TestNodeDelegation:
    """Verify sandbox/live delegate to _common for shared logic."""

    def test_sandbox_delegates_to_common(self):
        from tinohelm.node import sandbox
        source = _read_source(sandbox)
        assert "load_components" in source, "sandbox.py should call load_components"
        assert "run_with_signals" in source, "sandbox.py should call run_with_signals"

    def test_live_delegates_to_common(self):
        from tinohelm.node import live
        source = _read_source(live)
        assert "load_components" in source, "live.py should call load_components"
        assert "run_with_signals" in source, "live.py should call run_with_signals"


class TestFactoryAcceptsPortfolioConfig:
    """Verify node/factory.py accepts portfolio config."""

    def test_factory_module_exists(self):
        from tinohelm.node import factory
        assert hasattr(factory, 'build_trading_node_config') or hasattr(factory, 'build_node_config'), \
            "factory.py should have a config builder function"

    def test_factory_supports_portfolio_config_param(self):
        from tinohelm.node import factory
        source = _read_source(factory)
        assert "portfolio" in source.lower(), \
            "factory.py should reference portfolio config"
