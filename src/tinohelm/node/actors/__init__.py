"""Node actors — single-responsibility actors for event bridging and lifecycle.

The five NT-dependent Actor classes (``SnapshotActor``, ``CommandActor``,
``DbWriterActor``, ``HealthActor``, ``MetricsActor``) are re-exported lazily
via :pep:`562` so that pure-Python helpers co-located in this package
(``rate_limit``, ``serialize``, ``_utils``, ``command_dispatch``, ``file_watch``)
can be imported and unit-tested without pulling in NautilusTrader.

Submodule access (``from tinohelm.node.actors import command_dispatch``) always
works; attribute access (``tinohelm.node.actors.CommandActor``) triggers the NT
import on first use. The set of lazy exports and their config pairs mirror the
legacy eager-import contract.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "SnapshotActor", "SnapshotActorConfig",
    "CommandActor", "CommandActorConfig",
    "DbWriterActor", "DbWriterActorConfig",
    "HealthActor", "HealthActorConfig",
    "MetricsActor", "MetricsActorConfig",
]


_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "SnapshotActor": ("snapshot_actor", "SnapshotActor"),
    "SnapshotActorConfig": ("snapshot_actor", "SnapshotActorConfig"),
    "CommandActor": ("command_actor", "CommandActor"),
    "CommandActorConfig": ("command_actor", "CommandActorConfig"),
    "DbWriterActor": ("db_writer_actor", "DbWriterActor"),
    "DbWriterActorConfig": ("db_writer_actor", "DbWriterActorConfig"),
    "HealthActor": ("health_actor", "HealthActor"),
    "HealthActorConfig": ("health_actor", "HealthActorConfig"),
    "MetricsActor": ("metrics_actor", "MetricsActor"),
    "MetricsActorConfig": ("metrics_actor", "MetricsActorConfig"),
}


def __getattr__(name: str) -> Any:
    """Lazy re-export of NT-dependent Actor classes (PEP 562).

    Keeps ``import tinohelm.node.actors`` NT-free until a caller actually
    requests one of the Actor symbols.
    """
    if name in _LAZY_EXPORTS:
        submodule, attr = _LAZY_EXPORTS[name]
        import importlib
        module = importlib.import_module(f"tinohelm.node.actors.{submodule}")
        return getattr(module, attr)
    raise AttributeError(f"module 'tinohelm.node.actors' has no attribute {name!r}")


def __dir__() -> list[str]:
    """Expose lazy names for tab completion / ``dir()`` introspection."""
    return sorted(list(_LAZY_EXPORTS.keys()))
