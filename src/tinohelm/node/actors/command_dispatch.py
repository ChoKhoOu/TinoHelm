"""NT-free dispatch for CommandActor's Redis-originated lifecycle commands.

``CommandActor`` receives JSON command dicts via Redis ``SUBSCRIBE`` and has to
translate them into calls on a ``LifecycleController``. That translation is a
flat ``if/elif`` tree — pure Python once the dependencies (``lifecycle``,
``registry``, a way to find the strategies directory, and an ack publisher) are
parameterised. Keeping it inside the Cython-subclassed Actor made it untestable,
so the whole dispatch table lives here and ``CommandActor._drain_pending_commands``
shrinks to a forwarding loop.

All callables passed in are invoked synchronously on the NT event-loop thread
(the Actor's command-dispatch timer). No coroutine support is needed.

Contract guarantees kept in sync with the legacy inline code:

* Unknown ``cmd`` actions produce a ``log.warning`` and return without raising.
* A ``None`` ``lifecycle`` produces a warning + a ``commands_ack`` envelope with
  ``{"status": "error", "reason": "no_lifecycle"}`` — the same payload shape the
  original code emitted — then skips the action.
* Lifecycle method exceptions are caught and surfaced via ``commands_ack`` with
  ``{"status": "error", "reason": str(e)}`` plus a ``log.error``; the exception
  is **not** re-raised so one bad command cannot stop subsequent ones in the
  same drain cycle.
* ``pause`` / ``resume`` without a ``strategy_id`` dispatch to the ``_all``
  variants; with a ``strategy_id`` they dispatch per-strategy.
* ``cancel_order`` is a no-op when no ``client_order_id`` is supplied (the
  original code guarded the call with ``if coid:``).
* The internal ``_rescan_strategies`` command is only meaningful when a
  ``registry`` is attached; otherwise it is silently dropped — that matches the
  original behaviour where the ``if self._registry is not None:`` branch was
  the only path.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

__all__ = ["dispatch_command", "handle_rescan_strategies"]


PublishAck = Callable[[str, dict], None]
"""``(channel_suffix, data)`` — matches ``redis_publish`` with node_type pre-bound."""

ResolveStrategiesDir = Callable[[], Path]


def handle_rescan_strategies(
    *,
    registry: Any | None,
    resolve_strategies_dir: ResolveStrategiesDir,
    publish_ack: PublishAck,
    log: Any,
) -> None:
    """Handle the file-watcher-originated ``_rescan_strategies`` command.

    Walks the strategies directory, invokes ``registry.scan`` and — when the
    registry reports changes — publishes a ``strategy_update`` envelope with
    the new registry state.  Silently no-ops if the directory does not exist
    or the registry is absent; swallows any exception into a ``log.error`` to
    mirror the original inline behaviour, where a rescan failure could never
    take down the dispatch loop.
    """
    if registry is None:
        return
    try:
        strategies_dir = resolve_strategies_dir()
        if not strategies_dir.exists():
            return
        changed = registry.scan(strategies_dir)
        if changed:
            log.info(f"Strategy folder change detected: {changed}")
            publish_ack(
                "strategy_update",
                {"strategies": registry.get_all_states()},
            )
    except Exception as e:  # noqa: BLE001 — mirror legacy catch-all
        log.error(f"Rescan strategies error: {e}")


def dispatch_command(
    cmd: Mapping[str, Any],
    *,
    lifecycle: Any | None,
    registry: Any | None,
    resolve_strategies_dir: ResolveStrategiesDir,
    publish_ack: PublishAck,
    log: Any,
) -> None:
    """Route a single command dict to its handler.

    See module docstring for the contract guarantees this preserves. Designed
    to be called once per dequeued command.
    """
    action = cmd.get("cmd")
    strategy_id = cmd.get("strategy_id")

    if action == "_rescan_strategies":
        handle_rescan_strategies(
            registry=registry,
            resolve_strategies_dir=resolve_strategies_dir,
            publish_ack=publish_ack,
            log=log,
        )
        return

    if lifecycle is None:
        log.warning(f"Command '{action}' ignored: no LifecycleController")
        publish_ack(
            "commands_ack",
            {"cmd": action, "status": "error", "reason": "no_lifecycle"},
        )
        return

    try:
        if action == "pause" and not strategy_id:
            lifecycle.pause_all()
        elif action == "resume" and not strategy_id:
            lifecycle.resume_all()
        elif action == "pause":
            lifecycle.pause_strategy_id(strategy_id)
        elif action == "resume":
            lifecycle.resume_strategy_id(strategy_id)
        elif action == "flatten":
            lifecycle.flatten(strategy_id)
        elif action == "halt":
            lifecycle.halt()
        elif action == "unhalt":
            lifecycle.unhalt()
        elif action == "shutdown":
            lifecycle.shutdown()
        elif action == "start_strategy":
            lifecycle.start_strategy(cmd.get("strategy_name", ""))
        elif action == "flatten_stop_strategy":
            lifecycle.flatten_stop_strategy(cmd.get("strategy_name", ""))
        elif action == "pause_strategy":
            lifecycle.pause_strategy(cmd.get("strategy_name", ""))
        elif action == "resume_strategy":
            lifecycle.resume_strategy(cmd.get("strategy_name", ""))
        elif action == "cancel_order":
            coid = cmd.get("client_order_id")
            if coid:
                lifecycle.cancel_order(coid)
        else:
            log.warning(f"Unknown command: {action}")
    except Exception as e:  # noqa: BLE001 — mirror legacy catch-all
        log.error(f"Command '{action}' failed: {e}")
        publish_ack(
            "commands_ack",
            {"cmd": action, "status": "error", "reason": str(e)},
        )
