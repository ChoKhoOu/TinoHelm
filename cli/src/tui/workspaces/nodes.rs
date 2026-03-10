//! F4 — Nodes workspace: compact node cards + positions table + fills log.

use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Cell, Paragraph, Row, Table},
    Frame,
};
use ratatui_macros::{line, span};
use tokio::sync::mpsc;

use crate::api::ApiClient;
use crate::tui::app::{App, PanelFocus};
use crate::tui::theme;
use crate::tui::widgets::{self, colored_val, header_cell, titled_block};

use super::super::DataCmd;

const HEARTBEAT_TIMEOUT_SECS: u64 = widgets::HEARTBEAT_TIMEOUT_SECS;

pub fn render(f: &mut Frame, area: Rect, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(7),   // Top: node cards
            Constraint::Percentage(40), // Middle: positions table
            Constraint::Min(5),      // Bottom: fills table
        ])
        .split(area);

    // Top: side-by-side node cards (compact) + worker summary
    let top_cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(35),
            Constraint::Percentage(35),
            Constraint::Percentage(30),
        ])
        .split(chunks[0]);

    render_node_card(f, top_cols[0], "SANDBOX", app.sandbox_last_heartbeat, app, app.panel_focus == PanelFocus::Left);
    render_node_card(f, top_cols[1], "LIVE", app.live_last_heartbeat, app, app.panel_focus == PanelFocus::Right);
    render_workers(f, top_cols[2], app);

    // Middle: positions table
    render_positions(f, chunks[1], app);

    // Bottom: fills table
    render_fills(f, chunks[2], app);
}

fn render_node_card(
    f: &mut Frame,
    area: Rect,
    name: &str,
    last_hb: Option<std::time::Instant>,
    app: &App,
    focused: bool,
) {
    let now = std::time::Instant::now();
    let (status_text, status_color, dot) = match last_hb {
        Some(t) if now.duration_since(t).as_secs() < HEARTBEAT_TIMEOUT_SECS => {
            let ago = now.duration_since(t).as_secs();
            let color = widgets::pulse_color(
                theme::FG_POSITIVE,
                ratatui::style::Color::Rgb(0, 100, 0),
                app.frame_count,
            );
            (format!("Online ({}s)", ago), color, "\u{25CF}") // ●
        }
        Some(t) => {
            let ago = now.duration_since(t).as_secs();
            (format!("Stale ({}s)", ago), theme::FG_QUEUED, "\u{25D0}") // ◐
        }
        None => ("Stopped".to_string(), theme::FG_NEGATIVE, "\u{25CB}"), // ○
    };

    let extra = app
        .node_status
        .as_ref()
        .and_then(|s| s.get("nodes"))
        .and_then(|n| n.get(name.to_lowercase().as_str()));

    let restart_count = extra
        .and_then(|info| info.get("restart_count"))
        .and_then(|v| v.as_i64())
        .unwrap_or(0);

    let lines = vec![
        line![
            span!(Style::default().fg(status_color); " {} ", dot),
            span!(Style::default().fg(theme::FG_PRIMARY).add_modifier(Modifier::BOLD); "{}", name),
        ],
        line![
            span!(Style::default().fg(theme::FG_AMBER); " Status "),
            span!(Style::default().fg(status_color); "{}", &status_text),
        ],
        line![
            span!(Style::default().fg(theme::FG_AMBER); " Restart "),
            span!(Style::default().fg(theme::FG_PRIMARY); "{}", restart_count),
        ],
        Line::from(""),
        line![
            span!(theme::style_hint_key(); " [s]"),
            span!(theme::style_hint_desc(); " start "),
            span!(theme::style_hint_key(); "[x]"),
            span!(theme::style_hint_desc(); " stop"),
        ],
    ];

    let block = titled_block(&format!(" {} ", name), focused);

    let card = Paragraph::new(lines).block(block);
    f.render_widget(card, area);
}

fn render_workers(f: &mut Frame, area: Rect, app: &App) {
    let block = titled_block(" WORKERS ", false);

    let mut lines = Vec::new();

    if let Some(workers) = app
        .node_status
        .as_ref()
        .and_then(|s| s.get("backtest_workers"))
        .and_then(|w| w.as_array())
    {
        for w in workers {
            let pid = w.get("pid").and_then(|v| v.as_i64()).unwrap_or(0);
            let alive = w
                .get("alive")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            let (dot, color) = if alive {
                ("\u{25CF}", theme::FG_POSITIVE)
            } else {
                ("\u{25CB}", theme::FG_NEGATIVE)
            };
            let status = if alive { "idle" } else { "off" };
            lines.push(line![
                span!(Style::default().fg(color); " {} ", dot),
                span!(Style::default().fg(theme::FG_PRIMARY); "w:{} ", pid),
                span!(Style::default().fg(color); "{}", status),
            ]);
        }
    }

    if lines.is_empty() {
        if app.node_loading {
            lines.push(Line::from(Span::styled(
                format!(" {} Loading\u{2026}", widgets::spinner(app.frame_count)),
                theme::style_dim(),
            )));
        } else {
            lines.push(Line::from(Span::styled(
                " r to refresh",
                theme::style_dim(),
            )));
        }
    }

    let workers_p = Paragraph::new(lines).block(block);
    f.render_widget(workers_p, area);
}

fn render_positions(f: &mut Frame, area: Rect, app: &App) {
    let title = if app.trading_loading {
        format!(" POSITIONS {} ", widgets::spinner(app.frame_count))
    } else {
        format!(" POSITIONS ({}) ", app.positions.iter().filter(|p| p.is_open).count())
    };

    let block = titled_block(&title, false);

    if app.positions.is_empty() {
        let msg = if app.trading_loading {
            "  Loading\u{2026}"
        } else {
            "  No positions"
        };
        let empty = Paragraph::new(Line::from(Span::styled(msg, theme::style_dim())))
            .block(block);
        f.render_widget(empty, area);
        return;
    }

    let header = Row::new(vec![
        header_cell("Instrument"),
        header_cell("Side"),
        header_cell("Qty"),
        header_cell("Entry"),
        header_cell("PnL"),
        header_cell("Duration"),
    ])
    .height(2);

    // Open positions first, then closed
    let mut sorted: Vec<(usize, &crate::types::TradingPosition)> =
        app.positions.iter().enumerate().collect();
    sorted.sort_by(|a, b| b.1.is_open.cmp(&a.1.is_open));

    let rows: Vec<Row> = sorted
        .iter()
        .map(|(i, pos)| {
            let is_selected = *i == app.trading_selected;
            let base_style = if is_selected {
                theme::style_selected()
            } else if !pos.is_open {
                theme::style_dim()
            } else {
                theme::style_data()
            };

            // Instrument
            let inst_display = pos.instrument_id
                .strip_suffix(".BINANCE")
                .unwrap_or(&pos.instrument_id);
            let inst_cell = Cell::from(inst_display.to_string())
                .style(Style::default().fg(theme::FG_IDENTIFIER));

            // Side
            let side_color = match pos.side.as_str() {
                "LONG" => theme::FG_POSITIVE,
                "SHORT" => theme::FG_NEGATIVE,
                _ => theme::FG_DIM,
            };
            let side_cell = Cell::from(pos.side.clone())
                .style(Style::default().fg(side_color));

            // Quantity
            let qty_cell = Cell::from(pos.quantity.clone());

            // Entry price
            let entry_str = pos.avg_px_open
                .map(|px| format!("{:.2}", px))
                .unwrap_or_else(|| "-".to_string());
            let entry_cell = Cell::from(entry_str);

            // PnL (use unrealized for open, realized for closed)
            let pnl_val = if pos.is_open {
                pos.unrealized_pnl
            } else {
                pos.realized_pnl
            };
            let pnl_cell = match pnl_val {
                Some(v) => Cell::from(colored_val(v, "")),
                None => Cell::from(Span::styled("-", theme::style_dim())),
            };

            // Duration
            let dur_str = pos.duration.as_deref().unwrap_or("-");
            let dur_cell = Cell::from(dur_str.to_string())
                .style(Style::default().fg(theme::FG_DIM));

            Row::new(vec![inst_cell, side_cell, qty_cell, entry_cell, pnl_cell, dur_cell])
                .style(base_style)
        })
        .collect();

    let widths = [
        Constraint::Min(14),     // Instrument
        Constraint::Length(6),   // Side
        Constraint::Length(10),  // Qty
        Constraint::Length(10),  // Entry
        Constraint::Length(12),  // PnL
        Constraint::Length(10),  // Duration
    ];

    let table = Table::new(rows, widths)
        .header(header)
        .block(titled_block(&title, false));

    f.render_widget(table, area);
}

fn render_fills(f: &mut Frame, area: Rect, app: &App) {
    let title = format!(" RECENT FILLS ({}) ", app.fills.len());

    let block = titled_block(&title, false);

    if app.fills.is_empty() {
        let msg = if app.trading_loading {
            "  Loading\u{2026}"
        } else {
            "  No fills"
        };
        let empty = Paragraph::new(Line::from(Span::styled(msg, theme::style_dim())))
            .block(block);
        f.render_widget(empty, area);
        return;
    }

    let header = Row::new(vec![
        header_cell("Time"),
        header_cell("Instrument"),
        header_cell("Side"),
        header_cell("Qty"),
        header_cell("Price"),
        header_cell("Fee"),
    ])
    .height(2);

    let rows: Vec<Row> = app
        .fills
        .iter()
        .take(50)
        .map(|fill| {
            // Time — extract HH:MM:SS from ts_event (ISO string or epoch)
            let time_str = extract_time(&fill.ts_event);
            let time_cell = Cell::from(time_str)
                .style(Style::default().fg(theme::FG_DIM));

            // Instrument
            let inst_display = fill.instrument_id
                .strip_suffix(".BINANCE")
                .unwrap_or(&fill.instrument_id);
            let inst_cell = Cell::from(inst_display.to_string())
                .style(Style::default().fg(theme::FG_IDENTIFIER));

            // Side
            let side_color = match fill.order_side.as_str() {
                "BUY" => theme::FG_POSITIVE,
                "SELL" => theme::FG_NEGATIVE,
                _ => theme::FG_DIM,
            };
            let side_cell = Cell::from(fill.order_side.clone())
                .style(Style::default().fg(side_color));

            // Qty
            let qty_cell = Cell::from(fill.last_qty.clone());

            // Price
            let price_cell = Cell::from(fill.last_px.clone());

            // Fee
            let fee_str = fill.commission.as_deref().unwrap_or("-");
            let fee_cell = Cell::from(fee_str.to_string())
                .style(Style::default().fg(theme::FG_SECONDARY));

            Row::new(vec![time_cell, inst_cell, side_cell, qty_cell, price_cell, fee_cell])
                .style(theme::style_data())
        })
        .collect();

    let widths = [
        Constraint::Length(9),   // Time (HH:MM:SS)
        Constraint::Min(14),     // Instrument
        Constraint::Length(5),   // Side
        Constraint::Length(10),  // Qty
        Constraint::Length(12),  // Price
        Constraint::Length(10),  // Fee
    ];

    let table = Table::new(rows, widths)
        .header(header)
        .block(titled_block(&title, false));

    f.render_widget(table, area);
}

/// Extract HH:MM:SS from a timestamp string.
/// Handles ISO 8601 ("2025-01-15T15:32:01Z") or falls back to last 8 chars.
fn extract_time(ts: &str) -> String {
    // Try ISO format: find 'T' and take the next 8 chars (HH:MM:SS)
    if let Some(t_pos) = ts.find('T') {
        let after_t = &ts[t_pos + 1..];
        // Take up to 8 chars for HH:MM:SS
        let time_part: String = after_t.chars().take(8).collect();
        if time_part.len() >= 8 {
            return time_part;
        }
    }
    // Fallback: return the string as-is (truncated)
    ts.get(..8).unwrap_or(ts).to_string()
}

// ── Data loading ───────────────────────────────────────────────────────

pub fn fire_load_positions(
    client: &ApiClient,
    app: &mut App,
    tx: &mpsc::UnboundedSender<DataCmd>,
) {
    if app.trading_loading {
        return;
    }
    app.trading_loading = true;
    let client = client.clone();
    let tx = tx.clone();
    tokio::spawn(async move {
        let positions = client.list_positions(None, None).await;
        let _ = tx.send(DataCmd::Positions(positions));
    });
}

pub fn fire_load_fills(
    client: &ApiClient,
    _app: &mut App,
    tx: &mpsc::UnboundedSender<DataCmd>,
) {
    let client = client.clone();
    let tx = tx.clone();
    tokio::spawn(async move {
        let fills = client.list_fills(None, 50).await;
        let _ = tx.send(DataCmd::Fills(fills));
    });
}
