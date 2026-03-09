use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Paragraph, Sparkline},
    Frame,
};

use crate::tui::app::App;

pub fn render(f: &mut Frame, area: Rect, app: &App) {
    let bt = match app.backtests.get(app.backtest_selected) {
        Some(bt) => bt,
        None => {
            let msg = Paragraph::new("No backtest selected")
                .block(Block::default().borders(Borders::ALL).title(" Backtest Detail "));
            f.render_widget(msg, area);
            return;
        }
    };

    let has_equity = !app.detail_equity.is_empty();
    let constraints = if has_equity {
        vec![
            Constraint::Length(10),
            Constraint::Length(6),
            Constraint::Min(5),
        ]
    } else {
        vec![Constraint::Length(10), Constraint::Min(5)]
    };

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints(constraints)
        .split(area);

    // Stats panel
    let stats_lines = vec![
        Line::from(vec![
            Span::styled("Run ID:   ", Style::default().fg(Color::Yellow)),
            Span::raw(&bt.run_id),
        ]),
        Line::from(vec![
            Span::styled("Strategy: ", Style::default().fg(Color::Yellow)),
            Span::raw(bt.strategy_name.as_deref().unwrap_or("-")),
        ]),
        Line::from(vec![
            Span::styled("Symbol:   ", Style::default().fg(Color::Yellow)),
            Span::raw(&bt.symbol),
        ]),
        Line::from(vec![
            Span::styled("Interval: ", Style::default().fg(Color::Yellow)),
            Span::raw(&bt.interval),
        ]),
        Line::from(vec![
            Span::styled("Period:   ", Style::default().fg(Color::Yellow)),
            Span::raw(format!("{} → {}", bt.start_date, bt.end_date)),
        ]),
        Line::from(vec![
            Span::styled("Status:   ", Style::default().fg(Color::Yellow)),
            Span::styled(
                &bt.status,
                match bt.status.as_str() {
                    "completed" => Style::default().fg(Color::Green),
                    "failed" => Style::default().fg(Color::Red),
                    _ => Style::default().fg(Color::Cyan),
                },
            ),
        ]),
    ];

    let stats = Paragraph::new(stats_lines)
        .block(Block::default().borders(Borders::ALL).title(" Backtest Detail "));
    f.render_widget(stats, chunks[0]);

    // Equity curve sparkline (if available)
    if has_equity {
        let sparkline = Sparkline::default()
            .block(Block::default().borders(Borders::ALL).title(" Equity Curve "))
            .data(&app.detail_equity)
            .style(Style::default().fg(Color::Cyan));
        f.render_widget(sparkline, chunks[1]);
    }

    // Result summary
    let summary_idx = if has_equity { 2 } else { 1 };
    let source = app.detail_result.as_ref().and_then(|r| r.get("statistics"));
    let summary_text = if let Some(obj) = source.and_then(|s| s.as_object()) {
        let mut lines = Vec::new();
        for (key, val) in obj.iter().take(20) {
            lines.push(Line::from(vec![
                Span::styled(
                    format!("{:<28} ", key),
                    Style::default().fg(Color::Yellow),
                ),
                Span::raw(format!("{}", val)),
            ]));
        }
        if lines.is_empty() {
            lines.push(Line::from("No statistics data"));
        }
        lines
    } else if let Some(ref summary) = bt.result_summary {
        // Fallback to list-level summary
        let mut lines = Vec::new();
        if let Some(obj) = summary.as_object() {
            for (key, val) in obj.iter().take(15) {
                lines.push(Line::from(vec![
                    Span::styled(
                        format!("{:<28} ", key),
                        Style::default().fg(Color::Yellow),
                    ),
                    Span::raw(format!("{}", val)),
                ]));
            }
        }
        if lines.is_empty() {
            lines.push(Line::from("No result data available"));
        }
        lines
    } else {
        vec![Line::from("No result summary (run may still be in progress)")]
    };

    let summary = Paragraph::new(summary_text)
        .block(Block::default().borders(Borders::ALL).title(" Result Summary "));
    f.render_widget(summary, chunks[summary_idx]);
}

pub fn hints() -> Vec<(&'static str, &'static str)> {
    vec![("Esc", "back"), ("r", "refresh")]
}
