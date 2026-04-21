"""Shared helpers for API route modules.

This module collects small, reusable primitives that were previously duplicated
across `src/tinohelm/api/routes/*.py`:

* **Artifact path safety** — validate `run_id` as a strict UUID and resolve the
  artifact directory inside the configured `artifacts` root, rejecting
  path-traversal attempts. Five near-identical copies existed across
  ``backtest.py`` (status / result / delete / list_artifacts / get_artifact).
* **Redis progress fetch** — read ``tino:backtest:progress:{run_id}`` values and
  coerce to ``int`` with a shared error fallback. Previously inlined in
  ``list_backtest_runs`` (pipeline batch) and ``get_backtest_status`` (single).
* **Redis JSON-or-default** — the ``await rds.get(key)``, decode-bytes,
  ``json.loads`` pattern shows up seven times across ``node.py`` (lifecycle
  state, strategy registry, heartbeat, data-status, subscriptions, paper
  config) and ``trading.py`` (risk metrics).

Having these helpers in one place keeps the route handlers thin and — more
importantly — makes the security-sensitive path-traversal code testable under
a fast NT-free unit test. No FastAPI route imports this module for anything
beyond the HTTPException it already imports from FastAPI, so there is no new
dependency surface.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

__all__ = [
    "UUID_RE",
    "HEX_PREFIX_RE",
    "MIN_PREFIX_LEN",
    "is_full_uuid",
    "validate_uuid_or_400",
    "resolve_artifact_path",
    "decode_redis_str",
    "load_redis_json",
    "fetch_redis_progress",
    "fetch_redis_progress_batch",
]


# ---- regex constants ---------------------------------------------------

#: Matches a canonical lowercase 8-4-4-4-12 UUID hex string.
UUID_RE: re.Pattern[str] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

#: Matches a non-empty lowercase hex-only string (used for prefix queries).
HEX_PREFIX_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]+$")

#: Minimum length of a UUID prefix accepted for git-style lookups.
MIN_PREFIX_LEN: int = 4


# ---- UUID validation ----------------------------------------------------

def is_full_uuid(value: str) -> bool:
    """Return True when *value* is a canonical lowercase UUID string.

    Whitespace is **not** trimmed; callers should normalise first if they
    accept user input with stray spacing.
    """
    return bool(UUID_RE.match(value))


def validate_uuid_or_400(run_id: str) -> str:
    """Validate *run_id* as a canonical UUID, raising 400 otherwise.

    The input is lowercased before checking (UUIDs are case-insensitive in
    practice but our DB column stores them lowercase).
    """
    normalised = run_id.strip().lower()
    if not UUID_RE.match(normalised):
        raise HTTPException(status_code=400, detail="Invalid run_id format")
    return normalised


# ---- artifact path safety ----------------------------------------------

def resolve_artifact_path(artifacts_root: str | Path, run_id: str, *segments: str) -> Path:
    """Resolve a run artifact path and verify it stays under *artifacts_root*.

    Parameters
    ----------
    artifacts_root:
        Base directory that all artifacts must live inside (``settings.paths.artifacts``).
    run_id:
        The backtest run id. Must be a canonical UUID — anything else raises
        ``HTTPException(400)``.
    *segments:
        Optional child segments appended after the ``run_id`` directory.

    Returns
    -------
    Path
        The resolved absolute path. Existence is **not** checked; callers
        inspect ``path.exists()`` themselves to differentiate 404 vs. 200.

    Raises
    ------
    HTTPException
        * 400 — ``run_id`` not a canonical UUID.
        * 400 — resolved path escapes the artifacts root (path traversal).
    """
    normalised = validate_uuid_or_400(run_id)
    root = Path(artifacts_root).resolve()
    candidate = (root / normalised).joinpath(*segments).resolve()
    # Use Path.is_relative_to for reliable boundary check (string startswith is
    # fragile around trailing separators on different OSes).
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    return candidate


# ---- redis helpers -----------------------------------------------------

def decode_redis_str(raw: Any) -> str | None:
    """Normalise redis-py responses to ``str`` or ``None``.

    redis-py returns bytes by default but our code often runs with
    ``decode_responses=True`` for newer clients. This helper tolerates either
    — plus ``None`` for missing keys.
    """
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            return raw.decode()
        except UnicodeDecodeError:
            logger.warning("Redis value is not valid UTF-8; returning None")
            return None
    return str(raw)


async def load_redis_json(rds: Any, key: str, default: Any = None) -> Any:
    """``GET`` *key* from Redis and JSON-decode it, else return *default*.

    Returns *default* on missing key, invalid UTF-8, or JSON parse errors.
    The default is returned **by reference** — callers that want an isolated
    mutable dict must pass a fresh instance each time.
    """
    raw = await rds.get(key)
    decoded = decode_redis_str(raw)
    if decoded is None:
        return default
    try:
        return json.loads(decoded)
    except (ValueError, TypeError):
        logger.warning("Redis key %r holds invalid JSON; returning default", key)
        return default


async def fetch_redis_progress(rds: Any, key: str) -> int | None:
    """Read an integer progress value from *key*, returning ``None`` on miss/error."""
    raw = await rds.get(key)
    decoded = decode_redis_str(raw)
    if decoded is None:
        return None
    try:
        return int(decoded)
    except (ValueError, TypeError):
        return None


async def fetch_redis_progress_batch(rds: Any, keys: list[str]) -> list[int | None]:
    """Fetch progress for many keys via a single Redis pipeline.

    Returns a list of the same length as *keys* containing ``int`` or ``None``
    for each input position. An empty *keys* list returns an empty list
    without opening a pipeline.
    """
    if not keys:
        return []
    pipe = rds.pipeline()
    for k in keys:
        pipe.get(k)
    values = await pipe.execute()
    out: list[int | None] = []
    for raw in values:
        decoded = decode_redis_str(raw)
        if decoded is None:
            out.append(None)
            continue
        try:
            out.append(int(decoded))
        except (ValueError, TypeError):
            out.append(None)
    return out
