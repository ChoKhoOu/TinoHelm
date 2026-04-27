"""Unit tests — :mod:`tinohelm.nt_adapter.factor_panel`.

Coverage
--------
* :func:`supported_bar_fields` returns the expected OHLCV set.
* :func:`factor_uses_only_bar_fields` distinguishes OHLCV-only factors
  from those needing funding_rate / open_interest / etc.
* :func:`build_wide_panel` constructs the polars wide table from
  newest-first NT bar lists.
* :func:`build_wide_panel` returns ``None`` when any symbol's history is
  shorter than ``min_history``.
* :func:`compute_latest_factor_panel` invokes the kernel with the right
  signature and merges spec.params with extra params.
"""
from __future__ import annotations

from dataclasses import dataclass

import polars as pl
import pytest

from tinohelm.factor.types import FactorSpec, InputSpec
from tinohelm.nt_adapter.factor_panel import (
    build_wide_panel,
    compute_latest_factor_panel,
    factor_uses_only_bar_fields,
    supported_bar_fields,
)


# ---------------------------------------------------------------------------
# Bar stub mirrored on NT's Bar interface (close, high, low, open, volume,
# ts_init).  Plain dataclass so getattr/float work without NT imports.
# ---------------------------------------------------------------------------


@dataclass
class _Bar:
    ts_init: int
    open: float = 100.0
    high: float = 101.0
    low: float = 99.0
    close: float = 100.5
    volume: float = 10.0


def _make_bars_newest_first(n: int, base_close: float = 100.0) -> list[_Bar]:
    """Build n bars in NT cache order (newest-first).

    Each bar's close is ``base_close + idx`` where idx is the
    chronological index — so after reversing we get strictly increasing
    closes (helpful for verifying ordering).
    """
    base_ts = 1_700_000_000_000_000_000
    step_ns = 60_000_000_000  # 1 minute
    chrono = []
    for i in range(n):
        chrono.append(
            _Bar(
                ts_init=base_ts + i * step_ns,
                close=base_close + i,
                high=base_close + i + 0.5,
                low=base_close + i - 0.5,
                open=base_close + i - 0.25,
                volume=1000.0 + i,
            )
        )
    # NT cache order = newest-first.
    return list(reversed(chrono))


# ---------------------------------------------------------------------------
# 1. supported_bar_fields / factor_uses_only_bar_fields
# ---------------------------------------------------------------------------


def test_supported_bar_fields_is_exact_ohlcv_set():
    assert supported_bar_fields() == frozenset(
        {"close", "open", "high", "low", "volume"}
    )


def test_factor_uses_only_bar_fields_with_close():
    spec = FactorSpec(
        name="ret_N",
        category="动量",
        lookback=20,
        input_specs=(InputSpec(field_name="close"),),
    )
    assert factor_uses_only_bar_fields(spec) is True


def test_factor_uses_only_bar_fields_with_funding_rate():
    spec = FactorSpec(
        name="funding_rate_level",
        category="资金费率",
        lookback=1,
        input_specs=(InputSpec(field_name="funding_rate"),),
    )
    assert factor_uses_only_bar_fields(spec) is False


def test_factor_uses_only_bar_fields_with_open_interest():
    spec = FactorSpec(
        name="oi_change",
        category="链上数据",
        lookback=2,
        input_specs=(InputSpec(field_name="open_interest"),),
    )
    assert factor_uses_only_bar_fields(spec) is False


def test_factor_uses_only_bar_fields_mixed_partial_unsupported():
    spec = FactorSpec(
        name="weird_factor",
        category="实验",
        lookback=10,
        input_specs=(
            InputSpec(field_name="close"),
            InputSpec(field_name="funding_rate"),
        ),
    )
    # Even one unsupported field disqualifies the factor.
    assert factor_uses_only_bar_fields(spec) is False


def test_factor_uses_only_bar_fields_empty_input_specs_is_compatible():
    """Empty input_specs is rare (only when @factor failed to introspect)
    but treated as compatible to avoid false rejections in tests."""
    spec = FactorSpec(name="x", category="y", lookback=1, input_specs=())
    assert factor_uses_only_bar_fields(spec) is True


def test_factor_uses_only_bar_fields_supports_all_ohlcv():
    spec = FactorSpec(
        name="vwap_dev",
        category="成交量",
        lookback=20,
        input_specs=(
            InputSpec(field_name="high"),
            InputSpec(field_name="low"),
            InputSpec(field_name="close"),
            InputSpec(field_name="volume"),
        ),
    )
    assert factor_uses_only_bar_fields(spec) is True


# ---------------------------------------------------------------------------
# 2. build_wide_panel
# ---------------------------------------------------------------------------


def test_build_wide_panel_close_two_symbols():
    bars_btc = _make_bars_newest_first(n=10, base_close=50_000.0)
    bars_eth = _make_bars_newest_first(n=10, base_close=3_000.0)

    panel = build_wide_panel(
        field_name="close",
        bars_by_symbol={"BTCUSDT-PERP": bars_btc, "ETHUSDT-PERP": bars_eth},
        min_history=10,
    )
    assert panel is not None
    assert panel.columns == ["ts", "BTCUSDT-PERP", "ETHUSDT-PERP"]
    assert panel.height == 10
    # Verify chronological order — first close should be base_close (oldest).
    btc_first = panel["BTCUSDT-PERP"][0]
    btc_last = panel["BTCUSDT-PERP"][-1]
    assert btc_first == 50_000.0
    assert btc_last == 50_009.0  # base + (n-1)


def test_build_wide_panel_returns_none_when_history_short():
    bars_btc = _make_bars_newest_first(n=5)
    bars_eth = _make_bars_newest_first(n=2)  # below min_history
    panel = build_wide_panel(
        field_name="close",
        bars_by_symbol={"BTCUSDT-PERP": bars_btc, "ETHUSDT-PERP": bars_eth},
        min_history=5,
    )
    assert panel is None


def test_build_wide_panel_returns_none_when_one_symbol_has_no_bars():
    panel = build_wide_panel(
        field_name="close",
        bars_by_symbol={
            "BTCUSDT-PERP": _make_bars_newest_first(n=5),
            "ETHUSDT-PERP": [],
        },
        min_history=1,
    )
    assert panel is None


def test_build_wide_panel_high_field_uses_high_attr():
    bars = _make_bars_newest_first(n=3, base_close=100.0)
    panel = build_wide_panel(
        field_name="high",
        bars_by_symbol={"BTC": bars},
        min_history=3,
    )
    assert panel is not None
    # base_close + 0 + 0.5 = 100.5 (oldest), base_close + 2 + 0.5 = 102.5
    assert panel["BTC"][0] == 100.5
    assert panel["BTC"][-1] == 102.5


def test_build_wide_panel_rejects_non_ohlcv_field():
    bars = _make_bars_newest_first(n=3)
    with pytest.raises(KeyError, match="not an OHLCV field"):
        build_wide_panel(
            field_name="funding_rate",
            bars_by_symbol={"BTC": bars},
            min_history=3,
        )


# ---------------------------------------------------------------------------
# 3. compute_latest_factor_panel
# ---------------------------------------------------------------------------


def test_compute_latest_factor_panel_invokes_kernel_with_kw_panels():
    bars_btc = _make_bars_newest_first(n=10, base_close=100.0)
    bars_eth = _make_bars_newest_first(n=10, base_close=200.0)

    captured: dict = {}

    def _kernel(close, params=None):
        captured["close"] = close
        captured["params"] = params
        return close

    spec = FactorSpec(
        name="ret_N",
        category="动量",
        lookback=5,
        input_specs=(InputSpec(field_name="close"),),
        params={"lookback": 5},
    )

    result = compute_latest_factor_panel(
        factor_kernel=_kernel,
        factor_spec=spec,
        bars_by_symbol={"BTCUSDT-PERP": bars_btc, "ETHUSDT-PERP": bars_eth},
        min_history=10,
    )
    assert isinstance(result, pl.DataFrame)
    assert captured["close"].columns == ["ts", "BTCUSDT-PERP", "ETHUSDT-PERP"]
    assert captured["params"] == {"lookback": 5}


def test_compute_latest_factor_panel_merges_extra_params_over_defaults():
    bars = {"BTC": _make_bars_newest_first(n=10)}
    captured: dict = {}

    def _kernel(close, params=None):
        captured["params"] = params
        return close

    spec = FactorSpec(
        name="x",
        category="y",
        lookback=5,
        input_specs=(InputSpec(field_name="close"),),
        params={"lookback": 5, "extra": 1},
    )
    compute_latest_factor_panel(
        factor_kernel=_kernel,
        factor_spec=spec,
        bars_by_symbol=bars,
        min_history=10,
        extra_kernel_params={"lookback": 10},
    )
    # Extra wins over defaults; unrelated keys preserved.
    assert captured["params"] == {"lookback": 10, "extra": 1}


def test_compute_latest_factor_panel_multi_field_kernel():
    """vwap_dev-style kernel needs (high, low, close, volume) — all pulled."""
    bars = _make_bars_newest_first(n=10, base_close=100.0)
    captured: dict = {}

    def _kernel(high, low, close, volume, params=None):
        captured["fields"] = {
            "high": high.columns,
            "low": low.columns,
            "close": close.columns,
            "volume": volume.columns,
        }
        return close

    spec = FactorSpec(
        name="vwap_dev",
        category="成交量",
        lookback=20,
        input_specs=(
            InputSpec(field_name="high"),
            InputSpec(field_name="low"),
            InputSpec(field_name="close"),
            InputSpec(field_name="volume"),
        ),
    )
    compute_latest_factor_panel(
        factor_kernel=_kernel,
        factor_spec=spec,
        bars_by_symbol={"BTC": bars},
        min_history=10,
    )
    # All four panels were constructed.
    for fld in ("high", "low", "close", "volume"):
        assert "BTC" in captured["fields"][fld]


def test_compute_latest_factor_panel_returns_none_on_short_history():
    bars = {"BTC": _make_bars_newest_first(n=3)}

    def _kernel(close, params=None):
        return close

    spec = FactorSpec(
        name="x",
        category="y",
        lookback=20,
        input_specs=(InputSpec(field_name="close"),),
    )
    result = compute_latest_factor_panel(
        factor_kernel=_kernel,
        factor_spec=spec,
        bars_by_symbol=bars,
        min_history=20,
    )
    assert result is None


def test_compute_latest_factor_panel_rejects_non_ohlcv_input():
    bars = {"BTC": _make_bars_newest_first(n=10)}

    def _kernel(funding_rate, params=None):
        return funding_rate

    spec = FactorSpec(
        name="funding_rate_level",
        category="资金费率",
        lookback=1,
        input_specs=(InputSpec(field_name="funding_rate"),),
    )
    with pytest.raises(KeyError, match="not OHLCV"):
        compute_latest_factor_panel(
            factor_kernel=_kernel,
            factor_spec=spec,
            bars_by_symbol=bars,
            min_history=1,
        )


def test_compute_latest_factor_panel_runs_real_ret_N():
    """Smoke-test against the real ``ret_N`` builtin kernel."""
    from tinohelm.factor.builtins.momentum import ret_N

    bars = {
        "BTC": _make_bars_newest_first(n=20, base_close=100.0),
        "ETH": _make_bars_newest_first(n=20, base_close=2000.0),
    }
    spec = ret_N.__factor_spec__
    panel = compute_latest_factor_panel(
        factor_kernel=ret_N,
        factor_spec=spec,
        bars_by_symbol=bars,
        min_history=20,
        extra_kernel_params={"lookback": 5},
    )
    assert panel is not None
    assert panel.columns == ["ts", "BTC", "ETH"]
    # First 5 rows are null (pct_change(5)), subsequent rows finite.
    btc_last = panel["BTC"][-1]
    assert btc_last is not None
    # Closes were 100..119; pct_change(5) of last = (119-114)/114 ≈ 0.0438...
    assert btc_last == pytest.approx((119.0 - 114.0) / 114.0, rel=1e-9)
