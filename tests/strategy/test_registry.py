"""Tests for strategy registry — mixed directory scanning."""
from __future__ import annotations

import pytest
from pathlib import Path

from tinohelm.strategy.registry import scan_strategies


@pytest.fixture
def mixed_strategies_dir(tmp_path):
    """Create a strategies dir with both single files and portfolio folders."""
    strategies = tmp_path / "strategies"
    strategies.mkdir()

    # Single .py file (non-NT, will be skipped by scanner since no Strategy subclass)
    (strategies / "simple_ma.py").write_text("""\
class SimpleMA:
    pass
class SimpleMAConfig:
    pass
""")

    # Portfolio folder with portfolio.yaml
    portfolio = strategies / "crypto_momentum"
    portfolio.mkdir()
    (portfolio / "portfolio.yaml").write_text("""\
strategy:
  class: "strategy:BTCMultiFactor"

symbols:
  - BTCUSDT-PERP
  - ETHUSDT-PERP
  - XRPUSDT-PERP

interval: 5m

actors:
  - name: risk_guard
    params:
      max_drawdown_pct: -0.1
""")

    # Underscore file (should be skipped)
    (strategies / "_helper.py").write_text("class Helper: pass")

    # Non-portfolio folder (no portfolio.yaml, should be skipped)
    other = strategies / "data_cache"
    other.mkdir()
    (other / "readme.txt").write_text("not a strategy")

    return strategies


class TestScanStrategies:
    """Test mixed directory scanning."""

    def test_detects_portfolio_folder(self, mixed_strategies_dir):
        results = scan_strategies(mixed_strategies_dir)
        portfolio_results = [r for r in results if r["type"] == "portfolio"]

        assert len(portfolio_results) == 1
        p = portfolio_results[0]
        assert p["name"] == "crypto_momentum"
        assert p["symbols"] == ["BTCUSDT-PERP", "ETHUSDT-PERP", "XRPUSDT-PERP"]
        assert p["interval"] == "5m"
        assert "risk_guard" in p["actors"]

    def test_portfolio_has_type_field(self, mixed_strategies_dir):
        results = scan_strategies(mixed_strategies_dir)
        for r in results:
            assert "type" in r
            assert r["type"] in ("single", "portfolio")

    def test_skips_underscore_files(self, mixed_strategies_dir):
        results = scan_strategies(mixed_strategies_dir)
        names = [r["name"] for r in results]
        assert "_helper" not in names

    def test_skips_non_portfolio_folders(self, mixed_strategies_dir):
        results = scan_strategies(mixed_strategies_dir)
        names = [r["name"] for r in results]
        assert "data_cache" not in names

    def test_empty_dir(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        results = scan_strategies(empty)
        assert results == []

    def test_nonexistent_dir(self, tmp_path):
        results = scan_strategies(tmp_path / "nonexistent")
        assert results == []

    def test_portfolio_metadata_extraction(self, mixed_strategies_dir):
        results = scan_strategies(mixed_strategies_dir)
        portfolio = [r for r in results if r["type"] == "portfolio"][0]

        assert portfolio["strategy_class"] == "BTCMultiFactor"
        assert "code_hash" in portfolio
        assert len(portfolio["code_hash"]) == 64  # SHA-256
