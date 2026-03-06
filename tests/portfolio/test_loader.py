"""Tests for portfolio loader — strategy and actor instantiation."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from tinohelm.portfolio.config import PortfolioConfig, ActorRef, AccountSettings
from tinohelm.portfolio.loader import (
    create_strategies,
    create_actors,
    scan_actors,
    _normalize_symbol,
    _make_bar_type_str,
    _load_module_from_file,
)


class TestNormalization:
    """Test symbol and bar type string helpers."""

    def test_normalize_symbol(self):
        assert _normalize_symbol("BTCUSDT-PERP") == "BTCUSDT-PERP.BINANCE"

    def test_normalize_symbol_already_suffixed(self):
        assert _normalize_symbol("BTCUSDT-PERP.BINANCE") == "BTCUSDT-PERP.BINANCE"

    def test_make_bar_type_str(self):
        result = _make_bar_type_str("BTCUSDT-PERP", "5m")
        assert result == "BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL"

    def test_make_bar_type_str_1h(self):
        result = _make_bar_type_str("ETHUSDT-PERP", "1h")
        assert result == "ETHUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"


class TestCreateStrategies:
    """Test create_strategies with mocked NT imports."""

    def test_creates_one_per_symbol(self, tmp_path):
        """Verify N symbols -> N strategy instances."""
        # Create a dummy strategy file with NT-like classes
        strategy_file = tmp_path / "my_strat.py"
        strategy_file.write_text("""\
import msgspec

class FakeStrategyConfig(msgspec.Struct):
    instrument_id: str = ""
    bar_type: str = ""
    lookback: int = 50

class FakeStrategy:
    def __init__(self, config):
        self.config = config
""")

        config = PortfolioConfig(
            strategy_class="my_strat:FakeStrategy",
            config_class="my_strat:FakeStrategyConfig",
            symbols=["BTCUSDT-PERP", "ETHUSDT-PERP", "XRPUSDT-PERP"],
            interval="5m",
            params={"lookback": 100},
            source_path=tmp_path,
        )

        # Mock NT type conversions since we're using fake classes
        with patch("tinohelm.portfolio.loader.get_config_field_names") as mock_fields:
            mock_fields.return_value = {"instrument_id", "bar_type", "lookback"}

            # Mock NT imports
            mock_iid = MagicMock()
            mock_iid.from_str = lambda s: s
            mock_bt = MagicMock()
            mock_bt.from_str = lambda s: s

            with patch.dict("sys.modules", {
                "nautilus_trader.model.identifiers": MagicMock(InstrumentId=mock_iid),
                "nautilus_trader.model.data": MagicMock(BarType=mock_bt),
            }):
                strategies = create_strategies(config)

        assert len(strategies) == 3
        # Each should have its own instrument_id
        symbols_found = [s.config.instrument_id for s in strategies]
        assert "BTCUSDT-PERP.BINANCE" in symbols_found
        assert "ETHUSDT-PERP.BINANCE" in symbols_found
        assert "XRPUSDT-PERP.BINANCE" in symbols_found

    def test_empty_symbols_returns_empty(self, tmp_path):
        strategy_file = tmp_path / "empty_strat.py"
        strategy_file.write_text("class S: pass\nclass SC: pass\n")

        config = PortfolioConfig(
            strategy_class="empty_strat:S",
            config_class="empty_strat:SC",
            symbols=[],
            interval="5m",
            source_path=tmp_path,
        )

        with patch("tinohelm.portfolio.loader.get_config_field_names", return_value=set()):
            with patch.dict("sys.modules", {
                "nautilus_trader.model.identifiers": MagicMock(),
                "nautilus_trader.model.data": MagicMock(),
            }):
                strategies = create_strategies(config)

        assert strategies == []


class TestCreateActors:
    """Test create_actors loading from different sources."""

    def test_empty_actors_returns_empty(self):
        config = PortfolioConfig(
            strategy_class="m:S",
            config_class="m:SC",
            symbols=["BTCUSDT-PERP"],
            interval="5m",
            actors=[],
        )
        result = create_actors(config)
        assert result == []

    def test_actor_not_found_raises(self, tmp_path):
        config = PortfolioConfig(
            strategy_class="m:S",
            config_class="m:SC",
            symbols=["BTCUSDT-PERP"],
            interval="5m",
            actors=[ActorRef(name="nonexistent")],
        )
        with pytest.raises(FileNotFoundError, match="nonexistent"):
            create_actors(config, actors_dir=tmp_path)

    def test_actor_ref_needs_name_or_class(self):
        config = PortfolioConfig(
            strategy_class="m:S",
            config_class="m:SC",
            symbols=["BTCUSDT-PERP"],
            interval="5m",
            actors=[ActorRef()],  # no name, no class
        )
        with pytest.raises(ValueError, match="name.*class"):
            create_actors(config, actors_dir=Path("/tmp"))


class TestCreateActorsLocalPath:
    """Test create_actors loading from local portfolio folder."""

    def test_actor_from_local_class_path(self, tmp_path):
        """Load actor from ./module:ClassName relative to portfolio folder."""
        # Create a fake actor module in the portfolio folder
        actor_file = tmp_path / "custom_monitor.py"
        actor_file.write_text("""\
class MyMonitorConfig:
    pass

class MyMonitor:
    def __init__(self, config=None):
        self.config = config
""")

        config = PortfolioConfig(
            strategy_class="m:S",
            config_class="m:SC",
            symbols=["BTCUSDT-PERP"],
            interval="5m",
            actors=[ActorRef(class_path="./custom_monitor:MyMonitor")],
            source_path=tmp_path,
        )

        # Mock the actor class discovery to avoid NT base class check
        with patch("tinohelm.portfolio.loader._discover_actor_classes") as mock_discover:
            mock_cls = MagicMock(__name__="MyMonitor")
            mock_config_cls = None
            mock_discover.return_value = (mock_cls, mock_config_cls)
            actors = create_actors(config, actors_dir=tmp_path / "nonexistent")

        assert len(actors) == 1
        mock_cls.assert_called_once()


class TestSymbolProfilesValidation:
    """Test SYMBOL_PROFILES warning for unrecognized symbols."""

    def test_warns_on_unrecognized_symbol(self, caplog):
        """Symbols not in SYMBOL_PROFILES should log a warning."""
        from tinohelm.portfolio.loader import _warn_unrecognized_symbols, _nt_symbol_to_jesse

        # Create a mock strategy class with a module that has SYMBOL_PROFILES
        mock_module = MagicMock()
        mock_module.SYMBOL_PROFILES = {
            "BTC-USDT": {"enabled": True},
            "ETH-USDT": {"enabled": True},
        }
        mock_module.DEFAULT_PROFILE = {"enabled": False}

        mock_cls = MagicMock()
        mock_cls.__module__ = "_test_sym_profiles"

        import sys
        sys.modules["_test_sym_profiles"] = mock_module
        try:
            import logging
            with caplog.at_level(logging.WARNING):
                _warn_unrecognized_symbols(mock_cls, ["BTCUSDT-PERP", "DOGEUSDT-PERP"])
            assert "DOGEUSDT-PERP" in caplog.text
            assert "no entry in SYMBOL_PROFILES" in caplog.text
        finally:
            del sys.modules["_test_sym_profiles"]

    def test_no_warning_when_all_recognized(self, caplog):
        from tinohelm.portfolio.loader import _warn_unrecognized_symbols

        mock_module = MagicMock()
        mock_module.SYMBOL_PROFILES = {
            "BTC-USDT": {"enabled": True},
        }
        mock_cls = MagicMock()
        mock_cls.__module__ = "_test_sym_ok"

        import sys
        sys.modules["_test_sym_ok"] = mock_module
        try:
            import logging
            with caplog.at_level(logging.WARNING):
                _warn_unrecognized_symbols(mock_cls, ["BTCUSDT-PERP"])
            assert "no entry" not in caplog.text
        finally:
            del sys.modules["_test_sym_ok"]

    def test_nt_symbol_to_jesse_conversion(self):
        from tinohelm.portfolio.loader import _nt_symbol_to_jesse
        assert _nt_symbol_to_jesse("BTCUSDT-PERP") == "BTC-USDT"
        assert _nt_symbol_to_jesse("ETHUSDT-PERP.BINANCE") == "ETH-USDT"
        assert _nt_symbol_to_jesse("XRPUSDT-PERP") == "XRP-USDT"
        assert _nt_symbol_to_jesse("DOGEUSDT-PERP") == "DOGE-USDT"


class TestScanActors:
    """Test scanning the actors directory."""

    def test_empty_dir_returns_empty(self, tmp_path):
        result = scan_actors(tmp_path)
        assert result == []

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        result = scan_actors(tmp_path / "nonexistent")
        assert result == []

    def test_skips_underscore_files(self, tmp_path):
        (tmp_path / "_helper.py").write_text("class Helper: pass")
        result = scan_actors(tmp_path)
        assert result == []
