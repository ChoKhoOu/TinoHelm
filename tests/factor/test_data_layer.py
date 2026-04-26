"""Unit tests for ``tinohelm.factor.data_layer.DataLayer``.

Coverage
--------
- bar close field loaded for 2 symbols → Panel with correct shape and values
- funding_rate JSON loaded, shifted by 1 period, forward-filled onto bar index
- PIT filtering: symbol in new-coin isolation period has NaN in Panel
- Parallel multi-symbol load (ThreadPoolExecutor) completes without error
- Missing catalog data returns empty Series (no exception)
- load() groups DataRequests by field_name → dict with one key per field
- load_aligned() convenience function aligns funding onto bar index

Polars contract
---------------
``DataLayer.load`` returns ``dict[str, polars.DataFrame]`` panels with the
canonical layout ``[ts, sym1, sym2, ...]`` (Datetime[ns] + Float64 columns).
Internal Series helpers (``_load_funding_rate`` etc.) return 2-column
``[ts, value]`` polars frames.  These tests exercise that contract — the
pre-polars pandas API has been retired.
"""
from __future__ import annotations

import csv
import json
import math
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from tinohelm.factor.data_layer import DataLayer, _parse_ts, load_aligned
from tinohelm.factor.types import DataRequest
from tinohelm.factor.universe import Universe


# ---------------------------------------------------------------------------
# NT imports (lazy — only imported in fixtures that need them)
# ---------------------------------------------------------------------------

def _make_bar_objects(symbol_str: str, timestamps_ns: list[int], closes: list[float]):
    """Create minimal NT Bar objects for writing to a test catalog.

    Uses NT model directly (no tinohelm.data.catalog helpers, so tests don't
    depend on the Binance API cache).
    """
    from nautilus_trader.model.data import Bar, BarType, BarSpecification
    from nautilus_trader.model.enums import AggregationSource, BarAggregation, PriceType
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.objects import Price, Quantity

    inst_id = InstrumentId(Symbol(symbol_str), Venue("BINANCE"))
    bar_type = BarType(
        instrument_id=inst_id,
        bar_spec=BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST),
        aggregation_source=AggregationSource.EXTERNAL,
    )
    bars = []
    for ts_ns, close in zip(timestamps_ns, closes):
        bars.append(Bar(
            bar_type=bar_type,
            open=Price.from_str(f"{close - 10:.1f}"),
            high=Price.from_str(f"{close + 20:.1f}"),
            low=Price.from_str(f"{close - 20:.1f}"),
            close=Price.from_str(f"{close:.1f}"),
            volume=Quantity.from_str("5.0"),
            ts_event=ts_ns,
            ts_init=ts_ns,
        ))
    return bars, bar_type


def _write_catalog_bars(catalog_path: Path, symbol_str: str, timestamps_ns: list[int], closes: list[float]):
    """Write NT Bar objects to a ParquetDataCatalog."""
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    bars, _ = _make_bar_objects(symbol_str, timestamps_ns, closes)
    cat = ParquetDataCatalog(str(catalog_path))
    cat.write_data(bars)


def _make_universe_csv(tmp_path: Path, rows: list[dict]) -> Path:
    """Write a universe CSV and return its path."""
    path = tmp_path / "test_uni.csv"
    if rows:
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    return path


def _make_funding_json(funding_dir: Path, symbol: str, records: list[dict]) -> Path:
    """Write a funding-rate JSON file and return its path."""
    funding_dir.mkdir(parents=True, exist_ok=True)
    path = funding_dir / f"{symbol.lower()}.json"
    with open(path, "w") as fh:
        json.dump(records, fh)
    return path


def _stub_make_instrument(monkeypatch):
    """Monkeypatch tinohelm.data.catalog._make_instrument to avoid Binance API calls."""
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue

    class _FakeInstrument:
        def __init__(self, symbol_str: str):
            self.id = InstrumentId(Symbol(symbol_str), Venue("BINANCE"))

    monkeypatch.setattr(
        "tinohelm.data.catalog._make_instrument",
        lambda symbol: _FakeInstrument(symbol),
    )


def _ts_list(panel: pl.DataFrame) -> list[datetime]:
    """Return the panel's ``ts`` column as a list of ``datetime``."""
    return panel["ts"].to_list()


def _sym_values(panel: pl.DataFrame, symbol: str) -> list[float | None]:
    """Return panel[symbol] as a Python list (None for nulls)."""
    return panel[symbol].to_list()


# ---------------------------------------------------------------------------
# Base timestamps (fixed reference: 2021-05-03 00:00:00 UTC in nanoseconds)
# ---------------------------------------------------------------------------

_T0_NS = 1_620_000_000_000_000_000  # 2021-05-03T00:00:00Z in nanoseconds
_1MIN_NS = 60 * 1_000_000_000       # 1 minute in nanoseconds


# ---------------------------------------------------------------------------
# Tests: bar close loading
# ---------------------------------------------------------------------------

class TestBarClose:
    """DataLayer loads bar 'close' field into a correctly shaped Panel."""

    def test_single_symbol_close(self, tmp_path: Path, monkeypatch):
        _stub_make_instrument(monkeypatch)

        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(5)]
        closes = [100.0, 101.0, 102.0, 101.5, 103.0]
        _write_catalog_bars(catalog_path, "BTCUSDT-PERP", ts_ns, closes)

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        dl = DataLayer(uni, catalog_root=catalog_path)

        req = DataRequest(
            symbol="BTCUSDT-PERP",
            field_name="close",
            frequency="1m",
            lookback=0,
            source="bar",
        )
        panels = dl.load(req)

        assert "close" in panels
        panel = panels["close"]
        assert isinstance(panel, pl.DataFrame)
        assert "ts" in panel.columns
        assert "BTCUSDT-PERP" in panel.columns
        assert panel.height == 5
        np.testing.assert_allclose(
            panel["BTCUSDT-PERP"].to_numpy(), closes, rtol=1e-4
        )

    def test_two_symbols_close_shape(self, tmp_path: Path, monkeypatch):
        _stub_make_instrument(monkeypatch)

        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(4)]
        _write_catalog_bars(catalog_path, "BTCUSDT-PERP", ts_ns, [100.0, 101.0, 102.0, 103.0])
        _write_catalog_bars(catalog_path, "ETHUSDT-PERP", ts_ns, [3000.0, 3010.0, 3020.0, 3030.0])

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
            {"symbol": "ETHUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        dl = DataLayer(uni, catalog_root=catalog_path)

        reqs = [
            DataRequest("BTCUSDT-PERP", "close", "1m", 0, "bar"),
            DataRequest("ETHUSDT-PERP", "close", "1m", 0, "bar"),
        ]
        panels = dl.load(reqs)

        assert "close" in panels
        panel = panels["close"]
        non_ts_cols = {c for c in panel.columns if c != "ts"}
        assert non_ts_cols == {"BTCUSDT-PERP", "ETHUSDT-PERP"}
        assert panel.height >= 4


# ---------------------------------------------------------------------------
# Tests: funding_rate loading and as-of alignment
# ---------------------------------------------------------------------------

class TestFundingRate:
    """Funding rate is loaded from JSON, shifted by 1 period, and ffilled."""

    def _base_setup(self, tmp_path: Path):
        """Set up a 2-symbol catalog + universe + funding JSON."""
        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()
        funding_dir = tmp_path / "funding_rates"
        funding_dir.mkdir()

        # 12 1-minute bars starting at T0
        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(12)]
        closes = [float(100 + i) for i in range(12)]
        _write_catalog_bars(catalog_path, "BTCUSDT-PERP", ts_ns, closes)

        # funding_rate at T0 and T0 + 8h (two 8h periods)
        _8h_ms = 8 * 3600 * 1000
        t0_ms = _T0_NS // 1_000_000  # convert ns → ms
        funding_records = [
            {"funding_time_ms": t0_ms,          "funding_rate": 0.0001},
            {"funding_time_ms": t0_ms + _8h_ms, "funding_rate": 0.0002},
        ]
        _make_funding_json(funding_dir, "BTCUSDT-PERP", funding_records)

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        return catalog_path, funding_dir, uni, ts_ns

    def test_load_funding_json_returns_frame(self, tmp_path: Path, monkeypatch):
        _stub_make_instrument(monkeypatch)
        catalog_path, funding_dir, uni, _ = self._base_setup(tmp_path)
        dl = DataLayer(uni, catalog_root=catalog_path, funding_dir=funding_dir)

        frame = dl._load_funding_rate("BTCUSDT-PERP", start=None, end=None)
        assert isinstance(frame, pl.DataFrame)
        assert frame.columns == ["ts", "value"]
        assert frame.height == 2
        assert frame["value"].to_list()[0] == pytest.approx(0.0001)

    def test_funding_aligned_onto_bar_index_as_of_delay(self, tmp_path: Path, monkeypatch):
        """The first bar (at T0) should NOT see the T0 funding rate (as-of delay).

        The rate published at T0=00:00 only becomes visible at T0+8h (next period).
        So bars at T0 through T0+8h-1min should show the *previous* (shifted) value,
        which is null for the very first period.
        """
        _stub_make_instrument(monkeypatch)
        catalog_path, funding_dir, uni, ts_ns = self._base_setup(tmp_path)
        dl = DataLayer(uni, catalog_root=catalog_path, funding_dir=funding_dir)

        _8h_ms = 8 * 3600 * 1000
        t0_ms = _T0_NS // 1_000_000

        funding_ts = [
            datetime(1970, 1, 1) + timedelta(milliseconds=t0_ms),
            datetime(1970, 1, 1) + timedelta(milliseconds=t0_ms + _8h_ms),
        ]
        funding_series = pl.DataFrame(
            {"ts": funding_ts, "value": [0.0001, 0.0002]},
            schema={"ts": pl.Datetime("ns"), "value": pl.Float64},
        )

        bar_ts = [datetime(1970, 1, 1) + timedelta(microseconds=ts // 1_000) for ts in ts_ns]
        aligned = dl._align_funding_onto_bar_index(funding_series, bar_ts)

        # After shift(1), the T0 rate becomes visible only at T0+8h.
        # Bars before the T0+8h funding rate => null.
        first_value = aligned["value"].to_list()[0]
        assert first_value is None, (
            f"First bar at T0 should be null (rate not yet visible), got {first_value}"
        )

    def test_load_aligned_includes_funding_key(self, tmp_path: Path, monkeypatch):
        """load_aligned() should return a 'funding_rate' key aligned to bar index."""
        _stub_make_instrument(monkeypatch)
        catalog_path, funding_dir, uni, ts_ns = self._base_setup(tmp_path)
        dl = DataLayer(uni, catalog_root=catalog_path, funding_dir=funding_dir)

        bar_reqs = [DataRequest("BTCUSDT-PERP", "close", "1m", 0, "bar")]
        funding_reqs = [DataRequest("BTCUSDT-PERP", "funding_rate", "8h", 0, "funding_rate")]

        result = load_aligned(dl, bar_reqs, funding_reqs)

        assert "close" in result
        assert "funding_rate" in result
        assert isinstance(result["funding_rate"], pl.DataFrame)
        assert "BTCUSDT-PERP" in result["funding_rate"].columns
        # funding panel shape must match bar index length
        assert result["funding_rate"].height == result["close"].height

    def test_missing_funding_json_returns_empty_frame(self, tmp_path: Path, monkeypatch):
        _stub_make_instrument(monkeypatch)
        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()
        funding_dir = tmp_path / "no_funding"
        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        dl = DataLayer(uni, catalog_root=catalog_path, funding_dir=funding_dir)

        frame = dl._load_funding_rate("BTCUSDT-PERP", start=None, end=None)
        assert isinstance(frame, pl.DataFrame)
        assert frame.is_empty()


# ---------------------------------------------------------------------------
# Tests: PIT filtering (new-coin isolation)
# ---------------------------------------------------------------------------

class TestPITFiltering:
    """Symbols in new-coin isolation period are NaN, not removed."""

    def test_new_coin_cells_are_nan(self, tmp_path: Path, monkeypatch):
        """Symbol listed 6 days ago (inside isolation) → all cells null."""
        _stub_make_instrument(monkeypatch)

        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        # Reference time: 2021-05-03T00:00:00 UTC (_T0_NS)
        # listing_date for ETH: 2021-04-28 (5 days before T0 → still in 7-day isolation)
        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(3)]
        _write_catalog_bars(catalog_path, "BTCUSDT-PERP", ts_ns, [100.0, 101.0, 102.0])
        _write_catalog_bars(catalog_path, "ETHUSDT-PERP", ts_ns, [3000.0, 3010.0, 3020.0])

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
            # ETH listed 2021-04-28 — still in isolation at 2021-05-03 (only 5 days)
            {"symbol": "ETHUSDT-PERP", "listing_date": "2021-04-28", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        dl = DataLayer(uni, catalog_root=catalog_path)

        reqs = [
            DataRequest("BTCUSDT-PERP", "close", "1m", 0, "bar"),
            DataRequest("ETHUSDT-PERP", "close", "1m", 0, "bar"),
        ]
        panels = dl.load(reqs)

        panel = panels["close"]
        # ETH in isolation → all values are null
        eth_nulls = panel["ETHUSDT-PERP"].null_count()
        assert eth_nulls == panel.height, (
            "ETH in new-coin isolation should be all null"
        )
        # BTC outside isolation → no null
        btc_nulls = panel["BTCUSDT-PERP"].null_count()
        assert btc_nulls == 0, "BTC outside isolation should not have nulls"

    def test_symbol_visible_after_isolation_clears(self, tmp_path: Path, monkeypatch):
        """Symbol listed exactly 7 days before first bar → not null."""
        _stub_make_instrument(monkeypatch)

        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        # T0 = 2021-05-03T00:00:00 UTC
        # ETH listed 2021-04-26 → exactly 7 days before T0 → isolation clears at T0
        ts_ns = [_T0_NS]
        _write_catalog_bars(catalog_path, "ETHUSDT-PERP", ts_ns, [3000.0])

        uni_path = _make_universe_csv(tmp_path, [
            # Listing date: 7 days exactly before T0
            {"symbol": "ETHUSDT-PERP", "listing_date": "2021-04-26", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        dl = DataLayer(uni, catalog_root=catalog_path)

        reqs = [DataRequest("ETHUSDT-PERP", "close", "1m", 0, "bar")]
        panels = dl.load(reqs)

        panel = panels["close"]
        # Isolation clears exactly at listing_date + 7d; value must be visible
        assert panel["ETHUSDT-PERP"].null_count() == 0, (
            "ETH isolation should have cleared at T0 (7 days after listing)"
        )

    def test_pit_preserves_panel_shape(self, tmp_path: Path, monkeypatch):
        """PIT filtering sets cells to null but does NOT drop rows or columns."""
        _stub_make_instrument(monkeypatch)

        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        n_bars = 6
        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(n_bars)]
        _write_catalog_bars(catalog_path, "BTCUSDT-PERP", ts_ns, [100.0 + i for i in range(n_bars)])
        _write_catalog_bars(catalog_path, "ETHUSDT-PERP", ts_ns, [3000.0 + i for i in range(n_bars)])

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
            # ETH in isolation: listed only 3 days before T0
            {"symbol": "ETHUSDT-PERP", "listing_date": "2021-04-30", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        dl = DataLayer(uni, catalog_root=catalog_path)

        reqs = [
            DataRequest("BTCUSDT-PERP", "close", "1m", 0, "bar"),
            DataRequest("ETHUSDT-PERP", "close", "1m", 0, "bar"),
        ]
        panels = dl.load(reqs)
        panel = panels["close"]

        # Shape must have both columns and all rows
        non_ts = {c for c in panel.columns if c != "ts"}
        assert non_ts == {"BTCUSDT-PERP", "ETHUSDT-PERP"}
        assert panel.height == n_bars


# ---------------------------------------------------------------------------
# Tests: parallel loading
# ---------------------------------------------------------------------------

class TestParallelLoad:
    """Multiple symbols load in parallel via ThreadPoolExecutor."""

    def test_four_symbols_parallel(self, tmp_path: Path, monkeypatch):
        """Four symbols load concurrently without error."""
        _stub_make_instrument(monkeypatch)

        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        symbols = ["BTCUSDT-PERP", "ETHUSDT-PERP", "SOLUSDT-PERP", "BNBUSDT-PERP"]
        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(5)]
        for sym in symbols:
            _write_catalog_bars(catalog_path, sym, ts_ns, [100.0 + j for j in range(5)])

        uni_rows = [{"symbol": s, "listing_date": "2020-01-01", "delisting_date": ""} for s in symbols]
        uni_path = _make_universe_csv(tmp_path, uni_rows)
        uni = Universe.load_csv(uni_path)

        dl = DataLayer(uni, catalog_root=catalog_path, max_workers=4)
        reqs = [DataRequest(s, "close", "1m", 0, "bar") for s in symbols]
        panels = dl.load(reqs)

        assert "close" in panels
        panel = panels["close"]
        non_ts = {c for c in panel.columns if c != "ts"}
        assert non_ts == set(symbols)
        assert panel.height == 5

    def test_partial_failure_does_not_crash(self, tmp_path: Path, monkeypatch):
        """If one symbol has no data, others still load successfully."""
        _stub_make_instrument(monkeypatch)

        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(3)]
        # Only write BTC bars; ETH has no catalog data
        _write_catalog_bars(catalog_path, "BTCUSDT-PERP", ts_ns, [100.0, 101.0, 102.0])

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
            {"symbol": "ETHUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        dl = DataLayer(uni, catalog_root=catalog_path)

        reqs = [
            DataRequest("BTCUSDT-PERP", "close", "1m", 0, "bar"),
            DataRequest("ETHUSDT-PERP", "close", "1m", 0, "bar"),
        ]
        # Must not raise
        panels = dl.load(reqs)
        panel = panels["close"]
        # BTC has data; ETH is all null or absent
        assert "BTCUSDT-PERP" in panel.columns


# ---------------------------------------------------------------------------
# Tests: load() groups by field_name
# ---------------------------------------------------------------------------

class TestLoadGrouping:
    """Multiple fields return separate Panel entries in the result dict."""

    def test_close_and_volume_separate_panels(self, tmp_path: Path, monkeypatch):
        _stub_make_instrument(monkeypatch)

        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(4)]
        _write_catalog_bars(catalog_path, "BTCUSDT-PERP", ts_ns, [100.0, 101.0, 102.0, 103.0])

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        dl = DataLayer(uni, catalog_root=catalog_path)

        reqs = [
            DataRequest("BTCUSDT-PERP", "close", "1m", 0, "bar"),
            DataRequest("BTCUSDT-PERP", "volume", "1m", 0, "bar"),
        ]
        panels = dl.load(reqs)

        assert "close" in panels
        assert "volume" in panels
        assert panels["close"].shape == panels["volume"].shape


# ---------------------------------------------------------------------------
# Tests: _parse_ts helper
# ---------------------------------------------------------------------------

class TestLookbackWarmup:
    """Bug 008 regression: ``DataLayer`` must honor the ``DataRequest.lookback``
    field by shifting the effective load start earlier by ``lookback *
    bar_duration``.  Without this, the first N rows of the user window are NaN
    for any factor that uses ``pct_change(N)`` / ``shift(N)``.
    """

    def test_lookback_expands_start_by_bar_duration(self, tmp_path: Path, monkeypatch):
        _stub_make_instrument(monkeypatch)

        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        # 30 1-minute bars starting at T0
        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(30)]
        closes = [100.0 + i for i in range(30)]
        _write_catalog_bars(catalog_path, "BTCUSDT-PERP", ts_ns, closes)

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        dl = DataLayer(uni, catalog_root=catalog_path)

        # User window: skip the first 10 bars.  With lookback=5 the loader must
        # reach back 5 bars earlier, so the Panel should start 5 minutes before
        # the user-specified start, giving kernels the warmup they need.
        user_start = datetime(1970, 1, 1) + timedelta(microseconds=(_T0_NS + 10 * _1MIN_NS) // 1_000)
        user_end = datetime(1970, 1, 1) + timedelta(microseconds=(_T0_NS + 20 * _1MIN_NS) // 1_000)

        req = DataRequest(
            symbol="BTCUSDT-PERP",
            field_name="close",
            frequency="1m",
            lookback=5,
            source="bar",
        )
        panels = dl.load(req, start=user_start, end=user_end)
        panel = panels["close"]

        assert not panel.is_empty()
        # The panel's first timestamp must be <= user_start - 5 minutes
        expected_warmup_start = user_start - timedelta(minutes=5)
        ts_values = _ts_list(panel)
        assert ts_values[0] <= expected_warmup_start, (
            f"Expected panel to include warmup rows starting at or before "
            f"{expected_warmup_start}, got {ts_values[0]}"
        )
        # And must still cover the user window
        assert ts_values[-1] >= user_end - timedelta(minutes=1)

    def test_lookback_zero_does_not_expand_start(self, tmp_path: Path, monkeypatch):
        """When lookback=0 the panel must start at or after ``start`` — no shift."""
        _stub_make_instrument(monkeypatch)

        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(30)]
        closes = [100.0 + i for i in range(30)]
        _write_catalog_bars(catalog_path, "BTCUSDT-PERP", ts_ns, closes)

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        dl = DataLayer(uni, catalog_root=catalog_path)

        user_start = datetime(1970, 1, 1) + timedelta(microseconds=(_T0_NS + 10 * _1MIN_NS) // 1_000)
        user_end = datetime(1970, 1, 1) + timedelta(microseconds=(_T0_NS + 20 * _1MIN_NS) // 1_000)

        req = DataRequest(
            symbol="BTCUSDT-PERP",
            field_name="close",
            frequency="1m",
            lookback=0,
            source="bar",
        )
        panels = dl.load(req, start=user_start, end=user_end)
        panel = panels["close"]

        assert not panel.is_empty()
        assert _ts_list(panel)[0] >= user_start


class TestParseTs:
    def test_iso_string(self):
        ts = _parse_ts("2021-01-01T00:00:00")
        assert isinstance(ts, datetime)
        assert ts == datetime(2021, 1, 1)

    def test_datetime_passthrough(self):
        ts = datetime(2021, 6, 1)
        result = _parse_ts(ts)
        assert result == ts

    def test_tz_aware_stripped(self):
        # ``datetime.fromisoformat`` accepts the ``+00:00`` offset.
        ts = "2021-01-01T00:00:00+00:00"
        result = _parse_ts(ts)
        assert result.tzinfo is None
        assert result == datetime(2021, 1, 1)
