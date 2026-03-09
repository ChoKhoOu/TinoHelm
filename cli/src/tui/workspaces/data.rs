//! F5 — Data workspace: data catalog table.

use ratatui::{
    layout::Rect,
    style::Style,
    text::{Line, Span},
    widgets::{Block, Borders, Paragraph},
    Frame,
};

use crate::tui::app::App;
use crate::tui::theme;

pub fn render(f: &mut Frame, area: Rect, app: &App) {
    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::style_border())
        .title(Span::styled(" DATA CATALOG ", theme::style_header()));

    let mut lines = Vec::new();

    // Header row
    lines.push(Line::from(vec![
        Span::styled(
            format!(
                "  {:<18}{:<12}{:<12}{:<10}{}",
                "SYMBOL", "INTERVAL", "BARS", "SIZE", "RANGE"
            ),
            theme::style_header(),
        ),
    ]));
    lines.push(Line::from(Span::styled(
        "  \u{2500}".to_string() + &"\u{2500}".repeat(area.width.saturating_sub(4) as usize),
        theme::style_dim(),
    )));

    if let Some(catalog) = app.data_catalog.as_ref().and_then(|c| c.as_array()) {
        for (i, item) in catalog.iter().enumerate() {
            let is_selected = i == app.data_selected;
            let row_style = if is_selected {
                theme::style_selected()
            } else {
                theme::style_data()
            };

            let symbol = item
                .get("symbol")
                .and_then(|v| v.as_str())
                .unwrap_or("-");
            let interval = item
                .get("interval")
                .and_then(|v| v.as_str())
                .unwrap_or("-");
            let bars = item
                .get("bar_count")
                .and_then(|v| v.as_u64())
                .map(|v| format!("{}", v))
                .unwrap_or_else(|| "-".to_string());
            let size = item
                .get("size_mb")
                .and_then(|v| v.as_f64())
                .map(|v| format!("{:.1} MB", v))
                .unwrap_or_else(|| "-".to_string());
            let range = item
                .get("date_range")
                .and_then(|v| v.as_str())
                .unwrap_or("-");

            lines.push(Line::from(Span::styled(
                format!(
                    "  {:<18}{:<12}{:<12}{:<10}{}",
                    symbol, interval, bars, size, range
                ),
                row_style,
            )));
        }

        if catalog.is_empty() {
            lines.push(Line::from(Span::styled(
                "  No data in catalog. Press 'f' to fetch.",
                theme::style_dim(),
            )));
        }
    } else if app.data_loading {
        lines.push(Line::from(Span::styled(
            "  Loading\u{2026}",
            theme::style_dim(),
        )));
    } else {
        lines.push(Line::from(Span::styled(
            "  Press 'r' to load data catalog",
            theme::style_dim(),
        )));
    }

    let p = Paragraph::new(lines).block(block);
    f.render_widget(p, area);
}
