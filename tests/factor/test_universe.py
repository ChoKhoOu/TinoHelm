"""Unit tests for ``tinohelm.factor.universe``.

Coverage
--------
- ``Universe.load_csv`` from a valid CSV
- ``Universe.load_csv`` raises on missing file
- ``Universe.load_csv`` raises on missing required column
- PIT query: symbol absent on listing_date itself (isolation window)
- PIT query: symbol present 7 days after listing_date (isolation clears)
- PIT query: symbol absent after delisting_date
- PIT query: symbol with no delisting_date is always active after isolation
- New-coin isolation: exactly 7 days boundary (< 7d excluded, >= 7d included)
- ``list_universes`` returns stems of all .csv files in a directory
- ``list_universes`` returns empty list when directory does not exist
- ``load_csv`` of the pre-built binance_perp_top20.csv succeeds
- Return type is sorted list[str]
"""
from __future__ import annotations

import csv
import io
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from tinohelm.factor.universe import Universe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(tmp_path: Path, rows: list[dict], filename: str = "test_uni.csv") -> Path:
    """Write a CSV from a list of dicts and return the path."""
    if not rows:
        raise ValueError("rows must be non-empty")
    path = tmp_path / filename
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


# ---------------------------------------------------------------------------
# load_csv — basic success
# ---------------------------------------------------------------------------

class TestLoadCsv:
    def test_load_valid_csv(self, tmp_path: Path):
        path = _write_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
            {"symbol": "ETHUSDT-PERP", "listing_date": "2020-03-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(path)
        assert len(uni) == 2
        assert uni.name == "test_uni"

    def test_load_sets_name_from_stem(self, tmp_path: Path):
        path = _write_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ], filename="my_universe.csv")
        uni = Universe.load_csv(path)
        assert uni.name == "my_universe"

    def test_load_with_delisting_date(self, tmp_path: Path):
        path = _write_csv(tmp_path, [
            {"symbol": "DOTUSDT-PERP", "listing_date": "2020-08-01", "delisting_date": "2024-06-01"},
        ])
        uni = Universe.load_csv(path)
        assert len(uni) == 1

    def test_load_raises_file_not_found(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.csv"
        with pytest.raises(FileNotFoundError, match="nonexistent.csv"):
            Universe.load_csv(missing)

    def test_load_raises_on_missing_symbol_column(self, tmp_path: Path):
        path = tmp_path / "bad.csv"
        path.write_text("listing_date,delisting_date\n2020-01-01,\n")
        with pytest.raises(ValueError, match="symbol"):
            Universe.load_csv(path)

    def test_load_raises_on_missing_listing_date_column(self, tmp_path: Path):
        path = tmp_path / "bad.csv"
        path.write_text("symbol,delisting_date\nBTCUSDT-PERP,\n")
        with pytest.raises(ValueError, match="listing_date"):
            Universe.load_csv(path)

    def test_load_skips_blank_symbol_rows(self, tmp_path: Path):
        path = tmp_path / "blanks.csv"
        path.write_text(
            "symbol,listing_date,delisting_date\n"
            ",2020-01-01,\n"
            "BTCUSDT-PERP,2020-01-01,\n"
        )
        uni = Universe.load_csv(path)
        assert len(uni) == 1

    def test_load_iso8601_datetime_listing(self, tmp_path: Path):
        """listing_date as full ISO-8601 datetime string should parse correctly."""
        path = _write_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01T00:00:00", "delisting_date": ""},
        ])
        uni = Universe.load_csv(path)
        assert len(uni) == 1
        # Should be active 8 days later
        symbols = uni.get_symbols_at(datetime(2020, 1, 9))
        assert "BTCUSDT-PERP" in symbols


# ---------------------------------------------------------------------------
# get_symbols_at — PIT semantics
# ---------------------------------------------------------------------------

class TestGetSymbolsAt:
    """Tests for PIT filtering and new-coin isolation."""

    @pytest.fixture()
    def simple_uni(self, tmp_path: Path) -> Universe:
        """Universe with 2 symbols — BTC listed 2020-01-01, ETH listed 2020-03-01."""
        path = _write_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
            {"symbol": "ETHUSDT-PERP", "listing_date": "2020-03-01", "delisting_date": ""},
        ])
        return Universe.load_csv(path)

    def test_symbol_absent_on_listing_date(self, simple_uni: Universe):
        """Listing day itself — isolation window still active."""
        result = simple_uni.get_symbols_at(datetime(2020, 1, 1))
        assert "BTCUSDT-PERP" not in result

    def test_symbol_absent_6_days_after_listing(self, simple_uni: Universe):
        """6 days post-listing — still in isolation window."""
        result = simple_uni.get_symbols_at(datetime(2020, 1, 7))
        assert "BTCUSDT-PERP" not in result

    def test_symbol_present_exactly_7_days_after_listing(self, simple_uni: Universe):
        """Exactly 7 days after listing — isolation clears, symbol is eligible."""
        result = simple_uni.get_symbols_at(datetime(2020, 1, 8))
        assert "BTCUSDT-PERP" in result

    def test_symbol_present_well_after_listing(self, simple_uni: Universe):
        result = simple_uni.get_symbols_at(datetime(2021, 6, 1))
        assert "BTCUSDT-PERP" in result
        assert "ETHUSDT-PERP" in result

    def test_second_symbol_excluded_before_its_listing(self, simple_uni: Universe):
        """Before ETH's listing, ETH must not appear."""
        result = simple_uni.get_symbols_at(datetime(2020, 1, 15))
        assert "BTCUSDT-PERP" in result
        assert "ETHUSDT-PERP" not in result

    def test_returns_sorted_list(self, simple_uni: Universe):
        result = simple_uni.get_symbols_at(datetime(2021, 1, 1))
        assert result == sorted(result)

    def test_return_type_is_list(self, simple_uni: Universe):
        result = simple_uni.get_symbols_at(datetime(2021, 1, 1))
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)


class TestGetSymbolsAtDelisting:
    """Tests for delisting_date filtering."""

    @pytest.fixture()
    def uni_with_delisting(self, tmp_path: Path) -> Universe:
        path = _write_csv(tmp_path, [
            # BTC: no delisting
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
            # DOT: delisted 2024-06-01
            {"symbol": "DOTUSDT-PERP", "listing_date": "2020-09-01", "delisting_date": "2024-06-01"},
        ])
        return Universe.load_csv(path)

    def test_active_before_delisting(self, uni_with_delisting: Universe):
        result = uni_with_delisting.get_symbols_at(datetime(2023, 1, 1))
        assert "DOTUSDT-PERP" in result

    def test_absent_on_delisting_date(self, uni_with_delisting: Universe):
        """At exactly delisting_date, the symbol should be excluded."""
        result = uni_with_delisting.get_symbols_at(datetime(2024, 6, 1))
        assert "DOTUSDT-PERP" not in result

    def test_absent_after_delisting(self, uni_with_delisting: Universe):
        result = uni_with_delisting.get_symbols_at(datetime(2025, 1, 1))
        assert "DOTUSDT-PERP" not in result
        # BTC still present
        assert "BTCUSDT-PERP" in result

    def test_no_delisting_always_active_after_isolation(self, uni_with_delisting: Universe):
        """Symbol with no delisting_date never disappears after isolation window."""
        result = uni_with_delisting.get_symbols_at(datetime(2030, 1, 1))
        assert "BTCUSDT-PERP" in result


class TestGetSymbolsAtPdTimestamp:
    """Ensure pd.Timestamp inputs work identically to datetime inputs."""

    @pytest.fixture()
    def uni(self, tmp_path: Path) -> Universe:
        path = _write_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        return Universe.load_csv(path)

    def test_pd_timestamp_input(self, uni: Universe):
        ts = pd.Timestamp("2020-01-08")
        result = uni.get_symbols_at(ts)
        assert "BTCUSDT-PERP" in result

    def test_pd_timestamp_during_isolation(self, uni: Universe):
        ts = pd.Timestamp("2020-01-05")
        result = uni.get_symbols_at(ts)
        assert "BTCUSDT-PERP" not in result

    def test_pd_timestamp_tz_aware_treated_naive(self, uni: Universe):
        """Timezone-aware pd.Timestamp is accepted (tz stripped, naive comparison)."""
        ts = pd.Timestamp("2020-01-08", tz="UTC")
        # Should not raise
        result = uni.get_symbols_at(ts)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# list_universes
# ---------------------------------------------------------------------------

class TestListUniverses:
    def test_returns_stems_of_csv_files(self, tmp_path: Path):
        (tmp_path / "alpha.csv").write_text("symbol,listing_date,delisting_date\n")
        (tmp_path / "beta.csv").write_text("symbol,listing_date,delisting_date\n")
        result = Universe.list_universes(base_dir=tmp_path)
        assert result == ["alpha", "beta"]

    def test_returns_empty_when_dir_not_exist(self, tmp_path: Path):
        missing = tmp_path / "no_such_dir"
        result = Universe.list_universes(base_dir=missing)
        assert result == []

    def test_ignores_non_csv_files(self, tmp_path: Path):
        (tmp_path / "universe.csv").write_text("symbol,listing_date\n")
        (tmp_path / "readme.txt").write_text("ignored")
        (tmp_path / "data.json").write_text("{}")
        result = Universe.list_universes(base_dir=tmp_path)
        assert result == ["universe"]

    def test_returns_sorted(self, tmp_path: Path):
        for name in ["zzz", "aaa", "mmm"]:
            (tmp_path / f"{name}.csv").write_text("symbol,listing_date\n")
        result = Universe.list_universes(base_dir=tmp_path)
        assert result == ["aaa", "mmm", "zzz"]

    def test_returns_list_type(self, tmp_path: Path):
        result = Universe.list_universes(base_dir=tmp_path)
        assert isinstance(result, list)

    def test_default_base_dir_does_not_raise(self):
        """Calling without base_dir should not raise even if default dir is missing."""
        # The actual ~/.tino/research/universes/ may or may not exist
        result = Universe.list_universes()
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# from_symbols — inline construction without CSV
# ---------------------------------------------------------------------------

class TestFromSymbols:
    """Tests for ``Universe.from_symbols`` (no CSV required)."""

    def test_basic_construction(self):
        """Two symbols are present at a modern timestamp."""
        u = Universe.from_symbols(["BTCUSDT-PERP", "ETHUSDT-PERP"])
        result = u.get_symbols_at(datetime(2025, 1, 1))
        assert "BTCUSDT-PERP" in result
        assert "ETHUSDT-PERP" in result

    def test_empty_list(self):
        """Empty symbol list yields empty results at any timestamp."""
        u = Universe.from_symbols([])
        assert u.get_symbols_at(datetime(2025, 1, 1)) == []

    def test_pit_no_isolation(self):
        """listing_date defaults to 1970-01-01 → 7-day isolation never triggers
        for modern timestamps; symbol is directly available."""
        u = Universe.from_symbols(["BTCUSDT-PERP"])
        result = u.get_symbols_at(datetime(2025, 1, 1))
        assert "BTCUSDT-PERP" in result

    def test_name_defaults_to_inline(self):
        """Default ``name`` attribute is ``'inline'``."""
        u = Universe.from_symbols(["BTCUSDT-PERP"])
        assert u.name == "inline"

    def test_custom_name(self):
        """Custom name is preserved."""
        u = Universe.from_symbols(["BTCUSDT-PERP"], name="my_list")
        assert u.name == "my_list"

    def test_custom_listing_date_applies_isolation(self):
        """When a recent listing_date is given, 7-day isolation applies."""
        listing = datetime(2025, 1, 10)
        u = Universe.from_symbols(["BTCUSDT-PERP"], listing_date=listing)
        # 6 days after listing — still inside isolation window
        assert "BTCUSDT-PERP" not in u.get_symbols_at(datetime(2025, 1, 16))
        # 7 days after listing — isolation clears
        assert "BTCUSDT-PERP" in u.get_symbols_at(datetime(2025, 1, 17))

    def test_returns_sorted_list(self):
        """``get_symbols_at`` returns a sorted list even with from_symbols."""
        u = Universe.from_symbols(["ETHUSDT-PERP", "BTCUSDT-PERP", "SOLUSDT-PERP"])
        result = u.get_symbols_at(datetime(2025, 1, 1))
        assert result == sorted(result)

    def test_len_matches_symbol_count(self):
        """``len(universe)`` equals the number of symbols passed in."""
        symbols = ["BTCUSDT-PERP", "ETHUSDT-PERP", "SOLUSDT-PERP"]
        u = Universe.from_symbols(symbols)
        assert len(u) == 3


# ---------------------------------------------------------------------------
# Pre-built binance_perp_top20.csv
# ---------------------------------------------------------------------------

class TestPrebuiltUniverse:
    """Verify that the pre-built binance_perp_top20.csv exists and is valid."""

    @pytest.fixture()
    def top20_path(self) -> Path:
        return Path.home() / ".tino" / "research" / "universes" / "binance_perp_top20.csv"

    def test_file_exists(self, top20_path: Path):
        if not top20_path.exists():
            pytest.skip(
                f"Pre-built universe not found at {top20_path}. "
                "Run scripts/generate_binance_perp_top20.py or create it manually."
            )

    def test_load_succeeds(self, top20_path: Path):
        if not top20_path.exists():
            pytest.skip("binance_perp_top20.csv not present")
        uni = Universe.load_csv(top20_path)
        assert len(uni) >= 10, "Expected at least 10 symbols in Top20 universe"

    def test_contains_btc_and_eth(self, top20_path: Path):
        if not top20_path.exists():
            pytest.skip("binance_perp_top20.csv not present")
        uni = Universe.load_csv(top20_path)
        all_symbols = list(uni)
        assert "BTCUSDT-PERP" in all_symbols
        assert "ETHUSDT-PERP" in all_symbols

    def test_pit_query_2023(self, top20_path: Path):
        """All top20 symbols should be active in mid-2023."""
        if not top20_path.exists():
            pytest.skip("binance_perp_top20.csv not present")
        uni = Universe.load_csv(top20_path)
        symbols = uni.get_symbols_at(datetime(2023, 6, 1))
        # At minimum BTC/ETH should be present
        assert "BTCUSDT-PERP" in symbols
        assert "ETHUSDT-PERP" in symbols

    def test_listed_in_list_universes(self, top20_path: Path, paths_override):
        if not top20_path.exists():
            pytest.skip("binance_perp_top20.csv not present")
        paths_override("universes_dir", top20_path.parent)
        names = Universe.list_universes()
        assert "binance_perp_top20" in names
