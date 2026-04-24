"""Shared test fixtures for TinoHelm test suite."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

@pytest.fixture
def project_root() -> Path:
    """Return the project root directory."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def strategies_dir(tmp_path: Path) -> Path:
    """Create and return a temporary strategies directory."""
    d = tmp_path / "strategies"
    d.mkdir()
    return d


@pytest.fixture
def actors_dir(tmp_path: Path) -> Path:
    """Create and return a temporary actors directory."""
    d = tmp_path / "actors"
    d.mkdir()
    return d


@pytest.fixture
def catalog_dir(tmp_path: Path) -> Path:
    """Create and return a temporary data catalog directory."""
    d = tmp_path / "catalog"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Create a temporary config directory with default.yaml."""
    d = tmp_path / "config"
    d.mkdir()
    (d / "default.yaml").write_text(
        "server:\n  host: '0.0.0.0'\n  port: 8000\n"
        "database:\n  url: 'postgresql+asyncpg://test:test@localhost/test'\n"
        "redis:\n  url: 'redis://localhost:6379'\n"
    )
    return d


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """Remove all TINO_ environment variables for a clean test environment."""
    for key in list(os.environ):
        if key.startswith("TINO_"):
            monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# Strategy file helpers
# ---------------------------------------------------------------------------

_MINIMAL_STRATEGY_PY = """\
from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy


class MinimalConfig(StrategyConfig, frozen=True):
    instrument_id: str = "BTCUSDT-PERP.BINANCE"
    bar_type: str = "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
    order_id_tag: str = "000"


class MinimalStrategy(Strategy):
    def __init__(self, config: MinimalConfig) -> None:
        super().__init__(config)

    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass
"""


@pytest.fixture
def minimal_strategy_file(strategies_dir: Path) -> Path:
    """Write a minimal valid NT strategy file and return its path."""
    f = strategies_dir / "minimal_strat.py"
    f.write_text(_MINIMAL_STRATEGY_PY)
    return f


_NON_NT_MODULE_PY = """\
class NotAStrategy:
    pass

class NotAConfig:
    pass
"""


@pytest.fixture
def non_nt_module_file(tmp_path: Path) -> Path:
    """Write a Python file that has classes but no NT subclasses."""
    f = tmp_path / "not_nt.py"
    f.write_text(_NON_NT_MODULE_PY)
    return f


_SYNTAX_ERROR_PY = """\
def broken(
    # missing closing paren
"""


@pytest.fixture
def syntax_error_file(tmp_path: Path) -> Path:
    """Write a Python file with a syntax error."""
    f = tmp_path / "bad_syntax.py"
    f.write_text(_SYNTAX_ERROR_PY)
    return f


# ---------------------------------------------------------------------------
# PathRegistry overrides
# ---------------------------------------------------------------------------

from tinohelm.core.paths import paths as _paths  # noqa: E402


@pytest.fixture
def paths_override():
    """Return a helper to install PathRegistry overrides with auto-teardown.

    Usage::

        def test_x(tmp_path, paths_override):
            paths_override("funding_rates", tmp_path)
            # ... test body ...
        # teardown automatically clears overrides via reset_overrides()
    """
    def _set(field: str, value: Path) -> None:
        _paths.override(field, Path(value))

    yield _set
    _paths.reset_overrides()
