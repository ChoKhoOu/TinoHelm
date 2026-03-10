//! F5 — Data workspace: data catalog table.

use ratatui::{
    layout::Rect,
    text::{Line, Span},
    widgets::Paragraph,
    Frame,
};

use crate::tui::app::App;
use crate::tui::theme;
use crate::tui::widgets::{self, titled_block};

pub fn render(f: &mut Frame, area: Rect, app: &App) {
    let block = titled_block(" DATA CATALOG ", false);

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
            let bars = "-".to_string(); // API does not provide bar count
            let size = item
                .get("size_bytes")
                .and_then(|v| v.as_u64())
                .map(|v| format!("{:.1} MB", v as f64 / 1_048_576.0))
                .unwrap_or_else(|| "-".to_string());
            let start_d = item
                .get("start_date")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let end_d = item
                .get("end_date")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let range = if start_d.is_empty() && end_d.is_empty() {
                "-".to_string()
            } else {
                format!("{} ~ {}", start_d, end_d)
            };

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
            format!("  {} Loading\u{2026}", widgets::spinner(app.frame_count)),
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
