"""Tests for pure helpers in tinohelm.api.routes.data.

Covers interval ⇄ NT-suffix conversion, parquet size calculation, and the
storage-file deletion helper used by DELETE /api/data/catalog/{id}.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tinohelm.api.routes.data import (
    _UNIT_MAP,
    _UNIT_REVERSE,
    _delete_storage_files,
    _interval_to_nt,
    _nt_to_interval,
    _parquet_size_for,
)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


class TestUnitMaps:
    def test_unit_map_values(self):
        assert _UNIT_MAP == {"m": "MINUTE", "h": "HOUR", "d": "DAY"}

    def test_unit_reverse_is_inverse(self):
        assert _UNIT_REVERSE == {v: k for k, v in _UNIT_MAP.items()}

    def test_unit_reverse_roundtrip(self):
        for short, long in _UNIT_MAP.items():
            assert _UNIT_REVERSE[long] == short


# ---------------------------------------------------------------------------
# _interval_to_nt
# ---------------------------------------------------------------------------


class TestIntervalToNt:
    @pytest.mark.parametrize(
        "interval, expected",
        [
            ("1m", "1-MINUTE"),
            ("5m", "5-MINUTE"),
            ("15m", "15-MINUTE"),
            ("1h", "1-HOUR"),
            ("4h", "4-HOUR"),
            ("1d", "1-DAY"),
            ("99m", "99-MINUTE"),
        ],
    )
    def test_converts(self, interval: str, expected: str):
        assert _interval_to_nt(interval) == expected

    @pytest.mark.parametrize("bad", ["", "5", "5x", "abc", "m5", "5M", "1ms"])
    def test_invalid_raises(self, bad: str):
        with pytest.raises(ValueError, match="Invalid interval"):
            _interval_to_nt(bad)

    def test_error_message_includes_input(self):
        with pytest.raises(ValueError, match="abc"):
            _interval_to_nt("abc")


# ---------------------------------------------------------------------------
# _nt_to_interval
# ---------------------------------------------------------------------------


class TestNtToInterval:
    @pytest.mark.parametrize(
        "suffix, expected",
        [
            ("1-MINUTE", "1m"),
            ("5-MINUTE", "5m"),
            ("15-MINUTE", "15m"),
            ("1-HOUR", "1h"),
            ("4-HOUR", "4h"),
            ("1-DAY", "1d"),
        ],
    )
    def test_converts(self, suffix: str, expected: str):
        assert _nt_to_interval(suffix) == expected

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "MINUTE",
            "5",
            "5-minute",     # regex is digit-WORD but _UNIT_REVERSE is case-sensitive
            "5-FOO",
            "abc-MINUTE",
        ],
    )
    def test_unknown_returns_none(self, bad: str):
        assert _nt_to_interval(bad) is None

    def test_round_trip_from_interval_to_nt_and_back(self):
        for interval in ["1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d"]:
            suffix = _interval_to_nt(interval)
            assert _nt_to_interval(suffix) == interval


# ---------------------------------------------------------------------------
# _parquet_size_for
# ---------------------------------------------------------------------------


class TestParquetSizeFor:
    def test_returns_zero_when_dir_missing(self, tmp_path: Path):
        # No bar directory at all
        assert _parquet_size_for(str(tmp_path), "BTCUSDT-PERP", "5m") == 0

    def test_sums_parquet_files(self, tmp_path: Path):
        # Build the expected directory for normalized symbol
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        bar_dir = tmp_path / "data" / "bar" / f"{nt_sym}-5-MINUTE-LAST-EXTERNAL"
        bar_dir.mkdir(parents=True)
        (bar_dir / "a.parquet").write_bytes(b"x" * 100)
        (bar_dir / "b.parquet").write_bytes(b"y" * 200)
        # Non-parquet files must be ignored
        (bar_dir / "ignore.txt").write_bytes(b"z" * 1000)

        assert _parquet_size_for(str(tmp_path), "BTCUSDT-PERP", "5m") == 300

    def test_invalid_interval_propagates_value_error(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Invalid interval"):
            _parquet_size_for(str(tmp_path), "BTCUSDT-PERP", "5x")


# ---------------------------------------------------------------------------
# _delete_storage_files
# ---------------------------------------------------------------------------


class TestDeleteStorageFiles:
    def test_bar_deletes_parquet_and_dir(self, tmp_path: Path):
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        bar_dir = tmp_path / "data" / "bar" / f"{nt_sym}-5-MINUTE-LAST-EXTERNAL"
        bar_dir.mkdir(parents=True)
        (bar_dir / "a.parquet").write_bytes(b"x" * 50)
        (bar_dir / "b.parquet").write_bytes(b"y" * 75)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "bar", "5m", str(tmp_path)
        )
        assert deleted == 2
        assert freed == 125
        assert not bar_dir.exists()

    def test_bar_noop_when_dir_missing(self, tmp_path: Path):
        assert _delete_storage_files(
            "BTCUSDT-PERP", "bar", "5m", str(tmp_path)
        ) == (0, 0)

    def test_trade_tick_deletes_parquet(self, tmp_path: Path):
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        tick_dir = tmp_path / "data" / "trade_tick" / nt_sym
        tick_dir.mkdir(parents=True)
        (tick_dir / "a.parquet").write_bytes(b"x" * 30)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "trade_tick", "tick", str(tmp_path)
        )
        assert deleted == 1
        assert freed == 30

    def test_quote_tick_handled(self, tmp_path: Path):
        # No real data: exercises the branch and verifies (0, 0) default
        assert _delete_storage_files(
            "BTCUSDT-PERP", "quote_tick", "tick", str(tmp_path)
        ) == (0, 0)

    def test_funding_rate_deletes_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import tinohelm.data.funding_cache as fc

        cache_dir = tmp_path / "funding_rates"
        cache_dir.mkdir()
        target = cache_dir / "btcusdt-perp.json"
        target.write_bytes(b'[{"funding": 0.0001}]')
        monkeypatch.setattr(fc, "_CACHE_DIR", cache_dir)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "funding_rate", "8h", str(tmp_path)
        )
        assert deleted == 1
        assert freed == len(b'[{"funding": 0.0001}]')
        assert not target.exists()

    def test_funding_rate_missing_is_noop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import tinohelm.data.funding_cache as fc

        cache_dir = tmp_path / "funding_rates"
        cache_dir.mkdir()
        monkeypatch.setattr(fc, "_CACHE_DIR", cache_dir)

        assert _delete_storage_files(
            "BTCUSDT-PERP", "funding_rate", "8h", str(tmp_path)
        ) == (0, 0)

    def test_unknown_data_type_warns_and_returns_zero(self, tmp_path: Path, caplog):
        import logging

        caplog.set_level(logging.WARNING, logger="tinohelm.api.routes.data")
        result = _delete_storage_files(
            "BTCUSDT-PERP", "mystery", "irrelevant", str(tmp_path)
        )
        assert result == (0, 0)
        assert any("No storage handler" in r.getMessage() for r in caplog.records)

    def test_bar_removes_empty_dir(self, tmp_path: Path):
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        bar_dir = tmp_path / "data" / "bar" / f"{nt_sym}-5-MINUTE-LAST-EXTERNAL"
        bar_dir.mkdir(parents=True)
        (bar_dir / "only.parquet").write_bytes(b"a" * 10)

        _delete_storage_files("BTCUSDT-PERP", "bar", "5m", str(tmp_path))
        # Directory was pruned because it was empty post-delete
        assert not bar_dir.exists()

    def test_bar_keeps_dir_if_non_parquet_remains(self, tmp_path: Path):
        """If there are other files in the dir (e.g. metadata), only parquet are removed and dir stays."""
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        bar_dir = tmp_path / "data" / "bar" / f"{nt_sym}-5-MINUTE-LAST-EXTERNAL"
        bar_dir.mkdir(parents=True)
        (bar_dir / "one.parquet").write_bytes(b"x" * 5)
        (bar_dir / "meta.txt").write_bytes(b"keep")

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "bar", "5m", str(tmp_path)
        )
        assert deleted == 1
        assert freed == 5
        assert bar_dir.exists()
        assert (bar_dir / "meta.txt").exists()
