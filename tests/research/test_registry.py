"""Tests for `tinohelm.research.registry` — factor discovery (built-in + custom)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from tinohelm.research import registry as REG


# ──────────────────────────────────────────────────────────────────────
# Custom-factor file helpers
# ──────────────────────────────────────────────────────────────────────


_VALID_CUSTOM_FACTOR = """
import pandas as pd

FACTOR_META = {
    "name": "my_custom_factor",
    "label": "我的自定义因子",
    "category": "自定义",
    "data_type": "bar",
    "params": {"lookback": {"default": 10, "min": 1, "max": 100, "label": "回看"}},
}

def compute(df, params):
    return df["close"].pct_change(params.get("lookback", 10))
"""

_MISSING_META = """
def compute(df, params):
    return df["close"]
"""

_MISSING_COMPUTE = """
FACTOR_META = {"name": "no_compute", "label": "x", "category": "x", "data_type": "bar", "params": {}}
"""

_BROKEN_AT_IMPORT = """
raise RuntimeError("intentional load-time failure")
FACTOR_META = {"name": "broken", "label": "x", "category": "x", "data_type": "bar", "params": {}}
def compute(df, params): return df
"""


def _patch_custom_dir(monkeypatch, path: Path) -> None:
    """Redirect _custom_factors_dir() to a tmp_path location."""
    monkeypatch.setattr(REG, "_custom_factors_dir", lambda: path)


# ──────────────────────────────────────────────────────────────────────
# get_all_factors
# ──────────────────────────────────────────────────────────────────────


class TestGetAllFactors:
    def test_includes_all_builtins_with_source_marker(self, tmp_path, monkeypatch):
        _patch_custom_dir(monkeypatch, tmp_path / "nonexistent")  # No custom dir
        result = REG.get_all_factors()
        # All 14 built-ins present with source="builtin"
        from tinohelm.research.factors import BUILTIN_FACTORS
        for name in BUILTIN_FACTORS:
            assert name in result
            assert result[name]["source"] == "builtin"

    def test_skips_when_custom_dir_does_not_exist(self, tmp_path, monkeypatch):
        _patch_custom_dir(monkeypatch, tmp_path / "definitely_missing")
        result = REG.get_all_factors()
        # Only built-ins
        assert all(meta["source"] == "builtin" for meta in result.values())

    def test_loads_valid_custom_factor(self, tmp_path, monkeypatch):
        _patch_custom_dir(monkeypatch, tmp_path)
        (tmp_path / "my_custom_factor.py").write_text(_VALID_CUSTOM_FACTOR)
        result = REG.get_all_factors()
        assert "my_custom_factor" in result
        assert result["my_custom_factor"]["source"] == "custom"
        assert result["my_custom_factor"]["label"] == "我的自定义因子"

    def test_skips_files_starting_with_underscore(self, tmp_path, monkeypatch):
        # _template.py-style scaffolding files must not be auto-loaded
        _patch_custom_dir(monkeypatch, tmp_path)
        (tmp_path / "_template.py").write_text(_VALID_CUSTOM_FACTOR.replace(
            "my_custom_factor", "_template_factor"
        ))
        result = REG.get_all_factors()
        assert "_template_factor" not in result

    def test_skips_factor_missing_meta(self, tmp_path, monkeypatch, caplog):
        _patch_custom_dir(monkeypatch, tmp_path)
        (tmp_path / "no_meta.py").write_text(_MISSING_META)
        result = REG.get_all_factors()
        # File processed but produces no entry
        assert "no_meta" not in result
        # Built-ins still load
        assert "ret_N" in result

    def test_skips_factor_missing_compute(self, tmp_path, monkeypatch):
        _patch_custom_dir(monkeypatch, tmp_path)
        (tmp_path / "no_compute.py").write_text(_MISSING_COMPUTE)
        result = REG.get_all_factors()
        assert "no_compute" not in result
        assert "ret_N" in result

    def test_swallows_broken_factor_load_errors(self, tmp_path, monkeypatch, caplog):
        # Factor that raises at import time must not break discovery for the rest.
        _patch_custom_dir(monkeypatch, tmp_path)
        (tmp_path / "broken.py").write_text(_BROKEN_AT_IMPORT)
        (tmp_path / "my_custom_factor.py").write_text(_VALID_CUSTOM_FACTOR)
        result = REG.get_all_factors()
        # Broken file dropped, valid one loaded
        assert "broken" not in result
        assert "my_custom_factor" in result

    def test_custom_factor_can_override_builtin_name(self, tmp_path, monkeypatch):
        # If a custom factor uses a built-in name, the custom one wins (later in dict ordering)
        _patch_custom_dir(monkeypatch, tmp_path)
        override_src = _VALID_CUSTOM_FACTOR.replace("my_custom_factor", "ret_N")
        (tmp_path / "ret_N.py").write_text(override_src)
        result = REG.get_all_factors()
        # The custom override wins because it's added after the built-ins
        assert result["ret_N"]["source"] == "custom"


# ──────────────────────────────────────────────────────────────────────
# get_compute_fn
# ──────────────────────────────────────────────────────────────────────


class TestGetComputeFn:
    def test_returns_builtin_compute_fn(self, tmp_path, monkeypatch):
        _patch_custom_dir(monkeypatch, tmp_path / "missing")
        from tinohelm.research.factors import _COMPUTE_MAP
        fn = REG.get_compute_fn("ret_N")
        assert fn is _COMPUTE_MAP["ret_N"]

    def test_returns_custom_compute_fn(self, tmp_path, monkeypatch):
        _patch_custom_dir(monkeypatch, tmp_path)
        (tmp_path / "my_custom_factor.py").write_text(_VALID_CUSTOM_FACTOR)
        fn = REG.get_compute_fn("my_custom_factor")
        # Verify it actually computes
        df = pd.DataFrame({"close": [100.0, 101.0, 102.0, 103.0, 104.0,
                                     105.0, 106.0, 107.0, 108.0, 109.0, 110.0]})
        out = fn(df, {"lookback": 5})
        assert isinstance(out, pd.Series)

    def test_raises_for_unknown_factor(self, tmp_path, monkeypatch):
        _patch_custom_dir(monkeypatch, tmp_path)
        with pytest.raises(ValueError, match="Unknown factor: __nope__"):
            REG.get_compute_fn("__nope__")

    def test_skips_files_starting_with_underscore(self, tmp_path, monkeypatch):
        # The scaffold file should not match — a factor named "_template" shouldn't be reachable.
        _patch_custom_dir(monkeypatch, tmp_path)
        (tmp_path / "_template.py").write_text(_VALID_CUSTOM_FACTOR.replace(
            "my_custom_factor", "_template"
        ))
        with pytest.raises(ValueError, match="Unknown factor: _template"):
            REG.get_compute_fn("_template")

    def test_swallows_broken_files_during_search(self, tmp_path, monkeypatch):
        # Even with a broken file in the dir, looking up a built-in must succeed.
        _patch_custom_dir(monkeypatch, tmp_path)
        (tmp_path / "broken.py").write_text(_BROKEN_AT_IMPORT)
        # built-in lookup short-circuits before scanning custom dir
        fn = REG.get_compute_fn("ret_N")
        assert callable(fn)

    def test_custom_dir_missing_falls_back_to_builtin_only(self, tmp_path, monkeypatch):
        _patch_custom_dir(monkeypatch, tmp_path / "definitely_missing")
        fn = REG.get_compute_fn("ret_N")
        assert callable(fn)
        with pytest.raises(ValueError):
            REG.get_compute_fn("custom_only")


# ──────────────────────────────────────────────────────────────────────
# _load_custom_factor (private helper)
# ──────────────────────────────────────────────────────────────────────


class TestLoadCustomFactor:
    def test_returns_dict_for_valid_factor(self, tmp_path):
        path = tmp_path / "my_custom_factor.py"
        path.write_text(_VALID_CUSTOM_FACTOR)
        out = REG._load_custom_factor(path)
        assert out is not None
        assert out["name"] == "my_custom_factor"
        assert callable(out["compute"])
        assert out["meta"]["label"] == "我的自定义因子"

    def test_returns_none_for_missing_meta(self, tmp_path):
        path = tmp_path / "broken.py"
        path.write_text(_MISSING_META)
        assert REG._load_custom_factor(path) is None

    def test_returns_none_for_missing_compute(self, tmp_path):
        path = tmp_path / "no_compute.py"
        path.write_text(_MISSING_COMPUTE)
        assert REG._load_custom_factor(path) is None

    def test_returns_none_on_load_exception(self, tmp_path):
        path = tmp_path / "broken.py"
        path.write_text(_BROKEN_AT_IMPORT)
        assert REG._load_custom_factor(path) is None

    def test_falls_back_to_stem_when_meta_lacks_name(self, tmp_path):
        # If FACTOR_META omits the "name" key, the file stem is used as the name.
        src = """
FACTOR_META = {"label": "x", "category": "x", "data_type": "bar", "params": {}}
def compute(df, params): return df["close"]
"""
        path = tmp_path / "stem_named.py"
        path.write_text(src)
        out = REG._load_custom_factor(path)
        assert out is not None
        assert out["name"] == "stem_named"
