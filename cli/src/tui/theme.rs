//! Bloomberg-inspired retro pixel color palette and style presets.
//!
//! Color rules:
//! - Amber for structure (headers, labels, brand)
//! - White for data values
//! - Green/Red are NEVER decorative — always semantic (positive/negative)
//! - Pure black background

use ratatui::style::{Color, Modifier, Style};

// ── Backgrounds ─────────────────────────────────────────────────────────

pub const BG_PRIMARY: Color = Color::Rgb(0, 0, 0);
pub const BG_PANEL: Color = Color::Rgb(8, 8, 8);
pub const BG_HEADER: Color = Color::Rgb(15, 15, 15);
pub const BG_SELECTED: Color = Color::Rgb(20, 30, 50);
pub const BG_INPUT: Color = Color::Rgb(10, 10, 10);
pub const BG_ERROR: Color = Color::Rgb(180, 30, 30);
pub const BG_WARN: Color = Color::Rgb(180, 150, 0);

// ── Foreground: Structure ───────────────────────────────────────────────

pub const FG_AMBER: Color = Color::Rgb(255, 176, 0);
pub const FG_LOGO: Color = Color::Rgb(255, 140, 0);
pub const FG_BORDER: Color = Color::Rgb(60, 60, 60);
pub const FG_BORDER_ACTIVE: Color = Color::Rgb(120, 120, 120);
pub const FG_DIM: Color = Color::Rgb(100, 100, 100);
pub const FG_HINT: Color = Color::Rgb(0, 180, 220);

// ── Foreground: Data ────────────────────────────────────────────────────

pub const FG_PRIMARY: Color = Color::Rgb(230, 230, 230);
pub const FG_SECONDARY: Color = Color::Rgb(180, 180, 180);
pub const FG_BRIGHT: Color = Color::Rgb(255, 255, 255);

// ── Foreground: Semantic ────────────────────────────────────────────────

pub const FG_POSITIVE: Color = Color::Rgb(0, 220, 80);
pub const FG_NEGATIVE: Color = Color::Rgb(220, 50, 50);
pub const FG_RUNNING: Color = Color::Rgb(0, 200, 220);
pub const FG_QUEUED: Color = Color::Rgb(220, 200, 0);
pub const FG_CANCELLED: Color = Color::Rgb(80, 80, 80);

// ── Special ─────────────────────────────────────────────────────────────

pub const FG_CURSOR: Color = Color::Rgb(255, 220, 0);
pub const FG_FLASH: Color = Color::Rgb(255, 255, 200);

// ── Style Presets ───────────────────────────────────────────────────────

/// Panel title / section header — amber bold.
pub fn style_header() -> Style {
    Style::default().fg(FG_AMBER).add_modifier(Modifier::BOLD)
}

/// Primary data text — white.
pub fn style_data() -> Style {
    Style::default().fg(FG_PRIMARY)
}

/// Dimmed / secondary text.
pub fn style_dim() -> Style {
    Style::default().fg(FG_DIM)
}

/// Key hint labels — cyan.
pub fn style_hint_key() -> Style {
    Style::default().fg(FG_HINT)
}

/// Key hint descriptions — dim white.
pub fn style_hint_desc() -> Style {
    Style::default().fg(FG_SECONDARY)
}

/// Selected row background.
pub fn style_selected() -> Style {
    Style::default().bg(BG_SELECTED).fg(FG_BRIGHT)
}

/// Focused panel border — bright.
pub fn style_border_focused() -> Style {
    Style::default().fg(FG_BORDER_ACTIVE)
}

/// Unfocused panel border — dim.
pub fn style_border() -> Style {
    Style::default().fg(FG_BORDER)
}

/// Positive value (profit, online, completed).
pub fn style_positive() -> Style {
    Style::default().fg(FG_POSITIVE)
}

/// Negative value (loss, offline, failed).
pub fn style_negative() -> Style {
    Style::default().fg(FG_NEGATIVE)
}

/// Running / in-progress.
pub fn style_running() -> Style {
    Style::default().fg(FG_RUNNING)
}

/// Queued / pending.
pub fn style_queued() -> Style {
    Style::default().fg(FG_QUEUED)
}

/// Error banner.
pub fn style_error() -> Style {
    Style::default().fg(FG_BRIGHT).bg(BG_ERROR)
}

/// Status color for a given status string.
pub fn status_color(status: &str) -> Color {
    match status {
        "completed" => FG_POSITIVE,
        "running" => FG_RUNNING,
        s if s.starts_with("running") => FG_RUNNING,
        "queued" => FG_QUEUED,
        "failed" | "error" => FG_NEGATIVE,
        "cancelled" => FG_CANCELLED,
        _ => FG_DIM,
    }
}

/// Status indicator dot character.
pub fn status_dot(status: &str) -> (&'static str, Color) {
    match status {
        "online" | "completed" | "connected" => ("\u{25CF}", FG_POSITIVE), // ●
        "starting" | "connecting" | "queued" => ("\u{25D0}", FG_QUEUED),   // ◐
        "running" => ("\u{25C9}", FG_RUNNING),                             // ◉
        "offline" | "stopped" | "failed" => ("\u{25CB}", FG_NEGATIVE),     // ○
        _ => ("\u{25CC}", FG_DIM),                                         // ◌
    }
}
