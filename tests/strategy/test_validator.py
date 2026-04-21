"""Tests for ``tinohelm.strategy.validator`` + ``validator_helpers``.

``validate_strategy`` is the code path behind the frontend "Validate" button
on the Strategies page and the ``POST /api/strategies/{name}/validate``
endpoint. A regression here produces misleading UI feedback — a strategy
incorrectly labelled "invalid" blocks the user entirely, and a strategy
incorrectly labelled "valid" leads to a harder-to-debug backtest failure
later.

The helpers file is pure Python and fully exercised via fake classes. The
facade ``validate_strategy`` is exercised end-to-end by writing a synthetic
``.py`` file to disk and round-tripping through ``load_module_from_file``.
The fake strategies in these tests **do not import nautilus_trader**; they
declare dummy base classes whose ``__module__`` happens to start with
``nautilus_trader`` so that the NT-name-based discovery logic triggers.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tinohelm.strategy.validator import validate_strategy
from tinohelm.strategy.validator_helpers import (
    RECOMMENDED_HOOKS,
    STRATEGY_HOOK_NAMES,
    build_missing_hook_warnings,
    collect_implemented_hooks,
    empty_validation_result,
    extract_config_params,
)


# ---------------------------------------------------------------------------
# STRATEGY_HOOK_NAMES + RECOMMENDED_HOOKS constants
# ---------------------------------------------------------------------------


class TestHookConstants:

    def test_strategy_hook_names_tuple(self):
        assert isinstance(STRATEGY_HOOK_NAMES, tuple)
        # Order matters — it determines result["hooks"] ordering.
        assert STRATEGY_HOOK_NAMES[0] == "on_start"
        assert STRATEGY_HOOK_NAMES[1] == "on_stop"

    def test_strategy_hook_names_are_unique(self):
        assert len(set(STRATEGY_HOOK_NAMES)) == len(STRATEGY_HOOK_NAMES)

    def test_recommended_is_subset(self):
        assert set(RECOMMENDED_HOOKS).issubset(set(STRATEGY_HOOK_NAMES))

    def test_recommended_contents_pinned(self):
        # If someone proposes adding e.g. "on_bar" to the recommended set,
        # this test forces them to also update the test — making the policy
        # change explicit in the diff.
        assert RECOMMENDED_HOOKS == ("on_start", "on_stop")

    def test_hook_count_pinned(self):
        # Prevents accidental additions that would silently start claiming
        # a user's strategy lacks a "new" hook.
        assert len(STRATEGY_HOOK_NAMES) == 10


# ---------------------------------------------------------------------------
# empty_validation_result
# ---------------------------------------------------------------------------


class TestEmptyValidationResult:

    def test_default_shape(self):
        r = empty_validation_result("foo")
        assert r == {
            "valid": False,
            "name": "foo",
            "errors": [],
            "warnings": [],
            "strategy_class": None,
            "config_class": None,
            "config_params": [],
            "hooks": [],
        }

    def test_name_is_threaded_through(self):
        assert empty_validation_result("bar")["name"] == "bar"
        assert empty_validation_result("")["name"] == ""

    def test_lists_are_fresh_per_call(self):
        a = empty_validation_result("x")
        b = empty_validation_result("x")
        a["errors"].append("e1")
        a["hooks"].append("h1")
        # Second call must not share the mutable buffers.
        assert b["errors"] == []
        assert b["hooks"] == []

    def test_valid_flag_starts_false(self):
        # Critical: default False so any early-return path produces a safe
        # "invalid" verdict without having to explicitly set it.
        assert empty_validation_result("x")["valid"] is False


# ---------------------------------------------------------------------------
# collect_implemented_hooks
# ---------------------------------------------------------------------------


class _DummyBase:
    """Parent with stub hooks that should **not** count as 'implemented'."""

    def on_start(self) -> None: ...
    def on_stop(self) -> None: ...


class _DummyImplStartOnly(_DummyBase):
    def on_start(self) -> None:
        self.x = 1


class _DummyImplAll(_DummyBase):
    def on_start(self) -> None: self.x = 1
    def on_stop(self) -> None: self.x = 0
    def on_bar(self, bar: Any) -> None: ...
    def on_event(self, evt: Any) -> None: ...


class _DummyImplNone(_DummyBase):
    pass


class TestCollectImplementedHooks:

    def test_inherited_hooks_do_not_count(self):
        # _DummyImplNone inherits on_start/on_stop but declares neither.
        # collect_implemented_hooks uses __dict__ so should return [].
        assert collect_implemented_hooks(_DummyImplNone) == []

    def test_direct_declaration_counts(self):
        hooks = collect_implemented_hooks(_DummyImplStartOnly)
        assert hooks == ["on_start"]

    def test_multiple_hooks_reported_in_canonical_order(self):
        hooks = collect_implemented_hooks(_DummyImplAll)
        # Order follows STRATEGY_HOOK_NAMES, NOT the order defined in the class.
        assert hooks == ["on_start", "on_stop", "on_bar", "on_event"]

    def test_accepts_custom_hook_names_tuple(self):
        # Helper is generic — callers can pass their own whitelist.
        hooks = collect_implemented_hooks(_DummyImplAll, ("on_bar",))
        assert hooks == ["on_bar"]

    def test_empty_hook_names_returns_empty(self):
        assert collect_implemented_hooks(_DummyImplAll, ()) == []

    def test_unknown_hook_names_return_empty(self):
        hooks = collect_implemented_hooks(
            _DummyImplAll, ("on_nonexistent", "on_also_missing")
        )
        assert hooks == []


# ---------------------------------------------------------------------------
# build_missing_hook_warnings
# ---------------------------------------------------------------------------


class TestBuildMissingHookWarnings:

    def test_no_hooks_implemented(self):
        warnings = build_missing_hook_warnings([])
        assert warnings == [
            "on_start not implemented (recommended)",
            "on_stop not implemented (recommended)",
        ]

    def test_all_recommended_implemented(self):
        assert build_missing_hook_warnings(["on_start", "on_stop"]) == []

    def test_only_start_implemented(self):
        assert build_missing_hook_warnings(["on_start"]) == [
            "on_stop not implemented (recommended)"
        ]

    def test_only_stop_implemented(self):
        assert build_missing_hook_warnings(["on_stop"]) == [
            "on_start not implemented (recommended)"
        ]

    def test_extra_hooks_do_not_produce_warnings(self):
        assert build_missing_hook_warnings(
            ["on_start", "on_stop", "on_bar", "on_event"]
        ) == []

    def test_warnings_in_recommended_order(self):
        # Order is locked to RECOMMENDED_HOOKS iteration order.
        assert build_missing_hook_warnings([])[0].startswith("on_start")
        assert build_missing_hook_warnings([])[1].startswith("on_stop")


# ---------------------------------------------------------------------------
# extract_config_params
# ---------------------------------------------------------------------------


class _FakePydanticField:
    def __init__(self, annotation, default=None, required=True):
        self.annotation = annotation
        self.default = default
        self._required = required

    def is_required(self) -> bool:
        return self._required


class _FakePydanticConfig:
    model_fields = {
        "symbol": _FakePydanticField(str, default="BTCUSDT-PERP", required=False),
        "size": _FakePydanticField(int, default=None, required=True),
    }


class _FakeMsgspecConfig:
    __struct_fields__ = ("a", "b")
    __struct_defaults__ = (42,)  # only `b` has a default
    __annotations__ = {"a": int, "b": int}


class _BrokenConfig:
    """Has neither ``model_fields`` nor ``__struct_fields__``."""


class TestExtractConfigParams:

    def test_pydantic_path(self):
        params = extract_config_params(_FakePydanticConfig)
        assert len(params) == 2
        names = [p["name"] for p in params]
        assert names == ["symbol", "size"]

    def test_msgspec_path(self):
        params = extract_config_params(_FakeMsgspecConfig)
        names = [p["name"] for p in params]
        assert names == ["a", "b"]
        # `a` is required (no default), `b` has default=42.
        required = {p["name"]: p["required"] for p in params}
        assert required == {"a": True, "b": False}

    def test_unknown_shape_returns_empty(self):
        assert extract_config_params(_BrokenConfig) == []

    def test_introspection_error_is_swallowed(self, monkeypatch):
        # If the underlying get_config_fields raises — for any reason, not
        # just a bad msgspec shape — the helper must still return [] rather
        # than propagate. We simulate by patching get_config_fields itself.
        import tinohelm.strategy.utils as u

        def _boom(_cls):
            raise RuntimeError("synthetic introspection failure")

        monkeypatch.setattr(u, "get_config_fields", _boom)

        class _Anything:
            pass

        assert extract_config_params(_Anything) == []


# ---------------------------------------------------------------------------
# validate_strategy: end-to-end (NT-free, via synthetic modules)
# ---------------------------------------------------------------------------


_FAKE_NT_STRATEGY = '''
"""Fake NT strategy — dummy Strategy / StrategyConfig base classes.

``__module__.startswith("nautilus_trader")`` is what validator_helpers uses
to recognise NT bases, so we explicitly force ``__module__`` on the dummies.
"""
from __future__ import annotations


class Strategy:
    """Dummy stand-in whose ``__name__`` and ``__module__`` trip the
    NT-name-based discovery. Forced ``__module__`` reassignment pretends
    this base comes from the real NT package without importing it.
    """
    pass
Strategy.__module__ = "nautilus_trader.trading.strategy"


class StrategyConfig:
    pass
StrategyConfig.__module__ = "nautilus_trader.config"


class MyStratConfig(StrategyConfig):
    """msgspec-shaped fake config."""
    __struct_fields__ = ("symbol", "size")
    __struct_defaults__ = (0.01,)
    __annotations__ = {"symbol": str, "size": float}


class MyStrat(Strategy):
    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass

    def on_bar(self, bar) -> None:
        pass
'''


_STRATEGY_NO_HOOKS = '''
from __future__ import annotations


class Strategy:
    """Dummy stand-in whose ``__name__`` and ``__module__`` trip the
    NT-name-based discovery. Forced ``__module__`` reassignment pretends
    this base comes from the real NT package without importing it.
    """
    pass
Strategy.__module__ = "nautilus_trader.trading.strategy"


class StrategyConfig:
    pass
StrategyConfig.__module__ = "nautilus_trader.config"


class NoHooksConfig(StrategyConfig):
    __struct_fields__ = ()
    __struct_defaults__ = ()


class NoHooks(Strategy):
    pass
'''


_STRATEGY_ONLY_START = '''
from __future__ import annotations


class Strategy:
    """Dummy stand-in whose ``__name__`` and ``__module__`` trip the
    NT-name-based discovery. Forced ``__module__`` reassignment pretends
    this base comes from the real NT package without importing it.
    """
    pass
Strategy.__module__ = "nautilus_trader.trading.strategy"


class StrategyConfig:
    pass
StrategyConfig.__module__ = "nautilus_trader.config"


class PartialConfig(StrategyConfig):
    __struct_fields__ = ()
    __struct_defaults__ = ()


class Partial(Strategy):
    def on_start(self) -> None:
        pass
'''


_STRATEGY_MISSING_CONFIG = '''
from __future__ import annotations


class Strategy:
    pass
Strategy.__module__ = "nautilus_trader.trading.strategy"


class NoConfig(Strategy):
    def on_start(self) -> None:
        pass
'''


_NOT_A_STRATEGY = '''
class SomeClass:
    pass
'''


_SYNTAX_ERROR = '''
def broken(
'''


def _write(dir_: Path, name: str, body: str) -> None:
    (dir_ / f"{name}.py").write_text(body.lstrip())


class TestValidateStrategyMissingFile:

    def test_returns_error_when_file_missing(self, tmp_path: Path):
        r = validate_strategy("does_not_exist", tmp_path)
        assert r["valid"] is False
        assert r["errors"] == [f"File not found: {tmp_path / 'does_not_exist.py'}"]
        assert r["name"] == "does_not_exist"

    def test_empty_strategies_dir(self, tmp_path: Path):
        r = validate_strategy("anything", tmp_path)
        assert r["valid"] is False
        assert "File not found" in r["errors"][0]


class TestValidateStrategyImportFailure:

    def test_syntax_error_recorded(self, tmp_path: Path):
        _write(tmp_path, "broken", _SYNTAX_ERROR)
        r = validate_strategy("broken", tmp_path)
        assert r["valid"] is False
        assert any(e.startswith("Import failed:") for e in r["errors"])

    def test_import_of_missing_dependency(self, tmp_path: Path):
        (tmp_path / "needs.py").write_text(
            "import totally_not_a_real_module_xyz\n"
        )
        r = validate_strategy("needs", tmp_path)
        assert r["valid"] is False
        assert any("Import failed" in e for e in r["errors"])


class TestValidateStrategyMissingClasses:

    def test_no_nt_classes_at_all(self, tmp_path: Path):
        _write(tmp_path, "plain", _NOT_A_STRATEGY)
        r = validate_strategy("plain", tmp_path)
        assert r["valid"] is False
        assert "No Strategy subclass found" in r["errors"]
        assert "No StrategyConfig subclass found" in r["errors"]
        # Hooks / config_params left at the default empty shape.
        assert r["hooks"] == []
        assert r["config_params"] == []

    def test_strategy_without_config(self, tmp_path: Path):
        _write(tmp_path, "no_cfg", _STRATEGY_MISSING_CONFIG)
        r = validate_strategy("no_cfg", tmp_path)
        assert r["valid"] is False
        assert "No StrategyConfig subclass found" in r["errors"]
        assert "No Strategy subclass found" not in r["errors"]
        # strategy_class is reported even though validation failed overall.
        assert r["strategy_class"] == "NoConfig"


class TestValidateStrategyValid:

    def test_complete_strategy_validates(self, tmp_path: Path):
        _write(tmp_path, "my_strat", _FAKE_NT_STRATEGY)
        r = validate_strategy("my_strat", tmp_path)
        assert r["valid"] is True
        assert r["errors"] == []
        assert r["warnings"] == []
        assert r["strategy_class"] == "MyStrat"
        assert r["config_class"] == "MyStratConfig"
        assert r["hooks"] == ["on_start", "on_stop", "on_bar"]

    def test_config_params_extracted(self, tmp_path: Path):
        _write(tmp_path, "my_strat", _FAKE_NT_STRATEGY)
        r = validate_strategy("my_strat", tmp_path)
        names = [p["name"] for p in r["config_params"]]
        assert names == ["symbol", "size"]

    def test_missing_hooks_produce_warnings(self, tmp_path: Path):
        _write(tmp_path, "bare", _STRATEGY_NO_HOOKS)
        r = validate_strategy("bare", tmp_path)
        # Valid because errors are empty — hooks are only "recommended".
        assert r["valid"] is True
        assert r["errors"] == []
        assert r["warnings"] == [
            "on_start not implemented (recommended)",
            "on_stop not implemented (recommended)",
        ]
        assert r["hooks"] == []

    def test_partial_hooks_warn_about_missing(self, tmp_path: Path):
        _write(tmp_path, "partial", _STRATEGY_ONLY_START)
        r = validate_strategy("partial", tmp_path)
        assert r["valid"] is True
        assert r["hooks"] == ["on_start"]
        assert r["warnings"] == ["on_stop not implemented (recommended)"]

    def test_name_key_matches_input(self, tmp_path: Path):
        _write(tmp_path, "my_strat", _FAKE_NT_STRATEGY)
        r = validate_strategy("my_strat", tmp_path)
        assert r["name"] == "my_strat"


class TestValidateStrategyReturnsCanonicalShape:

    @pytest.mark.parametrize("body,name", [
        (_SYNTAX_ERROR, "broken"),
        (_NOT_A_STRATEGY, "plain"),
        (_STRATEGY_MISSING_CONFIG, "no_cfg"),
        (_FAKE_NT_STRATEGY, "my_strat"),
    ])
    def test_result_has_all_keys(self, tmp_path: Path, body: str, name: str):
        _write(tmp_path, name, body)
        r = validate_strategy(name, tmp_path)
        required_keys = {
            "valid", "name", "errors", "warnings",
            "strategy_class", "config_class", "config_params", "hooks",
        }
        assert set(r.keys()) == required_keys

    def test_accepts_str_path(self, tmp_path: Path):
        _write(tmp_path, "my_strat", _FAKE_NT_STRATEGY)
        r = validate_strategy("my_strat", str(tmp_path))
        assert r["valid"] is True
