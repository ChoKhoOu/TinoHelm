"""Tests for the example strategy's on_save / on_load round-trip.

When NT's kernel stops with ``save_state=True`` it calls ``Trader.save()``,
which in turn calls each strategy's ``on_save()``; the returned dict is
persisted to the cache database (Redis). On restart with ``load_state=True``,
the kernel calls ``on_load(state)`` with the saved bytes. The example
strategy demonstrates this round-trip so users have a concrete template to
copy when wiring their own state.

We exercise the protocol directly (no NT runtime) because:

  * ``on_save`` / ``on_load`` are documented hooks (Actor.pyx:210/228)
    designed for direct invocation from the framework
  * a true end-to-end test would need a Redis cache, full TradingNode, and
    a fake clock — pure schema fidelity is what we care about here
"""

from __future__ import annotations

from strategies.example.strategy import ExampleStrategy, ExampleStrategyConfig


def test_example_strategy_round_trips_tick_count() -> None:
    """A strategy that counted 42 ticks before shutdown should resume at 42.

    Without this, every redeploy would silently reset internal counters —
    exactly the kind of state drift NT's load/save protocol exists to
    prevent. on_save returns ``dict[str, bytes]``; on_load receives the
    same shape.
    """

    src = ExampleStrategy(ExampleStrategyConfig(strategy_id="FOO-001"))
    src._tick_count = 42  # simulate live activity

    saved = src.on_save()
    assert b"42" in saved.get("tick_count", b"")

    dst = ExampleStrategy(ExampleStrategyConfig(strategy_id="FOO-001"))
    dst.on_load(saved)
    assert dst._tick_count == 42


def test_example_strategy_on_load_tolerates_empty_state() -> None:
    """First boot with no prior cached state — on_load gets ``{}`` (or a
    dict missing our keys). Must not crash; the strategy should default to
    a fresh-start counter.
    """

    s = ExampleStrategy(ExampleStrategyConfig(strategy_id="FOO-001"))
    s.on_load({})
    assert s._tick_count == 0
