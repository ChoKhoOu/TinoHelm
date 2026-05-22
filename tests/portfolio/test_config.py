"""Tests for strategy bundle config parsing and loading."""
from __future__ import annotations

import pytest
from pathlib import Path
from tinohelm.portfolio.config import (
    StrategyBundle,
    ActorRef,
    AccountSettings,
    load_strategy_bundle,
)


@pytest.fixture
def tmp_strategies(tmp_path):
    """Create a temporary strategies directory."""
    return tmp_path / "strategies"


class TestStrategyBundle:
    """Test StrategyBundle dataclass."""

    def test_basic_fields(self):
        bundle = StrategyBundle(
            strategy_class="mod:Foo",
            config_class="mod:FooConfig",
            symbols=["BTCUSDT-PERP"],
            interval="5m",
        )
        assert bundle.symbols == ["BTCUSDT-PERP"]
        assert bundle.interval == "5m"
        assert bundle.resolved_bar_types == []
        assert bundle.params == {}

    def test_resolved_bar_types_field(self):
        bundle = StrategyBundle(
            strategy_class="mod:Foo",
            config_class="mod:FooConfig",
            symbols=["BTCUSDT-PERP"],
            interval="5m",
            resolved_bar_types=["BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"],
        )
        assert len(bundle.resolved_bar_types) == 1

class TestLoadStrategyBundle:
    """Test load_strategy_bundle dispatcher."""

    def test_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Cannot find"):
            load_strategy_bundle("nonexistent", strategies_dir=tmp_path)

    def test_single_file_requires_symbols(self, tmp_path):
        strategies = tmp_path / "strategies"
        strategies.mkdir()
        (strategies / "dummy.py").write_text("# empty")
        with pytest.raises(ValueError, match="--symbol"):
            load_strategy_bundle("dummy", strategies_dir=strategies)

    def test_single_file_requires_interval(self, tmp_path):
        strategies = tmp_path / "strategies"
        strategies.mkdir()
        (strategies / "dummy.py").write_text("# empty")
        with pytest.raises(ValueError, match="--interval"):
            load_strategy_bundle("dummy", strategies_dir=strategies, symbol="BTCUSDT-PERP")

    def test_symbols_list_param(self, tmp_path):
        strategies = tmp_path / "strategies"
        strategies.mkdir()
        (strategies / "dummy.py").write_text("# empty")
        with pytest.raises(ValueError, match="--interval"):
            load_strategy_bundle(
                "dummy",
                strategies_dir=strategies,
                symbols=["BTCUSDT-PERP", "ETHUSDT-PERP"],
            )
