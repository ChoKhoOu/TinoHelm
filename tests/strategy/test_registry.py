"""Tests for strategy registry — directory scanning."""
from __future__ import annotations

import pytest
from pathlib import Path

from tinohelm.strategy.registry import scan_strategies


@pytest.fixture
def strategies_dir(tmp_path):
    """Create a strategies dir with .py files and other items."""
    strategies = tmp_path / "strategies"
    strategies.mkdir()

    # Single .py file (non-NT, will be skipped by scanner since no Strategy subclass)
    (strategies / "simple_ma.py").write_text("""\
class SimpleMA:
    pass
class SimpleMAConfig:
    pass
""")

    # Underscore file (should be skipped)
    (strategies / "_helper.py").write_text("class Helper: pass")

    # Subfolder (should be ignored — scanner only looks at .py files)
    other = strategies / "data_cache"
    other.mkdir()
    (other / "readme.txt").write_text("not a strategy")

    return strategies


class TestScanStrategies:
    """Test directory scanning."""

    def test_only_scans_py_files(self, strategies_dir):
        results = scan_strategies(strategies_dir)
        for r in results:
            assert "type" in r
            assert r["type"] == "single"

    def test_skips_underscore_files(self, strategies_dir):
        results = scan_strategies(strategies_dir)
        names = [r["name"] for r in results]
        assert "_helper" not in names

    def test_skips_folders(self, strategies_dir):
        results = scan_strategies(strategies_dir)
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
