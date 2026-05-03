"""Tests for pure helpers in tinohelm.api.routes.data.

Covers interval ⇄ NT-suffix conversion, parquet size calculation, and the
storage-file deletion helper used by DELETE /api/data/catalog/{id}.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import polars as pl
import pytest
from fastapi import HTTPException

from tinohelm.api.routes.data import (
    CompactRequest,
    DataFetchBatchRequest,
    _UNIT_MAP,
    _UNIT_REVERSE,
    _delete_storage_files,
    _fetch_batch_intervals,
    _split_fetch_date_ranges,
    _interval_to_nt,
    _nt_to_interval,
    _parquet_size_for,
    _run_compact,
    scan_data_catalog,
    trigger_compact,
    trigger_data_fetch_batch,
    validate_data,
)


class _FetchBatchDb:
    def __init__(self):
        self.jobs = []
        self._next_id = 0

    def add(self, job):
        self.jobs.append(job)

    async def flush(self):
        self._next_id += 1
        self.jobs[-1].job_id = f"job-{self._next_id}"

    async def commit(self):
        pass


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


class TestFetchBatchSplitting:
    def test_agg_trades_multi_day_range_splits_by_configured_days(self):
        from datetime import date

        ranges = _split_fetch_date_ranges(
            data_type="aggTrades",
            start=date(2024, 1, 1),
            end=date(2024, 1, 3),
            max_days_per_job=1,
        )

        assert ranges == [
            (date(2024, 1, 1), date(2024, 1, 1)),
            (date(2024, 1, 2), date(2024, 1, 2)),
            (date(2024, 1, 3), date(2024, 1, 3)),
        ]

    def test_non_agg_trades_range_is_not_split(self):
        from datetime import date

        assert _split_fetch_date_ranges(
            data_type="klines",
            start=date(2024, 1, 1),
            end=date(2024, 1, 3),
            max_days_per_job=1,
        ) == [(date(2024, 1, 1), date(2024, 1, 3))]

    def test_fetch_batch_rejects_empty_intervals_for_bar_data(self):
        body = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            intervals=[],
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
            data_type="klines",
        )

        with pytest.raises(HTTPException) as exc:
            import asyncio

            asyncio.run(trigger_data_fetch_batch(body, AsyncMock(), AsyncMock(), SimpleNamespace()))

        assert exc.value.status_code == 400
        assert exc.value.detail == "intervals must not be empty"

    def test_fetch_batch_rejects_start_after_end(self):
        body = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            intervals=["1m"],
            start=date(2024, 1, 2),
            end=date(2024, 1, 1),
        )

        with pytest.raises(HTTPException) as exc:
            import asyncio

            asyncio.run(trigger_data_fetch_batch(body, AsyncMock(), AsyncMock(), SimpleNamespace()))

        assert exc.value.status_code == 400
        assert exc.value.detail == "start must be on or before end"

    def test_fetch_batch_response_intervals_for_raw_tick_type_are_string_only(self):
        assert _fetch_batch_intervals("aggTrades", ["1m", "5m"]) == []
        assert _fetch_batch_intervals("trades", ["1m", "5m"]) == []
        assert _fetch_batch_intervals("bookTicker", ["1m", "5m"]) == []

    def test_fetch_batch_intervals_for_kline_types_fan_out(self):
        assert _fetch_batch_intervals("klines", ["1m", "5m"]) == ["1m", "5m"]
        assert _fetch_batch_intervals("markPriceKlines", ["1m", "5m"]) == ["1m", "5m"]

    def test_fetch_batch_agg_trades_default_interval_enqueues_one_intervalless_job(self, monkeypatch):
        import asyncio

        enqueue = AsyncMock()
        monkeypatch.setattr("tinohelm.api.routes.data.enqueue_job", enqueue)
        db = _FetchBatchDb()
        rds = AsyncMock()
        body = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
            data_type="aggTrades",
        )

        result = asyncio.run(trigger_data_fetch_batch(
            body,
            db,
            rds,
            SimpleNamespace(data=SimpleNamespace(agg_trades_max_days_per_job=30)),
        ))

        assert result["count"] == 1
        assert result["intervals"] == []
        assert result["jobs"] == [
            {
                "job_id": "job-1",
                "data_type": "aggTrades",
                "db_interval": "tick",
                "interval": None,
                "start": "2024-01-01",
                "end": "2024-01-01",
            }
        ]
        assert [job.interval for job in db.jobs] == [None]
        enqueue.assert_awaited_once_with(rds, "job-1")

    def test_fetch_batch_klines_fans_out_body_intervals(self, monkeypatch):
        import asyncio

        enqueue = AsyncMock()
        monkeypatch.setattr("tinohelm.api.routes.data.enqueue_job", enqueue)
        db = _FetchBatchDb()
        rds = AsyncMock()
        body = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            intervals=["1m", "5m"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
            data_type="klines",
        )

        result = asyncio.run(trigger_data_fetch_batch(
            body,
            db,
            rds,
            SimpleNamespace(data=SimpleNamespace(agg_trades_max_days_per_job=30)),
        ))

        assert result["count"] == 2
        assert result["intervals"] == ["1m", "5m"]
        assert [job.interval for job in db.jobs] == ["1m", "5m"]
        assert enqueue.await_count == 2

    def test_fetch_batch_rejects_unknown_data_type_before_jobs(self, monkeypatch):
        import asyncio

        enqueue = AsyncMock()
        monkeypatch.setattr("tinohelm.api.routes.data.enqueue_job", enqueue)
        db = _FetchBatchDb()
        body = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            intervals=["1m"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
            data_type="mystery",
        )

        with pytest.raises(HTTPException) as exc:
            asyncio.run(trigger_data_fetch_batch(
                body,
                db,
                AsyncMock(),
                SimpleNamespace(data=SimpleNamespace(agg_trades_max_days_per_job=30)),
            ))

        assert exc.value.status_code == 400
        assert "Unsupported data_type" in exc.value.detail
        assert db.jobs == []
        enqueue.assert_not_awaited()
    def test_fetch_batch_raw_tick_allows_empty_intervals(self, monkeypatch):
        import asyncio

        enqueue = AsyncMock()
        monkeypatch.setattr("tinohelm.api.routes.data.enqueue_job", enqueue)
        db = _FetchBatchDb()
        rds = AsyncMock()
        body = DataFetchBatchRequest(
            symbols=["BTCUSDT-PERP"],
            intervals=[],
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
            data_type="bookTicker",
        )

        result = asyncio.run(trigger_data_fetch_batch(
            body,
            db,
            rds,
            SimpleNamespace(data=SimpleNamespace(agg_trades_max_days_per_job=30)),
        ))

        assert result["intervals"] == []
        assert result["jobs"][0]["db_interval"] == "tick"
        assert [job.interval for job in db.jobs] == [None]
        enqueue.assert_awaited_once_with(rds, "job-1")



class TestSourceAwareBarMaintenance:
    def test_validate_data_resolves_source_aware_bar_root(self, tmp_path: Path, monkeypatch):
        import asyncio
        from tinohelm.data.catalog_helpers import resolve_catalog_path

        calls = []

        def fake_validate_bars(*, symbol, interval, catalog_path):
            calls.append((symbol, interval, catalog_path))
            return {"status": "ok"}

        monkeypatch.setattr("tinohelm.data.catalog.validate_bars", fake_validate_bars)
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = asyncio.run(validate_data(
            "BTCUSDT-PERP",
            "1m",
            data_type="markPriceKlines",
            settings=settings,
        ))

        assert result == {"status": "ok"}
        assert calls == [("BTCUSDT-PERP", "1m", str(resolve_catalog_path(tmp_path, "markPriceKlines")))]

    def test_validate_data_falls_back_to_legacy_flat_default_klines_root(self, tmp_path: Path, monkeypatch):
        import asyncio
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        legacy_dir = tmp_path / "data" / "bar" / f"{nt_sym}-1-MINUTE-LAST-EXTERNAL"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "bars.parquet").write_bytes(b"legacy")
        resolved_dir = resolve_catalog_path(tmp_path, "klines") / "data" / "bar" / f"{nt_sym}-1-MINUTE-LAST-EXTERNAL"
        resolved_dir.mkdir(parents=True)

        calls = []

        def fake_validate_bars(*, symbol, interval, catalog_path):
            calls.append((symbol, interval, catalog_path))
            return {"status": "ok"}

        monkeypatch.setattr("tinohelm.data.catalog.validate_bars", fake_validate_bars)
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = asyncio.run(validate_data(
            "BTCUSDT-PERP",
            "1m",
            data_type="klines",
            settings=settings,
        ))

        assert result == {"status": "ok"}
        assert calls == [("BTCUSDT-PERP", "1m", str(tmp_path))]

    def test_run_compact_falls_back_to_legacy_flat_default_klines_root(self, tmp_path: Path, monkeypatch):
        import asyncio
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        legacy_dir = tmp_path / "data" / "bar" / f"{nt_sym}-1-MINUTE-LAST-EXTERNAL"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "bars.parquet").write_bytes(b"legacy")
        resolved_dir = resolve_catalog_path(tmp_path, "klines") / "data" / "bar" / f"{nt_sym}-1-MINUTE-LAST-EXTERNAL"
        resolved_dir.mkdir(parents=True)

        calls = []
        statements = []

        def fake_compact_bars(*, symbol, interval, catalog_path):
            calls.append((symbol, interval, catalog_path))
            return {"bars_count": 1, "size_before": 6, "size_after": 6}

        class FakeResult:
            def scalar_one_or_none(self):
                return None

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                statements.append(stmt)
                return FakeResult()

        monkeypatch.setattr("tinohelm.data.catalog.compact_bars", fake_compact_bars)
        monkeypatch.setattr("tinohelm.db.session.get_session_factory", lambda: FakeSession)
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        asyncio.run(_run_compact("BTCUSDT-PERP", "1m", settings, "klines", "klines"))

        assert calls == [("BTCUSDT-PERP", "1m", str(tmp_path))]
        assert statements
        assert "data_catalog.source_type = :source_type_1" in str(statements[0])

    def test_trigger_compact_passes_source_aware_bar_contract(self, tmp_path: Path):
        import asyncio

        class FakeBackgroundTasks:
            def __init__(self):
                self.calls = []

            def add_task(self, fn, *args, **kwargs):
                self.calls.append((fn, args, kwargs))

        background = FakeBackgroundTasks()
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))
        body = CompactRequest(symbol="BTCUSDT-PERP", interval="1m", data_type="markPriceKlines")

        result = asyncio.run(trigger_compact(body, background, settings))

        assert result["status"] == "accepted"
        assert len(background.calls) == 1
        fn, args, kwargs = background.calls[0]
        assert fn.__name__ == "_run_compact"
        assert args == ("BTCUSDT-PERP", "1m", settings, "markPriceKlines", "markPriceKlines")
        assert kwargs == {}


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

    def test_bar_source_aware_non_default_does_not_delete_flat_legacy(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        dir_name = f"{nt_sym}-5-MINUTE-LAST-EXTERNAL"
        mark_dir = resolve_catalog_path(tmp_path, "markPriceKlines") / "data" / "bar" / dir_name
        mark_dir.mkdir(parents=True)
        mark_path = mark_dir / "mark.parquet"
        mark_path.write_bytes(b"x" * 30)
        legacy_dir = tmp_path / "data" / "bar" / dir_name
        legacy_dir.mkdir(parents=True)
        legacy_path = legacy_dir / "legacy.parquet"
        legacy_path.write_bytes(b"y" * 20)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "bar", "5m", str(tmp_path), "markPriceKlines"
        )

        assert deleted == 1
        assert freed == 30
        assert not mark_path.exists()
        assert legacy_path.exists()

    def test_unknown_non_default_source_type_does_not_delete_flat_legacy(self, tmp_path: Path):
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        dir_name = f"{nt_sym}-5-MINUTE-LAST-EXTERNAL"
        legacy_dir = tmp_path / "data" / "bar" / dir_name
        legacy_dir.mkdir(parents=True)
        legacy_path = legacy_dir / "legacy.parquet"
        legacy_path.write_bytes(b"y" * 20)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "bar", "5m", str(tmp_path), "unknownSource"
        )

        assert deleted == 0
        assert freed == 0
        assert legacy_path.exists()

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

    def test_trade_tick_deletes_source_aware_agg_trades(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        tick_dir = resolve_catalog_path(tmp_path, "aggTrades") / "data" / "trade_tick" / nt_sym
        tick_dir.mkdir(parents=True)
        parquet_path = tick_dir / "a.parquet"
        parquet_path.write_bytes(b"x" * 30)
        legacy_tick_dir = tmp_path / "data" / "trade_tick" / nt_sym
        legacy_tick_dir.mkdir(parents=True)
        legacy_parquet_path = legacy_tick_dir / "legacy.parquet"
        legacy_parquet_path.write_bytes(b"y" * 20)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "trade_tick", "tick", str(tmp_path), "aggTrades"
        )
        assert deleted == 2
        assert freed == 50
        assert not parquet_path.exists()
        assert not legacy_parquet_path.exists()

    def test_trade_tick_deletes_source_aware_trades_without_flat_legacy_fallback(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        trades_dir = resolve_catalog_path(tmp_path, "trades") / "data" / "trade_tick" / nt_sym
        trades_dir.mkdir(parents=True)
        trades_path = trades_dir / "trades.parquet"
        trades_path.write_bytes(b"x" * 30)
        legacy_tick_dir = tmp_path / "data" / "trade_tick" / nt_sym
        legacy_tick_dir.mkdir(parents=True)
        legacy_path = legacy_tick_dir / "legacy.parquet"
        legacy_path.write_bytes(b"y" * 20)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "trade_tick", "tick", str(tmp_path), "trades"
        )

        assert deleted == 1
        assert freed == 30
        assert not trades_path.exists()
        assert legacy_path.exists()

    def test_quote_tick_handled(self, tmp_path: Path):
        # No real data: exercises the branch and verifies (0, 0) default
        assert _delete_storage_files(
            "BTCUSDT-PERP", "quote_tick", "tick", str(tmp_path)
        ) == (0, 0)

    def test_quote_tick_deletes_source_aware_book_ticker(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        tick_dir = resolve_catalog_path(tmp_path, "bookTicker") / "data" / "quote_tick" / nt_sym
        tick_dir.mkdir(parents=True)
        parquet_path = tick_dir / "a.parquet"
        parquet_path.write_bytes(b"x" * 40)
        legacy_tick_dir = tmp_path / "data" / "quote_tick" / nt_sym
        legacy_tick_dir.mkdir(parents=True)
        legacy_parquet_path = legacy_tick_dir / "legacy.parquet"
        legacy_parquet_path.write_bytes(b"y" * 10)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "quote_tick", "tick", str(tmp_path), "bookTicker"
        )
        assert deleted == 2
        assert freed == 50
        assert not parquet_path.exists()
        assert not legacy_parquet_path.exists()

    def test_metrics_deletes_parquet(self, tmp_path: Path):
        from tinohelm.data.catalog import metrics_parquet_path

        parquet_path = metrics_parquet_path("BTCUSDT-PERP", tmp_path)
        parquet_path.parent.mkdir(parents=True)
        parquet_path.write_bytes(b"x" * 25)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "metrics", "5m", str(tmp_path)
        )

        assert deleted == 1
        assert freed == 25
        assert not parquet_path.exists()

    def test_order_book_delta_deletes_parquet(self, tmp_path: Path):
        from tinohelm.data.catalog import book_depth_parquet_path

        parquet_path = book_depth_parquet_path("BTCUSDT-PERP", tmp_path)
        parquet_path.parent.mkdir(parents=True)
        parquet_path.write_bytes(b"x" * 35)

        deleted, freed = _delete_storage_files(
            "BTCUSDT-PERP", "order_book_delta", "tick", str(tmp_path)
        )

        assert deleted == 1
        assert freed == 35
        assert not parquet_path.exists()

    def test_funding_rate_deletes_parquet(self, tmp_path: Path):
        from tinohelm.core.paths import paths as _paths
        from tinohelm.data.catalog import funding_rate_parquet_path

        cache_dir = tmp_path / "funding_rates"
        cache_dir.mkdir()
        _paths.override("funding_rates", cache_dir)
        parquet_path = funding_rate_parquet_path("BTCUSDT-PERP", tmp_path)
        parquet_path.parent.mkdir(parents=True)
        parquet_path.write_bytes(b"x" * 45)
        try:
            deleted, freed = _delete_storage_files(
                "BTCUSDT-PERP", "funding_rate", "8h", str(tmp_path)
            )
        finally:
            _paths.reset_overrides()

        assert deleted == 1
        assert freed == 45
        assert not parquet_path.exists()

    def test_funding_rate_deletes_parquet_and_legacy_json(self, tmp_path: Path):
        from tinohelm.core.paths import paths as _paths
        from tinohelm.data.catalog import funding_rate_parquet_path

        cache_dir = tmp_path / "funding_rates"
        cache_dir.mkdir()
        target = cache_dir / "btcusdt-perp.json"
        target.write_bytes(b'[{"funding": 0.0001}]')
        _paths.override("funding_rates", cache_dir)
        parquet_path = funding_rate_parquet_path("BTCUSDT-PERP", tmp_path)
        parquet_path.parent.mkdir(parents=True)
        parquet_path.write_bytes(b"x" * 45)
        try:
            deleted, freed = _delete_storage_files(
                "BTCUSDT-PERP", "funding_rate", "8h", str(tmp_path)
            )
        finally:
            _paths.reset_overrides()

        assert deleted == 2
        assert freed == 45 + len(b'[{"funding": 0.0001}]')
        assert not parquet_path.exists()
        assert not target.exists()

    def test_funding_rate_deletes_json(self, tmp_path: Path):
        from tinohelm.core.paths import paths as _paths

        cache_dir = tmp_path / "funding_rates"
        cache_dir.mkdir()
        target = cache_dir / "btcusdt-perp.json"
        target.write_bytes(b'[{"funding": 0.0001}]')
        _paths.override("funding_rates", cache_dir)
        try:
            deleted, freed = _delete_storage_files(
                "BTCUSDT-PERP", "funding_rate", "8h", str(tmp_path)
            )
        finally:
            _paths.reset_overrides()
        assert deleted == 1
        assert freed == len(b'[{"funding": 0.0001}]')
        assert not target.exists()

    def test_funding_rate_missing_is_noop(self, tmp_path: Path):
        from tinohelm.core.paths import paths as _paths

        cache_dir = tmp_path / "funding_rates"
        cache_dir.mkdir()
        _paths.override("funding_rates", cache_dir)
        try:
            result = _delete_storage_files(
                "BTCUSDT-PERP", "funding_rate", "8h", str(tmp_path)
            )
        finally:
            _paths.reset_overrides()
        assert result == (0, 0)

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


class TestScanDataCatalog:
    @staticmethod
    def _empty_db(added_rows: list):
        execute_result = SimpleNamespace(scalar_one_or_none=lambda: None)
        return SimpleNamespace(
            execute=AsyncMock(return_value=execute_result),
            add=MagicMock(side_effect=added_rows.append),
            commit=AsyncMock(),
        )

    async def test_scan_creates_source_aware_book_ticker_quote_tick(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.db.models import DataCatalog
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        quote_dir = resolve_catalog_path(tmp_path, "bookTicker") / "data" / "quote_tick" / nt_sym
        quote_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000]}).write_parquet(
            quote_dir / "quotes.parquet"
        )

        added_rows = []
        execute_result = SimpleNamespace(scalar_one_or_none=lambda: None)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=execute_result),
            add=MagicMock(side_effect=added_rows.append),
            commit=AsyncMock(),
        )
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = await scan_data_catalog(db=db, settings=settings)

        assert result["created"] == 1
        assert len(added_rows) == 1
        row = added_rows[0]
        assert isinstance(row, DataCatalog)
        assert row.symbol == symbol
        assert row.data_type == "quote_tick"
        assert row.interval == "tick"
        assert row.source_type == "bookTicker"
        assert row.record_count == 1
        assert row.start_date == date(2025, 1, 1)
        assert row.end_date == date(2025, 1, 1)

    async def test_scan_combines_source_aware_and_legacy_book_ticker_stats(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        source_root = resolve_catalog_path(tmp_path, "bookTicker")
        source_dir = source_root / "data" / "quote_tick" / nt_sym
        legacy_dir = tmp_path / "data" / "quote_tick" / nt_sym
        source_dir.mkdir(parents=True)
        legacy_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000, 1_735_776_000_000_000_000]}).write_parquet(
            source_dir / "source.parquet"
        )
        pl.DataFrame({"ts_event": [1_735_862_400_000_000_000, 1_735_948_800_000_000_000, 1_736_035_200_000_000_000]}).write_parquet(
            legacy_dir / "legacy.parquet"
        )
        expected_size = (source_dir / "source.parquet").stat().st_size + (legacy_dir / "legacy.parquet").stat().st_size
        added_rows = []
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = await scan_data_catalog(db=self._empty_db(added_rows), settings=settings)

        assert result["created"] == 1
        assert len(added_rows) == 1
        row = added_rows[0]
        assert row.symbol == symbol
        assert row.data_type == "quote_tick"
        assert row.interval == "tick"
        assert row.source_type == "bookTicker"
        assert row.record_count == 5
        assert row.size_bytes == expected_size
        assert row.start_date == date(2025, 1, 1)
        assert row.end_date == date(2025, 1, 5)
        assert row.file_path == str(source_root)

    async def test_scan_book_ticker_unknown_legacy_count_clears_existing_record_count(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.db.models import DataCatalog
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        source_root = resolve_catalog_path(tmp_path, "bookTicker")
        source_dir = source_root / "data" / "quote_tick" / nt_sym
        legacy_dir = tmp_path / "data" / "quote_tick" / nt_sym
        source_dir.mkdir(parents=True)
        legacy_dir.mkdir(parents=True)
        source_path = source_dir / "source.parquet"
        legacy_path = legacy_dir / "legacy.parquet"
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000, 1_735_776_000_000_000_000]}).write_parquet(
            source_path
        )
        legacy_path.write_bytes(b"not parquet")
        legacy_mtime = datetime(2025, 1, 5, tzinfo=timezone.utc).timestamp()
        legacy_path.touch()
        import os

        os.utime(legacy_path, (legacy_mtime, legacy_mtime))
        expected_size = source_path.stat().st_size + legacy_path.stat().st_size
        existing_row = DataCatalog(
            symbol=symbol,
            data_type="quote_tick",
            interval="tick",
            start_date=date(2025, 1, 2),
            end_date=date(2025, 1, 2),
            file_path=str(tmp_path),
            size_bytes=1,
            record_count=99,
            source_type="bookTicker",
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: existing_row)),
            add=MagicMock(),
            commit=AsyncMock(),
        )
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = await scan_data_catalog(db=db, settings=settings)

        assert result["updated"] == 1
        db.add.assert_not_called()
        assert existing_row.record_count is None
        assert existing_row.size_bytes == expected_size
        assert existing_row.start_date == date(2025, 1, 1)
        assert existing_row.end_date == date(2025, 1, 5)
        assert existing_row.file_path == str(source_root)
        assert existing_row.source_type == "bookTicker"

    async def test_scan_combines_source_aware_and_legacy_agg_trades_stats(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        source_root = resolve_catalog_path(tmp_path, "aggTrades")
        source_dir = source_root / "data" / "trade_tick" / nt_sym
        legacy_dir = tmp_path / "data" / "trade_tick" / nt_sym
        source_dir.mkdir(parents=True)
        legacy_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000, 1_735_776_000_000_000_000]}).write_parquet(
            source_dir / "source.parquet"
        )
        pl.DataFrame({"ts_event": [1_735_862_400_000_000_000, 1_735_948_800_000_000_000, 1_736_035_200_000_000_000]}).write_parquet(
            legacy_dir / "legacy.parquet"
        )
        expected_size = (source_dir / "source.parquet").stat().st_size + (legacy_dir / "legacy.parquet").stat().st_size
        added_rows = []
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = await scan_data_catalog(db=self._empty_db(added_rows), settings=settings)

        assert result["created"] == 1
        assert len(added_rows) == 1
        row = added_rows[0]
        assert row.symbol == symbol
        assert row.data_type == "trade_tick"
        assert row.interval == "tick"
        assert row.source_type == "aggTrades"
        assert row.record_count == 5
        assert row.size_bytes == expected_size
        assert row.start_date == date(2025, 1, 1)
        assert row.end_date == date(2025, 1, 5)
        assert row.file_path == str(source_root)

    async def test_scan_keeps_source_aware_trades_separate_from_legacy_agg_trades(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        trades_root = resolve_catalog_path(tmp_path, "trades")
        trades_dir = trades_root / "data" / "trade_tick" / nt_sym
        legacy_dir = tmp_path / "data" / "trade_tick" / nt_sym
        trades_dir.mkdir(parents=True)
        legacy_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000, 1_735_776_000_000_000_000]}).write_parquet(
            trades_dir / "trades.parquet"
        )
        pl.DataFrame({"ts_event": [1_735_862_400_000_000_000, 1_735_948_800_000_000_000, 1_736_035_200_000_000_000]}).write_parquet(
            legacy_dir / "legacy.parquet"
        )
        added_rows = []
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = await scan_data_catalog(db=self._empty_db(added_rows), settings=settings)

        assert result["created"] == 2
        rows = {(row.source_type, row.file_path): row for row in added_rows}
        trades_row = rows[("trades", str(trades_root))]
        agg_row = rows[("aggTrades", str(tmp_path))]
        assert trades_row.record_count == 2
        assert agg_row.record_count == 3

    async def test_scan_non_default_tick_does_not_adopt_null_default_row(self, tmp_path: Path):
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.db.models import DataCatalog
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        trades_root = resolve_catalog_path(tmp_path, "trades")
        trades_dir = trades_root / "data" / "trade_tick" / nt_sym
        trades_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000]}).write_parquet(trades_dir / "trades.parquet")
        legacy_row = DataCatalog(
            symbol=symbol,
            data_type="trade_tick",
            interval="tick",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 1),
            file_path=str(tmp_path),
            size_bytes=1,
            record_count=1,
            source_type=None,
        )
        added_rows = []

        async def execute(stmt):
            text = str(stmt)
            if "source_type IS NULL" in text:
                return SimpleNamespace(scalar_one_or_none=lambda: legacy_row)
            return SimpleNamespace(scalar_one_or_none=lambda: None)

        db = SimpleNamespace(execute=AsyncMock(side_effect=execute), add=MagicMock(side_effect=added_rows.append), commit=AsyncMock())
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=tmp_path))

        result = await scan_data_catalog(db=db, settings=settings)

        assert result["created"] == 1
        assert result["updated"] == 0
        assert legacy_row.source_type is None
        assert added_rows[0].source_type == "trades"
