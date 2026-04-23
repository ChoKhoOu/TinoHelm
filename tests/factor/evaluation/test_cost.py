"""Unit tests — ``tinohelm.factor.evaluation.cost.edge_waterfall``.

Locks the cost-waterfall math contract.  The function is tiny (~10 lines
of arithmetic) but exported to the frontend and forms the net-edge
number users see on every factor card; every constant needs to be pinned.

* Schema — exactly 4 keys ``{gross_edge_bps, fee_cost_bps, slippage_bps,
  net_edge_bps}``.
* Formulas (all in bps per day):
    gross     = |IC| × 10000 × turnover_daily
    fee_cost  = fee_rate × 2 × 10000 × turnover_daily        (round-trip)
    slippage  = slippage_bps × turnover_daily
    net       = gross − fee_cost − slippage
* Defaults — ``fee_rate = 0.0004``, ``slippage_bps = 1.0``.
* Rounding — all four values at 2 dp.
* ``abs(ic_mean)`` — sign is stripped.

Pure, deterministic, NT-free, < 10 ms.
"""
from __future__ import annotations

import pytest

from tinohelm.factor.evaluation.cost import edge_waterfall


class TestSchema:
    def test_four_keys(self):
        out = edge_waterfall(0.05, 0.5)
        assert set(out.keys()) == {
            "gross_edge_bps", "fee_cost_bps", "slippage_bps", "net_edge_bps",
        }

    def test_values_are_floats_rounded_to_2dp(self):
        out = edge_waterfall(0.03754321, 0.1234567)
        for v in out.values():
            assert round(v, 2) == v


class TestFormula:
    def test_gross_matches_abs_ic_times_10000_times_turnover(self):
        out = edge_waterfall(ic_mean=0.05, turnover_daily=0.5)
        # 0.05 × 10000 × 0.5 = 250 bps
        assert out["gross_edge_bps"] == 250.0

    def test_fee_uses_round_trip_convention(self):
        # fee_rate = 0.0005, turnover = 1.0
        # fee_bps = 0.0005 × 2 × 10000 × 1.0 = 10 bps
        out = edge_waterfall(ic_mean=0.0, turnover_daily=1.0, fee_rate=0.0005)
        assert out["fee_cost_bps"] == 10.0

    def test_slippage_scales_linearly_with_turnover(self):
        out1 = edge_waterfall(ic_mean=0.0, turnover_daily=0.5, slippage_bps=2.0)
        out2 = edge_waterfall(ic_mean=0.0, turnover_daily=1.0, slippage_bps=2.0)
        assert out2["slippage_bps"] == 2.0  # 2 × 1.0
        assert out1["slippage_bps"] == 1.0  # 2 × 0.5

    def test_net_is_gross_minus_fee_minus_slippage(self):
        out = edge_waterfall(ic_mean=0.1, turnover_daily=0.5,
                             fee_rate=0.0004, slippage_bps=1.0)
        expected_net = round(out["gross_edge_bps"] - out["fee_cost_bps"] - out["slippage_bps"], 2)
        assert out["net_edge_bps"] == expected_net

    def test_zero_turnover_zeros_every_daily_figure(self):
        out = edge_waterfall(ic_mean=0.1, turnover_daily=0.0)
        assert out["gross_edge_bps"] == 0.0
        assert out["fee_cost_bps"] == 0.0
        assert out["slippage_bps"] == 0.0
        assert out["net_edge_bps"] == 0.0


class TestSignHandling:
    def test_negative_ic_same_gross_as_positive(self):
        pos = edge_waterfall(0.05, 0.5)
        neg = edge_waterfall(-0.05, 0.5)
        assert pos["gross_edge_bps"] == neg["gross_edge_bps"]

    def test_negative_ic_same_net_as_positive(self):
        # Since fee/slippage don't depend on ic sign, net should match too.
        pos = edge_waterfall(0.07, 0.3)
        neg = edge_waterfall(-0.07, 0.3)
        assert pos == neg


class TestDefaults:
    def test_default_fee_rate_is_0_0004(self):
        out_default = edge_waterfall(0.05, 0.5)
        out_explicit = edge_waterfall(0.05, 0.5, fee_rate=0.0004)
        assert out_default == out_explicit

    def test_default_slippage_is_1bps(self):
        out_default = edge_waterfall(0.05, 0.5)
        out_explicit = edge_waterfall(0.05, 0.5, slippage_bps=1.0)
        assert out_default == out_explicit


class TestRealism:
    def test_strong_ic_low_turnover_produces_positive_net(self):
        # 0.08 IC, rebalance every 5 days (turnover=0.2) → net should be > 0.
        out = edge_waterfall(ic_mean=0.08, turnover_daily=0.2)
        assert out["net_edge_bps"] > 0

    def test_weak_ic_high_turnover_eats_all_edge(self):
        # IC=0.0005 (=5 bps gross per full-rebalance day) is eaten by
        # 8 bps round-trip fees + 1 bps slippage → net ≈ -4 bps.
        out = edge_waterfall(ic_mean=0.0005, turnover_daily=1.0)
        assert out["net_edge_bps"] < 0
