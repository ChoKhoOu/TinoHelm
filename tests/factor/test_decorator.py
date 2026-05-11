"""Unit tests for ``tinohelm.factor.decorator`` and ``tinohelm.factor.ast_check``.

Coverage
--------
- @factor attaches __factor_spec__ transparently (function still callable)
- category is stored verbatim including Chinese strings
- lookback base value propagated correctly
- shift detection adds to lookback (AC-2: shift(-3) + base 10 → 13)
- chained shifts are summed
- code_hash changes when source text changes (AC-3)
- input_specs derived from annotated Panel parameters (AC-1)
- scalar-annotated parameters (int/float/str) excluded from input_specs
- unannotated parameters included as Panel inputs
- ShiftDetector.detect_max_shift on various AST patterns
- dynamic shift (variable argument) contributes 0 conservatively
- source-unavailable function returns 0 from ShiftDetector
- @factor with no-annotation function still works
- output_spec and params forwarded into FactorSpec
- lookback clipped to minimum 1
"""
from __future__ import annotations

import hashlib
import inspect
import textwrap
from typing import Any
from unittest.mock import patch

import pytest

from tinohelm.factor.ast_check import ShiftDetector
from tinohelm.factor.decorator import factor
from tinohelm.factor.types import FactorSpec, InputSpec, OutputSpec, Panel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_source(func) -> str:
    source = inspect.getsource(func)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# AC-1: Basic decorator attachment and field values
# ---------------------------------------------------------------------------

class TestFactorDecoratorBasic:
    def test_spec_attached(self):
        @factor(category="动量", lookback=20)
        def f(close: Panel) -> Panel:
            return close.rank()

        assert hasattr(f, "__factor_spec__")
        assert isinstance(f.__factor_spec__, FactorSpec)

    def test_category_stored(self):
        @factor(category="动量", lookback=20)
        def f(close: Panel) -> Panel:
            return close.rank()

        assert f.__factor_spec__.category == "动量"

    def test_lookback_stored(self):
        @factor(category="动量", lookback=20)
        def f(close: Panel) -> Panel:
            return close.rank()

        assert f.__factor_spec__.lookback == 20

    def test_input_specs_panel_param(self):
        @factor(category="动量", lookback=20)
        def f(close: Panel) -> Panel:
            return close.rank()

        specs = f.__factor_spec__.input_specs
        assert len(specs) == 1
        assert specs[0].field_name == "close"

    def test_function_still_callable(self):
        """Decorator must not break function call — returns original function."""
        import pandas as pd

        @factor(category="测试")
        def f(close: Panel) -> Panel:
            return close

        result = f(pd.DataFrame({"A": [1, 2, 3]}))
        assert len(result) == 3

    def test_function_name_preserved(self):
        @factor(category="动量")
        def my_factor(close: Panel) -> Panel:
            return close

        assert my_factor.__name__ == "my_factor"
        assert my_factor.__factor_spec__.name == "my_factor"

    def test_chinese_category_preserved(self):
        """Chinese strings must survive all intermediate processing."""
        for cat in ("动量", "波动", "量价", "微观结构"):
            @factor(category=cat)
            def f(close: Panel) -> Panel:
                return close

            assert f.__factor_spec__.category == cat


# ---------------------------------------------------------------------------
# AC-1 extended: alias resolution for input params
# ---------------------------------------------------------------------------

class TestInputSpecAliasResolution:
    def test_alias_close_canonical(self):
        @factor(category="X")
        def f(收盘价: Panel) -> Panel:
            return 收盘价

        assert f.__factor_spec__.input_specs[0].field_name == "close"

    def test_alias_vol_to_volume(self):
        @factor(category="X")
        def f(vol: Panel) -> Panel:
            return vol

        assert f.__factor_spec__.input_specs[0].field_name == "volume"

    def test_multi_input_params(self):
        @factor(category="量价")
        def f(close: Panel, volume: Panel) -> Panel:
            return close / volume

        specs = f.__factor_spec__.input_specs
        assert len(specs) == 2
        field_names = {s.field_name for s in specs}
        assert field_names == {"close", "volume"}

    def test_scalar_int_param_excluded(self):
        """Parameters annotated as int are factor params, not data inputs."""
        @factor(category="动量")
        def f(close: Panel, lookback: int = 20) -> Panel:
            return close.pct_change(lookback)

        specs = f.__factor_spec__.input_specs
        field_names = [s.field_name for s in specs]
        assert "lookback" not in field_names
        assert "close" in field_names

    def test_scalar_float_param_excluded(self):
        @factor(category="X")
        def f(close: Panel, threshold: float = 0.5) -> Panel:
            return close

        field_names = [s.field_name for s in f.__factor_spec__.input_specs]
        assert "threshold" not in field_names

    def test_scalar_str_param_excluded(self):
        @factor(category="X")
        def f(close: Panel, mode: str = "default") -> Panel:
            return close

        field_names = [s.field_name for s in f.__factor_spec__.input_specs]
        assert "mode" not in field_names

    def test_params_kwarg_excluded(self):
        """Legacy 'params' dict parameter must be excluded from input_specs."""
        @factor(category="X")
        def f(close: Panel, params: dict) -> Panel:
            return close

        field_names = [s.field_name for s in f.__factor_spec__.input_specs]
        assert "params" not in field_names

    def test_unannotated_param_treated_as_panel(self):
        """Unannotated parameters are conservatively treated as Panel inputs."""
        @factor(category="X")
        def f(close) -> Panel:
            return close

        field_names = [s.field_name for s in f.__factor_spec__.input_specs]
        assert "close" in field_names


# ---------------------------------------------------------------------------
# AC-2: Shift detection adds to lookback
# ---------------------------------------------------------------------------

class TestShiftLookback:
    def test_shift_added_to_base(self):
        @factor(category="动量", lookback=10)
        def f(close: Panel) -> Panel:
            return close.shift(-3)

        assert f.__factor_spec__.lookback == 13  # 10 + 3

    def test_shift_positive_arg(self):
        @factor(category="X", lookback=5)
        def f(close: Panel) -> Panel:
            return close.shift(4)

        assert f.__factor_spec__.lookback == 9  # 5 + 4

    def test_shift_zero_base(self):
        @factor(category="X", lookback=0)
        def f(close: Panel) -> Panel:
            return close.shift(-7)

        # 0 + 7 = 7; max(7,1) = 7
        assert f.__factor_spec__.lookback == 7

    def test_no_shift_base_only(self):
        @factor(category="X", lookback=15)
        def f(close: Panel) -> Panel:
            return close.pct_change(15)

        assert f.__factor_spec__.lookback == 15

    def test_chained_shifts_summed(self):
        """Chained .shift(-3).shift(-2) → both detected → sum = 5."""
        @factor(category="X", lookback=10)
        def f(close: Panel) -> Panel:
            return close.shift(-3).shift(-2)

        assert f.__factor_spec__.lookback == 15  # 10 + 3 + 2

    def test_multiple_independent_shifts(self):
        """Multiple shift calls in function body → all absolute values summed."""
        @factor(category="X", lookback=0)
        def f(close: Panel, volume: Panel) -> Panel:
            a = close.shift(-2)
            b = volume.shift(-5)
            return a + b

        # 0 + 2 + 5 = 7
        assert f.__factor_spec__.lookback == 7

    def test_lookback_minimum_one(self):
        """Even with lookback=0 and no shifts, final lookback >= 1."""
        @factor(category="X", lookback=0)
        def f(close: Panel) -> Panel:
            return close.rank()

        assert f.__factor_spec__.lookback >= 1


# ---------------------------------------------------------------------------
# AC-3: code_hash changes when source changes
# ---------------------------------------------------------------------------

class TestCodeHash:
    def test_code_hash_is_sha256(self):
        @factor(category="X")
        def f(close: Panel) -> Panel:
            return close

        h = f.__factor_spec__.code_hash
        assert len(h) == 64  # SHA-256 hex = 64 chars
        assert all(c in "0123456789abcdef" for c in h)

    def test_code_hash_changes_with_source(self):
        """Two differently-sourced functions must have different code_hash."""
        @factor(category="X")
        def f_v1(close: Panel) -> Panel:
            return close.rank()  # version 1

        @factor(category="X")
        def f_v2(close: Panel) -> Panel:
            return close.pct_change()  # version 2 — different body

        assert f_v1.__factor_spec__.code_hash != f_v2.__factor_spec__.code_hash

    def test_code_hash_identical_for_same_source(self):
        """Same function body decorated twice should produce same hash."""
        # We replicate exact identical logic in two definitions
        @factor(category="X")
        def f_a(close: Panel) -> Panel:
            return close  # identical body

        # Re-decorate the same function object directly
        decorated_twice = factor(category="X")(f_a.__wrapped__ if hasattr(f_a, "__wrapped__") else f_a)
        # Both should produce the same hash (they are literally the same object)
        assert f_a.__factor_spec__.code_hash == decorated_twice.__factor_spec__.code_hash

    def test_code_hash_nonempty(self):
        @factor(category="X")
        def f(close: Panel) -> Panel:
            return close

        assert f.__factor_spec__.code_hash != ""

    def test_code_hash_falls_back_when_source_unavailable(self, monkeypatch):
        """Same contract as signal decorator: when ``inspect.getsource``
        cannot retrieve the body (stale pyc with ``co_filename`` pointing
        at a path that no longer exists, C extensions, etc.), the factor
        decorator must still return a non-empty deterministic hash. An
        empty hash would collapse every factor's identity and break
        caches keyed on ``code_hash``.
        """
        import tinohelm.factor.decorator as fac_dec

        def raising_getsource(_obj):
            raise OSError("could not get source code")

        monkeypatch.setattr(fac_dec.inspect, "getsource", raising_getsource)

        @fac_dec.factor(category="X")
        def f(close: Panel) -> Panel:
            return close

        h = f.__factor_spec__.code_hash
        assert h != "", "source-missing fallback must produce a non-empty hash"
        assert len(h) == 64


# ---------------------------------------------------------------------------
# ShiftDetector unit tests
# ---------------------------------------------------------------------------

class TestShiftDetector:
    def test_no_shift_returns_zero(self):
        def f():
            x = 1 + 2
            return x

        assert ShiftDetector.detect_max_shift(f) == 0

    def test_negative_shift_literal(self):
        def f():
            return x.shift(-5)

        assert ShiftDetector.detect_max_shift(f) == 5

    def test_positive_shift_literal(self):
        def f():
            return x.shift(3)

        assert ShiftDetector.detect_max_shift(f) == 3

    def test_chained_shift_sum(self):
        def f():
            return x.shift(-3).shift(-2)

        assert ShiftDetector.detect_max_shift(f) == 5  # 3 + 2

    def test_multiple_shifts_summed(self):
        def f():
            a = x.shift(-4)
            b = y.shift(-1)
            return a + b

        assert ShiftDetector.detect_max_shift(f) == 5  # 4 + 1

    def test_dynamic_shift_contributes_zero(self):
        """Variable argument: conservative 0 contribution."""
        n = 5

        def f():
            return x.shift(n)  # n is a variable, not a literal

        result = ShiftDetector.detect_max_shift(f)
        assert result == 0

    def test_source_unavailable_returns_zero(self):
        """Built-in / C functions: getsource raises OSError → return 0."""
        assert ShiftDetector.detect_max_shift(len) == 0

    def test_zero_shift_literal(self):
        """shift(0) contributes abs(0) = 0."""
        def f():
            return x.shift(0)

        assert ShiftDetector.detect_max_shift(f) == 0

    def test_non_shift_attribute_call_ignored(self):
        """Only .shift() calls are tracked; other attribute calls are ignored."""
        def f():
            return x.rolling(20).mean()

        assert ShiftDetector.detect_max_shift(f) == 0

    def test_shift_with_kwargs_ignored(self):
        """shift(periods=-3) using keyword arg — not matched (no positional arg)."""
        def f():
            return x.shift(periods=-3)

        # Our detector requires exactly 1 positional arg, no kwargs
        assert ShiftDetector.detect_max_shift(f) == 0

    def test_method_named_shift_on_unrelated_object_tracked(self):
        """Detector is conservative: ANY .shift(N) call is tracked."""
        def f():
            return some_object.shift(-10)

        assert ShiftDetector.detect_max_shift(f) == 10


# ---------------------------------------------------------------------------
# FactorSpec field forwarding
# ---------------------------------------------------------------------------

class TestFactorSpecFieldForwarding:
    def test_params_forwarded(self):
        @factor(category="X", params={"n": 20, "alpha": 0.5})
        def f(close: Panel) -> Panel:
            return close

        assert f.__factor_spec__.params == {"n": 20, "alpha": 0.5}

    def test_params_none_becomes_empty_dict(self):
        @factor(category="X", params=None)
        def f(close: Panel) -> Panel:
            return close

        assert f.__factor_spec__.params == {}

    def test_description_forwarded(self):
        @factor(category="X", description="my description")
        def f(close: Panel) -> Panel:
            return close

        assert f.__factor_spec__.description == "my description"

    def test_version_forwarded(self):
        @factor(category="X", version="2.1.0")
        def f(close: Panel) -> Panel:
            return close

        assert f.__factor_spec__.version == "2.1.0"

    def test_output_spec_forwarded(self):
        custom_out = OutputSpec(dtype="float32", value_range=(-1.0, 1.0), description="z-score")

        @factor(category="X", output_spec=custom_out)
        def f(close: Panel) -> Panel:
            return close

        assert f.__factor_spec__.output_spec is custom_out

    def test_default_output_spec(self):
        @factor(category="X")
        def f(close: Panel) -> Panel:
            return close

        assert f.__factor_spec__.output_spec == OutputSpec()

    def test_negative_lookback_raises(self):
        with pytest.raises(ValueError, match="lookback"):
            @factor(category="X", lookback=-1)
            def f(close: Panel) -> Panel:
                return close

    def test_input_spec_is_tuple(self):
        @factor(category="X")
        def f(close: Panel, volume: Panel) -> Panel:
            return close

        assert isinstance(f.__factor_spec__.input_specs, tuple)

    def test_input_spec_field_name_type(self):
        @factor(category="X")
        def f(close: Panel) -> Panel:
            return close

        spec = f.__factor_spec__.input_specs[0]
        assert isinstance(spec, InputSpec)
        assert isinstance(spec.field_name, str)
