"""Unit tests for ``tinohelm.factor.alias``.

Coverage
--------
- Basic alias resolution (English short names, full names)
- Chinese alias resolution
- Unknown field returns the lowercased input (pass-through)
- ``custom`` parameter overrides built-in aliases
- ``custom`` parameter adds new aliases not in the built-in table
- Case-insensitive matching (mixed-case input → canonical lowercase)
- Data source shortcuts resolve correctly
- ``FIELD_ALIAS`` table completeness: mandatory fields present
"""
from __future__ import annotations

import pytest

from tinohelm.factor.alias import FIELD_ALIAS, resolve_alias


# ---------------------------------------------------------------------------
# Basic canonical resolution
# ---------------------------------------------------------------------------

class TestResolveBasic:
    def test_close_canonical(self):
        assert resolve_alias("close") == "close"

    def test_open_canonical(self):
        assert resolve_alias("open") == "open"

    def test_high_canonical(self):
        assert resolve_alias("high") == "high"

    def test_low_canonical(self):
        assert resolve_alias("low") == "low"

    def test_volume_canonical(self):
        assert resolve_alias("volume") == "volume"

    def test_amount_canonical(self):
        assert resolve_alias("amount") == "amount"

    def test_vwap_canonical(self):
        assert resolve_alias("vwap") == "vwap"

    def test_funding_rate_canonical(self):
        assert resolve_alias("funding_rate") == "funding_rate"

    def test_open_interest_canonical(self):
        assert resolve_alias("open_interest") == "open_interest"

    def test_orderbook_imbalance_canonical(self):
        assert resolve_alias("orderbook_imbalance") == "orderbook_imbalance"


# ---------------------------------------------------------------------------
# Short alias resolution
# ---------------------------------------------------------------------------

class TestResolveShortAliases:
    def test_vol_to_volume(self):
        assert resolve_alias("vol") == "volume"

    def test_oi_to_open_interest(self):
        assert resolve_alias("oi") == "open_interest"

    def test_fr_to_funding_rate(self):
        assert resolve_alias("fr") == "funding_rate"

    def test_obi_to_orderbook_imbalance(self):
        assert resolve_alias("obi") == "orderbook_imbalance"

    def test_c_to_close(self):
        assert resolve_alias("c") == "close"

    def test_v_to_volume(self):
        assert resolve_alias("v") == "volume"

    def test_turnover_to_amount(self):
        assert resolve_alias("turnover") == "amount"

    def test_quote_volume_to_amount(self):
        assert resolve_alias("quote_volume") == "amount"

    def test_last_to_close(self):
        assert resolve_alias("last") == "close"


# ---------------------------------------------------------------------------
# Chinese alias resolution
# ---------------------------------------------------------------------------

class TestResolveChineseAliases:
    def test_shoubi_to_close(self):
        assert resolve_alias("收盘") == "close"

    def test_shoubijia_to_close(self):
        assert resolve_alias("收盘价") == "close"

    def test_kaipan_to_open(self):
        assert resolve_alias("开盘") == "open"

    def test_kaipanjia_to_open(self):
        assert resolve_alias("开盘价") == "open"

    def test_zuigao_to_high(self):
        assert resolve_alias("最高") == "high"

    def test_zuidi_to_low(self):
        assert resolve_alias("最低") == "low"

    def test_chengjiao_volume(self):
        assert resolve_alias("成交量") == "volume"

    def test_chengjiao_amount(self):
        assert resolve_alias("成交额") == "amount"

    def test_jingjunjia_to_vwap(self):
        assert resolve_alias("成交均价") == "vwap"

    def test_zijinfeilv_to_funding_rate(self):
        assert resolve_alias("资金费率") == "funding_rate"

    def test_chichiliang_to_open_interest(self):
        assert resolve_alias("持仓量") == "open_interest"

    def test_weituo_to_obi(self):
        assert resolve_alias("委托不平衡") == "orderbook_imbalance"


# ---------------------------------------------------------------------------
# Case-insensitive matching
# ---------------------------------------------------------------------------

class TestCaseInsensitive:
    def test_close_upper(self):
        assert resolve_alias("Close") == "close"

    def test_close_all_caps(self):
        assert resolve_alias("CLOSE") == "close"

    def test_volume_mixed(self):
        assert resolve_alias("VoLuMe") == "volume"

    def test_vol_upper(self):
        assert resolve_alias("VOL") == "volume"

    def test_funding_mixed(self):
        assert resolve_alias("Funding_Rate") == "funding_rate"

    def test_vwap_upper(self):
        assert resolve_alias("VWAP") == "vwap"

    def test_obi_upper(self):
        assert resolve_alias("OBI") == "orderbook_imbalance"


# ---------------------------------------------------------------------------
# Unknown field pass-through
# ---------------------------------------------------------------------------

class TestUnknownFieldPassthrough:
    """Unknown fields are returned as lowercase — no exception raised."""

    def test_unknown_returns_lowercased(self):
        assert resolve_alias("my_custom_field") == "my_custom_field"

    def test_unknown_uppercase_returned_lowercased(self):
        assert resolve_alias("MY_FIELD") == "my_field"

    def test_completely_unknown(self):
        result = resolve_alias("xyzzy_not_a_field")
        assert result == "xyzzy_not_a_field"

    def test_numeric_string(self):
        # Edge case: numeric-ish strings should not raise
        result = resolve_alias("123")
        assert result == "123"


# ---------------------------------------------------------------------------
# Custom override
# ---------------------------------------------------------------------------

class TestCustomOverride:
    def test_custom_overrides_builtin(self):
        """custom dict can shadow a built-in alias."""
        result = resolve_alias("vol", custom={"vol": "custom_volume"})
        assert result == "custom_volume"

    def test_custom_adds_new_alias(self):
        """custom dict can introduce a brand new alias."""
        result = resolve_alias("my_alias", custom={"my_alias": "close"})
        assert result == "close"

    def test_custom_case_insensitive_key(self):
        """custom dict keys are matched case-insensitively."""
        result = resolve_alias("VOL", custom={"vol": "custom_volume"})
        assert result == "custom_volume"

    def test_custom_mixed_case_key(self):
        result = resolve_alias("MixedKey", custom={"mixedkey": "open_interest"})
        assert result == "open_interest"

    def test_no_custom_falls_through_to_builtin(self):
        """Providing an empty custom dict doesn't break built-in resolution."""
        result = resolve_alias("funding_rate", custom={})
        assert result == "funding_rate"

    def test_custom_none_uses_builtin(self):
        result = resolve_alias("close", custom=None)
        assert result == "close"


# ---------------------------------------------------------------------------
# Data source shortcuts
# ---------------------------------------------------------------------------

class TestDataSourceShortcuts:
    def test_bar_source(self):
        assert resolve_alias("bar") == "bar"

    def test_kline_to_bar(self):
        assert resolve_alias("kline") == "bar"

    def test_klines_to_bar(self):
        assert resolve_alias("klines") == "bar"

    def test_trade_tick(self):
        assert resolve_alias("trade_tick") == "trade_tick"

    def test_aggtrade_to_trade_tick(self):
        assert resolve_alias("aggtrade") == "trade_tick"

    def test_quote_tick(self):
        assert resolve_alias("quote_tick") == "quote_tick"

    def test_bookticker_to_quote_tick(self):
        assert resolve_alias("bookticker") == "quote_tick"


# ---------------------------------------------------------------------------
# FIELD_ALIAS table completeness
# ---------------------------------------------------------------------------

class TestFieldAliasTableCompleteness:
    """Sanity checks that mandatory canonical targets exist in the table."""

    REQUIRED_CANONICALS = {
        "close", "open", "high", "low", "volume", "amount",
        "vwap", "funding_rate", "open_interest", "orderbook_imbalance",
        "bar", "trade_tick", "quote_tick",
    }

    def test_required_canonicals_are_reachable(self):
        """Every required canonical name must be reachable via resolve_alias."""
        for canonical in self.REQUIRED_CANONICALS:
            result = resolve_alias(canonical)
            assert result == canonical, (
                f"resolve_alias('{canonical}') returned '{result}', expected '{canonical}'"
            )

    def test_all_table_values_are_lowercase(self):
        """Every value in FIELD_ALIAS must be lowercase (canonical convention)."""
        for alias, canonical in FIELD_ALIAS.items():
            assert canonical == canonical.lower(), (
                f"FIELD_ALIAS['{alias}'] = '{canonical}' is not lowercase"
            )

    def test_all_table_keys_are_lowercase(self):
        """All keys must be stored lowercase (resolve_alias lowercases input before lookup)."""
        for alias in FIELD_ALIAS:
            assert alias == alias.lower(), f"Key '{alias}' in FIELD_ALIAS is not lowercase"
