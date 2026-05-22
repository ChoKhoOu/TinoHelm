"""NT-free file-watcher step used by ``HealthActor``.

The Actor polls the strategies directory every 10 seconds to decide whether a
rescan command should be enqueued.  The logic is pure IO + dict comparison, so
it lives here where it can be tested without spinning up NT or a background
thread.

The public surface is deliberately small:

``compute_mtime_map(dir_path, pattern)``
    Walk ``dir_path`` recursively and return ``{absolute_path_str: mtime}``.
    Files that disappear between ``rglob`` and ``stat`` are skipped silently
    (matches the inline ``try/except OSError`` in the legacy thread body).

``detect_and_enqueue(dir_path, prev_mtimes, command_deque, pattern=...)``
    One tick of the polling loop: recompute the map, enqueue a
    ``_rescan_strategies`` command when the map differs from the previous
    snapshot, return the new map so the caller can swap it in.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["compute_mtime_map", "detect_and_enqueue"]


def compute_mtime_map(
    dir_path: Path, pattern: str = "*.py"
) -> dict[str, float]:
    """Return ``{abs_path: mtime}`` for every file under ``dir_path`` matching
    ``pattern``.

    Files whose ``stat()`` raises :class:`OSError` (e.g. the file was deleted
    between the directory walk and the stat call) are silently skipped; this
    matches the legacy inline behaviour where any one OS error would otherwise
    have skipped the entire rescan.
    """
    result: dict[str, float] = {}
    for path in dir_path.rglob(pattern):
        try:
            result[str(path)] = path.stat().st_mtime
        except OSError:
            continue
    return result


def detect_and_enqueue(
    dir_path: Path,
    prev_mtimes: dict[str, float],
    command_deque: Any,
    *,
    pattern: str = "*.py",
) -> dict[str, float]:
    """Run one iteration of the strategy-file polling loop.

    * If ``dir_path`` does not exist, returns ``prev_mtimes`` unchanged — the
      thread should continue polling; the directory may appear later once the
      user has created strategies.
    * Recomputes the map for ``dir_path`` via :func:`compute_mtime_map`.
    * If the new map differs from ``prev_mtimes`` (any file added / removed /
      mtime bumped), appends a ``{"cmd": "_rescan_strategies"}`` entry to
      ``command_deque`` so the :class:`CommandActor` drain timer can pick it
      up on the next tick.

    Always returns the new map so the caller can atomically swap its tracking
    state in one assignment.
    """
    if not dir_path.exists():
        return prev_mtimes
    current = compute_mtime_map(dir_path, pattern)
    if current != prev_mtimes:
        command_deque.append({"cmd": "_rescan_strategies"})
    return current
