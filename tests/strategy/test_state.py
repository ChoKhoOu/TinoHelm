"""Tests for tinohelm.strategy.state — @stateful decorator & serialize utils."""
from __future__ import annotations

import json
from collections import deque

import pytest

from tinohelm.strategy.state import (
    deserialize_state,
    serialize_state,
    stateful,
)


# ---------------------------------------------------------------------------
# Helpers — plain Python stubs (NT Strategy is Cython, can't instantiate)
# ---------------------------------------------------------------------------

class _FakeLog:
    def __init__(self):
        self.warnings: list[str] = []

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)


class _BaseStrategy:
    """Minimal stub mimicking NT Strategy interface for testing."""
    def __init__(self):
        self.log = _FakeLog()


# ---------------------------------------------------------------------------
# @stateful decorator tests
# ---------------------------------------------------------------------------

class TestStatefulDecorator:
    def test_basic_json_types(self):
        @stateful("counter", "flag", "name", "values", "config")
        class S(_BaseStrategy):
            def __init__(self):
                super().__init__()
                self.counter = 42
                self.flag = True
                self.name = "test"
                self.values = [1, 2, 3]
                self.config = {"k": "v"}

        s = S()
        state = s.on_save()

        assert set(state.keys()) == {"counter", "flag", "name", "values", "config"}
        assert json.loads(state["counter"]) == 42
        assert json.loads(state["flag"]) is True
        assert json.loads(state["name"]) == "test"
        assert json.loads(state["values"]) == [1, 2, 3]
        assert json.loads(state["config"]) == {"k": "v"}

    def test_load_restores_state(self):
        @stateful("x", "y")
        class S(_BaseStrategy):
            def __init__(self):
                super().__init__()
                self.x = 0
                self.y = "initial"

        s = S()
        saved = {
            "x": json.dumps(99).encode("utf-8"),
            "y": json.dumps("restored").encode("utf-8"),
        }
        s.on_load(saved)

        assert s.x == 99
        assert s.y == "restored"

    def test_round_trip(self):
        @stateful("count", "data")
        class S(_BaseStrategy):
            def __init__(self):
                super().__init__()
                self.count = 100
                self.data = {"nested": [1, 2, 3]}

        s1 = S()
        state = s1.on_save()

        s2 = S()
        s2.on_load(state)

        assert s2.count == 100
        assert s2.data == {"nested": [1, 2, 3]}

    def test_custom_encoder_deque(self):
        deque_encoder = (list, lambda v: deque(v, maxlen=5))

        @stateful("window", encoders={"window": deque_encoder})
        class S(_BaseStrategy):
            def __init__(self):
                super().__init__()
                self.window = deque([10, 20, 30], maxlen=5)

        s = S()
        state = s.on_save()
        assert json.loads(state["window"]) == [10, 20, 30]

        s2 = S()
        s2.on_load(state)
        assert isinstance(s2.window, deque)
        assert list(s2.window) == [10, 20, 30]
        assert s2.window.maxlen == 5

    def test_unserializable_field_skipped_with_warning(self):
        @stateful("good", "bad")
        class S(_BaseStrategy):
            def __init__(self):
                super().__init__()
                self.good = 42
                self.bad = object()  # not JSON serializable

        s = S()
        state = s.on_save()

        assert "good" in state
        assert "bad" not in state
        assert len(s.log.warnings) == 1
        assert "bad" in s.log.warnings[0]

    def test_load_missing_field_keeps_current_value(self):
        @stateful("a", "b")
        class S(_BaseStrategy):
            def __init__(self):
                super().__init__()
                self.a = "original_a"
                self.b = "original_b"

        s = S()
        s.on_load({"a": json.dumps("new_a").encode("utf-8")})

        assert s.a == "new_a"
        assert s.b == "original_b"

    def test_load_corrupt_data_skipped_with_warning(self):
        @stateful("x")
        class S(_BaseStrategy):
            def __init__(self):
                super().__init__()
                self.x = 0

        s = S()
        s.on_load({"x": b"not valid json{{"})

        assert s.x == 0  # unchanged
        assert len(s.log.warnings) == 1

    def test_user_defined_on_save_not_overridden(self):
        @stateful("x")
        class S(_BaseStrategy):
            def __init__(self):
                super().__init__()
                self.x = 0

            def on_save(self):
                return {"custom": b"yes"}

        s = S()
        assert s.on_save() == {"custom": b"yes"}

    def test_user_defined_on_load_not_overridden(self):
        @stateful("x")
        class S(_BaseStrategy):
            def __init__(self):
                super().__init__()
                self.x = 0

            def on_load(self, state):
                self.x = -1

        s = S()
        s.on_load({})
        assert s.x == -1

    def test_on_reset_injected(self):
        @stateful("x")
        class S(_BaseStrategy):
            def __init__(self):
                super().__init__()
                self.x = 0

        s = S()
        assert hasattr(s, "on_reset")
        s.on_reset()  # should not raise

    def test_stateful_metadata_stored(self):
        @stateful("a", "b")
        class S(_BaseStrategy):
            pass

        assert S._stateful_fields == ("a", "b")
        assert S._stateful_encoders == {}

    def test_none_value_round_trip(self):
        @stateful("val")
        class S(_BaseStrategy):
            def __init__(self):
                super().__init__()
                self.val = None

        s = S()
        state = s.on_save()
        assert json.loads(state["val"]) is None

        s2 = S()
        s2.val = "something"
        s2.on_load(state)
        assert s2.val is None


# ---------------------------------------------------------------------------
# Standalone serialize/deserialize tests
# ---------------------------------------------------------------------------

class TestSerializeUtils:
    def test_serialize_basic(self):
        result = serialize_state({"a": 1, "b": "hello", "c": [1, 2]})
        assert json.loads(result["a"]) == 1
        assert json.loads(result["b"]) == "hello"
        assert json.loads(result["c"]) == [1, 2]

    def test_deserialize_basic(self):
        raw = {
            "x": json.dumps(42).encode("utf-8"),
            "y": json.dumps("test").encode("utf-8"),
        }
        result = deserialize_state(raw)
        assert result == {"x": 42, "y": "test"}

    def test_round_trip_with_encoder(self):
        enc = {"window": (list, lambda v: deque(v, maxlen=10))}
        original = {"window": deque([1, 2, 3], maxlen=10)}

        serialized = serialize_state(original, encoders=enc)
        restored = deserialize_state(serialized, encoders=enc)

        assert isinstance(restored["window"], deque)
        assert list(restored["window"]) == [1, 2, 3]
        assert restored["window"].maxlen == 10

    def test_serialize_skips_unserializable(self):
        result = serialize_state({"ok": 1, "bad": object()})
        assert "ok" in result
        assert "bad" not in result

    def test_deserialize_skips_corrupt(self):
        result = deserialize_state({"ok": b'"hello"', "bad": b"{{invalid"})
        assert result == {"ok": "hello"}
