"""Unit tests for ``tinohelm.factor.registry.Registry``.

Coverage
--------
- scan() after writing a @factor .py to tmp_path → get_spec returns FactorSpec
- rescan after file content change → code_hash detected, new spec returned
- get_kernel(name)(panel) → callable, returns Panel
- get_all_specs() → includes both builtin (mocked) and user factors
- user factor with same name as builtin → user wins (override)
- builtins package missing → Registry gracefully handles ImportError, user factors load
- get_spec for unknown name → None
- get_kernel for unknown name → KeyError
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import inspect

import pandas as pd
import pytest

from tinohelm.factor.registry import Registry
from tinohelm.factor.types import FactorSpec, Panel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_factor_file(directory: Path, factor_name: str, category: str = "测试") -> Path:
    """Write a valid @factor decorated .py file to directory."""
    py_file = directory / f"{factor_name}.py"
    py_file.write_text(
        textwrap.dedent(f"""\
            from tinohelm.factor.decorator import factor
            from tinohelm.factor.types import Panel

            @factor(category="{category}", lookback=5)
            def {factor_name}(close: Panel) -> Panel:
                return close.pct_change(5)
        """),
        encoding="utf-8",
    )
    return py_file


def _write_factor_file_v2(directory: Path, factor_name: str) -> Path:
    """Write a modified version of a factor file (different body → different hash)."""
    py_file = directory / f"{factor_name}.py"
    py_file.write_text(
        textwrap.dedent(f"""\
            from tinohelm.factor.decorator import factor
            from tinohelm.factor.types import Panel

            @factor(category="改版", lookback=10)
            def {factor_name}(close: Panel) -> Panel:
                return close.pct_change(10)  # changed body
        """),
        encoding="utf-8",
    )
    return py_file


# ---------------------------------------------------------------------------
# Basic scan: discover user factor
# ---------------------------------------------------------------------------

class TestRegistryScanBasic:
    def test_get_spec_returns_factor_spec(self, tmp_path: Path) -> None:
        _write_factor_file(tmp_path, "my_factor")
        reg = Registry(user_dir=tmp_path)
        reg.scan()

        spec = reg.get_spec("my_factor")

        assert spec is not None
        assert isinstance(spec, FactorSpec)
        assert spec.name == "my_factor"

    def test_get_spec_unknown_returns_none(self, tmp_path: Path) -> None:
        reg = Registry(user_dir=tmp_path)
        reg.scan()

        assert reg.get_spec("nonexistent_factor") is None

    def test_scan_returns_dict_with_spec(self, tmp_path: Path) -> None:
        _write_factor_file(tmp_path, "momentum")
        reg = Registry(user_dir=tmp_path)
        result = reg.scan()

        assert "momentum" in result
        assert isinstance(result["momentum"], FactorSpec)

    def test_category_stored_correctly(self, tmp_path: Path) -> None:
        _write_factor_file(tmp_path, "factor_a", category="动量")
        reg = Registry(user_dir=tmp_path)
        reg.scan()

        spec = reg.get_spec("factor_a")
        assert spec is not None
        assert spec.category == "动量"

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        # Disable built-in scanning so we only exercise user-dir scanning.
        # Prior to s12, the builtins package did not exist (ImportError swallowed);
        # after s12 the package exists, so we pass a non-existent package name to
        # keep this test focused on user-dir-only behaviour.
        reg = Registry(user_dir=tmp_path, builtins_package="tinohelm.factor.builtins.__nonexistent__")
        result = reg.scan()
        assert result == {}

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        # Same rationale as test_empty_dir_returns_empty (builtins isolation).
        reg = Registry(user_dir=missing, builtins_package="tinohelm.factor.builtins.__nonexistent__")
        result = reg.scan()
        assert result == {}


# ---------------------------------------------------------------------------
# Incremental rescan: code_hash change detection
# ---------------------------------------------------------------------------

class TestRegistryRescan:
    def test_rescan_detects_hash_change(self, tmp_path: Path) -> None:
        _write_factor_file(tmp_path, "evolving_factor")
        reg = Registry(user_dir=tmp_path)
        reg.scan()

        spec_v1 = reg.get_spec("evolving_factor")
        assert spec_v1 is not None
        assert spec_v1.category == "测试"

        # Overwrite with different content
        _write_factor_file_v2(tmp_path, "evolving_factor")
        reg.scan()

        spec_v2 = reg.get_spec("evolving_factor")
        assert spec_v2 is not None
        assert spec_v2.category == "改版"

    def test_rescan_updated_spec_different_lookback(self, tmp_path: Path) -> None:
        _write_factor_file(tmp_path, "dynamic_factor")  # lookback=5
        reg = Registry(user_dir=tmp_path)
        reg.scan()
        spec_v1 = reg.get_spec("dynamic_factor")
        assert spec_v1 is not None and spec_v1.lookback == 5

        _write_factor_file_v2(tmp_path, "dynamic_factor")  # lookback=10
        reg.scan()
        spec_v2 = reg.get_spec("dynamic_factor")
        assert spec_v2 is not None and spec_v2.lookback == 10

    def test_unchanged_file_uses_cache(self, tmp_path: Path) -> None:
        """If file content is unchanged, cached spec is reused (no reload)."""
        _write_factor_file(tmp_path, "stable_factor")
        reg = Registry(user_dir=tmp_path)
        reg.scan()

        spec_after_first = reg.get_spec("stable_factor")

        # Second scan without file modification
        reg.scan()
        spec_after_second = reg.get_spec("stable_factor")

        # Same object (reused from cache)
        assert spec_after_first is spec_after_second


# ---------------------------------------------------------------------------
# get_kernel: callable and returns Panel
# ---------------------------------------------------------------------------

class TestRegistryGetKernel:
    def test_get_kernel_is_callable(self, tmp_path: Path) -> None:
        _write_factor_file(tmp_path, "kernel_factor")
        reg = Registry(user_dir=tmp_path)
        reg.scan()

        kernel = reg.get_kernel("kernel_factor")
        assert callable(kernel)

    def test_get_kernel_returns_panel(self, tmp_path: Path) -> None:
        _write_factor_file(tmp_path, "returns_panel")
        reg = Registry(user_dir=tmp_path)
        reg.scan()

        kernel = reg.get_kernel("returns_panel")
        panel: Panel = pd.DataFrame(
            {"BTC": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]}
        )
        result = kernel(panel)
        assert isinstance(result, pd.DataFrame)

    def test_get_kernel_unknown_raises_key_error(self, tmp_path: Path) -> None:
        reg = Registry(user_dir=tmp_path)
        reg.scan()

        with pytest.raises(KeyError, match="not found in registry"):
            reg.get_kernel("ghost_factor")


# ---------------------------------------------------------------------------
# get_all_specs: includes all registered factors
# ---------------------------------------------------------------------------

class TestRegistryGetAllSpecs:
    def test_get_all_specs_includes_user_factors(self, tmp_path: Path) -> None:
        _write_factor_file(tmp_path, "alpha")
        _write_factor_file(tmp_path, "beta")
        reg = Registry(user_dir=tmp_path)
        reg.scan()

        all_specs = reg.get_all_specs()
        names = {s.name for s in all_specs}
        assert "alpha" in names
        assert "beta" in names

    def test_get_all_specs_returns_list_of_factor_spec(self, tmp_path: Path) -> None:
        _write_factor_file(tmp_path, "gamma")
        reg = Registry(user_dir=tmp_path)
        reg.scan()

        all_specs = reg.get_all_specs()
        assert isinstance(all_specs, list)
        assert all(isinstance(s, FactorSpec) for s in all_specs)

    def test_get_all_specs_empty_when_no_factors(self, tmp_path: Path) -> None:
        # Disable built-in scanning so we only exercise user-dir scanning.
        reg = Registry(user_dir=tmp_path, builtins_package="tinohelm.factor.builtins.__nonexistent__")
        reg.scan()
        assert reg.get_all_specs() == []


# ---------------------------------------------------------------------------
# User factor overrides built-in of the same name
# ---------------------------------------------------------------------------

class TestUserOverridesBuiltin:
    def test_user_factor_wins_over_builtin(self, tmp_path: Path) -> None:
        """If a user factor shares a name with a built-in, user wins."""
        # Write a user factor named "shared_factor" with category "用户"
        py_file = tmp_path / "shared_factor.py"
        py_file.write_text(
            textwrap.dedent("""\
                from tinohelm.factor.decorator import factor
                from tinohelm.factor.types import Panel

                @factor(category="用户", lookback=1)
                def shared_factor(close: Panel) -> Panel:
                    return close
            """),
            encoding="utf-8",
        )

        # Mock a built-in module that also exposes shared_factor with category "内置"
        mock_builtin_func = MagicMock()
        builtin_spec = FactorSpec(
            name="shared_factor",
            category="内置",
            lookback=1,
        )
        mock_builtin_func.__factor_spec__ = builtin_spec
        mock_builtin_func.__name__ = "shared_factor"
        # Make it callable (MagicMock already is)

        mock_pkg = MagicMock()
        mock_pkg.__name__ = "tinohelm.factor.builtins"
        mock_pkg.__path__ = []

        with patch("tinohelm.factor.registry.importlib.import_module", return_value=mock_pkg), \
             patch("tinohelm.factor.registry.pkgutil.iter_modules", return_value=[]), \
             patch("tinohelm.factor.registry.inspect.getmembers") as mock_members:

            # First call (for builtin pkg): expose the builtin func
            # Second+ calls (for user modules): normal inspect behaviour
            call_count = 0
            real_getmembers = inspect.getmembers

            def patched_getmembers(module, predicate=None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # This is the builtin package scan
                    return [("shared_factor", mock_builtin_func)]
                return real_getmembers(module, predicate)

            mock_members.side_effect = patched_getmembers

            reg = Registry(user_dir=tmp_path, builtins_package="tinohelm.factor.builtins")
            reg.scan()

        # User factor must win — category should be "用户"
        spec = reg.get_spec("shared_factor")
        assert spec is not None
        assert spec.category == "用户", f"Expected '用户' but got {spec.category!r}"


# ---------------------------------------------------------------------------
# Builtins package missing: graceful ImportError handling
# ---------------------------------------------------------------------------

class TestBuiltinsMissing:
    def test_missing_builtins_package_no_error(self, tmp_path: Path) -> None:
        """Registry must not raise when builtins package does not exist."""
        _write_factor_file(tmp_path, "user_only_factor")
        reg = Registry(
            user_dir=tmp_path,
            builtins_package="tinohelm.factor.builtins_nonexistent_xyz",
        )
        # Should not raise
        result = reg.scan()
        assert "user_only_factor" in result

    def test_missing_builtins_user_factors_still_loaded(self, tmp_path: Path) -> None:
        _write_factor_file(tmp_path, "sole_factor")
        reg = Registry(
            user_dir=tmp_path,
            builtins_package="totally.fake.package.xyz",
        )
        reg.scan()
        spec = reg.get_spec("sole_factor")
        assert spec is not None
        assert spec.name == "sole_factor"


# ---------------------------------------------------------------------------
# Multiple factors in a single file
# ---------------------------------------------------------------------------

class TestMultipleFactorsInFile:
    def test_multiple_factors_from_one_file(self, tmp_path: Path) -> None:
        py_file = tmp_path / "multi.py"
        py_file.write_text(
            textwrap.dedent("""\
                from tinohelm.factor.decorator import factor
                from tinohelm.factor.types import Panel

                @factor(category="动量", lookback=5)
                def ret5(close: Panel) -> Panel:
                    return close.pct_change(5)

                @factor(category="波动", lookback=20)
                def vol20(close: Panel) -> Panel:
                    return close.rolling(20).std()
            """),
            encoding="utf-8",
        )
        reg = Registry(user_dir=tmp_path)
        reg.scan()

        assert reg.get_spec("ret5") is not None
        assert reg.get_spec("vol20") is not None
        names = {s.name for s in reg.get_all_specs()}
        assert {"ret5", "vol20"}.issubset(names)

    def test_non_factor_functions_ignored(self, tmp_path: Path) -> None:
        py_file = tmp_path / "mixed.py"
        py_file.write_text(
            textwrap.dedent("""\
                from tinohelm.factor.decorator import factor
                from tinohelm.factor.types import Panel

                def helper(x):
                    return x * 2

                @factor(category="动量", lookback=5)
                def real_factor(close: Panel) -> Panel:
                    return close.pct_change(5)
            """),
            encoding="utf-8",
        )
        reg = Registry(user_dir=tmp_path)
        reg.scan()

        assert reg.get_spec("real_factor") is not None
        assert reg.get_spec("helper") is None
