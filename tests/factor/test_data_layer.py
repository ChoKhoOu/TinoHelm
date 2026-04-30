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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from tinohelm.factor.data_layer import DataLayer, _parse_ts, load_aligned
from tinohelm.factor.types import DataRequest
from tinohelm.factor.universe import Universe
from tinohelm.data.catalog_helpers import resolve_catalog_path
from tinohelm.strategy.loader_helpers import make_bar_type_str


def test_parse_ts_converts_offset_to_utc_naive() -> None:
    """Offset-aware timestamps must preserve the instant before stripping tz."""
    assert _parse_ts("2026-01-01T00:00:00-05:00") == datetime(2026, 1, 1, 5, 0)
    aware = datetime(2026, 1, 1, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert _parse_ts(aware) == datetime(2026, 1, 1, 5, 0)


# ---------------------------------------------------------------------------
# Test catalog writer (NT-free)
# ---------------------------------------------------------------------------

def _write_catalog_bars(
    catalog_path: Path,
    symbol_str: str,
    timestamps_ns: list[int],
    closes: list[float],
    *,
    interval: str = "1m",
    source_type: str | None = None,
):
    """Write a minimal bar Parquet fixture matching DataLayer's direct reader."""
    root = resolve_catalog_path(catalog_path, source_type) if source_type else catalog_path
    bar_dir = root / "data" / "bar" / make_bar_type_str(symbol_str, interval)
    bar_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "ts_event": timestamps_ns,
        "open": [close - 10.0 for close in closes],
        "high": [close + 20.0 for close in closes],
        "low": [close - 20.0 for close in closes],
        "close": [float(close) for close in closes],
        "volume": [5.0 for _ in closes],
    }).write_parquet(bar_dir / "bars.parquet")


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
    """Legacy no-network stub kept for tests that still call it."""
    try:
        from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue

        class _FakeInstrument:
            def __init__(self, symbol_str: str):
                self.id = InstrumentId(Symbol(symbol_str), Venue("BINANCE"))
    except ModuleNotFoundError:
        class _FakeInstrument:
            def __init__(self, symbol_str: str):
                self.id = f"{symbol_str}.BINANCE"

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


def _fixed_precision_bytes(value: float) -> bytes:
    """Encode a test value like Nautilus fixed-precision Int128 binary."""
    scaled = int(round(value * 10_000_000_000_000_000))
    return scaled.to_bytes(16, byteorder="little", signed=True)


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
        """Funding prints become visible only after their exact timestamp."""
        _stub_make_instrument(monkeypatch)
        catalog_path, funding_dir, uni, ts_ns = self._base_setup(tmp_path)
        dl = DataLayer(uni, catalog_root=catalog_path, funding_dir=funding_dir)

        funding_ts = [
            datetime(2024, 1, 1, 0, 0),
            datetime(2024, 1, 1, 8, 0),
        ]
        funding_series = pl.DataFrame(
            {"ts": funding_ts, "value": [0.0001, 0.0002]},
            schema={"ts": pl.Datetime("ns"), "value": pl.Float64},
        )

        bar_ts = [
            datetime(2024, 1, 1, 0, 0),
            datetime(2024, 1, 1, 1, 0),
            datetime(2024, 1, 1, 8, 0),
            datetime(2024, 1, 1, 9, 0),
        ]
        aligned = dl._align_funding_onto_bar_index(funding_series, bar_ts)

        assert aligned["value"].to_list() == [None, 0.0001, 0.0001, 0.0002]

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

    def test_load_funding_direct_uses_bar_index_not_raw_funding_index(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        """Direct funding loads must align onto bars, not sparse 8h timestamps."""
        _stub_make_instrument(monkeypatch)
        catalog_path, funding_dir, uni, ts_ns = self._base_setup(tmp_path)
        dl = DataLayer(uni, catalog_root=catalog_path, funding_dir=funding_dir)

        req = DataRequest(
            "BTCUSDT-PERP",
            "funding_rate",
            "1m",
            0,
            "funding_rate",
        )
        result = dl.load(req)

        assert "funding_rate" in result
        panel = result["funding_rate"]
        assert panel.height == len(ts_ns)
        assert _ts_list(panel) == [
            datetime(1970, 1, 1) + timedelta(microseconds=ts // 1_000)
            for ts in ts_ns
        ]

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

    def test_duplicate_field_across_frequencies_is_rejected(self, tmp_path: Path):
        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        dl = DataLayer(Universe.load_csv(uni_path), catalog_root=catalog_path)

        with pytest.raises(ValueError, match="output key collision"):
            dl.load([
                DataRequest("BTCUSDT-PERP", "close", "1m", 0, "bar"),
                DataRequest("BTCUSDT-PERP", "close", "5m", 0, "bar"),
            ])

    def test_invalid_bar_frequency_rejected_instead_of_falling_back_to_1m(self, tmp_path: Path):
        catalog_path = tmp_path / "catalog"
        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(3)]
        _write_catalog_bars(catalog_path, "BTCUSDT-PERP", ts_ns, [100.0, 101.0, 102.0])

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        dl = DataLayer(Universe.load_csv(uni_path), catalog_root=catalog_path)

        with pytest.raises(ValueError, match="Unsupported bar frequency"):
            dl.load(DataRequest("BTCUSDT-PERP", "close", "typo", 0, "bar"))

    def test_custom_6m_frequency_resamples_from_1m_source(self, tmp_path: Path):
        catalog_path = tmp_path / "catalog"
        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(12)]
        closes = [100.0 + i for i in range(12)]
        _write_catalog_bars(catalog_path, "BTCUSDT-PERP", ts_ns, closes)

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        dl = DataLayer(Universe.load_csv(uni_path), catalog_root=catalog_path)

        panels = dl.load([
            DataRequest("BTCUSDT-PERP", "open", "6m", 0, "bar"),
            DataRequest("BTCUSDT-PERP", "high", "6m", 0, "bar"),
            DataRequest("BTCUSDT-PERP", "low", "6m", 0, "bar"),
            DataRequest("BTCUSDT-PERP", "close", "6m", 0, "bar"),
            DataRequest("BTCUSDT-PERP", "volume", "6m", 0, "bar"),
        ])

        assert _ts_list(panels["close"]) == [
            datetime(1970, 1, 1) + timedelta(microseconds=ts_ns[0] // 1_000),
            datetime(1970, 1, 1) + timedelta(microseconds=ts_ns[6] // 1_000),
        ]
        assert _sym_values(panels["open"], "BTCUSDT-PERP") == [90.0, 96.0]
        assert _sym_values(panels["high"], "BTCUSDT-PERP") == [125.0, 131.0]
        assert _sym_values(panels["low"], "BTCUSDT-PERP") == [80.0, 86.0]
        assert _sym_values(panels["close"], "BTCUSDT-PERP") == [105.0, 111.0]
        assert _sym_values(panels["volume"], "BTCUSDT-PERP") == [30.0, 30.0]

    def test_bar_reader_uses_klines_source_root_before_legacy_flat_path(self, tmp_path: Path):
        catalog_path = tmp_path / "catalog"
        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(2)]
        _write_catalog_bars(
            catalog_path,
            "BTCUSDT-PERP",
            ts_ns,
            [100.0, 101.0],
            source_type="klines",
        )

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        dl = DataLayer(Universe.load_csv(uni_path), catalog_root=catalog_path)

        panel = dl.load(DataRequest("BTCUSDT-PERP", "close", "1m", 0, "bar"))["close"]
        assert _sym_values(panel, "BTCUSDT-PERP") == [100.0, 101.0]

    def test_close_and_volume_separate_panels(self, tmp_path: Path, monkeypatch):
        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        dl = DataLayer(uni, catalog_root=catalog_path, max_workers=1)

        ts_values = [datetime(2021, 5, 3, 0, i) for i in range(4)]

        def fake_load_bar_fields(symbol, field_names, frequency, start, end, source_type=None):
            values_by_field = {
                "close": [100.0, 101.0, 102.0, 103.0],
                "volume": [5.0, 5.0, 5.0, 5.0],
            }
            return {
                field: pl.DataFrame(
                    {"ts": ts_values, "value": values_by_field[field]},
                    schema={"ts": pl.Datetime("ns"), "value": pl.Float64},
                )
                for field in field_names
            }

        monkeypatch.setattr(dl, "_load_bar_fields", fake_load_bar_fields)

        reqs = [
            DataRequest("BTCUSDT-PERP", "close", "1m", 0, "bar"),
            DataRequest("BTCUSDT-PERP", "volume", "1m", 0, "bar"),
        ]
        panels = dl.load(reqs)

        assert "close" in panels
        assert "volume" in panels
        assert panels["close"].shape == panels["volume"].shape

    def test_multi_field_same_symbol_reads_once_without_nt(self, tmp_path: Path, monkeypatch):
        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        dl = DataLayer(uni, catalog_root=catalog_path, max_workers=1)

        ts_values = [
            datetime(2021, 5, 3, 0, 0),
            datetime(2021, 5, 3, 0, 1),
            datetime(2021, 5, 3, 0, 2),
        ]
        field_values = {
            "close": [100.0, 101.0, 102.0],
            "high": [110.0, 111.0, 112.0],
            "low": [90.0, 91.0, 92.0],
            "volume": [5.0, 6.0, 7.0],
        }
        calls: list[tuple[str, tuple[str, ...]]] = []

        def fake_load_bar_fields(symbol, field_names, frequency, start, end, source_type=None):
            calls.append((symbol, tuple(field_names)))
            assert frequency == "1m"
            return {
                field: pl.DataFrame(
                    {"ts": ts_values, "value": values},
                    schema={"ts": pl.Datetime("ns"), "value": pl.Float64},
                )
                for field, values in field_values.items()
                if field in field_names
            }

        monkeypatch.setattr(dl, "_load_bar_fields", fake_load_bar_fields)

        reqs = [
            DataRequest("BTCUSDT-PERP", "close", "1m", 0, "bar"),
            DataRequest("BTCUSDT-PERP", "high", "1m", 0, "bar"),
            DataRequest("BTCUSDT-PERP", "low", "1m", 0, "bar"),
            DataRequest("BTCUSDT-PERP", "volume", "1m", 0, "bar"),
        ]
        panels = dl.load(reqs)

        assert calls == [("BTCUSDT-PERP", ("close", "high", "low", "volume"))]
        assert set(panels) == {"close", "high", "low", "volume"}
        for field, values in field_values.items():
            assert panels[field].columns == ["ts", "BTCUSDT-PERP"]
            assert _sym_values(panels[field], "BTCUSDT-PERP") == values

    def test_grouped_bar_lookbacks_remain_per_field_without_nt(self, tmp_path: Path, monkeypatch):
        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        dl = DataLayer(uni, catalog_root=catalog_path, max_workers=1)

        ts_values = [datetime(2021, 5, 3, 0, i) for i in range(6)]

        def fake_load_bar_fields(symbol, field_names, frequency, start, end, source_type=None):
            return {
                field: pl.DataFrame(
                    {"ts": ts_values, "value": [float(i) for i in range(6)]},
                    schema={"ts": pl.Datetime("ns"), "value": pl.Float64},
                )
                for field in field_names
            }

        monkeypatch.setattr(dl, "_load_bar_fields", fake_load_bar_fields)

        user_start = datetime(2021, 5, 3, 0, 3)
        panels = dl.load([
            DataRequest("BTCUSDT-PERP", "close", "1m", 2, "bar"),
            DataRequest("BTCUSDT-PERP", "high", "1m", 0, "bar"),
        ], start=user_start)

        assert _ts_list(panels["close"])[0] == datetime(2021, 5, 3, 0, 1)
        assert _ts_list(panels["high"])[0] == user_start

    def test_direct_bar_reader_uses_pure_bar_type_without_make_instrument(self, tmp_path: Path, monkeypatch):
        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        dl = DataLayer(uni, catalog_root=catalog_path)

        def fail_make_instrument(symbol):
            raise AssertionError("_make_instrument must not be called")

        monkeypatch.setattr("tinohelm.data.catalog._make_instrument", fail_make_instrument)

        series = dl._load_bar_field("BTCUSDT-PERP", "close", "1m", None, None)
        assert series.is_empty()

    def test_direct_bar_reader_projects_numeric_columns_without_nt(self, tmp_path: Path):
        catalog_path = tmp_path / "catalog"
        bar_dir = catalog_path / "data" / "bar" / make_bar_type_str("BTCUSDT-PERP", "1m")
        bar_dir.mkdir(parents=True)
        pl.DataFrame({
            "ts_event": [_T0_NS, _T0_NS + _1MIN_NS],
            "close": [100.0, 101.0],
            "high": [110.0, 111.0],
            "unused": [1.0, 2.0],
        }).write_parquet(bar_dir / "bars.parquet")

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        dl = DataLayer(Universe.load_csv(uni_path), catalog_root=catalog_path)

        fields = dl._load_bar_fields("BTCUSDT-PERP", ["close", "high"], "1m", None, None)
        assert fields["close"]["value"].to_list() == [100.0, 101.0]
        assert fields["high"]["value"].to_list() == [110.0, 111.0]

    def test_direct_bar_reader_decodes_binary_columns_without_nt(self, tmp_path: Path):
        catalog_path = tmp_path / "catalog"
        bar_dir = catalog_path / "data" / "bar" / make_bar_type_str("BTCUSDT-PERP", "1m")
        bar_dir.mkdir(parents=True)
        pl.DataFrame({
            "ts_event": [_T0_NS, _T0_NS + _1MIN_NS],
            "close": [_fixed_precision_bytes(100.25), _fixed_precision_bytes(101.5)],
        }).write_parquet(bar_dir / "bars.parquet")

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        dl = DataLayer(Universe.load_csv(uni_path), catalog_root=catalog_path)

        series = dl._load_bar_field("BTCUSDT-PERP", "close", "1m", None, None)
        assert series["value"].to_list() == [100.25, 101.5]

    def test_direct_bar_reader_decodes_nautilus_written_bars(self, tmp_path: Path):
        pytest.importorskip("nautilus_trader")

        from nautilus_trader.model.data import Bar, BarSpecification, BarType
        from nautilus_trader.model.enums import AggregationSource, BarAggregation, PriceType
        from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
        from nautilus_trader.model.objects import Price, Quantity
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        catalog_path = tmp_path / "catalog"
        inst_id = InstrumentId(Symbol("BTCUSDT-PERP"), Venue("BINANCE"))
        bar_type = BarType(
            instrument_id=inst_id,
            bar_spec=BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST),
            aggregation_source=AggregationSource.EXTERNAL,
        )
        bars = [
            Bar(
                bar_type=bar_type,
                open=Price.from_str("100.1"),
                high=Price.from_str("110.2"),
                low=Price.from_str("90.3"),
                close=Price.from_str("101.4"),
                volume=Quantity.from_str("5.5"),
                ts_event=_T0_NS,
                ts_init=_T0_NS,
            ),
            Bar(
                bar_type=bar_type,
                open=Price.from_str("101.1"),
                high=Price.from_str("111.2"),
                low=Price.from_str("91.3"),
                close=Price.from_str("102.4"),
                volume=Quantity.from_str("6.5"),
                ts_event=_T0_NS + _1MIN_NS,
                ts_init=_T0_NS + _1MIN_NS,
            ),
        ]
        ParquetDataCatalog(str(catalog_path)).write_data(bars)

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        dl = DataLayer(Universe.load_csv(uni_path), catalog_root=catalog_path)

        fields = dl._load_bar_fields(
            "BTCUSDT-PERP",
            ["open", "high", "low", "close", "volume"],
            "1m",
            None,
            None,
        )
        assert fields["open"]["value"].to_list() == pytest.approx([100.1, 101.1])
        assert fields["high"]["value"].to_list() == pytest.approx([110.2, 111.2])
        assert fields["low"]["value"].to_list() == pytest.approx([90.3, 91.3])
        assert fields["close"]["value"].to_list() == pytest.approx([101.4, 102.4])
        assert fields["volume"]["value"].to_list() == pytest.approx([5.5, 6.5])


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
