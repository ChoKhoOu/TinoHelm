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


def test_remote_single_file_metrics_uploads_merged_parquet_bytes_without_local_staging(tmp_path: Path) -> None:
    from tinohelm.data.catalog import metrics_parquet_path

    symbol = "BTCUSDT-PERP"
    logical_path = metrics_parquet_path(symbol, tmp_path)
    existing_payload = _parquet_bytes(pl.DataFrame({
        "symbol": [symbol],
        "ts_event": [1],
        "ts_init": [1],
        "open_interest": [10.0],
        "sum_open_interest": [10.0],
        "open_interest_value": [100.0],
        "toptrader_long_short_ratio_count": [0.0],
        "toptrader_long_short_ratio_sum": [0.0],
        "global_long_short_ratio": [0.0],
        "taker_long_short_vol_ratio": [0.0],
    }))
    uploaded: dict[Path, bytes] = {}

    class RemoteStorage:
        provider = "s3"
        catalog_root = tmp_path

        def exists(self, path):
            assert Path(path) == logical_path
            return True

        def read_bytes(self, path):
            assert Path(path) == logical_path
            return existing_payload

        def upload_bytes(self, path, payload: bytes):
            assert Path(path) == logical_path
            uploaded[Path(path)] = payload
            return "s3://bucket/catalog/metrics/metrics/data/metrics/btcusdt-perp.parquet"

        def upload_path(self, *_args, **_kwargs):  # pragma: no cover - must not be called
            raise AssertionError("remote metrics writes must not upload a local staged file")

    record = SimpleNamespace(
        symbol=symbol,
        ts_event=2,
        ts_init=2,
        open_interest=11.0,
        open_interest_value=110.0,
        toptrader_long_short_ratio_count=0.0,
        toptrader_long_short_ratio_sum=0.0,
        global_long_short_ratio=0.0,
        taker_long_short_vol_ratio=0.0,
    )
    pipeline = BinanceVisionPipeline(catalog_path=tmp_path)
    pipeline._storage = RemoteStorage()
    pipeline.catalog_path = str(tmp_path)

    paths = pipeline._write_objects([record], symbol, "metrics", None)

    assert paths == [str(logical_path)]
    assert set(uploaded) == {logical_path}
    assert not logical_path.exists()
    assert not (tmp_path / "metrics").exists()
    merged = pl.read_parquet(BytesIO(uploaded[logical_path]))
    assert merged.select("ts_event").to_series().to_list() == [1, 2]
    assert merged.select("open_interest").to_series().to_list() == [10.0, 11.0]


def test_remote_single_file_metrics_raises_on_existing_object_read_error(tmp_path: Path) -> None:
    from tinohelm.data.catalog import metrics_parquet_path

    symbol = "BTCUSDT-PERP"
    logical_path = metrics_parquet_path(symbol, tmp_path)

    class RemoteStorage:
        provider = "s3"
        catalog_root = tmp_path

        def exists(self, path):
            assert Path(path) == logical_path
            return True

        def read_bytes(self, path):
            assert Path(path) == logical_path
            raise ValueError("corrupted remote object")

        def upload_bytes(self, path, payload: bytes):  # pragma: no cover - must not be called
            raise AssertionError("remote metrics writes must not fall back to uploading the new batch only")

    record = SimpleNamespace(
        symbol=symbol,
        ts_event=2,
        ts_init=2,
        open_interest=11.0,
        open_interest_value=110.0,
        toptrader_long_short_ratio_count=0.0,
        toptrader_long_short_ratio_sum=0.0,
        global_long_short_ratio=0.0,
        taker_long_short_vol_ratio=0.0,
    )
    pipeline = BinanceVisionPipeline(catalog_path=tmp_path)
    pipeline._storage = RemoteStorage()
    pipeline.catalog_path = str(tmp_path)

    with pytest.raises(ValueError, match="corrupted remote object"):
        pipeline._write_objects([record], symbol, "metrics", None)


def test_write_bars_remote_storage_uses_nt_from_uri_without_local_catalog(tmp_path: Path, monkeypatch) -> None:
    from tinohelm.data.catalog import write_bars

    symbol = "BTCUSDT-PERP"
    source_type = "klines"
    bar_type_str = "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
    resolved_root = tmp_path / "bar" / source_type
    bar_dir = resolved_root / "data" / "bar" / bar_type_str
    written_path = bar_dir / "part-0.parquet"

    class RemoteStorage:
        provider = "s3"
        catalog_root = tmp_path
        fs_storage_options = {"endpoint_url": "https://example.com"}
        fs_rust_storage_options = {"endpoint_url": "https://example.com"}

        def uri_for_catalog_root(self, logical_root):
            assert Path(logical_root) == resolved_root
            return "s3://bucket/catalog/bar/klines"

        def iter_files(self, prefix, *, suffix="", recursive=True):
            assert Path(prefix) == bar_dir
            assert suffix == ".parquet"
            assert recursive is False
            return iter([SimpleNamespace(path=written_path, size=7)])

    class FakeCatalog:
        from_uri_calls: list[tuple[str, dict, dict]] = []
        write_data_calls: list[tuple[list, bool]] = []

        def __init__(self, catalog_path=None):
            raise AssertionError(f"local ParquetDataCatalog must not be used for remote storage: {catalog_path}")

        @classmethod
        def from_uri(cls, uri, fs_storage_options=None, fs_rust_storage_options=None):
            cls.from_uri_calls.append((uri, fs_storage_options, fs_rust_storage_options))
            return cls.__new__(cls)

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
    assert FakeCatalog.from_uri_calls == [
        (
            "s3://bucket/catalog/bar/klines",
            {"endpoint_url": "https://example.com"},
            {"endpoint_url": "https://example.com"},
        )
    ]
    assert not resolved_root.exists()


def test_write_bars_remote_merge_skips_deleting_fresh_parquet(tmp_path: Path, monkeypatch) -> None:
    from tinohelm.data.catalog import write_bars

    symbol = "BTCUSDT-PERP"
    source_type = "klines"
    bar_type_str = "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
    resolved_root = tmp_path / "bar" / source_type
    bar_dir = resolved_root / "data" / "bar" / bar_type_str
    bar_dir.mkdir(parents=True)

    same_name_file = bar_dir / "2024-01-01T00-00-00-000000000Z_2024-01-01T00-01-00-000000000Z.parquet"
    same_name_file.write_bytes(b"old-final")
    other_old = bar_dir / "old-b.parquet"
    other_old.write_bytes(b"old-b")
    deleted_paths: list[Path] = []
    copied_paths: list[tuple[Path, Path]] = []

    class RemoteStorage:
        provider = "s3"
        catalog_root = tmp_path
        fs_storage_options = {"endpoint_url": "https://example.com"}
        fs_rust_storage_options = {"endpoint_url": "https://example.com"}

        def uri_for_catalog_root(self, logical_root):
            logical_root = Path(logical_root)
            assert logical_root.is_relative_to(resolved_root)
            rel = logical_root.relative_to(tmp_path).as_posix()
            return f"s3://bucket/catalog/{rel}"

        def iter_files(self, prefix, *, suffix="", recursive=True):
            prefix = Path(prefix)
            if not prefix.exists():
                return iter(())
            iterator = prefix.rglob("*") if recursive else prefix.glob("*")
            files = [
                path
                for path in sorted(iterator)
                if path.is_file() and (not suffix or path.name.endswith(suffix))
            ]
            return iter(
                [
                    SimpleNamespace(path=path, size=path.stat().st_size)
                    for path in files
                ]
            )

        def copy_path(self, source_path, dest_path):
            source = Path(source_path)
            dest = Path(dest_path)
            copied_paths.append((source, dest))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source.read_bytes())
            rel = dest.relative_to(tmp_path).as_posix()
            return f"s3://bucket/catalog/{rel}"

        def delete_path(self, local_path):
            path = Path(local_path)
            deleted_paths.append(path)
            if path.exists():
                path.unlink()
            else:  # pragma: no cover - defensive
                raise FileNotFoundError(path)

    class FakeCatalog:
        from_uri_calls: list[tuple[str, dict, dict]] = []

        def __init__(self, catalog_path=None):
            self.catalog_path = catalog_path

        @classmethod
        def from_uri(cls, uri, fs_storage_options=None, fs_rust_storage_options=None):
            cls.from_uri_calls.append((uri, fs_storage_options, fs_rust_storage_options))
            return cls(uri)

        def bars(self, bar_types):
            assert bar_types == [bar_type_str]
            return [SimpleNamespace(ts_event=1), SimpleNamespace(ts_event=2)]

        def write_data(self, data, skip_disjoint_check=False):
            if data and hasattr(data[0], "ts_event"):
                rel = Path(str(self.catalog_path).removeprefix("s3://bucket/catalog/"))
                dest = tmp_path / rel / "data" / "bar" / bar_type_str / same_name_file.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"new")

    class FakeBarType:
        def __str__(self) -> str:
            return bar_type_str

    instrument = SimpleNamespace(id="BTCUSDT-PERP.BINANCE")
    bar = SimpleNamespace(ts_event=1)
    monkeypatch.setattr("tinohelm.data.catalog._make_instrument", lambda _symbol: instrument)
    monkeypatch.setattr("tinohelm.data.catalog._make_bar_type", lambda _instrument_id, _interval: FakeBarType())
    monkeypatch.setattr("uuid.uuid4", lambda: SimpleNamespace(hex="abc123"))
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
        merge=True,
        source_type=source_type,
        storage=RemoteStorage(),
    )

    temp_key = resolved_root / ".merge" / f"{bar_type_str}-abc123" / "data" / "bar" / bar_type_str / same_name_file.name
    rollback_dir = resolved_root / ".merge-rollback" / f"{bar_type_str}-abc123"
    rollback_same_key = rollback_dir / same_name_file.name
    rollback_other_key = rollback_dir / other_old.name
    assert paths == [same_name_file]
    assert same_name_file.read_bytes() == b"new"
    assert not other_old.exists()
    assert bar_dir.exists()
    assert copied_paths == [
        (same_name_file, rollback_same_key),
        (other_old, rollback_other_key),
        (temp_key, same_name_file),
    ]
    assert deleted_paths == [other_old, rollback_same_key, rollback_other_key, temp_key]


def test_write_bars_remote_merge_fails_closed_when_existing_catalog_read_fails(tmp_path: Path, monkeypatch) -> None:
    from tinohelm.data.catalog import write_bars

    symbol = "BTCUSDT-PERP"
    source_type = "klines"
    bar_type_str = "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
    resolved_root = tmp_path / "bar" / source_type
    bar_dir = resolved_root / "data" / "bar" / bar_type_str
    existing_path = bar_dir / "old.parquet"
    write_data_calls = []

    class RemoteStorage:
        provider = "s3"
        catalog_root = tmp_path
        fs_storage_options = {"endpoint_url": "https://example.com"}
        fs_rust_storage_options = {"endpoint_url": "https://example.com"}

        def uri_for_catalog_root(self, logical_root):
            assert Path(logical_root) == resolved_root
            return "s3://bucket/catalog/bar/klines"

        def iter_files(self, prefix, *, suffix="", recursive=True):
            assert Path(prefix) == bar_dir
            assert suffix == ".parquet"
            assert recursive is False
            return iter([SimpleNamespace(path=existing_path, size=9)])

        def copy_path(self, *_args, **_kwargs):  # pragma: no cover - must not be called
            raise AssertionError("remote merge must not promote after catalog read failure")

        def delete_path(self, *_args, **_kwargs):  # pragma: no cover - must not be called
            raise AssertionError("remote merge must not delete after catalog read failure")

    class FakeCatalog:
        def __init__(self, catalog_path=None):
            self.catalog_path = catalog_path

        @classmethod
        def from_uri(cls, uri, fs_storage_options=None, fs_rust_storage_options=None):
            assert uri == "s3://bucket/catalog/bar/klines"
            return cls(uri)

        def bars(self, bar_types):
            assert bar_types == [bar_type_str]
            raise RuntimeError("remote read failed")

        def write_data(self, data, skip_disjoint_check=False):  # pragma: no cover - must not be called
            write_data_calls.append((list(data), skip_disjoint_check))

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

    with pytest.raises(RuntimeError, match="remote read failed"):
        write_bars(
            [bar],
            symbol,
            "1m",
            tmp_path,
            merge=True,
            source_type=source_type,
            storage=RemoteStorage(),
        )

    assert write_data_calls == []
