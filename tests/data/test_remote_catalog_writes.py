from __future__ import annotations

import sys
import types
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from pydantic import SecretStr

from tinohelm.core.config import TosStorageSettings
from tinohelm.data.pipeline import BinanceVisionPipeline
from tinohelm.data.storage import TosCatalogStorage


class _WritableS3File(BytesIO):
    def __init__(self, fs: "_WritableFakeS3FileSystem", path: str) -> None:
        super().__init__()
        self._fs = fs
        self._path = path

    def close(self) -> None:
        if not self.closed:
            self._fs.objects[self._path] = self.getvalue()
        super().close()


class _ReadableS3File(BytesIO):
    pass


class _WritableFakeS3FileSystem:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.open_calls: list[tuple[str, str]] = []
        self.put_file_calls: list[tuple[str, str]] = []

    def info(self, path: str) -> dict:
        if path not in self.objects:
            raise FileNotFoundError(path)
        return {"name": path, "size": len(self.objects[path]), "type": "file"}

    def find(self, prefix: str, withdirs: bool = False, detail: bool = True):
        matches = {
            path: self.info(path)
            for path in sorted(self.objects)
            if path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/")
        }
        return matches if detail else list(matches)

    def ls(self, prefix: str, detail: bool = True):
        prefix = prefix.rstrip("/")
        out = []
        for path in sorted(self.objects):
            if not path.startswith(prefix + "/"):
                continue
            rel = path[len(prefix) + 1 :]
            if "/" in rel:
                continue
            out.append(self.info(path) if detail else path)
        return out

    def open(self, path: str, mode: str = "rb"):
        self.open_calls.append((path, mode))
        if mode == "rb":
            return _ReadableS3File(self.objects[path])
        if mode == "wb":
            return _WritableS3File(self, path)
        raise AssertionError(f"unexpected open mode: {mode!r}")

    def put_file(self, local_path: str, remote_path: str) -> None:
        self.put_file_calls.append((local_path, remote_path))
        raise AssertionError("remote writes must not require a local staged file")


def _tos_settings(**overrides) -> TosStorageSettings:
    values = {
        "region": "cn-beijing",
        "bucket": "bucket-a",
        "prefix": "dataset/root",
        "access_key": SecretStr("ak"),
        "secret_key": SecretStr("sk"),
    }
    values.update(overrides)
    return TosStorageSettings(**values)


def _parquet_bytes(frame: pl.DataFrame) -> bytes:
    buf = BytesIO()
    frame.write_parquet(buf)
    return buf.getvalue()


def test_tos_upload_bytes_writes_object_without_materializing_logical_path(tmp_path: Path) -> None:
    fs = _WritableFakeS3FileSystem()
    storage = TosCatalogStorage(_tos_settings(), filesystem=fs, catalog_root=tmp_path / "catalog")
    logical_path = storage.catalog_root / "data" / "bar" / "BTC" / "bars.parquet"

    uri = storage.upload_bytes(logical_path, b"payload")

    remote_key = "bucket-a/dataset/root/catalog/data/bar/BTC/bars.parquet"
    assert uri == "s3://bucket-a/dataset/root/catalog/data/bar/BTC/bars.parquet"
    assert fs.open_calls == [(remote_key, "wb")]
    assert fs.objects[remote_key] == b"payload"
    assert not logical_path.exists()
    assert not (storage.catalog_root / "data").exists()


def test_write_trade_ticks_remote_storage_constructs_catalog_without_from_uri_host_leak(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tinohelm.data.catalog import write_trade_ticks

    symbol = "BTCUSDT-PERP"
    source_type = "aggTrades"
    instrument = SimpleNamespace(id="BTCUSDT-PERP.BINANCE")
    resolved_root = tmp_path
    tick_dir = resolved_root / "data" / "trade_tick" / str(instrument.id)
    written_path = tick_dir / "part-0.parquet"

    class RemoteStorage:
        provider = "s3"
        catalog_root = tmp_path
        fs_storage_options = {
            "endpoint_url": "https://tos-cn-beijing.volces.com",
            "client_kwargs": {"endpoint_url": "https://tos-cn-beijing.volces.com"},
        }
        fs_rust_storage_options = {"endpoint_url": "https://tos-cn-beijing.volces.com"}

        def __init__(self):
            self.iter_calls = 0

        def uri_for_catalog_root(self, logical_root):
            assert Path(logical_root) == resolved_root
            return "s3://bucket/catalog"

        def iter_files(self, prefix, *, suffix="", recursive=True):
            assert Path(prefix) == tick_dir
            assert suffix == ".parquet"
            assert recursive is False
            self.iter_calls += 1
            if self.iter_calls == 1:
                return iter([])
            return iter([SimpleNamespace(path=written_path, size=7)])

    class FakeCatalog:
        init_calls: list[tuple[str, str | None, dict | None, dict | None]] = []
        from_uri_calls: list = []
        write_data_calls: list[tuple[list, bool]] = []

        def __init__(
            self,
            path,
            fs_protocol=None,
            fs_storage_options=None,
            fs_rust_storage_options=None,
        ):
            if str(path).startswith("s3://"):
                raise AssertionError("remote writer must pass bucket/key path, not an s3 URI")
            if fs_storage_options and "host" in fs_storage_options:
                raise AssertionError("s3fs options must not include fsspec's parsed host key")
            self.init_calls.append((path, fs_protocol, fs_storage_options, fs_rust_storage_options))

        @classmethod
        def from_uri(cls, *args, **kwargs):
            cls.from_uri_calls.append((args, kwargs))
            raise AssertionError("from_uri would merge fsspec's host into s3fs options")

        def write_data(self, data, skip_disjoint_check=False):
            self.write_data_calls.append((list(data), skip_disjoint_check))

    monkeypatch.setattr("tinohelm.data.instruments.make_instrument", lambda _symbol: instrument)
    nt_mod = types.ModuleType("nautilus_trader")
    persistence_mod = types.ModuleType("nautilus_trader.persistence")
    catalog_mod = types.ModuleType("nautilus_trader.persistence.catalog")
    catalog_mod.ParquetDataCatalog = FakeCatalog
    monkeypatch.setitem(sys.modules, "nautilus_trader", nt_mod)
    monkeypatch.setitem(sys.modules, "nautilus_trader.persistence", persistence_mod)
    monkeypatch.setitem(sys.modules, "nautilus_trader.persistence.catalog", catalog_mod)

    tick = SimpleNamespace(ts_init=1)
    paths = write_trade_ticks(
        [tick],
        symbol,
        tmp_path,
        source_type=source_type,
        storage=RemoteStorage(),
    )

    assert paths == [str(written_path)]
    assert FakeCatalog.from_uri_calls == []
    assert FakeCatalog.init_calls == [
        (
            "bucket/catalog",
            "s3",
            RemoteStorage.fs_storage_options,
            RemoteStorage.fs_rust_storage_options,
        )
    ]
    assert FakeCatalog.write_data_calls == [([tick], True)]
    assert not (tmp_path / "data").exists()


def test_write_trade_ticks_remote_storage_allows_overwrite_without_new_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tinohelm.data.catalog import write_trade_ticks

    symbol = "BTCUSDT-PERP"
    source_type = "aggTrades"
    instrument = SimpleNamespace(id="BTCUSDT-PERP.BINANCE")
    resolved_root = tmp_path
    tick_dir = resolved_root / "data" / "trade_tick" / str(instrument.id)
    written_path = tick_dir / "part-0.parquet"

    class RemoteStorage:
        provider = "s3"
        catalog_root = tmp_path
        fs_storage_options = {"endpoint_url": "https://tos-cn-beijing.volces.com"}
        fs_rust_storage_options = {"endpoint_url": "https://tos-cn-beijing.volces.com"}

        def __init__(self):
            self.iter_calls = 0

        def uri_for_catalog_root(self, logical_root):
            assert Path(logical_root) == resolved_root
            return "s3://bucket/catalog"

        def iter_files(self, prefix, *, suffix="", recursive=True):
            assert Path(prefix) == tick_dir
            assert suffix == ".parquet"
            assert recursive is False
            self.iter_calls += 1
            return iter([SimpleNamespace(path=written_path, size=7)])

    class FakeCatalog:
        write_data_calls: list[tuple[list, bool]] = []

        def __init__(self, *args, **kwargs):
            pass

        def write_data(self, data, skip_disjoint_check=False):
            self.write_data_calls.append((list(data), skip_disjoint_check))

    monkeypatch.setattr("tinohelm.data.instruments.make_instrument", lambda _symbol: instrument)
    nt_mod = types.ModuleType("nautilus_trader")
    persistence_mod = types.ModuleType("nautilus_trader.persistence")
    catalog_mod = types.ModuleType("nautilus_trader.persistence.catalog")
    catalog_mod.ParquetDataCatalog = FakeCatalog
    monkeypatch.setitem(sys.modules, "nautilus_trader", nt_mod)
    monkeypatch.setitem(sys.modules, "nautilus_trader.persistence", persistence_mod)
    monkeypatch.setitem(sys.modules, "nautilus_trader.persistence.catalog", catalog_mod)

    paths = write_trade_ticks(
        [SimpleNamespace(ts_init=1)],
        symbol,
        tmp_path,
        source_type=source_type,
        storage=RemoteStorage(),
    )

    assert paths == [str(written_path)]
    assert FakeCatalog.write_data_calls == [([SimpleNamespace(ts_init=1)], True)]



def test_write_trade_ticks_remote_storage_raises_when_write_produces_no_parquet(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tinohelm.data.catalog import write_trade_ticks

    symbol = "BTCUSDT-PERP"
    source_type = "aggTrades"
    instrument = SimpleNamespace(id="BTCUSDT-PERP.BINANCE")
    resolved_root = tmp_path
    tick_dir = resolved_root / "data" / "trade_tick" / str(instrument.id)

    class RemoteStorage:
        provider = "s3"
        catalog_root = tmp_path
        fs_storage_options = {"endpoint_url": "https://tos-cn-beijing.volces.com"}
        fs_rust_storage_options = {"endpoint_url": "https://tos-cn-beijing.volces.com"}

        def uri_for_catalog_root(self, logical_root):
            assert Path(logical_root) == resolved_root
            return "s3://bucket/catalog"

        def iter_files(self, prefix, *, suffix="", recursive=True):
            assert Path(prefix) == tick_dir
            assert suffix == ".parquet"
            assert recursive is False
            return iter([])

    class FakeCatalog:
        def __init__(self, *args, **kwargs):
            pass

        def write_data(self, data, skip_disjoint_check=False):
            pass

    monkeypatch.setattr("tinohelm.data.instruments.make_instrument", lambda _symbol: instrument)
    nt_mod = types.ModuleType("nautilus_trader")
    persistence_mod = types.ModuleType("nautilus_trader.persistence")
    catalog_mod = types.ModuleType("nautilus_trader.persistence.catalog")
    catalog_mod.ParquetDataCatalog = FakeCatalog
    monkeypatch.setitem(sys.modules, "nautilus_trader", nt_mod)
    monkeypatch.setitem(sys.modules, "nautilus_trader.persistence", persistence_mod)
    monkeypatch.setitem(sys.modules, "nautilus_trader.persistence.catalog", catalog_mod)

    with pytest.raises(RuntimeError, match="produced no parquet files"):
        write_trade_ticks(
            [SimpleNamespace(ts_init=1)],
            symbol,
            tmp_path,
            source_type=source_type,
            storage=RemoteStorage(),
        )



def test_write_bars_remote_storage_uses_nt_constructor_without_local_catalog(tmp_path: Path, monkeypatch) -> None:
    from tinohelm.data.catalog import write_bars

    symbol = "BTCUSDT-PERP"
    source_type = "klines"
    bar_type_str = "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
    resolved_root = tmp_path
    bar_dir = resolved_root / "data" / "bar" / bar_type_str
    written_path = bar_dir / "part-0.parquet"

    class RemoteStorage:
        provider = "s3"
        catalog_root = tmp_path
        fs_storage_options = {"endpoint_url": "https://example.com"}
        fs_rust_storage_options = {"endpoint_url": "https://example.com"}

        def uri_for_catalog_root(self, logical_root):
            assert Path(logical_root) == resolved_root
            return "s3://bucket/catalog"

        def iter_files(self, prefix, *, suffix="", recursive=True):
            assert Path(prefix) == bar_dir
            assert suffix == ".parquet"
            assert recursive is False
            return iter([SimpleNamespace(path=written_path, size=7)])

    class FakeCatalog:
        init_calls: list[tuple[str, str | None, dict | None, dict | None]] = []
        from_uri_calls: list = []
        write_data_calls: list[tuple[list, bool]] = []

        def __init__(self, path, fs_protocol=None, fs_storage_options=None, fs_rust_storage_options=None):
            self.init_calls.append((path, fs_protocol, fs_storage_options, fs_rust_storage_options))

        @classmethod
        def from_uri(cls, *args, **kwargs):
            cls.from_uri_calls.append((args, kwargs))
            raise AssertionError("remote writer must not use from_uri")

        def write_data(self, data, skip_disjoint_check=False):
            self.write_data_calls.append((list(data), skip_disjoint_check))

    class FakeBarType:
        def __str__(self) -> str:
            return bar_type_str

    instrument = SimpleNamespace(id="BTCUSDT-PERP.BINANCE")
    bar = SimpleNamespace(ts_event=1)
    monkeypatch.setattr("tinohelm.data.catalog._make_instrument", lambda _symbol: instrument)
    monkeypatch.setattr("tinohelm.data.catalog._make_bar_type", lambda _instrument_id, _interval: FakeBarType())
    nt_mod = types.ModuleType("nautilus_trader")
    persistence_mod = types.ModuleType("nautilus_trader.persistence")
    catalog_mod = types.ModuleType("nautilus_trader.persistence.catalog")
    catalog_mod.ParquetDataCatalog = FakeCatalog
    monkeypatch.setitem(sys.modules, "nautilus_trader", nt_mod)
    monkeypatch.setitem(sys.modules, "nautilus_trader.persistence", persistence_mod)
    monkeypatch.setitem(sys.modules, "nautilus_trader.persistence.catalog", catalog_mod)

    paths = write_bars(
        [bar],
        symbol,
        "1m",
        tmp_path,
        merge=False,
        source_type=source_type,
        storage=RemoteStorage(),
    )

    assert paths == [written_path]
    assert FakeCatalog.from_uri_calls == []
    assert FakeCatalog.init_calls == [
        (
            "bucket/catalog",
            "s3",
            {"endpoint_url": "https://example.com"},
            {"endpoint_url": "https://example.com"},
        )
    ]
    assert not (tmp_path / "data").exists()


