"""Tests for pure helpers in tinohelm.api.routes.node."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tinohelm.api.routes.node import _enrich_strategy_meta, _PAPER_CONFIG_DEFAULT


def _make_settings(strategies_dir: Path) -> SimpleNamespace:
    """Minimal `Settings` stand-in — only `paths.strategies` is used."""
    return SimpleNamespace(paths=SimpleNamespace(strategies=str(strategies_dir)))


# ---------------------------------------------------------------------------
# _PAPER_CONFIG_DEFAULT
# ---------------------------------------------------------------------------


class TestPaperConfigDefault:
    def test_fields_and_values(self):
        assert _PAPER_CONFIG_DEFAULT == {
            "starting_capital": 10000.0,
            "fee_rate": 0.0004,
            "slippage_model": "binance-default",
            "latency_ms": 0,
        }


# ---------------------------------------------------------------------------
# _enrich_strategy_meta
# ---------------------------------------------------------------------------


class TestEnrichStrategyMeta:
    def test_strategy_without_yaml_gets_empty_defaults(self, tmp_path: Path):
        settings = _make_settings(tmp_path)
        strategies = {"simple_strat": {"state": "running"}}
        result = _enrich_strategy_meta(strategies, settings)
        assert result["simple_strat"]["symbols"] == []
        assert result["simple_strat"]["interval"] == ""
        # Existing fields preserved
        assert result["simple_strat"]["state"] == "running"

    def test_portfolio_yaml_values_merged(self, tmp_path: Path):
        (tmp_path / "crypto_momentum").mkdir()
        (tmp_path / "crypto_momentum" / "portfolio.yaml").write_text(
            "symbols:\n  - BTCUSDT-PERP\n  - ETHUSDT-PERP\ninterval: '15m'\n"
        )
        settings = _make_settings(tmp_path)
        strategies = {"crypto_momentum": {"state": "running"}}
        result = _enrich_strategy_meta(strategies, settings)
        assert result["crypto_momentum"]["symbols"] == ["BTCUSDT-PERP", "ETHUSDT-PERP"]
        assert result["crypto_momentum"]["interval"] == "15m"

    def test_malformed_yaml_defaults_to_empty(self, tmp_path: Path):
        (tmp_path / "broken").mkdir()
        (tmp_path / "broken" / "portfolio.yaml").write_text("this: is: not: valid: yaml:")
        settings = _make_settings(tmp_path)
        strategies = {"broken": {}}
        result = _enrich_strategy_meta(strategies, settings)
        # On yaml parse error, the except branch sets defaults via setdefault
        assert result["broken"]["symbols"] == []
        assert result["broken"]["interval"] == ""

    def test_empty_yaml_file(self, tmp_path: Path):
        (tmp_path / "empty").mkdir()
        (tmp_path / "empty" / "portfolio.yaml").write_text("")
        settings = _make_settings(tmp_path)
        strategies = {"empty": {}}
        result = _enrich_strategy_meta(strategies, settings)
        # yaml.safe_load("") → None; helper falls back to {} → defaults
        assert result["empty"]["symbols"] == []
        assert result["empty"]["interval"] == ""

    def test_yaml_without_interval_key(self, tmp_path: Path):
        (tmp_path / "only_symbols").mkdir()
        (tmp_path / "only_symbols" / "portfolio.yaml").write_text(
            "symbols:\n  - SOLUSDT-PERP\n"
        )
        settings = _make_settings(tmp_path)
        strategies = {"only_symbols": {}}
        result = _enrich_strategy_meta(strategies, settings)
        assert result["only_symbols"]["symbols"] == ["SOLUSDT-PERP"]
        assert result["only_symbols"]["interval"] == ""

    def test_multiple_strategies_independent(self, tmp_path: Path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "portfolio.yaml").write_text("symbols:\n  - X\ninterval: '1m'\n")
        (tmp_path / "b").mkdir()
        # No portfolio.yaml for b
        settings = _make_settings(tmp_path)

        strategies = {"a": {}, "b": {}}
        result = _enrich_strategy_meta(strategies, settings)
        assert result["a"]["symbols"] == ["X"]
        assert result["a"]["interval"] == "1m"
        assert result["b"]["symbols"] == []
        assert result["b"]["interval"] == ""

    def test_empty_strategies_dict(self, tmp_path: Path):
        settings = _make_settings(tmp_path)
        assert _enrich_strategy_meta({}, settings) == {}

    def test_preserves_existing_symbols_when_no_yaml(self, tmp_path: Path):
        """setdefault means if caller already populated symbols, they survive."""
        settings = _make_settings(tmp_path)
        strategies = {"already_has_symbols": {"symbols": ["PRE"], "interval": "5m"}}
        result = _enrich_strategy_meta(strategies, settings)
        assert result["already_has_symbols"]["symbols"] == ["PRE"]
        assert result["already_has_symbols"]["interval"] == "5m"

    def test_yaml_overrides_existing(self, tmp_path: Path):
        """If portfolio.yaml exists, its values overwrite any previous data."""
        (tmp_path / "override").mkdir()
        (tmp_path / "override" / "portfolio.yaml").write_text(
            "symbols: ['NEW']\ninterval: '1h'\n"
        )
        settings = _make_settings(tmp_path)
        strategies = {"override": {"symbols": ["OLD"], "interval": "5m"}}
        result = _enrich_strategy_meta(strategies, settings)
        assert result["override"]["symbols"] == ["NEW"]
        assert result["override"]["interval"] == "1h"
