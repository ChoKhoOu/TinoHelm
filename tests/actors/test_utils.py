"""Tests for :mod:`tinohelm.node.actors._utils` — shared actor helpers.

``ts_ns_to_iso`` and ``redis_publish`` are used by every one of the five node
actors. Until this suite they had no direct coverage: a regression in the
nanosecond → ISO-8601 conversion (for example, losing the UTC timezone suffix
or regressing on the epoch boundary) would only surface in the end-to-end
trading pipeline, where it looks like a "bad timestamp" bug far from the
source. Likewise ``redis_publish`` is the one and only wrapper that turns an
actor payload into a Redis ``PUBLISH`` — if it swallowed the channel prefix or
stopped catching exceptions, the whole live/sandbox pipeline would fall over.

These tests run NT-free; ``_utils`` intentionally has zero NT dependencies.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import pytest

from tinohelm.node.actors._utils import redis_publish, ts_ns_to_iso


# ---------------------------------------------------------------------------
# ts_ns_to_iso — nanosecond → ISO-8601 UTC conversion
# ---------------------------------------------------------------------------

class TestTsNsToIso:
    def test_epoch_zero(self):
        # 0 ns = 1970-01-01T00:00:00+00:00
        assert ts_ns_to_iso(0) == "1970-01-01T00:00:00+00:00"

    def test_known_unix_second_boundary(self):
        # 1700000000 s = 2023-11-14T22:13:20+00:00 (well-known epoch value).
        assert ts_ns_to_iso(1_700_000_000 * 1_000_000_000) == (
            "2023-11-14T22:13:20+00:00"
        )

    def test_sub_second_precision_preserved(self):
        # 500ms after epoch → trailing .5 seconds with UTC suffix.
        assert ts_ns_to_iso(500_000_000).startswith("1970-01-01T00:00:00.5")
        assert ts_ns_to_iso(500_000_000).endswith("+00:00")

    def test_microsecond_precision(self):
        # 123456000 ns → 0.123456 seconds, ISO preserves 6 decimal places.
        out = ts_ns_to_iso(123_456_000)
        assert out.startswith("1970-01-01T00:00:00.123456")

    def test_always_utc_suffix(self):
        # A machine in non-UTC local time must still return +00:00 because the
        # function constructs the datetime with ``tz=timezone.utc``.
        for ns in (0, 1_000_000_000, 1_700_000_000_000_000_000):
            assert ts_ns_to_iso(ns).endswith("+00:00")

    def test_is_string_type(self):
        assert isinstance(ts_ns_to_iso(0), str)

    def test_round_trips_via_fromisoformat(self):
        # If we roundtrip the ISO string back to datetime we should recover the
        # same instant (within microsecond truncation).
        from datetime import datetime

        ns = 1_700_123_456_789_000_000  # 1700123456.789 s
        iso = ts_ns_to_iso(ns)
        parsed = datetime.fromisoformat(iso)
        # Equivalent to the same Unix timestamp up to microseconds.
        assert abs(parsed.timestamp() - ns / 1e9) < 1e-6


# ---------------------------------------------------------------------------
# redis_publish — node_type-aware PUBLISH wrapper
# ---------------------------------------------------------------------------

class TestRedisPublishChannelFormatting:
    def test_uses_tino_sandbox_prefix(self):
        r = MagicMock()
        redis_publish(r, "sandbox", "fills", {"a": 1})
        assert r.publish.call_args[0][0] == "tino:sandbox:fills"

    def test_uses_tino_live_prefix(self):
        r = MagicMock()
        redis_publish(r, "live", "positions", {"a": 1})
        assert r.publish.call_args[0][0] == "tino:live:positions"

    def test_arbitrary_node_type_is_preserved_verbatim(self):
        """``node_type`` is not sanitised — callers control the channel."""
        r = MagicMock()
        redis_publish(r, "custom-env", "events", {})
        assert r.publish.call_args[0][0] == "tino:custom-env:events"

    def test_channel_suffix_supports_colons(self):
        r = MagicMock()
        redis_publish(r, "sandbox", "progress:abc-123", {})
        # Additional colons inside the suffix pass through untouched.
        assert r.publish.call_args[0][0] == "tino:sandbox:progress:abc-123"


class TestRedisPublishPayloadEncoding:
    def test_encodes_dict_as_json(self):
        r = MagicMock()
        redis_publish(r, "sandbox", "fills", {"trade_id": "T-1", "qty": 0.5})
        payload = json.loads(r.publish.call_args[0][1])
        assert payload == {"trade_id": "T-1", "qty": 0.5}

    def test_uses_default_str_for_non_json_types(self):
        """``default=str`` lets NT's Price/Quantity/datetime pass through.

        We simulate this with a custom object whose ``__str__`` returns a
        predictable token.
        """
        class _Exotic:
            def __str__(self) -> str:
                return "exotic-stringified"

        r = MagicMock()
        redis_publish(r, "sandbox", "event", {"value": _Exotic()})
        payload = json.loads(r.publish.call_args[0][1])
        assert payload == {"value": "exotic-stringified"}

    def test_empty_dict_encodes_to_empty_json_object(self):
        r = MagicMock()
        redis_publish(r, "sandbox", "event", {})
        assert r.publish.call_args[0][1] == "{}"

    def test_nested_dict_roundtrip(self):
        r = MagicMock()
        nested = {"outer": {"inner": [1, 2, 3], "flag": True}}
        redis_publish(r, "sandbox", "event", nested)
        assert json.loads(r.publish.call_args[0][1]) == nested


class TestRedisPublishErrorHandling:
    def test_none_redis_client_is_silently_skipped(self):
        """A None client must not raise — several actors call ``_publish``
        before their Redis connection is wired up (e.g. during Actor __init__
        or after ``on_stop``)."""
        # This would raise AttributeError if we tried to .publish() on None.
        redis_publish(None, "sandbox", "fills", {"a": 1})

    def test_exception_from_publish_is_swallowed(self, caplog):
        """Redis downtime must NEVER take down the NT event loop.

        The helper catches any Exception from ``.publish()`` and logs it at
        ERROR level so operators can see it, then returns normally.
        """
        r = MagicMock()
        r.publish.side_effect = ConnectionError("redis down")
        with caplog.at_level(logging.ERROR, logger="tinohelm.node.actors._utils"):
            redis_publish(r, "sandbox", "fills", {"a": 1})
        assert r.publish.called
        # Check a log record captured the error
        assert any(
            "Redis publish error" in rec.getMessage()
            and "tino:sandbox:fills" in rec.getMessage()
            for rec in caplog.records
        )

    def test_logs_include_exception_message(self, caplog):
        r = MagicMock()
        r.publish.side_effect = RuntimeError("boom-token-xyz")
        with caplog.at_level(logging.ERROR, logger="tinohelm.node.actors._utils"):
            redis_publish(r, "live", "orders", {})
        assert any("boom-token-xyz" in rec.getMessage() for rec in caplog.records)

    def test_does_not_wrap_keyboard_interrupt(self):
        """``except Exception`` lets ``KeyboardInterrupt`` / ``SystemExit``
        propagate so operators can still ^C a stuck actor."""
        r = MagicMock()
        r.publish.side_effect = KeyboardInterrupt
        with pytest.raises(KeyboardInterrupt):
            redis_publish(r, "sandbox", "fills", {})


# ---------------------------------------------------------------------------
# Package-level contract — the ``actors`` __init__ is PEP 562 lazy
# ---------------------------------------------------------------------------

class TestActorsPackageLazyExports:
    """``tinohelm.node.actors`` eagerly loaded NT-dependent Actors until this
    refactor. The conversion to ``__getattr__``-based lazy re-export must:

    * Keep every legacy symbol accessible (regressions in consumer code like
      ``tinohelm.node._common`` would surface as :class:`AttributeError`).
    * Trigger the NT import **only** on the first attribute access — so
      importing the package alone never touches NT, letting NT-free helpers
      be tested in environments without the NT wheel.
    * Raise :class:`AttributeError` for truly unknown names (so ``hasattr``
      still works and ``from pkg import foo`` fails fast on typos).
    """

    def test_all_legacy_names_still_accessible(self):
        from tinohelm.node.actors import (  # noqa: F401
            CommandActor,
            CommandActorConfig,
            DbWriterActor,
            DbWriterActorConfig,
            HealthActor,
            HealthActorConfig,
            MetricsActor,
            MetricsActorConfig,
            SnapshotActor,
            SnapshotActorConfig,
        )
        # Sanity: every symbol is a class object.
        for obj in (
            CommandActor, CommandActorConfig,
            DbWriterActor, DbWriterActorConfig,
            HealthActor, HealthActorConfig,
            MetricsActor, MetricsActorConfig,
            SnapshotActor, SnapshotActorConfig,
        ):
            assert isinstance(obj, type)

    def test_unknown_attribute_raises_attribute_error(self):
        import tinohelm.node.actors as pkg

        with pytest.raises(AttributeError, match="no attribute 'Nonexistent'"):
            pkg.Nonexistent  # noqa: B018

    def test_dir_reports_lazy_names(self):
        import tinohelm.node.actors as pkg

        names = dir(pkg)
        for legacy in (
            "CommandActor", "SnapshotActor", "HealthActor",
            "DbWriterActor", "MetricsActor",
        ):
            assert legacy in names

    def test_package_import_does_not_pull_in_nautilus_trader(self):
        """Importing just the package — no attribute access — must not trigger
        any NT module load. Uses a ``sys.meta_path`` blocker to enforce this.
        """
        import importlib
        import sys

        class _Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.startswith("nautilus_trader"):
                    raise ImportError(f"blocked: {name}")
                return None

        # Remove the package from sys.modules so importlib re-runs __init__.
        saved = sys.modules.pop("tinohelm.node.actors", None)
        nt_before = {k for k in sys.modules if k.startswith("nautilus_trader")}

        blocker = _Blocker()
        sys.meta_path.insert(0, blocker)
        try:
            mod = importlib.import_module("tinohelm.node.actors")
            # Package import alone must be NT-free.
            nt_after = {k for k in sys.modules if k.startswith("nautilus_trader")}
            assert nt_after - nt_before == set(), (
                f"package import pulled in NT modules: "
                f"{sorted(nt_after - nt_before)}"
            )
            # __all__ is still populated so ``from pkg import *`` still works.
            assert "CommandActor" in mod.__all__
        finally:
            sys.meta_path.remove(blocker)
            if saved is not None:
                sys.modules["tinohelm.node.actors"] = saved

    def test_submodule_access_bypasses_getattr(self):
        """Importing a submodule directly (e.g. ``command_dispatch``) must
        succeed without triggering any of the lazy NT exports."""
        import importlib
        import sys

        class _Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.startswith("nautilus_trader"):
                    raise ImportError(f"blocked: {name}")
                return None

        for submodule in ("command_dispatch", "file_watch", "rate_limit", "_utils"):
            saved = sys.modules.pop(
                f"tinohelm.node.actors.{submodule}", None,
            )
            sys.modules.pop("tinohelm.node.actors", None)
            blocker = _Blocker()
            sys.meta_path.insert(0, blocker)
            try:
                importlib.import_module(f"tinohelm.node.actors.{submodule}")
            finally:
                sys.meta_path.remove(blocker)
                if saved is not None:
                    sys.modules[f"tinohelm.node.actors.{submodule}"] = saved
