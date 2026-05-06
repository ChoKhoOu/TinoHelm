from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from pydantic import SecretStr

from tinohelm.core.config import Settings, StorageSettings, TosStorageSettings
from tinohelm.data.storage import (
    LocalCatalogStorage,
    S3CatalogStorage,
    TosCatalogStorage,
    _default_tos_endpoint,
    delete_prefix,
    get_catalog_storage,
)


class _FakeS3File(BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.closed_flag = False

    def close(self) -> None:
        self.closed_flag = True
        super().close()


class _WritableS3File(BytesIO):
    def __init__(self, fs: "_FakeS3FileSystem", path: str) -> None:
        super().__init__()
        self._fs = fs
        self._path = path

    def close(self) -> None:
        if not self.closed:
            self._fs.objects[self._path] = self.getvalue()
        super().close()


class _FakeS3FileSystem:
    def __init__(self, objects: dict[str, bytes]) -> None:
        # keys are bucket-relative fsspec paths: bucket/key
        self.objects = dict(objects)
        self.open_calls: list[tuple[str, str]] = []
        self.put_calls: list[tuple[str, str]] = []
        self.copy_calls: list[tuple[str, str]] = []
        self.rm_calls: list[str] = []

    def info(self, path: str) -> dict:
        if path not in self.objects:
            raise FileNotFoundError(path)
        return {"name": path, "size": len(self.objects[path]), "type": "file", "ETag": f"etag-{path}"}

    def find(self, prefix: str, withdirs: bool = False, detail: bool = True):
        matches = {
            path: self.info(path)
            for path in sorted(self.objects)
            if path.startswith(prefix.rstrip("/") + "/") or path == prefix.rstrip("/")
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
            return _FakeS3File(self.objects[path])
        if mode == "wb":
            return _WritableS3File(self, path)
        raise AssertionError(f"unexpected open mode: {mode!r}")

    def put_file(self, local_path: str, remote_path: str) -> None:
        self.put_calls.append((local_path, remote_path))
        self.objects[remote_path] = Path(local_path).read_bytes()

    def copy(self, source: str, dest: str) -> None:
        self.copy_calls.append((source, dest))
        self.objects[dest] = self.objects[source]

    def rm(self, path: str) -> None:
        self.rm_calls.append(path)
        if path not in self.objects:
            raise FileNotFoundError(path)
        del self.objects[path]


def _settings(**overrides) -> TosStorageSettings:
    values = {
        "region": "cn-beijing",
        "bucket": "bucket-a",
        "prefix": "dataset/root",
        "access_key": SecretStr("ak"),
        "secret_key": SecretStr("sk"),
    }
    values.update(overrides)
    return TosStorageSettings(**values)


def test_tos_default_endpoint_uses_s3_specific_same_region_domain() -> None:
    assert _default_tos_endpoint("cn-beijing", use_internal_endpoint=True) == "https://tos-s3-cn-beijing.ivolces.com"
    assert _default_tos_endpoint("cn-beijing", use_internal_endpoint=False) == "https://tos-s3-cn-beijing.volces.com"


def test_tos_provider_exposes_s3_catalog_uri_and_nt_options(tmp_path: Path) -> None:
    storage = TosCatalogStorage(_settings(), filesystem=_FakeS3FileSystem({}), catalog_root=tmp_path / "catalog")

    assert isinstance(storage, S3CatalogStorage)
    assert storage.provider == "s3"
    assert storage.catalog_uri == "s3://bucket-a/dataset/root/catalog"
    assert storage.nt_catalog_path == "bucket-a/dataset/root/catalog"
    assert storage.fs_protocol == "s3"
    assert storage.fs_storage_options["endpoint_url"] == "https://tos-s3-cn-beijing.ivolces.com"
    assert storage.fs_storage_options["key"] == "ak"
    assert storage.fs_storage_options["secret"] == "sk"
    assert storage.fs_storage_options["config_kwargs"]["signature_version"] == "s3v4"
    assert storage.fs_storage_options["config_kwargs"]["s3"]["addressing_style"] == "virtual"
    assert storage.fs_rust_storage_options["endpoint_url"] == "https://tos-s3-cn-beijing.ivolces.com"
    assert storage.fs_rust_storage_options["region"] == "cn-beijing"
    assert storage.fs_rust_storage_options["access_key_id"] == "ak"
    assert storage.fs_rust_storage_options["secret_access_key"] == "sk"
    assert storage.fs_rust_storage_options["virtual_hosted_style_request"] == "true"


def test_tos_endpoint_override_normalizes_scheme(tmp_path: Path) -> None:
    storage = TosCatalogStorage(
        _settings(endpoint="tos-s3-cn-shanghai.volces.com"),
        filesystem=_FakeS3FileSystem({}),
        catalog_root=tmp_path / "catalog",
    )

    assert storage.endpoint_url == "https://tos-s3-cn-shanghai.volces.com"


def test_tos_iter_files_lists_s3_objects_without_creating_cache_dirs(tmp_path: Path) -> None:
    objects = {
        "bucket-a/dataset/root/catalog/data/bar/BTC/a.parquet": b"a",
        "bucket-a/dataset/root/catalog/data/bar/BTC/b.parquet": b"bb",
        "bucket-a/dataset/root/catalog/data/bar/ETH/c.parquet": b"ccc",
    }
    fs = _FakeS3FileSystem(objects)
    storage = TosCatalogStorage(_settings(), filesystem=fs, catalog_root=tmp_path / "catalog")

    files = list(storage.iter_files(storage.catalog_root / "data" / "bar" / "BTC", suffix=".parquet"))

    assert [file.path.name for file in files] == ["a.parquet", "b.parquet"]
    assert [file.size for file in files] == [1, 2]
    assert [file.uri for file in files] == [
        "s3://bucket-a/dataset/root/catalog/data/bar/BTC/a.parquet",
        "s3://bucket-a/dataset/root/catalog/data/bar/BTC/b.parquet",
    ]
    assert not (storage.catalog_root / "data").exists()


def test_tos_open_input_file_reads_from_s3_filesystem_without_local_cache(tmp_path: Path) -> None:
    key_path = "bucket-a/dataset/root/catalog/data/bar/BTC/bars.parquet"
    fs = _FakeS3FileSystem({key_path: b"payload"})
    storage = TosCatalogStorage(_settings(), filesystem=fs, catalog_root=tmp_path / "catalog")
    logical_path = storage.catalog_root / "data" / "bar" / "BTC" / "bars.parquet"

    with storage.open_input_file(logical_path) as fh:
        assert fh.read() == b"payload"

    assert fs.open_calls == [(key_path, "rb")]
    assert not logical_path.exists()


def test_stage_prefix_for_local_consumer_does_not_materialize_remote_catalog(tmp_path: Path) -> None:
    objects = {
        "bucket-a/dataset/root/catalog/data/bar/BTC/a.parquet": b"aaa",
        "bucket-a/dataset/root/catalog/data/bar/BTC/b.parquet": b"bbb",
    }
    storage = TosCatalogStorage(_settings(), filesystem=_FakeS3FileSystem(objects), catalog_root=tmp_path / "catalog")
    logical_prefix = storage.catalog_root / "data" / "bar" / "BTC"

    staged_prefix = storage.materialize_path(logical_prefix)

    assert staged_prefix == logical_prefix
    assert not logical_prefix.exists()


def test_tos_upload_path_returns_s3_uri(tmp_path: Path) -> None:
    fs = _FakeS3FileSystem({})
    storage = TosCatalogStorage(_settings(), filesystem=fs, catalog_root=tmp_path / "catalog")
    local_path = tmp_path / "staging" / "data" / "bar" / "BTC" / "bars.parquet"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"payload")

    uri = storage.upload_path(local_path, logical_path=storage.catalog_root / "data" / "bar" / "BTC" / "bars.parquet")

    assert uri == "s3://bucket-a/dataset/root/catalog/data/bar/BTC/bars.parquet"
    assert fs.put_calls == [(str(local_path), "bucket-a/dataset/root/catalog/data/bar/BTC/bars.parquet")]
    assert fs.objects["bucket-a/dataset/root/catalog/data/bar/BTC/bars.parquet"] == b"payload"


def test_tos_upload_bytes_returns_s3_uri_without_local_materialization(tmp_path: Path) -> None:
    fs = _FakeS3FileSystem({})
    storage = TosCatalogStorage(_settings(), filesystem=fs, catalog_root=tmp_path / "catalog")
    logical_path = storage.catalog_root / "data" / "bar" / "BTC" / "bars.parquet"

    uri = storage.upload_bytes(logical_path, b"payload")

    remote_key = "bucket-a/dataset/root/catalog/data/bar/BTC/bars.parquet"
    assert uri == "s3://bucket-a/dataset/root/catalog/data/bar/BTC/bars.parquet"
    assert fs.open_calls == [(remote_key, "wb")]
    assert fs.objects[remote_key] == b"payload"
    assert not logical_path.exists()
    assert not (storage.catalog_root / "data").exists()


def test_tos_copy_path_copies_object_without_local_materialization(tmp_path: Path) -> None:
    source_key = "bucket-a/dataset/root/catalog/data/bar/BTC/source.parquet"
    dest_key = "bucket-a/dataset/root/catalog/data/bar/BTC/dest.parquet"
    fs = _FakeS3FileSystem({source_key: b"payload"})
    storage = TosCatalogStorage(_settings(), filesystem=fs, catalog_root=tmp_path / "catalog")
    source = storage.catalog_root / "data" / "bar" / "BTC" / "source.parquet"
    dest = storage.catalog_root / "data" / "bar" / "BTC" / "dest.parquet"

    uri = storage.copy_path(source, dest)

    assert uri == "s3://bucket-a/dataset/root/catalog/data/bar/BTC/dest.parquet"
    assert fs.copy_calls == [(source_key, dest_key)]
    assert fs.objects[source_key] == b"payload"
    assert fs.objects[dest_key] == b"payload"
    assert not source.exists()
    assert not dest.exists()


def test_tos_path_mapping_rejects_paths_outside_logical_catalog_root(tmp_path: Path) -> None:
    storage = TosCatalogStorage(_settings(), filesystem=_FakeS3FileSystem({}), catalog_root=tmp_path / "catalog")

    with pytest.raises(ValueError, match="outside catalog root"):
        storage.uri_for_path(tmp_path / "outside.parquet")


def test_delete_prefix_removes_s3_objects_without_local_materialization(tmp_path: Path) -> None:
    objects = {
        "bucket-a/dataset/root/catalog/data/bar/BTC/a.parquet": b"a",
        "bucket-a/dataset/root/catalog/data/bar/BTC/b.parquet": b"bb",
    }
    fs = _FakeS3FileSystem(objects)
    storage = TosCatalogStorage(_settings(), filesystem=fs, catalog_root=tmp_path / "catalog")

    deleted, freed = delete_prefix(storage, storage.catalog_root / "data" / "bar" / "BTC")

    assert deleted == 2
    assert freed == 3
    assert fs.rm_calls == [
        "bucket-a/dataset/root/catalog/data/bar/BTC/a.parquet",
        "bucket-a/dataset/root/catalog/data/bar/BTC/b.parquet",
    ]
    assert not fs.objects
    assert not (storage.catalog_root / "data").exists()


def test_delete_prefix_treats_dotted_bar_type_path_as_prefix(tmp_path: Path) -> None:
    bar_type = "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
    key_path = f"bucket-a/dataset/root/catalog/data/bar/{bar_type}/part-0.parquet"
    fs = _FakeS3FileSystem({key_path: b"payload"})
    storage = TosCatalogStorage(_settings(), filesystem=fs, catalog_root=tmp_path / "catalog")

    deleted, freed = delete_prefix(storage, storage.catalog_root / "data" / "bar" / bar_type)

    assert (deleted, freed) == (1, 7)
    assert fs.rm_calls == [key_path]
    assert not fs.objects
    assert not (storage.catalog_root / "data").exists()


def test_get_catalog_storage_local_uses_configured_catalog_path(tmp_path: Path) -> None:
    settings = Settings(storage=StorageSettings(provider="local"))
    settings.paths.catalog = tmp_path / "catalog"

    storage = get_catalog_storage(settings=settings)

    assert isinstance(storage, LocalCatalogStorage)
    assert storage.catalog_root == tmp_path / "catalog"


def test_get_catalog_storage_tos_uses_logical_catalog_path(tmp_path: Path) -> None:
    settings = Settings(storage=StorageSettings(provider="tos", tos=_settings()))
    settings.paths.catalog = tmp_path / "catalog"

    storage = get_catalog_storage(settings=settings)

    assert isinstance(storage, S3CatalogStorage)
    assert storage.catalog_root == tmp_path / "catalog"
    assert storage.catalog_uri == "s3://bucket-a/dataset/root/catalog"


def test_get_catalog_storage_s3_aliases_tos_settings(tmp_path: Path) -> None:
    settings = Settings(storage=StorageSettings(provider="s3", tos=_settings(prefix="prod")))
    settings.paths.catalog = tmp_path / "catalog"

    storage = get_catalog_storage(settings=settings)

    assert isinstance(storage, S3CatalogStorage)
    assert storage.catalog_uri == "s3://bucket-a/prod/catalog"
