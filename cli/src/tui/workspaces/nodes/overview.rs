//! Nodes overview — sandbox card + live card + risk summary + positions + fills + trading summary.

use ratatui::{
    layout::{Alignment, Constraint, Direction, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Cell, Paragraph, Row, Table},
    Frame,
};
use ratatui_macros::{line, span};
use tokio::sync::mpsc;

use crate::api::ApiClient;
use crate::tui::app::App;
use crate::tui::theme;
use crate::tui::widgets::{self, colored_val, header_cell, strip_venue, titled_block};
use crate::tui::DataCmd;

const HEARTBEAT_TIMEOUT_SECS: u64 = widgets::HEARTBEAT_TIMEOUT_SECS;

// ── Helpers ────────────────────────────────────────────────────────────

pub fn is_sandbox_online(app: &App) -> bool {
    match app.sandbox_last_heartbeat {
        Some(t) => std::time::Instant::now().duration_since(t).as_secs() < HEARTBEAT_TIMEOUT_SECS,
        None => false,
    }
}

/// Get positions filtered by current instrument filter.
fn filtered_positions(app: &App) -> Vec<(usize, &crate::types::TradingPosition)> {
    app.positions
        .iter()
        .enumerate()
        .filter(|(_, p)| {
            if let Some(ref filter) = app.sandbox_filter_instrument {
                p.instrument_id.contains(filter)
            } else {
                true
            }
        })
        .collect()
}


// ── Main render ────────────────────────────────────────────────────────

pub fn render(f: &mut Frame, area: Rect, app: &App) {
    let sandbox_online = is_sandbox_online(app);

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(7),      // Top: node cards + risk
            Constraint::Percentage(45), // Middle: positions table
            Constraint::Min(5),         // Bottom: fills + summary
        ])
        .split(area);

    // Top: sandbox card + live card + risk metrics
    let top_cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(35),
            Constraint::Percentage(30),
            Constraint::Percentage(35),
        ])
        .split(chunks[0]);

    render_sandbox_card(f, top_cols[0], app);
    render_node_card(f, top_cols[1], "LIVE", app.live_last_heartbeat, app);
    render_risk_summary(f, top_cols[2], app);

    // Middle: positions (or empty state when node stopped)
    if !sandbox_online && app.positions.is_empty() {
        render_node_stopped(f, chunks[1]);
    } else {
        render_positions(f, chunks[1], app);
    }

    // Bottom: fills + trading summary
    let bottom_cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(60),
            Constraint::Percentage(40),
        ])
        .split(chunks[2]);

    render_fills(f, bottom_cols[0], app);
    render_trading_summary(f, bottom_cols[1], app);
}

// ── Sandbox card (enhanced) ────────────────────────────────────────────

fn render_sandbox_card(f: &mut Frame, area: Rect, app: &App) {
    let now = std::time::Instant::now();
    let (status_text, status_color, dot) = if app.sandbox_shutting_down {
        ("Shutting down\u{2026}".to_string(), theme::FG_AMBER, "\u{25CF}")
    } else {
        match app.sandbox_last_heartbeat {
            Some(t) if now.duration_since(t).as_secs() < HEARTBEAT_TIMEOUT_SECS => {
                let ago = now.duration_since(t).as_secs();
                let color = widgets::pulse_color(
                    theme::FG_POSITIVE,
                    ratatui::style::Color::Rgb(0, 100, 0),
                    app.frame_count,
                );
                (format!("Online ({}s)", ago), color, "\u{25CF}") // bullet
            }
            Some(t) => {
                let ago = now.duration_since(t).as_secs();
                (format!("Stale ({}s)", ago), theme::FG_QUEUED, "\u{25D0}")
            }
            None => ("OFFLINE".to_string(), theme::FG_NEGATIVE, "\u{25CB}"),
        }
    };

    // Trading state from lifecycle_state
    let trading_state = app
        .lifecycle_state
        .as_ref()
        .and_then(|s| s.get("trading_state"))
        .and_then(|v| v.as_str())
        .unwrap_or("--");

    let ts_color = match trading_state {
        "active" => theme::FG_POSITIVE,
        "halted" => theme::FG_NEGATIVE,
        "reducing" => theme::FG_AMBER,
        _ => theme::FG_DIM,
    };

    // Strategy count from lifecycle_state
    let strategy_count = app
        .lifecycle_state
        .as_ref()
        .and_then(|s| s.get("strategy_states"))
        .and_then(|v| v.as_object())
        .map(|m| m.len())
        .unwrap_or(0);

    let paused_count = app
        .lifecycle_state
        .as_ref()
        .and_then(|s| s.get("paused"))
        .and_then(|v| v.as_array())
        .map(|a| a.len())
        .unwrap_or(0);

    let dot_color = if app.sandbox_shutting_down {
        widgets::pulse_color(
            theme::FG_AMBER,
            ratatui::style::Color::Rgb(100, 60, 0),
            app.frame_count,
        )
    } else {
        status_color
    };

    let paused_span = if paused_count > 0 {
        span!(Style::default().fg(theme::FG_QUEUED); " ({} paused)", paused_count)
    } else {
        span!("")
    };

    let lines = vec![
        line![
            span!(Style::default().fg(dot_color); " {} ", dot),
            span!(Style::default().fg(theme::FG_PRIMARY).add_modifier(Modifier::BOLD); "SANDBOX"),
        ],
        line![
            span!(Style::default().fg(theme::FG_AMBER); " Status "),
            span!(Style::default().fg(status_color); "{}", &status_text),
        ],
        line![
            span!(Style::default().fg(theme::FG_AMBER); " Trade  "),
            span!(Style::default().fg(ts_color); "{}", trading_state.to_uppercase()),
        ],
        line![
            span!(Style::default().fg(theme::FG_AMBER); " Strats "),
            span!(Style::default().fg(theme::FG_PRIMARY); "{}", strategy_count),
            paused_span,
        ],
        line![
            span!(theme::style_hint_key(); " [s]"),
            span!(theme::style_hint_desc(); " start "),
            span!(theme::style_hint_key(); "[x]"),
            span!(theme::style_hint_desc(); " stop"),
        ],
    ];

    let block = titled_block(" SANDBOX ", true);
    let card = Paragraph::new(lines).block(block);
    f.render_widget(card, area);
}

// ── Live node card (kept from original) ────────────────────────────────

fn render_node_card(
    f: &mut Frame,
    area: Rect,
    name: &str,
    last_hb: Option<std::time::Instant>,
    app: &App,
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
            (format!("Online ({}s)", ago), color, "\u{25CF}")
        }
        Some(t) => {
            let ago = now.duration_since(t).as_secs();
            (format!("Stale ({}s)", ago), theme::FG_QUEUED, "\u{25D0}")
        }
        None => ("Stopped".to_string(), theme::FG_NEGATIVE, "\u{25CB}"),
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

    let block = titled_block(&format!(" {} ", name), false);
    let card = Paragraph::new(lines).block(block);
    f.render_widget(card, area);
}

// ── Risk summary panel ─────────────────────────────────────────────────

fn render_risk_summary(f: &mut Frame, area: Rect, app: &App) {
    let block = titled_block(" RISK ", false);
    let online = is_sandbox_online(app);

    let risk = app
        .node_status
        .as_ref()
        .and_then(|s| s.get("risk_metrics"));

    let dash = "--".to_string();
    let mut lines = Vec::new();

    if online {
        let exposure = risk
            .and_then(|r| r.get("total_exposure"))
            .and_then(|v| v.as_f64());
        let margin = risk
            .and_then(|r| r.get("margin_used_pct"))
            .and_then(|v| v.as_f64());
        let leverage = risk
            .and_then(|r| r.get("leverage"))
            .and_then(|v| v.as_f64());

        lines.push(widgets::kv_line(
            " Exposure",
            &exposure
                .map(|v| format!("{:.2}", v))
                .unwrap_or(dash.clone()),
        ));
        lines.push(widgets::kv_line(
            " Margin",
            &margin
                .map(|v| format!("{:.1}%", v))
                .unwrap_or(dash.clone()),
        ));
        lines.push(widgets::kv_line(
            " Leverage",
            &leverage
                .map(|v| format!("{:.2}x", v))
                .unwrap_or(dash.clone()),
        ));
    } else {
        lines.push(widgets::kv_line(" Exposure", &dash));
        lines.push(widgets::kv_line(" Margin", &dash));
        lines.push(widgets::kv_line(" Leverage", &dash));
    }

    let p = Paragraph::new(lines).block(block);
    f.render_widget(p, area);
}

// ── Node stopped empty state ───────────────────────────────────────────

fn render_node_stopped(f: &mut Frame, area: Rect) {
    let block = titled_block(" POSITIONS ", false);
    let lines = vec![
        Line::from(""),
        Line::from(""),
        Line::from(Span::styled(
            "Sandbox node is not running",
            Style::default().fg(theme::FG_DIM),
        )),
        Line::from(Span::styled(
            "Press 's' to start",
            Style::default().fg(theme::FG_HINT),
        )),
    ];
    let p = Paragraph::new(lines)
        .alignment(Alignment::Center)
        .block(block);
    f.render_widget(p, area);
}

// ── Positions table (with Strategy column + filter) ────────────────────

fn render_positions(f: &mut Frame, area: Rect, app: &App) {
    let filter_label = app
        .sandbox_filter_instrument
        .as_deref()
        .unwrap_or("ALL");
    let positions = filtered_positions(app); // call once, reuse
    let title = if app.trading_loading {
        format!(" POSITIONS {} ", widgets::spinner(app.frame_count))
    } else {
        let open_count = positions.iter().filter(|(_, p)| p.is_open).count();
        format!(" POSITIONS ({}) [{}] ", open_count, filter_label)
    };

    if positions.is_empty() {
        let msg = if app.trading_loading {
            "  Loading\u{2026}"
        } else {
            "  No positions"
        };
        let empty = Paragraph::new(Line::from(Span::styled(msg, theme::style_dim())))
            .block(titled_block(&title, false));
        f.render_widget(empty, area);
        return;
    }

    let header = Row::new(vec![
        header_cell("Strategy"),
        header_cell("Instrument"),
        header_cell("Side"),
        header_cell("Qty"),
        header_cell("Entry"),
        header_cell("PnL"),
    ])
    .height(2);

    // Sort: open first, then by strategy, then instrument
    let mut sorted = positions;
    sorted.sort_by(|a, b| {
        b.1.is_open
            .cmp(&a.1.is_open)
            .then(a.1.strategy_id_tag.cmp(&b.1.strategy_id_tag))
            .then(a.1.instrument_id.cmp(&b.1.instrument_id))
    });

    let mut rows: Vec<Row> = Vec::new();
    let mut last_strategy: Option<&str> = None;

    // Pre-compute per-strategy PnL totals (O(n) instead of O(n*k))
    let group_pnls: std::collections::HashMap<&str, f64> = if app.sandbox_group_by_strategy {
        let mut m = std::collections::HashMap::new();
        for (_, p) in &sorted {
            *m.entry(p.strategy_id_tag.as_str()).or_insert(0.0) += p.realized_pnl.unwrap_or(0.0);
        }
        m
    } else {
        std::collections::HashMap::new()
    };

    for (i, pos) in &sorted {
        // Group-by-strategy: insert section header when strategy changes
        if app.sandbox_group_by_strategy {
            let tag = pos.strategy_id_tag.as_str();
            if last_strategy != Some(tag) {
                let group_pnl = group_pnls.get(tag).copied().unwrap_or(0.0);
                let pnl_span = colored_val(group_pnl, "");
                let section = Row::new(vec![
                    Cell::from(Span::styled(
                        format!("\u{25B8} {}", tag),
                        Style::default()
                            .fg(theme::FG_AMBER)
                            .add_modifier(Modifier::BOLD),
                    )),
                    Cell::from(""),
                    Cell::from(""),
                    Cell::from(""),
                    Cell::from(""),
                    Cell::from(pnl_span),
                ]);
                rows.push(section);
                last_strategy = Some(tag);
            }
        }

        let is_selected = *i == app.trading_selected;
        let base_style = if is_selected {
            theme::style_selected()
        } else if !pos.is_open {
            theme::style_dim()
        } else {
            theme::style_data()
        };

        // Strategy tag (short form)
        let strat_display = pos
            .strategy_id_tag
            .split('-')
            .last()
            .unwrap_or(&pos.strategy_id_tag);
        let strat_cell = Cell::from(strat_display.to_string())
            .style(Style::default().fg(theme::FG_TAG));

        // Instrument
        let inst_display = strip_venue(&pos.instrument_id);
        let inst_cell = Cell::from(inst_display.to_string())
            .style(Style::default().fg(theme::FG_IDENTIFIER));

        // Side
        let side_color = match pos.side.as_str() {
            "LONG" => theme::FG_POSITIVE,
            "SHORT" => theme::FG_NEGATIVE,
            _ => theme::FG_DIM,
        };
        let side_cell =
            Cell::from(pos.side.clone()).style(Style::default().fg(side_color));

        // Quantity
        let qty_cell = Cell::from(pos.quantity.clone());

        // Entry price
        let entry_str = pos
            .avg_px_open
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

        rows.push(
            Row::new(vec![
                strat_cell, inst_cell, side_cell, qty_cell, entry_cell, pnl_cell,
            ])
            .style(base_style),
        );
    }

    let widths = [
        Constraint::Length(8),  // Strategy
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

// ── Fills table (with Strategy column + filter) ────────────────────────

fn render_fills(f: &mut Frame, area: Rect, app: &App) {
    let filtered: Vec<&crate::types::TradingFill> = app
        .fills
        .iter()
        .filter(|fill| {
            if let Some(ref filter) = app.sandbox_filter_instrument {
                fill.instrument_id.contains(filter)
            } else {
                true
            }
        })
        .take(50)
        .collect();

    let title = format!(" RECENT FILLS ({}) ", filtered.len());
    let block = titled_block(&title, false);

    if filtered.is_empty() {
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
        header_cell("Strategy"),
        header_cell("Side"),
        header_cell("Qty"),
        header_cell("Price"),
    ])
    .height(2);

    let rows: Vec<Row> = filtered
        .iter()
        .map(|fill| {
            let time_str = widgets::extract_time(&fill.ts_event);
            let time_cell =
                Cell::from(time_str).style(Style::default().fg(theme::FG_DIM));

            let inst_display = strip_venue(&fill.instrument_id);
            let inst_cell = Cell::from(inst_display.to_string())
                .style(Style::default().fg(theme::FG_IDENTIFIER));

            let strat_display = fill
                .strategy_id_tag
                .as_deref()
                .and_then(|s| s.split('-').last())
                .unwrap_or("-");
            let strat_cell = Cell::from(strat_display.to_string())
                .style(Style::default().fg(theme::FG_TAG));

            let side_color = match fill.order_side.as_str() {
                "BUY" => theme::FG_POSITIVE,
                "SELL" => theme::FG_NEGATIVE,
                _ => theme::FG_DIM,
            };
            let side_cell =
                Cell::from(fill.order_side.clone()).style(Style::default().fg(side_color));

            let qty_cell = Cell::from(fill.last_qty.clone());
            let price_cell = Cell::from(fill.last_px.clone());

            Row::new(vec![
                time_cell, inst_cell, strat_cell, side_cell, qty_cell, price_cell,
            ])
            .style(theme::style_data())
        })
        .collect();

    let widths = [
        Constraint::Length(9),  // Time
        Constraint::Min(12),    // Instrument
        Constraint::Length(8),  // Strategy
        Constraint::Length(5),  // Side
        Constraint::Length(10), // Qty
        Constraint::Length(12), // Price
    ];

    let table = Table::new(rows, widths)
        .header(header)
        .block(titled_block(&title, false));

    f.render_widget(table, area);
}

// ── Trading summary panel ──────────────────────────────────────────────

fn render_trading_summary(f: &mut Frame, area: Rect, app: &App) {
    let block = titled_block(" SUMMARY ", false);
    let mut lines = Vec::new();

    if let Some(ref summary) = app.trading_summary {
        lines.push(widgets::kv_line(
            " Open",
            &format!("{}/{}", summary.open_positions, summary.total_positions),
        ));
        lines.push(widgets::kv_line(" Fills", &summary.total_fills.to_string()));

        let pnl_str = format!("{:.2}", summary.total_realized_pnl);
        let pnl_color = if summary.total_realized_pnl >= 0.0 {
            theme::FG_POSITIVE
        } else {
            theme::FG_NEGATIVE
        };
        lines.push(Line::from(vec![
            Span::styled(" PnL     ", Style::default().fg(theme::FG_AMBER)),
            Span::styled(pnl_str, Style::default().fg(pnl_color)),
        ]));

        // Trading state
        let ts = app
            .lifecycle_state
            .as_ref()
            .and_then(|s| s.get("trading_state"))
            .and_then(|v| v.as_str())
            .unwrap_or("--");
        let ts_color = match ts {
            "active" => theme::FG_POSITIVE,
            "halted" => theme::FG_NEGATIVE,
            "reducing" => theme::FG_AMBER,
            _ => theme::FG_DIM,
        };
        lines.push(Line::from(vec![
            Span::styled(" State   ", Style::default().fg(theme::FG_AMBER)),
            Span::styled(ts.to_uppercase(), Style::default().fg(ts_color)),
        ]));

        // Open instruments
        if !summary.open_instruments.is_empty() {
            let instr_str = summary
                .open_instruments
                .iter()
                .map(|i| strip_venue(i))
                .collect::<Vec<_>>()
                .join(", ");
            lines.push(Line::from(vec![
                Span::styled(" Instr   ", Style::default().fg(theme::FG_AMBER)),
                Span::styled(instr_str, Style::default().fg(theme::FG_IDENTIFIER)),
            ]));
        }
    } else {
        lines.push(Line::from(Span::styled(
            "  Loading\u{2026}",
            theme::style_dim(),
        )));
    }

    let p = Paragraph::new(lines).block(block);
    f.render_widget(p, area);
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
        let positions = client.list_positions(Some("sandbox"), None, None).await;
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
        let fills = client.list_fills(Some("sandbox"), 100, None).await;
        let _ = tx.send(DataCmd::Fills(fills));
    });
}
