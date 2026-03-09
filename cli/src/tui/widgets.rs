//! Shared TUI primitives — reusable across all workspaces.
//!
//! Design rules:
//! - `header_cell`: every Table header must include a `─` divider (design principle).
//! - `kv_line`: amber label + white value, consistent key-value rendering.
//! - Color helpers use semantic positive/negative colors (never decorative).

use ratatui::{
    style::{Modifier, Style},
    text::{Line, Span, Text},
    widgets::{Block, Borders, Cell},
};

use super::theme;

// ── Constants ───────────────────────────────────────────────────────────

pub const HEARTBEAT_TIMEOUT_SECS: u64 = 30;

// ── Block helpers ───────────────────────────────────────────────────────

/// Standard bordered block with title. `focused` controls border highlight.
pub fn titled_block(title: &str, focused: bool) -> Block<'static> {
    let border_style = if focused {
        theme::style_border_focused()
    } else {
        theme::style_border()
    };
    Block::default()
        .borders(Borders::ALL)
        .border_style(border_style)
        .title(Span::styled(title.to_string(), theme::style_header()))
}

// ── Table helpers ───────────────────────────────────────────────────────

/// Table header cell with `─` divider line underneath (2 lines tall).
///
/// Must be used with `.height(2)` on the header `Row`.
pub fn header_cell(name: &str) -> Cell<'static> {
    Cell::from(Text::from(vec![
        Line::from(Span::styled(name.to_string(), theme::style_header())),
        Line::from(Span::styled(
            "\u{2500}".repeat(50),
            Style::default().fg(theme::FG_BORDER),
        )),
    ]))
}

// ── Line helpers ────────────────────────────────────────────────────────

/// Key-value line: amber label + white value.
pub fn kv_line(label: &str, value: &str) -> Line<'static> {
    Line::from(vec![
        Span::styled(label.to_string(), Style::default().fg(theme::FG_AMBER)),
        Span::raw(" "),
        Span::styled(value.to_string(), Style::default().fg(theme::FG_PRIMARY)),
    ])
}

/// Thin `─` divider line for separating sections.
pub fn divider_line() -> Line<'static> {
    Line::from(Span::styled(
        "  ".to_string() + &"\u{2500}".repeat(30),
        Style::default().fg(theme::FG_BORDER),
    ))
}

/// Section title — amber bold.
pub fn section_title(title: &str) -> Line<'static> {
    Line::from(Span::styled(
        title.to_string(),
        Style::default()
            .fg(theme::FG_AMBER)
            .add_modifier(Modifier::BOLD),
    ))
}

// ── Value formatting ────────────────────────────────────────────────────

/// Colored numeric value: green for positive, red for negative, white for zero.
pub fn colored_val(val: f64, suffix: &str) -> Span<'static> {
    let color = if val > 0.001 {
        theme::FG_POSITIVE
    } else if val < -0.001 {
        theme::FG_NEGATIVE
    } else {
        theme::FG_PRIMARY
    };
    let prefix = if val > 0.001 { "+" } else { "" };
    Span::styled(
        format!("{}{:.2}{}", prefix, val, suffix),
        Style::default().fg(color),
    )
}

/// Two stats side by side: `"  Label1 value1    Label2 value2"`.
pub fn stat_pair(l1: &str, v1: Option<f64>, l2: &str, v2: Option<f64>) -> Line<'static> {
    let v1_span = match v1 {
        Some(v) => colored_val(v, ""),
        None => Span::styled("-".to_string(), theme::style_dim()),
    };
    let v2_span = match v2 {
        Some(v) => colored_val(v, ""),
        None => Span::styled("-".to_string(), theme::style_dim()),
    };
    Line::from(vec![
        Span::styled(format!("  {:<7}", l1), Style::default().fg(theme::FG_AMBER)),
        Span::raw(" "),
        v1_span,
        Span::raw("    "),
        Span::styled(format!("{:<7}", l2), Style::default().fg(theme::FG_AMBER)),
        Span::raw(" "),
        v2_span,
    ])
}
