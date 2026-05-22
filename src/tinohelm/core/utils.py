"""Shared utility functions."""
from __future__ import annotations

import math
from pathlib import Path


def sanitize_for_json(obj):
    """Recursively replace NaN/Infinity with None for JSON serialization."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    return obj


def is_within_dir(candidate: str | Path, boundary: str | Path) -> bool:
    """Return ``True`` when *candidate* resolves inside *boundary*.

    Both paths are ``Path.resolve()``-d first so symlinks cannot be used to
    tunnel out of *boundary*. Containment is tested via ``Path.relative_to``
    rather than ``str(a).startswith(str(b))`` — the latter is fragile across
    operating systems (trailing separator, case-insensitive FS, drive-letter
    casing on Windows) and has been the root cause of past path-traversal
    regressions in this codebase.

    The boundary directory itself is considered "within" itself; i.e. the
    function returns ``True`` when ``candidate == boundary``.
    """
    candidate_path = Path(candidate).resolve()
    boundary_path = Path(boundary).resolve()
    try:
        candidate_path.relative_to(boundary_path)
        return True
    except ValueError:
        return False
