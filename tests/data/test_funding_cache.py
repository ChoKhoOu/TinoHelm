"""Tests for tinohelm.data.funding_cache — the I/O layer on top of helpers.

The decision / normalisation logic lives in ``funding_cache_helpers`` and is
tested there. Here we exercise the filesystem bridge (``_load_cache``,
``_save_cache``) and the ``load_funding_rates`` pure-read orchestrator.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import tinohelm.data.funding_cache as fc


@pytest.fixture
def tmp_cache_dir(tmp_path: Path, paths_override) -> Path:
    """Redirect the active cache dir to a tmp_path sandbox via PathRegistry override."""
    d = tmp_path / "funding_rates"
    paths_override("funding_rates", d)
    return d


def _utc(y, m, d, *rest):
    return datetime(y, m, d, *rest, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _cache_path — lowercasing contract
# ---------------------------------------------------------------------------

class TestCachePath:
    def test_lowercases_symbol(self, tmp_cache_dir: Path):
        assert fc._cache_path("BTCUSDT-PERP").name == "btcusdt-perp.json"

    def test_path_rooted_at_cache_dir(self, tmp_cache_dir: Path):
        p = fc._cache_path("ETHUSDT-PERP")
        assert p.parent == tmp_cache_dir

    def test_mixed_case_symbol_normalised(self, tmp_cache_dir: Path):
        # Callers pass anything — we must always serialize to lowercase.
        assert fc._cache_path("BtcUsdt-Perp").name == "btcusdt-perp.json"


# ---------------------------------------------------------------------------
# _load_cache
# ---------------------------------------------------------------------------

class TestLoadCache:
    def test_missing_file_returns_empty_list(self, tmp_cache_dir: Path):
        assert fc._load_cache("X-PERP") == []

    def test_valid_json_list_loaded(self, tmp_cache_dir: Path):
        tmp_cache_dir.mkdir()
        (tmp_cache_dir / "x-perp.json").write_text(
            json.dumps([{"funding_time_ms": 100, "funding_rate": 0.01}])
        )
        out = fc._load_cache("X-PERP")
        assert out == [{"funding_time_ms": 100, "funding_rate": 0.01}]

    def test_non_list_json_returns_empty(self, tmp_cache_dir: Path):
        tmp_cache_dir.mkdir()
        (tmp_cache_dir / "x-perp.json").write_text(json.dumps({"wrong": "shape"}))
        assert fc._load_cache("X-PERP") == []

    def test_corrupt_json_returns_empty(self, tmp_cache_dir: Path, caplog):
        tmp_cache_dir.mkdir()
        (tmp_cache_dir / "x-perp.json").write_text("{ not json")
        caplog.set_level("WARNING", logger="tinohelm.data.funding_cache")
        out = fc._load_cache("X-PERP")
        assert out == []
        assert any("Corrupt funding rate cache" in r.message for r in caplog.records)

    def test_empty_list_file_returns_empty(self, tmp_cache_dir: Path):
        tmp_cache_dir.mkdir()
        (tmp_cache_dir / "x-perp.json").write_text("[]")
        assert fc._load_cache("X-PERP") == []


# ---------------------------------------------------------------------------
# _save_cache
# ---------------------------------------------------------------------------

class TestSaveCache:
    def test_creates_dir_if_missing(self, tmp_cache_dir: Path):
        assert not tmp_cache_dir.exists()
        fc._save_cache("X-PERP", [{"funding_time_ms": 100, "rate": 0.01}])
        assert tmp_cache_dir.exists()

    def test_writes_sorted_deduped(self, tmp_cache_dir: Path):
        records = [
            {"funding_time_ms": 300, "funding_rate": 0.3},
            {"funding_time_ms": 100, "funding_rate": 0.1},
            {"funding_time_ms": 200, "funding_rate": 0.2},
            {"funding_time_ms": 100, "funding_rate": 0.11},  # dup of first ts
        ]
        fc._save_cache("X-PERP", records)
        on_disk = json.loads((tmp_cache_dir / "x-perp.json").read_text())
        assert [r["funding_time_ms"] for r in on_disk] == [100, 200, 300]
        # Later-occurring dup wins (0.11 > 0.1)
        assert on_disk[0]["funding_rate"] == 0.11

    def test_invalid_records_filtered_out(self, tmp_cache_dir: Path):
        # Corrupt entries must not crash the flush — they get dropped.
        records = [
            {"funding_time_ms": 100, "funding_rate": 0.1},
            {"garbage": True},             # no ts
            {"funding_time_ms": "bad"},    # wrong type
            {"funding_time_ms": 200, "funding_rate": 0.2},
        ]
        fc._save_cache("X-PERP", records)
        on_disk = json.loads((tmp_cache_dir / "x-perp.json").read_text())
        assert [r["funding_time_ms"] for r in on_disk] == [100, 200]

    def test_round_trip_load_save(self, tmp_cache_dir: Path):
        original = [
            {"funding_time_ms": 100, "funding_rate": 0.01, "mark_price": 50_000.0},
            {"funding_time_ms": 200, "funding_rate": 0.02, "mark_price": 51_000.0},
        ]
        fc._save_cache("X-PERP", original)
        loaded = fc._load_cache("X-PERP")
        assert loaded == original

    def test_empty_records_writes_empty_json_list(self, tmp_cache_dir: Path):
        fc._save_cache("X-PERP", [])
        assert json.loads((tmp_cache_dir / "x-perp.json").read_text()) == []


# ---------------------------------------------------------------------------
# load_funding_rates — pure-read orchestrator (no pipeline calls)
# ---------------------------------------------------------------------------

class TestLoadFundingRates:
    def test_cache_fully_covers_returns_filtered_records(
        self, tmp_cache_dir: Path,
    ):
        # Seed cache with records spanning the requested range.
        fc._save_cache("X-PERP", [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},  # 2024-01-01
            {"funding_time_ms": 1_704_672_000_000, "funding_rate": 0.02},  # 2024-01-08
            {"funding_time_ms": 1_705_276_800_000, "funding_rate": 0.03},  # 2024-01-15
        ])

        out = fc.load_funding_rates(
            "X-PERP",
            start=_utc(2024, 1, 5),
            end=_utc(2024, 1, 12),
        )

        # Only the middle record falls in [2024-01-05, 2024-01-12].
        assert [r["funding_time_ms"] for r in out] == [1_704_672_000_000]

    def test_no_cache_returns_empty_list(
        self, tmp_cache_dir: Path,
    ):
        # Pure-read: when the cache is absent the function returns empty without
        # attempting any network fetch. It is the caller's responsibility to
        # ensure the cache is populated before calling load_funding_rates.
        out = fc.load_funding_rates(
            "X-PERP",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 10),
        )
        assert out == []

    def test_filters_returned_records_to_range(
        self, tmp_cache_dir: Path,
    ):
        fc._save_cache("X-PERP", [
            {"funding_time_ms": 1_703_808_000_000, "funding_rate": 0.0},   # 2023-12-29 (before start)
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},  # 2024-01-01
            {"funding_time_ms": 1_704_672_000_000, "funding_rate": 0.02},  # 2024-01-08
            {"funding_time_ms": 1_706_486_400_000, "funding_rate": 0.99},  # 2024-01-29 (after end)
        ])

        out = fc.load_funding_rates(
            "X-PERP",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 10),
        )
        assert [r["funding_time_ms"] for r in out] == [
            1_704_067_200_000, 1_704_672_000_000,
        ]

    def test_naive_datetime_treated_as_utc(
        self, tmp_cache_dir: Path,
    ):
        # Critical: naive datetimes use UTC, not the machine's local tz.
        fc._save_cache("X-PERP", [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},
            {"funding_time_ms": 1_704_672_000_000, "funding_rate": 0.02},
        ])

        naive_out = fc.load_funding_rates(
            "X-PERP",
            start=datetime(2024, 1, 1),     # naive
            end=datetime(2024, 1, 10),      # naive
        )
        aware_out = fc.load_funding_rates(
            "X-PERP",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 10),
        )
        assert naive_out == aware_out

    def test_partial_cache_returns_available_records(
        self, tmp_cache_dir: Path,
    ):
        # Cache only covers part of the range. Pure-read returns what is there;
        # it does NOT fetch the gap. Upstream (BacktestRunner) handles fetching.
        fc._save_cache("X-PERP", [
            {"funding_time_ms": 1_704_844_800_000, "funding_rate": 0.01},  # 2024-01-10
        ])

        out = fc.load_funding_rates(
            "X-PERP",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 31),
        )
        # Only the cached record (which is within range) is returned.
        assert [r["funding_time_ms"] for r in out] == [1_704_844_800_000]
