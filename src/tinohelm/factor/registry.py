"""Factor registry — discovers and tracks ``@factor``-decorated functions.

Scan flow
---------
1. **User factors**: Walk ``user_dir`` (default resolved from
   ``settings.paths.research / "factors"``; falls back to
   ``~/.tino/research/factors/`` when the configured path does not exist)
   for all ``.py`` files.  Each file is loaded via
   :func:`~tinohelm.strategy.module_loader.load_module_from_file` (safe
   importlib pattern — try/finally sys.path cleanup + sys.modules eviction).
   Functions that carry ``__factor_spec__`` are collected.

2. **Built-in factors**: Attempt to import ``tinohelm.factor.builtins``
   package.  If it does not exist yet (s12 pending), the ImportError is
   silently swallowed.  Sub-modules are iterated and functions with
   ``__factor_spec__`` are collected.

3. **Merge / priority**: User factors override built-ins with the same name.

4. **Incremental rescans**: ``_spec_cache`` stores ``{name: (code_hash,
   FactorSpec)}``.  On ``scan()``, only files whose code_hash changed are
   reloaded — unchanged entries are reused from cache.
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
from tinohelm.factor.types import FactorSpec
from tinohelm.strategy.module_loader import load_module_from_file

logger = logging.getLogger(__name__)

_DEFAULT_BUILTINS_PACKAGE = "tinohelm.factor.builtins"


def _file_hash(file_path: Path) -> str:
    """Return SHA-256 hex digest of a file's raw bytes."""
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def _collect_specs_from_module(module: object) -> dict[str, tuple[FactorSpec, Callable]]:  # type: ignore[type-arg]
    """Return ``{name: (spec, func)}`` for all ``@factor``-decorated callables in a module."""
    results: dict[str, tuple[FactorSpec, Callable]] = {}  # type: ignore[type-arg]
    for _attr_name, obj in inspect.getmembers(module, callable):
        spec = getattr(obj, "__factor_spec__", None)
        if isinstance(spec, FactorSpec):
            results[spec.name] = (spec, obj)
    return results


class Registry:
    """Discovers and caches ``@factor``-decorated functions.

    Parameters
    ----------
    user_dir:
        Directory to scan for user-defined factor ``.py`` files.
        Defaults to ``paths.get("factors_dir")`` from
        :class:`tinohelm.core.paths.PathRegistry`.
    builtins_package:
        Dotted package name for built-in factors.
        Defaults to ``"tinohelm.factor.builtins"``.
    """

    def __init__(
        self,
        user_dir: Path | None = None,
        builtins_package: str = _DEFAULT_BUILTINS_PACKAGE,
    ) -> None:
        self._user_dir: Path = Path(user_dir) if user_dir is not None else paths.get("factors_dir")
        self._builtins_package: str = builtins_package

        # {name: (code_hash, FactorSpec)}
        self._spec_cache: dict[str, tuple[str, FactorSpec]] = {}
        # {name: Callable}  — kept separate so spec_cache stays serialisable
        self._kernel_cache: dict[str, Callable] = {}  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> dict[str, FactorSpec]:
        """Scan all sources and return ``{name: FactorSpec}``.

        Built-in factors are collected first; user factors are merged on top,
        so user definitions override same-named built-ins.  Only files whose
        content (code_hash) changed since the last call are reloaded.

        Returns
        -------
        dict[str, FactorSpec]
            Mapping from factor name to its specification.
        """
        # 1. Collect built-in factors (may not exist yet)
        builtin_specs = self._scan_builtins()

        # 2. Collect user factors (incremental, hash-based)
        user_specs = self._scan_user_dir()

        # 3. Merge: user overrides builtins
        merged: dict[str, FactorSpec] = {**builtin_specs, **user_specs}

        # 4. Update spec cache with current merged state
        for name, spec in merged.items():
            kernel = self._kernel_cache.get(name)
            current_hash = self._spec_cache.get(name, ("", None))[0]
            self._spec_cache[name] = (current_hash, spec)

        # 5. Remove stale entries no longer present in any source
        stale = set(self._spec_cache) - set(merged)
        for name in stale:
            self._spec_cache.pop(name)
            self._kernel_cache.pop(name, None)
            logger.debug("Removed stale factor %r from cache", name)

        return merged

    def get_spec(self, name: str) -> FactorSpec | None:
        """Return the :class:`FactorSpec` for *name*, or ``None`` if not found."""
        entry = self._spec_cache.get(name)
        return entry[1] if entry is not None else None

    def get_all_specs(self) -> list[FactorSpec]:
        """Return all registered :class:`FactorSpec` objects."""
        return [spec for _hash, spec in self._spec_cache.values()]

    def get_kernel(self, name: str) -> Callable:  # type: ignore[type-arg]
        """Return the callable factor function for *name*.

        Raises
        ------
        KeyError
            If *name* is not registered.
        """
        if name not in self._kernel_cache:
            raise KeyError(f"Factor {name!r} not found in registry. Did you call scan()?")
        return self._kernel_cache[name]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan_builtins(self) -> dict[str, FactorSpec]:
        """Attempt to import the builtins package and collect all factor specs.

        Returns an empty dict if the package does not exist (s12 pending).
        """
        try:
            builtins_pkg = importlib.import_module(self._builtins_package)
        except ImportError:
            logger.debug(
                "Built-in factors package %r not found (s12 pending) — skipping",
                self._builtins_package,
            )
            return {}

        collected: dict[str, FactorSpec] = {}

        # Collect from the package __init__ itself
        for name, (spec, func) in _collect_specs_from_module(builtins_pkg).items():
            collected[name] = spec
            self._kernel_cache[name] = func

        # Walk sub-modules
        pkg_path = getattr(builtins_pkg, "__path__", [])
        pkg_prefix = builtins_pkg.__name__ + "."
        for module_info in pkgutil.iter_modules(pkg_path, prefix=pkg_prefix):
            try:
                sub_mod = importlib.import_module(module_info.name)
            except ImportError as exc:
                logger.warning(
                    "Failed to import built-in sub-module %r: %s",
                    module_info.name,
                    exc,
                )
                continue

            for name, (spec, func) in _collect_specs_from_module(sub_mod).items():
                collected[name] = spec
                self._kernel_cache[name] = func

        logger.debug(
            "Built-in scan: found %d factor(s) from %r",
            len(collected),
            self._builtins_package,
        )
        return collected

    def _scan_user_dir(self) -> dict[str, FactorSpec]:
        """Scan ``user_dir`` for ``.py`` files containing ``@factor`` functions.

        Uses incremental code-hash detection: files unchanged since the last
        scan reuse cached entries.  Only changed or new files are re-imported.

        Returns
        -------
        dict[str, FactorSpec]
        """
        if not self._user_dir.exists():
            logger.debug("User factor dir %s does not exist — skipping", self._user_dir)
            return {}

        collected: dict[str, FactorSpec] = {}

        py_files = sorted(
            f for f in self._user_dir.rglob("*.py") if not f.name.startswith("_")
        )

        for py_file in py_files:
            try:
                current_hash = _file_hash(py_file)
            except OSError as exc:
                logger.warning("Cannot hash %s: %s", py_file, exc)
                continue

            # Check whether we already have up-to-date cached entries for all
            # factors coming from this file.  We store file-level hash per
            # discovered factor name, so we iterate existing cache to find ones
            # originating from this file.
            #
            # Strategy: reload the file, but skip if hash unchanged and we have
            # at least one cached spec from this file already.
            cached_names = [
                n for n, (h, _) in self._spec_cache.items()
                if h == f"user:{py_file}:{current_hash}"
            ]
            if cached_names:
                # All specs from this file are already cached at this hash
                for n in cached_names:
                    collected[n] = self._spec_cache[n][1]
                logger.debug("Cache hit for %s (hash unchanged)", py_file.name)
                continue

            # Load fresh
            try:
                mod = load_module_from_file(py_file, boundary_dir=self._user_dir)
            except Exception as exc:
                logger.warning("Failed to load user factor file %s: %s", py_file, exc)
                continue

            file_specs = _collect_specs_from_module(mod)
            if not file_specs:
                logger.debug("No @factor functions found in %s", py_file.name)
                continue

            for name, (spec, func) in file_specs.items():
                cache_key = f"user:{py_file}:{current_hash}"
                self._spec_cache[name] = (cache_key, spec)
                self._kernel_cache[name] = func
                collected[name] = spec
                logger.debug(
                    "Registered user factor %r from %s (hash=%s…)",
                    name,
                    py_file.name,
                    current_hash[:8],
                )

        return collected
