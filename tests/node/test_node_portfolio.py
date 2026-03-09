"""Tests for sandbox/live node portfolio config integration (Task 8.5).

Verifies that node startup code correctly references portfolio_loader
and wires BridgeActor. These are source-level checks since full node
startup requires a NautilusTrader runtime.
"""
from __future__ import annotations


def _read_source(module) -> str:
    """Read the source code of a module."""
    with open(module.__file__) as f:
        return f.read()


class TestSandboxPortfolioIntegration:
    """Verify sandbox.py uses portfolio_loader for strategy/actor creation."""

    def test_sandbox_imports_portfolio_loader(self):
        """Sandbox should import from portfolio.loader."""
        from tinohelm.node import sandbox
        source = _read_source(sandbox)
        assert "create_strategies" in source, "sandbox.py should call create_strategies"
        assert "create_actors" in source, "sandbox.py should call create_actors"

    def test_sandbox_imports_bridge_actor(self):
        """Sandbox should import BridgeActor."""
        from tinohelm.node import sandbox
        source = _read_source(sandbox)
        assert "BridgeActor" in source, "sandbox.py should reference BridgeActor"
        assert "BridgeActorConfig" in source, "sandbox.py should reference BridgeActorConfig"

    def test_sandbox_adds_bridge_to_trader(self):
        """Sandbox should add BridgeActor via node.trader.add_actor."""
        from tinohelm.node import sandbox
        source = _read_source(sandbox)
        assert "add_actor(bridge_actor)" in source or "add_actor(bridge" in source, \
            "sandbox.py should add bridge_actor to the trader"

    def test_live_imports_portfolio_loader(self):
        """Live node should import from portfolio.loader."""
        from tinohelm.node import live
        source = _read_source(live)
        assert "create_strategies" in source or "load_portfolio_config" in source, \
            "live.py should import portfolio loader functions"

    def test_live_imports_bridge_actor(self):
        """Live node should import BridgeActor."""
        from tinohelm.node import live
        source = _read_source(live)
        assert "BridgeActor" in source, "live.py should reference BridgeActor"
        assert "BridgeActorConfig" in source, "live.py should reference BridgeActorConfig"

    def test_live_adds_bridge_to_trader(self):
        """Live node should add BridgeActor via node.trader.add_actor."""
        from tinohelm.node import live
        source = _read_source(live)
        assert "add_actor(bridge_actor)" in source or "add_actor(bridge" in source, \
            "live.py should add bridge_actor to the trader"


class TestFactoryAcceptsPortfolioConfig:
    """Verify node/factory.py accepts portfolio config."""

    def test_factory_module_exists(self):
        """node/factory.py should exist and be importable."""
        from tinohelm.node import factory
        assert hasattr(factory, 'build_trading_node_config') or hasattr(factory, 'build_node_config'), \
            "factory.py should have a config builder function"

    def test_factory_supports_portfolio_config_param(self):
        """Factory config builder should accept portfolio_config in input dict."""
        from tinohelm.node import factory
        source = _read_source(factory)
        assert "portfolio" in source.lower(), \
            "factory.py should reference portfolio config"
