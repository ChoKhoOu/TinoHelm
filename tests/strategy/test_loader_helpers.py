"""NT-free tests for :mod:`tinohelm.strategy.loader_helpers`.

Every helper in ``loader_helpers`` is deterministic and NT-free, so this
suite can run without ``nautilus_trader`` installed.  The tests here are
organised one class per helper to make failures easy to localise.

They also protect against regressions in three places that share the
same logic: BacktestRunner, Sandbox node, Live node.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from tinohelm.portfolio.config import StrategyBundle
from tinohelm.strategy.loader_helpers import (
    INTERVAL_MAP,
    UNIT_MAP,
    build_strategy_params,
    check_symbol_profiles,
    make_bar_type_str,
    normalize_symbol,
    nt_symbol_to_jesse,
    parse_interval,
    resolve_actor_class_path,
    resolve_module_file,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _bundle(**overrides) -> StrategyBundle:
    """Build a minimal StrategyBundle with overridable fields."""
    defaults = dict(
        strategy_class="m:S",
        config_class="m:SC",
        symbols=["BTCUSDT-PERP"],
        interval="5m",
    )
    defaults.update(overrides)
    return StrategyBundle(**defaults)


# ---------------------------------------------------------------------------
# parse_interval
# ---------------------------------------------------------------------------

class TestParseInterval:
    """``parse_interval`` translates TinoHelm shorthand into NT interval strings."""

    @pytest.mark.parametrize("shorthand,expected", [
        ("1m",  "1-MINUTE"),
        ("5m",  "5-MINUTE"),
        ("15m", "15-MINUTE"),
        ("1h",  "1-HOUR"),
        ("4h",  "4-HOUR"),
        ("1d",  "1-DAY"),
    ])
    def test_known_intervals_hit_the_map(self, shorthand, expected):
        assert parse_interval(shorthand) == expected

    def test_dynamic_parsing_for_unmapped_interval(self):
        """``7h`` isn't in the map but follows the ``<n><unit>`` grammar."""
        assert parse_interval("7h") == "7-HOUR"

    def test_dynamic_parsing_handles_seconds(self):
        assert parse_interval("30s") == "30-SECOND"

    def test_uppercase_input_is_lowercased_before_regex(self):
        assert parse_interval("2H") == "2-HOUR"

    def test_garbage_returns_1_minute_fallback(self):
        assert parse_interval("banana") == "1-MINUTE"

    def test_empty_string_returns_fallback(self):
        assert parse_interval("") == "1-MINUTE"

    def test_unit_map_is_complete(self):
        """Every unit we document is reachable from UNIT_MAP."""
        assert set(UNIT_MAP.keys()) == {"s", "m", "h", "d"}

    def test_interval_map_has_no_unknown_units(self):
        """Values in INTERVAL_MAP always end with one of the declared units."""
        for value in INTERVAL_MAP.values():
            suffix = value.split("-", 1)[1]
            assert suffix in {"SECOND", "MINUTE", "HOUR", "DAY"}


# ---------------------------------------------------------------------------
# normalize_symbol
# ---------------------------------------------------------------------------

class TestNormalizeSymbol:
    """``normalize_symbol`` appends a venue suffix idempotently."""

    def test_appends_binance_suffix(self):
        assert normalize_symbol("BTCUSDT-PERP") == "BTCUSDT-PERP.BINANCE"

    def test_idempotent_when_already_suffixed(self):
        assert normalize_symbol("BTCUSDT-PERP.BINANCE") == "BTCUSDT-PERP.BINANCE"

    def test_does_not_touch_non_perp_symbols(self):
        assert normalize_symbol("ETHUSDT-SWAP") == "ETHUSDT-SWAP.BINANCE"


# ---------------------------------------------------------------------------
# make_bar_type_str
# ---------------------------------------------------------------------------

class TestMakeBarTypeStr:
    """``make_bar_type_str`` builds NT bar-type strings from (symbol, interval)."""

    def test_5m_bar_type(self):
        assert make_bar_type_str("BTCUSDT-PERP", "5m") \
            == "BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL"

    def test_1h_bar_type(self):
        assert make_bar_type_str("ETHUSDT-PERP", "1h") \
            == "ETHUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"

    def test_dynamic_interval_flows_through(self):
        assert make_bar_type_str("SOLUSDT-PERP", "7m") \
            == "SOLUSDT-PERP.BINANCE-7-MINUTE-LAST-EXTERNAL"


# ---------------------------------------------------------------------------
# nt_symbol_to_jesse
# ---------------------------------------------------------------------------

class TestNtSymbolToJesse:
    """``nt_symbol_to_jesse`` maps NT-style symbols to Jesse ``BASE-QUOTE`` keys."""

    def test_strips_venue_and_perp_suffix(self):
        assert nt_symbol_to_jesse("BTCUSDT-PERP.BINANCE") == "BTC-USDT"

    def test_perp_variant(self):
        assert nt_symbol_to_jesse("BTCUSDT-PERP") == "BTC-USDT"

    def test_swap_suffix(self):
        assert nt_symbol_to_jesse("ETHUSDT-SWAP") == "ETH-USDT"

    def test_linear_suffix(self):
        assert nt_symbol_to_jesse("SOLUSDT-LINEAR") == "SOL-USDT"

    def test_no_suffix_no_venue(self):
        """Bare BTCUSDT should still split into BTC-USDT."""
        assert nt_symbol_to_jesse("BTCUSDT") == "BTC-USDT"

    def test_exotic_quote_currency(self):
        assert nt_symbol_to_jesse("BTCUSDC-PERP") == "BTC-USDC"

    def test_unknown_quote_returned_unchanged(self):
        """If the quote token isn't in the known set, return the venue-stripped raw."""
        assert nt_symbol_to_jesse("ABCXYZ-PERP") == "ABCXYZ"


# ---------------------------------------------------------------------------
# resolve_module_file
# ---------------------------------------------------------------------------

class TestResolveModuleFile:
    """``resolve_module_file`` walks a fixed set of search roots."""

    def test_finds_literal_py_file(self, tmp_path):
        target = tmp_path / "my_strat.py"
        target.write_text("x=1\n")
        resolved = resolve_module_file(str(target), source_path=tmp_path)
        assert resolved == target

    def test_finds_in_source_path(self, tmp_path):
        target = tmp_path / "my_strat.py"
        target.write_text("x=1\n")
        resolved = resolve_module_file("my_strat", source_path=tmp_path)
        assert resolved == target

    def test_finds_via_extra_search(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        target = other / "shared.py"
        target.write_text("x=1\n")
        source_path = tmp_path / "src"
        source_path.mkdir()

        resolved = resolve_module_file(
            "shared",
            source_path=source_path,
            extra_search=[other],
        )
        assert resolved == target

    def test_missing_module_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="ghost"):
            resolve_module_file(
                "ghost",
                source_path=tmp_path,
                extra_search=[],  # avoid fallback to /app
            )

    def test_absolute_path_that_exists_returns_as_is(self, tmp_path):
        target = tmp_path / "abs.py"
        target.write_text("x=1\n")
        resolved = resolve_module_file(str(target), source_path=tmp_path / "sub")
        assert resolved == target


# ---------------------------------------------------------------------------
# resolve_actor_class_path
# ---------------------------------------------------------------------------

class TestResolveActorClassPath:
    """``resolve_actor_class_path`` parses and boundary-checks ``module:Class``."""

    def test_relative_path_within_source_path(self, tmp_path):
        (tmp_path / "my_actor.py").write_text("class A: pass\n")
        module_file, class_name = resolve_actor_class_path(
            "./my_actor:A", source_path=tmp_path,
        )
        assert module_file == (tmp_path / "my_actor.py").resolve()
        assert class_name == "A"

    def test_relative_path_escaping_source_path_raises(self, tmp_path):
        other = tmp_path / "outside"
        other.mkdir()
        (other / "evil.py").write_text("class A: pass\n")
        source = tmp_path / "src"
        source.mkdir()

        with pytest.raises(ValueError, match="outside strategy folder"):
            resolve_actor_class_path(
                "./../outside/evil:A", source_path=source,
            )

    def test_relative_path_without_source_path_raises(self):
        with pytest.raises(ValueError, match="requires source_path"):
            resolve_actor_class_path("./whatever:A", source_path=None)

    def test_absolute_path_inside_tino_dir(self, tmp_path):
        tino_dir = tmp_path / "tino_home"
        tino_dir.mkdir()
        (tino_dir / "shared.py").write_text("class A: pass\n")

        module_file, class_name = resolve_actor_class_path(
            f"{tino_dir / 'shared'}:A",
            source_path=None,
            home_tino_dir=tino_dir,
        )
        assert module_file == (tino_dir / "shared.py").resolve()
        assert class_name == "A"

    def test_absolute_path_outside_allowed_dirs_raises(self, tmp_path):
        tino_dir = tmp_path / "tino_home"
        tino_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "danger.py").write_text("class A: pass\n")

        with pytest.raises(ValueError, match="outside allowed directories"):
            resolve_actor_class_path(
                f"{outside / 'danger'}:A",
                source_path=None,
                home_tino_dir=tino_dir,
            )

    def test_missing_module_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            resolve_actor_class_path("./ghost:A", source_path=tmp_path)

    def test_malformed_class_path_without_colon_raises(self, tmp_path):
        with pytest.raises(ValueError, match="module:ClassName"):
            resolve_actor_class_path("no_colon_here", source_path=tmp_path)

    def test_malformed_class_path_missing_class_name_raises(self, tmp_path):
        with pytest.raises(ValueError, match="missing class name"):
            resolve_actor_class_path("./my_actor:", source_path=tmp_path)


# ---------------------------------------------------------------------------
# build_strategy_params
# ---------------------------------------------------------------------------

class TestBuildStrategyParams:
    """``build_strategy_params`` resolves tag/manage_stop/bar_type and filters fields."""

    def test_injects_symbols_and_interval_when_config_accepts(self):
        bundle = _bundle(params={"lookback": 100})
        fields = {"symbols", "interval", "lookback"}
        result = build_strategy_params(bundle, fields)

        assert result["symbols"] == ["BTCUSDT-PERP"]
        assert result["interval"] == "5m"
        assert result["lookback"] == 100
        # Fields not in the config must be stripped
        assert "order_id_tag" not in result
        assert "manage_stop" not in result

    def test_injects_instrument_id_when_config_accepts(self):
        bundle = _bundle(symbols=["ETHUSDT-PERP"])
        fields = {"instrument_id", "symbols", "interval"}
        result = build_strategy_params(bundle, fields)
        assert result["instrument_id"] == "ETHUSDT-PERP.BINANCE"

    def test_skips_instrument_id_when_config_rejects(self):
        bundle = _bundle(symbols=["ETHUSDT-PERP"])
        fields = {"symbols", "interval"}
        result = build_strategy_params(bundle, fields)
        assert "instrument_id" not in result

    def test_injects_bar_type_derived_from_symbol_interval(self):
        bundle = _bundle(symbols=["SOLUSDT-PERP"], interval="15m")
        fields = {"bar_type", "symbols", "interval"}
        result = build_strategy_params(bundle, fields)
        assert result["bar_type"] == "SOLUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL"

    def test_bar_type_uses_resolved_bar_types_when_available(self):
        """Runner-supplied composite bar types must override the derived default."""
        bundle = _bundle(
            resolved_bar_types=["BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"],
        )
        fields = {"bar_type", "symbols", "interval"}
        result = build_strategy_params(bundle, fields)
        assert result["bar_type"] \
            == "BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"

    def test_resolved_bar_types_always_in_params_when_non_empty(self):
        bundle = _bundle(resolved_bar_types=["BAR1", "BAR2"])
        fields = {"symbols", "interval", "resolved_bar_types"}
        result = build_strategy_params(bundle, fields)
        assert result["resolved_bar_types"] == ["BAR1", "BAR2"]

    def test_order_id_tag_explicit_wins(self):
        bundle = _bundle(tag="99")
        fields = {"order_id_tag"}
        result = build_strategy_params(bundle, fields, order_id_tag="42")
        assert result["order_id_tag"] == "42"

    def test_order_id_tag_from_plural_list(self):
        bundle = _bundle()
        fields = {"order_id_tag"}
        result = build_strategy_params(bundle, fields, order_id_tags=["7a", "7b"])
        assert result["order_id_tag"] == "7a"

    def test_order_id_tag_falls_back_to_bundle_tag(self):
        bundle = _bundle(tag="77")
        fields = {"order_id_tag"}
        result = build_strategy_params(bundle, fields)
        assert result["order_id_tag"] == "77"

    def test_order_id_tag_default_000(self):
        bundle = _bundle()
        fields = {"order_id_tag"}
        result = build_strategy_params(bundle, fields)
        assert result["order_id_tag"] == "000"

    def test_existing_order_id_tag_in_params_is_preserved(self):
        """When no explicit tag is given, don't overwrite a user-supplied value."""
        bundle = _bundle(params={"order_id_tag": "ZZ"})
        fields = {"order_id_tag"}
        result = build_strategy_params(bundle, fields)
        assert result["order_id_tag"] == "ZZ"

    def test_manage_stop_default_true(self):
        bundle = _bundle()
        fields = {"manage_stop"}
        result = build_strategy_params(bundle, fields)
        assert result["manage_stop"] is True

    def test_manage_stop_explicit_false_preserved(self):
        bundle = _bundle(params={"manage_stop": False})
        fields = {"manage_stop"}
        result = build_strategy_params(bundle, fields)
        assert result["manage_stop"] is False

    def test_no_config_fields_returns_unfiltered(self):
        bundle = _bundle(params={"arbitrary": "stuff"})
        result = build_strategy_params(bundle, None)
        assert result["symbols"] == ["BTCUSDT-PERP"]
        assert result["interval"] == "5m"
        assert result["arbitrary"] == "stuff"
        assert result["manage_stop"] is True
        assert result["order_id_tag"] == "000"

    def test_empty_symbols_skips_instrument_and_bar_type(self):
        bundle = _bundle(symbols=[])
        fields = {"instrument_id", "bar_type", "symbols", "interval"}
        result = build_strategy_params(bundle, fields)
        assert "instrument_id" not in result
        assert "bar_type" not in result
        assert result["symbols"] == []

    def test_original_params_dict_not_mutated(self):
        """``build_strategy_params`` must not mutate the caller's dict."""
        original = {"lookback": 100}
        bundle = _bundle(params=original)
        build_strategy_params(bundle, {"lookback", "symbols", "interval"})
        assert original == {"lookback": 100}


# ---------------------------------------------------------------------------
# check_symbol_profiles
# ---------------------------------------------------------------------------

class TestCheckSymbolProfiles:
    """``check_symbol_profiles`` is the pure core behind unrecognised-symbol warnings."""

    def _install_module(self, name: str, profiles, monkeypatch):
        mod = ModuleType(name)
        mod.SYMBOL_PROFILES = profiles  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    def test_missing_symbol_flagged(self, monkeypatch):
        class StratCls:
            pass

        StratCls.__module__ = "_loader_helper_missing"
        self._install_module(
            "_loader_helper_missing",
            {"BTC-USDT": {"enabled": True}},
            monkeypatch,
        )

        issues = check_symbol_profiles(StratCls, ["BTCUSDT-PERP", "DOGEUSDT-PERP"])
        assert issues == [("DOGEUSDT-PERP", "DOGE-USDT", "missing")]

    def test_disabled_symbol_flagged(self, monkeypatch):
        class StratCls:
            pass

        StratCls.__module__ = "_loader_helper_disabled"
        self._install_module(
            "_loader_helper_disabled",
            {"BTC-USDT": {"enabled": False}},
            monkeypatch,
        )

        issues = check_symbol_profiles(StratCls, ["BTCUSDT-PERP"])
        assert issues == [("BTCUSDT-PERP", "BTC-USDT", "disabled")]

    def test_no_symbol_profiles_returns_empty(self, monkeypatch):
        class StratCls:
            pass

        StratCls.__module__ = "_loader_helper_noprofiles"
        mod = ModuleType("_loader_helper_noprofiles")
        monkeypatch.setitem(sys.modules, "_loader_helper_noprofiles", mod)

        assert check_symbol_profiles(StratCls, ["BTCUSDT-PERP"]) == []

    def test_module_missing_from_sys_modules_returns_empty(self):
        class StratCls:
            pass

        StratCls.__module__ = "_loader_helper_never_imported"
        assert check_symbol_profiles(StratCls, ["BTCUSDT-PERP"]) == []

    def test_enabled_default_true_when_flag_absent(self, monkeypatch):
        """An entry without an ``enabled`` key is treated as enabled."""
        class StratCls:
            pass

        StratCls.__module__ = "_loader_helper_default_enabled"
        self._install_module(
            "_loader_helper_default_enabled",
            {"BTC-USDT": {}},
            monkeypatch,
        )

        assert check_symbol_profiles(StratCls, ["BTCUSDT-PERP"]) == []


# ---------------------------------------------------------------------------
# Cross-cutting invariants
# ---------------------------------------------------------------------------

class TestImportStability:
    """Backward-compat re-exports from ``strategy.loader`` must keep working."""

    def test_loader_re_exports_normalize_symbol(self):
        from tinohelm.strategy import loader as L
        assert L.normalize_symbol is normalize_symbol

    def test_loader_re_exports_parse_interval(self):
        from tinohelm.strategy import loader as L
        assert L.parse_interval is parse_interval

    def test_loader_underscored_aliases_remain(self):
        """External callers (and tests/portfolio/test_loader.py) rely on these."""
        from tinohelm.strategy import loader as L
        assert L._normalize_symbol is normalize_symbol
        assert L._make_bar_type_str is make_bar_type_str
        assert L._nt_symbol_to_jesse is nt_symbol_to_jesse
        assert L._INTERVAL_MAP is INTERVAL_MAP
        assert L._resolve_module_file is resolve_module_file
