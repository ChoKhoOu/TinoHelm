//! F4 — Nodes workspace: sandbox monitoring with overview + strategy detail drill-down.

pub mod detail;
pub mod overview;

use ratatui::Frame;
use ratatui::layout::Rect;

use crate::tui::app::App;

/// Top-level render dispatch based on current node view.
pub fn render(f: &mut Frame, area: Rect, app: &App) {
    match app.node_view {
        crate::tui::app::NodeView::Overview => overview::render(f, area, app),
        crate::tui::app::NodeView::StrategyDetail => detail::render(f, area, app),
    }
}

pub use overview::{fire_load_fills, fire_load_positions};
