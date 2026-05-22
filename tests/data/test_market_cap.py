"""Tests for market-cap data loading via DataLayer and fetch_circulating_supply.

Coverage
--------
- fetch_circulating_supply: cache hit within 24 h → no HTTP call
- fetch_circulating_supply: cache expired (> 24 h) → new HTTP call
- fetch_circulating_supply: HTTP failure + stale cache → stale returned
- fetch_circulating_supply: HTTP failure + no cache + fallback constant
- DataLayer.load(source="market_cap"): returns non-empty Panel, mcap = close × supply
- Multi-symbol fetch: both symbols resolved independently
"""
from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from freezegun import freeze_time

from tinohelm.data.instruments import fetch_circulating_supply
from tinohelm.factor.data_layer import DataLayer
from tinohelm.factor.types import DataRequest
from tinohelm.factor.universe import Universe


# ---------------------------------------------------------------------------
# Shared test helpers (copied from test_data_layer.py pattern)
# ---------------------------------------------------------------------------

_T0_NS = 1_620_000_000_000_000_000  # 2021-05-03T00:00:00Z in nanoseconds
_1MIN_NS = 60 * 1_000_000_000       # 1 minute in nanoseconds


def _make_universe_csv(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "test_uni.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _make_bar_objects(symbol_str: str, timestamps_ns: list[int], closes: list[float]):
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


def _write_catalog_bars(
    catalog_path: Path,
    symbol_str: str,
    timestamps_ns: list[int],
    closes: list[float],
):
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    bars, _ = _make_bar_objects(symbol_str, timestamps_ns, closes)
    cat = ParquetDataCatalog(str(catalog_path))
    cat.write_data(bars)


def _stub_make_instrument(monkeypatch):
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue

    class _FakeInstrument:
        def __init__(self, symbol_str: str):
            self.id = InstrumentId(Symbol(symbol_str), Venue("BINANCE"))

    monkeypatch.setattr(
        "tinohelm.data.catalog._make_instrument",
        lambda symbol: _FakeInstrument(symbol),
    )


# ---------------------------------------------------------------------------
# Fake bapi response builder
# ---------------------------------------------------------------------------

def _make_bapi_response(symbol: str, supply: float) -> dict:
    """Build a minimal fake Binance bapi composite products response."""
    return {
        "data": [
            {"s": symbol, "cs": supply, "b": symbol.replace("USDT", "")},
        ]
    }


# ---------------------------------------------------------------------------
# Tests: fetch_circulating_supply cache behaviour
# ---------------------------------------------------------------------------

class TestFetchCirculatingSupplyCache:
    """Cache TTL and HTTP-call count verification via freezegun."""

    def _write_cache(self, cache_file: Path, symbol: str, supply: float, fetched_at: str):
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({symbol: {"supply": supply, "fetched_at": fetched_at}}),
            encoding="utf-8",
        )

    def test_cache_hit_within_24h_no_http_call(self, tmp_path: Path, monkeypatch):
        """Second call within 24 h must NOT issue a new HTTP request."""
        cache_file = tmp_path / "circulating_supply_cache.json"

        # Patch the cache file path and httpx.Client
        monkeypatch.setattr(
            "tinohelm.data.instruments._circulating_supply_cache_file",
            lambda: cache_file,
        )

        http_call_count = {"n": 0}

        def _fake_get(url, **kwargs):
            http_call_count["n"] += 1
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = _make_bapi_response("BTCUSDT", 19_700_000.0)
            return mock_resp

        fake_client = MagicMock()
        fake_client.__enter__ = MagicMock(return_value=fake_client)
        fake_client.__exit__ = MagicMock(return_value=False)
        fake_client.get = _fake_get

        with patch("httpx.Client", return_value=fake_client):
            # First call at T0: cache is empty → HTTP is called
            with freeze_time("2026-04-26 08:00:00"):
                result1 = fetch_circulating_supply("BTCUSDT")

            assert result1 == pytest.approx(19_700_000.0)
            assert http_call_count["n"] == 1

            # Second call at T0+14h (within 24h window): cache must be used
            with freeze_time("2026-04-26 22:00:00"):
                result2 = fetch_circulating_supply("BTCUSDT")

            assert result2 == pytest.approx(19_700_000.0)
            # Still only 1 HTTP call — cache was hit
            assert http_call_count["n"] == 1

    def test_cache_expired_after_24h_triggers_new_http_call(self, tmp_path: Path, monkeypatch):
        """Call after 24 h must issue a new HTTP request."""
        cache_file = tmp_path / "circulating_supply_cache.json"

        monkeypatch.setattr(
            "tinohelm.data.instruments._circulating_supply_cache_file",
            lambda: cache_file,
        )

        http_call_count = {"n": 0}

        def _fake_get(url, **kwargs):
            http_call_count["n"] += 1
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = _make_bapi_response("BTCUSDT", 19_750_000.0)
            return mock_resp

        fake_client = MagicMock()
        fake_client.__enter__ = MagicMock(return_value=fake_client)
        fake_client.__exit__ = MagicMock(return_value=False)
        fake_client.get = _fake_get

        with patch("httpx.Client", return_value=fake_client):
            # First call at T0
            with freeze_time("2026-04-26 08:00:00"):
                result1 = fetch_circulating_supply("BTCUSDT")

            assert http_call_count["n"] == 1

            # Third call at T0+25h (> 24h) — cache expired
            with freeze_time("2026-04-27 09:00:00"):
                result2 = fetch_circulating_supply("BTCUSDT")

            assert result2 == pytest.approx(19_750_000.0)
            assert http_call_count["n"] == 2

    def test_http_failure_returns_stale_cache(self, tmp_path: Path, monkeypatch):
        """When HTTP fails and stale cache exists, stale value is returned."""
        cache_file = tmp_path / "circulating_supply_cache.json"

        monkeypatch.setattr(
            "tinohelm.data.instruments._circulating_supply_cache_file",
            lambda: cache_file,
        )

        # Pre-populate cache with an entry already 30h old
        self._write_cache(
            cache_file,
            "BTCUSDT",
            supply=19_700_000.0,
            fetched_at="2026-04-25T03:00:00Z",
        )

        def _fake_get(url, **kwargs):
            raise ConnectionError("network unavailable")

        fake_client = MagicMock()
        fake_client.__enter__ = MagicMock(return_value=fake_client)
        fake_client.__exit__ = MagicMock(return_value=False)
        fake_client.get = _fake_get

        with patch("httpx.Client", return_value=fake_client):
            with freeze_time("2026-04-26 09:00:00"):
                # Cache is 30h old → expired, but HTTP fails → stale returned
                result = fetch_circulating_supply("BTCUSDT")

        assert result == pytest.approx(19_700_000.0)

    def test_http_failure_no_cache_uses_hardcoded_fallback(self, tmp_path: Path, monkeypatch):
        """When HTTP fails and no cache, hardcoded fallback is returned."""
        cache_file = tmp_path / "circulating_supply_cache.json"
        # Do not write any cache file

        monkeypatch.setattr(
            "tinohelm.data.instruments._circulating_supply_cache_file",
            lambda: cache_file,
        )

        def _fake_get(url, **kwargs):
            raise ConnectionError("network unavailable")

        fake_client = MagicMock()
        fake_client.__enter__ = MagicMock(return_value=fake_client)
        fake_client.__exit__ = MagicMock(return_value=False)
        fake_client.get = _fake_get

        with patch("httpx.Client", return_value=fake_client):
            result = fetch_circulating_supply("BTCUSDT-PERP")

        # Must match the hardcoded fallback constant
        assert result == pytest.approx(19_700_000.0)

    def test_unknown_symbol_no_cache_no_fallback_raises(self, tmp_path: Path, monkeypatch):
        """Unknown symbol with no cache and no fallback raises ValueError."""
        cache_file = tmp_path / "circulating_supply_cache.json"

        monkeypatch.setattr(
            "tinohelm.data.instruments._circulating_supply_cache_file",
            lambda: cache_file,
        )

        def _fake_get(url, **kwargs):
            raise ConnectionError("network unavailable")

        fake_client = MagicMock()
        fake_client.__enter__ = MagicMock(return_value=fake_client)
        fake_client.__exit__ = MagicMock(return_value=False)
        fake_client.get = _fake_get

        with patch("httpx.Client", return_value=fake_client):
            with pytest.raises(ValueError, match="circulating supply"):
                fetch_circulating_supply("UNKNOWNUSDT-PERP")


# ---------------------------------------------------------------------------
# Tests: DataLayer.load(source="market_cap")
# ---------------------------------------------------------------------------

class TestDataLayerMarketCap:
    """DataLayer routes source='market_cap' to _load_market_cap_field."""

    def _setup_catalog_and_universe(self, tmp_path: Path, symbol: str, closes: list[float]):
        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir(parents=True, exist_ok=True)

        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(len(closes))]
        _write_catalog_bars(catalog_path, symbol, ts_ns, closes)

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": symbol, "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)
        return catalog_path, uni, ts_ns

    def test_market_cap_panel_non_empty(self, tmp_path: Path, monkeypatch):
        """DataLayer.load with source='market_cap' returns a non-empty Panel."""
        _stub_make_instrument(monkeypatch)

        closes = [50_000.0, 51_000.0, 52_000.0, 53_000.0, 54_000.0]
        supply = 19_700_000.0
        catalog_path, uni, _ = self._setup_catalog_and_universe(
            tmp_path, "BTCUSDT-PERP", closes
        )

        # Patch fetch_circulating_supply to avoid HTTP
        monkeypatch.setattr(
            "tinohelm.data.instruments.fetch_circulating_supply",
            lambda symbol: supply,
        )

        dl = DataLayer(uni, catalog_root=catalog_path)
        req = DataRequest(
            symbol="BTCUSDT-PERP",
            field_name="market_cap",
            frequency="1m",
            lookback=0,
            source="market_cap",
        )
        panels = dl.load(req)

        assert "market_cap" in panels
        panel = panels["market_cap"]
        assert "BTCUSDT-PERP" in panel.columns
        assert panel.height == len(closes)

    def test_market_cap_values_equal_close_times_supply(self, tmp_path: Path, monkeypatch):
        """Each mcap bar = close × circulating_supply."""
        _stub_make_instrument(monkeypatch)

        closes = [50_000.0, 51_000.0, 52_000.0]
        supply = 19_700_000.0
        catalog_path, uni, _ = self._setup_catalog_and_universe(
            tmp_path, "BTCUSDT-PERP", closes
        )

        monkeypatch.setattr(
            "tinohelm.data.instruments.fetch_circulating_supply",
            lambda symbol: supply,
        )

        dl = DataLayer(uni, catalog_root=catalog_path)
        req = DataRequest(
            symbol="BTCUSDT-PERP",
            field_name="market_cap",
            frequency="1m",
            lookback=0,
            source="market_cap",
        )
        panels = dl.load(req)

        panel = panels["market_cap"]
        expected_mcap = [c * supply for c in closes]
        import numpy as np
        np.testing.assert_allclose(
            panel["BTCUSDT-PERP"].to_numpy(),
            expected_mcap,
            rtol=1e-4,
        )

    def test_market_cap_two_symbols(self, tmp_path: Path, monkeypatch):
        """Two-symbol market_cap load: both columns present with correct values."""
        _stub_make_instrument(monkeypatch)

        catalog_path = tmp_path / "catalog"
        catalog_path.mkdir(parents=True, exist_ok=True)

        btc_closes = [50_000.0, 51_000.0, 52_000.0]
        eth_closes = [3_000.0, 3_010.0, 3_020.0]
        ts_ns = [_T0_NS + i * _1MIN_NS for i in range(3)]

        _write_catalog_bars(catalog_path, "BTCUSDT-PERP", ts_ns, btc_closes)
        _write_catalog_bars(catalog_path, "ETHUSDT-PERP", ts_ns, eth_closes)

        uni_path = _make_universe_csv(tmp_path, [
            {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
            {"symbol": "ETHUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
        ])
        uni = Universe.load_csv(uni_path)

        supplies = {"BTCUSDT": 19_700_000.0, "ETHUSDT": 120_000_000.0}

        def _mock_fetch(symbol: str) -> float:
            from tinohelm.data.instruments import strip_to_binance_api_symbol
            return supplies[strip_to_binance_api_symbol(symbol)]

        monkeypatch.setattr(
            "tinohelm.data.instruments.fetch_circulating_supply",
            _mock_fetch,
        )

        dl = DataLayer(uni, catalog_root=catalog_path, max_workers=2)
        reqs = [
            DataRequest("BTCUSDT-PERP", "market_cap", "1m", 0, "market_cap"),
            DataRequest("ETHUSDT-PERP", "market_cap", "1m", 0, "market_cap"),
        ]
        panels = dl.load(reqs)

        assert "market_cap" in panels
        panel = panels["market_cap"]
        non_ts = {c for c in panel.columns if c != "ts"}
        assert non_ts == {"BTCUSDT-PERP", "ETHUSDT-PERP"}

        import numpy as np
        np.testing.assert_allclose(
            panel["BTCUSDT-PERP"].to_numpy(),
            [c * supplies["BTCUSDT"] for c in btc_closes],
            rtol=1e-4,
        )
        np.testing.assert_allclose(
            panel["ETHUSDT-PERP"].to_numpy(),
            [c * supplies["ETHUSDT"] for c in eth_closes],
            rtol=1e-4,
        )

    def test_market_cap_supply_fetch_failure_returns_empty(self, tmp_path: Path, monkeypatch):
        """If fetch_circulating_supply raises, the symbol column is empty (no crash)."""
        _stub_make_instrument(monkeypatch)

        closes = [50_000.0, 51_000.0]
        catalog_path, uni, _ = self._setup_catalog_and_universe(
            tmp_path, "BTCUSDT-PERP", closes
        )

        monkeypatch.setattr(
            "tinohelm.data.instruments.fetch_circulating_supply",
            lambda symbol: (_ for _ in ()).throw(ValueError("no supply")),
        )

        dl = DataLayer(uni, catalog_root=catalog_path)
        req = DataRequest(
            symbol="BTCUSDT-PERP",
            field_name="market_cap",
            frequency="1m",
            lookback=0,
            source="market_cap",
        )
        # Must not raise — empty series is returned and panel is empty
        panels = dl.load(req)
        panel = panels["market_cap"]
        # The symbol column may be present but all null, or panel is empty
        if "BTCUSDT-PERP" in panel.columns:
            null_count = panel["BTCUSDT-PERP"].null_count()
            assert null_count == panel.height or panel.is_empty()
        else:
            assert panel.is_empty()
