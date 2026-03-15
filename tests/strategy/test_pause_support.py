"""Tests for L1 soft-pause helper functions in tinohelm.strategy.utils.

Covers:
    setup_pause_support(strategy) — subscribes pause/resume handlers on msgbus
    is_paused(strategy)           — reads from module-level WeakKeyDictionary

The mock strategy replicates the minimal interface expected by the helpers
without requiring the NT Cython Strategy base class.
"""
from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from tinohelm.node.topics import LIFECYCLE_PAUSE, LIFECYCLE_RESUME
from tinohelm.strategy.utils import _pause_state, is_paused, setup_pause_support


# ---------------------------------------------------------------------------
# Mock strategy
# ---------------------------------------------------------------------------


class _MockStrategy:
    def __init__(self, strategy_id: str = "MyStrategy-000"):
        self.id = strategy_id
        self.msgbus = MagicMock()
        self.log = MagicMock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_handlers(strategy: _MockStrategy) -> tuple:
    """Return (pause_handler, resume_handler) captured from msgbus.subscribe calls."""
    calls = strategy.msgbus.subscribe.call_args_list
    assert len(calls) == 2, f"Expected 2 subscribe calls, got {len(calls)}"
    pause_topic = f"{LIFECYCLE_PAUSE}.{strategy.id}"
    resume_topic = f"{LIFECYCLE_RESUME}.{strategy.id}"

    handlers: dict[str, object] = {}
    for c in calls:
        topic, handler = c.args
        handlers[topic] = handler

    assert pause_topic in handlers, f"No subscribe for {pause_topic}"
    assert resume_topic in handlers, f"No subscribe for {resume_topic}"
    return handlers[pause_topic], handlers[resume_topic]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSetupPauseSupport:
    def test_setup_subscribes_to_topics(self):
        """setup_pause_support calls msgbus.subscribe twice with correct topics."""
        s = _MockStrategy()
        setup_pause_support(s)

        pause_topic = f"{LIFECYCLE_PAUSE}.{s.id}"
        resume_topic = f"{LIFECYCLE_RESUME}.{s.id}"

        topics_subscribed = [c.args[0] for c in s.msgbus.subscribe.call_args_list]
        assert pause_topic in topics_subscribed
        assert resume_topic in topics_subscribed
        assert s.msgbus.subscribe.call_count == 2

    def test_is_paused_default_false(self):
        """is_paused returns False immediately after setup_pause_support."""
        s = _MockStrategy()
        setup_pause_support(s)
        assert is_paused(s) is False

    def test_is_paused_unknown_strategy(self):
        """is_paused returns False for a strategy that never called setup."""
        s = _MockStrategy()
        assert is_paused(s) is False


class TestPauseSignal:
    def test_pause_signal_sets_paused(self):
        """Calling the pause handler sets is_paused to True."""
        s = _MockStrategy()
        setup_pause_support(s)
        pause_handler, _ = _capture_handlers(s)

        pause_handler(msg=None)

        assert is_paused(s) is True

    def test_resume_signal_clears_paused(self):
        """After pausing, calling the resume handler sets is_paused to False."""
        s = _MockStrategy()
        setup_pause_support(s)
        pause_handler, resume_handler = _capture_handlers(s)

        pause_handler(msg=None)
        assert is_paused(s) is True

        resume_handler(msg=None)
        assert is_paused(s) is False

    def test_pause_resume_cycle(self):
        """Full pause -> resume -> pause cycle reflects correctly in is_paused."""
        s = _MockStrategy()
        setup_pause_support(s)
        pause_handler, resume_handler = _capture_handlers(s)

        pause_handler(msg=None)
        assert is_paused(s) is True

        resume_handler(msg=None)
        assert is_paused(s) is False

        pause_handler(msg=None)
        assert is_paused(s) is True


class TestMultipleStrategies:
    def test_multiple_strategies_independent(self):
        """Pausing one strategy does not affect another."""
        s1 = _MockStrategy("Alpha-001")
        s2 = _MockStrategy("Beta-002")
        setup_pause_support(s1)
        setup_pause_support(s2)

        pause_s1, _ = _capture_handlers(s1)
        pause_s1(msg=None)

        assert is_paused(s1) is True
        assert is_paused(s2) is False
