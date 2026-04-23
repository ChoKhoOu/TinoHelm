"""L2 disk cache for factor computation results.

Two-level cache:
* ``values/{key}.parquet`` — factor_values Panel (DatetimeIndex × symbol)
* ``eval/{key}.json``     — EvalResult serialised to JSON with NaN/Inf scrub

Cache key is a SHA-256 digest of (factor_name, code_hash, stable_json(config),
stable_json(data_range)) — identical inputs always produce the same key.

Partial-hit semantics: ``lookup`` checks both sub-caches independently.  A
caller that only needs to re-evaluate (e.g. after config change) can skip
re-computing factor values when they are already cached.

NaN/Inf contract: ``EvalResult`` floats are scrubbed to ``None`` before
serialisation.  On deserialisation, ``None`` stays ``None`` — callers must
handle the absence of a value.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import math
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tinohelm.factor.types import EvalConfig, EvalResult, Panel

log = logging.getLogger(__name__)

_DEFAULT_CACHE_ROOT = Path.home() / ".tino" / "factor_cache"


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
        return o

    return json.dumps(_make_serializable(obj), sort_keys=True, separators=(",", ":"))


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
        if cache_root is None:
            try:
                from tinohelm.core.config import get_settings
                settings = get_settings()
                paths = settings.paths
                if hasattr(paths, "factor_cache"):
                    cache_root = paths.factor_cache
                else:
                    cache_root = _DEFAULT_CACHE_ROOT
            except Exception:
                cache_root = _DEFAULT_CACHE_ROOT
        self._root = Path(cache_root)
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
        return self._values_dir / f"{key}.parquet"

    def _eval_path(self, key: str) -> Path:
        return self._eval_dir / f"{key}.json"

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

        Returns
        -------
        str
            64-char hex digest.
        """
        config_dict = dataclasses.asdict(config)
        payload = (
            factor_name
            + "|"
            + code_hash
            + "|"
            + _stable_json(config_dict)
            + "|"
            + _stable_json(data_range)
        )
        return hashlib.sha256(payload.encode()).hexdigest()

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
                factor_values = pd.read_parquet(vpath)
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
            factor_values.to_parquet(vpath)
            added_bytes += vpath.stat().st_size

        if eval_result is not None:
            epath = self._eval_path(key)
            data = _eval_result_to_dict(eval_result)
            with open(epath, "w") as f:
                json.dump(data, f, indent=2, default=str)
            added_bytes += epath.stat().st_size

        if factor_values is not None or eval_result is not None:
            self._update_manifest(key, factor_name, code_hash, added_bytes)

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
        if name is None:
            keys_to_delete = list(manifest.keys())
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
