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
use ratatui_macros::{line, span};

use super::theme;

// ── Constants ───────────────────────────────────────────────────────────

pub const HEARTBEAT_TIMEOUT_SECS: u64 = 30;

const BRAILLE_SPINNER: &[char] = &['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

// ── Animation helpers ──────────────────────────────────────────────────

/// Braille spinner character, cycles every 2 frames.
pub fn spinner(frame: u64) -> char {
    BRAILLE_SPINNER[(frame as usize / 2) % BRAILLE_SPINNER.len()]
}

/// Pulse a color smoothly between bright and dim using a sine wave.
/// Full cycle is ~20 frames (~1.3s at 15 fps).
pub fn pulse_color(bright: ratatui::style::Color, dim: ratatui::style::Color, frame: u64) -> ratatui::style::Color {
    use ratatui::style::Color;
    let (Color::Rgb(br, bg, bb), Color::Rgb(dr, dg, db)) = (bright, dim) else {
        return if (frame / 5) % 2 == 0 { bright } else { dim };
    };
    // sine wave mapped to 0.0..=1.0 (t=1 means fully bright)
    let phase = (frame % 20) as f64 / 20.0 * std::f64::consts::TAU;
    let t = (phase.sin() + 1.0) / 2.0;
    let lerp = |a: u8, b: u8| -> u8 {
        (a as f64 + (b as f64 - a as f64) * t) as u8
    };
    Color::Rgb(lerp(dr, br), lerp(dg, bg), lerp(db, bb))
}

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
    line![
        span!(Style::default().fg(theme::FG_AMBER); "{}", label),
        span!(" "),
        span!(Style::default().fg(theme::FG_PRIMARY); "{}", value),
    ]
}

/// Thin `─` divider line for separating sections.
pub fn divider_line() -> Line<'static> {
    line![span!(Style::default().fg(theme::FG_BORDER); "  {}", "\u{2500}".repeat(30))]
}

/// Section title — amber bold.
pub fn section_title(title: &str) -> Line<'static> {
    line![span!(Style::default().fg(theme::FG_AMBER).add_modifier(Modifier::BOLD); "{}", title)]
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

/// Neutral numeric value: always primary color, no pos/neg semantics.
/// Displays integers without decimals, floats with 2 decimal places.
pub fn neutral_val(val: f64, suffix: &str) -> Span<'static> {
    let text = if val.fract().abs() < 0.001 {
        format!("{}{}", val as i64, suffix)
    } else {
        format!("{:.2}{}", val, suffix)
    };
    Span::styled(text, Style::default().fg(theme::FG_PRIMARY))
}

/// Two stats side by side: `"  Label1 value1    Label2 value2"`.
pub fn stat_pair(l1: &str, v1: Option<f64>, l2: &str, v2: Option<f64>) -> Line<'static> {
    let v1_span = match v1 {
        Some(v) => colored_val(v, ""),
        None => span!(theme::style_dim(); "-"),
    };
    let v2_span = match v2 {
        Some(v) => colored_val(v, ""),
        None => span!(theme::style_dim(); "-"),
    };
    line![
        span!(Style::default().fg(theme::FG_AMBER); "  {:<7}", l1),
        span!(" "),
        v1_span,
        span!("    "),
        span!(Style::default().fg(theme::FG_AMBER); "{:<7}", l2),
        span!(" "),
        v2_span,
    ]
}

/// Two stats side by side with neutral (non-colored) values — for counts and non-PnL fields.
pub fn stat_pair_neutral(l1: &str, v1: Option<f64>, l2: &str, v2: Option<f64>) -> Line<'static> {
    let v1_span = match v1 {
        Some(v) => neutral_val(v, ""),
        None => span!(theme::style_dim(); "-"),
    };
    let v2_span = match v2 {
        Some(v) => neutral_val(v, ""),
        None => span!(theme::style_dim(); "-"),
    };
    line![
        span!(Style::default().fg(theme::FG_AMBER); "  {:<7}", l1),
        span!(" "),
        v1_span,
        span!("    "),
        span!(Style::default().fg(theme::FG_AMBER); "{:<7}", l2),
        span!(" "),
        v2_span,
    ]
}
