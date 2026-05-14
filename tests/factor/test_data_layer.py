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
from unittest.mock import patch

import numpy as np
import polars as pl
import pytest

from tinohelm.factor.data_layer import DataLayer, _parse_ts, load_aligned
from tinohelm.factor.types import DataRequest, EventRequest
from tinohelm.factor.universe import Universe
from tinohelm.data.catalog_helpers import resolve_catalog_path
from tinohelm.strategy.loader_helpers import make_bar_type_str, normalize_symbol


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


def test_grouped_bar_loader_validates_unknown_fields_before_dispatch(tmp_path: Path) -> None:
    dl = DataLayer(catalog_root=tmp_path, universe=Universe.from_symbols(["BTCUSDT-PERP"]))

    with pytest.raises(ValueError) as exc:
        dl._load_bar_panels_grouped(
            [
                DataRequest(
                    symbol="BTCUSDT-PERP",
                    field_name="closing_price",
                    frequency="1m",
                    lookback=0,
                    source="bar",
                )
            ],
            frequency="1m",
            source_type="klines",
            start=None,
            end=None,
        )

    assert "Unknown bar field 'closing_price'" in str(exc.value)
    assert "close" in str(exc.value)
    assert "volume" in str(exc.value)


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
        """Set up a 2-symbol catalog + universe + funding updates."""
        from decimal import Decimal
        from tinohelm.data.catalog import _catalog_for_root
        from nautilus_trader.model.data import FundingRateUpdate
        from nautilus_trader.model.identifiers import InstrumentId

        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()
        funding_dir = tmp_path / "funding_rates"
        funding_dir.mkdir()

        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(12)]
        closes = [float(100 + i) for i in range(12)]
        _write_catalog_bars(catalog_path, "BTCUSDT-PERP", ts_ns, closes)

        inst = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
        cat = _catalog_for_root(catalog_path)
        cat.write_data([
            FundingRateUpdate(inst, Decimal("0.0001"), _T0_NS, _T0_NS, interval=480),
            FundingRateUpdate(inst, Decimal("0.0002"), _T0_NS + 8 * 3600 * 1_000_000_000, _T0_NS + 8 * 3600 * 1_000_000_000, interval=480),
        ])

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        return catalog_path, funding_dir, uni, ts_ns

    def test_load_funding_updates_returns_frame(self, tmp_path: Path, monkeypatch):
        _stub_make_instrument(monkeypatch)
        catalog_path, funding_dir, uni, _ = self._base_setup(tmp_path)
        dl = DataLayer(uni, catalog_root=catalog_path, funding_dir=funding_dir)

        frame = dl._load_funding_rate("BTCUSDT-PERP", start=None, end=None)
        assert isinstance(frame, pl.DataFrame)
        assert frame.columns == ["ts", "value"]
        assert frame.height == 2
        assert frame["value"].to_list()[0] == pytest.approx(0.0001)

    def test_load_funding_skips_json_when_parquet_is_sufficient_for_bounded_range(self, tmp_path: Path, monkeypatch):
        _stub_make_instrument(monkeypatch)
        catalog_path, funding_dir, uni, _ = self._base_setup(tmp_path)
        dl = DataLayer(uni, catalog_root=catalog_path, funding_dir=funding_dir)

        calls = []

        def fake_load_json(symbol, start, end):
            calls.append((symbol, start, end))
            return pl.DataFrame({"ts": [], "value": []}, schema={"ts": pl.Datetime("ns"), "value": pl.Float64})

        monkeypatch.setattr(dl, "_load_funding_rate_json", fake_load_json)

        start = datetime(2021, 5, 3, 0, 0)
        end = datetime(2021, 5, 3, 8, 0)
        frame = dl._load_funding_rate("BTCUSDT-PERP", start=start, end=end)

        assert frame.height == 2
        assert calls == []

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

    def test_missing_funding_updates_returns_empty_frame(self, tmp_path: Path, monkeypatch):
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

    def test_funding_updates_are_visible_from_bar_source_root(self, tmp_path: Path, monkeypatch):
        _stub_make_instrument(monkeypatch)
        catalog_path, funding_dir, uni, _ = self._base_setup(tmp_path)
        bar_root = resolve_catalog_path(catalog_path, "klines")
        dl = DataLayer(uni, catalog_root=bar_root, funding_dir=funding_dir)

        frame = dl._load_funding_rate("BTCUSDT-PERP", start=None, end=None)

        assert isinstance(frame, pl.DataFrame)
        assert frame.columns == ["ts", "value"]
        assert frame.height == 2
        assert frame["value"].to_list() == pytest.approx([0.0001, 0.0002])


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

    def test_custom_6m_frequency_resamples_from_1m_close_time_source(self, tmp_path: Path):
        catalog_path = tmp_path / "catalog"
        ts_ns = [_T0_NS + i * _1MIN_NS + _1MIN_NS - 1_000_000 for i in range(12)]
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
            datetime(1970, 1, 1) + timedelta(microseconds=ts_ns[5] // 1_000),
            datetime(1970, 1, 1) + timedelta(microseconds=ts_ns[11] // 1_000),
        ]
        assert _sym_values(panels["open"], "BTCUSDT-PERP") == [90.0, 96.0]
        assert _sym_values(panels["high"], "BTCUSDT-PERP") == [125.0, 131.0]
        assert _sym_values(panels["low"], "BTCUSDT-PERP") == [80.0, 86.0]
        assert _sym_values(panels["close"], "BTCUSDT-PERP") == [105.0, 111.0]
        assert _sym_values(panels["volume"], "BTCUSDT-PERP") == [30.0, 30.0]

        filtered = dl.load(
            DataRequest(
                "BTCUSDT-PERP",
                "close",
                "6m",
                0,
                "bar",
            ),
            start=datetime(2021, 5, 3, 0, 6),
        )["close"]
        assert _ts_list(filtered) == [
            datetime(1970, 1, 1) + timedelta(microseconds=ts_ns[11] // 1_000),
        ]
        assert _sym_values(filtered, "BTCUSDT-PERP") == [111.0]

        exact_start = dl.load(
            DataRequest(
                "BTCUSDT-PERP",
                "open",
                "6m",
                0,
                "bar",
            ),
            start=datetime(2021, 5, 3, 0, 5, 59, 999000),
        )["open"]
        assert _ts_list(exact_start) == [
            datetime(1970, 1, 1) + timedelta(microseconds=ts_ns[5] // 1_000),
            datetime(1970, 1, 1) + timedelta(microseconds=ts_ns[11] // 1_000),
        ]
        assert _sym_values(exact_start, "BTCUSDT-PERP") == [90.0, 96.0]

        mid_bar_end = dl.load(
            DataRequest(
                "BTCUSDT-PERP",
                "close",
                "6m",
                0,
                "bar",
            ),
            end=datetime(2021, 5, 3, 0, 10),
        )["close"]
        assert _ts_list(mid_bar_end) == [
            datetime(1970, 1, 1) + timedelta(microseconds=ts_ns[5] // 1_000),
        ]
        assert _sym_values(mid_bar_end, "BTCUSDT-PERP") == [105.0]

    def test_fallback_resample_reads_bounded_source_window(self, tmp_path: Path, monkeypatch):
        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir()

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        dl = DataLayer(Universe.load_csv(uni_path), catalog_root=catalog_path)

        requested_start = datetime(2021, 5, 3, 0, 5, 59, 999000)
        requested_end = datetime(2021, 5, 3, 0, 10)
        calls: list[dict[str, object]] = []

        def fake_read_bar_frame(**kwargs):
            calls.append(kwargs)
            if kwargs["frequency"] != "1m":
                return None, []
            return pl.DataFrame(), []

        monkeypatch.setattr(dl, "_read_bar_frame", fake_read_bar_frame)

        dl.load(
            DataRequest("BTCUSDT-PERP", "close", "6m", 0, "bar"),
            start=requested_start,
            end=requested_end,
        )

        direct_call = calls[0]
        assert direct_call["frequency"] == "6m"
        assert direct_call["start"] == requested_start
        assert direct_call["end"] == requested_end

        fallback_call = next(call for call in calls if call["frequency"] == "1m")
        assert fallback_call["start"] is not None
        assert fallback_call["end"] is not None
        assert fallback_call["start"] != requested_start
        assert fallback_call["end"] != requested_end
        assert fallback_call["start"] == datetime(2021, 5, 3, 0, 0, 59, 999000)
        assert fallback_call["end"] == datetime(2021, 5, 3, 0, 15)

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

    def test_explicit_non_default_bar_source_does_not_fall_back_to_base_klines(self, tmp_path: Path):
        catalog_path = tmp_path / "catalog"
        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(2)]
        _write_catalog_bars(
            catalog_path,
            "BTCUSDT-PERP",
            ts_ns,
            [100.0, 101.0],
        )

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        dl = DataLayer(Universe.load_csv(uni_path), catalog_root=catalog_path)

        panel = dl.load(
            DataRequest(
                "BTCUSDT-PERP",
                "close",
                "1m",
                0,
                "bar",
                source_type="markPriceKlines",
            )
        )["close"]

        assert panel.is_empty()

    def test_fallback_resample_drops_incomplete_child_buckets(self, tmp_path: Path):
        catalog_path = tmp_path / "catalog"
        # close-time 1m source timestamps.  Minute 3 is missing in the first
        # 6m bucket; the second bucket is complete and should be kept.
        source_indices = [0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11]
        ts_ns = [
            _T0_NS + i * _1MIN_NS + _1MIN_NS - 1_000_000
            for i in source_indices
        ]
        _write_catalog_bars(
            catalog_path,
            "BTCUSDT-PERP",
            ts_ns,
            [100.0 + i for i in source_indices],
        )

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        dl = DataLayer(Universe.load_csv(uni_path), catalog_root=catalog_path)

        panel = dl.load(DataRequest("BTCUSDT-PERP", "volume", "6m", 0, "bar"))["volume"]

        assert _ts_list(panel) == [
            datetime(1970, 1, 1) + timedelta(microseconds=ts_ns[-1] // 1_000),
        ]
        assert _sym_values(panel, "BTCUSDT-PERP") == [30.0]

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

# ---------------------------------------------------------------------------
# Tests: tick/event DataLayer contract and lazy planning
# ---------------------------------------------------------------------------

def _write_trade_ticks(catalog_path: Path, symbol_str: str, rows: list[dict], filename: str = "ticks.parquet") -> Path:
    from tinohelm.strategy.loader_helpers import normalize_symbol

    tick_dir = catalog_path / "data" / "trade_tick" / normalize_symbol(symbol_str)
    tick_dir.mkdir(parents=True, exist_ok=True)
    path = tick_dir / filename
    pl.DataFrame(rows).write_parquet(path)
    return path


def _write_quote_ticks(catalog_path: Path, symbol_str: str, rows: list[dict], filename: str = "quotes.parquet") -> Path:
    from tinohelm.strategy.loader_helpers import normalize_symbol

    tick_dir = catalog_path / "data" / "quote_tick" / normalize_symbol(symbol_str)
    tick_dir.mkdir(parents=True, exist_ok=True)
    path = tick_dir / filename
    pl.DataFrame(rows).write_parquet(path)
    return path


def test_load_panel_rejects_raw_trade_tick_frequency(tmp_path: Path) -> None:
    dl = DataLayer(Universe.from_symbols(["BTCUSDT-PERP"]), catalog_root=tmp_path)
    req = DataRequest("BTCUSDT-PERP", "trade_qty", "tick", 0, "trade_tick")

    with pytest.raises(ValueError, match="load_events"):
        dl.load_panel(req)
    with pytest.raises(ValueError, match="load_events"):
        dl.load(req)


def test_load_events_trade_tick_returns_raw_rows_without_aggregation(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    rows = [
        {"ts_event": _T0_NS + 1_000_000_000, "price": 100.0, "size": 1.0, "aggressor_side": "BUYER", "trade_id": 1},
        {"ts_event": _T0_NS + 2_000_000_000, "price": 101.0, "size": 2.0, "aggressor_side": "SELLER", "trade_id": 2},
    ]
    _write_trade_ticks(tmp_path, symbol, rows)
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    events = dl.load_events(symbol=symbol, source="trade_tick", fields=("trade_price", "trade_qty", "trade_id"))

    assert events.height == 2
    assert events.schema["trade_id"] == pl.Utf8
    assert events.select("ts", "trade_price", "trade_qty", "trade_id").to_dicts() == [
        {"ts": datetime(2021, 5, 3, 0, 0, 1), "trade_price": 100.0, "trade_qty": 1.0, "trade_id": "1"},
        {"ts": datetime(2021, 5, 3, 0, 0, 2), "trade_price": 101.0, "trade_qty": 2.0, "trade_id": "2"},
    ]


def test_load_events_trade_tick_string_trade_id_returns_utf8(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS + 1_000_000_000, "price": 100.0, "size": 1.0, "trade_id": "abc"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    events = dl.load_events(symbol=symbol, source="trade_tick", fields=("trade_price", "trade_id"))

    assert events.schema["trade_id"] == pl.Utf8
    assert events["trade_id"].to_list() == ["abc"]


def test_load_events_quote_tick_returns_raw_derived_rows(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    rows = [
        {"ts_event": _T0_NS + 1_000_000_000, "bid_price": 99.0, "bid_size": 3.0, "ask_price": 101.0, "ask_size": 1.0},
        {"ts_event": _T0_NS + 2_000_000_000, "bid_price": 98.0, "bid_size": 1.0, "ask_price": 102.0, "ask_size": 3.0},
    ]
    _write_quote_ticks(resolve_catalog_path(tmp_path, "bookTicker"), symbol, rows)
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    events = dl.load_events(
        symbol=symbol,
        source="quote_tick",
        fields=("bid_price", "bid_qty", "mid_price", "orderbook_imbalance"),
        start="2021-05-03T00:00:02",
    )

    assert events.select("ts", "bid_price", "bid_qty", "mid_price", "orderbook_imbalance").to_dicts() == [
        {
            "ts": datetime(2021, 5, 3, 0, 0, 2),
            "bid_price": 98.0,
            "bid_qty": 1.0,
            "mid_price": 100.0,
            "orderbook_imbalance": -0.5,
        },
    ]


def test_load_events_empty_trade_tick_preserves_requested_schema(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    events = dl.load_events(symbol=symbol, source="trade_tick", fields=("trade_price", "trade_qty", "trade_id"))

    assert events.height == 0
    assert events.columns == ["ts", "trade_price", "trade_qty", "trade_id"]
    assert events.schema == {
        "ts": pl.Datetime("ns"),
        "trade_price": pl.Float64,
        "trade_qty": pl.Float64,
        "trade_id": pl.Utf8,
    }

    non_empty = pl.DataFrame({
        "ts": [datetime(2021, 5, 3, 0, 0, 1)],
        "trade_price": [100.0],
        "trade_qty": [1.0],
        "trade_id": ["1"],
    }, schema=events.schema)
    assert pl.concat([events, non_empty]).schema["trade_id"] == pl.Utf8


def test_load_events_trade_tick_read_error_raises_by_default(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS + 1_000_000_000, "price": 100.0, "size": 1.0, "trade_id": 1},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    with patch("polars.scan_parquet", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            dl.load_events(symbol=symbol, source="trade_tick", fields=("trade_price", "trade_qty", "trade_id"))


def test_load_events_trade_tick_read_error_empty_fallback_preserves_schema(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS + 1_000_000_000, "price": 100.0, "size": 1.0, "trade_id": 1},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    with patch("polars.scan_parquet", side_effect=RuntimeError("boom")):
        events = dl.load_events(
            symbol=symbol,
            source="trade_tick",
            fields=("trade_price", "trade_qty", "trade_id"),
            on_error="empty",
        )

    assert events.height == 0
    assert events.schema == {
        "ts": pl.Datetime("ns"),
        "trade_price": pl.Float64,
        "trade_qty": pl.Float64,
        "trade_id": pl.Utf8,
    }


def test_load_events_trade_tick_missing_ts_event_raises_by_default(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    root = tmp_path / "data" / "trade_tick" / normalize_symbol(symbol)
    root.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"price": [100.0], "size": [1.0], "trade_id": [1]}).write_parquet(root / "no_ts.parquet")
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    with pytest.raises(ValueError, match="ts_event"):
        dl.load_events(symbol=symbol, source="trade_tick", fields=("trade_price", "trade_qty", "trade_id"))


def test_load_events_trade_tick_missing_requested_trade_id_raises_by_default(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS + 1_000_000_000, "price": 100.0, "size": 1.0},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    with pytest.raises(ValueError, match="trade_id"):
        dl.load_events(symbol=symbol, source="trade_tick", fields=("trade_id",))


def test_load_events_trade_tick_missing_ts_event_empty_fallback(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    root = tmp_path / "data" / "trade_tick" / normalize_symbol(symbol)
    root.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"price": [100.0], "size": [1.0], "trade_id": [1]}).write_parquet(root / "no_ts.parquet")
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    events = dl.load_events(
        symbol=symbol,
        source="trade_tick",
        fields=("trade_price", "trade_qty", "trade_id"),
        on_error="empty",
    )

    assert events.height == 0
    assert events.schema == {
        "ts": pl.Datetime("ns"),
        "trade_price": pl.Float64,
        "trade_qty": pl.Float64,
        "trade_id": pl.Utf8,
    }


def test_load_events_quote_tick_read_error_raises_by_default(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_quote_ticks(resolve_catalog_path(tmp_path, "bookTicker"), symbol, [
        {"ts_event": _T0_NS + 1_000_000_000, "bid_price": 99.0, "bid_size": 1.0, "ask_price": 101.0, "ask_size": 1.0},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    with patch("polars.scan_parquet", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            dl.load_events(symbol=symbol, source="quote_tick", fields=("mid_price",))


def test_load_events_quote_tick_read_error_empty_fallback(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_quote_ticks(resolve_catalog_path(tmp_path, "bookTicker"), symbol, [
        {"ts_event": _T0_NS + 1_000_000_000, "bid_price": 99.0, "bid_size": 1.0, "ask_price": 101.0, "ask_size": 1.0},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    with patch("polars.scan_parquet", side_effect=RuntimeError("boom")):
        events = dl.load_events(symbol=symbol, source="quote_tick", fields=("mid_price",), on_error="empty")

    assert events.height == 0
    assert events.schema == {"ts": pl.Datetime("ns"), "mid_price": pl.Float64}


def test_event_request_on_error_empty_fallback(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS + 1_000_000_000, "price": 100.0, "size": 1.0, "trade_id": 1},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    request = EventRequest(symbol=symbol, source="trade_tick", fields=("trade_id",), on_error="empty")

    with patch("polars.scan_parquet", side_effect=RuntimeError("boom")):
        events = dl.load_events(request)

    assert events.schema == {"ts": pl.Datetime("ns"), "trade_id": pl.Utf8}


def test_load_events_rejects_trade_imbalance_raw_field(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    with pytest.raises(ValueError, match="panel aggregation.*signed_trade_qty.*buy_qty.*sell_qty"):
        dl.load_events(symbol=symbol, source="trade_tick", fields=("trade_imbalance",))


def test_load_panel_rejects_unknown_trade_tick_source_type(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick", source_type="typo")

    with pytest.raises(ValueError, match="Unknown trade_tick source_type.*typo"):
        dl.load_panel(req)


def test_load_events_rejects_unknown_trade_tick_source_type(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    with pytest.raises(ValueError, match="Unknown trade_tick source_type.*typo"):
        dl.load_events(symbol=symbol, source="trade_tick", fields=("trade_qty",), source_type="typo")


def test_load_panel_rejects_unknown_quote_tick_source_type(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "bid_price", "1m", 0, "quote_tick", source_type="typo")

    with pytest.raises(ValueError, match="Unknown quote_tick source_type.*typo"):
        dl.load_panel(req)


def test_trade_tick_panel_grouped_loader_reads_once_for_multiple_fields(tmp_path: Path, monkeypatch) -> None:
    symbol = "BTCUSDT-PERP"
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path, max_workers=1)
    calls = []

    def fake_loader(symbol, field_names, frequency, start, end, source_type=None):
        calls.append((symbol, tuple(field_names)))
        ts = datetime(2021, 5, 3)
        return {
            field: pl.DataFrame({"ts": [ts], "value": [1.0]}, schema={"ts": pl.Datetime("ns"), "value": pl.Float64})
            for field in field_names
        }

    monkeypatch.setattr(dl, "_load_trade_tick_fields", fake_loader)
    reqs = [
        DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick"),
        DataRequest(symbol, "buy_qty", "1m", 0, "trade_tick"),
    ]

    panels = dl.load_panel(reqs)

    assert calls == [(symbol, ("trade_qty", "buy_qty"))]
    assert set(panels) == {"trade_qty", "buy_qty"}


def test_quote_tick_panel_grouped_loader_reads_once_for_multiple_fields(tmp_path: Path, monkeypatch) -> None:
    symbol = "BTCUSDT-PERP"
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path, max_workers=1)
    calls = []

    def fake_loader(symbol, field_names, frequency, start, end, source_type=None):
        calls.append((symbol, tuple(field_names)))
        ts = datetime(2021, 5, 3)
        return {
            field: pl.DataFrame({"ts": [ts], "value": [1.0]}, schema={"ts": pl.Datetime("ns"), "value": pl.Float64})
            for field in field_names
        }

    monkeypatch.setattr(dl, "_load_quote_tick_fields", fake_loader)
    reqs = [
        DataRequest(symbol, "bid_price", "1m", 0, "quote_tick"),
        DataRequest(symbol, "orderbook_imbalance", "1m", 0, "quote_tick"),
    ]

    panels = dl.load_panel(reqs)

    assert calls == [(symbol, ("bid_price", "orderbook_imbalance"))]
    assert set(panels) == {"bid_price", "orderbook_imbalance"}


def test_trade_tick_panel_rejects_same_field_across_source_types(tmp_path: Path, monkeypatch) -> None:
    symbol = "BTCUSDT-PERP"
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path, max_workers=1)

    def fake_loader(symbol, field_names, frequency, start, end, source_type=None):
        ts = datetime(2021, 5, 3)
        return {
            field: pl.DataFrame({"ts": [ts], "value": [1.0]}, schema={"ts": pl.Datetime("ns"), "value": pl.Float64})
            for field in field_names
        }

    monkeypatch.setattr(dl, "_load_trade_tick_fields", fake_loader)
    reqs = [
        DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick", source_type="aggTrades"),
        DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick", source_type="trades"),
    ]

    with pytest.raises(ValueError, match="output key collision.*trade_qty.*single frequency/source_type"):
        dl.load_panel(reqs)


def test_quote_tick_panel_rejects_same_field_across_frequencies(tmp_path: Path, monkeypatch) -> None:
    symbol = "BTCUSDT-PERP"
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path, max_workers=1)

    def fake_loader(symbol, field_names, frequency, start, end):
        ts = datetime(2021, 5, 3)
        return {
            field: pl.DataFrame({"ts": [ts], "value": [1.0]}, schema={"ts": pl.Datetime("ns"), "value": pl.Float64})
            for field in field_names
        }

    monkeypatch.setattr(dl, "_load_quote_tick_fields", fake_loader)
    reqs = [
        DataRequest(symbol, "bid_price", "1m", 0, "quote_tick"),
        DataRequest(symbol, "bid_price", "5m", 0, "quote_tick"),
    ]

    with pytest.raises(ValueError, match="output key collision.*bid_price.*single frequency/source_type"):
        dl.load_panel(reqs)


def test_quote_tick_lazy_panel_filters_window_and_matches_spread(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_quote_ticks(resolve_catalog_path(tmp_path, "bookTicker"), symbol, [
        {"ts_event": _T0_NS - 60 * 1_000_000_000, "bid_price": 90.0, "bid_size": 10.0, "ask_price": 110.0, "ask_size": 10.0, "extra": 1},
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "bid_price": 99.0, "bid_size": 3.0, "ask_price": 101.0, "ask_size": 1.0, "extra": 2},
        {"ts_event": _T0_NS + 20 * 1_000_000_000, "bid_price": 98.0, "bid_size": 1.0, "ask_price": 102.0, "ask_size": 3.0, "extra": 3},
        {"ts_event": _T0_NS + 70 * 1_000_000_000, "bid_price": 50.0, "bid_size": 10.0, "ask_price": 150.0, "ask_size": 10.0, "extra": 4},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "orderbook_imbalance", "1m", 0, "quote_tick")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:00:59.999000")["orderbook_imbalance"]

    assert panel.height == 1
    assert panel["ts"].to_list() == [datetime(2021, 5, 3, 0, 0, 59, 999000)]
    np.testing.assert_allclose(panel[symbol].to_numpy(), [(1.0 - 3.0) / (1.0 + 3.0)])


def test_quote_tick_panel_boundaries_are_left_closed_right_open(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_quote_ticks(resolve_catalog_path(tmp_path, "bookTicker"), symbol, [
        {"ts_event": _T0_NS, "bid_price": 99.0, "bid_size": 1.0, "ask_price": 101.0, "ask_size": 1.0},
        {"ts_event": _T0_NS + 59_999_000_000, "bid_price": 100.0, "bid_size": 1.0, "ask_price": 102.0, "ask_size": 1.0},
        {"ts_event": _T0_NS + 60_000_000_000, "bid_price": 109.0, "bid_size": 1.0, "ask_price": 111.0, "ask_size": 1.0},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "mid_price", "1m", 0, "quote_tick")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:01:59.999000")["mid_price"]

    assert panel["ts"].dtype == pl.Datetime("ns")
    assert panel["ts"].to_list() == [
        datetime(2021, 5, 3, 0, 0, 59, 999000),
        datetime(2021, 5, 3, 0, 1, 59, 999000),
    ]
    np.testing.assert_allclose(panel[symbol].to_numpy(), [101.0, 110.0])


def test_quote_tick_panel_end_inside_bucket_does_not_return_partial_bar(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_quote_ticks(resolve_catalog_path(tmp_path, "bookTicker"), symbol, [
        {"ts_event": _T0_NS, "bid_price": 99.0, "bid_size": 1.0, "ask_price": 101.0, "ask_size": 1.0},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "mid_price", "1m", 0, "quote_tick")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:00:01")["mid_price"]

    assert panel.is_empty()


def test_quote_tick_panel_start_inside_bucket_uses_complete_first_bucket(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_quote_ticks(resolve_catalog_path(tmp_path, "bookTicker"), symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "bid_price": 99.0, "bid_size": 3.0, "ask_price": 101.0, "ask_size": 1.0},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "orderbook_imbalance", "1m", 0, "quote_tick")

    panel = dl.load_panel(req, start="2021-05-03T00:00:30", end="2021-05-03T00:00:59.999000")["orderbook_imbalance"]

    assert panel["ts"].to_list() == [datetime(2021, 5, 3, 0, 0, 59, 999000)]
    np.testing.assert_allclose(panel[symbol].to_numpy(), [0.5])


def test_quote_tick_panel_uses_lazy_aggregation_not_raw_frame_helper(tmp_path: Path, monkeypatch) -> None:
    symbol = "BTCUSDT-PERP"
    _write_quote_ticks(resolve_catalog_path(tmp_path, "bookTicker"), symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "bid_price": 99.0, "bid_size": 3.0, "ask_price": 101.0, "ask_size": 1.0},
        {"ts_event": _T0_NS + 20 * 1_000_000_000, "bid_price": 98.0, "bid_size": 1.0, "ask_price": 102.0, "ask_size": 3.0},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    def fail_raw_helper(*args, **kwargs):
        raise AssertionError("_quote_frame_to_series should not be called")

    monkeypatch.setattr("tinohelm.factor.data_layer._quote_frame_to_series", fail_raw_helper)

    panel = dl.load_panel(DataRequest(symbol, "mid_price", "1m", 0, "quote_tick"))["mid_price"]

    assert panel.height == 1
    assert panel["ts"].to_list() == [datetime(2021, 5, 3, 0, 0, 59, 999000)]
    np.testing.assert_allclose(panel[symbol].to_numpy(), [100.0])


def test_trade_tick_lazy_panel_filters_window_and_matches_imbalance(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS - 60 * 1_000_000_000, "price": 99.0, "size": 100.0, "aggressor_side": "BUYER"},
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "price": 100.0, "size": 2.0, "aggressor_side": "BUYER"},
        {"ts_event": _T0_NS + 20 * 1_000_000_000, "price": 101.0, "size": 1.0, "aggressor_side": "SELLER"},
        {"ts_event": _T0_NS + 70 * 1_000_000_000, "price": 102.0, "size": 50.0, "aggressor_side": "SELLER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "trade_imbalance", "1m", 0, "trade_tick")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:00:59.999000")["trade_imbalance"]

    assert panel.height == 1
    assert panel["ts"].to_list() == [datetime(2021, 5, 3, 0, 0, 59, 999000)]
    np.testing.assert_allclose(panel[symbol].to_numpy(), [(2.0 - 1.0) / 3.0])


def test_trade_tick_panel_boundaries_are_left_closed_right_open(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS, "price": 100.0, "size": 1.0, "aggressor_side": "BUYER"},
        {"ts_event": _T0_NS + 59_999_000_000, "price": 101.0, "size": 2.0, "aggressor_side": "BUYER"},
        {"ts_event": _T0_NS + 60_000_000_000, "price": 102.0, "size": 4.0, "aggressor_side": "BUYER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:01:59.999000")["trade_qty"]

    assert panel["ts"].dtype == pl.Datetime("ns")
    assert panel["ts"].to_list() == [
        datetime(2021, 5, 3, 0, 0, 59, 999000),
        datetime(2021, 5, 3, 0, 1, 59, 999000),
    ]
    np.testing.assert_allclose(panel[symbol].to_numpy(), [3.0, 4.0])


def test_trade_tick_panel_end_inside_bucket_does_not_return_partial_bar(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS, "price": 100.0, "size": 1.0, "aggressor_side": "BUYER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:00:01")["trade_qty"]

    assert panel.is_empty()


def test_trade_tick_panel_start_inside_bucket_uses_complete_first_bucket(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "price": 100.0, "size": 2.0, "aggressor_side": "BUYER"},
        {"ts_event": _T0_NS + 50 * 1_000_000_000, "price": 101.0, "size": 5.0, "aggressor_side": "BUYER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick")

    panel = dl.load_panel(req, start="2021-05-03T00:00:30", end="2021-05-03T00:00:59.999000")["trade_qty"]

    assert panel["ts"].to_list() == [datetime(2021, 5, 3, 0, 0, 59, 999000)]
    np.testing.assert_allclose(panel[symbol].to_numpy(), [7.0])


def test_trade_tick_panel_implicit_source_prefers_default_feed_without_union(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(resolve_catalog_path(tmp_path, "aggTrades"), symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "price": 100.0, "size": 2.0, "aggressor_side": "BUYER"},
    ])
    _write_trade_ticks(resolve_catalog_path(tmp_path, "trades"), symbol, [
        {"ts_event": _T0_NS + 20 * 1_000_000_000, "price": 101.0, "size": 5.0, "aggressor_side": "BUYER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:00:59.999000")["trade_qty"]

    assert panel.height == 1
    np.testing.assert_allclose(panel[symbol].to_numpy(), [2.0])


def test_trade_tick_panel_source_type_reads_only_requested_catalog(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(resolve_catalog_path(tmp_path, "trades"), symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "price": 100.0, "size": 2.0, "aggressor_side": "BUYER"},
    ])
    _write_trade_ticks(resolve_catalog_path(tmp_path, "aggTrades"), symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "price": 200.0, "size": 99.0, "aggressor_side": "BUYER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick", source_type="trades")

    panel = dl.load_panel(req)["trade_qty"]

    assert panel.height == 1
    np.testing.assert_allclose(panel[symbol].to_numpy(), [2.0])


def test_trade_tick_source_type_does_not_fallback_to_unrelated_catalog_root(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    catalog_path = resolve_catalog_path(tmp_path, "aggTrades")
    _write_trade_ticks(catalog_path, symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "price": 200.0, "size": 99.0, "aggressor_side": "BUYER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=catalog_path)
    req = DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick", source_type="trades")

    panel = dl.load_panel(req)["trade_qty"]

    assert panel.is_empty() or 99.0 not in panel.get_column(symbol).drop_nulls().to_list()


def test_trade_tick_explicit_agg_trades_does_not_read_trades_source_root(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    catalog_path = resolve_catalog_path(tmp_path, "trades")
    _write_trade_ticks(catalog_path, symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "price": 100.0, "size": 7.0, "aggressor_side": "BUYER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=catalog_path)
    req = DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick", source_type="aggTrades")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:00:59.999000")["trade_qty"]

    assert panel.is_empty()


def test_trade_tick_explicit_agg_trades_does_not_read_nested_under_trades_root(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    catalog_path = resolve_catalog_path(tmp_path, "trades")
    nested_agg_trades_path = resolve_catalog_path(catalog_path, "aggTrades")
    _write_trade_ticks(nested_agg_trades_path, symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "price": 100.0, "size": 7.0, "aggressor_side": "BUYER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=catalog_path)
    req = DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick", source_type="aggTrades")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:00:59.999000")["trade_qty"]

    assert panel.is_empty()


def test_trade_tick_explicit_agg_trades_reads_legacy_flat_catalog(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "price": 100.0, "size": 3.0, "aggressor_side": "BUYER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick", source_type="aggTrades")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:00:59.999000")["trade_qty"]

    assert panel.height == 1
    np.testing.assert_allclose(panel[symbol].to_numpy(), [3.0])


def test_trade_tick_explicit_agg_trades_falls_through_after_pruned_root(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(resolve_catalog_path(tmp_path, "aggTrades"), symbol, [
        {"ts_event": _T0_NS - 86_400 * 1_000_000_000, "price": 200.0, "size": 99.0, "aggressor_side": "BUYER"},
    ])
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "price": 100.0, "size": 4.0, "aggressor_side": "BUYER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick", source_type="aggTrades")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:00:59.999000")["trade_qty"]

    assert panel.height == 1
    np.testing.assert_allclose(panel[symbol].to_numpy(), [4.0])


def test_trade_tick_explicit_agg_trades_uses_source_aware_only_when_available(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(resolve_catalog_path(tmp_path, "aggTrades"), symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "price": 100.0, "size": 2.0, "aggressor_side": "BUYER"},
    ])
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS + 70 * 1_000_000_000, "price": 101.0, "size": 3.0, "aggressor_side": "BUYER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick", source_type="aggTrades")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:01:59.999000")["trade_qty"]

    assert panel["ts"].to_list() == [datetime(2021, 5, 3, 0, 0, 59, 999000)]
    np.testing.assert_allclose(panel[symbol].to_numpy(), [2.0])


def test_trade_tick_overlap_dedupes_by_trade_id_preferring_source_aware(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    ts_event = _T0_NS + 10 * 1_000_000_000
    _write_trade_ticks(resolve_catalog_path(tmp_path, "aggTrades"), symbol, [
        {"ts_event": ts_event, "price": 100.0, "size": 2.0, "aggressor_side": "BUYER", "trade_id": 10},
    ])
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": ts_event, "price": 999.0, "size": 99.0, "aggressor_side": "SELLER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    panel = dl.load_panel(
        DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick", source_type="aggTrades"),
        start="2021-05-03T00:00:00",
        end="2021-05-03T00:00:59.999000",
    )["trade_qty"]
    events = dl.load_events(
        symbol=symbol,
        source="trade_tick",
        fields=("trade_price", "trade_qty", "trade_side", "trade_id"),
        source_type="aggTrades",
    )

    np.testing.assert_allclose(panel[symbol].to_numpy(), [2.0])
    assert events.select("trade_price", "trade_qty", "trade_side", "trade_id").to_dicts() == [
        {"trade_price": 100.0, "trade_qty": 2.0, "trade_side": 1.0, "trade_id": "10"},
    ]


def test_trade_tick_no_trade_id_load_events_preserves_same_business_rows(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    ts_event = _T0_NS + 10 * 1_000_000_000
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": ts_event, "price": 100.0, "size": 2.0, "aggressor_side": "BUYER"},
        {"ts_event": ts_event, "price": 100.0, "size": 2.0, "aggressor_side": "BUYER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    events = dl.load_events(symbol=symbol, source="trade_tick", fields=("trade_qty",))

    assert events.columns == ["ts", "trade_qty"]
    assert events.to_dicts() == [
        {"ts": datetime(2021, 5, 3, 0, 0, 10), "trade_qty": 2.0},
        {"ts": datetime(2021, 5, 3, 0, 0, 10), "trade_qty": 2.0},
    ]


def test_trade_tick_mixed_null_trade_id_load_events_preserves_same_business_rows(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    ts_event = _T0_NS + 10 * 1_000_000_000
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": ts_event, "price": 100.0, "size": 2.0, "aggressor_side": "BUYER", "trade_id": None},
        {"ts_event": ts_event, "price": 100.0, "size": 2.0, "aggressor_side": "BUYER", "trade_id": None},
        {"ts_event": ts_event, "price": 101.0, "size": 1.0, "aggressor_side": "SELLER", "trade_id": 10},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    events = dl.load_events(symbol=symbol, source="trade_tick", fields=("trade_qty", "trade_id"))

    assert events.columns == ["ts", "trade_qty", "trade_id"]
    assert events["trade_qty"].to_list() == [1.0, 2.0, 2.0]
    assert events["trade_id"].to_list() == ["10", None, None]


def test_trade_tick_no_trade_id_panel_aggregation_sums_same_business_rows(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    ts_event = _T0_NS + 10 * 1_000_000_000
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": ts_event, "price": 100.0, "size": 2.0, "aggressor_side": "BUYER"},
        {"ts_event": ts_event, "price": 100.0, "size": 2.0, "aggressor_side": "BUYER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    panel = dl.load_panel(
        DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick"),
        start="2021-05-03T00:00:00",
        end="2021-05-03T00:00:59.999000",
    )["trade_qty"]

    assert panel["ts"].to_list() == [datetime(2021, 5, 3, 0, 0, 59, 999000)]
    np.testing.assert_allclose(panel[symbol].to_numpy(), [4.0])


def test_trade_tick_same_timestamp_last_price_uses_trade_id_order(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    ts_event = _T0_NS + 10 * 1_000_000_000
    _write_trade_ticks(resolve_catalog_path(tmp_path, "aggTrades"), symbol, [
        {"ts_event": ts_event, "price": 101.0, "size": 1.0, "aggressor_side": "BUYER", "trade_id": 2},
        {"ts_event": ts_event, "price": 100.0, "size": 1.0, "aggressor_side": "SELLER", "trade_id": 1},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    price_panel = dl.load_panel(
        DataRequest(symbol, "trade_price", "1m", 0, "trade_tick"),
        start="2021-05-03T00:00:00",
        end="2021-05-03T00:00:59.999000",
    )["trade_price"]
    side_panel = dl.load_panel(
        DataRequest(symbol, "trade_side", "1m", 0, "trade_tick"),
        start="2021-05-03T00:00:00",
        end="2021-05-03T00:00:59.999000",
    )["trade_side"]

    np.testing.assert_allclose(price_panel[symbol].to_numpy(), [101.0])
    np.testing.assert_allclose(side_panel[symbol].to_numpy(), [1.0])


def test_trade_tick_explicit_trades_does_not_read_legacy_flat_agg_trades(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "price": 100.0, "size": 99.0, "aggressor_side": "BUYER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick", source_type="trades")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:00:59.999000")["trade_qty"]

    assert panel.is_empty()


def test_trade_tick_implicit_source_fallback_continues_after_pruned_root(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(resolve_catalog_path(tmp_path, "aggTrades"), symbol, [
        {"ts_event": _T0_NS - 86_400 * 1_000_000_000, "price": 200.0, "size": 99.0, "aggressor_side": "BUYER"},
    ])
    _write_trade_ticks(resolve_catalog_path(tmp_path, "trades"), symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "price": 100.0, "size": 2.0, "aggressor_side": "BUYER"},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "trade_qty", "1m", 0, "trade_tick")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:00:59.999000")["trade_qty"]

    assert panel.height == 1
    np.testing.assert_allclose(panel[symbol].to_numpy(), [2.0])


def test_quote_tick_implicit_root_fallback_continues_after_pruned_root(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_quote_ticks(resolve_catalog_path(tmp_path, "bookTicker"), symbol, [
        {"ts_event": _T0_NS - 86_400 * 1_000_000_000, "bid_price": 90.0, "bid_size": 1.0, "ask_price": 110.0, "ask_size": 1.0},
    ])
    _write_quote_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "bid_price": 99.0, "bid_size": 3.0, "ask_price": 101.0, "ask_size": 1.0},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "mid_price", "1m", 0, "quote_tick")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:00:59.999000")["mid_price"]

    assert panel.height == 1
    np.testing.assert_allclose(panel[symbol].to_numpy(), [100.0])


def test_quote_tick_implicit_root_uses_source_aware_only_when_available(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_quote_ticks(resolve_catalog_path(tmp_path, "bookTicker"), symbol, [
        {"ts_event": _T0_NS + 10 * 1_000_000_000, "bid_price": 99.0, "bid_size": 3.0, "ask_price": 101.0, "ask_size": 1.0},
    ])
    _write_quote_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS + 70 * 1_000_000_000, "bid_price": 109.0, "bid_size": 1.0, "ask_price": 111.0, "ask_size": 1.0},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)
    req = DataRequest(symbol, "mid_price", "1m", 0, "quote_tick")

    panel = dl.load_panel(req, start="2021-05-03T00:00:00", end="2021-05-03T00:01:59.999000")["mid_price"]

    assert panel["ts"].to_list() == [datetime(2021, 5, 3, 0, 0, 59, 999000)]
    np.testing.assert_allclose(panel[symbol].to_numpy(), [100.0])


def test_quote_tick_overlap_prefers_source_aware_for_panel_and_events(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    ts_event = _T0_NS + 10 * 1_000_000_000
    _write_quote_ticks(resolve_catalog_path(tmp_path, "bookTicker"), symbol, [
        {"ts_event": ts_event, "bid_price": 99.0, "bid_size": 3.0, "ask_price": 101.0, "ask_size": 1.0, "update_id": 2},
    ])
    _write_quote_ticks(tmp_path, symbol, [
        {"ts_event": ts_event, "bid_price": 1.0, "bid_size": 1.0, "ask_price": 3.0, "ask_size": 1.0},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    panel = dl.load_panel(
        DataRequest(symbol, "mid_price", "1m", 0, "quote_tick"),
        start="2021-05-03T00:00:00",
        end="2021-05-03T00:00:59.999000",
    )["mid_price"]
    events = dl.load_events(symbol=symbol, source="quote_tick", fields=("bid_price", "ask_price", "mid_price"))

    np.testing.assert_allclose(panel[symbol].to_numpy(), [100.0])
    assert events.select("bid_price", "ask_price", "mid_price").to_dicts() == [
        {"bid_price": 99.0, "ask_price": 101.0, "mid_price": 100.0},
    ]


def test_quote_tick_explicit_book_ticker_source_uses_same_overlap_rules(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    ts_event = _T0_NS + 10 * 1_000_000_000
    _write_quote_ticks(resolve_catalog_path(tmp_path, "bookTicker"), symbol, [
        {"ts_event": ts_event, "bid_price": 99.0, "bid_size": 3.0, "ask_price": 101.0, "ask_size": 1.0, "update_id": 2},
    ])
    _write_quote_ticks(tmp_path, symbol, [
        {"ts_event": ts_event, "bid_price": 1.0, "bid_size": 1.0, "ask_price": 3.0, "ask_size": 1.0},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    panel = dl.load_panel(
        DataRequest(symbol, "mid_price", "1m", 0, "quote_tick", source_type="bookTicker"),
        start="2021-05-03T00:00:00",
        end="2021-05-03T00:00:59.999000",
    )["mid_price"]
    events = dl.load_events(
        symbol=symbol,
        source="quote_tick",
        fields=("bid_price", "ask_price", "mid_price"),
        source_type="bookTicker",
    )

    np.testing.assert_allclose(panel[symbol].to_numpy(), [100.0])
    assert events.select("bid_price", "ask_price", "mid_price").to_dicts() == [
        {"bid_price": 99.0, "ask_price": 101.0, "mid_price": 100.0},
    ]


def test_quote_tick_same_timestamp_without_update_id_preserves_distinct_events(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    ts_event = _T0_NS + 10 * 1_000_000_000
    _write_quote_ticks(resolve_catalog_path(tmp_path, "bookTicker"), symbol, [
        {"ts_event": ts_event, "bid_price": 89.0, "bid_size": 1.0, "ask_price": 91.0, "ask_size": 1.0},
        {"ts_event": ts_event, "bid_price": 99.0, "bid_size": 1.0, "ask_price": 101.0, "ask_size": 1.0},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    events = dl.load_events(symbol=symbol, source="quote_tick", fields=("bid_price", "ask_price", "mid_price"))
    panel = dl.load_panel(
        DataRequest(symbol, "mid_price", "1m", 0, "quote_tick"),
        start="2021-05-03T00:00:00",
        end="2021-05-03T00:00:59.999000",
    )["mid_price"]

    assert events.select("bid_price", "ask_price", "mid_price").to_dicts() == [
        {"bid_price": 89.0, "ask_price": 91.0, "mid_price": 90.0},
        {"bid_price": 99.0, "ask_price": 101.0, "mid_price": 100.0},
    ]
    np.testing.assert_allclose(panel[symbol].to_numpy(), [100.0])


def test_quote_tick_same_timestamp_last_quote_uses_update_id_order(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    ts_event = _T0_NS + 10 * 1_000_000_000
    _write_quote_ticks(resolve_catalog_path(tmp_path, "bookTicker"), symbol, [
        {"ts_event": ts_event, "bid_price": 99.0, "bid_size": 1.0, "ask_price": 101.0, "ask_size": 1.0, "update_id": 2},
        {"ts_event": ts_event, "bid_price": 89.0, "bid_size": 1.0, "ask_price": 91.0, "ask_size": 1.0, "update_id": 1},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    panel = dl.load_panel(
        DataRequest(symbol, "mid_price", "1m", 0, "quote_tick"),
        start="2021-05-03T00:00:00",
        end="2021-05-03T00:00:59.999000",
    )["mid_price"]

    np.testing.assert_allclose(panel[symbol].to_numpy(), [100.0])


def test_load_events_derived_trade_field_requires_aggressor_side(tmp_path: Path) -> None:
    symbol = "BTCUSDT-PERP"
    _write_trade_ticks(tmp_path, symbol, [
        {"ts_event": _T0_NS + 1_000_000_000, "price": 100.0, "size": 1.0},
    ])
    dl = DataLayer(Universe.from_symbols([symbol]), catalog_root=tmp_path)

    with pytest.raises(ValueError, match="aggressor_side"):
        dl.load_events(symbol=symbol, source="trade_tick", fields=("buy_qty",))


def test_trade_tick_required_columns_are_minimal() -> None:
    from tinohelm.factor.data_layer import _trade_tick_required_columns

    assert _trade_tick_required_columns(["trade_imbalance"]) == ["ts_event", "size", "trade_qty", "aggressor_side"]
    assert "trade_id" not in _trade_tick_required_columns(["trade_imbalance"])
    assert _trade_tick_required_columns(["trade_price"]) == ["ts_event", "price", "trade_price"]


def test_tick_file_pruning_excludes_non_overlapping_files(tmp_path: Path) -> None:
    from tinohelm.factor.data_layer import _prune_parquet_files_by_time

    old_file = tmp_path / "old.parquet"
    new_file = tmp_path / "new.parquet"
    pl.DataFrame({"ts_event": [_T0_NS - 10 * _1MIN_NS], "size": [1.0]}).write_parquet(old_file)
    pl.DataFrame({"ts_event": [_T0_NS + 10 * _1MIN_NS], "size": [1.0]}).write_parquet(new_file)

    files = _prune_parquet_files_by_time([old_file, new_file], datetime(2021, 5, 3, 0, 9), datetime(2021, 5, 3, 0, 11))

    assert files == [new_file]
