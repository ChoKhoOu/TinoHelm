"""Tearsheet enhancement — injects per-instrument breakdown into HTML tearsheet."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def enhance_tearsheet(artifacts_dir: Path, results: dict[str, Any]) -> None:
    """Inject per-instrument performance breakdown into the tearsheet HTML.

    Adds a Plotly horizontal bar chart and a detailed summary table
    showing PnL, return %, win rate, and other per-symbol metrics.
    Only activates for multi-instrument (portfolio) backtests.
    """
    tearsheet_path = artifacts_dir / "tearsheet.html"
    if not tearsheet_path.exists():
        return

    per_instrument = results.get("per_instrument", {})
    if len(per_instrument) <= 1:
        return

    sorted_items = sorted(
        per_instrument.items(),
        key=lambda x: x[1].get("total_pnl", 0),
        reverse=True,
    )

    # Plotly data (reversed for bottom-to-top horizontal bar display)
    symbols = [k.replace(".BINANCE", "") for k, _ in reversed(sorted_items)]
    pnls = [round(v.get("total_pnl", 0), 2) for _, v in reversed(sorted_items)]
    colors = ["#36884B" if p >= 0 else "#8A2425" for p in pnls]
    chart_height = max(300, len(sorted_items) * 35 + 100)

    trace = json.dumps([{
        "type": "bar", "orientation": "h",
        "y": symbols, "x": pnls,
        "marker": {"color": colors},
        "text": [f"{p:+.2f}" for p in pnls],
        "textposition": "outside",
        "hovertemplate": "%{y}: %{x:+.2f} USDT<extra></extra>",
    }])
    chart_layout = json.dumps({
        "title": {"text": "PnL by Instrument (USDT)", "font": {"size": 16}},
        "xaxis": {"title": "PnL", "zeroline": True, "zerolinecolor": "#ddd", "gridcolor": "#eee"},
        "yaxis": {"automargin": True},
        "margin": {"l": 130, "r": 80, "t": 50, "b": 40},
        "template": "plotly_white",
        "height": chart_height,
    })

    # Build HTML table rows (extended with Sharpe, MaxDD, Recovery)
    rows = []
    for inst_id, data in sorted_items:
        short = inst_id.replace(".BINANCE", "")
        pnl = data.get("total_pnl", 0)
        ret = data.get("return_pct", 0)
        trades = data.get("total_trades", 0)
        wr = data.get("win_rate", 0) * 100
        pf = data.get("profit_factor")
        lg_w = data.get("largest_win")
        lg_l = data.get("largest_loss")
        avg = data.get("avg_pnl")
        sr = data.get("sharpe_ratio")
        mdd = data.get("max_drawdown")
        rf = data.get("recovery_factor")

        pc = "pos" if pnl >= 0 else "neg"
        rc = "pos" if ret >= 0 else "neg"
        _d = "\u2013"
        pf_s = f"{pf:.2f}" if pf is not None else _d
        lw_s = f"{lg_w:+.2f}" if lg_w is not None else _d
        ll_s = f"{lg_l:.2f}" if lg_l is not None else _d
        avg_s = f"{avg:+.2f}" if avg is not None else _d
        sr_s = f"{sr:.2f}" if sr is not None else _d
        mdd_s = f"{mdd * 100:.1f}%" if mdd is not None else _d
        rf_s = f"{rf:.1f}" if rf is not None else _d

        rows.append(
            f'<tr><td class="sym">{short}</td>'
            f'<td class="{pc}">{pnl:+.2f}</td>'
            f'<td class="{rc}">{ret:+.2f}%</td>'
            f'<td>{trades}</td><td>{wr:.1f}%</td>'
            f'<td>{pf_s}</td><td>{sr_s}</td>'
            f'<td>{mdd_s}</td><td>{rf_s}</td>'
            f'<td class="pos">{lw_s}</td>'
            f'<td class="neg">{ll_s}</td>'
            f'<td>{avg_s}</td></tr>'
        )

    # ── Additional charts ──

    # Chart 2: Cumulative PnL stacked area
    cum_chart_html = ""
    cum_pnl_data = results.get("instrument_cumulative_pnl", {})
    if cum_pnl_data:
        traces = []
        for inst_id in sorted(cum_pnl_data.keys()):
            curve = cum_pnl_data[inst_id]
            short = inst_id.replace(".BINANCE", "")
            traces.append({
                "type": "scatter", "mode": "lines",
                "name": short,
                "x": [p["date"] for p in curve],
                "y": [p["cum_pnl"] for p in curve],
                "stackgroup": "one",
                "hovertemplate": f"{short}: %{{y:+.2f}} USDT<extra></extra>",
            })
        cum_trace = json.dumps(traces)
        cum_layout = json.dumps({
            "title": {"text": "Cumulative PnL by Instrument", "font": {"size": 16}},
            "xaxis": {"title": "Date"},
            "yaxis": {"title": "Cumulative PnL (USDT)"},
            "template": "plotly_white", "height": 400,
            "margin": {"l": 80, "r": 40, "t": 50, "b": 50},
        })
        cum_chart_html = (
            f'<div id="th-cum-pnl" style="width:100%;height:400px;margin-top:30px"></div>'
            f'<script>Plotly.newPlot("th-cum-pnl",{cum_trace},{cum_layout},{{responsive:true}})</script>'
        )

    # Chart 3: Correlation heatmap
    corr_chart_html = ""
    corr_data = results.get("instrument_correlation", {})
    if corr_data and len(corr_data) >= 2:
        insts = sorted(corr_data.keys())
        short_names = [i.replace(".BINANCE", "") for i in insts]
        z, text_m = [], []
        for inst_i in insts:
            row, trow = [], []
            for inst_j in insts:
                val = 1.0 if inst_i == inst_j else corr_data.get(inst_i, {}).get(inst_j, 0)
                row.append(val)
                trow.append(f"{val:.2f}")
            z.append(row)
            text_m.append(trow)
        ch = max(350, len(insts) * 50 + 150)
        corr_trace = json.dumps([{
            "type": "heatmap", "z": z, "x": short_names, "y": short_names,
            "colorscale": "RdBu", "zmid": 0, "zmin": -1, "zmax": 1,
            "text": text_m, "texttemplate": "%{text}",
            "colorbar": {"title": "Corr"},
            "hovertemplate": "%{x} vs %{y}: %{z:.4f}<extra></extra>",
        }])
        corr_layout = json.dumps({
            "title": {"text": "Return Correlation Matrix", "font": {"size": 16}},
            "template": "plotly_white", "height": ch,
            "margin": {"l": 130, "r": 60, "t": 50, "b": 100},
        })
        corr_chart_html = (
            f'<div id="th-corr" style="width:100%;height:{ch}px;margin-top:30px"></div>'
            f'<script>Plotly.newPlot("th-corr",{corr_trace},{corr_layout},{{responsive:true}})</script>'
        )

    # Chart 4: Monthly PnL heatmap (instrument x month)
    heat_chart_html = ""
    heatmap_data = results.get("monthly_pnl_heatmap", [])
    if heatmap_data:
        h_insts = sorted({d["instrument"] for d in heatmap_data})
        h_months = sorted({d["month"] for d in heatmap_data})
        h_short = [i.replace(".BINANCE", "") for i in h_insts]
        lookup = {(d["instrument"], d["month"]): d["pnl"] for d in heatmap_data}
        z, text_m = [], []
        for inst in h_insts:
            row, trow = [], []
            for month in h_months:
                val = lookup.get((inst, month), 0)
                row.append(val)
                trow.append(f"{val:+.0f}")
            z.append(row)
            text_m.append(trow)
        hh = max(300, len(h_insts) * 40 + 150)
        heat_trace = json.dumps([{
            "type": "heatmap", "z": z, "x": h_months, "y": h_short,
            "colorscale": "RdYlGn", "zmid": 0,
            "text": text_m, "texttemplate": "%{text}",
            "hovertemplate": "%{y} %{x}: %{z:+.2f} USDT<extra></extra>",
        }])
        heat_layout = json.dumps({
            "title": {"text": "Monthly PnL Heatmap (Instrument \u00d7 Month)", "font": {"size": 16}},
            "xaxis": {"title": "Month"},
            "yaxis": {"automargin": True},
            "template": "plotly_white", "height": hh,
            "margin": {"l": 130, "r": 60, "t": 50, "b": 60},
        })
        heat_chart_html = (
            f'<div id="th-monthly-heat" style="width:100%;height:{hh}px;margin-top:30px"></div>'
            f'<script>Plotly.newPlot("th-monthly-heat",{heat_trace},{heat_layout},{{responsive:true}})</script>'
        )

    # Chart 5: PnL Treemap (proportional boxes by contribution)
    treemap_html = ""
    if len(sorted_items) >= 2:
        tm_labels = [k.replace(".BINANCE", "") for k, _ in sorted_items]
        tm_pnls = [round(v.get("total_pnl", 0), 2) for _, v in sorted_items]
        tm_abs = [abs(p) for p in tm_pnls]
        tm_parents = ["Portfolio"] * len(tm_labels)
        tm_text = [f"{p:+.2f}" for p in tm_pnls]
        tm_colors = ["#36884B" if p >= 0 else "#8A2425" for p in tm_pnls]
        treemap_trace = json.dumps([{
            "type": "treemap",
            "labels": tm_labels,
            "parents": tm_parents,
            "values": tm_abs,
            "text": tm_text,
            "texttemplate": "<b>%{label}</b><br>%{text} USDT",
            "marker": {"colors": tm_colors},
            "hovertemplate": "%{label}: %{text} USDT<extra></extra>",
        }])
        treemap_layout = json.dumps({
            "title": {"text": "PnL Contribution Treemap", "font": {"size": 16}},
            "template": "plotly_white", "height": 400,
            "margin": {"l": 10, "r": 10, "t": 50, "b": 10},
        })
        treemap_html = (
            f'<div id="th-treemap" style="width:100%;height:400px;margin-top:30px"></div>'
            f'<script>Plotly.newPlot("th-treemap",{treemap_trace},{treemap_layout},{{responsive:true}})</script>'
        )

    # Portfolio analytics summary
    pa = results.get("portfolio_analytics", {})
    analytics_html = ""
    if pa:
        dr = pa.get("diversification_ratio")
        db = pa.get("diversification_benefit_pct")
        parts = []
        if dr is not None:
            parts.append(f"<b>Diversification Ratio:</b> {dr:.2f}")
        if db is not None:
            parts.append(f"<b>Diversification Benefit:</b> {db:.1f}%")
        if parts:
            analytics_html = (
                '<div style="margin-top:20px;padding:16px;background:#f5f4ed;'
                'border:1px solid #dedbd3;border-radius:12px;font-size:14px;color:#2C2C2A;">'
                + " &nbsp;\u2502&nbsp; ".join(parts)
                + "</div>"
            )

    section = f"""
<!-- TinoHelm: Per-Instrument Breakdown -->
<style>
.th-inst {{ max-width:1200px; margin:40px auto; padding:0 20px;
  font-family:'IBM Plex Sans',-apple-system,BlinkMacSystemFont,sans-serif; }}
.th-inst h2 {{ color:#2C2C2A; border-bottom:3px solid #D97857; padding-bottom:10px; font-size:20px; }}
.th-inst h3 {{ color:#73726C; margin-top:30px; font-size:16px; }}
.th-inst table {{ width:100%; border-collapse:collapse; margin-top:16px; font-size:13px; }}
.th-inst th {{ padding:10px 12px; text-align:left; border-bottom:2px solid #D97857;
  font-weight:600; color:#73726C; font-size:12px; text-transform:uppercase; letter-spacing:.5px; }}
.th-inst td {{ padding:8px 12px; border-bottom:1px solid #dedbd3; }}
.th-inst tbody tr:hover {{ background:#f5f4ed; }}
.th-inst .sym {{ font-weight:600; color:#D97857; }}
.th-inst .pos {{ color:#36884B; font-weight:600; }}
.th-inst .neg {{ color:#8A2425; font-weight:600; }}
</style>
<div class="th-inst">
<h2>Per-Instrument Performance</h2>
{analytics_html}
<div id="th-inst-chart" style="width:100%;height:{chart_height}px"></div>
<script>Plotly.newPlot('th-inst-chart',{trace},{chart_layout},{{responsive:true}})</script>
<table><thead><tr>
<th>Symbol</th><th>PnL</th><th>Return</th><th>Trades</th><th>Win Rate</th>
<th>PF</th><th>Sharpe</th><th>MaxDD</th><th>Recovery</th>
<th>Best</th><th>Worst</th><th>Avg PnL</th>
</tr></thead><tbody>{"".join(rows)}</tbody></table>
{cum_chart_html}
{treemap_html}
{corr_chart_html}
{heat_chart_html}
</div>
"""
    try:
        html = tearsheet_path.read_text(encoding="utf-8")
        html = html.replace("</body>", section + "\n</body>")
        tearsheet_path.write_text(html, encoding="utf-8")
        logger.info(
            "Enhanced tearsheet with per-instrument breakdown (%d instruments, %d charts)",
            len(sorted_items),
            1 + bool(cum_chart_html) + bool(corr_chart_html) + bool(heat_chart_html),
        )
    except Exception:
        logger.warning("Failed to enhance tearsheet", exc_info=True)
