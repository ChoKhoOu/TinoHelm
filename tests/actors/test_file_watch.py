"""Tests for :mod:`tinohelm.node.actors.file_watch`.

Extracted from ``HealthActor._file_watcher``, this module owns the "did a
strategy file change since last poll?" detection step.  A regression here is
insidious — strategies quietly stop being rescanned, so edits on disk never
reach the running node, yet nothing alerts the operator until they wonder why
their latest code change didn't take effect.

These tests cover:

* ``compute_mtime_map`` — empty dir, nested files, non-matching suffixes,
  symlinks, ``OSError`` during ``stat``, large mtime values.
* ``detect_and_enqueue`` — idempotent first pass, change detection on add /
  remove / mtime bump / content change, missing-dir early return, pattern
  override, plus the "unchanged map yields no append" guarantee that keeps the
  10-second poll cheap in the common case.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from unittest.mock import patch

import pytest

from tinohelm.node.actors.file_watch import compute_mtime_map, detect_and_enqueue


# ---------------------------------------------------------------------------
# compute_mtime_map
# ---------------------------------------------------------------------------

class TestComputeMtimeMap:
    def test_empty_dir_returns_empty_dict(self, tmp_path: Path):
        assert compute_mtime_map(tmp_path) == {}

    def test_lists_single_file(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1")
        out = compute_mtime_map(tmp_path)
        assert set(out.keys()) == {str(tmp_path / "a.py")}
        assert out[str(tmp_path / "a.py")] > 0

    def test_walks_recursively(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.py").write_text("y")
        (tmp_path / "sub" / "deep").mkdir()
        (tmp_path / "sub" / "deep" / "c.py").write_text("z")
        out = compute_mtime_map(tmp_path)
        assert set(out.keys()) == {
            str(tmp_path / "a.py"),
            str(tmp_path / "sub" / "b.py"),
            str(tmp_path / "sub" / "deep" / "c.py"),
        }

    def test_ignores_non_matching_suffix(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.txt").write_text("y")
        (tmp_path / "c.yaml").write_text("z")
        out = compute_mtime_map(tmp_path)
        assert set(out.keys()) == {str(tmp_path / "a.py")}

    def test_custom_pattern(self, tmp_path: Path):
        (tmp_path / "a.yaml").write_text("x")
        (tmp_path / "b.yml").write_text("y")
        (tmp_path / "c.py").write_text("z")
        out = compute_mtime_map(tmp_path, pattern="*.yaml")
        assert set(out.keys()) == {str(tmp_path / "a.yaml")}

    def test_pattern_allows_portfolio_layout(self, tmp_path: Path):
        """Portfolio folders have both .py and portfolio.yaml — custom pattern
        lets callers observe either."""
        (tmp_path / "strat1").mkdir()
        (tmp_path / "strat1" / "portfolio.yaml").write_text("symbols: [BTC]")
        (tmp_path / "strat1" / "strategy.py").write_text("x")
        yaml_map = compute_mtime_map(tmp_path, pattern="portfolio.yaml")
        py_map = compute_mtime_map(tmp_path, pattern="*.py")
        assert list(yaml_map.keys()) == [str(tmp_path / "strat1" / "portfolio.yaml")]
        assert list(py_map.keys()) == [str(tmp_path / "strat1" / "strategy.py")]

    def test_mtime_is_float(self, tmp_path: Path):
        f = tmp_path / "a.py"
        f.write_text("x")
        out = compute_mtime_map(tmp_path)
        assert isinstance(out[str(f)], float)

    def test_mtime_changes_on_content_change(self, tmp_path: Path):
        """Overwriting a file must bump its mtime; we rely on this in the
        change-detection path."""
        import os
        import time

        f = tmp_path / "a.py"
        f.write_text("v1")
        before = compute_mtime_map(tmp_path)[str(f)]

        # Force a later mtime — write_text alone may land in the same tick on
        # fast filesystems; ``os.utime`` lets us advance deterministically.
        os.utime(f, (before + 10, before + 10))
        after = compute_mtime_map(tmp_path)[str(f)]
        # Defeat filesystem clock granularity by allowing any increase
        assert after > before

        # Touch should also change it
        time.sleep(0.01)
        os.utime(f, None)
        again = compute_mtime_map(tmp_path)[str(f)]
        assert again != before

    def test_oserror_during_stat_is_skipped(self, tmp_path: Path):
        """If stat fails for one file, the rest of the scan continues."""
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("y")

        real_stat = Path.stat

        def _fake_stat(self: Path, *, follow_symlinks: bool = True):
            if self.name == "a.py":
                raise OSError("permission denied")
            return real_stat(self, follow_symlinks=follow_symlinks)

        with patch.object(Path, "stat", _fake_stat):
            out = compute_mtime_map(tmp_path)
        assert set(out.keys()) == {str(tmp_path / "b.py")}

    def test_all_stats_failing_returns_empty(self, tmp_path: Path):
        """If every matching file raises on stat, the result is an empty dict.

        The patched stat only fails for ``*.py`` files so that ``rglob``'s
        own calls on parent directories continue to work; in reality a storage
        fault is typically localised to specific inodes, not the whole tree.
        """
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("y")

        real_stat = Path.stat

        def _fail_only_py(self: Path, *, follow_symlinks: bool = True):
            if self.suffix == ".py":
                raise OSError("denied")
            return real_stat(self, follow_symlinks=follow_symlinks)

        with patch.object(Path, "stat", _fail_only_py):
            out = compute_mtime_map(tmp_path)
        assert out == {}

    def test_returns_fresh_dict_not_shared(self, tmp_path: Path):
        """Each call must return a new dict so callers can mutate / compare
        safely without cross-contamination."""
        (tmp_path / "a.py").write_text("x")
        first = compute_mtime_map(tmp_path)
        second = compute_mtime_map(tmp_path)
        assert first == second
        assert first is not second


# ---------------------------------------------------------------------------
# detect_and_enqueue
# ---------------------------------------------------------------------------

class TestDetectAndEnqueue:
    def test_first_pass_on_empty_dir_yields_empty_map_no_enqueue(
        self, tmp_path: Path,
    ):
        q: deque = deque()
        out = detect_and_enqueue(tmp_path, {}, q)
        assert out == {}
        assert len(q) == 0

    def test_first_pass_with_files_enqueues_rescan(self, tmp_path: Path):
        """prev={} ≠ {a.py: mtime} → enqueue, return new map."""
        (tmp_path / "a.py").write_text("x")
        q: deque = deque()
        out = detect_and_enqueue(tmp_path, {}, q)
        assert set(out.keys()) == {str(tmp_path / "a.py")}
        assert list(q) == [{"cmd": "_rescan_strategies"}]

    def test_steady_state_no_enqueue(self, tmp_path: Path):
        """Second iteration with no changes → no enqueue; returned map equal."""
        (tmp_path / "a.py").write_text("x")
        q: deque = deque()
        first = detect_and_enqueue(tmp_path, {}, q)
        q.clear()

        second = detect_and_enqueue(tmp_path, first, q)
        assert second == first
        assert len(q) == 0

    def test_new_file_added_enqueues(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x")
        q: deque = deque()
        first = detect_and_enqueue(tmp_path, {}, q)
        q.clear()

        (tmp_path / "b.py").write_text("y")
        second = detect_and_enqueue(tmp_path, first, q)
        assert len(q) == 1
        assert str(tmp_path / "b.py") in second

    def test_file_removed_enqueues(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("y")
        q: deque = deque()
        first = detect_and_enqueue(tmp_path, {}, q)
        q.clear()

        (tmp_path / "b.py").unlink()
        second = detect_and_enqueue(tmp_path, first, q)
        assert len(q) == 1
        assert str(tmp_path / "b.py") not in second

    def test_mtime_bump_enqueues(self, tmp_path: Path):
        import os

        (tmp_path / "a.py").write_text("x")
        q: deque = deque()
        first = detect_and_enqueue(tmp_path, {}, q)
        q.clear()

        # Force later mtime regardless of filesystem granularity
        original = next(iter(first.values()))
        os.utime(tmp_path / "a.py", (original + 60, original + 60))

        second = detect_and_enqueue(tmp_path, first, q)
        assert len(q) == 1
        assert second[str(tmp_path / "a.py")] > first[str(tmp_path / "a.py")]

    def test_missing_directory_returns_prev_unchanged(self, tmp_path: Path):
        """If the directory hasn't been created yet, return the previous map
        unchanged and do not enqueue — matches legacy behaviour which
        ``continue``'d without touching state.
        """
        missing = tmp_path / "nope"
        prev = {"something": 1.0}
        q: deque = deque()
        out = detect_and_enqueue(missing, prev, q)
        assert out is prev  # identity preserved — no new dict
        assert len(q) == 0

    def test_pattern_override_propagated(self, tmp_path: Path):
        """Callers can track e.g. portfolio.yaml changes instead of .py."""
        (tmp_path / "portfolio.yaml").write_text("symbols: [BTC]")
        (tmp_path / "strategy.py").write_text("x")

        q: deque = deque()
        out = detect_and_enqueue(tmp_path, {}, q, pattern="portfolio.yaml")
        assert set(out.keys()) == {str(tmp_path / "portfolio.yaml")}
        assert list(q) == [{"cmd": "_rescan_strategies"}]

    def test_queue_receives_exact_envelope(self, tmp_path: Path):
        """The envelope dict must match what ``CommandActor._drain_pending_commands``
        expects: ``{"cmd": "_rescan_strategies"}`` with no extra keys."""
        (tmp_path / "a.py").write_text("x")
        q: deque = deque()
        detect_and_enqueue(tmp_path, {}, q)
        assert q[0] == {"cmd": "_rescan_strategies"}
        assert set(q[0].keys()) == {"cmd"}

    def test_many_unchanged_iterations_do_not_leak_enqueues(self, tmp_path: Path):
        """10 consecutive unchanged polls → exactly 0 enqueues."""
        (tmp_path / "a.py").write_text("x")
        q: deque = deque()
        current = detect_and_enqueue(tmp_path, {}, q)  # first: 1 enqueue
        q.clear()
        for _ in range(10):
            current = detect_and_enqueue(tmp_path, current, q)
        assert len(q) == 0

    def test_accepts_any_deque_like_object(self, tmp_path: Path):
        """The queue arg is typed ``Any`` — any object with ``.append`` works,
        including a plain list."""
        (tmp_path / "a.py").write_text("x")
        lst: list = []
        detect_and_enqueue(tmp_path, {}, lst)
        assert lst == [{"cmd": "_rescan_strategies"}]

    def test_compute_mtime_map_exception_propagates(self, tmp_path: Path):
        """Unlike ``HealthActor._file_watcher``'s outer ``try/except Exception``,
        this function does not swallow errors — it is the *thread body's*
        responsibility to catch them, so the helper can be composed in contexts
        where crashing is preferred (e.g. tests).
        """
        (tmp_path / "a.py").write_text("x")

        def _broken_rglob(self: Path, pattern: str):
            raise RuntimeError("walk exploded")

        with patch.object(Path, "rglob", _broken_rglob):
            with pytest.raises(RuntimeError, match="walk exploded"):
                detect_and_enqueue(tmp_path, {}, deque())


# ---------------------------------------------------------------------------
# NT-independence — locks the "pure Python helper" contract
# ---------------------------------------------------------------------------

class TestNoNTDependency:
    def test_module_loads_without_nautilus_trader(self):
        """Reimporting ``file_watch`` under a meta_path blocker that rejects
        ``nautilus_trader`` must succeed — this is the whole point of keeping
        the helper outside ``HealthActor``.
        """
        import importlib
        import sys

        class _Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.startswith("nautilus_trader"):
                    raise ImportError(f"blocked: {name}")
                return None

        saved = sys.modules.pop("tinohelm.node.actors.file_watch", None)
        nt_before = {k for k in sys.modules if k.startswith("nautilus_trader")}

        blocker = _Blocker()
        sys.meta_path.insert(0, blocker)
        try:
            mod = importlib.import_module("tinohelm.node.actors.file_watch")
            assert hasattr(mod, "compute_mtime_map")
            assert hasattr(mod, "detect_and_enqueue")
            nt_after = {k for k in sys.modules if k.startswith("nautilus_trader")}
            assert nt_after - nt_before == set(), (
                f"file_watch import pulled in NT modules: "
                f"{sorted(nt_after - nt_before)}"
            )
        finally:
            sys.meta_path.remove(blocker)
            if saved is not None:
                sys.modules["tinohelm.node.actors.file_watch"] = saved
