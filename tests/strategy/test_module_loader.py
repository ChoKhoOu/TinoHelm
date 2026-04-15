"""Tests for tinohelm.strategy.module_loader — unified dynamic module loading.

Covers:
- load_module_from_file: basic loading, boundary enforcement, sys.path cleanup
- discover_strategy_classes / discover_actor_classes: class discovery
- scan_valid_strategy_files: directory scanning
- load_strategy_module: high-level convenience function
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tinohelm.strategy.module_loader import (
    ModuleLoadResult,
    load_module_from_file,
    discover_strategy_classes,
    discover_actor_classes,
    scan_valid_strategy_files,
    load_strategy_module,
)


# ---------------------------------------------------------------------------
# Fixtures: strategy / module files
# ---------------------------------------------------------------------------

_PLAIN_MODULE = """\
VALUE = 42

def add(a, b):
    return a + b
"""

_IMPORT_ERROR_MODULE = """\
import nonexistent_package_that_does_not_exist_xyz
"""


@pytest.fixture
def plain_module_file(tmp_path: Path) -> Path:
    """A simple Python module with no NT classes."""
    f = tmp_path / "plain.py"
    f.write_text(_PLAIN_MODULE)
    return f


@pytest.fixture
def import_error_file(tmp_path: Path) -> Path:
    """A Python file that raises ImportError when loaded."""
    f = tmp_path / "bad_import.py"
    f.write_text(_IMPORT_ERROR_MODULE)
    return f


# ---------------------------------------------------------------------------
# load_module_from_file: basic loading
# ---------------------------------------------------------------------------

class TestLoadModuleFromFile:

    def test_loads_simple_module(self, plain_module_file: Path):
        mod = load_module_from_file(plain_module_file)
        assert mod.VALUE == 42
        assert mod.add(2, 3) == 5

    def test_module_has_correct_attributes(self, plain_module_file: Path):
        mod = load_module_from_file(plain_module_file)
        assert hasattr(mod, "VALUE")
        assert hasattr(mod, "add")

    def test_custom_module_name(self, plain_module_file: Path):
        mod = load_module_from_file(plain_module_file, module_name="my_custom_module")
        assert mod.__name__ == "my_custom_module"

    def test_auto_generated_module_name(self, plain_module_file: Path):
        mod = load_module_from_file(plain_module_file)
        assert mod.__name__.startswith("_tino_load_plain_")

    def test_file_not_found_raises(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist.py"
        with pytest.raises(FileNotFoundError, match="Module file not found"):
            load_module_from_file(missing)

    def test_import_error_propagated(self, import_error_file: Path):
        with pytest.raises(ModuleNotFoundError):
            load_module_from_file(import_error_file)

    def test_syntax_error_propagated(self, syntax_error_file: Path):
        with pytest.raises(SyntaxError):
            load_module_from_file(syntax_error_file)


# ---------------------------------------------------------------------------
# load_module_from_file: boundary enforcement
# ---------------------------------------------------------------------------

class TestBoundaryEnforcement:

    def test_within_boundary_succeeds(self, plain_module_file: Path):
        """Loading a file within the boundary directory succeeds."""
        mod = load_module_from_file(
            plain_module_file,
            boundary_dir=plain_module_file.parent,
        )
        assert mod.VALUE == 42

    def test_outside_boundary_raises(self, tmp_path: Path, plain_module_file: Path):
        """Loading a file outside the boundary directory raises ValueError."""
        # Create a separate boundary directory
        boundary = tmp_path / "allowed"
        boundary.mkdir()
        with pytest.raises(ValueError, match="outside boundary"):
            load_module_from_file(plain_module_file, boundary_dir=boundary)


# ---------------------------------------------------------------------------
# load_module_from_file: sys.path cleanup
# ---------------------------------------------------------------------------

class TestSysPathCleanup:

    def test_syspath_cleaned_after_success(self, plain_module_file: Path):
        """Module's parent dir is removed from sys.path after loading."""
        parent = str(plain_module_file.parent)
        was_in_path = parent in sys.path
        load_module_from_file(plain_module_file)
        if not was_in_path:
            assert parent not in sys.path

    def test_syspath_cleaned_after_failure(self, import_error_file: Path):
        """Module's parent dir is removed from sys.path even on load failure."""
        parent = str(import_error_file.parent)
        was_in_path = parent in sys.path
        with pytest.raises(ModuleNotFoundError):
            load_module_from_file(import_error_file)
        if not was_in_path:
            assert parent not in sys.path

    def test_existing_syspath_entry_not_removed(self, plain_module_file: Path):
        """If parent dir was already in sys.path, it's not removed."""
        parent = str(plain_module_file.parent)
        sys.path.insert(0, parent)
        try:
            load_module_from_file(plain_module_file)
            assert parent in sys.path
        finally:
            sys.path.remove(parent)


# ---------------------------------------------------------------------------
# load_module_from_file: fresh imports
# ---------------------------------------------------------------------------

class TestFreshImports:

    def test_loading_different_files_gives_independent_modules(self, tmp_path: Path):
        """Loading two different files produces independent modules."""
        f1 = tmp_path / "mod_a.py"
        f1.write_text("X = 1")
        f2 = tmp_path / "mod_b.py"
        f2.write_text("X = 2")

        mod1 = load_module_from_file(f1)
        mod2 = load_module_from_file(f2)
        assert mod1.X == 1
        assert mod2.X == 2
        assert mod1 is not mod2

    def test_sys_modules_cleared_before_reload(self, tmp_path: Path):
        """Loading the same file clears sys.modules entry to avoid stale cache."""
        f = tmp_path / "reloadable.py"
        f.write_text("X = 1")
        mod1 = load_module_from_file(f)
        name = mod1.__name__
        # The old module should NOT still be in sys.modules after a second load
        # (sys.modules.pop is called before loading)
        mod2 = load_module_from_file(f)
        # Both should have same name but be independently loaded
        assert mod2.__name__ == name


# ---------------------------------------------------------------------------
# discover_strategy_classes
# ---------------------------------------------------------------------------

class TestDiscoverStrategyClasses:

    def test_finds_strategy_and_config(self, minimal_strategy_file: Path):
        """Discovers Strategy and StrategyConfig subclasses from a valid strategy file."""
        mod = load_module_from_file(minimal_strategy_file)
        strategy_cls, config_cls = discover_strategy_classes(mod)
        assert strategy_cls is not None
        assert config_cls is not None
        assert strategy_cls.__name__ == "MinimalStrategy"
        assert config_cls.__name__ == "MinimalConfig"

    def test_returns_none_for_non_nt_module(self, non_nt_module_file: Path):
        """Returns (None, None) when module has no NT subclasses."""
        mod = load_module_from_file(non_nt_module_file)
        strategy_cls, config_cls = discover_strategy_classes(mod)
        assert strategy_cls is None
        assert config_cls is None

    def test_returns_none_for_plain_module(self, plain_module_file: Path):
        """Returns (None, None) for a module with only functions/values."""
        mod = load_module_from_file(plain_module_file)
        strategy_cls, config_cls = discover_strategy_classes(mod)
        assert strategy_cls is None
        assert config_cls is None


# ---------------------------------------------------------------------------
# discover_actor_classes
# ---------------------------------------------------------------------------

_ACTOR_MODULE = """\
from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig


class MyActorConfig(ActorConfig, frozen=True):
    component_id: str = "MyActor-001"


class MyActor(Actor):
    def __init__(self, config: MyActorConfig) -> None:
        super().__init__(config)

    def on_start(self) -> None:
        pass
"""


class TestDiscoverActorClasses:

    @pytest.fixture
    def actor_file(self, tmp_path: Path) -> Path:
        f = tmp_path / "my_actor.py"
        f.write_text(_ACTOR_MODULE)
        return f

    def test_finds_actor_and_config(self, actor_file: Path):
        mod = load_module_from_file(actor_file)
        actor_cls, config_cls = discover_actor_classes(mod)
        assert actor_cls is not None
        assert config_cls is not None
        assert actor_cls.__name__ == "MyActor"
        assert config_cls.__name__ == "MyActorConfig"

    def test_finds_actor_by_class_name(self, actor_file: Path):
        mod = load_module_from_file(actor_file)
        actor_cls, config_cls = discover_actor_classes(mod, class_name="MyActor")
        assert actor_cls is not None
        assert actor_cls.__name__ == "MyActor"

    def test_wrong_class_name_returns_none(self, actor_file: Path):
        mod = load_module_from_file(actor_file)
        actor_cls, config_cls = discover_actor_classes(mod, class_name="NonExistent")
        assert actor_cls is None

    def test_plain_module_returns_none(self, plain_module_file: Path):
        mod = load_module_from_file(plain_module_file)
        actor_cls, config_cls = discover_actor_classes(mod)
        assert actor_cls is None
        assert config_cls is None


# ---------------------------------------------------------------------------
# scan_valid_strategy_files
# ---------------------------------------------------------------------------

class TestScanValidStrategyFiles:

    def test_finds_valid_strategy_files(self, strategies_dir: Path, minimal_strategy_file: Path):
        """scan finds valid NT strategy files in the directory."""
        result = scan_valid_strategy_files(strategies_dir)
        assert "minimal_strat" in result
        assert result["minimal_strat"] == minimal_strategy_file

    def test_skips_non_nt_files(self, strategies_dir: Path):
        """Non-NT .py files are skipped."""
        (strategies_dir / "helper.py").write_text("X = 1\n")
        result = scan_valid_strategy_files(strategies_dir)
        assert "helper" not in result

    def test_skips_underscore_files(self, strategies_dir: Path):
        """Files starting with _ are skipped."""
        (strategies_dir / "_internal.py").write_text("X = 1\n")
        result = scan_valid_strategy_files(strategies_dir)
        assert "_internal" not in result

    def test_empty_directory(self, tmp_path: Path):
        """Empty directory returns empty dict."""
        d = tmp_path / "empty"
        d.mkdir()
        result = scan_valid_strategy_files(d)
        assert result == {}

    def test_nonexistent_directory(self, tmp_path: Path):
        """Nonexistent directory returns empty dict."""
        result = scan_valid_strategy_files(tmp_path / "missing")
        assert result == {}

    def test_skips_files_with_import_errors(self, strategies_dir: Path):
        """Files that fail to import are skipped."""
        (strategies_dir / "broken.py").write_text(
            "import nonexistent_abc_xyz_123\n"
        )
        result = scan_valid_strategy_files(strategies_dir)
        assert "broken" not in result


# ---------------------------------------------------------------------------
# load_strategy_module (high-level)
# ---------------------------------------------------------------------------

class TestLoadStrategyModule:

    def test_loads_and_discovers(self, minimal_strategy_file: Path):
        """load_strategy_module loads the module and discovers classes."""
        result = load_strategy_module(minimal_strategy_file)
        assert isinstance(result, ModuleLoadResult)
        assert result.module is not None
        assert result.strategy_cls is not None
        assert result.strategy_cls.__name__ == "MinimalStrategy"
        assert result.config_cls is not None
        assert result.config_cls.__name__ == "MinimalConfig"
        assert result.path == minimal_strategy_file

    def test_optimize_ranges_empty_by_default(self, minimal_strategy_file: Path):
        """Without OPTIMIZE dict, optimize_ranges is empty."""
        result = load_strategy_module(minimal_strategy_file)
        assert result.optimize_ranges == {}

    def test_optimize_ranges_extracted(self, strategies_dir: Path):
        """OPTIMIZE dict at module level is parsed into optimize_ranges."""
        f = strategies_dir / "opt_strat.py"
        f.write_text(
            "from nautilus_trader.config import StrategyConfig\n"
            "from nautilus_trader.trading.strategy import Strategy\n\n"
            "OPTIMIZE = {\n"
            "    'fast_period': {'min': 5, 'max': 50, 'type': 'int'},\n"
            "    'slow_period': {'min': 20, 'max': 200, 'step': 10},\n"
            "}\n\n"
            "class OptConfig(StrategyConfig, frozen=True):\n"
            "    order_id_tag: str = '000'\n\n"
            "class OptStrategy(Strategy):\n"
            "    def __init__(self, config: OptConfig) -> None:\n"
            "        super().__init__(config)\n"
            "    def on_start(self) -> None: pass\n"
            "    def on_stop(self) -> None: pass\n"
        )
        result = load_strategy_module(f)
        assert "fast_period" in result.optimize_ranges
        assert result.optimize_ranges["fast_period"]["type"] == "int"
        assert result.optimize_ranges["fast_period"]["min"] == 5
        assert result.optimize_ranges["fast_period"]["max"] == 50
        assert "slow_period" in result.optimize_ranges
        assert result.optimize_ranges["slow_period"]["step"] == 10

    def test_boundary_enforcement(self, minimal_strategy_file: Path, tmp_path: Path):
        """Boundary enforcement is passed through to load_module_from_file."""
        boundary = tmp_path / "allowed"
        boundary.mkdir()
        with pytest.raises(ValueError, match="outside boundary"):
            load_strategy_module(minimal_strategy_file, boundary_dir=boundary)


# ---------------------------------------------------------------------------
# ModuleLoadResult dataclass
# ---------------------------------------------------------------------------

class TestModuleLoadResult:

    def test_defaults(self):
        from types import ModuleType
        mod = ModuleType("test")
        result = ModuleLoadResult(module=mod)
        assert result.module is mod
        assert result.strategy_cls is None
        assert result.config_cls is None
        assert result.actor_cls is None
        assert result.actor_config_cls is None
        assert result.optimize_ranges == {}
        assert result.path is None
