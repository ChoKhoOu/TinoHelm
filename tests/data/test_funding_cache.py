"""Tests for tinohelm.data.funding_cache — the I/O layer on top of helpers.

The decision / normalisation logic lives in ``funding_cache_helpers`` and is
tested there. Here we exercise the filesystem bridge (``_load_cache``,
``_save_cache``) and the ``load_funding_rates`` orchestrator with the
pipeline stubbed out.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import tinohelm.data.funding_cache as fc


@pytest.fixture
def tmp_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the module-level cache dir to a tmp_path sandbox."""
    d = tmp_path / "funding_rates"
    monkeypatch.setattr(fc, "_CACHE_DIR", d)
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
# load_funding_rates — orchestrator w/ pipeline stubbed out
# ---------------------------------------------------------------------------

class _FakeIngestResult:
    def __init__(self, objects_count: int):
        self.objects_count = objects_count


class _FakePipeline:
    """Captures ingest_sync calls; writes nothing. Test decides objects_count."""

    def __init__(self, *, objects_count: int = 0, side_effect_cache=None, symbol: str = ""):
        self._objects_count = objects_count
        self._side_effect_cache = side_effect_cache
        self._symbol = symbol
        self.calls: list[dict] = []

    def ingest_sync(self, *, symbol, data_type, start, end):
        self.calls.append({
            "symbol": symbol, "data_type": data_type,
            "start": start, "end": end,
        })
        # Simulate pipeline writing to the cache (via _save_cache).
        if self._side_effect_cache is not None:
            fc._save_cache(symbol, self._side_effect_cache)
        return _FakeIngestResult(self._objects_count)


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, fake: _FakePipeline) -> None:
    """Wire both BinanceVisionPipeline and get_settings into fakes."""
    import tinohelm.data.pipeline as pipeline_mod
    import tinohelm.core.config as cfg_mod

    monkeypatch.setattr(
        pipeline_mod, "BinanceVisionPipeline",
        lambda *_args, **_kw: fake,
    )

    fake_settings = MagicMock()
    fake_settings.paths.catalog = "/tmp/unused"
    monkeypatch.setattr(cfg_mod, "get_settings", lambda: fake_settings)


class TestLoadFundingRates:
    def test_cache_fully_covers_no_pipeline_call(
        self, tmp_cache_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        # Seed cache with records spanning the requested range.
        fc._save_cache("X-PERP", [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},  # 2024-01-01
            {"funding_time_ms": 1_704_672_000_000, "funding_rate": 0.02},  # 2024-01-08
            {"funding_time_ms": 1_705_276_800_000, "funding_rate": 0.03},  # 2024-01-15
        ])
        fake = _FakePipeline(objects_count=0)
        _patch_pipeline(monkeypatch, fake)

        out = fc.load_funding_rates(
            "X-PERP",
            start=_utc(2024, 1, 5),
            end=_utc(2024, 1, 12),
        )

        assert fake.calls == []  # pipeline never invoked
        # Only the middle record falls in [2024-01-05, 2024-01-12].
        assert [r["funding_time_ms"] for r in out] == [1_704_672_000_000]

    def test_no_cache_triggers_full_fetch(
        self, tmp_cache_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        fake = _FakePipeline(
            objects_count=1,
            side_effect_cache=[
                {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},
            ],
        )
        _patch_pipeline(monkeypatch, fake)

        out = fc.load_funding_rates(
            "X-PERP",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 10),
        )

        assert len(fake.calls) == 1
        # start date == 2024-01-01
        assert fake.calls[0]["start"].isoformat() == "2024-01-01"
        assert fake.calls[0]["data_type"] == "fundingRate"
        assert len(out) == 1

    def test_filters_returned_records_to_range(
        self, tmp_cache_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        fc._save_cache("X-PERP", [
            {"funding_time_ms": 1_703_808_000_000, "funding_rate": 0.0},   # 2023-12-29 (before start)
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},  # 2024-01-01
            {"funding_time_ms": 1_704_672_000_000, "funding_rate": 0.02},  # 2024-01-08
            {"funding_time_ms": 1_706_486_400_000, "funding_rate": 0.99},  # 2024-01-29 (after end)
        ])
        fake = _FakePipeline(objects_count=0)
        _patch_pipeline(monkeypatch, fake)

        out = fc.load_funding_rates(
            "X-PERP",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 10),
        )
        assert [r["funding_time_ms"] for r in out] == [
            1_704_067_200_000, 1_704_672_000_000,
        ]

    def test_naive_datetime_treated_as_utc(
        self, tmp_cache_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        # Critical: naive datetimes use UTC, not the machine's local tz.
        fc._save_cache("X-PERP", [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},
            {"funding_time_ms": 1_704_672_000_000, "funding_rate": 0.02},
        ])
        _patch_pipeline(monkeypatch, _FakePipeline(objects_count=0))

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

    def test_pipeline_failure_falls_back_to_cache(
        self, tmp_cache_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog,
    ):
        fc._save_cache("X-PERP", [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},
        ])

        class _BoomPipeline:
            def ingest_sync(self, **_kw):
                raise RuntimeError("binance down")

        import tinohelm.data.pipeline as pipeline_mod
        import tinohelm.core.config as cfg_mod
        monkeypatch.setattr(pipeline_mod, "BinanceVisionPipeline", lambda *a, **kw: _BoomPipeline())
        monkeypatch.setattr(cfg_mod, "get_settings", lambda: MagicMock(paths=MagicMock(catalog="/tmp")))

        caplog.set_level("WARNING", logger="tinohelm.data.funding_cache")
        # Extend range beyond cache to force a fetch attempt.
        out = fc.load_funding_rates(
            "X-PERP",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 2, 1),
        )
        # We still return the cached record — the pipeline failure is warned, not raised.
        assert len(out) == 1
        assert any("Failed to fetch funding rates" in r.message for r in caplog.records)

    def test_empty_result_with_empty_cache_warns(
        self, tmp_cache_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog,
    ):
        # Pipeline ingests 0 objects AND cache was empty → log warning about
        # no data available. This is the "Binance returned nothing" case.
        fake = _FakePipeline(objects_count=0)
        _patch_pipeline(monkeypatch, fake)

        caplog.set_level("WARNING", logger="tinohelm.data.funding_cache")
        out = fc.load_funding_rates(
            "X-PERP",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 10),
        )
        assert out == []
        assert any("No funding rate data available" in r.message for r in caplog.records)

    def test_incremental_tail_fetch_start_is_after_last_cached_ms(
        self, tmp_cache_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        latest_ms = 1_704_067_200_000  # 2024-01-01
        fc._save_cache("X-PERP", [{"funding_time_ms": latest_ms, "funding_rate": 0.01}])
        fake = _FakePipeline(objects_count=0)
        _patch_pipeline(monkeypatch, fake)

        fc.load_funding_rates(
            "X-PERP",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 31),
        )
        # Tail-fetch path → start is latest+1 ms, which on date() resolves to
        # 2024-01-01 (same day is fine; Binance pipeline dedupes by ts).
        assert len(fake.calls) == 1
        assert fake.calls[0]["start"].isoformat() == "2024-01-01"

    def test_refetch_older_when_cache_missing_start(
        self, tmp_cache_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        # Cache only has data >= 2024-01-10; user asks for range starting
        # 2024-01-01 → trigger a full re-fetch from start.
        fc._save_cache("X-PERP", [
            {"funding_time_ms": 1_704_844_800_000, "funding_rate": 0.01},  # 2024-01-10
        ])
        fake = _FakePipeline(objects_count=0)
        _patch_pipeline(monkeypatch, fake)

        fc.load_funding_rates(
            "X-PERP",
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 5),
        )
        assert len(fake.calls) == 1
        assert fake.calls[0]["start"].isoformat() == "2024-01-01"
