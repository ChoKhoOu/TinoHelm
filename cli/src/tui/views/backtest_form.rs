use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Paragraph},
    Frame,
};

use crate::tui::app::App;

const FIELDS: &[&str] = &["Strategy", "Symbol", "Interval", "Start Date", "End Date"];

pub fn render(f: &mut Frame, area: Rect, app: &App) {
    let values = [
        &app.form_strategy,
        &app.form_symbol,
        &app.form_interval,
        &app.form_start,
        &app.form_end,
    ];

    let constraints: Vec<Constraint> = (0..FIELDS.len())
        .map(|_| Constraint::Length(3))
        .chain(std::iter::once(Constraint::Min(1)))
        .collect();

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints(constraints)
        .split(area);

    for (i, (&label, value)) in FIELDS.iter().zip(values.iter()).enumerate() {
        let style = if i == app.form_focus {
            Style::default().fg(Color::Yellow)
        } else {
            Style::default().fg(Color::Gray)
        };

        let border_style = if i == app.form_focus {
            Style::default().fg(Color::Yellow)
        } else {
            Style::default().fg(Color::DarkGray)
        };

        let display = if value.is_empty() && i == app.form_focus {
            "│"
        } else if value.is_empty() {
            ""
        } else {
            value.as_str()
        };

        let input = Paragraph::new(Line::from(Span::styled(display, style))).block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(border_style)
                .title(format!(" {} ", label)),
        );
        f.render_widget(input, chunks[i]);
    }

    // Help text at bottom
    let help = Paragraph::new(Line::from(vec![
        Span::styled("Tab", Style::default().fg(Color::Yellow)),
        Span::raw(" next field  "),
        Span::styled("Enter", Style::default().fg(Color::Yellow)),
        Span::raw(" submit  "),
        Span::styled("Esc", Style::default().fg(Color::Yellow)),
        Span::raw(" cancel"),
    ]));
    if chunks.len() > FIELDS.len() {
        f.render_widget(help, chunks[FIELDS.len()]);
    }
}

pub fn hints() -> Vec<(&'static str, &'static str)> {
    vec![("Tab", "next field"), ("Enter", "submit"), ("Esc", "cancel")]
}
