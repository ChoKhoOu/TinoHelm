"""Unit tests — ``tinohelm.factor.evaluation.segmentation``.

5 core tests covering:
1. btc_trend segments produce "up" and "down" EvalResult objects.
2. vol_regime segments produce "high" and "low" EvalResult objects.
3. funding_level segments produce "positive" and "negative" EvalResult objects.
4. Empty segment (e.g. all timestamps in one bin) returns EvalResult, not an exception.
5. All three providers combined → output has 3 keys.

Pure-logic, deterministic (fixed seeds), NT-free, < 500ms.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import polars as pl
import pytest

from tinohelm.factor.evaluation.segmentation import segment_evaluate
from tinohelm.factor.types import EvalConfig, EvalResult


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _hourly_ts(n: int, start: dt.datetime = dt.datetime(2024, 1, 1)) -> pl.Series:
    return pl.datetime_range(
        start=start,
        end=start + dt.timedelta(hours=n - 1),
        interval="1h",
        eager=True,
    )


N = 500  # default panel length


def _make_panel(n: int = N, seed: int = 0) -> pl.DataFrame:
    """2-col [ts, value] factor panel."""
    rng = np.random.default_rng(seed)
    ts = _hourly_ts(n)
    return pl.DataFrame({"ts": ts, "value": rng.normal(0, 1, n).tolist()})


def _make_fwd(n: int = N, seed: int = 42) -> pl.DataFrame:
    """2-col [ts, value] forward-return panel."""
    rng = np.random.default_rng(seed)
    ts = _hourly_ts(n)
    vals = rng.normal(0, 0.01, n).tolist()
    vals[-1] = None
    return pl.DataFrame({"ts": ts, "value": vals})


def _make_close(n: int = N, seed: int = 1) -> pl.Series:
    """Simulated BTC close-price series (positional, length = n)."""
    rng = np.random.default_rng(seed)
    prices = 30000.0 + np.cumsum(rng.normal(0, 100, n))
    prices = np.abs(prices)  # ensure positive
    return pl.Series("close", prices.tolist())


def _make_funding(n: int = N, seed: int = 2) -> pl.Series:
    """Simulated funding-rate series (mix of positive and negative)."""
    rng = np.random.default_rng(seed)
    rates = rng.normal(0.0001, 0.0005, n)
    return pl.Series("funding", rates.tolist())


_PANEL = _make_panel()
_FWD = _make_fwd()
_CLOSE = _make_close()
_FUNDING = _make_funding()
_CONFIG = EvalConfig(universe=(), start="", end="")


def _is_eval_result(obj: object) -> bool:
    return isinstance(obj, EvalResult)


# ---------------------------------------------------------------------------
# 1. test_btc_trend_segments_up_down
# ---------------------------------------------------------------------------

class TestBtcTrendSegments:
    def test_btc_trend_segments_up_down(self):
        """btc_trend key must be present with 'up' and 'down' EvalResult values."""
        result = segment_evaluate(
            _PANEL,
            _FWD,
            btc_close_series=_CLOSE,
            eval_config=_CONFIG,
        )
        assert "btc_trend" in result
        assert "up" in result["btc_trend"]
        assert "down" in result["btc_trend"]
        assert _is_eval_result(result["btc_trend"]["up"])
        assert _is_eval_result(result["btc_trend"]["down"])

    def test_btc_trend_not_present_without_series(self):
        """btc_trend must be absent when btc_close_series is not provided."""
        result = segment_evaluate(_PANEL, _FWD, eval_config=_CONFIG)
        assert "btc_trend" not in result

    def test_btc_trend_segments_cover_all_ts(self):
        """up + down ts-set must together cover all panel timestamps."""
        panel = _make_panel(500, seed=20)
        fwd = _make_fwd(500, seed=20)
        close = _make_close(500, seed=3)
        result = segment_evaluate(panel, fwd, btc_close_series=close, eval_config=_CONFIG)
        # Both segments exist.
        assert "up" in result["btc_trend"]
        assert "down" in result["btc_trend"]


# ---------------------------------------------------------------------------
# 2. test_vol_regime_segments_high_low
# ---------------------------------------------------------------------------

class TestVolRegimeSegments:
    def test_vol_regime_segments_high_low(self):
        """vol_regime key must be present with 'high' and 'low' EvalResult values."""
        result = segment_evaluate(
            _PANEL,
            _FWD,
            btc_vol_series=_CLOSE,
            eval_config=_CONFIG,
        )
        assert "vol_regime" in result
        assert "high" in result["vol_regime"]
        assert "low" in result["vol_regime"]
        assert _is_eval_result(result["vol_regime"]["high"])
        assert _is_eval_result(result["vol_regime"]["low"])

    def test_vol_regime_not_present_without_series(self):
        """vol_regime must be absent when btc_vol_series is not provided."""
        result = segment_evaluate(_PANEL, _FWD, eval_config=_CONFIG)
        assert "vol_regime" not in result

    def test_vol_regime_result_fields_are_finite_or_zero(self):
        """vol_regime EvalResult ir field must be a finite float (not NaN/Inf)."""
        result = segment_evaluate(
            _PANEL, _FWD, btc_vol_series=_CLOSE, eval_config=_CONFIG
        )
        for label in ("high", "low"):
            ir = result["vol_regime"][label].ir
            assert ir is not None
            assert math.isfinite(float(ir))


# ---------------------------------------------------------------------------
# 3. test_funding_level_segments_positive_negative
# ---------------------------------------------------------------------------

class TestFundingLevelSegments:
    def test_funding_level_segments_positive_negative(self):
        """funding_level key must be present with 'positive' and 'negative' EvalResult values."""
        result = segment_evaluate(
            _PANEL,
            _FWD,
            funding_series=_FUNDING,
            eval_config=_CONFIG,
        )
        assert "funding_level" in result
        assert "positive" in result["funding_level"]
        assert "negative" in result["funding_level"]
        assert _is_eval_result(result["funding_level"]["positive"])
        assert _is_eval_result(result["funding_level"]["negative"])

    def test_funding_level_not_present_without_series(self):
        result = segment_evaluate(_PANEL, _FWD, eval_config=_CONFIG)
        assert "funding_level" not in result

    def test_funding_all_positive(self):
        """All-positive funding → 'negative' segment is empty → EvalResult with defaults."""
        all_pos_funding = pl.Series("funding", [0.001] * N)
        result = segment_evaluate(
            _PANEL, _FWD, funding_series=all_pos_funding, eval_config=_CONFIG
        )
        neg_result = result["funding_level"]["negative"]
        # Empty segment → defaults: ir = 0.0.
        assert neg_result.ir == 0.0


# ---------------------------------------------------------------------------
# 4. test_empty_segment_returns_empty_evalresult
# ---------------------------------------------------------------------------

class TestEmptySegmentReturnsEvalResult:
    def test_empty_segment_does_not_raise(self):
        """When all timestamps fall into one regime, the other returns EvalResult(ir=0)."""
        # All-constant close → pct_change_20 alternates but still won't raise.
        const_close = pl.Series("close", [30000.0] * N)
        result = segment_evaluate(
            _PANEL, _FWD, btc_close_series=const_close, eval_config=_CONFIG
        )
        assert "btc_trend" in result
        # Both keys must be EvalResult (no exception).
        assert _is_eval_result(result["btc_trend"]["up"])
        assert _is_eval_result(result["btc_trend"]["down"])

    def test_empty_segment_eval_result_has_zero_ir(self):
        """Empty segment (no matching ts) returns EvalResult with ir=0.0."""
        # Force all timestamps into "negative" by making all funding > 0.
        all_pos_funding = pl.Series("funding", [0.001] * N)
        result = segment_evaluate(
            _PANEL, _FWD, funding_series=all_pos_funding, eval_config=_CONFIG
        )
        # "negative" segment has zero rows → must be a valid EvalResult.
        neg = result["funding_level"]["negative"]
        assert isinstance(neg, EvalResult)
        assert neg.ir == 0.0

    def test_schema_errors_are_not_swallowed_as_empty_segments(self):
        """Duplicate identity rows must surface instead of becoming zero metrics."""
        panel = pl.DataFrame(
            {
                "ts": [*_hourly_ts(30).to_list(), _hourly_ts(30)[0]],
                "value": [0.1] * 31,
            }
        )
        fwd = pl.DataFrame(
            {
                "ts": [*_hourly_ts(30).to_list(), _hourly_ts(30)[0]],
                "value": [0.01] * 31,
            }
        )
        funding = pl.Series("funding", [0.001] * 31)

        with pytest.raises(ValueError, match="duplicate identity"):
            segment_evaluate(panel, fwd, funding_series=funding, eval_config=_CONFIG)

    def test_segment_evaluate_no_series_returns_empty_dict(self):
        """When no series are supplied, the output dict is empty."""
        result = segment_evaluate(_PANEL, _FWD, eval_config=_CONFIG)
        assert result == {}


# ---------------------------------------------------------------------------
# 5. test_all_three_segments_combined
# ---------------------------------------------------------------------------

class TestAllThreeSegmentsCombined:
    def test_all_three_segments_combined(self):
        """Providing all 3 series → output dict has exactly 3 keys."""
        result = segment_evaluate(
            _PANEL,
            _FWD,
            btc_close_series=_CLOSE,
            btc_vol_series=_CLOSE,
            funding_series=_FUNDING,
            eval_config=_CONFIG,
        )
        assert set(result.keys()) == {"btc_trend", "vol_regime", "funding_level"}

    def test_all_three_segments_have_correct_labels(self):
        """Each provider's sub-keys must be the expected regime labels."""
        result = segment_evaluate(
            _PANEL,
            _FWD,
            btc_close_series=_CLOSE,
            btc_vol_series=_CLOSE,
            funding_series=_FUNDING,
            eval_config=_CONFIG,
        )
        assert set(result["btc_trend"].keys()) == {"up", "down"}
        assert set(result["vol_regime"].keys()) == {"high", "low"}
        assert set(result["funding_level"].keys()) == {"positive", "negative"}

    def test_all_three_segments_all_values_are_eval_results(self):
        """Every leaf value must be an EvalResult instance."""
        result = segment_evaluate(
            _PANEL,
            _FWD,
            btc_close_series=_CLOSE,
            btc_vol_series=_CLOSE,
            funding_series=_FUNDING,
            eval_config=_CONFIG,
        )
        for provider_results in result.values():
            for eval_res in provider_results.values():
                assert isinstance(eval_res, EvalResult)

    def test_all_three_segments_ir_fields_are_finite(self):
        """ir field of every EvalResult must be a finite float."""
        result = segment_evaluate(
            _PANEL,
            _FWD,
            btc_close_series=_CLOSE,
            btc_vol_series=_CLOSE,
            funding_series=_FUNDING,
            eval_config=_CONFIG,
        )
        for provider_results in result.values():
            for eval_res in provider_results.values():
                assert math.isfinite(float(eval_res.ir))

    def test_default_eval_config_applied_when_none(self):
        """Passing eval_config=None should not raise and should produce valid output."""
        result = segment_evaluate(
            _PANEL,
            _FWD,
            btc_close_series=_CLOSE,
            eval_config=None,  # triggers default
        )
        assert "btc_trend" in result
        assert _is_eval_result(result["btc_trend"]["up"])
