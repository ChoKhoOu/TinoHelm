//! Shared CLI styling utilities — Rust equivalent of Python `_style.py`.
//!
//! Provides semantic colors, text helpers, table rendering, box-drawn reports,
//! and progress indicators for consistent terminal output across all commands.

use crossterm::style::{Color, Stylize};

/// Bright green (ANSI 92) — standard ANSI 32 green looks grey on light terminals.
pub const POS: Color = Color::AnsiValue(10);

// ── ANSI Helpers ─────────────────────────────────────────────────────────

/// Strip ANSI escape sequences from a string.
pub fn strip_ansi(s: &str) -> String {
    let mut result = String::with_capacity(s.len());
    let mut in_escape = false;
    for c in s.chars() {
        if in_escape {
            if c == 'm' {
                in_escape = false;
            }
            continue;
        }
        if c == '\x1b' {
            in_escape = true;
            continue;
        }
        result.push(c);
    }
    result
}

/// Get the visual (non-ANSI) length of a string.
pub fn ansi_len(s: &str) -> usize {
    strip_ansi(s).len()
}

/// Right-pad a string to a given visual width (ANSI-aware).
pub fn rpad(text: &str, width: usize) -> String {
    let visible = ansi_len(text);
    let padding = width.saturating_sub(visible);
    format!("{}{}", text, " ".repeat(padding))
}

// ── Text Helpers ─────────────────────────────────────────────────────────

pub fn bold(text: &str) -> String {
    format!("{}", text.bold())
}

pub fn dim(text: &str) -> String {
    format!("{}", text.dim())
}

pub fn accent(text: &str) -> String {
    format!("{}", text.cyan())
}

pub fn muted(text: &str) -> String {
    format!("{}", text.dark_grey())
}

/// Format and color a numeric value: green if positive, red if negative.
pub fn color_value(val: Option<f64>, fmt: &str) -> String {
    match val {
        None => muted("-"),
        Some(v) => {
            let s = match fmt {
                "+.2f" => format!("{:+.2}", v),
                ".2f" => format!("{:.2}", v),
                ".4f" => format!("{:.4}", v),
                "+.4f" => format!("{:+.4}", v),
                ".6f" => format!("{:.6}", v),
                "+.6f" => format!("{:+.6}", v),
                _ => format!("{:+.2}", v),
            };
            if v > 0.0 {
                format!("{}", s.with(POS))
            } else if v < 0.0 {
                format!("{}", s.red())
            } else {
                s
            }
        }
    }
}

/// Format a value without color.
pub fn format_value(val: Option<f64>, fmt: &str, suffix: &str) -> String {
    match val {
        None => muted("-"),
        Some(v) => {
            let s = match fmt {
                "+.2f" => format!("{:+.2}", v),
                ".2f" => format!("{:.2}", v),
                ".4f" => format!("{:.4}", v),
                _ => format!("{:.2}", v),
            };
            format!("{}{}", s, suffix)
        }
    }
}

/// Color a status string with status-specific color.
pub fn color_status(status: &str) -> String {
    match status {
        "completed" => format!("{}", status.with(POS).bold()),
        "running" => format!("{}", status.yellow().bold()),
        "queued" => format!("{}", status.cyan().bold()),
        "failed" | "error" => format!("{}", status.red().bold()),
        "cancelled" => format!("{}", status.magenta().bold()),
        _ => format!("{}", status.white().bold()),
    }
}

/// Render a colored [icon] badge for a status.
pub fn status_badge(status: &str) -> String {
    match status {
        "completed" => format!("{}", "[+]".with(POS)),
        "running" => format!("{}", "[~]".yellow()),
        "queued" => format!("{}", "[.]".cyan()),
        "failed" | "error" => format!("{}", "[x]".red()),
        "cancelled" => format!("{}", "[!]".magenta()),
        _ => "[?]".to_string(),
    }
}

// ── Layout Helpers ───────────────────────────────────────────────────────

/// Print a section header.
pub fn header(title: &str) {
    println!();
    println!("  {}", bold(title));
}

/// Print a horizontal divider.
pub fn divider(width: usize) {
    println!("  {}", "-".repeat(width));
}

/// Print a key-value pair with right-aligned label.
pub fn kv(label: &str, value: &str, label_width: usize) {
    println!("    {:>width$}: {}", label, value, width = label_width);
}

// ── Table ────────────────────────────────────────────────────────────────

pub enum Align {
    Left,
    Right,
}

struct TableColumn {
    name: String,
    width: usize,
    align: Align,
}

pub struct Table {
    columns: Vec<TableColumn>,
}

impl Table {
    pub fn new(cols: &[(&str, usize, &str)]) -> Self {
        let columns = cols
            .iter()
            .map(|(name, width, align)| TableColumn {
                name: name.to_string(),
                width: *width,
                align: if *align == "right" {
                    Align::Right
                } else {
                    Align::Left
                },
            })
            .collect();
        Self { columns }
    }

    fn pad(text: &str, width: usize, align: &Align) -> String {
        let visible = ansi_len(text);
        let padding = width.saturating_sub(visible);
        match align {
            Align::Left => format!("{}{}", text, " ".repeat(padding)),
            Align::Right => format!("{}{}", " ".repeat(padding), text),
        }
    }

    pub fn header(&self) {
        let parts: Vec<String> = self
            .columns
            .iter()
            .map(|c| Self::pad(&c.name, c.width, &c.align))
            .collect();
        println!();
        println!("  {}", bold(&parts.join("  ")));
        let total_w: usize =
            self.columns.iter().map(|c| c.width).sum::<usize>()
                + self.columns.len().saturating_sub(1) * 2;
        divider(total_w);
    }

    pub fn row(&self, values: &[&str]) {
        let parts: Vec<String> = values
            .iter()
            .enumerate()
            .map(|(i, val)| {
                if i < self.columns.len() {
                    Self::pad(val, self.columns[i].width, &self.columns[i].align)
                } else {
                    val.to_string()
                }
            })
            .collect();
        println!("  {}", parts.join("  "));
    }

    pub fn footer(&self) {
        println!();
    }
}

// ── Progress ─────────────────────────────────────────────────────────────

/// Render a text progress bar.
pub fn progress_bar(pct: u8, width: usize) -> String {
    let filled = (width as u16 * pct as u16 / 100) as usize;
    let empty = width.saturating_sub(filled);
    let bar_filled = format!("{}", "=".repeat(filled).with(POS));
    let bar_empty = dim(&"-".repeat(empty));
    format!("[{}{}] {}%", bar_filled, bar_empty, pct)
}

/// Render a single-line progress indicator for polling loops.
pub fn inline_progress(pct: u8, status: &str, elapsed: u64, width: usize) -> String {
    let filled = (width as u16 * pct as u16 / 100) as usize;
    let empty = width.saturating_sub(filled);
    let bar = format!("{}{}", "=".repeat(filled), "-".repeat(empty));
    format!(
        "  [{}] {:>3}%  {:>10}  [{}s]",
        bar,
        pct,
        color_status(status),
        elapsed,
    )
}

// ── Node Helpers ─────────────────────────────────────────────────────────

pub fn mode_label(mode: &str) -> String {
    match mode {
        "live" => format!("{}", "LIVE".yellow().bold()),
        _ => format!("{}", "SANDBOX".cyan().bold()),
    }
}

pub fn node_status_color(status: &str) -> String {
    match status {
        "running" => format!("{}", status.with(POS).bold()),
        "starting" => format!("{}", status.yellow().bold()),
        "stopped" => format!("{}", status.red().bold()),
        "error" => format!("{}", status.red().bold()),
        _ => format!("{}", status.white().bold()),
    }
}

pub fn node_badge(status: &str) -> String {
    match status {
        "running" => format!("{}", "[+]".with(POS)),
        "starting" => format!("{}", "[~]".yellow()),
        "stopped" => format!("{}", "[x]".red()),
        "error" => format!("{}", "[!]".red()),
        _ => "[?]".to_string(),
    }
}

// ── Box Drawing (for result reports) ─────────────────────────────────────

const BOX_H: &str = "\u{2500}";
const BOX_V: &str = "\u{2502}";
const BOX_TL: &str = "\u{250C}";
const BOX_TR: &str = "\u{2510}";
const BOX_BL: &str = "\u{2514}";
const BOX_BR: &str = "\u{2518}";
const BOX_LT: &str = "\u{251C}";
const BOX_RT: &str = "\u{2524}";

pub struct BoxReport {
    width: usize,
}

impl BoxReport {
    pub fn new(width: usize) -> Self {
        Self { width }
    }

    pub fn top(&self) {
        println!(
            "  {}{}{}",
            BOX_TL,
            BOX_H.repeat(self.width),
            BOX_TR,
        );
    }

    pub fn mid(&self) {
        println!(
            "  {}{}{}",
            BOX_LT,
            BOX_H.repeat(self.width),
            BOX_RT,
        );
    }

    pub fn bot(&self) {
        println!(
            "  {}{}{}",
            BOX_BL,
            BOX_H.repeat(self.width),
            BOX_BR,
        );
    }

    pub fn line(&self, text: &str) {
        let pad = self.width.saturating_sub(ansi_len(text) + 1);
        println!("  {} {}{}{}", BOX_V, text, " ".repeat(pad), BOX_V);
    }

    pub fn pair(&self, l1: &str, v1: &str, l2: &str, v2: &str) {
        let left = format!("{:>14}: {}", l1, rpad(v1, 12));
        let right = format!("{:>14}: {}", l2, v2);
        let combined = format!("{}  {}", left, right);
        self.line(&combined);
    }

    pub fn empty(&self) {
        self.line("");
    }
}
