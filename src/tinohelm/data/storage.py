"""Catalog storage providers backed by NT-compatible filesystem URIs.

The catalog layout stays NautilusTrader-native (``data/bar/...``,
``data/trade_tick/...`` etc.).  Remote Volcengine TOS storage is accessed
through its S3-compatible endpoint, so every logical remote catalog path is an
``s3://bucket/prefix/catalog`` URI that can also be handed to
``ParquetDataCatalog.from_uri`` / ``DataCatalogConfig`` / ``BacktestDataConfig``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable, Iterator, Protocol
from urllib.parse import urlparse

from tinohelm.core.config import Settings, TosStorageSettings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageObject:
    """Object metadata returned by storage-provider list calls."""

    key: str
    path: Path
    size: int | None = None
    last_modified: Any | None = None
    etag: str | None = None
    uri: str | None = None


class CatalogStorageProvider(Protocol):
    """Minimal provider surface needed by catalog readers/writers."""

    provider: str
    catalog_root: Path

    def materialize_path(self, local_path: Path | str) -> Path:
        """Compatibility shim. Remote providers must not write local cache."""

    def materialize_prefix(self, local_prefix: Path | str) -> None:
        """Compatibility shim. Remote providers must not write local cache."""

    def iter_files(
        self,
        prefix: Path | str,
        *,
        suffix: str = "",
        recursive: bool = True,
    ) -> Iterator[StorageObject]:
        """Yield files below *prefix* without materializing remote objects."""

    def exists(self, path: Path | str) -> bool:
        """Return whether one logical catalog path exists."""

    def open_input_file(self, path_or_object: Path | str | StorageObject) -> BinaryIO:
        """Open one logical catalog object for binary reads."""

    def read_bytes(self, path_or_object: Path | str | StorageObject) -> bytes:
        """Read one logical catalog object into bytes."""

    def upload_path(self, local_path: Path | str, *, logical_path: Path | str | None = None) -> str:
        """Upload one local file and return its logical URI."""

    def delete_path(self, local_path: Path | str) -> None:
        """Delete one catalog object from the backing store if present."""

    def uri_for_path(self, local_path: Path | str) -> str:
        """Return the logical URI for one catalog path."""


class LocalCatalogStorage:
    provider = "local"
    fs_protocol: str | None = None
    fs_storage_options: dict[str, Any] | None = None
    fs_rust_storage_options: dict[str, str] | None = None

    def __init__(self, catalog_root: Path | str) -> None:
        self.catalog_root = Path(catalog_root)

    @property
    def catalog_uri(self) -> str:
        return str(self.catalog_root)

    @property
    def nt_catalog_path(self) -> str:
        return str(self.catalog_root)

    def uri_for_catalog_root(self, catalog_root: Path | str | None = None) -> str:
        return str(Path(catalog_root) if catalog_root is not None else self.catalog_root)

    def materialize_path(self, local_path: Path | str) -> Path:
        return Path(local_path)

    def materialize_prefix(self, local_prefix: Path | str) -> None:
        return None

    def iter_files(
        self,
        prefix: Path | str,
        *,
        suffix: str = "",
        recursive: bool = True,
    ) -> Iterator[StorageObject]:
        path = Path(prefix)
        if path.is_file():
            if not suffix or path.name.endswith(suffix):
                yield self._object_for_path(path)
            return
        if not path.exists():
            return
        iterator = path.rglob(f"*{suffix}") if recursive else path.glob(f"*{suffix}")
        for file_path in sorted(iterator):
            if file_path.is_file() and (not suffix or file_path.name.endswith(suffix)):
                yield self._object_for_path(file_path)

    def exists(self, path: Path | str) -> bool:
        return Path(path).exists()

    def open_input_file(self, path_or_object: Path | str | StorageObject) -> BinaryIO:
        path = path_or_object.path if isinstance(path_or_object, StorageObject) else Path(path_or_object)
        return open(path, "rb")

    def read_bytes(self, path_or_object: Path | str | StorageObject) -> bytes:
        path = path_or_object.path if isinstance(path_or_object, StorageObject) else Path(path_or_object)
        return path.read_bytes()

    def upload_path(self, local_path: Path | str, *, logical_path: Path | str | None = None) -> str:
        return self.uri_for_path(logical_path or local_path)

    def delete_path(self, local_path: Path | str) -> None:
        path = Path(local_path)
        if path.is_file():
            path.unlink()

    def uri_for_path(self, local_path: Path | str) -> str:
        return str(Path(local_path))

    def _object_for_path(self, path: Path) -> StorageObject:
        try:
            stat = path.stat()
            size = stat.st_size
            last_modified = stat.st_mtime_ns
            key = str(path.resolve(strict=False))
        except OSError:
            size = None
            last_modified = None
            key = str(path)
        return StorageObject(key=key, path=path, size=size, last_modified=last_modified, uri=str(path))


class S3CatalogStorage:
    """S3-compatible backing store for a NautilusTrader Parquet catalog.

    Volcengine TOS is configured here as an S3-compatible object store.  This is
    intentionally not a ``tos://`` provider: the same URI/options can be passed
    to NautilusTrader and to vectorized Arrow/Polars readers.
    """

    provider = "s3"
    fs_protocol = "s3"

    def __init__(
        self,
        settings: TosStorageSettings,
        filesystem: Any | None = None,
        catalog_root: Path | str | None = None,
    ) -> None:
        self.settings = settings
        self.bucket = settings.bucket.strip()
        if not self.bucket:
            raise ValueError("S3/TOS storage requires TINO_STORAGE__TOS__BUCKET")
        self.prefix = _normalise_prefix(settings.prefix)
        self.catalog_root = Path(catalog_root or Path("tino/data/catalog")).resolve(strict=False)
        self._fs = filesystem

    @property
    def endpoint_url(self) -> str:
        endpoint = self.settings.endpoint.strip()
        if endpoint:
            return _ensure_url_scheme(endpoint)
        return _default_tos_endpoint(
            self.settings.region,
            use_internal_endpoint=self.settings.use_internal_endpoint,
        )

    @property
    def catalog_key_prefix(self) -> str:
        return f"{self.prefix}/catalog" if self.prefix else "catalog"

    @property
    def catalog_uri(self) -> str:
        return f"s3://{self.bucket}/{self.catalog_key_prefix}"

    @property
    def nt_catalog_path(self) -> str:
        """Catalog path value for NT configs that also carry ``fs_protocol``."""

        return f"{self.bucket}/{self.catalog_key_prefix}"

    @property
    def fs_storage_options(self) -> dict[str, Any]:
        """fsspec/s3fs options for Python-side catalog and vectorized reads."""

        ak = self.settings.access_key.get_secret_value()
        sk = self.settings.secret_key.get_secret_value()
        token = self.settings.security_token.get_secret_value()
        opts: dict[str, Any] = {
            # s3fs/fsspec-compatible names.  Rust/DataFusion receives its own
            # flat AWS-style map from ``fs_rust_storage_options`` below.
            "endpoint_url": self.endpoint_url,
            "client_kwargs": {
                "endpoint_url": self.endpoint_url,
                "region_name": self.settings.region,
            },
            "config_kwargs": {
                "signature_version": "s3v4",
                "s3": {"addressing_style": "virtual"},
            },
        }
        if ak:
            opts["key"] = ak
        if sk:
            opts["secret"] = sk
        if token:
            opts["token"] = token
        return opts

    @property
    def fs_rust_storage_options(self) -> dict[str, str]:
        """Flat Rust/object-store options for NT's DataFusion backend."""

        ak = self.settings.access_key.get_secret_value()
        sk = self.settings.secret_key.get_secret_value()
        token = self.settings.security_token.get_secret_value()
        opts = {
            "endpoint_url": self.endpoint_url,
            "region": self.settings.region,
            "virtual_hosted_style_request": "true",
            "enable_virtual_host_style": "true",
            "force_virtual_addressing": "true",
        }
        if ak:
            opts["access_key_id"] = ak
        if sk:
            opts["secret_access_key"] = sk
        if token:
            opts["session_token"] = token
        return opts

    @property
    def fs(self) -> Any:
        if self._fs is None:
            try:
                import fsspec
            except ModuleNotFoundError as exc:
                raise RuntimeError("S3/TOS catalog storage requires package 'fsspec' and an S3 implementation such as 's3fs'") from exc
            self._fs = fsspec.filesystem("s3", **self.fs_storage_options)
        return self._fs

    def uri_for_catalog_root(self, catalog_root: Path | str | None = None) -> str:
        if catalog_root is None:
            return self.catalog_uri
        return self.uri_for_path(catalog_root)

    def materialize_path(self, local_path: Path | str) -> Path:
        return Path(local_path)

    def materialize_prefix(self, local_prefix: Path | str) -> None:
        return None

    def iter_files(
        self,
        prefix: Path | str,
        *,
        suffix: str = "",
        recursive: bool = True,
    ) -> Iterator[StorageObject]:
        prefix_path = Path(prefix)
        if suffix and prefix_path.name.endswith(suffix):
            try:
                obj = self._head_object_for_path(prefix_path)
            except FileNotFoundError:
                return
            yield obj
            return

        key_prefix = self._key_for_path(prefix_path).rstrip("/")
        infos = self._find_infos(key_prefix) if recursive else self._ls_infos(key_prefix)
        seen: set[str] = set()
        for info in infos:
            key = self._key_from_info(info)
            if not key or key in seen:
                continue
            seen.add(key)
            if suffix and not key.endswith(suffix):
                continue
            yield self._object_for_key(key, info)

    def exists(self, path: Path | str) -> bool:
        path_obj = Path(path)
        try:
            self._head_object_for_path(path_obj)
            return True
        except FileNotFoundError:
            pass
        key_prefix = self._key_for_path(path_obj).rstrip("/")
        return next(iter(self._find_infos(key_prefix, limit=1)), None) is not None

    def open_input_file(self, path_or_object: Path | str | StorageObject) -> BinaryIO:
        obj = path_or_object if isinstance(path_or_object, StorageObject) else self._head_object_for_path(Path(path_or_object))
        return self.fs.open(self._fs_path_for_key(obj.key), "rb")

    def read_bytes(self, path_or_object: Path | str | StorageObject) -> bytes:
        with self.open_input_file(path_or_object) as fh:
            return fh.read()

    def upload_path(self, local_path: Path | str, *, logical_path: Path | str | None = None) -> str:
        path = Path(local_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        key_path = Path(logical_path) if logical_path is not None else path
        key = self._key_for_path(key_path)
        put_file = getattr(self.fs, "put_file", None)
        if callable(put_file):
            put_file(str(path), self._fs_path_for_key(key))
        else:
            self.fs.put(str(path), self._fs_path_for_key(key))
        return self._uri_for_key(key)

    def delete_path(self, local_path: Path | str) -> None:
        key = self._key_for_path(Path(local_path))
        try:
            self.fs.rm(self._fs_path_for_key(key))
        except FileNotFoundError:
            return

    def uri_for_path(self, local_path: Path | str) -> str:
        return self._uri_for_key(self._key_for_path(Path(local_path)))

    def _key_for_path(self, local_path: Path) -> str:
        root = self.catalog_root.resolve(strict=False)
        path = local_path.resolve(strict=False)
        try:
            rel = path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"path {local_path} is outside catalog root {root}") from exc
        rel_key = "" if rel == Path(".") else rel.as_posix()
        return f"{self.catalog_key_prefix}/{rel_key}".rstrip("/")

    def _path_for_key(self, key: str) -> Path:
        expected = f"{self.catalog_key_prefix}/"
        if key == self.catalog_key_prefix:
            return self.catalog_root
        if not key.startswith(expected):
            raise ValueError(f"S3 key {key!r} is outside catalog prefix {expected!r}")
        rel = key.removeprefix(expected)
        return self.catalog_root / Path(PurePosixPath(rel))

    def _fs_path_for_key(self, key: str) -> str:
        return f"{self.bucket}/{key}"

    def _uri_for_key(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def _head_object_for_path(self, logical_path: Path) -> StorageObject:
        key = self._key_for_path(logical_path)
        fs_path = self._fs_path_for_key(key)
        try:
            info = self.fs.info(fs_path)
        except (FileNotFoundError, OSError, KeyError) as exc:
            raise FileNotFoundError(fs_path) from exc
        return self._object_for_key(key, info)

    def _object_for_key(self, key: str, info: dict[str, Any] | Any | None = None) -> StorageObject:
        data = info if isinstance(info, dict) else {}
        return StorageObject(
            key=key,
            path=self._path_for_key(key),
            size=_info_size(data),
            last_modified=data.get("LastModified") or data.get("last_modified") or data.get("mtime"),
            etag=data.get("ETag") or data.get("etag"),
            uri=self._uri_for_key(key),
        )

    def _find_infos(self, key_prefix: str, *, limit: int | None = None) -> Iterator[dict[str, Any]]:
        fs_prefix = self._fs_path_for_key(key_prefix)
        find = getattr(self.fs, "find", None)
        if callable(find):
            try:
                found = find(fs_prefix, withdirs=False, detail=True)
            except TypeError:
                found = find(fs_prefix)
            yield from self._iter_found_infos(found, limit=limit)
            return

        glob = getattr(self.fs, "glob", None)
        if callable(glob):
            names = glob(f"{fs_prefix.rstrip('/')}/**")
            yielded = 0
            for name in names:
                info = self._info_for_name(name)
                if info is None:
                    continue
                yield info
                yielded += 1
                if limit is not None and yielded >= limit:
                    return

    def _ls_infos(self, key_prefix: str) -> Iterator[dict[str, Any]]:
        fs_prefix = self._fs_path_for_key(key_prefix)
        try:
            entries = self.fs.ls(fs_prefix, detail=True)
        except FileNotFoundError:
            return
        if isinstance(entries, dict):
            values = entries.values()
        else:
            values = entries
        for entry in values:
            info = entry if isinstance(entry, dict) else self._info_for_name(str(entry))
            if info is None:
                continue
            if str(info.get("type", "file")) not in {"file", "object"}:
                continue
            yield info

    def _iter_found_infos(self, found: Any, *, limit: int | None = None) -> Iterator[dict[str, Any]]:
        yielded = 0
        if isinstance(found, dict):
            iterable = found.items()
        else:
            iterable = ((name, None) for name in found or [])
        for name, raw_info in iterable:
            info = raw_info if isinstance(raw_info, dict) else self._info_for_name(str(name))
            if info is None:
                continue
            if str(info.get("type", "file")) not in {"file", "object"}:
                continue
            yield info
            yielded += 1
            if limit is not None and yielded >= limit:
                return

    def _info_for_name(self, name: str) -> dict[str, Any] | None:
        try:
            info = self.fs.info(name)
        except Exception:
            key = self._key_from_name(name)
            if not key:
                return None
            return {"name": self._fs_path_for_key(key), "size": None, "type": "file"}
        return info if isinstance(info, dict) else None

    def _key_from_info(self, info: dict[str, Any]) -> str:
        return self._key_from_name(str(info.get("name") or info.get("Key") or info.get("key") or ""))

    def _key_from_name(self, raw_name: str) -> str:
        name = raw_name.strip()
        if not name:
            return ""
        if name.startswith("s3://"):
            parsed = urlparse(name)
            if parsed.netloc != self.bucket:
                return ""
            return parsed.path.lstrip("/")
        if name.startswith(f"{self.bucket}/"):
            return name[len(self.bucket) + 1 :].lstrip("/")
        return name.lstrip("/")


# Backward-compatible import name for existing callers/tests.  The protocol is
# still S3; this alias must not emit ``tos://`` URIs.
TosCatalogStorage = S3CatalogStorage


def get_catalog_storage(
    *,
    settings: Settings | None = None,
    catalog_root: Path | str | None = None,
) -> CatalogStorageProvider:
    cfg = settings or get_settings()
    storage_cfg = getattr(cfg, "storage", None)
    raw_provider = getattr(storage_cfg, "provider", None) if storage_cfg is not None else None
    provider = raw_provider.lower().strip() if isinstance(raw_provider, str) else "local"
    if provider == "local":
        return LocalCatalogStorage(catalog_root or cfg.paths.catalog)
    if provider in {"tos", "s3"}:
        if storage_cfg is None or not hasattr(storage_cfg, "tos"):
            raise ValueError("S3/TOS storage requires a storage config with TOS settings")
        return S3CatalogStorage(storage_cfg.tos, catalog_root=catalog_root or cfg.paths.catalog)
    raise ValueError(f"Unknown storage provider {raw_provider!r}; expected 'local', 's3' or 'tos'")


def get_active_catalog_root(settings: Settings | None = None) -> Path:
    """Return the logical catalog root consumers should use."""

    cfg = settings or get_settings()
    return get_catalog_storage(settings=cfg, catalog_root=cfg.paths.catalog).catalog_root


def is_remote_storage(provider: CatalogStorageProvider) -> bool:
    return provider.provider != "local"


def staged_path_for_local_consumer(provider: CatalogStorageProvider, logical_path: Path | str) -> Path:
    """Return the logical path; remote consumers should use provider/NT URIs."""

    return Path(logical_path)


def stage_prefix_for_local_consumer(
    provider: CatalogStorageProvider,
    logical_prefix: Path | str,
    *,
    suffix: str = ".parquet",
) -> Path:
    """Compatibility shim retained for old call sites.

    Remote S3/TOS catalogs are not materialized into a read cache.  New code
    should use ``provider.iter_files`` / ``provider.open_input_file`` or NT's
    ``from_uri`` path instead of checking ``Path.exists()`` after this call.
    """

    return Path(logical_prefix)


def upload_paths(
    provider: CatalogStorageProvider,
    paths: Iterable[Path | str],
    *,
    logical_root: Path | str | None = None,
    staging_root: Path | str | None = None,
) -> list[str]:
    """Upload existing files through *provider* and return their logical URIs."""

    uris: list[str] = []
    staging_root_path = Path(staging_root).resolve(strict=False) if staging_root is not None else None
    logical_root_path = Path(logical_root).resolve(strict=False) if logical_root is not None else None
    for path in paths:
        p = Path(path)
        if not p.is_file():
            continue
        logical_path = None
        if staging_root_path is not None and logical_root_path is not None:
            try:
                logical_path = logical_root_path / p.resolve(strict=False).relative_to(staging_root_path)
            except ValueError:
                logical_path = None
        uris.append(provider.upload_path(p, logical_path=logical_path))
    return uris


def delete_prefix(provider: CatalogStorageProvider, local_prefix: Path | str) -> tuple[int, int]:
    """Delete one catalog file or all files under a catalog prefix."""

    prefix_path = Path(local_prefix)
    if is_remote_storage(provider):
        remote_objects: list[StorageObject]
        if prefix_path.name.endswith(".parquet"):
            try:
                remote_objects = [provider._head_object_for_path(prefix_path)]  # type: ignore[attr-defined]
            except FileNotFoundError:
                remote_objects = []
        else:
            remote_objects = list(provider.iter_files(prefix_path, recursive=True))
        deleted = 0
        freed = 0
        for obj in remote_objects:
            if obj.size is not None:
                freed += int(obj.size)
            provider.delete_path(obj.path)
            deleted += 1
        return (deleted, freed)

    if prefix_path.is_file():
        size = prefix_path.stat().st_size
        prefix_path.unlink()
        return (1, size)
    if not prefix_path.exists():
        return (0, 0)
    deleted = 0
    freed = 0
    for file_path in prefix_path.glob("*.parquet"):
        if not file_path.is_file():
            continue
        size = file_path.stat().st_size
        file_path.unlink()
        deleted += 1
        freed += size
    return (deleted, freed)


def _default_tos_endpoint(region: str, *, use_internal_endpoint: bool = True) -> str:
    suffix = "ivolces.com" if use_internal_endpoint else "volces.com"
    return f"https://tos-s3-{region}.{suffix}"


def _normalise_prefix(prefix: str) -> str:
    return prefix.strip().strip("/")


def _ensure_url_scheme(endpoint: str) -> str:
    value = endpoint.strip()
    if value.startswith(("http://", "https://")):
        return value
    return f"https://{value}"


def _info_size(info: dict[str, Any]) -> int | None:
    for key in ("size", "Size", "ContentLength", "content_length"):
        value = info.get(key)
        if value is not None:
            return int(value)
    return None
