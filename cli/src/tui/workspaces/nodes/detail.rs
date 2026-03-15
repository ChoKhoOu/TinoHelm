//! Nodes strategy detail — per-strategy drill-down with equity curve, positions, fills.

use std::collections::BTreeMap;

use ratatui::{
    layout::{Alignment, Constraint, Direction, Layout, Rect},
    style::{Modifier, Style},
    symbols::Marker,
    text::{Line, Span},
    widgets::{Axis, Cell, Chart, Dataset, GraphType, Paragraph, Row, Table},
    Frame,
};
use ratatui_macros::{line, span};

use crate::tui::app::App;
use crate::tui::theme;
use crate::tui::widgets::{colored_val, header_cell, strip_venue, titled_block};
use crate::types::{TradingFill, TradingPosition};

/// Render the strategy detail view.
pub fn render(f: &mut Frame, area: Rect, app: &App) {
    let tag = match &app.selected_strategy_tag {
        Some(t) => t.as_str(),
        None => {
            let lines = vec![
                Line::from(""),
                Line::from(Span::styled(
                    "No strategy selected",
                    Style::default().fg(theme::FG_DIM),
                )),
                Line::from(Span::styled(
                    "Press Esc to return",
                    Style::default().fg(theme::FG_HINT),
                )),
            ];
            let p = Paragraph::new(lines).alignment(Alignment::Center);
            f.render_widget(p, area);
            return;
        }
    };

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),      // Strategy header
            Constraint::Percentage(50), // Upper: equity + positions
            Constraint::Min(5),         // Lower: instrument PnL + fills
        ])
        .split(area);

    render_strategy_header(f, chunks[0], app, tag);

    // Upper half: equity curve (left) + positions (right)
    let upper_cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(40),
            Constraint::Percentage(60),
        ])
        .split(chunks[1]);

    render_equity_curve(f, upper_cols[0], app, tag);
    render_strategy_positions(f, upper_cols[1], app, tag);

    // Lower half: instrument PnL (left) + fills (right)
    let lower_cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(40),
            Constraint::Percentage(60),
        ])
        .split(chunks[2]);

    render_instrument_breakdown(f, lower_cols[0], app, tag);
    render_strategy_fills(f, lower_cols[1], app, tag);
}

// ── Strategy header ──────────────────────────────────────────────────

fn render_strategy_header(f: &mut Frame, area: Rect, app: &App, tag: &str) {
    // Determine strategy status from lifecycle_state
    let status = app
        .lifecycle_state
        .as_ref()
        .and_then(|s| s.get("strategy_states"))
        .and_then(|v| v.as_object())
        .and_then(|m| m.get(tag))
        .and_then(|v| v.as_str())
        .unwrap_or("--");

    let status_color = match status {
        "running" => theme::FG_POSITIVE,
        "paused" => theme::FG_QUEUED,
        _ => theme::FG_DIM,
    };

    // Compute totals from positions matching this strategy
    let strategy_positions: Vec<&TradingPosition> = app
        .positions
        .iter()
        .filter(|p| p.strategy_id_tag == tag)
        .collect();

    let total_realized: f64 = strategy_positions
        .iter()
        .filter_map(|p| p.realized_pnl)
        .sum();

    let total_unrealized: f64 = strategy_positions
        .iter()
        .filter(|p| p.is_open)
        .filter_map(|p| p.unrealized_pnl)
        .sum();

    let fill_count = app
        .fills
        .iter()
        .filter(|f| f.strategy_id_tag.as_deref() == Some(tag))
        .count();

    let cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(25),
            Constraint::Percentage(25),
            Constraint::Percentage(25),
            Constraint::Percentage(25),
        ])
        .split(area);

    // Name + status
    let name_lines = vec![line![
        span!(Style::default().fg(theme::FG_IDENTIFIER).add_modifier(Modifier::BOLD); " {} ", tag),
        span!(Style::default().fg(status_color); "[{}]", status.to_uppercase()),
    ]];
    f.render_widget(Paragraph::new(name_lines), cols[0]);

    // Realized PnL
    let rpnl_lines = vec![line![
        span!(Style::default().fg(theme::FG_AMBER); " Realized "),
        colored_val(total_realized, ""),
    ]];
    f.render_widget(Paragraph::new(rpnl_lines), cols[1]);

    // Unrealized PnL
    let upnl_lines = vec![line![
        span!(Style::default().fg(theme::FG_AMBER); " Unrealized "),
        colored_val(total_unrealized, ""),
    ]];
    f.render_widget(Paragraph::new(upnl_lines), cols[2]);

    // Fill count
    let fill_lines = vec![line![
        span!(Style::default().fg(theme::FG_AMBER); " Fills "),
        span!(Style::default().fg(theme::FG_PRIMARY); "{}", fill_count),
    ]];
    f.render_widget(Paragraph::new(fill_lines), cols[3]);
}

// ── Equity curve ─────────────────────────────────────────────────────

fn render_equity_curve(f: &mut Frame, area: Rect, app: &App, tag: &str) {
    let mut closed: Vec<&TradingPosition> = app
        .positions
        .iter()
        .filter(|p| !p.is_open && p.strategy_id_tag == tag)
        .collect();

    // Sort by ts_closed ascending
    closed.sort_by(|a, b| {
        let a_ts = a.ts_closed.as_deref().unwrap_or("");
        let b_ts = b.ts_closed.as_deref().unwrap_or("");
        a_ts.cmp(b_ts)
    });

    if closed.is_empty() {
        let block = titled_block(" EQUITY ", false);
        let p = Paragraph::new(vec![
            Line::from(""),
            Line::from(Span::styled(
                "No closed positions yet",
                Style::default().fg(theme::FG_DIM),
            )),
        ])
        .alignment(Alignment::Center)
        .block(block);
        f.render_widget(p, area);
        return;
    }

    // Build cumulative PnL series
    let mut cumulative = 0.0;
    let data_points: Vec<(f64, f64)> = closed
        .iter()
        .enumerate()
        .map(|(i, pos)| {
            cumulative += pos.realized_pnl.unwrap_or(0.0);
            (i as f64, cumulative)
        })
        .collect();

    let min_y = data_points
        .iter()
        .map(|(_, y)| *y)
        .fold(f64::INFINITY, f64::min);
    let max_y = data_points
        .iter()
        .map(|(_, y)| *y)
        .fold(f64::NEG_INFINITY, f64::max);
    let y_margin = (max_y - min_y).abs() * 0.1 + 0.01;

    let datasets = vec![Dataset::default()
        .marker(Marker::Braille)
        .graph_type(GraphType::Line)
        .style(Style::default().fg(theme::FG_RUNNING))
        .data(&data_points)];

    let chart = Chart::new(datasets)
        .block(titled_block(" EQUITY ", false))
        .x_axis(
            Axis::default()
                .bounds([0.0, (closed.len() as f64 - 1.0).max(1.0)])
                .labels(Vec::<Span>::new()),
        )
        .y_axis(
            Axis::default()
                .bounds([min_y - y_margin, max_y + y_margin])
                .labels(vec![
                    Span::styled(format!("{:.0}", min_y), Style::default().fg(theme::FG_DIM)),
                    Span::styled(format!("{:.0}", max_y), Style::default().fg(theme::FG_DIM)),
                ]),
        );

    f.render_widget(chart, area);
}

// ── Strategy positions table ─────────────────────────────────────────

fn render_strategy_positions(f: &mut Frame, area: Rect, app: &App, tag: &str) {
    let positions: Vec<&TradingPosition> = app
        .positions
        .iter()
        .filter(|p| p.strategy_id_tag == tag)
        .collect();

    let open_count = positions.iter().filter(|p| p.is_open).count();
    let title = format!(" POSITIONS ({}) ", open_count);

    if positions.is_empty() {
        let block = titled_block(&title, false);
        let p = Paragraph::new(Line::from(Span::styled(
            "  No positions",
            theme::style_dim(),
        )))
        .block(block);
        f.render_widget(p, area);
        return;
    }

    let header = Row::new(vec![
        header_cell("Instrument"),
        header_cell("Side"),
        header_cell("Qty"),
        header_cell("Entry"),
        header_cell("PnL"),
    ])
    .height(2);

    // Sort: open first, then by instrument
    let mut sorted = positions;
    sorted.sort_by(|a, b| {
        b.is_open
            .cmp(&a.is_open)
            .then(a.instrument_id.cmp(&b.instrument_id))
    });

    let rows: Vec<Row> = sorted
        .iter()
        .map(|pos| {
            let base_style = if !pos.is_open {
                theme::style_dim()
            } else {
                theme::style_data()
            };

            let inst_display = strip_venue(&pos.instrument_id);
            let inst_cell = Cell::from(inst_display.to_string())
                .style(Style::default().fg(theme::FG_IDENTIFIER));

            let side_color = match pos.side.as_str() {
                "LONG" => theme::FG_POSITIVE,
                "SHORT" => theme::FG_NEGATIVE,
                _ => theme::FG_DIM,
            };
            let side_cell = Cell::from(pos.side.clone()).style(Style::default().fg(side_color));

            let qty_cell = Cell::from(pos.quantity.clone());

            let entry_str = pos
                .avg_px_open
                .map(|px| format!("{:.2}", px))
                .unwrap_or_else(|| "-".to_string());
            let entry_cell = Cell::from(entry_str);

            let pnl_val = if pos.is_open {
                pos.unrealized_pnl
            } else {
                pos.realized_pnl
            };
            let pnl_cell = match pnl_val {
                Some(v) => Cell::from(colored_val(v, "")),
                None => Cell::from(Span::styled("-", theme::style_dim())),
            };

            Row::new(vec![inst_cell, side_cell, qty_cell, entry_cell, pnl_cell]).style(base_style)
        })
        .collect();

    let widths = [
        Constraint::Min(14),    // Instrument
        Constraint::Length(6),  // Side
        Constraint::Length(10), // Qty
        Constraint::Length(10), // Entry
        Constraint::Length(12), // PnL
    ];

    let table = Table::new(rows, widths)
        .header(header)
        .block(titled_block(&title, false));

    f.render_widget(table, area);
}

// ── Per-instrument PnL breakdown ─────────────────────────────────────

fn render_instrument_breakdown(f: &mut Frame, area: Rect, app: &App, tag: &str) {
    let block = titled_block(" INSTRUMENT PNL ", false);

    let positions: Vec<&TradingPosition> = app
        .positions
        .iter()
        .filter(|p| p.strategy_id_tag == tag)
        .collect();

    if positions.is_empty() {
        let p = Paragraph::new(Line::from(Span::styled(
            "  No data",
            theme::style_dim(),
        )))
        .block(block);
        f.render_widget(p, area);
        return;
    }

    // Group by instrument
    let mut by_instrument: BTreeMap<&str, (usize, f64, f64)> = BTreeMap::new();
    for pos in &positions {
        let inst = strip_venue(&pos.instrument_id);
        let entry = by_instrument.entry(inst).or_insert((0, 0.0, 0.0));
        if pos.is_open {
            entry.0 += 1; // open count
            entry.2 += pos.unrealized_pnl.unwrap_or(0.0); // unrealized
        }
        entry.1 += pos.realized_pnl.unwrap_or(0.0); // realized
    }

    let header = Row::new(vec![
        header_cell("Instrument"),
        header_cell("Open"),
        header_cell("Realized"),
        header_cell("Unreal."),
    ])
    .height(2);

    let rows: Vec<Row> = by_instrument
        .iter()
        .map(|(inst, (open, realized, unrealized))| {
            Row::new(vec![
                Cell::from(inst.to_string()).style(Style::default().fg(theme::FG_IDENTIFIER)),
                Cell::from(open.to_string()).style(Style::default().fg(theme::FG_PRIMARY)),
                Cell::from(colored_val(*realized, "")),
                Cell::from(colored_val(*unrealized, "")),
            ])
            .style(theme::style_data())
        })
        .collect();

    let widths = [
        Constraint::Min(12),    // Instrument
        Constraint::Length(5),  // Open
        Constraint::Length(10), // Realized
        Constraint::Length(10), // Unrealized
    ];

    let table = Table::new(rows, widths).header(header).block(block);
    f.render_widget(table, area);
}

// ── Strategy fills table ─────────────────────────────────────────────

fn render_strategy_fills(f: &mut Frame, area: Rect, app: &App, tag: &str) {
    let filtered: Vec<&TradingFill> = app
        .fills
        .iter()
        .filter(|fill| fill.strategy_id_tag.as_deref() == Some(tag))
        .take(50)
        .collect();

    let title = format!(" FILLS ({}) ", filtered.len());
    let block = titled_block(&title, false);

    if filtered.is_empty() {
        let p = Paragraph::new(Line::from(Span::styled(
            "  No fills",
            theme::style_dim(),
        )))
        .block(block);
        f.render_widget(p, area);
        return;
    }

    let header = Row::new(vec![
        header_cell("Time"),
        header_cell("Instrument"),
        header_cell("Side"),
        header_cell("Qty"),
        header_cell("Price"),
    ])
    .height(2);

    let rows: Vec<Row> = filtered
        .iter()
        .map(|fill| {
            let time_str = crate::tui::widgets::extract_time(&fill.ts_event);
            let time_cell = Cell::from(time_str).style(Style::default().fg(theme::FG_DIM));

            let inst_display = strip_venue(&fill.instrument_id);
            let inst_cell = Cell::from(inst_display.to_string())
                .style(Style::default().fg(theme::FG_IDENTIFIER));

            let side_color = match fill.order_side.as_str() {
                "BUY" => theme::FG_POSITIVE,
                "SELL" => theme::FG_NEGATIVE,
                _ => theme::FG_DIM,
            };
            let side_cell =
                Cell::from(fill.order_side.clone()).style(Style::default().fg(side_color));

            let qty_cell = Cell::from(fill.last_qty.clone());
            let price_cell = Cell::from(fill.last_px.clone());

            Row::new(vec![time_cell, inst_cell, side_cell, qty_cell, price_cell])
                .style(theme::style_data())
        })
        .collect();

    let widths = [
        Constraint::Length(9),  // Time
        Constraint::Min(12),    // Instrument
        Constraint::Length(5),  // Side
        Constraint::Length(10), // Qty
        Constraint::Length(12), // Price
    ];

    let table = Table::new(rows, widths).header(header).block(block);
    f.render_widget(table, area);
}
