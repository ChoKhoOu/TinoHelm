"""Tests for tinohelm.core.utils — sanitize_for_json and shared utilities."""
from __future__ import annotations

import math

import pytest

from tinohelm.core.utils import sanitize_for_json


# ---------------------------------------------------------------------------
# sanitize_for_json: float handling
# ---------------------------------------------------------------------------

class TestSanitizeFloat:

    def test_nan_becomes_none(self):
        assert sanitize_for_json(float("nan")) is None

    def test_positive_inf_becomes_none(self):
        assert sanitize_for_json(float("inf")) is None

    def test_negative_inf_becomes_none(self):
        assert sanitize_for_json(float("-inf")) is None

    def test_normal_float_preserved(self):
        assert sanitize_for_json(3.14) == 3.14

    def test_zero_float_preserved(self):
        assert sanitize_for_json(0.0) == 0.0

    def test_negative_float_preserved(self):
        assert sanitize_for_json(-99.5) == -99.5

    def test_very_small_float_preserved(self):
        val = 1e-300
        assert sanitize_for_json(val) == val

    def test_very_large_float_preserved(self):
        val = 1e300
        assert sanitize_for_json(val) == val


# ---------------------------------------------------------------------------
# sanitize_for_json: non-float scalars pass through
# ---------------------------------------------------------------------------

class TestSanitizePassthrough:

    def test_int_passthrough(self):
        assert sanitize_for_json(42) == 42

    def test_string_passthrough(self):
        assert sanitize_for_json("hello") == "hello"

    def test_none_passthrough(self):
        assert sanitize_for_json(None) is None

    def test_bool_passthrough(self):
        assert sanitize_for_json(True) is True
        assert sanitize_for_json(False) is False


# ---------------------------------------------------------------------------
# sanitize_for_json: dict handling
# ---------------------------------------------------------------------------

class TestSanitizeDict:

    def test_empty_dict(self):
        assert sanitize_for_json({}) == {}

    def test_dict_with_nan_value(self):
        result = sanitize_for_json({"a": float("nan"), "b": 1.0})
        assert result["a"] is None
        assert result["b"] == 1.0

    def test_dict_with_inf_value(self):
        result = sanitize_for_json({"x": float("inf")})
        assert result["x"] is None

    def test_nested_dict(self):
        data = {
            "outer": {
                "inner": float("nan"),
                "ok": 42,
            },
            "top_level": float("-inf"),
        }
        result = sanitize_for_json(data)
        assert result["outer"]["inner"] is None
        assert result["outer"]["ok"] == 42
        assert result["top_level"] is None

    def test_deeply_nested_dict(self):
        data = {"a": {"b": {"c": {"d": float("nan")}}}}
        result = sanitize_for_json(data)
        assert result["a"]["b"]["c"]["d"] is None

    def test_dict_preserves_string_keys(self):
        data = {"key_with_nan": float("nan")}
        result = sanitize_for_json(data)
        assert "key_with_nan" in result


# ---------------------------------------------------------------------------
# sanitize_for_json: list handling
# ---------------------------------------------------------------------------

class TestSanitizeList:

    def test_empty_list(self):
        assert sanitize_for_json([]) == []

    def test_list_with_nan(self):
        result = sanitize_for_json([1.0, float("nan"), 3.0])
        assert result == [1.0, None, 3.0]

    def test_list_with_inf(self):
        result = sanitize_for_json([float("inf"), float("-inf")])
        assert result == [None, None]

    def test_nested_list(self):
        result = sanitize_for_json([[float("nan")], [1.0, 2.0]])
        assert result == [[None], [1.0, 2.0]]


# ---------------------------------------------------------------------------
# sanitize_for_json: mixed structures (realistic backtest result shapes)
# ---------------------------------------------------------------------------

class TestSanitizeMixed:

    def test_realistic_statistics_dict(self):
        """Simulate a backtest statistics dict with NaN values."""
        stats = {
            "total_pnl": 1234.56,
            "sharpe_ratio": float("nan"),
            "max_drawdown": -0.15,
            "calmar_ratio": float("inf"),
            "trades": [
                {"pnl": 50.0, "return_pct": float("nan")},
                {"pnl": -20.0, "return_pct": -0.02},
            ],
        }
        result = sanitize_for_json(stats)
        assert result["total_pnl"] == 1234.56
        assert result["sharpe_ratio"] is None
        assert result["max_drawdown"] == -0.15
        assert result["calmar_ratio"] is None
        assert result["trades"][0]["return_pct"] is None
        assert result["trades"][1]["pnl"] == -20.0

    def test_list_of_dicts(self):
        data = [
            {"a": float("nan")},
            {"b": 1.0},
        ]
        result = sanitize_for_json(data)
        assert result[0]["a"] is None
        assert result[1]["b"] == 1.0

    def test_dict_with_mixed_value_types(self):
        data = {
            "str_val": "hello",
            "int_val": 42,
            "float_val": 3.14,
            "nan_val": float("nan"),
            "none_val": None,
            "bool_val": True,
            "list_val": [1, float("inf")],
            "dict_val": {"nested": float("-inf")},
        }
        result = sanitize_for_json(data)
        assert result["str_val"] == "hello"
        assert result["int_val"] == 42
        assert result["float_val"] == 3.14
        assert result["nan_val"] is None
        assert result["none_val"] is None
        assert result["bool_val"] is True
        assert result["list_val"] == [1, None]
        assert result["dict_val"]["nested"] is None

    def test_does_not_mutate_original(self):
        """sanitize_for_json should return a new structure, not mutate in place."""
        original_dict = {"a": float("nan"), "b": [float("inf")]}
        result = sanitize_for_json(original_dict)
        # Original should still have NaN
        assert math.isnan(original_dict["a"])
        assert math.isinf(original_dict["b"][0])
        # Result should be sanitized
        assert result["a"] is None
        assert result["b"][0] is None
