"""Tests for `tinohelm.research.cost` — edge waterfall computation."""
from __future__ import annotations

import pytest

from tinohelm.research.cost import edge_waterfall


class TestEdgeWaterfall:
    def test_returns_four_keys(self):
        out = edge_waterfall(0.05, 1.0)
        assert set(out.keys()) == {"gross_edge_bps", "fee_cost_bps", "slippage_bps", "net_edge_bps"}

    def test_uses_abs_ic_for_gross(self):
        # Same magnitude IC, opposite sign — gross edge should be identical.
        pos = edge_waterfall(0.05, 1.0)
        neg = edge_waterfall(-0.05, 1.0)
        assert pos["gross_edge_bps"] == neg["gross_edge_bps"]

    def test_default_fee_rate_and_slippage(self):
        # ic=0.01, turnover=1: gross = 0.01*10000*1 = 100 bps
        # default fee_rate=0.0004 → fee_bps = 0.0004*2*10000 = 8 bps × 1 turnover = 8 bps
        # default slippage_bps=1.0 → 1 bps × 1 turnover = 1 bps
        # net = 100 - 8 - 1 = 91 bps
        out = edge_waterfall(0.01, 1.0)
        assert out["gross_edge_bps"] == pytest.approx(100.0)
        assert out["fee_cost_bps"] == pytest.approx(8.0)
        assert out["slippage_bps"] == pytest.approx(1.0)
        assert out["net_edge_bps"] == pytest.approx(91.0)

    def test_zero_turnover_yields_zero_everything(self):
        out = edge_waterfall(0.05, 0.0)
        assert out["gross_edge_bps"] == 0
        assert out["fee_cost_bps"] == 0
        assert out["slippage_bps"] == 0
        assert out["net_edge_bps"] == 0

    def test_high_fees_can_make_net_negative(self):
        # Tiny IC, huge turnover, high fees → net negative
        out = edge_waterfall(0.001, 5.0, fee_rate=0.001, slippage_bps=2.0)
        # gross = 0.001*10000*5 = 50 bps
        # fee = 0.001*2*10000*5 = 100 bps
        # slip = 2*5 = 10 bps
        # net = 50 - 100 - 10 = -60 bps
        assert out["net_edge_bps"] == pytest.approx(-60.0)
        assert out["net_edge_bps"] < 0

    def test_turnover_scales_all_bps_quantities(self):
        a = edge_waterfall(0.01, 1.0)
        b = edge_waterfall(0.01, 2.0)
        for k in ("gross_edge_bps", "fee_cost_bps", "slippage_bps"):
            # All scale linearly with daily turnover
            assert b[k] == pytest.approx(2 * a[k])

    def test_results_are_rounded_to_two_decimals(self):
        out = edge_waterfall(0.0123456, 1.0)
        # 0.0123456 * 10000 = 123.456 → rounded to 123.46
        assert out["gross_edge_bps"] == pytest.approx(123.46, abs=1e-9)
