"""NT-free helpers for ``tinohelm.strategy.scaffold``.

The public entry point ``generate_scaffold`` is a thin orchestrator; all of its
decision points (name validation, class-name derivation, template rendering,
path-traversal check) live here so they can be exercised by fast unit tests
without importing NautilusTrader.

This module is imported by :mod:`tinohelm.strategy.scaffold`; keep it free of
third-party / NT dependencies beyond the standard library.
"""
from __future__ import annotations

import re
from pathlib import Path

from tinohelm.core.utils import is_within_dir

__all__ = [
    "IDENTIFIER_RE",
    "validate_identifier",
    "derive_class_name",
    "render_scaffold",
    "resolve_new_strategy_path",
]

#: Match a valid Python identifier. ``re.fullmatch`` semantics via ``^...$``.
IDENTIFIER_RE: re.Pattern[str] = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def validate_identifier(name: str) -> None:
    """Raise :class:`ValueError` when *name* is not a valid Python identifier.

    Used to guard scaffold names — the generated file is imported as a module
    named ``name``, so the filename ``f"{name}.py"`` must itself be a legal
    identifier.
    """
    if not isinstance(name, str) or not IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Invalid strategy name: must be a valid Python identifier, got {name!r}"
        )


def derive_class_name(name: str) -> str:
    """Convert a ``snake_case`` name to ``PascalCase``.

    Each underscore-delimited segment is capitalised and the segments are
    concatenated without a separator. Empty segments (leading / trailing /
    consecutive underscores) collapse to the empty string, matching the
    historical behaviour of ``"".join(word.capitalize() for word in
    name.split("_"))``.
    """
    return "".join(word.capitalize() for word in name.split("_"))


def render_scaffold(name: str) -> str:
    """Return the rendered scaffold body for a strategy *name*.

    The caller is responsible for having validated *name* already (via
    :func:`validate_identifier`). Rendering is a pure ``str.format`` call
    against the ``STRATEGY_SCAFFOLD`` template; we import it lazily to avoid
    creating a circular dependency with :mod:`tinohelm.strategy.scaffold`.
    """
    from tinohelm.strategy.scaffold import STRATEGY_SCAFFOLD

    return STRATEGY_SCAFFOLD.format(name=name, class_name=derive_class_name(name))


def resolve_new_strategy_path(strategies_dir: str | Path, name: str) -> Path:
    """Resolve the path for a new strategy file and check boundary safety.

    Returns the resolved path ``{strategies_dir}/{name}.py``. The caller is
    responsible for checking ``.exists()`` if they want to reject overwrites —
    this helper performs only the boundary check.

    Raises
    ------
    ValueError
        If the resolved path falls outside *strategies_dir* (e.g. because
        *name* contains path-traversal sequences). Because
        :func:`validate_identifier` is typically called first, this should be
        unreachable from well-behaved callers; the check is defense-in-depth.
    """
    base = Path(strategies_dir).resolve()
    candidate = (base / f"{name}.py").resolve()
    if not is_within_dir(candidate, base):
        raise ValueError("Path traversal detected in strategy name")
    return candidate
