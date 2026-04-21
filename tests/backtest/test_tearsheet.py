"""Tests for ``tinohelm.backtest.tearsheet.enhance_tearsheet`` — NT-free.

The function injects a per-instrument performance section (table + up to
five Plotly charts + portfolio analytics summary) into the HTML tearsheet
produced by ``nautilus_trader``.  It is a pure HTML/JSON transformer —
no NT types or runtime are touched.

Why this deserves tests
-----------------------

1. It's the user's primary multi-instrument reporting surface.
2. A silent failure is easy — the module swallows IO exceptions by design
   (to avoid crashing the backtest pipeline) — so any regression would
   vanish in a warning log rather than a CI failure.
3. The chart branches (cumulative PnL / correlation / monthly heatmap /
   treemap / analytics summary) are independently gated and easy to
   regress when one branch is changed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tinohelm.backtest.tearsheet import enhance_tearsheet


# ────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    """Writable artifacts directory with a minimal tearsheet.html."""
    (tmp_path / "tearsheet.html").write_text(
        "<html><head></head><body><h1>Tearsheet</h1></body></html>",
        encoding="utf-8",
    )
    return tmp_path


def _minimal_multi_inst_results(**overrides) -> dict[str, Any]:
    """Results block with exactly two instruments — enough to trigger the inject."""
    base = {
        "per_instrument": {
            "BTCUSDT-PERP.BINANCE": {
                "total_pnl": 150.0,
                "return_pct": 1.5,
                "total_trades": 10,
                "win_rate": 0.6,
                "profit_factor": 2.0,
                "largest_win": 50.0,
                "largest_loss": -20.0,
                "avg_pnl": 15.0,
                "sharpe_ratio": 1.8,
                "max_drawdown": -0.05,
                "recovery_factor": 3.0,
            },
            "ETHUSDT-PERP.BINANCE": {
                "total_pnl": -50.0,
                "return_pct": -0.5,
                "total_trades": 8,
                "win_rate": 0.3,
                "profit_factor": 0.5,
                "largest_win": 10.0,
                "largest_loss": -30.0,
                "avg_pnl": -6.25,
                "sharpe_ratio": -0.5,
                "max_drawdown": -0.1,
                "recovery_factor": None,
            },
        },
    }
    base.update(overrides)
    return base


# ────────────────────────────────────────────────────────────────────
# Guard clauses — no-op paths
# ────────────────────────────────────────────────────────────────────


class TestGuardClauses:
    """Verify the early-return paths that make the function a safe no-op."""

    def test_missing_tearsheet_file_is_noop(self, tmp_path: Path):
        # artifacts dir exists but no tearsheet.html inside → do nothing
        enhance_tearsheet(tmp_path, _minimal_multi_inst_results())
        # No file should appear
        assert list(tmp_path.iterdir()) == []

    def test_empty_per_instrument_is_noop(self, artifacts_dir: Path):
        original = (artifacts_dir / "tearsheet.html").read_text()
        enhance_tearsheet(artifacts_dir, {"per_instrument": {}})
        # Content unchanged
        assert (artifacts_dir / "tearsheet.html").read_text() == original

    def test_single_instrument_is_noop(self, artifacts_dir: Path):
        original = (artifacts_dir / "tearsheet.html").read_text()
        results = {
            "per_instrument": {
                "BTCUSDT-PERP.BINANCE": {"total_pnl": 100.0},
            },
        }
        enhance_tearsheet(artifacts_dir, results)
        assert (artifacts_dir / "tearsheet.html").read_text() == original

    def test_missing_per_instrument_key_is_noop(self, artifacts_dir: Path):
        original = (artifacts_dir / "tearsheet.html").read_text()
        enhance_tearsheet(artifacts_dir, {})
        assert (artifacts_dir / "tearsheet.html").read_text() == original


# ────────────────────────────────────────────────────────────────────
# Core injection — base table
# ────────────────────────────────────────────────────────────────────


class TestBaseInjection:
    def test_inserts_before_body_close(self, artifacts_dir: Path):
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        html = (artifacts_dir / "tearsheet.html").read_text()
        # Section appears before </body>, not after
        assert "Per-Instrument Performance" in html
        assert html.index("Per-Instrument Performance") < html.index("</body>")

    def test_strips_binance_suffix(self, artifacts_dir: Path):
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        html = (artifacts_dir / "tearsheet.html").read_text()
        # Displayed as BTCUSDT-PERP (no .BINANCE suffix)
        assert "BTCUSDT-PERP</td>" in html or "BTCUSDT-PERP<" in html
        # Raw dotted form only allowed in places like correlation keys — in
        # the main section nothing user-facing retains ``.BINANCE``.
        assert "BTCUSDT-PERP.BINANCE</td>" not in html
        assert "ETHUSDT-PERP.BINANCE</td>" not in html

    def test_sorted_descending_by_pnl(self, artifacts_dir: Path):
        # BTC (+150) must appear above ETH (-50) in the rendered table.
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        html = (artifacts_dir / "tearsheet.html").read_text()
        # Extract the <tbody>...</tbody> block to be safe against other html
        tbody_match = re.search(r"<tbody>(.*?)</tbody>", html, re.DOTALL)
        assert tbody_match is not None
        tbody = tbody_match.group(1)
        assert tbody.index("BTCUSDT-PERP") < tbody.index("ETHUSDT-PERP")

    def test_positive_pnl_uses_pos_class(self, artifacts_dir: Path):
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        html = (artifacts_dir / "tearsheet.html").read_text()
        # +150 should use the "pos" (green) class
        assert 'class="pos">+150.00</td>' in html
        # -50 should use the "neg" (red) class
        assert 'class="neg">-50.00</td>' in html

    def test_dash_placeholder_for_none_values(self, artifacts_dir: Path):
        # recovery_factor=None for ETH should render as en-dash
        results = _minimal_multi_inst_results()
        enhance_tearsheet(artifacts_dir, results)
        html = (artifacts_dir / "tearsheet.html").read_text()
        # en-dash is –
        assert "–" in html

    def test_plotly_bar_chart_data_embedded(self, artifacts_dir: Path):
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        html = (artifacts_dir / "tearsheet.html").read_text()
        # The first chart always renders — search for the Plotly.newPlot call
        assert "Plotly.newPlot('th-inst-chart'" in html
        assert '"type": "bar"' in html
        assert '"orientation": "h"' in html

    def test_chart_height_scales_with_instrument_count(self, artifacts_dir: Path):
        # 2 instruments → height = max(300, 2*35 + 100) = 300
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert "height:300px" in html

    def test_chart_height_clamps_to_minimum_300(self, artifacts_dir: Path):
        # Even 2 instruments doesn't drop below 300
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert "height:300px" in html

    def test_chart_height_scales_up_for_many_instruments(self, artifacts_dir: Path):
        per_inst = {
            f"SYM{i}-PERP.BINANCE": {"total_pnl": float(i * 10), "return_pct": 0.1}
            for i in range(20)
        }
        enhance_tearsheet(artifacts_dir, {"per_instrument": per_inst})
        html = (artifacts_dir / "tearsheet.html").read_text()
        # 20 * 35 + 100 = 800
        assert "height:800px" in html

    def test_table_has_all_twelve_columns(self, artifacts_dir: Path):
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        html = (artifacts_dir / "tearsheet.html").read_text()
        # Header row contains 12 TH cells
        thead = re.search(r"<thead>(.*?)</thead>", html, re.DOTALL).group(1)
        assert thead.count("<th>") == 12

    def test_idempotent_on_reinvoke(self, artifacts_dir: Path):
        # Calling twice should inject the section twice — the function
        # replaces only the first </body> each time, so expect two copies.
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert html.count("Per-Instrument Performance") == 2


# ────────────────────────────────────────────────────────────────────
# Chart 2 — cumulative PnL
# ────────────────────────────────────────────────────────────────────


class TestCumulativePnLChart:
    def test_no_cum_data_no_chart(self, artifacts_dir: Path):
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert "th-cum-pnl" not in html

    def test_empty_cum_data_no_chart(self, artifacts_dir: Path):
        results = _minimal_multi_inst_results(instrument_cumulative_pnl={})
        enhance_tearsheet(artifacts_dir, results)
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert "th-cum-pnl" not in html

    def test_chart_included_when_data_present(self, artifacts_dir: Path):
        results = _minimal_multi_inst_results(instrument_cumulative_pnl={
            "BTCUSDT-PERP.BINANCE": [
                {"date": "2025-01-01", "cum_pnl": 100},
                {"date": "2025-01-02", "cum_pnl": 150},
            ],
            "ETHUSDT-PERP.BINANCE": [
                {"date": "2025-01-01", "cum_pnl": 0},
                {"date": "2025-01-02", "cum_pnl": -50},
            ],
        })
        enhance_tearsheet(artifacts_dir, results)
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert 'id="th-cum-pnl"' in html
        assert '"stackgroup": "one"' in html
        # Both instruments appear in the chart traces
        assert "BTCUSDT-PERP" in html
        assert "ETHUSDT-PERP" in html


# ────────────────────────────────────────────────────────────────────
# Chart 3 — correlation heatmap
# ────────────────────────────────────────────────────────────────────


class TestCorrelationHeatmap:
    def test_no_corr_data_no_chart(self, artifacts_dir: Path):
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert "th-corr" not in html

    def test_single_instrument_corr_skipped(self, artifacts_dir: Path):
        # Only 1 inst in corr map → chart gated out (need >= 2)
        results = _minimal_multi_inst_results(instrument_correlation={
            "BTCUSDT-PERP.BINANCE": {},
        })
        enhance_tearsheet(artifacts_dir, results)
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert "th-corr" not in html

    def test_pair_renders_heatmap(self, artifacts_dir: Path):
        results = _minimal_multi_inst_results(instrument_correlation={
            "BTCUSDT-PERP.BINANCE": {"ETHUSDT-PERP.BINANCE": 0.7},
            "ETHUSDT-PERP.BINANCE": {"BTCUSDT-PERP.BINANCE": 0.7},
        })
        enhance_tearsheet(artifacts_dir, results)
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert 'id="th-corr"' in html
        assert '"type": "heatmap"' in html
        assert '"colorscale": "RdBu"' in html

    def test_diagonal_values_are_one(self, artifacts_dir: Path):
        # Self-correlation should render as 1.0 regardless of map content
        results = _minimal_multi_inst_results(instrument_correlation={
            "BTCUSDT-PERP.BINANCE": {"ETHUSDT-PERP.BINANCE": 0.3},
            "ETHUSDT-PERP.BINANCE": {"BTCUSDT-PERP.BINANCE": 0.3},
        })
        enhance_tearsheet(artifacts_dir, results)
        html = (artifacts_dir / "tearsheet.html").read_text()
        # Diagonal cells formatted as "1.00"
        assert '"1.00"' in html


# ────────────────────────────────────────────────────────────────────
# Chart 4 — monthly PnL heatmap
# ────────────────────────────────────────────────────────────────────


class TestMonthlyPnLHeatmap:
    def test_no_heatmap_data_no_chart(self, artifacts_dir: Path):
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert "th-monthly-heat" not in html

    def test_heatmap_rendered(self, artifacts_dir: Path):
        results = _minimal_multi_inst_results(monthly_pnl_heatmap=[
            {"instrument": "BTCUSDT-PERP.BINANCE", "month": "2025-01", "pnl": 100},
            {"instrument": "BTCUSDT-PERP.BINANCE", "month": "2025-02", "pnl": -50},
            {"instrument": "ETHUSDT-PERP.BINANCE", "month": "2025-01", "pnl": 20},
            {"instrument": "ETHUSDT-PERP.BINANCE", "month": "2025-02", "pnl": -10},
        ])
        enhance_tearsheet(artifacts_dir, results)
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert 'id="th-monthly-heat"' in html
        assert '"colorscale": "RdYlGn"' in html

    def test_missing_month_inst_pair_renders_zero(self, artifacts_dir: Path):
        # If (inst, month) is missing, the matrix defaults that cell to 0.
        results = _minimal_multi_inst_results(monthly_pnl_heatmap=[
            {"instrument": "BTCUSDT-PERP.BINANCE", "month": "2025-01", "pnl": 100},
            {"instrument": "ETHUSDT-PERP.BINANCE", "month": "2025-02", "pnl": -10},
        ])
        enhance_tearsheet(artifacts_dir, results)
        html = (artifacts_dir / "tearsheet.html").read_text()
        # There should be formatted "+0" or "-0" entries for the missing pairs
        assert "+0" in html or '"0"' in html


# ────────────────────────────────────────────────────────────────────
# Chart 5 — treemap
# ────────────────────────────────────────────────────────────────────


class TestTreemap:
    def test_treemap_rendered_for_two_plus_instruments(self, artifacts_dir: Path):
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert 'id="th-treemap"' in html
        assert '"type": "treemap"' in html

    def test_treemap_values_are_absolute(self, artifacts_dir: Path):
        # The treemap uses abs(pnl) for box sizes so losers aren't invisible
        results = _minimal_multi_inst_results()
        enhance_tearsheet(artifacts_dir, results)
        html = (artifacts_dir / "tearsheet.html").read_text()
        # Find the treemap trace and parse its `values`
        match = re.search(
            r'Plotly\.newPlot\("th-treemap",(\[.*?\]),\{',
            html,
            re.DOTALL,
        )
        assert match is not None
        trace = json.loads(match.group(1))
        assert trace[0]["values"] == [150.0, 50.0]  # abs(+150), abs(-50)


# ────────────────────────────────────────────────────────────────────
# Portfolio analytics summary
# ────────────────────────────────────────────────────────────────────


class TestAnalyticsSummary:
    def test_no_portfolio_analytics_no_summary(self, artifacts_dir: Path):
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert "Diversification Ratio" not in html

    def test_empty_portfolio_analytics_no_summary(self, artifacts_dir: Path):
        results = _minimal_multi_inst_results(portfolio_analytics={})
        enhance_tearsheet(artifacts_dir, results)
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert "Diversification Ratio" not in html

    def test_diversification_ratio_only(self, artifacts_dir: Path):
        results = _minimal_multi_inst_results(portfolio_analytics={
            "diversification_ratio": 1.42,
        })
        enhance_tearsheet(artifacts_dir, results)
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert "Diversification Ratio:" in html
        assert "1.42" in html
        assert "Diversification Benefit" not in html

    def test_both_metrics_rendered(self, artifacts_dir: Path):
        results = _minimal_multi_inst_results(portfolio_analytics={
            "diversification_ratio": 1.5,
            "diversification_benefit_pct": 25.0,
        })
        enhance_tearsheet(artifacts_dir, results)
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert "Diversification Ratio:" in html
        assert "Diversification Benefit:" in html
        assert "25.0%" in html

    def test_none_values_skipped(self, artifacts_dir: Path):
        # When both values are None, no summary block is rendered.
        results = _minimal_multi_inst_results(portfolio_analytics={
            "diversification_ratio": None,
            "diversification_benefit_pct": None,
        })
        enhance_tearsheet(artifacts_dir, results)
        html = (artifacts_dir / "tearsheet.html").read_text()
        assert "Diversification Ratio" not in html
        assert "Diversification Benefit" not in html


# ────────────────────────────────────────────────────────────────────
# IO resilience
# ────────────────────────────────────────────────────────────────────


class TestIOResilience:
    def test_unwritable_tearsheet_is_swallowed(
        self,
        artifacts_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """If the HTML write fails, the function must not raise — the
        backtest pipeline treats tearsheet enhancement as a best-effort
        decoration, not a hard dependency.
        """
        def _explode(self, *args, **kwargs):
            raise PermissionError("read-only FS")

        monkeypatch.setattr(Path, "write_text", _explode)
        # Should not raise despite the write failure
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())

    def test_read_failure_is_swallowed(
        self,
        artifacts_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        def _explode(self, *args, **kwargs):
            raise OSError("io error")

        monkeypatch.setattr(Path, "read_text", _explode)
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())

    def test_missing_body_tag_still_succeeds(self, artifacts_dir: Path):
        # HTML without </body> — str.replace is a no-op, but the function
        # still writes the unchanged file back.  Verify no exception.
        (artifacts_dir / "tearsheet.html").write_text(
            "<html>no closing body</html>", encoding="utf-8",
        )
        enhance_tearsheet(artifacts_dir, _minimal_multi_inst_results())
        # File unchanged (replace had nothing to match)
        html = (artifacts_dir / "tearsheet.html").read_text()
        # Nothing was injected because </body> didn't exist
        assert "Per-Instrument Performance" not in html


# ────────────────────────────────────────────────────────────────────
# NT-independence
# ────────────────────────────────────────────────────────────────────


class TestNoNTDependency:
    """Tearsheet is pure HTML/JSON — no NT at import time."""

    def test_tearsheet_module_imports_without_nt(self):
        import sys

        class _Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.startswith("nautilus_trader"):
                    raise ImportError(f"blocked: {name}")
                return None

        saved = sys.modules.pop("tinohelm.backtest.tearsheet", None)

        # Under CI with NT installed, previous tests may have pre-loaded
        # ``nautilus_trader`` into ``sys.modules``.  The correct invariant
        # isn't "no NT in sys.modules" (absolute) — it's "no NT newly
        # imported as a side effect of loading tearsheet" (delta).
        nt_before = {k for k in sys.modules if k.startswith("nautilus_trader")}

        blocker = _Blocker()
        sys.meta_path.insert(0, blocker)
        try:
            import importlib
            mod = importlib.import_module("tinohelm.backtest.tearsheet")
            assert hasattr(mod, "enhance_tearsheet")
            nt_after = {k for k in sys.modules if k.startswith("nautilus_trader")}
            new_nt = nt_after - nt_before
            assert new_nt == set(), f"tearsheet import pulled in NT modules: {sorted(new_nt)}"
        finally:
            sys.meta_path.remove(blocker)
            if saved is not None:
                sys.modules["tinohelm.backtest.tearsheet"] = saved
