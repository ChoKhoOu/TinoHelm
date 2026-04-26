"""Signal registry — discovers and tracks ``@signal``-decorated functions.

Mirrors :class:`tinohelm.factor.registry.Registry` but is purpose-built for
the signal layer.  Two scan sources:

1. **User signals**: ``.py`` files under ``paths.get("signals_dir")``
   (default ``research/signals/``).  Loaded via the shared
   :func:`~tinohelm.strategy.module_loader.load_module_from_file` helper
   (sys.path cleanup + boundary check).
2. **Built-in signals**: not used yet — the 5 built-in *kernels* live in
   :mod:`tinohelm.signal.kernels` but have no @signal decorations of
   their own.  Users assemble a signal by writing a kernel function that
   delegates to one of the 5 built-ins (or runs custom logic) and wraps
   it with ``@signal(method=...)``.  The hook is kept open for future
   built-in shipped signals.

Incremental rescans
-------------------
``_spec_cache`` stores ``{name: (file_hash_key, SignalSpec)}``.  On
:meth:`scan`, only files whose code (sha256 of bytes) changed since the
last call are reloaded.

Usage
-----
::

    registry = SignalRegistry()
    registry.scan()
    kernel = registry.get_kernel("my_signal")
    spec = registry.get_spec("my_signal")
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import logging
import pkgutil
from pathlib import Path
from typing import Callable

from tinohelm.core.paths import paths
from tinohelm.signal.types import SignalSpec
from tinohelm.strategy.module_loader import load_module_from_file

logger = logging.getLogger(__name__)

_DEFAULT_BUILTINS_PACKAGE = "tinohelm.signal.builtins"


def _file_hash(file_path: Path) -> str:
    """Return SHA-256 hex digest of a file's raw bytes."""
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def _collect_specs_from_module(module: object) -> dict[str, tuple[SignalSpec, Callable]]:  # type: ignore[type-arg]
    """Return ``{name: (spec, func)}`` for ``@signal``-decorated callables."""
    results: dict[str, tuple[SignalSpec, Callable]] = {}  # type: ignore[type-arg]
    for _attr_name, obj in inspect.getmembers(module, callable):
        spec = getattr(obj, "__signal_spec__", None)
        if isinstance(spec, SignalSpec):
            results[spec.name] = (spec, obj)
    return results


class SignalRegistry:
    """Discovers and caches ``@signal``-decorated functions.

    Parameters
    ----------
    signals_dir:
        Directory to scan for user-defined signal ``.py`` files.
        Defaults to ``paths.get("signals_dir")`` (a derived field that
        resolves to ``research/signals/``).
    builtins_package:
        Dotted package name for built-in signals.  Defaults to
        ``"tinohelm.signal.builtins"``.  The package may not exist yet —
        ImportError is silently swallowed.
    """

    def __init__(
        self,
        signals_dir: Path | None = None,
        builtins_package: str = _DEFAULT_BUILTINS_PACKAGE,
    ) -> None:
        self._signals_dir: Path = (
            Path(signals_dir)
            if signals_dir is not None
            else paths.get("signals_dir")
        )
        self._builtins_package: str = builtins_package

        # {name: (file_hash_key, SignalSpec)}
        self._spec_cache: dict[str, tuple[str, SignalSpec]] = {}
        # {name: Callable} — separate so spec_cache stays serialisable
        self._kernel_cache: dict[str, Callable] = {}  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> dict[str, SignalSpec]:
        """Scan all sources and return ``{name: SignalSpec}``.

        Built-in signals are collected first; user signals are merged on
        top, so user definitions override same-named built-ins.  Only
        files whose content (sha256 of bytes) changed since the last call
        are reloaded.
        """
        builtin_specs = self._scan_builtins()
        user_specs = self._scan_user_dir()
        merged: dict[str, SignalSpec] = {**builtin_specs, **user_specs}

        # Update spec cache with current merged state.
        for name, spec in merged.items():
            current_hash = self._spec_cache.get(name, ("", None))[0]
            self._spec_cache[name] = (current_hash, spec)

        # Remove stale entries no longer present in any source.
        stale = set(self._spec_cache) - set(merged)
        for name in stale:
            self._spec_cache.pop(name)
            self._kernel_cache.pop(name, None)
            logger.debug("Removed stale signal %r from cache", name)

        return merged

    def get_spec(self, name: str) -> SignalSpec | None:
        """Return :class:`SignalSpec` for *name*, or ``None`` if absent."""
        entry = self._spec_cache.get(name)
        return entry[1] if entry is not None else None

    def get_all_specs(self) -> list[SignalSpec]:
        """Return all registered :class:`SignalSpec` objects."""
        return [spec for _hash, spec in self._spec_cache.values()]

    def get_kernel(self, name: str) -> Callable:  # type: ignore[type-arg]
        """Return the callable signal kernel for *name*.

        Raises
        ------
        KeyError
            If *name* is not registered.  Caller should call
            :meth:`scan` first.
        """
        if name not in self._kernel_cache:
            raise KeyError(
                f"Signal {name!r} not found in registry. Did you call scan()?"
            )
        return self._kernel_cache[name]

    def list_signals(self) -> list[str]:
        """Return a sorted list of registered signal names."""
        return sorted(self._spec_cache.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan_builtins(self) -> dict[str, SignalSpec]:
        """Import built-in signal package, collect specs.

        Returns an empty dict if the package does not exist.
        """
        try:
            builtins_pkg = importlib.import_module(self._builtins_package)
        except ImportError:
            logger.debug(
                "Built-in signals package %r not found — skipping",
                self._builtins_package,
            )
            return {}

        collected: dict[str, SignalSpec] = {}

        # Top-level module
        for name, (spec, func) in _collect_specs_from_module(builtins_pkg).items():
            collected[name] = spec
            self._kernel_cache[name] = func

        # Sub-modules
        pkg_path = getattr(builtins_pkg, "__path__", [])
        pkg_prefix = builtins_pkg.__name__ + "."
        for module_info in pkgutil.iter_modules(pkg_path, prefix=pkg_prefix):
            try:
                sub_mod = importlib.import_module(module_info.name)
            except ImportError as exc:
                logger.warning(
                    "Failed to import built-in signal sub-module %r: %s",
                    module_info.name,
                    exc,
                )
                continue
            for name, (spec, func) in _collect_specs_from_module(sub_mod).items():
                collected[name] = spec
                self._kernel_cache[name] = func

        logger.debug(
            "Built-in scan: found %d signal(s) from %r",
            len(collected),
            self._builtins_package,
        )
        return collected

    def _scan_user_dir(self) -> dict[str, SignalSpec]:
        """Scan ``signals_dir`` for ``.py`` files containing ``@signal``.

        Skips files whose name starts with ``_``.  Uses incremental
        code-hash detection: files unchanged since the last scan reuse
        cached entries.
        """
        if not self._signals_dir.exists():
            logger.debug(
                "User signal dir %s does not exist — skipping",
                self._signals_dir,
            )
            return {}

        collected: dict[str, SignalSpec] = {}

        py_files = sorted(
            f
            for f in self._signals_dir.rglob("*.py")
            if not f.name.startswith("_")
        )

        for py_file in py_files:
            try:
                current_hash = _file_hash(py_file)
            except OSError as exc:
                logger.warning("Cannot hash %s: %s", py_file, exc)
                continue

            cache_key = f"user:{py_file}:{current_hash}"
            cached_names = [
                n for n, (h, _) in self._spec_cache.items() if h == cache_key
            ]
            if cached_names:
                for n in cached_names:
                    collected[n] = self._spec_cache[n][1]
                logger.debug("Cache hit for %s (hash unchanged)", py_file.name)
                continue

            try:
                mod = load_module_from_file(
                    py_file, boundary_dir=self._signals_dir
                )
            except Exception as exc:
                logger.warning(
                    "Failed to load user signal file %s: %s", py_file, exc
                )
                continue

            file_specs = _collect_specs_from_module(mod)
            if not file_specs:
                logger.debug("No @signal functions found in %s", py_file.name)
                continue

            for name, (spec, func) in file_specs.items():
                self._spec_cache[name] = (cache_key, spec)
                self._kernel_cache[name] = func
                collected[name] = spec
                logger.debug(
                    "Registered user signal %r from %s (hash=%s…)",
                    name,
                    py_file.name,
                    current_hash[:8],
                )

        return collected
