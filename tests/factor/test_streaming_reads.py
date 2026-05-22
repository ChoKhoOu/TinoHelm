from __future__ import annotations

from io import BytesIO
from pathlib import Path

import polars as pl

from tinohelm.data.storage import TosCatalogStorage
from tinohelm.factor.data_layer import DataLayer
from tinohelm.factor.universe import Universe
from tinohelm.strategy.loader_helpers import make_bar_type_str

from tests.data.test_storage_provider import _FakeS3FileSystem, _settings
from tests.factor.test_data_layer import _T0_NS, _1MIN_NS, _make_universe_csv


def test_bar_reader_uses_lazy_scan_and_streaming_collect(tmp_path: Path, monkeypatch) -> None:
    catalog_path = tmp_path / "catalog"
    bar_dir = catalog_path / "data" / "bar" / make_bar_type_str("BTCUSDT-PERP", "1m")
    bar_dir.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [_T0_NS, _T0_NS + _1MIN_NS],
        "close": [100.0, 101.0],
    }).write_parquet(bar_dir / "bars.parquet")

    uni_path = _make_universe_csv(tmp_path, [
        {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
    ])
    dl = DataLayer(Universe.load_csv(uni_path), catalog_root=catalog_path)

    original_scan = pl.scan_parquet
    original_collect = pl.LazyFrame.collect
    calls: dict[str, list] = {"scan": [], "collect_engine": []}

    def scan_spy(*args, **kwargs):
        calls["scan"].append((args, kwargs))
        return original_scan(*args, **kwargs)

    def collect_spy(self, *args, **kwargs):
        calls["collect_engine"].append(kwargs.get("engine"))
        return original_collect(self, *args, **kwargs)

    monkeypatch.setattr(pl, "scan_parquet", scan_spy)
    monkeypatch.setattr(pl.LazyFrame, "collect", collect_spy)

    series = dl._load_bar_field("BTCUSDT-PERP", "close", "1m", None, None)

    assert series["value"].to_list() == [100.0, 101.0]
    assert calls["scan"]
    assert "streaming" in calls["collect_engine"]


def test_tos_bar_reader_uses_s3_filesystem_without_local_cache(tmp_path: Path, monkeypatch) -> None:
    catalog_path = tmp_path / "catalog"
    bar_type = make_bar_type_str("BTCUSDT-PERP", "1m")
    payload = BytesIO()
    pl.DataFrame({
        "ts_event": [_T0_NS, _T0_NS + _1MIN_NS],
        "close": [100.0, 101.0],
    }).write_parquet(payload)
    remote_path = f"bucket-a/dataset/root/catalog/data/bar/{bar_type}/bars.parquet"
    fs = _FakeS3FileSystem({remote_path: payload.getvalue()})
    storage = TosCatalogStorage(_settings(), filesystem=fs, catalog_root=catalog_path)

    import tinohelm.data.storage as storage_module

    monkeypatch.setattr(storage_module, "get_catalog_storage", lambda **_kwargs: storage)
    uni_path = _make_universe_csv(tmp_path, [
        {"symbol": "BTCUSDT-PERP", "listing_date": "2020-01-01", "delisting_date": ""},
    ])
    dl = DataLayer(Universe.load_csv(uni_path), catalog_root=catalog_path)

    series = dl._load_bar_field("BTCUSDT-PERP", "close", "1m", None, None)

    assert series["value"].to_list() == [100.0, 101.0]
    assert fs.open_calls
    assert not (catalog_path / "data").exists()
