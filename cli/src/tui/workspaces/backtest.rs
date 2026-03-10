//! F2 — Backtest workspace: master-detail split view.

use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::Style,
    symbols,
    text::{Line, Span},
    widgets::{Axis, Cell, Chart, Dataset, GraphType, Paragraph, Row, Table},
    Frame,
};

use crate::tui::app::{App, PanelFocus};
use crate::tui::theme;
use crate::tui::widgets::{self, colored_val, divider_line, header_cell, kv_line, section_title, stat_pair, stat_pair_neutral, titled_block};

pub fn render(f: &mut Frame, area: Rect, app: &App) {
    let cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(45), Constraint::Percentage(55)])
        .split(area);

    render_list(f, cols[0], app);
    render_detail(f, cols[1], app);
}

fn render_list(f: &mut Frame, area: Rect, app: &App) {
    let is_focused = app.panel_focus == PanelFocus::Left;
    let title = if app.backtest_loading {
        format!(" BACKTEST RUNS {} ", widgets::spinner(app.frame_count))
    } else {
        " BACKTEST RUNS ".to_string()
    };

    let header = Row::new(vec![
        header_cell("ID"),
        header_cell("Strategy"),
        header_cell("Symbol"),
        header_cell("Intv"),
        header_cell("Status"),
    ])
    .height(2);

    let rows: Vec<Row> = app
        .backtests
        .iter()
        .enumerate()
        .map(|(i, bt)| {
            let is_selected = i == app.backtest_selected;
            let row_style = if is_selected {
                theme::style_selected()
            } else {
                theme::style_data()
            };

            let effective_status = if bt.status == "running" || bt.progress_pct.is_some() {
                "running"
            } else {
                bt.status.as_str()
            };
            let status_color = theme::status_color(effective_status);

            let status_short = if bt.status == "completed" {
                "\u{2713}"
            } else if bt.status == "failed" {
                "\u{2717}"
            } else if bt.status == "running" || bt.progress_pct.is_some() {
                let pct_str = bt.progress_pct
                    .map(|p| format!("{}%", p))
                    .unwrap_or_else(|| "run".to_string());
                return Row::new(vec![
                    Cell::from(bt.run_id.get(..6).unwrap_or(&bt.run_id).to_string())
                        .style(Style::default().fg(theme::FG_HINT)),
                    Cell::from(bt.strategy_name.as_deref().unwrap_or("-").to_string()),
                    Cell::from(bt.symbol.clone()),
                    Cell::from(bt.interval.clone()),
                    Cell::from(Span::styled(pct_str, Style::default().fg(status_color))),
                ])
                .style(row_style);
            } else if bt.status == "queued" {
                "\u{2026}"
            } else {
                "?"
            };

            let status_display = status_short.to_string();

            Row::new(vec![
                Cell::from(bt.run_id.get(..6).unwrap_or(&bt.run_id).to_string())
                    .style(Style::default().fg(theme::FG_HINT)),
                Cell::from(bt.strategy_name.as_deref().unwrap_or("-").to_string()),
                Cell::from(bt.symbol.clone()),
                Cell::from(bt.interval.clone()),
                Cell::from(Span::styled(status_display, Style::default().fg(status_color))),
            ])
            .style(row_style)
        })
        .collect();

    let sym_w = app
        .backtests
        .iter()
        .map(|bt| bt.symbol.len() as u16)
        .max()
        .unwrap_or(6)
        .max(6); // at least "Symbol" header width

    let widths = [
        Constraint::Length(8),
        Constraint::Min(10),
        Constraint::Length(sym_w),
        Constraint::Length(5),
        Constraint::Length(10),
    ];

    let table = Table::new(rows, widths)
        .header(header)
        .block(titled_block(&title, is_focused));

    f.render_widget(table, area);
}

fn render_detail(f: &mut Frame, area: Rect, app: &App) {
    let is_focused = app.panel_focus == PanelFocus::Right;

    let bt = match app.backtests.get(app.backtest_selected) {
        Some(bt) => bt,
        None => {
            let p = Paragraph::new(Span::styled(
                "  No backtest selected",
                theme::style_dim(),
            ))
            .block(titled_block(" DETAIL ", is_focused));
            f.render_widget(p, area);
            return;
        }
    };

    let has_equity = !app.detail_equity.is_empty();
    let constraints = if has_equity {
        vec![Constraint::Min(10), Constraint::Length(6)]
    } else {
        vec![Constraint::Min(10)]
    };

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints(constraints)
        .split(area);

    let id_short = bt.run_id.get(..8).unwrap_or(&bt.run_id);
    let title = format!(" DETAIL: #{} ", id_short);
    let detail_effective_status = if bt.status == "running" || bt.progress_pct.is_some() {
        "running"
    } else {
        bt.status.as_str()
    };
    let status_color = theme::status_color(detail_effective_status);

    // Get statistics from detail_result (full) or result_summary (partial)
    let stats_source = app
        .detail_result
        .as_ref()
        .and_then(|r| r.get("statistics"))
        .or(bt.result_summary.as_ref());

    let mut lines = Vec::new();

    // ── Basic info ──
    lines.push(Line::from(""));
    lines.push(kv_line("  Strategy", bt.strategy_name.as_deref().unwrap_or("-")));
    lines.push(kv_line("  Symbol  ", &bt.symbol));
    lines.push(kv_line(
        "  Period  ",
        &format!("{} \u{2192} {}", bt.start_date, bt.end_date),
    ));
    let status_display = if let Some(pct) = bt.progress_pct {
        format!("running ({}%)", pct)
    } else {
        bt.status.clone()
    };
    lines.push(Line::from(vec![
        Span::styled("  Status  ".to_string(), Style::default().fg(theme::FG_AMBER)),
        Span::raw(" "),
        Span::styled(status_display, Style::default().fg(status_color)),
    ]));

    if let Some(stats) = stats_source.and_then(|s| s.as_object()) {
        // Helper closures
        let get_f = |key: &str| -> Option<f64> {
            stats.get(key).and_then(|v| v.as_f64())
        };
        let get_s = |key: &str| -> Option<String> {
            stats
                .get(key)
                .and_then(|v| {
                    if v.is_string() {
                        Some(v.as_str().unwrap().to_string())
                    } else if v.is_null() {
                        None
                    } else {
                        Some(v.to_string())
                    }
                })
        };

        // ── Hero PnL ──
        lines.push(divider_line());
        if let Some(pnl) = get_f("total_pnl") {
            let ret = get_f("total_return_pct");
            let mut spans = vec![
                Span::styled("  PnL     ".to_string(), Style::default().fg(theme::FG_AMBER)),
                Span::raw(" "),
                colored_val(pnl, ""),
            ];
            if let Some(r) = ret {
                spans.push(Span::raw("  ("));
                spans.push(colored_val(r, "%"));
                spans.push(Span::raw(")"));
            }
            lines.push(Line::from(spans));
        }
        if let Some(bal) = get_s("final_balance") {
            lines.push(kv_line("  Balance ", &bal));
        }

        // ── Risk Metrics ──
        lines.push(divider_line());
        lines.push(section_title("  RISK METRICS"));
        lines.push(stat_pair(
            "Sharpe",
            get_f("sharpe_ratio"),
            "Sortino",
            get_f("sortino_ratio"),
        ));
        lines.push(stat_pair(
            "MaxDD",
            get_f("max_drawdown"),
            "Calmar",
            get_f("calmar_ratio"),
        ));
        lines.push(stat_pair(
            "Volat.",
            get_f("returns_volatility"),
            "CAGR",
            get_f("annual_return"),
        ));
        // Diversification ratio (from portfolio_analytics, only for multi-instrument)
        if let Some(pa) = app
            .detail_result
            .as_ref()
            .and_then(|r| r.get("portfolio_analytics"))
            .and_then(|p| p.as_object())
        {
            let div_r = pa.get("diversification_ratio").and_then(|v| v.as_f64());
            let div_b = pa
                .get("diversification_benefit_pct")
                .and_then(|v| v.as_f64());
            if div_r.is_some() || div_b.is_some() {
                lines.push(stat_pair(
                    "DivRat",
                    div_r,
                    "DivBen",
                    div_b,
                ));
            }
        }

        // ── Trade Statistics ──
        lines.push(divider_line());
        lines.push(section_title("  TRADE STATISTICS"));

        let total_trades = get_f("total_trades").unwrap_or(0.0) as u32;
        let winning = get_f("winning_trades").unwrap_or(0.0) as u32;
        let losing = get_f("losing_trades").unwrap_or(0.0) as u32;
        let win_rate = get_f("win_rate");

        // Win/Loss visual bar
        if total_trades > 0 {
            let wr = win_rate.unwrap_or(0.0);
            let bar_w = 20_usize;
            let w_fill = ((bar_w as f64) * wr).round() as usize;
            let l_fill = bar_w.saturating_sub(w_fill);
            let wr_str = format!("{:.1}%", wr * 100.0);
            lines.push(Line::from(vec![
                Span::raw("  "),
                Span::styled(
                    "\u{2588}".repeat(w_fill),
                    Style::default().fg(theme::FG_POSITIVE),
                ),
                Span::styled(
                    "\u{2588}".repeat(l_fill),
                    Style::default().fg(theme::FG_NEGATIVE),
                ),
                Span::styled(
                    format!(" {}T  {}W {}L  WR: {}", total_trades, winning, losing, wr_str),
                    Style::default().fg(theme::FG_SECONDARY),
                ),
            ]));
        }

        lines.push(stat_pair(
            "PF",
            get_f("profit_factor"),
            "Expect.",
            get_f("expectancy"),
        ));
        lines.push(stat_pair(
            "Lg Win",
            get_f("largest_win"),
            "Lg Loss",
            get_f("largest_loss").map(|v| -v.abs()),
        ));
        lines.push(stat_pair(
            "Avg W",
            get_f("avg_win"),
            "Avg L",
            get_f("avg_loss").map(|v| -v.abs()),
        ));

        let w_streak = get_f("winning_streak").unwrap_or(0.0) as u32;
        let l_streak = get_f("losing_streak").unwrap_or(0.0) as u32;
        lines.push(stat_pair(
            "W/L Rat",
            get_f("avg_win_loss_ratio"),
            "Streaks",
            None, // rendered manually below
        ));
        // Override the last line's second value with streak text
        lines.pop();
        lines.push(Line::from(vec![
            Span::styled("  W/L Rat".to_string(), Style::default().fg(theme::FG_AMBER)),
            Span::raw(" "),
            match get_f("avg_win_loss_ratio") {
                Some(v) => colored_val(v, ""),
                None => Span::styled("-".to_string(), theme::style_dim()),
            },
            Span::raw("    "),
            Span::styled("Streaks".to_string(), Style::default().fg(theme::FG_AMBER)),
            Span::raw(" "),
            Span::styled(
                format!("{}W/{}L", w_streak, l_streak),
                Style::default().fg(theme::FG_PRIMARY),
            ),
        ]));

        // ── Profit/Loss Breakdown ──
        lines.push(divider_line());
        lines.push(section_title("  PROFIT / LOSS"));
        lines.push(stat_pair(
            "Gross+",
            get_f("gross_profit"),
            "Gross-",
            get_f("gross_loss").map(|v| -v.abs()),
        ));
        lines.push(stat_pair_neutral(
            "Fees",
            get_f("total_fees"),
            "Orders",
            get_f("total_orders"),
        ));
        lines.push(stat_pair_neutral(
            "Filled",
            get_f("filled_orders"),
            "Open",
            get_f("open_positions"),
        ));

        // ── Position & Holding ──
        lines.push(divider_line());
        lines.push(section_title("  POSITION & HOLDING"));
        let long_pct = get_f("long_pct").map(|v| v * 100.0);
        let short_pct = get_f("short_pct").map(|v| v * 100.0);
        if let (Some(lp), Some(sp)) = (long_pct, short_pct) {
            let bar_w = 16_usize;
            let l_fill = ((bar_w as f64) * lp / 100.0).round() as usize;
            let s_fill = bar_w.saturating_sub(l_fill);
            lines.push(Line::from(vec![
                Span::raw("  "),
                Span::styled(
                    "\u{2588}".repeat(l_fill),
                    Style::default().fg(theme::FG_HINT),
                ),
                Span::styled(
                    "\u{2588}".repeat(s_fill),
                    Style::default().fg(theme::FG_QUEUED),
                ),
                Span::styled(
                    format!(" Long {:.0}% / Short {:.0}%", lp, sp),
                    Style::default().fg(theme::FG_SECONDARY),
                ),
            ]));
        }
        if let Some(ht) = get_s("avg_holding_time") {
            lines.push(kv_line("  Avg Hold", &ht));
        }
        if let Some(wht) = get_s("avg_winning_holding_time") {
            lines.push(kv_line("  Win Hold", &wht));
        }
        if let Some(lht) = get_s("avg_losing_holding_time") {
            lines.push(kv_line("  Los Hold", &lht));
        }

        // ── Instrument Breakdown (from detail_result) ──
        if let Some(per_inst) = app
            .detail_result
            .as_ref()
            .and_then(|r| r.get("per_instrument"))
            .and_then(|p| p.as_object())
        {
            if per_inst.len() > 1 {
                lines.push(divider_line());
                lines.push(section_title("  INSTRUMENT BREAKDOWN"));

                let mut instruments: Vec<(&String, &serde_json::Value)> =
                    per_inst.iter().collect();
                instruments.sort_by(|a, b| {
                    let pa = a
                        .1
                        .get("total_pnl")
                        .and_then(|v| v.as_f64())
                        .unwrap_or(0.0);
                    let pb = b
                        .1
                        .get("total_pnl")
                        .and_then(|v| v.as_f64())
                        .unwrap_or(0.0);
                    pb.partial_cmp(&pa).unwrap_or(std::cmp::Ordering::Equal)
                });

                let max_abs = instruments
                    .iter()
                    .map(|(_, v)| {
                        v.get("total_pnl")
                            .and_then(|p| p.as_f64())
                            .unwrap_or(0.0)
                            .abs()
                    })
                    .fold(0.0_f64, f64::max)
                    .max(1.0);

                for (name, data) in &instruments {
                    let pnl = data
                        .get("total_pnl")
                        .and_then(|v| v.as_f64())
                        .unwrap_or(0.0);
                    let trades = data
                        .get("total_trades")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(0);
                    let wr = data
                        .get("win_rate")
                        .and_then(|v| v.as_f64())
                        .unwrap_or(0.0);
                    let short = name.trim_end_matches(".BINANCE");
                    let sr = data
                        .get("sharpe_ratio")
                        .and_then(|v| v.as_f64());
                    let mdd = data
                        .get("max_drawdown")
                        .and_then(|v| v.as_f64());

                    let bar_max = 8_usize;
                    let bar_len = ((pnl.abs() / max_abs) * bar_max as f64).round() as usize;
                    let bar_len =
                        bar_len.min(bar_max).max(if pnl.abs() > 0.01 { 1 } else { 0 });
                    let bar_color = if pnl >= 0.0 {
                        theme::FG_POSITIVE
                    } else {
                        theme::FG_NEGATIVE
                    };
                    let pnl_color = if pnl > 0.001 {
                        theme::FG_POSITIVE
                    } else if pnl < -0.001 {
                        theme::FG_NEGATIVE
                    } else {
                        theme::FG_PRIMARY
                    };

                    let sr_str = match sr {
                        Some(v) => format!(" SR:{:.1}", v),
                        None => String::new(),
                    };
                    let mdd_str = match mdd {
                        Some(v) if v.abs() > 0.0001 => format!(" DD:{:.0}%", v * 100.0),
                        _ => String::new(),
                    };

                    lines.push(Line::from(vec![
                        Span::styled(
                            format!("  {:<13}", short),
                            Style::default().fg(theme::FG_IDENTIFIER),
                        ),
                        Span::styled(
                            format!("{:>+10.2}", pnl),
                            Style::default().fg(pnl_color),
                        ),
                        Span::styled(
                            format!(" {:>3}T {:>3.0}%{}{}", trades, wr * 100.0, sr_str, mdd_str),
                            Style::default().fg(theme::FG_SECONDARY),
                        ),
                        Span::raw(" "),
                        Span::styled(
                            "\u{2588}".repeat(bar_len),
                            Style::default().fg(bar_color),
                        ),
                    ]));
                }
            }
        }

        // ── Correlation Highlights (from detail_result) ──
        if let Some(corr) = app
            .detail_result
            .as_ref()
            .and_then(|r| r.get("instrument_correlation"))
            .and_then(|c| c.as_object())
        {
            // Collect unique pairs (a < b to avoid duplicates)
            let mut pairs: Vec<(String, String, f64)> = Vec::new();
            for (inst_a, corrs) in corr {
                if let Some(map) = corrs.as_object() {
                    for (inst_b, val) in map {
                        if inst_a < inst_b {
                            if let Some(v) = val.as_f64() {
                                pairs.push((
                                    inst_a.trim_end_matches(".BINANCE").to_string(),
                                    inst_b.trim_end_matches(".BINANCE").to_string(),
                                    v,
                                ));
                            }
                        }
                    }
                }
            }
            if !pairs.is_empty() {
                pairs.sort_by(|a, b| {
                    b.2.partial_cmp(&a.2).unwrap_or(std::cmp::Ordering::Equal)
                });
                lines.push(divider_line());
                lines.push(section_title("  CORRELATIONS"));
                // Top 2 (most correlated)
                for (a, b, v) in pairs.iter().take(2) {
                    let color = if *v > 0.7 {
                        theme::FG_NEGATIVE
                    } else if *v > 0.3 {
                        theme::FG_QUEUED
                    } else {
                        theme::FG_SECONDARY
                    };
                    lines.push(Line::from(vec![
                        Span::styled(
                            format!("  \u{25B2} {}/{}", a, b),
                            Style::default().fg(theme::FG_IDENTIFIER),
                        ),
                        Span::styled(
                            format!(" {:>+.3}", v),
                            Style::default().fg(color),
                        ),
                    ]));
                }
                // Bottom 2 (least correlated = best diversification)
                for (a, b, v) in pairs.iter().rev().take(2) {
                    let color = if *v < 0.0 {
                        theme::FG_POSITIVE
                    } else if *v < 0.3 {
                        theme::FG_HINT
                    } else {
                        theme::FG_SECONDARY
                    };
                    lines.push(Line::from(vec![
                        Span::styled(
                            format!("  \u{25BC} {}/{}", a, b),
                            Style::default().fg(theme::FG_IDENTIFIER),
                        ),
                        Span::styled(
                            format!(" {:>+.3}", v),
                            Style::default().fg(color),
                        ),
                    ]));
                }
            }
        }

        // ── Monthly Returns (from detail_result) ──
        if let Some(monthly) = app
            .detail_result
            .as_ref()
            .and_then(|r| r.get("monthly_returns"))
            .and_then(|m| m.as_array())
        {
            if !monthly.is_empty() {
                lines.push(divider_line());
                lines.push(section_title("  MONTHLY RETURNS"));
                for m in monthly.iter().rev().take(12) {
                    let period = m
                        .get("period")
                        .and_then(|p| p.as_str())
                        .unwrap_or("?");
                    let ret = m.get("return_pct").and_then(|r| r.as_f64());
                    if let Some(r) = ret {
                        let bar_len = (r.abs() * 3.0).min(15.0) as usize;
                        let bar_char = if r >= 0.0 { "\u{2588}" } else { "\u{2588}" };
                        let bar_color = if r >= 0.0 {
                            theme::FG_POSITIVE
                        } else {
                            theme::FG_NEGATIVE
                        };
                        lines.push(Line::from(vec![
                            Span::styled(
                                format!("  {:<8}", period),
                                Style::default().fg(theme::FG_SECONDARY),
                            ),
                            colored_val(r, "%"),
                            Span::raw(" "),
                            Span::styled(
                                bar_char.repeat(bar_len),
                                Style::default().fg(bar_color),
                            ),
                        ]));
                    }
                }
            }
        }

        // ── Top Trades (from detail_result) ──
        if let Some(trade_log) = app
            .detail_result
            .as_ref()
            .and_then(|r| r.get("trade_log"))
            .and_then(|t| t.as_array())
        {
            if trade_log.len() > 1 {
                lines.push(divider_line());
                lines.push(section_title("  TOP TRADES"));

                let mut trades_with_pnl: Vec<(&serde_json::Value, f64)> = trade_log
                    .iter()
                    .filter_map(|t| {
                        t.get("realized_pnl")
                            .and_then(|v| v.as_f64())
                            .map(|pnl| (t, pnl))
                    })
                    .collect();
                trades_with_pnl.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());

                // Best 3
                for (t, pnl) in trades_with_pnl.iter().take(3) {
                    let inst = t
                        .get("instrument")
                        .and_then(|i| i.as_str())
                        .unwrap_or("?")
                        .trim_end_matches(".BINANCE");
                    let side = t
                        .get("side")
                        .and_then(|s| s.as_str())
                        .unwrap_or("?");
                    let dur = t
                        .get("duration")
                        .and_then(|d| d.as_str())
                        .unwrap_or("-");
                    lines.push(Line::from(vec![
                        Span::styled(
                            format!("  {:<13} ", inst),
                            Style::default().fg(theme::FG_IDENTIFIER),
                        ),
                        Span::styled(
                            format!("{:<4} ", side),
                            Style::default().fg(theme::FG_SECONDARY),
                        ),
                        colored_val(*pnl, ""),
                        Span::styled(
                            format!("  {}", dur),
                            Style::default().fg(theme::FG_DIM),
                        ),
                    ]));
                }
                // Worst 3
                for (t, pnl) in trades_with_pnl.iter().rev().take(3) {
                    if *pnl < 0.0 {
                        let inst = t
                            .get("instrument")
                            .and_then(|i| i.as_str())
                            .unwrap_or("?")
                            .trim_end_matches(".BINANCE");
                        let side = t
                            .get("side")
                            .and_then(|s| s.as_str())
                            .unwrap_or("?");
                        let dur = t
                            .get("duration")
                            .and_then(|d| d.as_str())
                            .unwrap_or("-");
                        lines.push(Line::from(vec![
                            Span::styled(
                                format!("  {:<13} ", inst),
                                Style::default().fg(theme::FG_IDENTIFIER),
                            ),
                            Span::styled(
                                format!("{:<4} ", side),
                                Style::default().fg(theme::FG_SECONDARY),
                            ),
                            colored_val(*pnl, ""),
                            Span::styled(
                                format!("  {}", dur),
                                Style::default().fg(theme::FG_DIM),
                            ),
                        ]));
                    }
                }
            }
        }

        // ── Drawdown Periods (from detail_result) ──
        if let Some(drawdowns) = app
            .detail_result
            .as_ref()
            .and_then(|r| r.get("drawdown_periods"))
            .and_then(|d| d.as_array())
        {
            if !drawdowns.is_empty() {
                lines.push(divider_line());
                lines.push(section_title("  NOTABLE DRAWDOWNS"));
                for dd in drawdowns.iter().take(5) {
                    let dd_pct = dd.get("max_drawdown_pct").and_then(|v| v.as_f64());
                    let start_d = dd
                        .get("start")
                        .and_then(|s| s.as_str())
                        .unwrap_or("?")
                        .get(..10)
                        .unwrap_or("?");
                    let dur = dd
                        .get("duration_days")
                        .and_then(|d| d.as_u64())
                        .map(|d| format!("{}d", d))
                        .unwrap_or_else(|| "-".to_string());
                    let rec = dd
                        .get("recovery_days")
                        .and_then(|r| r.as_u64())
                        .map(|r| format!("rec {}d", r))
                        .unwrap_or_else(|| "no rec".to_string());
                    if let Some(pct) = dd_pct {
                        lines.push(Line::from(vec![
                            Span::styled(
                                format!("  {} ", start_d),
                                Style::default().fg(theme::FG_SECONDARY),
                            ),
                            colored_val(pct, "%"),
                            Span::styled(
                                format!("  {}  {}", dur, rec),
                                Style::default().fg(theme::FG_DIM),
                            ),
                        ]));
                    }
                }
            }
        }

        // Scroll hint
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            "  \u{2190} focus list  \u{2193}\u{2191} scroll".to_string(),
            Style::default().fg(theme::FG_DIM),
        )));
    } else if bt.status != "completed" {
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            "  Waiting for result data\u{2026}".to_string(),
            theme::style_dim(),
        )));
    }

    let stats_para = Paragraph::new(lines)
        .scroll((app.detail_scroll, 0))
        .block(titled_block(&title, is_focused));
    f.render_widget(stats_para, chunks[0]);

    // ── Equity chart ──
    if has_equity {
        let data: Vec<(f64, f64)> = app
            .detail_equity
            .iter()
            .enumerate()
            .map(|(i, &v)| (i as f64, v as f64))
            .collect();

        let x_max = data.len().saturating_sub(1) as f64;
        let y_min = data.iter().map(|d| d.1).fold(f64::INFINITY, f64::min);
        let y_max = data.iter().map(|d| d.1).fold(f64::NEG_INFINITY, f64::max);
        let y_pad = (y_max - y_min).max(1.0) * 0.05;

        let dataset = Dataset::default()
            .marker(symbols::Marker::Braille)
            .graph_type(GraphType::Line)
            .style(Style::default().fg(theme::FG_RUNNING))
            .data(&data);

        let chart = Chart::new(vec![dataset])
            .block(titled_block(" EQUITY ", is_focused))
            .x_axis(
                Axis::default()
                    .style(Style::default().fg(theme::FG_DIM))
                    .bounds([0.0, x_max]),
            )
            .y_axis(
                Axis::default()
                    .style(Style::default().fg(theme::FG_DIM))
                    .bounds([y_min - y_pad, y_max + y_pad])
                    .labels(vec![
                        Span::styled(format!("{:.0}", y_min), Style::default().fg(theme::FG_SECONDARY)),
                        Span::styled(format!("{:.0}", y_max), Style::default().fg(theme::FG_SECONDARY)),
                    ]),
            );

        f.render_widget(chart, chunks[1]);
    }
}

