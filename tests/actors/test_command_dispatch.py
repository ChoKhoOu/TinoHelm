"""Tests for :mod:`tinohelm.node.actors.command_dispatch`.

The dispatcher is the entry point for every external lifecycle command that
reaches a live or sandbox :class:`TradingNode` — pause, resume, flatten,
halt, shutdown, start/stop strategy, cancel order, and the internal
``_rescan_strategies`` command posted by ``HealthActor`` when the strategies
directory changes on disk.  Before the extraction this logic lived inside the
Cython-subclassed :class:`CommandActor`, which made it untestable with mocks.

Now the dispatcher is pure Python and these tests exhaustively cover:

* Each of the 13 ``cmd`` actions → the right ``LifecycleController`` method.
* The ``pause``/``resume`` overload — implicit ``_all`` fan-out when there is
  no ``strategy_id``, per-strategy variant when there is.
* Error paths: ``lifecycle=None`` → ack with ``no_lifecycle`` reason; any
  lifecycle exception → ack with ``str(exc)`` reason and no re-raise.
* The ``_rescan_strategies`` branch — registry-less no-op, non-existent
  directory, changed vs unchanged scan output.
* Unknown commands log a warning but do not ack or raise.
* ``cancel_order`` with a missing ``client_order_id`` silently no-ops.

The tests are NT-free; every dependency (``lifecycle``, ``registry``, the
directory resolver, the publish_ack callback, the logger) is a MagicMock.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tinohelm.node.actors.command_dispatch import (
    dispatch_command,
    handle_rescan_strategies,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def lifecycle() -> MagicMock:
    """A bare LifecycleController stand-in; every method is a MagicMock."""
    return MagicMock(name="LifecycleController")


@pytest.fixture
def registry() -> MagicMock:
    """A StrategyRegistry stand-in — supplies .scan / .get_all_states."""
    r = MagicMock(name="StrategyRegistry")
    r.scan.return_value = []  # No changes by default
    r.get_all_states.return_value = {"strat_a": "available"}
    return r


@pytest.fixture
def publish_ack() -> MagicMock:
    return MagicMock(name="publish_ack")


@pytest.fixture
def log() -> MagicMock:
    return MagicMock(name="log")


@pytest.fixture
def existing_dir(tmp_path: Path) -> Path:
    """A real, empty directory — resolve_strategies_dir returns this."""
    d = tmp_path / "strategies"
    d.mkdir()
    return d


@pytest.fixture
def resolver(existing_dir: Path):
    return lambda: existing_dir


def _call(cmd: dict, *, lifecycle=None, registry=None,
          resolver=None, publish_ack=None, log=None) -> None:
    """Shim to reduce boilerplate in each test."""
    dispatch_command(
        cmd,
        lifecycle=lifecycle,
        registry=registry,
        resolve_strategies_dir=resolver if resolver else lambda: Path("/tmp/ignored"),
        publish_ack=publish_ack if publish_ack else MagicMock(),
        log=log if log else MagicMock(),
    )


# ---------------------------------------------------------------------------
# pause / resume — all vs per-strategy overload
# ---------------------------------------------------------------------------

class TestPauseResumeOverload:
    def test_pause_without_strategy_id_calls_pause_all(self, lifecycle):
        _call({"cmd": "pause"}, lifecycle=lifecycle)
        lifecycle.pause_all.assert_called_once_with()
        lifecycle.pause_strategy_id.assert_not_called()

    def test_pause_with_empty_string_strategy_id_treated_as_pause_all(self, lifecycle):
        """Empty string is falsy; original inline code used ``not strategy_id``."""
        _call({"cmd": "pause", "strategy_id": ""}, lifecycle=lifecycle)
        lifecycle.pause_all.assert_called_once_with()

    def test_pause_with_none_strategy_id_treated_as_pause_all(self, lifecycle):
        _call({"cmd": "pause", "strategy_id": None}, lifecycle=lifecycle)
        lifecycle.pause_all.assert_called_once_with()

    def test_pause_with_strategy_id_calls_pause_strategy_id(self, lifecycle):
        _call({"cmd": "pause", "strategy_id": "S-1"}, lifecycle=lifecycle)
        lifecycle.pause_strategy_id.assert_called_once_with("S-1")
        lifecycle.pause_all.assert_not_called()

    def test_resume_without_strategy_id_calls_resume_all(self, lifecycle):
        _call({"cmd": "resume"}, lifecycle=lifecycle)
        lifecycle.resume_all.assert_called_once_with()

    def test_resume_with_strategy_id_calls_resume_strategy_id(self, lifecycle):
        _call({"cmd": "resume", "strategy_id": "S-42"}, lifecycle=lifecycle)
        lifecycle.resume_strategy_id.assert_called_once_with("S-42")


# ---------------------------------------------------------------------------
# flatten / halt / unhalt / shutdown — system-wide commands
# ---------------------------------------------------------------------------

class TestSystemWideCommands:
    def test_flatten_with_strategy_id_forwards_arg(self, lifecycle):
        _call({"cmd": "flatten", "strategy_id": "S-1"}, lifecycle=lifecycle)
        lifecycle.flatten.assert_called_once_with("S-1")

    def test_flatten_without_strategy_id_passes_none(self, lifecycle):
        """``lifecycle.flatten`` accepts ``None`` as "flatten everything"."""
        _call({"cmd": "flatten"}, lifecycle=lifecycle)
        lifecycle.flatten.assert_called_once_with(None)

    def test_halt(self, lifecycle):
        _call({"cmd": "halt"}, lifecycle=lifecycle)
        lifecycle.halt.assert_called_once_with()

    def test_unhalt(self, lifecycle):
        _call({"cmd": "unhalt"}, lifecycle=lifecycle)
        lifecycle.unhalt.assert_called_once_with()

    def test_shutdown(self, lifecycle):
        _call({"cmd": "shutdown"}, lifecycle=lifecycle)
        lifecycle.shutdown.assert_called_once_with()


# ---------------------------------------------------------------------------
# Named-strategy commands
# ---------------------------------------------------------------------------

class TestStrategyNameCommands:
    def test_start_strategy_with_name(self, lifecycle):
        _call({"cmd": "start_strategy", "strategy_name": "momentum_v1"}, lifecycle=lifecycle)
        lifecycle.start_strategy.assert_called_once_with("momentum_v1")

    def test_start_strategy_without_name_passes_empty_string(self, lifecycle):
        """Match legacy behaviour: ``cmd.get("strategy_name", "")``."""
        _call({"cmd": "start_strategy"}, lifecycle=lifecycle)
        lifecycle.start_strategy.assert_called_once_with("")

    def test_flatten_stop_strategy(self, lifecycle):
        _call({"cmd": "flatten_stop_strategy", "strategy_name": "btc"}, lifecycle=lifecycle)
        lifecycle.flatten_stop_strategy.assert_called_once_with("btc")

    def test_pause_strategy_by_name(self, lifecycle):
        _call({"cmd": "pause_strategy", "strategy_name": "btc"}, lifecycle=lifecycle)
        lifecycle.pause_strategy.assert_called_once_with("btc")

    def test_resume_strategy_by_name(self, lifecycle):
        _call({"cmd": "resume_strategy", "strategy_name": "btc"}, lifecycle=lifecycle)
        lifecycle.resume_strategy.assert_called_once_with("btc")


# ---------------------------------------------------------------------------
# cancel_order — guarded by presence of client_order_id
# ---------------------------------------------------------------------------

class TestCancelOrder:
    def test_with_client_order_id(self, lifecycle):
        _call({"cmd": "cancel_order", "client_order_id": "O-1"}, lifecycle=lifecycle)
        lifecycle.cancel_order.assert_called_once_with("O-1")

    def test_without_client_order_id_is_silent_no_op(self, lifecycle):
        """Legacy guard: ``if coid: lifecycle.cancel_order(coid)``."""
        _call({"cmd": "cancel_order"}, lifecycle=lifecycle)
        lifecycle.cancel_order.assert_not_called()

    def test_with_empty_string_client_order_id_is_no_op(self, lifecycle):
        """Empty string is falsy → skip, matching the legacy guard."""
        _call({"cmd": "cancel_order", "client_order_id": ""}, lifecycle=lifecycle)
        lifecycle.cancel_order.assert_not_called()

    def test_with_none_client_order_id_is_no_op(self, lifecycle):
        _call({"cmd": "cancel_order", "client_order_id": None}, lifecycle=lifecycle)
        lifecycle.cancel_order.assert_not_called()


# ---------------------------------------------------------------------------
# Unknown command — warn but do not ack
# ---------------------------------------------------------------------------

class TestUnknownCommand:
    def test_unknown_command_logs_warning(self, lifecycle, log):
        _call({"cmd": "grass_grows"}, lifecycle=lifecycle, log=log)
        log.warning.assert_called_once()
        # Message mentions the action name
        assert "grass_grows" in log.warning.call_args[0][0]

    def test_unknown_command_does_not_publish_ack(self, lifecycle, publish_ack):
        _call({"cmd": "grass_grows"}, lifecycle=lifecycle, publish_ack=publish_ack)
        publish_ack.assert_not_called()

    def test_unknown_command_does_not_call_any_lifecycle_method(self, lifecycle):
        _call({"cmd": "frobnicate"}, lifecycle=lifecycle)
        # Nothing on the lifecycle should have been invoked.
        assert lifecycle.method_calls == []

    def test_missing_cmd_key_is_treated_as_unknown(self, lifecycle, log):
        # ``cmd.get("cmd")`` returns None; flat if/elif reach the else branch.
        _call({}, lifecycle=lifecycle, log=log)
        log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# No lifecycle — ack with "no_lifecycle" reason
# ---------------------------------------------------------------------------

class TestNoLifecycle:
    def test_pause_without_lifecycle_publishes_error_ack(self, publish_ack, log):
        _call({"cmd": "pause"}, lifecycle=None, publish_ack=publish_ack, log=log)
        publish_ack.assert_called_once()
        suffix, payload = publish_ack.call_args[0]
        assert suffix == "commands_ack"
        assert payload == {"cmd": "pause", "status": "error", "reason": "no_lifecycle"}

    def test_flatten_without_lifecycle_publishes_error_ack(self, publish_ack):
        _call({"cmd": "flatten"}, lifecycle=None, publish_ack=publish_ack)
        assert publish_ack.call_args[0][1]["cmd"] == "flatten"
        assert publish_ack.call_args[0][1]["reason"] == "no_lifecycle"

    def test_no_lifecycle_logs_warning_before_ack(self, publish_ack, log):
        _call({"cmd": "halt"}, lifecycle=None, publish_ack=publish_ack, log=log)
        log.warning.assert_called_once()
        assert "halt" in log.warning.call_args[0][0]

    def test_unknown_cmd_with_no_lifecycle_still_logs_warn(self, publish_ack, log):
        """Even unknown commands hit the no_lifecycle branch first (it guards
        all non-_rescan actions)."""
        _call({"cmd": "banana"}, lifecycle=None, publish_ack=publish_ack, log=log)
        # One warning for "no lifecycle", and the ack is published.
        log.warning.assert_called_once()
        assert publish_ack.called

    def test_rescan_strategies_does_not_need_lifecycle(
        self, registry, existing_dir, publish_ack,
    ):
        """The ``_rescan_strategies`` branch runs before the lifecycle check."""
        _call(
            {"cmd": "_rescan_strategies"},
            lifecycle=None, registry=registry,
            resolver=lambda: existing_dir, publish_ack=publish_ack,
        )
        registry.scan.assert_called_once_with(existing_dir)


# ---------------------------------------------------------------------------
# Exception from lifecycle — caught, logged, acked, not re-raised
# ---------------------------------------------------------------------------

class TestLifecycleException:
    def test_exception_is_caught_not_reraised(self, lifecycle, publish_ack):
        lifecycle.pause_all.side_effect = RuntimeError("kaboom")
        # Must not raise
        _call({"cmd": "pause"}, lifecycle=lifecycle, publish_ack=publish_ack)

    def test_exception_publishes_error_ack_with_str_reason(self, lifecycle, publish_ack):
        lifecycle.halt.side_effect = ValueError("halt failed: X")
        _call({"cmd": "halt"}, lifecycle=lifecycle, publish_ack=publish_ack)
        publish_ack.assert_called_once()
        suffix, payload = publish_ack.call_args[0]
        assert suffix == "commands_ack"
        assert payload == {
            "cmd": "halt", "status": "error", "reason": "halt failed: X",
        }

    def test_exception_is_logged_at_error(self, lifecycle, log):
        lifecycle.shutdown.side_effect = RuntimeError("x")
        _call({"cmd": "shutdown"}, lifecycle=lifecycle, log=log)
        log.error.assert_called_once()
        assert "shutdown" in log.error.call_args[0][0]

    def test_exception_in_cancel_order_acked(self, lifecycle, publish_ack):
        lifecycle.cancel_order.side_effect = RuntimeError("no such order")
        _call(
            {"cmd": "cancel_order", "client_order_id": "O-1"},
            lifecycle=lifecycle, publish_ack=publish_ack,
        )
        payload = publish_ack.call_args[0][1]
        assert payload["reason"] == "no such order"

    def test_exception_in_one_command_does_not_affect_next(
        self, lifecycle, publish_ack,
    ):
        """Each dispatch call is independent — since the dispatcher catches
        ``Exception`` and returns, the outer drain loop can call the next one.
        """
        lifecycle.pause_all.side_effect = RuntimeError("boom")
        _call({"cmd": "pause"}, lifecycle=lifecycle, publish_ack=publish_ack)
        # Second call still lands on the right method
        lifecycle.resume_all.side_effect = None
        _call({"cmd": "resume"}, lifecycle=lifecycle, publish_ack=publish_ack)
        lifecycle.resume_all.assert_called_once_with()


# ---------------------------------------------------------------------------
# _rescan_strategies — internal file-watcher originated command
# ---------------------------------------------------------------------------

class TestRescanStrategies:
    def test_runs_scan_on_existing_dir(self, registry, existing_dir, publish_ack):
        registry.scan.return_value = ["new_strat"]
        _call(
            {"cmd": "_rescan_strategies"},
            registry=registry, resolver=lambda: existing_dir, publish_ack=publish_ack,
        )
        registry.scan.assert_called_once_with(existing_dir)

    def test_publishes_strategy_update_when_scan_returns_changes(
        self, registry, existing_dir, publish_ack,
    ):
        registry.scan.return_value = ["new_strat"]
        registry.get_all_states.return_value = {"new_strat": "available"}
        _call(
            {"cmd": "_rescan_strategies"},
            registry=registry, resolver=lambda: existing_dir, publish_ack=publish_ack,
        )
        publish_ack.assert_called_once()
        suffix, payload = publish_ack.call_args[0]
        assert suffix == "strategy_update"
        assert payload == {"strategies": {"new_strat": "available"}}

    def test_skips_publish_when_scan_returns_empty(
        self, registry, existing_dir, publish_ack,
    ):
        """``registry.scan`` returning an empty (falsy) list → no ack."""
        registry.scan.return_value = []
        _call(
            {"cmd": "_rescan_strategies"},
            registry=registry, resolver=lambda: existing_dir, publish_ack=publish_ack,
        )
        publish_ack.assert_not_called()

    def test_no_registry_is_no_op(self, existing_dir, publish_ack, log):
        """When ``registry`` is None, the branch returns without side effects."""
        _call(
            {"cmd": "_rescan_strategies"},
            registry=None, resolver=lambda: existing_dir, publish_ack=publish_ack, log=log,
        )
        publish_ack.assert_not_called()
        log.info.assert_not_called()
        log.error.assert_not_called()

    def test_missing_directory_is_no_op(self, registry, tmp_path, publish_ack):
        missing = tmp_path / "does-not-exist"
        _call(
            {"cmd": "_rescan_strategies"},
            registry=registry, resolver=lambda: missing, publish_ack=publish_ack,
        )
        registry.scan.assert_not_called()
        publish_ack.assert_not_called()

    def test_scan_exception_is_logged_and_swallowed(
        self, registry, existing_dir, log, publish_ack,
    ):
        """A broken ``registry.scan`` must not crash the command dispatcher."""
        registry.scan.side_effect = RuntimeError("walk blew up")
        _call(
            {"cmd": "_rescan_strategies"},
            registry=registry, resolver=lambda: existing_dir, log=log, publish_ack=publish_ack,
        )
        log.error.assert_called_once()
        assert "walk blew up" in log.error.call_args[0][0]
        # No ack on failure — matches legacy path
        publish_ack.assert_not_called()

    def test_resolve_exception_is_logged_and_swallowed(
        self, registry, log, publish_ack,
    ):
        def _bad_resolver() -> Path:
            raise RuntimeError("bad resolver")

        _call(
            {"cmd": "_rescan_strategies"},
            registry=registry, resolver=_bad_resolver, log=log, publish_ack=publish_ack,
        )
        log.error.assert_called_once()
        publish_ack.assert_not_called()

    def test_handle_rescan_strategies_is_directly_callable(
        self, registry, existing_dir, publish_ack, log,
    ):
        """The helper is exported so tests and future callers don't need the
        full ``dispatch_command`` wrapper to rescan.
        """
        registry.scan.return_value = ["s1"]
        registry.get_all_states.return_value = {"s1": "available"}
        handle_rescan_strategies(
            registry=registry,
            resolve_strategies_dir=lambda: existing_dir,
            publish_ack=publish_ack, log=log,
        )
        publish_ack.assert_called_once_with(
            "strategy_update", {"strategies": {"s1": "available"}},
        )


# ---------------------------------------------------------------------------
# Public surface — module import is NT-free; __all__ matches export contract
# ---------------------------------------------------------------------------

class TestPublicSurface:
    def test_all_lists_the_two_public_names(self):
        import tinohelm.node.actors.command_dispatch as cd
        assert set(cd.__all__) == {"dispatch_command", "handle_rescan_strategies"}

    def test_module_has_no_nt_dependency(self):
        """Importing ``command_dispatch`` must not pull in NautilusTrader.

        Uses a ``sys.meta_path`` blocker so that any transitive NT import is
        turned into an :class:`ImportError`.  The invariant checked is "no NT
        modules newly loaded *as a side effect* of importing this helper" —
        absolute "NT absent from sys.modules" is impossible under CI because
        sibling tests already loaded NT.
        """
        import importlib
        import sys

        class _Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.startswith("nautilus_trader"):
                    raise ImportError(f"blocked: {name}")
                return None

        saved = sys.modules.pop("tinohelm.node.actors.command_dispatch", None)
        nt_before = {k for k in sys.modules if k.startswith("nautilus_trader")}

        blocker = _Blocker()
        sys.meta_path.insert(0, blocker)
        try:
            mod = importlib.import_module("tinohelm.node.actors.command_dispatch")
            assert hasattr(mod, "dispatch_command")
            assert hasattr(mod, "handle_rescan_strategies")
            nt_after = {k for k in sys.modules if k.startswith("nautilus_trader")}
            assert nt_after - nt_before == set(), (
                f"command_dispatch import pulled in NT modules: "
                f"{sorted(nt_after - nt_before)}"
            )
        finally:
            sys.meta_path.remove(blocker)
            if saved is not None:
                sys.modules["tinohelm.node.actors.command_dispatch"] = saved
