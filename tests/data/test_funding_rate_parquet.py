"""Unit tests for funding-rate Parquet write/read path (s13 upgrade).

Coverage
--------
1. write_funding_rate_parquet — writes Parquet with correct schema and values.
2. read_funding_rate_parquet — reads back and values match.
3. write_funding_rate_parquet — incremental merge dedupes by ts_event.
4. Migration script — 2 JSON files → 2 Parquet files; content equal.
5. Migration script --dry-run — no files written.
6. DataLayer._load_funding_rate — Parquet-only → loads correctly.
7. DataLayer._load_funding_rate — JSON-only → fallback loads correctly.
8. DataLayer._load_funding_rate — both present → Parquet wins (values match).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_records_dataclass(n: int = 3):
    """Return n BinanceFundingRate dataclass instances."""
    from tinohelm.data.converters.funding_rate import BinanceFundingRate

    records = []
    base_ms = 1_620_000_000_000  # 2021-05-03T00:00:00 UTC in ms
    for i in range(n):
        ms = base_ms + i * 8 * 3600 * 1000
        ns = ms * 1_000_000
        records.append(BinanceFundingRate(
            symbol="BTCUSDT-PERP",
            funding_rate=0.0001 * (i + 1),
            funding_time_ms=ms,
            ts_event=ns,
            ts_init=ns,
        ))
    return records


def _make_records_dict(n: int = 3) -> list[dict]:
    """Return n plain-dict records in JSON-cache format."""
    base_ms = 1_620_000_000_000
    return [
        {
            "funding_time_ms": base_ms + i * 8 * 3600 * 1000,
            "funding_rate": 0.0001 * (i + 1),
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 1-2. write / read round-trip
# ---------------------------------------------------------------------------

class TestWriteReadRoundTrip:
    def test_write_parquet_creates_file(self, tmp_path: Path):
        from tinohelm.data.catalog import write_funding_rate_parquet, funding_rate_parquet_path

        records = _make_records_dataclass(3)
        out = write_funding_rate_parquet(records, "BTCUSDT-PERP", tmp_path)

        expected = funding_rate_parquet_path("BTCUSDT-PERP", tmp_path)
        assert out == expected
        assert out.exists()

    def test_write_parquet_schema(self, tmp_path: Path):
        import pyarrow.parquet as pq
        from tinohelm.data.catalog import write_funding_rate_parquet

        records = _make_records_dataclass(2)
        out = write_funding_rate_parquet(records, "BTCUSDT-PERP", tmp_path)

        table = pq.read_table(str(out))
        assert "ts_event" in table.schema.names
        assert "funding_rate" in table.schema.names
        assert str(table.schema.field("ts_event").type) == "int64"
        assert str(table.schema.field("funding_rate").type) == "double"

    def test_read_values_match_written(self, tmp_path: Path):
        from tinohelm.data.catalog import write_funding_rate_parquet, read_funding_rate_parquet

        records = _make_records_dataclass(3)
        write_funding_rate_parquet(records, "BTCUSDT-PERP", tmp_path)

        df = read_funding_rate_parquet("BTCUSDT-PERP", tmp_path)
        assert df is not None
        assert len(df) == 3

        # ts_event must be nanosecond precision (each original ms × 1e6)
        expected_ts_ns = [r.ts_event for r in records]
        assert list(df["ts_event"]) == expected_ts_ns

        # funding_rate values preserved
        expected_rates = [r.funding_rate for r in records]
        for written, expected in zip(df["funding_rate"], expected_rates):
            assert abs(written - expected) < 1e-10

    def test_read_nonexistent_returns_none(self, tmp_path: Path):
        from tinohelm.data.catalog import read_funding_rate_parquet

        result = read_funding_rate_parquet("NONEXISTENT", tmp_path)
        assert result is None

    def test_write_accepts_dict_records(self, tmp_path: Path):
        """Dict records (JSON-cache format) are also accepted."""
        from tinohelm.data.catalog import write_funding_rate_parquet, read_funding_rate_parquet

        dicts = _make_records_dict(2)
        write_funding_rate_parquet(dicts, "ETHUSDT-PERP", tmp_path)

        df = read_funding_rate_parquet("ETHUSDT-PERP", tmp_path)
        assert df is not None
        assert len(df) == 2
        # ts_event should be ms * 1_000_000
        assert df["ts_event"].iloc[0] == dicts[0]["funding_time_ms"] * 1_000_000


# ---------------------------------------------------------------------------
# 3. Incremental merge / dedup
# ---------------------------------------------------------------------------

class TestIncrementalMerge:
    def test_second_write_merges_not_duplicates(self, tmp_path: Path):
        from tinohelm.data.catalog import write_funding_rate_parquet, read_funding_rate_parquet

        first_batch = _make_records_dataclass(2)   # ts 0, 1
        second_batch = _make_records_dataclass(3)  # ts 0, 1, 2 (overlap with first)

        write_funding_rate_parquet(first_batch, "BTCUSDT-PERP", tmp_path)
        write_funding_rate_parquet(second_batch, "BTCUSDT-PERP", tmp_path)

        df = read_funding_rate_parquet("BTCUSDT-PERP", tmp_path)
        assert df is not None
        # 3 unique ts_events, no duplicates
        assert len(df) == 3
        assert len(df["ts_event"].unique()) == 3


# ---------------------------------------------------------------------------
# 4-5. Migration script
# ---------------------------------------------------------------------------

class TestMigrationScript:
    def _make_json_dir(self, tmp_path: Path, symbols: list[str], n_records: int = 3) -> Path:
        """Write JSON files for each symbol and return the json_dir."""
        json_dir = tmp_path / "funding_rates"
        json_dir.mkdir()
        for sym in symbols:
            records = _make_records_dict(n_records)
            path = json_dir / f"{sym.lower()}.json"
            with open(path, "w") as fh:
                json.dump(records, fh)
        return json_dir

    def test_migration_creates_parquet_files(self, tmp_path: Path):
        """Running the migration on 2 JSON files produces 2 Parquet files."""
        # Add scripts/ to path so the script can be imported
        scripts_dir = Path(__file__).parent.parent.parent / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

        from migrate_funding_json_to_parquet import migrate
        from tinohelm.data.catalog import funding_rate_parquet_path

        symbols = ["BTCUSDT-PERP", "ETHUSDT-PERP"]
        json_dir = self._make_json_dir(tmp_path, symbols, n_records=3)
        catalog_root = tmp_path / "catalog"

        n = migrate(json_dir=json_dir, catalog_root=catalog_root, dry_run=False, delete_json=False)

        assert n == 2
        for sym in symbols:
            p = funding_rate_parquet_path(sym, catalog_root)
            assert p.exists(), f"Expected Parquet at {p}"

    def test_migration_content_matches_json(self, tmp_path: Path):
        """Parquet content (ts_event, funding_rate) matches the source JSON exactly."""
        scripts_dir = Path(__file__).parent.parent.parent / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

        from migrate_funding_json_to_parquet import migrate
        from tinohelm.data.catalog import read_funding_rate_parquet

        symbols = ["BTCUSDT-PERP"]
        json_dir = self._make_json_dir(tmp_path, symbols, n_records=4)
        catalog_root = tmp_path / "catalog"

        # Read the source JSON before migration
        source_records = _make_records_dict(4)

        migrate(json_dir=json_dir, catalog_root=catalog_root, dry_run=False, delete_json=False)

        df = read_funding_rate_parquet("BTCUSDT-PERP", catalog_root)
        assert df is not None
        assert len(df) == 4

        for i, src in enumerate(source_records):
            expected_ts_ns = src["funding_time_ms"] * 1_000_000
            assert df["ts_event"].iloc[i] == expected_ts_ns
            assert abs(df["funding_rate"].iloc[i] - src["funding_rate"]) < 1e-10

    def test_migration_dry_run_writes_nothing(self, tmp_path: Path):
        """--dry-run must not write any Parquet files."""
        scripts_dir = Path(__file__).parent.parent.parent / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

        from migrate_funding_json_to_parquet import migrate
        from tinohelm.data.catalog import funding_rate_parquet_path

        symbols = ["BTCUSDT-PERP"]
        json_dir = self._make_json_dir(tmp_path, symbols, n_records=2)
        catalog_root = tmp_path / "catalog"

        migrate(json_dir=json_dir, catalog_root=catalog_root, dry_run=True, delete_json=False)

        p = funding_rate_parquet_path("BTCUSDT-PERP", catalog_root)
        assert not p.exists(), "dry-run must not write Parquet"

    def test_migration_delete_json_removes_source(self, tmp_path: Path):
        """--delete-json removes the JSON file after successful conversion."""
        scripts_dir = Path(__file__).parent.parent.parent / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

        from migrate_funding_json_to_parquet import migrate

        symbols = ["BTCUSDT-PERP"]
        json_dir = self._make_json_dir(tmp_path, symbols, n_records=2)
        catalog_root = tmp_path / "catalog"
        json_file = json_dir / "btcusdt-perp.json"

        assert json_file.exists()
        migrate(json_dir=json_dir, catalog_root=catalog_root, dry_run=False, delete_json=True)
        assert not json_file.exists(), "JSON should be deleted after migration with --delete-json"


# ---------------------------------------------------------------------------
# 6-8. DataLayer._load_funding_rate priority
# ---------------------------------------------------------------------------

def _make_universe(tmp_path: Path) -> "Universe":
    """Create a minimal Universe with a single always-eligible symbol."""
    import csv
    from tinohelm.factor.universe import Universe

    uni_path = tmp_path / "uni.csv"
    with uni_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["symbol", "listing_date", "delisting_date"])
        writer.writeheader()
        writer.writerow({"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""})
    return Universe.load_csv(uni_path)


class TestDataLayerFundingLoad:
    """DataLayer._load_funding_rate priority: Parquet > JSON."""

    # Reference: 2021-05-03T00:00:00 UTC, 2 records 8h apart
    _BASE_MS = 1_620_000_000_000
    _8H_MS = 8 * 3600 * 1000

    def _records(self) -> list[dict]:
        return [
            {"funding_time_ms": self._BASE_MS, "funding_rate": 0.0001},
            {"funding_time_ms": self._BASE_MS + self._8H_MS, "funding_rate": 0.0002},
        ]

    def _write_parquet(self, catalog_root: Path) -> None:
        from tinohelm.data.catalog import write_funding_rate_parquet
        write_funding_rate_parquet(self._records(), "BTCUSDT-PERP", catalog_root)

    def _write_json(self, funding_dir: Path) -> None:
        funding_dir.mkdir(parents=True, exist_ok=True)
        path = funding_dir / "btcusdt-perp.json"
        with open(path, "w") as fh:
            json.dump(self._records(), fh)

    def test_parquet_only_loads_correctly(self, tmp_path: Path):
        """Only Parquet present → series with 2 correct values."""
        catalog_root = tmp_path / "catalog"
        funding_dir = tmp_path / "no_json_here"

        self._write_parquet(catalog_root)
        uni = _make_universe(tmp_path)
        from tinohelm.factor.data_layer import DataLayer

        dl = DataLayer(uni, catalog_root=catalog_root, funding_dir=funding_dir)
        series = dl._load_funding_rate("BTCUSDT-PERP", start=None, end=None)

        assert isinstance(series, pd.Series)
        assert len(series) == 2
        assert series.iloc[0] == pytest.approx(0.0001)
        assert series.iloc[1] == pytest.approx(0.0002)

    def test_json_only_fallback_loads_correctly(self, tmp_path: Path):
        """Only JSON present → series loaded from JSON fallback."""
        catalog_root = tmp_path / "catalog"
        funding_dir = tmp_path / "funding_rates"

        self._write_json(funding_dir)
        uni = _make_universe(tmp_path)
        from tinohelm.factor.data_layer import DataLayer

        dl = DataLayer(uni, catalog_root=catalog_root, funding_dir=funding_dir)
        series = dl._load_funding_rate("BTCUSDT-PERP", start=None, end=None)

        assert isinstance(series, pd.Series)
        assert len(series) == 2
        assert series.iloc[0] == pytest.approx(0.0001)

    def test_parquet_wins_when_both_present(self, tmp_path: Path):
        """Both Parquet and JSON present → Parquet values are returned.

        We write different rates to Parquet and JSON so the winner is detectable.
        """
        import pyarrow as pa
        import pyarrow.parquet as pq
        from tinohelm.data.catalog import funding_rate_parquet_path

        catalog_root = tmp_path / "catalog"
        funding_dir = tmp_path / "funding_rates"

        # Write Parquet with distinct rate 0.9999
        parquet_records = [
            {"funding_time_ms": self._BASE_MS, "funding_rate": 0.9999},
        ]
        from tinohelm.data.catalog import write_funding_rate_parquet
        write_funding_rate_parquet(parquet_records, "BTCUSDT-PERP", catalog_root)

        # Write JSON with different rate 0.0001
        funding_dir.mkdir(parents=True, exist_ok=True)
        json_records = [{"funding_time_ms": self._BASE_MS, "funding_rate": 0.0001}]
        with open(funding_dir / "btcusdt-perp.json", "w") as fh:
            json.dump(json_records, fh)

        uni = _make_universe(tmp_path)
        from tinohelm.factor.data_layer import DataLayer

        dl = DataLayer(uni, catalog_root=catalog_root, funding_dir=funding_dir)
        series = dl._load_funding_rate("BTCUSDT-PERP", start=None, end=None)

        # Parquet has 0.9999; JSON has 0.0001. Parquet must win.
        assert series.iloc[0] == pytest.approx(0.9999), (
            f"Expected Parquet value 0.9999, got {series.iloc[0]} — JSON fallback must NOT win"
        )
