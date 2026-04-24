"""Centralised filesystem path resolution for TinoHelm.

All modules that need a directory root must go through ``paths.get(field)``.
Fail-fast: settings loading failures or unknown fields raise
``PathConfigError`` — there is no silent fallback to ``~/.tino/*``.

Usage::

    from tinohelm.core.paths import paths

    cache_dir = paths.get("funding_rates")
    catalog_dir = paths.get("catalog")
    factors_dir = paths.get("factors_dir")  # derived field

Tests can install temporary overrides via :meth:`PathRegistry.override` and
tear them down with :meth:`PathRegistry.reset_overrides`.  The
``paths_override`` pytest fixture in ``tests/conftest.py`` wraps both calls
with automatic teardown.
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar


class PathConfigError(RuntimeError):
    """Raised when a path cannot be resolved from settings or an unknown
    field is requested.  The message always includes the failing field name
    and the underlying cause so Docker boot logs pinpoint the problem.

    Inherits from :class:`RuntimeError` (consistent with ``QueueFullError``,
    ``BacktestError`` and other project exceptions).
    """


class PathRegistry:
    """Resolve project paths from ``Settings.paths`` with test-time override.

    Fields exposed by :meth:`get`:

    * Direct fields (1:1 with ``PathSettings``):
      ``strategies``, ``actors``, ``catalog``, ``artifacts``, ``research``,
      ``logs``, ``funding_rates``, ``data_cache``, ``factor_cache``.
    * Derived fields (computed from a base field + subpath):
      ``factors_dir`` = ``research / "factors"``;
      ``universes_dir`` = ``research / "universes"``.

    Unknown fields raise :class:`PathConfigError`.  Tests can install
    temporary values via :meth:`override` and reset with
    :meth:`reset_overrides`.

    Design notes
    ------------
    * No existence check is performed — existence is the caller's
      responsibility (``FactorCache._ensure_dirs`` already does ``mkdir``,
      ``Universe.load_csv`` already raises ``FileNotFoundError``).
    * ``get()`` is *not* ``@lru_cache``'d because ``_overrides`` is mutable.
      The underlying ``get_settings()`` is already cached, so each
      ``getattr + resolve()`` call is O(1) with negligible overhead.
    * ``override()`` accepts any field name — not limited to registered fields
      — to keep test ergonomics flexible.
    """

    _DIRECT_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "strategies",
        "actors",
        "catalog",
        "artifacts",
        "research",
        "logs",
        "funding_rates",
        "data_cache",
        "factor_cache",
    })

    # {derived_name: (base_field, subpath)}
    _DERIVED_FIELDS: ClassVar[dict[str, tuple[str, str]]] = {
        "factors_dir": ("research", "factors"),
        "universes_dir": ("research", "universes"),
    }

    def __init__(self) -> None:
        self._overrides: dict[str, Path] = {}

    def get(self, field: str) -> Path:
        """Return the resolved :class:`~pathlib.Path` for *field*.

        Resolution order:

        1. In-memory override (see :meth:`override`).
        2. Derived field lookup (recurses into the base field).
        3. Direct field on ``settings.paths``.

        Parameters
        ----------
        field:
            Name of a registered direct or derived path field.

        Returns
        -------
        Path
            Absolute, normalised path.  The directory does not need to exist.

        Raises
        ------
        PathConfigError
            If *field* is not registered, or if settings cannot be loaded.
        """
        if field in self._overrides:
            return self._overrides[field]

        if field in self._DERIVED_FIELDS:
            base_field, subpath = self._DERIVED_FIELDS[field]
            return self._normalise(self.get(base_field) / subpath)

        if field not in self._DIRECT_FIELDS:
            raise PathConfigError(
                f"Unknown path field {field!r}. "
                f"Known direct fields: {sorted(self._DIRECT_FIELDS)}; "
                f"known derived fields: {sorted(self._DERIVED_FIELDS)}."
            )

        try:
            from tinohelm.core.config import get_settings

            configured: Path = getattr(get_settings().paths, field)
        except Exception as exc:  # fail-fast — no silent fallback
            raise PathConfigError(
                f"Failed to resolve settings.paths.{field}: {exc!r}"
            ) from exc

        return self._normalise(configured)

    @staticmethod
    def _normalise(path: Path) -> Path:
        """Resolve a Path to an absolute location.

        * Absolute paths pass through unchanged.
        * Relative paths resolve against CWD (``Path.resolve()``).  The
          directory does **not** need to exist yet — callers create it
          lazily via ``mkdir(parents=True, exist_ok=True)``.
        """
        if path.is_absolute():
            return path
        return path.resolve()

    def override(self, field: str, value: Path | str) -> None:
        """Install a test-time override for *field*.

        Accepts any field name (direct, derived, or even new names for
        test-only keys) to keep test ergonomics flexible.  Callers MUST
        pair this with :meth:`reset_overrides` in teardown (the
        ``paths_override`` fixture does this automatically).

        Parameters
        ----------
        field:
            Field name to override (any string accepted).
        value:
            The :class:`~pathlib.Path` (or string coerced to one) to use.
        """
        self._overrides[field] = Path(value)

    def reset_overrides(self) -> None:
        """Clear every installed override.  Idempotent."""
        self._overrides.clear()


#: Module-level singleton.  Import as ``from tinohelm.core.paths import paths``.
paths = PathRegistry()
