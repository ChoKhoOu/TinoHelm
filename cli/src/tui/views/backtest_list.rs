use ratatui::{
    layout::Rect,
    style::{Color, Modifier, Style},
    text::Span,
    widgets::{Block, Borders, Cell, Row, Table},
    Frame,
};

use crate::tui::app::App;

pub fn render(f: &mut Frame, area: Rect, app: &App) {
    let header = Row::new(vec![
        Cell::from("Run ID"),
        Cell::from("Strategy"),
        Cell::from("Symbol"),
        Cell::from("Interval"),
        Cell::from("Status"),
        Cell::from("Created"),
    ])
    .style(Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD));

    let rows: Vec<Row> = app
        .backtests
        .iter()
        .enumerate()
        .map(|(i, bt)| {
            let status_style = match bt.status.as_str() {
                "completed" => Style::default().fg(Color::Green),
                "failed" => Style::default().fg(Color::Red),
                "cancelled" => Style::default().fg(Color::DarkGray),
                s if s.starts_with("running") => Style::default().fg(Color::Cyan),
                "queued" => Style::default().fg(Color::Yellow),
                _ => Style::default(),
            };

            let selected_style = if i == app.backtest_selected {
                Style::default().bg(Color::DarkGray)
            } else {
                Style::default()
            };

            Row::new(vec![
                Cell::from(bt.run_id.get(..8).unwrap_or(&bt.run_id).to_string()),
                Cell::from(bt.strategy_name.clone().unwrap_or_default()),
                Cell::from(bt.symbol.clone()),
                Cell::from(bt.interval.clone()),
                Cell::from(Span::styled(bt.status.clone(), status_style)),
                Cell::from(
                    bt.created_at
                        .as_deref()
                        .unwrap_or("-")
                        .get(..19)
                        .unwrap_or("-")
                        .to_string(),
                ),
            ])
            .style(selected_style)
        })
        .collect();

    let widths = [
        ratatui::layout::Constraint::Length(10),
        ratatui::layout::Constraint::Min(12),
        ratatui::layout::Constraint::Length(14),
        ratatui::layout::Constraint::Length(8),
        ratatui::layout::Constraint::Length(16),
        ratatui::layout::Constraint::Length(20),
    ];

    let title = if app.backtest_loading {
        " Backtests (loading...) "
    } else {
        " Backtests "
    };

    let table = Table::new(rows, widths)
        .header(header)
        .block(Block::default().borders(Borders::ALL).title(title));

    f.render_widget(table, area);
}

pub fn hints() -> Vec<(&'static str, &'static str)> {
    vec![
        ("j/k", "navigate"),
        ("Enter", "detail"),
        ("n", "new backtest"),
        ("r", "refresh"),
    ]
}
