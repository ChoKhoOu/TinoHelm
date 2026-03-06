"""Tests for portfolio config parsing and loading."""
from __future__ import annotations

import pytest
from pathlib import Path
from tinohelm.portfolio.config import (
    PortfolioConfig,
    ActorRef,
    AccountSettings,
    load_portfolio_config,
    _load_from_yaml,
)


@pytest.fixture
def tmp_strategies(tmp_path):
    """Create a temporary strategies directory."""
    return tmp_path / "strategies"


@pytest.fixture
def valid_yaml(tmp_path):
    """Create a valid portfolio.yaml and return the folder path."""
    folder = tmp_path / "crypto_momentum"
    folder.mkdir()
    yaml_content = """\
strategy:
  class: "strategy:BTCMultiFactor"
  config: "strategy:BTCMultiFactorConfig"

symbols:
  - BTCUSDT-PERP
  - ETHUSDT-PERP

interval: 5m

params:
  lookback_window: 100
  confidence_threshold: 0.6

actors:
  - name: risk_guard
    params:
      daily_stop_loss_pct: -0.02
      max_drawdown_pct: -0.1

account:
  starting_balance: 10000
  currency: USDT
  leverage: 5
"""
    (folder / "portfolio.yaml").write_text(yaml_content)
    return folder


@pytest.fixture
def minimal_yaml(tmp_path):
    """Create a minimal valid portfolio.yaml (no actors, no account, no params)."""
    folder = tmp_path / "simple_portfolio"
    folder.mkdir()
    yaml_content = """\
strategy:
  class: "strategy:SimpleMA"

symbols:
  - BTCUSDT-PERP

interval: 1h
"""
    (folder / "portfolio.yaml").write_text(yaml_content)
    return folder


class TestPortfolioConfigParsing:
    """Test portfolio.yaml parsing into PortfolioConfig."""

    def test_valid_yaml_full(self, valid_yaml):
        config = _load_from_yaml(valid_yaml / "portfolio.yaml", valid_yaml)

        assert config.strategy_class == "strategy:BTCMultiFactor"
        assert config.config_class == "strategy:BTCMultiFactorConfig"
        assert config.symbols == ["BTCUSDT-PERP", "ETHUSDT-PERP"]
        assert config.interval == "5m"
        assert config.params["lookback_window"] == 100
        assert config.params["confidence_threshold"] == 0.6
        assert len(config.actors) == 1
        assert config.actors[0].name == "risk_guard"
        assert config.actors[0].params["daily_stop_loss_pct"] == -0.02
        assert config.account.starting_balance == 10000
        assert config.account.currency == "USDT"
        assert config.account.leverage == 5
        assert config.implicit is False

    def test_minimal_yaml(self, minimal_yaml):
        config = _load_from_yaml(minimal_yaml / "portfolio.yaml", minimal_yaml)

        assert config.strategy_class == "strategy:SimpleMA"
        assert config.config_class == "strategy:SimpleMAConfig"  # auto-derived
        assert config.symbols == ["BTCUSDT-PERP"]
        assert config.interval == "1h"
        assert config.params == {}
        assert config.actors == []
        assert config.account.starting_balance == 10000  # default
        assert config.implicit is False

    def test_no_actors_section(self, tmp_path):
        folder = tmp_path / "no_actors"
        folder.mkdir()
        (folder / "portfolio.yaml").write_text("""\
strategy:
  class: "strategy:Foo"
symbols:
  - BTCUSDT-PERP
interval: 5m
actors: []
""")
        config = _load_from_yaml(folder / "portfolio.yaml", folder)
        assert config.actors == []

    def test_missing_strategy_class_raises(self, tmp_path):
        folder = tmp_path / "bad"
        folder.mkdir()
        (folder / "portfolio.yaml").write_text("""\
strategy:
  config: "strategy:FooConfig"
symbols:
  - BTCUSDT-PERP
interval: 5m
""")
        with pytest.raises(ValueError, match="strategy.class"):
            _load_from_yaml(folder / "portfolio.yaml", folder)

    def test_missing_symbols_raises(self, tmp_path):
        folder = tmp_path / "bad2"
        folder.mkdir()
        (folder / "portfolio.yaml").write_text("""\
strategy:
  class: "strategy:Foo"
interval: 5m
""")
        with pytest.raises(ValueError, match="symbols"):
            _load_from_yaml(folder / "portfolio.yaml", folder)

    def test_missing_interval_raises(self, tmp_path):
        folder = tmp_path / "bad3"
        folder.mkdir()
        (folder / "portfolio.yaml").write_text("""\
strategy:
  class: "strategy:Foo"
symbols:
  - BTCUSDT-PERP
""")
        with pytest.raises(ValueError, match="interval"):
            _load_from_yaml(folder / "portfolio.yaml", folder)

    def test_missing_strategy_section_raises(self, tmp_path):
        folder = tmp_path / "bad4"
        folder.mkdir()
        (folder / "portfolio.yaml").write_text("symbols:\n  - BTCUSDT-PERP\n")
        with pytest.raises(ValueError, match="strategy"):
            _load_from_yaml(folder / "portfolio.yaml", folder)


class TestLoadPortfolioConfig:
    """Test the load_portfolio_config dispatcher."""

    def test_load_from_folder_path(self, valid_yaml):
        config = load_portfolio_config(str(valid_yaml))
        assert config.strategy_class == "strategy:BTCMultiFactor"
        assert len(config.symbols) == 2

    def test_load_from_name(self, valid_yaml):
        # The folder is named "crypto_momentum" inside tmp_path
        config = load_portfolio_config(
            "crypto_momentum",
            strategies_dir=valid_yaml.parent,
        )
        assert config.strategy_class == "strategy:BTCMultiFactor"

    def test_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Cannot find"):
            load_portfolio_config("nonexistent", strategies_dir=tmp_path)

    def test_single_file_requires_symbol(self, tmp_path):
        # Create a dummy .py file (won't actually import, just test path resolution)
        strategies = tmp_path / "strategies"
        strategies.mkdir()
        (strategies / "dummy.py").write_text("# empty")
        with pytest.raises(ValueError, match="--symbol"):
            load_portfolio_config("dummy", strategies_dir=strategies)

    def test_single_file_requires_interval(self, tmp_path):
        strategies = tmp_path / "strategies"
        strategies.mkdir()
        (strategies / "dummy.py").write_text("# empty")
        with pytest.raises(ValueError, match="--interval"):
            load_portfolio_config("dummy", strategies_dir=strategies, symbol="BTCUSDT-PERP")


class TestImplicitWrapping:
    """Test auto-wrapping single .py files as implicit portfolio."""

    def test_implicit_config_from_yaml_is_false(self, valid_yaml):
        config = load_portfolio_config(str(valid_yaml))
        assert config.implicit is False

    def test_symbol_format_warning(self, tmp_path, caplog):
        """Unrecognized symbol format logs a warning but does not raise."""
        folder = tmp_path / "warn_test"
        folder.mkdir()
        (folder / "portfolio.yaml").write_text("""\
strategy:
  class: "strategy:Foo"
symbols:
  - badformat
interval: 5m
""")
        import logging
        with caplog.at_level(logging.WARNING):
            config = _load_from_yaml(folder / "portfolio.yaml", folder)
        assert config.symbols == ["badformat"]
        assert "does not match expected format" in caplog.text
