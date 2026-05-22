"""Tests for sandbox/live node actor wiring.

Verifies that node startup code correctly references the decomposed
actors (SnapshotActor, CommandActor, etc.) and wires lifecycle deps.
These are source-level checks since full node startup requires a
NautilusTrader runtime.
"""
from __future__ import annotations


def _read_source(module) -> str:
    """Read the source code of a module."""
    with open(module.__file__) as f:
        return f.read()


class TestCommonActorIntegration:
    """Verify _common.py has the shared actor wiring logic."""

    def test_common_imports_strategy_loader(self):
        from tinohelm.node import _common
        source = _read_source(_common)
        assert "create_strategies" in source, "_common.py should call create_strategies"
        assert "create_actors" in source, "_common.py should call create_actors"

    def test_common_imports_node_actors(self):
        from tinohelm.node import _common
        source = _read_source(_common)
        assert "SnapshotActor" in source, "_common.py should reference SnapshotActor"
        assert "CommandActor" in source, "_common.py should reference CommandActor"
        assert "DbWriterActor" in source, "_common.py should reference DbWriterActor"
        assert "HealthActor" in source, "_common.py should reference HealthActor"
        assert "MetricsActor" in source, "_common.py should reference MetricsActor"

    def test_common_adds_actors_to_trader(self):
        from tinohelm.node import _common
        source = _read_source(_common)
        assert "add_actor(snapshot_actor)" in source
        assert "add_actor(command_actor)" in source
        assert "add_actor(health_actor)" in source
        assert "add_actor(metrics_actor)" in source


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


class TestActorsPackage:
    """Verify actors package exports all 5 actors."""

    def test_actors_importable(self):
        from tinohelm.node.actors import (
            SnapshotActor, CommandActor, DbWriterActor, HealthActor, MetricsActor,
        )
        assert SnapshotActor is not None
        assert CommandActor is not None
        assert DbWriterActor is not None
        assert HealthActor is not None
        assert MetricsActor is not None

    def test_actor_configs_importable(self):
        from tinohelm.node.actors import (
            SnapshotActorConfig, CommandActorConfig, DbWriterActorConfig,
            HealthActorConfig, MetricsActorConfig,
        )
        assert SnapshotActorConfig is not None
        assert CommandActorConfig is not None
        assert DbWriterActorConfig is not None
        assert HealthActorConfig is not None
        assert MetricsActorConfig is not None


class TestFactoryConfig:
    """Verify node/factory.py exists with config builder."""

    def test_factory_module_exists(self):
        from tinohelm.node import factory
        assert hasattr(factory, 'build_trading_node_config'), \
            "factory.py should have build_trading_node_config"
