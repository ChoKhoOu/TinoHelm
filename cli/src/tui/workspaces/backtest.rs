//! F2 — Backtest workspace: master-detail split view.

use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    prelude::Stylize,
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Cell, Paragraph, Row, Sparkline, Table},
    Frame,
};

use crate::tui::app::{App, PanelFocus};
use crate::tui::theme;

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
    let border_style = if is_focused {
        theme::style_border_focused()
    } else {
        theme::style_border()
    };

    let title = if app.backtest_loading {
        " BACKTEST RUNS (loading\u{2026}) "
    } else {
        " BACKTEST RUNS "
    };

    let header = Row::new(vec![
        Cell::from("ID"),
        Cell::from("Strategy"),
        Cell::from("Symbol"),
        Cell::from("Intv"),
        Cell::from("Status"),
    ])
    .style(theme::style_header());

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

            let status_color = theme::status_color(&bt.status);
            let status_short = if bt.status == "completed" {
                "\u{2713}"
            } else if bt.status == "failed" {
                "\u{2717}"
            } else if bt.status.starts_with("running") {
                let pct = bt.status.split('(').nth(1)
                    .and_then(|s| s.trim_end_matches("%)").parse::<u8>().ok())
                    .map(|p| format!("{}%", p))
                    .unwrap_or_else(|| "run".to_string());
                return Row::new(vec![
                    Cell::from(bt.run_id.get(..6).unwrap_or(&bt.run_id).to_string())
                        .style(Style::default().fg(theme::FG_HINT)),
                    Cell::from(bt.strategy_name.as_deref().unwrap_or("-").to_string()),
                    Cell::from(bt.symbol.get(..8).unwrap_or(&bt.symbol).to_string()),
                    Cell::from(bt.interval.clone()),
                    Cell::from(Span::styled(pct, Style::default().fg(status_color))),
                ])
                .style(row_style);
            } else if bt.status == "queued" {
                "\u{2026}"
            } else {
                "?"
            };

            // PnL for completed
            let status_display = if bt.status == "completed" {
                bt.result_summary
                    .as_ref()
                    .and_then(|s| s.get("total_pnl_pct").or_else(|| s.get("total_pnl")))
                    .and_then(|v| v.as_f64())
                    .map(|v| format!("{}{:+.1}%", status_short, v))
                    .unwrap_or_else(|| status_short.to_string())
            } else {
                status_short.to_string()
            };

            Row::new(vec![
                Cell::from(bt.run_id.get(..6).unwrap_or(&bt.run_id).to_string())
                    .style(Style::default().fg(theme::FG_HINT)),
                Cell::from(bt.strategy_name.as_deref().unwrap_or("-").to_string()),
                Cell::from(bt.symbol.get(..8).unwrap_or(&bt.symbol).to_string()),
                Cell::from(bt.interval.clone()),
                Cell::from(Span::styled(status_display, Style::default().fg(status_color))),
            ])
            .style(row_style)
        })
        .collect();

    let widths = [
        Constraint::Length(8),
        Constraint::Min(10),
        Constraint::Length(10),
        Constraint::Length(5),
        Constraint::Length(10),
    ];

    let table = Table::new(rows, widths)
        .header(header)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(border_style)
                .title(Span::styled(title, theme::style_header())),
        );

    f.render_widget(table, area);
}

fn render_detail(f: &mut Frame, area: Rect, app: &App) {
    let is_focused = app.panel_focus == PanelFocus::Right;
    let border_style = if is_focused {
        theme::style_border_focused()
    } else {
        theme::style_border()
    };

    let bt = match app.backtests.get(app.backtest_selected) {
        Some(bt) => bt,
        None => {
            let p = Paragraph::new(Span::styled(
                "  No backtest selected",
                theme::style_dim(),
            ))
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(border_style)
                    .title(Span::styled(" DETAIL ", theme::style_header())),
            );
            f.render_widget(p, area);
            return;
        }
    };

    let has_equity = !app.detail_equity.is_empty();
    let constraints = if has_equity {
        vec![
            Constraint::Length(12),
            Constraint::Length(6),
            Constraint::Min(3),
        ]
    } else {
        vec![Constraint::Length(12), Constraint::Min(3)]
    };

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints(constraints)
        .split(area);

    // Stats panel
    let id_short = bt.run_id.get(..8).unwrap_or(&bt.run_id);
    let title = format!(" DETAIL: #{} ", id_short);

    let status_color = theme::status_color(&bt.status);
    let mut stat_lines = vec![
        Line::from(""),
        kv_line("  Strategy", bt.strategy_name.as_deref().unwrap_or("-")),
        kv_line("  Symbol  ", &bt.symbol),
        kv_line("  Interval", &bt.interval),
        kv_line("  Period  ", &format!("{} \u{2192} {}", bt.start_date, bt.end_date)),
        Line::from(vec![
            Span::styled("  Status  ", Style::default().fg(theme::FG_AMBER)),
            Span::styled(&bt.status, Style::default().fg(status_color)),
        ]),
    ];

    // Add stats from result_summary or detail_result
    let stats_source = app
        .detail_result
        .as_ref()
        .and_then(|r| r.get("statistics"))
        .or(bt.result_summary.as_ref());

    if let Some(stats) = stats_source.and_then(|s| s.as_object()) {
        stat_lines.push(Line::from(Span::styled(
            "  \u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}",
            theme::style_dim(),
        )));
        for (key, val) in stats.iter().take(8) {
            let val_str = if let Some(n) = val.as_f64() {
                format!("{:.4}", n)
            } else {
                val.to_string().trim_matches('"').to_string()
            };
            stat_lines.push(kv_line(&format!("  {:<10}", key), &val_str));
        }
    }

    let stats_para = Paragraph::new(stat_lines).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(border_style)
            .title(Span::styled(title, theme::style_header())),
    );
    f.render_widget(stats_para, chunks[0]);

    // Equity sparkline
    if has_equity {
        let sparkline = Sparkline::default()
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(border_style)
                    .title(Span::styled(" EQUITY ", theme::style_header())),
            )
            .data(&app.detail_equity)
            .style(Style::default().fg(theme::FG_RUNNING))
            .add_modifier(Modifier::BOLD);
        f.render_widget(sparkline, chunks[1]);
    }

    // Additional result summary
    let summary_idx = if has_equity { 2 } else { 1 };
    let source = app.detail_result.as_ref().and_then(|r| r.get("statistics"));
    let summary_lines = if let Some(obj) = source.and_then(|s| s.as_object()) {
        let mut lines = Vec::new();
        for (key, val) in obj.iter().skip(8).take(12) {
            let val_str = if let Some(n) = val.as_f64() {
                format!("{:.4}", n)
            } else {
                val.to_string().trim_matches('"').to_string()
            };
            lines.push(kv_line(&format!("  {:<10}", key), &val_str));
        }
        if lines.is_empty() {
            lines.push(Line::from(Span::styled("  \u{2014}", theme::style_dim())));
        }
        lines
    } else {
        vec![Line::from(Span::styled(
            "  Waiting for result data\u{2026}",
            theme::style_dim(),
        ))]
    };

    let summary_para = Paragraph::new(summary_lines).block(
        Block::default()
            .borders(Borders::ALL)
            .border_style(border_style)
            .title(Span::styled(" STATS ", theme::style_header())),
    );
    f.render_widget(summary_para, chunks[summary_idx]);
}

fn kv_line(label: &str, value: &str) -> Line<'static> {
    Line::from(vec![
        Span::styled(label.to_string(), Style::default().fg(theme::FG_AMBER)),
        Span::raw(" "),
        Span::styled(value.to_string(), Style::default().fg(theme::FG_PRIMARY)),
    ])
}
