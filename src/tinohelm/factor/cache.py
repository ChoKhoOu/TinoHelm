"""L2 disk cache for factor computation results.

Two-level cache:
* ``values/{key}.parquet`` — factor_values Panel (``ts`` + symbol columns)
* ``eval/{key}.json``     — EvalResult serialised to JSON with NaN/Inf scrub

Cache key is a SHA-256 digest of (factor_name, code_hash, stable_json(config),
stable_json(data_range)) — identical inputs always produce the same key.

Partial-hit semantics: ``lookup`` checks both sub-caches independently.  A
caller that only needs to re-evaluate (e.g. after config change) can skip
re-computing factor values when they are already cached.

NaN/Inf contract: ``EvalResult`` floats are scrubbed to ``None`` before
serialisation.  On deserialisation, ``None`` stays ``None`` — callers must
handle the absence of a value.

Polars contract
---------------
Cached factor values are written and read as :class:`polars.DataFrame`
panels — :data:`Panel` is ``pl.DataFrame`` after the polars migration.
Existing on-disk pandas-written parquet files remain readable through
polars' parquet reader because the format is column-compatible.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import math
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from tinohelm.core.paths import paths
from tinohelm.factor.research.panel import MatrixPanel, matrix_to_wide, wide_to_matrix
from tinohelm.factor.types import EvalConfig, EvalResult, Panel

log = logging.getLogger(__name__)
_KEY_RE = re.compile(r"^[A-Za-z0-9_.=-]+$")
_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_.=-]+$")


# ---------------------------------------------------------------------------
# Helpers — stable JSON and NaN/Inf scrub
# ---------------------------------------------------------------------------

def _stable_json(obj: Any) -> str:
    """Produce a deterministic JSON string regardless of dict insertion order.

    * Dicts are sorted by key.
    * Floats are rounded to 10 decimal places to avoid FP representation drift.
    * Tuples and lists are treated identically (both become JSON arrays).
    """
    def _default(o: Any) -> Any:
        if isinstance(o, (tuple, set, frozenset)):
            return list(o)
        raise TypeError(f"Object of type {type(o).__name__} is not JSON-serializable")

    def _make_serializable(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: _make_serializable(v) for k, v in sorted(o.items(), key=str)}
        if isinstance(o, (list, tuple)):
            return [_make_serializable(i) for i in o]
        if isinstance(o, float):
            if math.isnan(o) or math.isinf(o):
                return None
            return round(o, 10)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return o

    return json.dumps(_make_serializable(obj), sort_keys=True, separators=(",", ":"))


def _hash_payload(namespace: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        f"{namespace}|{_stable_json(payload)}".encode("utf-8")
    ).hexdigest()


def _normalize_tuple(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        return (values,)
    return tuple(str(value) for value in values)


def _normalize_set_tuple(values: Any) -> tuple[str, ...]:
    return tuple(sorted(set(_normalize_tuple(values))))


def _normalize_positive_ints(values: Any) -> tuple[int, ...]:
    normalized: list[int] = []
    seen: set[int] = set()
    for raw in values:
        value = int(raw)
        if value <= 0:
            raise ValueError("forward return periods must all be > 0")
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    return tuple(sorted(normalized))


def _validate_path_component(value: str, label: str) -> str:
    if not value or value in {".", ".."} or not _KEY_RE.fullmatch(value):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def _validate_namespace(namespace: str) -> str:
    if not namespace or namespace in {".", ".."} or not _NAMESPACE_RE.fullmatch(namespace):
        raise ValueError(f"invalid cache namespace: {namespace!r}")
    return namespace


def _write_parquet_atomic(frame: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=f".{path.stem}_")
    os.close(fd)
    tmp = Path(tmp_path)
    try:
        frame.write_parquet(tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _scrub(obj: Any) -> Any:
    """Replace NaN/Inf floats (and numpy scalars) with ``None`` recursively."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, (np.floating, np.integer)):
        f = float(obj)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub(i) for i in obj]
    return obj


def _eval_result_to_dict(result: EvalResult) -> dict:
    """Serialise ``EvalResult`` to a plain dict with NaN/Inf scrubbed."""
    raw = dataclasses.asdict(result)
    return _scrub(raw)  # type: ignore[return-value]


def _dict_to_eval_result(data: dict) -> EvalResult:
    """Deserialise a plain dict back to ``EvalResult``.

    Unknown keys in ``data`` are silently ignored so forward-compatibility is
    maintained when new fields are added to ``EvalResult``.
    """
    known = {f.name for f in dataclasses.fields(EvalResult)}
    filtered = {k: v for k, v in data.items() if k in known}
    return EvalResult(**filtered)


# ---------------------------------------------------------------------------
# CacheHit — returned by lookup()
# ---------------------------------------------------------------------------

@dataclass
class CacheHit:
    """Result of a cache lookup.

    Attributes
    ----------
    factor_values_hit : bool
        True when the Parquet values file exists and was loaded successfully.
    eval_hit : bool
        True when the JSON eval file exists and was loaded successfully.
    factor_values : Panel | None
        Loaded factor values Panel.  ``None`` when ``factor_values_hit`` is False.
    eval_result : EvalResult | None
        Loaded EvalResult.  ``None`` when ``eval_hit`` is False.
    """
    factor_values_hit: bool
    eval_hit: bool
    factor_values: Panel | None
    eval_result: EvalResult | None


# ---------------------------------------------------------------------------
# FactorCache
# ---------------------------------------------------------------------------

class FactorCache:
    """L2 disk cache for factor values and evaluation results.

    Parameters
    ----------
    cache_root :
        Root directory for all cached data.  Defaults to
        ``~/.tino/factor_cache/``.
    """

    def __init__(self, cache_root: Path | None = None) -> None:
        self._root = Path(cache_root) if cache_root is not None else paths.get("factor_cache")
        self._values_dir = self._root / "values"
        self._eval_dir = self._root / "eval"
        self._manifest_path = self._root / "manifest.json"
        self._manifest_lock = threading.Lock()
        self._ensure_dirs()

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _ensure_dirs(self) -> None:
        self._values_dir.mkdir(parents=True, exist_ok=True)
        self._eval_dir.mkdir(parents=True, exist_ok=True)

    def _values_path(self, key: str) -> Path:
        safe_key = _validate_path_component(key, "cache key")
        return self._values_dir / f"{safe_key}.parquet"

    def _eval_path(self, key: str) -> Path:
        safe_key = _validate_path_component(key, "cache key")
        return self._eval_dir / f"{safe_key}.json"

    def _matrix_dir(self, namespace: str) -> Path:
        return self._root / "matrix" / _validate_namespace(namespace)

    def _matrix_path(self, namespace: str, key: str) -> Path:
        safe_key = _validate_path_component(key, "cache key")
        return self._matrix_dir(namespace) / f"{safe_key}.parquet"

    # ------------------------------------------------------------------ #
    # Manifest helpers
    # ------------------------------------------------------------------ #

    def _load_manifest(self) -> dict:
        if self._manifest_path.exists():
            try:
                with open(self._manifest_path) as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def _save_manifest(self, manifest: dict) -> None:
        # Atomic write: write to a temp file in the same directory, then
        # os.replace (which is atomic on POSIX and Windows NTFS).
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._root), suffix=".tmp", prefix=".manifest_"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(manifest, f, indent=2, default=str)
            os.replace(tmp_path, str(self._manifest_path))
        except BaseException:
            # Clean up temp file on any failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _update_manifest(
        self,
        key: str,
        factor_name: str,
        code_hash: str,
        extra_size: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._manifest_lock:
            manifest = self._load_manifest()
            entry = manifest.get(key, {})
            if not entry:
                entry = {
                    "factor_name": factor_name,
                    "code_hash": code_hash,
                    "created_at": datetime.now().isoformat(),
                    "size_bytes": 0,
                }
            entry["size_bytes"] = entry.get("size_bytes", 0) + extra_size
            entry["updated_at"] = datetime.now().isoformat()
            if metadata:
                entry_metadata = dict(entry.get("metadata") or {})
                entry_metadata.update(metadata)
                entry["metadata"] = entry_metadata
            manifest[key] = entry
            self._save_manifest(manifest)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @staticmethod
    def build_key(
        factor_name: str,
        code_hash: str,
        config: EvalConfig,
        data_range: tuple,
        interval: str,
        *,
        full: bool = False,
    ) -> str:
        """Compute a SHA-256 cache key.

        Parameters
        ----------
        factor_name :
            Unique factor identifier.
        code_hash :
            SHA-256 hex of the factor's source code (from ``FactorSpec``).
        config :
            Evaluation configuration (universe, dates, params …).
        data_range :
            ``(start, end)`` tuple of the actual data window loaded.
        interval :
            Bar interval used for factor/evaluation inputs. Different bar
            cadences must never share combined factor/eval cache entries.
        full :
            Evaluation mode. Full diagnostics and fast runs must not share an
            EvalResult cache entry because full runs include extra outputs.

        Returns
        -------
        str
            64-char hex digest.
        """
        config_dict = dataclasses.asdict(config)
        payload = _stable_json({
            "identity_version": 2,
            "factor_name": factor_name,
            "code_hash": code_hash,
            "config": config_dict,
            "data_range": data_range,
            "interval": interval,
            "eval_mode": "full" if full else "fast",
        })
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def build_raw_data_key(
        *,
        source: str,
        interval: str,
        universe: tuple[str, ...] | list[str],
        fields: tuple[str, ...] | list[str],
        start: Any,
        end: Any,
        data_version: str,
    ) -> str:
        """Key for source/interval/field-specific canonical raw bars."""
        return _hash_payload("raw_data", {
            "source": source,
            "interval": interval,
            "universe": _normalize_tuple(universe),
            "fields": _normalize_set_tuple(fields),
            "start": start,
            "end": end,
            "data_version": data_version,
        })

    @staticmethod
    def build_factor_values_key(
        *,
        factor_name: str,
        code_hash: str,
        params: dict[str, Any],
        universe: tuple[str, ...] | list[str],
        interval: str,
        start: Any,
        end: Any,
        required_fields: tuple[str, ...] | list[str],
        raw_data_key: str,
    ) -> str:
        """Key for raw factor values, intentionally excluding eval knobs."""
        return _hash_payload("factor_values", {
            "factor_name": factor_name,
            "code_hash": code_hash,
            "params": params,
            "universe": _normalize_tuple(universe),
            "interval": interval,
            "start": start,
            "end": end,
            "required_fields": _normalize_set_tuple(required_fields),
            "raw_data_key": raw_data_key,
        })

    @staticmethod
    def build_forward_returns_key(
        *,
        close_panel_key: str,
        close_content_key: str,
        periods: tuple[int, ...] | list[int],
        log_ret: bool,
        interval: str,
        expected_step_ns: int | None = None,
    ) -> str:
        """Key for reusable forward-return matrices derived from close prices."""
        normalized_periods = _normalize_positive_ints(periods)
        return _hash_payload("forward_returns", {
            "close_panel_key": close_panel_key,
            "close_content_key": close_content_key,
            "periods": normalized_periods,
            "log_ret": bool(log_ret),
            "interval": interval,
            "expected_step_ns": expected_step_ns,
        })

    @staticmethod
    def build_eval_key(
        factor_values_key: str,
        eval_config: EvalConfig | dict[str, Any],
        *,
        full: bool = False,
        returns_key: str | None = None,
        eval_version: str = "v1",
    ) -> str:
        """Key for metrics/evaluation output; changes with eval-only config."""
        config_payload = dataclasses.asdict(eval_config) if dataclasses.is_dataclass(eval_config) else eval_config
        return _hash_payload("eval", {
            "factor_values_key": factor_values_key,
            "returns_key": returns_key,
            "eval_version": eval_version,
            "eval_mode": "full" if full else "fast",
            "eval_config": config_payload,
        })

    def get_matrix_panel(self, namespace: str, key: str) -> MatrixPanel | None:
        """Read a namespaced matrix-panel cache entry, returning ``None`` on miss."""
        path = self._matrix_path(namespace, key)
        if not path.exists():
            return None
        try:
            return wide_to_matrix(pl.read_parquet(path))
        except (OSError, ValueError, pl.exceptions.PolarsError) as exc:  # pragma: no cover
            log.warning("FactorCache: failed to read matrix panel %s: %s", path, exc)
            try:
                path.unlink(missing_ok=True)
            except OSError as unlink_exc:  # pragma: no cover
                log.warning("FactorCache: cannot delete corrupt matrix panel %s: %s", path, unlink_exc)
            return None

    def put_matrix_panel(self, namespace: str, key: str, panel: MatrixPanel) -> None:
        """Atomically store a matrix panel under a namespaced cache key."""
        panel.validate()
        path = self._matrix_path(namespace, key)
        _write_parquet_atomic(matrix_to_wide(panel), path)

    def lookup(self, key: str) -> CacheHit | None:
        """Look up a cache key and return what is available.

        Checks the values Parquet and eval JSON independently.  Returns
        ``None`` only when *neither* sub-cache has the key (full miss).
        Returns a ``CacheHit`` with the available components when at least
        one sub-cache hits.

        Parameters
        ----------
        key :
            Cache key produced by :meth:`build_key`.
        """
        vpath = self._values_path(key)
        epath = self._eval_path(key)

        values_hit = False
        factor_values: Panel | None = None
        if vpath.exists():
            try:
                factor_values = pl.read_parquet(vpath)
                values_hit = True
            except Exception as exc:  # pragma: no cover
                log.warning("FactorCache: failed to read %s: %s", vpath, exc)

        eval_hit = False
        eval_result: EvalResult | None = None
        if epath.exists():
            try:
                with open(epath) as f:
                    data = json.load(f)
                eval_result = _dict_to_eval_result(data)
                eval_hit = True
            except Exception as exc:  # pragma: no cover
                log.warning("FactorCache: failed to read %s: %s", epath, exc)

        if not values_hit and not eval_hit:
            return None

        return CacheHit(
            factor_values_hit=values_hit,
            eval_hit=eval_hit,
            factor_values=factor_values,
            eval_result=eval_result,
        )

    def store(
        self,
        key: str,
        *,
        factor_name: str = "",
        code_hash: str = "",
        factor_values: Panel | None = None,
        eval_result: EvalResult | None = None,
    ) -> None:
        """Write factor values and/or eval result to disk.

        Either or both of ``factor_values`` / ``eval_result`` may be
        provided.  Existing cached data is overwritten.

        Parameters
        ----------
        key :
            Cache key produced by :meth:`build_key`.
        factor_name :
            Factor name stored in the manifest (used by :meth:`invalidate`).
        code_hash :
            Code hash stored in the manifest.
        factor_values :
            Panel (DatetimeIndex × symbol) to store as Parquet.
        eval_result :
            ``EvalResult`` dataclass to store as JSON.
        """
        added_bytes = 0

        if factor_values is not None:
            vpath = self._values_path(key)
            # Polars uses ``write_parquet``; pandas ``to_parquet`` is kept as
            # a fallback so transitional callers that still pass pandas
            # frames don't regress.
            if hasattr(factor_values, "write_parquet"):
                factor_values.write_parquet(vpath)
            else:
                factor_values.to_parquet(vpath)  # type: ignore[union-attr]
            added_bytes += vpath.stat().st_size

        if eval_result is not None:
            epath = self._eval_path(key)
            data = _eval_result_to_dict(eval_result)
            with open(epath, "w") as f:
                json.dump(data, f, indent=2, default=str)
            added_bytes += epath.stat().st_size

        if factor_values is not None or eval_result is not None:
            metadata: dict[str, Any] = {}
            if eval_result is not None:
                metadata["effective_params"] = dict(eval_result.effective_params or {})
                metadata["cache_key"] = eval_result.cache_key
                metadata["cache_hit"] = eval_result.cache_hit
                metadata["factor_code_hash"] = eval_result.factor_code_hash
                metadata["factor_source_file"] = eval_result.factor_source_file
                metadata["factor_module_path"] = eval_result.factor_module_path
                metadata["warnings"] = list(eval_result.warnings or [])
                if eval_result.walk_forward is not None:
                    metadata["walk_forward_status"] = eval_result.walk_forward.get("status")
            self._update_manifest(
                key,
                factor_name,
                code_hash,
                added_bytes,
                metadata=metadata,
            )

    def invalidate(self, name: str | None = None) -> int:
        """Delete cached entries.

        Parameters
        ----------
        name :
            Factor name to purge.  When ``None``, all entries are deleted.

        Returns
        -------
        int
            Number of manifest entries removed.
        """
        manifest = self._load_manifest()
        matrix_dir = self._root / "matrix"
        if name is None:
            keys_to_delete = list(manifest.keys())
            if matrix_dir.exists():
                shutil.rmtree(matrix_dir)
        else:
            keys_to_delete = [k for k, v in manifest.items() if v.get("factor_name") == name]

        removed = 0
        for key in keys_to_delete:
            vpath = self._values_path(key)
            epath = self._eval_path(key)
            for p in (vpath, epath):
                try:
                    p.unlink(missing_ok=True)
                except OSError as exc:  # pragma: no cover
                    log.warning("FactorCache: cannot delete %s: %s", p, exc)
            manifest.pop(key, None)
            removed += 1

        self._save_manifest(manifest)
        return removed
