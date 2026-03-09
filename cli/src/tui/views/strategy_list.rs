use ratatui::{
    layout::Rect,
    style::{Color, Modifier, Style},
    widgets::{Block, Borders, Cell, Row, Table},
    Frame,
};

use crate::tui::app::App;

pub fn render(f: &mut Frame, area: Rect, app: &App) {
    let header = Row::new(vec![
        Cell::from("Name"),
        Cell::from("Class"),
        Cell::from("Config"),
        Cell::from("File"),
    ])
    .style(Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD));

    let rows: Vec<Row> = app
        .strategies
        .iter()
        .enumerate()
        .map(|(i, s)| {
            let selected_style = if i == app.strategy_selected {
                Style::default().bg(Color::DarkGray)
            } else {
                Style::default()
            };

            Row::new(vec![
                Cell::from(s.name.clone()),
                Cell::from(s.strategy_class.clone().unwrap_or_default()),
                Cell::from(s.config_class.clone().unwrap_or_default()),
                Cell::from(
                    s.file_path
                        .as_deref()
                        .and_then(|p| p.rsplit('/').next())
                        .unwrap_or("-")
                        .to_string(),
                ),
            ])
            .style(selected_style)
        })
        .collect();

    let widths = [
        ratatui::layout::Constraint::Min(16),
        ratatui::layout::Constraint::Min(16),
        ratatui::layout::Constraint::Min(16),
        ratatui::layout::Constraint::Min(20),
    ];

    let title = if app.strategy_loading {
        " Strategies (loading...) "
    } else {
        " Strategies "
    };

    let table = Table::new(rows, widths)
        .header(header)
        .block(Block::default().borders(Borders::ALL).title(title));

    f.render_widget(table, area);
}

pub fn hints() -> Vec<(&'static str, &'static str)> {
    vec![("j/k", "navigate"), ("r", "refresh/rescan")]
}
