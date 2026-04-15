"""StrategyRegistry — tracks available and running strategies on a node.

Pure Python class with zero NT dependencies — fully testable with plain pytest.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StrategyEntry:
    """State of a single strategy on the node."""
    name: str
    source_path: Path
    state: str = "available"  # available | starting | running | paused | flattening
    strategy_ids: list[str] = field(default_factory=list)
    order_id_tag_prefix: str = ""
    tag_offset: int = 0
    was_running: bool = False


class StrategyRegistry:
    """Registry of discovered and running strategies.

    Manages:
    - Discovery: scan strategies dir for .py files (skipping files starting with _)
    - Tag allocation: globally unique order_id_tag with prefix scheme
    - State tracking: available -> starting -> running -> paused/flattening -> available
    """

    def __init__(self) -> None:
        self._strategies: dict[str, StrategyEntry] = {}
        self._next_tag_offset: int = 0
        self._strategy_to_bundle: dict[str, str] = {}
        self._used_prefixes: dict[str, str] = {}  # prefix -> strategy_name

    def scan(self, strategies_dir: Path) -> list[str]:
        """Scan directory for strategy .py files (skipping files starting with _).

        Strategy name = file stem. Source path = the .py file's parent dir.
        Returns list of change descriptions (for logging).
        """
        changes: list[str] = []
        if not strategies_dir.exists():
            return changes

        # Discover valid NT strategy files via unified module_loader
        from tinohelm.strategy.module_loader import scan_valid_strategy_files

        valid_files = scan_valid_strategy_files(strategies_dir)
        current_files: dict[str, Path] = {
            name: path.parent for name, path in valid_files.items()
        }

        # Detect new strategies
        for name, path in current_files.items():
            if name not in self._strategies:
                try:
                    self.register(name, path)
                except ValueError as exc:
                    logger.warning("Skipping strategy '%s': %s", name, exc)
                    changes.append(f"skipped_collision:{name}")
                    continue
                changes.append(f"added:{name}")

        # Detect deleted strategies (only remove if not running)
        for name in list(self._strategies.keys()):
            if name not in current_files:
                entry = self._strategies[name]
                if entry.state in ("available", "starting"):
                    del self._strategies[name]
                    # Clean up prefix mapping
                    if entry.order_id_tag_prefix in self._used_prefixes:
                        if self._used_prefixes[entry.order_id_tag_prefix] == name:
                            del self._used_prefixes[entry.order_id_tag_prefix]
                    changes.append(f"removed:{name}")
                else:
                    changes.append(f"deleted_but_running:{name}")

        return changes

    def register(
        self, name: str, source_path: Path, *, manual_tag: str | None = None,
    ) -> StrategyEntry:
        """Register a strategy and assign tag prefix.

        If *manual_tag* is provided, it is used verbatim.  Otherwise a tag is
        derived from the strategy name using ``_derive_tag()``.  Collisions raise
        ``ValueError`` — the user must resolve by renaming the strategy file.
        """
        if name in self._strategies:
            return self._strategies[name]

        tag = manual_tag or _derive_tag(name)
        if tag in self._used_prefixes:
            existing_owner = self._used_prefixes[tag]
            if manual_tag:
                raise ValueError(
                    f"Tag '{tag}' already used by '{existing_owner}', pick another"
                )
            raise ValueError(
                f"Auto-derived tag '{tag}' collides between '{name}' and "
                f"'{existing_owner}'. Rename one of the strategy files to resolve."
            )

        entry = StrategyEntry(
            name=name,
            source_path=source_path,
            order_id_tag_prefix=tag,
            tag_offset=self._next_tag_offset,
        )
        self._strategies[name] = entry
        self._used_prefixes[tag] = name
        return entry

    def allocate_tags(
        self,
        name: str,
        count: int,
        existing_tags: set[str],
    ) -> list[str]:
        """Generate globally unique order_id_tags for a strategy.

        Args:
            name: Strategy name (must be registered).
            count: Number of tags needed (one per symbol).
            existing_tags: Set of strategy ID strings currently on the trader,
                          used for final collision validation.

        Returns:
            List of tag strings like ["mom000", "mom001"].

        Raises:
            ValueError: If strategy not found, or tag collision detected.
        """
        entry = self._strategies.get(name)
        if entry is None:
            raise ValueError(f"Strategy '{name}' not registered")

        prefix = entry.order_id_tag_prefix
        offset = self._next_tag_offset
        tags = []

        for i in range(count):
            if offset + i > 999:
                raise ValueError(
                    f"Tag offset overflow: {offset + i} exceeds 3-digit format. "
                    f"Too many cumulative strategy allocations. Consider restarting the node."
                )
            tag = f"{prefix}{offset + i:03d}"
            # Check for collision with existing strategy IDs
            # Strategy ID format: "{ClassName}-{tag}"
            for existing in existing_tags:
                if existing.endswith(f"-{tag}"):
                    raise ValueError(
                        f"order_id_tag collision: '{tag}' conflicts with "
                        f"existing strategy '{existing}'"
                    )
            tags.append(tag)

        # Update offset only after successful allocation
        entry.tag_offset = offset
        self._next_tag_offset = offset + count
        return tags

    def mark_starting(self, name: str) -> None:
        entry = self._strategies.get(name)
        if entry:
            entry.state = "starting"

    def mark_running(self, name: str, strategy_ids: list[str]) -> None:
        entry = self._strategies.get(name)
        if entry:
            entry.state = "running"
            entry.strategy_ids = strategy_ids
            for sid in strategy_ids:
                self._strategy_to_bundle[sid] = name

    def mark_paused(self, name: str) -> None:
        entry = self._strategies.get(name)
        if entry:
            entry.state = "paused"

    def mark_flattening(self, name: str) -> None:
        entry = self._strategies.get(name)
        if entry:
            entry.state = "flattening"

    def mark_stopped(self, name: str) -> None:
        entry = self._strategies.get(name)
        if entry:
            for sid in entry.strategy_ids:
                self._strategy_to_bundle.pop(sid, None)
            entry.strategy_ids = []
            entry.state = "available"
            entry.was_running = False

    def get(self, name: str) -> StrategyEntry | None:
        return self._strategies.get(name)

    def get_bundle_for_strategy(self, strategy_id: str) -> str | None:
        return self._strategy_to_bundle.get(strategy_id)

    def available(self) -> list[str]:
        return [n for n, e in self._strategies.items() if e.state == "available"]

    def get_all_states(self) -> dict[str, dict[str, Any]]:
        """Return all strategies with their state for API/heartbeat."""
        result = {}
        for name, entry in self._strategies.items():
            result[name] = {
                "state": entry.state,
                "strategy_ids": entry.strategy_ids,
                "source_path": str(entry.source_path),
                "order_id_tag_prefix": entry.order_id_tag_prefix,
                "was_running": entry.was_running,
            }
        return result

    def to_dict(self) -> dict[str, Any]:
        """Serializable snapshot for Redis persistence."""
        return {
            "strategies": self.get_all_states(),
            "next_tag_offset": self._next_tag_offset,
            "was_running": [
                name for name, e in self._strategies.items()
                if e.state in ("running", "paused", "flattening")
            ],
        }

    def restore_was_running(self, saved_state: dict[str, Any]) -> None:
        """Mark strategies that were running before last restart.

        Note: ``next_tag_offset`` is NOT restored — container restart kills
        all strategies, so offsets reset to 0.  The ``existing_tags`` collision
        check in ``allocate_tags()`` is the real safety net.
        """
        was_running = saved_state.get("was_running", [])
        for name in was_running:
            entry = self._strategies.get(name)
            if entry:
                entry.was_running = True


def _derive_tag(name: str) -> str:
    """Derive a readable tag from a strategy file stem.

    Takes the first letter of each ``_``-separated word, but preserves
    version numbers (``v33`` → ``33``, plain digits kept as-is).

    Examples::

        multi_factor_v33  → "mf33"
        multi_factor_v32  → "mf32"
        momentum_btc      → "mb"
        mean_reversion_v1 → "mr1"
        stat_arb_btc_eth  → "sabe"
        trend_following   → "tf"
    """
    parts = name.split("_")
    tag = ""
    for part in parts:
        if not part:
            continue
        if part.startswith("v") and part[1:].isdigit():
            tag += part[1:]       # "v33" → "33"
        elif part.isdigit():
            tag += part           # pure digits kept
        else:
            tag += part[0].lower()
    return tag
