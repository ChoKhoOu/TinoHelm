from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from tinohelm.data.catalog_helpers import WRITABLE_CATEGORIES, resolve_catalog_path
from tinohelm.data.pipeline_helpers import WRITE_CATEGORY


@dataclass
class _Obj:
    ts_event: int


def test_book_ticker_resolves_to_base_path() -> None:
    assert WRITE_CATEGORY["bookTicker"] == "quote_tick"
    assert "quote_tick" in WRITABLE_CATEGORIES
    assert resolve_catalog_path("/tmp/cat", "bookTicker") == Path("/tmp/cat")


def test_write_objects_dispatches_book_ticker_to_quote_writer(monkeypatch, tmp_path: Path) -> None:
    pytest.importorskip("pandas")
    from tinohelm.data.pipeline import BinanceVisionPipeline

    called = {}

    def fake_writer(*, ticks, symbol, catalog_path, source_type, storage=None):
        called.update({
            "ticks": ticks,
            "symbol": symbol,
            "catalog_path": catalog_path,
            "source_type": source_type,
            "storage": storage,
        })
        return ["quote-file.parquet"]

    monkeypatch.setattr("tinohelm.data.catalog.write_quote_ticks", fake_writer)
    pipe = BinanceVisionPipeline(catalog_path=tmp_path / "catalog")

    out = pipe._write_objects([_Obj(1)], "BTCUSDT-PERP", "bookTicker", None)

    assert out == ["quote-file.parquet"]
    assert called["ticks"] == [_Obj(1)]
    assert called["symbol"] == "BTCUSDT-PERP"
    assert Path(called["catalog_path"]) == tmp_path / "catalog"
    assert called["source_type"] == "bookTicker"
    assert called["storage"].provider == "local"


