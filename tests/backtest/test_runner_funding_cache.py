"""Tests for BacktestRunner funding-rate cache short-circuit.

A second identical backtest must not enqueue a DataFetchJob for a symbol
whose local funding-rate JSON cache already spans ``[self.start, self.end]``.
This protects against the root cause of the "repeated downloads on
every rerun" bug.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

import tinohelm.data.funding_cache as fc
from tinohelm.backtest.runner import BacktestRunner


def _utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


@pytest.fixture
def tmp_cache_dir(tmp_path, paths_override):
    d = tmp_path / "funding_rates"
    paths_override("funding_rates", d)
    return d


def _mk_runner(symbols, start, end) -> BacktestRunner:
    runner = BacktestRunner(
        strategy_path="x:X",
        config_path="x:XConfig",
        symbols=symbols,
        intervals=["1m"],
        start=start,
        end=end,
    )
    # Install a fake sync-Redis client — its presence is what causes the
    # pre-check branch to run. The method must NOT call lpush when the
    # cache fully covers the range.
    fake_redis = MagicMock()
    runner._redis_client = fake_redis
    return runner


class TestLoadFundingRatesCacheShortCircuit:
    def test_covered_cache_skips_submit(
        self, tmp_cache_dir, monkeypatch,
    ):
        # Seed a cache spanning 2024-01-01 → 2024-02-01 for BTC.
        fc._save_cache("BTCUSDT-PERP", [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},  # 2024-01-01
            {"funding_time_ms": 1_705_276_800_000, "funding_rate": 0.02},  # 2024-01-15
            {"funding_time_ms": 1_706_745_600_000, "funding_rate": 0.03},  # 2024-02-01
        ])

        runner = _mk_runner(
            ["BTCUSDT-PERP"],
            start=_utc(2024, 1, 5),
            end=_utc(2024, 1, 20),
        )

        # Spy on _submit_and_wait_fetch — it must never be awaited.
        submit_calls: list[dict] = []

        async def _spy(sym, ivl=None, *, data_type=None, start_override=None):
            submit_calls.append({
                "sym": sym, "ivl": ivl, "data_type": data_type,
                "start_override": start_override,
            })
            return True

        monkeypatch.setattr(runner, "_submit_and_wait_fetch", _spy)

        # Stub out fetch_funding_info (would otherwise hit Binance REST).
        import tinohelm.data.instruments as instr_mod
        monkeypatch.setattr(
            instr_mod, "fetch_funding_info", lambda: {"BTCUSDT": 8},
        )

        asyncio.run(runner._load_funding_rates(["BTCUSDT-PERP.BINANCE"]))

        # Cache fully covers [2024-01-05, 2024-01-20] → no job enqueued.
        assert submit_calls == []

    def test_gap_in_cache_enqueues_narrow_job(
        self, tmp_cache_dir, monkeypatch,
    ):
        # Cache only has 2024-01-01 and 2024-01-10. Request [2024-01-01, 2024-01-31].
        # → tail gap → fetch should start just AFTER the last cached record.
        fc._save_cache("BTCUSDT-PERP", [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},  # 2024-01-01
            {"funding_time_ms": 1_704_844_800_000, "funding_rate": 0.02},  # 2024-01-10
        ])

        runner = _mk_runner(
            ["BTCUSDT-PERP"],
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 31),
        )

        captured: list[dict] = []

        async def _spy(sym, ivl=None, *, data_type=None, start_override=None):
            captured.append({
                "sym": sym, "ivl": ivl, "data_type": data_type,
                "start_override": start_override,
            })
            return True

        monkeypatch.setattr(runner, "_submit_and_wait_fetch", _spy)

        import tinohelm.data.instruments as instr_mod
        monkeypatch.setattr(
            instr_mod, "fetch_funding_info", lambda: {"BTCUSDT": 8},
        )

        asyncio.run(runner._load_funding_rates(["BTCUSDT-PERP.BINANCE"]))

        # Job is created for the tail gap only.
        assert len(captured) == 1
        call = captured[0]
        assert call["sym"] == "BTCUSDT-PERP"
        assert call["data_type"] == "fundingRate"
        # start_override must be strictly after the latest cached ts (2024-01-10).
        assert call["start_override"] is not None
        assert call["start_override"] > _utc(2024, 1, 10)
        # And must be well before the requested end.
        assert call["start_override"] < _utc(2024, 1, 31)

    def test_empty_cache_enqueues_full_range(
        self, tmp_cache_dir, monkeypatch,
    ):
        # No cache file at all → first run must fetch the full window.
        runner = _mk_runner(
            ["BTCUSDT-PERP"],
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 31),
        )

        captured: list[dict] = []

        async def _spy(sym, ivl=None, *, data_type=None, start_override=None):
            captured.append({"start_override": start_override})
            return True

        monkeypatch.setattr(runner, "_submit_and_wait_fetch", _spy)

        import tinohelm.data.instruments as instr_mod
        monkeypatch.setattr(
            instr_mod, "fetch_funding_info", lambda: {"BTCUSDT": 8},
        )

        asyncio.run(runner._load_funding_rates(["BTCUSDT-PERP.BINANCE"]))

        assert len(captured) == 1
        # With no cache, compute_fetch_start returns ``start`` itself.
        assert captured[0]["start_override"] == _utc(2024, 1, 1)

    def test_mixed_symbols_partial_coverage(
        self, tmp_cache_dir, monkeypatch,
    ):
        # BTC covered, ETH has no cache → only ETH should enqueue.
        fc._save_cache("BTCUSDT-PERP", [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},
            {"funding_time_ms": 1_705_276_800_000, "funding_rate": 0.02},
            {"funding_time_ms": 1_706_745_600_000, "funding_rate": 0.03},
        ])

        runner = _mk_runner(
            ["BTCUSDT-PERP", "ETHUSDT-PERP"],
            start=_utc(2024, 1, 5),
            end=_utc(2024, 1, 20),
        )

        captured: list[str] = []

        async def _spy(sym, ivl=None, *, data_type=None, start_override=None):
            captured.append(sym)
            return True

        monkeypatch.setattr(runner, "_submit_and_wait_fetch", _spy)

        import tinohelm.data.instruments as instr_mod
        monkeypatch.setattr(
            instr_mod, "fetch_funding_info", lambda: {"BTCUSDT": 8, "ETHUSDT": 8},
        )

        asyncio.run(runner._load_funding_rates(
            ["BTCUSDT-PERP.BINANCE", "ETHUSDT-PERP.BINANCE"]
        ))

        # Only ETH should trigger a fetch job.
        assert captured == ["ETHUSDT-PERP"]


class TestLoadOrFetchAuxUpdates:
    """Mark/Index aux data must prefer Parquet catalog over REST."""

    def test_cache_hit_skips_fetch(self, monkeypatch):
        runner = _mk_runner(
            ["BTCUSDT-PERP"],
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 5),
        )

        # Stub bars returned by the catalog.
        fake_bars = [MagicMock(), MagicMock()]
        monkeypatch.setattr(
            runner, "_try_load_bars",
            lambda bar_type_str, source_type=None: fake_bars,
        )

        submit_calls = []

        async def _spy(sym, ivl=None, *, data_type=None, start_override=None):
            submit_calls.append(data_type)
            return True

        monkeypatch.setattr(runner, "_submit_and_wait_fetch", _spy)

        # build_fn shouldn't call any NT code in the test — just return a marker.
        out = asyncio.run(runner._load_or_fetch_aux_updates(
            "BTCUSDT-PERP", "BTCUSDT-PERP.BINANCE", "1m",
            source_type="markPriceKlines",
            build_fn=lambda bars, sym: list(bars),
        ))

        assert out == fake_bars
        # Cache hit → no fetch job submitted.
        assert submit_calls == []

    def test_cache_miss_enqueues_fetch(self, monkeypatch):
        runner = _mk_runner(
            ["BTCUSDT-PERP"],
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 5),
        )

        # First call returns None (miss); after the fetch, return fake bars.
        calls = {"n": 0}
        fake_bars = [MagicMock()]

        def _load(bar_type_str, source_type=None):
            calls["n"] += 1
            return None if calls["n"] == 1 else fake_bars

        monkeypatch.setattr(runner, "_try_load_bars", _load)

        submit_data_types = []

        async def _spy(sym, ivl=None, *, data_type=None, start_override=None):
            submit_data_types.append(data_type)
            return True

        monkeypatch.setattr(runner, "_submit_and_wait_fetch", _spy)

        out = asyncio.run(runner._load_or_fetch_aux_updates(
            "BTCUSDT-PERP", "BTCUSDT-PERP.BINANCE", "1m",
            source_type="indexPriceKlines",
            build_fn=lambda bars, sym: list(bars),
        ))

        assert out == fake_bars
        # Miss → one fetch job for the correct data_type.
        assert submit_data_types == ["indexPriceKlines"]
