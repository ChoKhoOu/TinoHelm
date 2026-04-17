"""Tests for StrategyRegistry and _derive_tag — pure-Python node subsystem.

StrategyRegistry is a plain Python class with zero NT dependencies, so every
branch is covered here with standard pytest primitives.

Coverage targets (matching `node/strategy_registry.py`):

- ``_derive_tag``              — auto-derived order_id_tag prefix from file name
- ``StrategyRegistry.scan``    — filesystem discovery with delete protection
- ``StrategyRegistry.register``— manual vs auto tag, collision handling
- ``StrategyRegistry.allocate_tags`` — sequential global offset, collision guard,
                                       overflow, atomicity on failure
- State machine transitions    — starting / running / paused / flattening / stopped
- ``to_dict`` / ``restore_was_running`` — Redis-persisted snapshot round-trip
- Query helpers                — ``get``, ``get_bundle_for_strategy``,
                                  ``available``, ``get_all_states``
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tinohelm.node.strategy_registry import (
    StrategyEntry,
    StrategyRegistry,
    _derive_tag,
)


# ---------------------------------------------------------------------------
# _derive_tag
# ---------------------------------------------------------------------------


class TestDeriveTag:
    """Auto-derivation of order_id_tag prefixes from strategy file stems.

    Contract: first letter of each underscore-separated word, but:
    - version markers `vNN` collapse to `NN`
    - pure digit words are kept as-is
    - empty parts (consecutive or trailing underscores) are skipped
    """

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("momentum_btc", "mb"),
            ("multi_factor_v33", "mf33"),
            ("multi_factor_v32", "mf32"),
            ("mean_reversion_v1", "mr1"),
            ("stat_arb_btc_eth", "sabe"),
            ("trend_following", "tf"),
            ("a", "a"),
            ("single", "s"),
        ],
    )
    def test_docstring_examples(self, name: str, expected: str) -> None:
        assert _derive_tag(name) == expected

    def test_uppercase_first_letter_is_lowered(self) -> None:
        assert _derive_tag("Momentum_Btc") == "mb"

    def test_pure_digit_word_kept_verbatim(self) -> None:
        # A plain digit word should be preserved in the tag.
        assert _derive_tag("mom_7") == "m7"
        assert _derive_tag("strat_100") == "s100"

    def test_v_prefix_is_stripped_only_when_followed_by_digits(self) -> None:
        # "vX" where X is not digits is treated as a normal word
        assert _derive_tag("value_strategy") == "vs"
        # "v33" acts as a version marker and contributes just "33"
        assert _derive_tag("x_v42") == "x42"

    def test_uppercase_v_prefix_is_not_treated_as_version(self) -> None:
        # Only lowercase "v" is recognised as the version prefix.
        assert _derive_tag("V42") == "v"

    def test_consecutive_underscores_are_skipped(self) -> None:
        # "a__b" -> ["a", "", "b"]; the empty entry must not index into
        # part[0] and raise. "__leading" -> ["", "", "leading"].
        assert _derive_tag("a__b") == "ab"
        assert _derive_tag("__leading") == "l"
        assert _derive_tag("trailing__") == "t"

    def test_empty_name_returns_empty_tag(self) -> None:
        # "" -> [""] after split; the loop hits the skip branch and we return "".
        assert _derive_tag("") == ""


# ---------------------------------------------------------------------------
# StrategyEntry
# ---------------------------------------------------------------------------


class TestStrategyEntry:
    """StrategyEntry is a dataclass — verify defaults and field-level defaults."""

    def test_defaults(self) -> None:
        entry = StrategyEntry(name="x", source_path=Path("/tmp"))
        assert entry.state == "available"
        assert entry.strategy_ids == []
        assert entry.order_id_tag_prefix == ""
        assert entry.tag_offset == 0
        assert entry.was_running is False

    def test_strategy_ids_is_independent_per_instance(self) -> None:
        # default_factory must not share the list between entries.
        a = StrategyEntry(name="a", source_path=Path("/a"))
        b = StrategyEntry(name="b", source_path=Path("/b"))
        a.strategy_ids.append("A-000")
        assert b.strategy_ids == []


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


class TestRegister:
    """register() assigns prefixes and guards against collisions."""

    def test_register_new_strategy_uses_derived_tag(self) -> None:
        reg = StrategyRegistry()
        entry = reg.register("momentum_btc", Path("/tmp/mb"))

        assert entry.name == "momentum_btc"
        assert entry.source_path == Path("/tmp/mb")
        assert entry.order_id_tag_prefix == "mb"
        assert reg._used_prefixes == {"mb": "momentum_btc"}

    def test_register_manual_tag_wins_over_derivation(self) -> None:
        reg = StrategyRegistry()
        entry = reg.register(
            "momentum_btc", Path("/tmp/mb"), manual_tag="CUSTOM",
        )
        assert entry.order_id_tag_prefix == "CUSTOM"

    def test_register_idempotent_returns_existing_entry(self) -> None:
        """Re-registering the same name returns the existing entry unchanged."""
        reg = StrategyRegistry()
        first = reg.register("mom", Path("/tmp/a"))
        second = reg.register("mom", Path("/tmp/other_path"))

        assert first is second
        # Second call should NOT have touched source_path or the prefix map.
        assert second.source_path == Path("/tmp/a")

    def test_register_manual_tag_collision_raises(self) -> None:
        reg = StrategyRegistry()
        reg.register("alpha", Path("/a"), manual_tag="xx")
        with pytest.raises(ValueError, match="already used by 'alpha'"):
            reg.register("beta", Path("/b"), manual_tag="xx")

    def test_register_auto_tag_collision_raises_with_rename_hint(self) -> None:
        """Two files that derive the same tag raise a rename-hint error."""
        reg = StrategyRegistry()
        reg.register("momentum_btc", Path("/a"))  # -> "mb"
        with pytest.raises(ValueError, match="Rename one of the strategy files"):
            reg.register("market_bot", Path("/b"))  # also -> "mb"

    def test_register_preserves_first_owner_on_collision(self) -> None:
        reg = StrategyRegistry()
        reg.register("momentum_btc", Path("/a"))
        with pytest.raises(ValueError):
            reg.register("market_bot", Path("/b"))

        # The second strategy must NOT have been added.
        assert reg.get("market_bot") is None
        assert reg._used_prefixes == {"mb": "momentum_btc"}


# ---------------------------------------------------------------------------
# allocate_tags()
# ---------------------------------------------------------------------------


class TestAllocateTags:
    """Global sequential offset allocation with collision and overflow guards."""

    def test_unregistered_strategy_raises(self) -> None:
        reg = StrategyRegistry()
        with pytest.raises(ValueError, match="not registered"):
            reg.allocate_tags("ghost", count=1, existing_tags=set())

    def test_single_allocation_formats_prefix_plus_000(self) -> None:
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        tags = reg.allocate_tags("mom", count=1, existing_tags=set())
        assert tags == ["m000"]
        assert reg._next_tag_offset == 1

    def test_multiple_sequential_tags_share_prefix(self) -> None:
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        tags = reg.allocate_tags("mom", count=3, existing_tags=set())
        assert tags == ["m000", "m001", "m002"]
        assert reg._next_tag_offset == 3

    def test_global_offset_advances_across_strategies(self) -> None:
        """Offsets are global: strategy B's first tag is still advanced."""
        reg = StrategyRegistry()
        reg.register("alpha", Path("/a"))
        reg.register("beta", Path("/b"))

        tags_a = reg.allocate_tags("alpha", count=2, existing_tags=set())
        tags_b = reg.allocate_tags("beta", count=1, existing_tags=set())

        assert tags_a == ["a000", "a001"]
        assert tags_b == ["b002"]  # offset advanced past alpha's 2 allocations
        assert reg._next_tag_offset == 3

    def test_count_zero_is_noop_and_preserves_offset(self) -> None:
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        tags = reg.allocate_tags("mom", count=0, existing_tags=set())
        assert tags == []
        assert reg._next_tag_offset == 0

    def test_collision_with_existing_strategy_id_raises(self) -> None:
        """Existing StrategyId 'Cls-m000' collides with the tag 'm000'."""
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        with pytest.raises(ValueError, match="collision"):
            reg.allocate_tags(
                "mom", count=1, existing_tags={"SomeStrategy-m000"},
            )

    def test_collision_does_not_mutate_offset(self) -> None:
        """On collision the registry must remain in its pre-call state."""
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        with pytest.raises(ValueError):
            reg.allocate_tags(
                "mom", count=1, existing_tags={"SomeStrategy-m000"},
            )
        assert reg._next_tag_offset == 0

    def test_overflow_beyond_999_raises(self) -> None:
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        reg._next_tag_offset = 999  # simulate near-overflow

        tags = reg.allocate_tags("mom", count=1, existing_tags=set())
        assert tags == ["m999"]

        with pytest.raises(ValueError, match="overflow"):
            reg.allocate_tags("mom", count=1, existing_tags=set())

    def test_collision_only_matches_exact_suffix(self) -> None:
        """`endswith('-m000')` must not match 'm000' inside other tags."""
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        # These must NOT collide — they only contain 'm000' as a substring,
        # not as a full '-m000' suffix.
        reg.allocate_tags(
            "mom",
            count=1,
            existing_tags={"Cls-m0001", "Other-zzz"},
        )


# ---------------------------------------------------------------------------
# State machine transitions
# ---------------------------------------------------------------------------


class TestStateTransitions:
    """mark_* transitions drive the state machine documented on the class."""

    def test_mark_starting_sets_state(self) -> None:
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        reg.mark_starting("mom")
        assert reg.get("mom").state == "starting"

    def test_mark_running_sets_state_and_strategy_ids(self) -> None:
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        reg.mark_running("mom", ["S-000", "S-001"])

        entry = reg.get("mom")
        assert entry.state == "running"
        assert entry.strategy_ids == ["S-000", "S-001"]
        # Bundle reverse-index populated for both IDs.
        assert reg.get_bundle_for_strategy("S-000") == "mom"
        assert reg.get_bundle_for_strategy("S-001") == "mom"

    def test_mark_paused_sets_state(self) -> None:
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        reg.mark_paused("mom")
        assert reg.get("mom").state == "paused"

    def test_mark_flattening_sets_state(self) -> None:
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        reg.mark_flattening("mom")
        assert reg.get("mom").state == "flattening"

    def test_mark_stopped_resets_entry_to_available(self) -> None:
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        reg.mark_running("mom", ["S-000"])
        # Mark was_running to prove mark_stopped clears it.
        reg.get("mom").was_running = True

        reg.mark_stopped("mom")

        entry = reg.get("mom")
        assert entry.state == "available"
        assert entry.strategy_ids == []
        assert entry.was_running is False
        # Bundle reverse-index must be cleared.
        assert reg.get_bundle_for_strategy("S-000") is None

    def test_mark_unknown_strategy_is_silent_noop(self) -> None:
        reg = StrategyRegistry()
        # All mark_* methods silently ignore unknown names — they're used by
        # event handlers where races with deletion are possible.
        reg.mark_starting("ghost")
        reg.mark_running("ghost", ["S-000"])
        reg.mark_paused("ghost")
        reg.mark_flattening("ghost")
        reg.mark_stopped("ghost")

        assert reg.get("ghost") is None
        assert reg.get_bundle_for_strategy("S-000") is None


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


class TestQueries:
    def test_get_unknown_returns_none(self) -> None:
        reg = StrategyRegistry()
        assert reg.get("ghost") is None

    def test_available_only_lists_available_state(self) -> None:
        reg = StrategyRegistry()
        reg.register("a", Path("/a"))
        reg.register("b", Path("/b"))
        reg.register("c", Path("/c"))
        reg.mark_running("b", ["B-000"])
        reg.mark_paused("c")

        assert reg.available() == ["a"]

    def test_get_bundle_for_strategy_returns_none_for_unknown(self) -> None:
        reg = StrategyRegistry()
        assert reg.get_bundle_for_strategy("Unknown-999") is None

    def test_get_all_states_shape(self) -> None:
        reg = StrategyRegistry()
        reg.register("mom", Path("/tmp/mom"))
        reg.mark_running("mom", ["M-000"])

        out = reg.get_all_states()
        assert out == {
            "mom": {
                "state": "running",
                "strategy_ids": ["M-000"],
                "source_path": "/tmp/mom",
                "order_id_tag_prefix": "m",
                "was_running": False,
            },
        }


# ---------------------------------------------------------------------------
# to_dict / restore_was_running
# ---------------------------------------------------------------------------


class TestSerialization:
    """Redis-persisted snapshot round-trip for auto-resume-after-restart."""

    def test_to_dict_empty_registry(self) -> None:
        reg = StrategyRegistry()
        snap = reg.to_dict()
        assert snap == {
            "strategies": {},
            "next_tag_offset": 0,
            "was_running": [],
        }

    def test_to_dict_was_running_includes_active_states(self) -> None:
        """running / paused / flattening all count as 'was_running' for restore."""
        reg = StrategyRegistry()
        reg.register("a", Path("/a"))
        reg.register("b", Path("/b"))
        reg.register("c", Path("/c"))
        reg.register("d", Path("/d"))

        reg.mark_running("a", ["A-000"])
        reg.mark_paused("b")
        reg.mark_flattening("c")
        # d is left available — must NOT appear in was_running

        snap = reg.to_dict()
        assert set(snap["was_running"]) == {"a", "b", "c"}
        assert "d" not in snap["was_running"]

    def test_to_dict_includes_next_tag_offset(self) -> None:
        reg = StrategyRegistry()
        reg.register("a", Path("/a"))
        reg.allocate_tags("a", count=3, existing_tags=set())
        assert reg.to_dict()["next_tag_offset"] == 3

    def test_restore_was_running_flags_existing_entries(self) -> None:
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        assert reg.get("mom").was_running is False

        reg.restore_was_running({"was_running": ["mom"]})
        assert reg.get("mom").was_running is True

    def test_restore_was_running_silently_ignores_unknown(self) -> None:
        """If the file was deleted between restart and restore, skip silently."""
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        reg.restore_was_running({"was_running": ["mom", "deleted_one"]})
        assert reg.get("mom").was_running is True

    def test_restore_was_running_missing_key_defaults_to_empty(self) -> None:
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        reg.restore_was_running({})  # no "was_running" key
        assert reg.get("mom").was_running is False

    def test_restore_was_running_does_not_mutate_state(self) -> None:
        """restore only flips was_running; state machine must be untouched."""
        reg = StrategyRegistry()
        reg.register("mom", Path("/a"))
        assert reg.get("mom").state == "available"

        reg.restore_was_running({"was_running": ["mom"]})

        # State must remain 'available'. HealthActor re-starts the strategy
        # by invoking start_strategy(), not by flipping the state here.
        assert reg.get("mom").state == "available"
        assert reg.get("mom").was_running is True


# ---------------------------------------------------------------------------
# scan() — filesystem discovery + delete protection
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_scan(monkeypatch):
    """Monkeypatch scan_valid_strategy_files so tests can control discovery.

    Returns a ``setter`` callable that installs a new {name: Path} mapping.
    ``StrategyRegistry.scan`` imports the helper lazily, so we patch the
    underlying module attribute.
    """
    import tinohelm.strategy.module_loader as ml

    state = {"files": {}}

    def fake(_dir):
        return dict(state["files"])

    monkeypatch.setattr(ml, "scan_valid_strategy_files", fake)

    def set_files(mapping):
        state["files"] = mapping

    return set_files


class TestScan:
    """scan() incremental discovery with delete-protection for active strategies."""

    def test_scan_missing_directory_returns_empty(self, tmp_path: Path) -> None:
        reg = StrategyRegistry()
        missing = tmp_path / "does_not_exist"
        assert reg.scan(missing) == []
        assert reg.get_all_states() == {}

    def test_scan_empty_directory_returns_empty(
        self, tmp_path: Path, patched_scan
    ) -> None:
        reg = StrategyRegistry()
        patched_scan({})
        tmp_path.mkdir(exist_ok=True)
        assert reg.scan(tmp_path) == []

    def test_scan_adds_new_strategies(
        self, tmp_path: Path, patched_scan
    ) -> None:
        reg = StrategyRegistry()
        patched_scan({
            "alpha": tmp_path / "alpha.py",
            "beta": tmp_path / "beta.py",
        })

        changes = reg.scan(tmp_path)

        assert set(changes) == {"added:alpha", "added:beta"}
        assert reg.get("alpha") is not None
        assert reg.get("beta") is not None
        # source_path is the file's parent dir, not the file itself.
        assert reg.get("alpha").source_path == tmp_path

    def test_scan_removes_deleted_available_strategy(
        self, tmp_path: Path, patched_scan
    ) -> None:
        reg = StrategyRegistry()
        patched_scan({"alpha": tmp_path / "alpha.py"})
        reg.scan(tmp_path)

        # Now the file disappears.
        patched_scan({})
        changes = reg.scan(tmp_path)

        assert changes == ["removed:alpha"]
        assert reg.get("alpha") is None
        # Prefix must be freed for future re-registration.
        assert "a" not in reg._used_prefixes

    def test_scan_preserves_running_strategy_marked_deleted(
        self, tmp_path: Path, patched_scan
    ) -> None:
        """A strategy that disappeared from disk but is still running stays
        in the registry and surfaces as ``deleted_but_running:<name>``."""
        reg = StrategyRegistry()
        patched_scan({"alpha": tmp_path / "alpha.py"})
        reg.scan(tmp_path)
        reg.mark_running("alpha", ["Alpha-000"])

        patched_scan({})
        changes = reg.scan(tmp_path)

        assert changes == ["deleted_but_running:alpha"]
        # The registry entry must remain so the running instance is still
        # reachable via get().
        assert reg.get("alpha") is not None
        assert reg.get("alpha").state == "running"
        # Prefix must NOT be freed — the running strategy still owns it.
        assert reg._used_prefixes == {"a": "alpha"}

    @pytest.mark.parametrize("active_state", ["paused", "flattening"])
    def test_scan_preserves_active_states(
        self, tmp_path: Path, patched_scan, active_state: str
    ) -> None:
        reg = StrategyRegistry()
        patched_scan({"alpha": tmp_path / "alpha.py"})
        reg.scan(tmp_path)

        if active_state == "paused":
            reg.mark_paused("alpha")
        else:
            reg.mark_flattening("alpha")

        patched_scan({})
        changes = reg.scan(tmp_path)

        assert changes == ["deleted_but_running:alpha"]
        assert reg.get("alpha").state == active_state

    def test_scan_removes_starting_state_strategy(
        self, tmp_path: Path, patched_scan
    ) -> None:
        """Starting is treated as 'safe to prune' — the start flow has not
        yet placed any NT strategies on the trader."""
        reg = StrategyRegistry()
        patched_scan({"alpha": tmp_path / "alpha.py"})
        reg.scan(tmp_path)
        reg.mark_starting("alpha")

        patched_scan({})
        changes = reg.scan(tmp_path)

        assert changes == ["removed:alpha"]
        assert reg.get("alpha") is None

    def test_scan_second_call_with_no_changes_is_empty(
        self, tmp_path: Path, patched_scan
    ) -> None:
        reg = StrategyRegistry()
        patched_scan({"alpha": tmp_path / "alpha.py"})
        reg.scan(tmp_path)

        # Same file set on second scan — no changes.
        assert reg.scan(tmp_path) == []

    def test_scan_add_then_remove_then_readd_reuses_prefix(
        self, tmp_path: Path, patched_scan
    ) -> None:
        """After a clean removal, the prefix is free and re-registration
        reuses it."""
        reg = StrategyRegistry()
        patched_scan({"alpha": tmp_path / "alpha.py"})
        reg.scan(tmp_path)
        assert reg._used_prefixes == {"a": "alpha"}

        patched_scan({})
        reg.scan(tmp_path)
        assert reg._used_prefixes == {}

        patched_scan({"alpha": tmp_path / "alpha.py"})
        reg.scan(tmp_path)
        assert reg._used_prefixes == {"a": "alpha"}

    def test_scan_mixed_add_and_remove_in_single_pass(
        self, tmp_path: Path, patched_scan
    ) -> None:
        reg = StrategyRegistry()
        patched_scan({"alpha": tmp_path / "alpha.py"})
        reg.scan(tmp_path)

        # Second pass: alpha gone, beta added.
        patched_scan({"beta": tmp_path / "beta.py"})
        changes = reg.scan(tmp_path)

        assert set(changes) == {"removed:alpha", "added:beta"}
        assert reg.get("alpha") is None
        assert reg.get("beta") is not None
