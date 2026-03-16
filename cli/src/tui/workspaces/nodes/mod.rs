//! F4 — Nodes workspace: Bloomberg-style trading dashboard.

pub mod overview;

use ratatui::Frame;
use ratatui::layout::Rect;

use crate::tui::app::App;

/// Top-level render — single overview layout (no more detail drill-down).
pub fn render(f: &mut Frame, area: Rect, app: &App) {
    overview::render(f, area, app);
}

pub use overview::{fire_load_fills, fire_load_orders, fire_load_portfolios, fire_load_positions};
