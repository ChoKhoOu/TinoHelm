//! Nodes overview — Bloomberg-style trading dashboard with sidebar + four-panel grid.

use ratatui::{
    layout::{Alignment, Constraint, Layout, Rect},
    style::{Modifier, Style},
    symbols::Marker,
    text::{Line, Span},
    widgets::{Axis, Cell, Chart, Dataset, GraphType, Paragraph, Row, Table},
    Frame,
};
use tokio::sync::mpsc;

use crate::api::ApiClient;
use crate::tui::app::{App, NodePanel, NodeSidebarSection};
use crate::tui::theme;
use crate::tui::widgets::{self, colored_val, header_cell, strip_venue, titled_block};
use crate::tui::DataCmd;

const HEARTBEAT_TIMEOUT_SECS: u64 = widgets::HEARTBEAT_TIMEOUT_SECS;

// ── Helpers ────────────────────────────────────────────────────────────

fn filtered_positions(app: &App) -> Vec<(usize, &crate::types::TradingPosition)> {
    app.positions
        .iter()
        .enumerate()
        .filter(|(_, p)| match &app.selected_strategy {
            Some(tag) => p.strategy_id_tag == *tag,
            None => true,
        })
        .collect()
}

fn filtered_fills(app: &App) -> Vec<&crate::types::TradingFill> {
    app.fills
        .iter()
        .filter(|f| match &app.selected_strategy {
            Some(tag) => f.strategy_id_tag.as_deref() == Some(tag.as_str()),
            None => true,
        })
        .take(50)
        .collect()
}

fn filtered_orders(app: &App) -> Vec<(usize, &crate::types::TradingOrder)> {
    app.orders
        .iter()
        .enumerate()
        .filter(|(_, o)| {
            // Only show open orders (non-terminal status)
            let is_open = matches!(
                o.status.as_str(),
                "ACCEPTED" | "SUBMITTED" | "PARTIALLY_FILLED" | "accepted" | "submitted" | "partially_filled"
            );
            if !is_open {
                return false;
            }
            match &app.selected_strategy {
                Some(tag) => o.strategy_id_tag.as_deref() == Some(tag.as_str()),
                None => true,
            }
        })
        .collect()
}

// ── Main render ────────────────────────────────────────────────────────

pub fn render(f: &mut Frame, area: Rect, app: &App) {
    // Sidebar (18%) | Content (82%)
    let [sidebar_area, content_area] =
        Layout::horizontal([Constraint::Percentage(18), Constraint::Min(0)]).areas(area);

    render_sidebar(f, sidebar_area, app);

    // Content: Summary bar (2 lines) + Grid (fill)
    let [summary_area, grid_area] =
        Layout::vertical([Constraint::Length(2), Constraint::Min(0)]).areas(content_area);

    render_summary_bar(f, summary_area, app);

    if !app.is_active_node_online() && app.positions.is_empty() {
        render_node_stopped(f, grid_area);
        return;
    }

    // Grid: Upper (50%) / Lower (50%)
    let [upper_area, lower_area] =
        Layout::vertical([Constraint::Percentage(50), Constraint::Percentage(50)])
            .areas(grid_area);

    // Upper: Positions (55%) | PnL Chart (45%)
    let [positions_area, chart_area] =
        Layout::horizontal([Constraint::Percentage(55), Constraint::Percentage(45)])
            .areas(upper_area);

    // Lower: Fills (55%) | Orders (45%)
    let [fills_area, orders_area] =
        Layout::horizontal([Constraint::Percentage(55), Constraint::Percentage(45)])
            .areas(lower_area);

    render_positions(f, positions_area, app);
    render_pnl_chart(f, chart_area, app);
    render_fills(f, fills_area, app);
    render_orders(f, orders_area, app);
}

// ── Sidebar ────────────────────────────────────────────────────────────

fn render_sidebar(f: &mut Frame, area: Rect, app: &App) {
    let block = titled_block(" NODES ", app.node_panel_focus == NodePanel::Sidebar);

    // Split sidebar: Node selector (3 lines) | Strategy list (fill)
    let inner = block.inner(area);
    f.render_widget(block, area);

    let [node_sel_area, strat_area] =
        Layout::vertical([Constraint::Length(4), Constraint::Min(0)]).areas(inner);

    // ── Node selector ──────────────────────────────────────────────
    let nodes = [("sandbox", app.sandbox_last_heartbeat, app.sandbox_uptime.as_deref()),
                 ("live", app.live_last_heartbeat, app.live_uptime.as_deref())];

    let mut node_lines = Vec::new();
    for (i, (name, last_hb, uptime)) in nodes.iter().enumerate() {
        let online = match last_hb {
            Some(t) => std::time::Instant::now().duration_since(*t).as_secs() < HEARTBEAT_TIMEOUT_SECS,
            None => false,
        };
        let is_active = app.active_node_type == *name;
        let is_cursor = app.node_sidebar_section == NodeSidebarSection::NodeSelector
            && app.node_sidebar_idx == i
            && app.node_panel_focus == NodePanel::Sidebar;

        let (dot, dot_color) = if online {
            ("\u{25CF}", theme::FG_POSITIVE)
        } else {
            ("\u{25CB}", theme::FG_NEGATIVE)
        };

        let uptime_str = if online {
            uptime.unwrap_or("--")
        } else {
            "offline"
        };

        let bg = if is_cursor {
            theme::BG_SELECTED
        } else {
            theme::BG_PRIMARY
        };

        let name_style = if is_active {
            Style::default().fg(theme::FG_BRIGHT).bg(bg).add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(theme::FG_DIM).bg(bg)
        };

        node_lines.push(Line::from(vec![
            Span::styled(format!(" {} ", dot), Style::default().fg(dot_color).bg(bg)),
            Span::styled(name.to_uppercase(), name_style),
            Span::styled(format!(" {}", uptime_str), Style::default().fg(theme::FG_DIM).bg(bg)),
        ]));
    }

    // Divider between node selector and strategy list
    node_lines.push(Line::from(Span::styled(
        "\u{2500}".repeat(area.width.saturating_sub(2) as usize),
        Style::default().fg(theme::FG_BORDER),
    )));

    f.render_widget(Paragraph::new(node_lines), node_sel_area);

    // ── Strategy list ──────────────────────────────────────────────
    let mut strat_lines = Vec::new();

    // "ALL" entry
    {
        let is_cursor = app.node_sidebar_section == NodeSidebarSection::StrategyList
            && app.node_sidebar_idx == 0
            && app.node_panel_focus == NodePanel::Sidebar;
        let is_selected = app.selected_strategy.is_none();

        let bg = if is_cursor {
            theme::BG_SELECTED
        } else {
            theme::BG_PRIMARY
        };

        let total_pnl: f64 = app.strategy_list.iter().map(|s| s.realized_pnl).sum();

        let name_style = if is_selected {
            Style::default().fg(theme::FG_BRIGHT).bg(bg).add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(theme::FG_PRIMARY).bg(bg)
        };

        let pnl_color = if total_pnl > 0.001 {
            theme::FG_POSITIVE
        } else if total_pnl < -0.001 {
            theme::FG_NEGATIVE
        } else {
            theme::FG_PRIMARY
        };

        strat_lines.push(Line::from(vec![
            Span::styled(" ALL", name_style),
            Span::raw(" "),
            Span::styled(
                format!("{:+.2}", total_pnl),
                Style::default().fg(pnl_color).bg(bg),
            ),
        ]));
    }

    // Individual strategies
    for (i, item) in app.strategy_list.iter().enumerate() {
        let sidebar_idx = i + 1; // offset by 1 for ALL
        let is_cursor = app.node_sidebar_section == NodeSidebarSection::StrategyList
            && app.node_sidebar_idx == sidebar_idx
            && app.node_panel_focus == NodePanel::Sidebar;
        let is_selected = app.selected_strategy.as_ref() == Some(&item.tag);

        let bg = if is_cursor {
            theme::BG_SELECTED
        } else {
            theme::BG_PRIMARY
        };

        let name_style = if is_selected {
            Style::default().fg(theme::FG_BRIGHT).bg(bg).add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(theme::FG_TAG).bg(bg)
        };

        let pnl_color = if item.realized_pnl > 0.001 {
            theme::FG_POSITIVE
        } else if item.realized_pnl < -0.001 {
            theme::FG_NEGATIVE
        } else {
            theme::FG_PRIMARY
        };

        let count_str = if item.position_count > 0 {
            format!("({})", item.position_count)
        } else {
            String::new()
        };

        strat_lines.push(Line::from(vec![
            Span::styled(format!(" {}", &item.name), name_style),
            Span::styled(count_str, Style::default().fg(theme::FG_DIM).bg(bg)),
            Span::raw(" "),
            Span::styled(
                format!("{:+.2}", item.realized_pnl),
                Style::default().fg(pnl_color).bg(bg),
            ),
        ]));
    }

    if strat_lines.is_empty() {
        strat_lines.push(Line::from(Span::styled(
            " No strategies",
            Style::default().fg(theme::FG_DIM),
        )));
    }

    // ── Portfolio list section ───────────────────────────────────────
    strat_lines.push(Line::from(Span::styled(
        "\u{2500}".repeat(area.width.saturating_sub(2) as usize),
        Style::default().fg(theme::FG_BORDER),
    )));
    strat_lines.push(Line::from(Span::styled(
        " PORTFOLIOS",
        Style::default().fg(theme::FG_AMBER).add_modifier(Modifier::BOLD),
    )));

    if app.portfolio_loading {
        strat_lines.push(Line::from(Span::styled(
            format!(" {} Loading\u{2026}", widgets::spinner(app.frame_count)),
            Style::default().fg(theme::FG_DIM),
        )));
    } else if app.portfolio_list.is_empty() {
        strat_lines.push(Line::from(Span::styled(
            " No portfolios",
            Style::default().fg(theme::FG_DIM),
        )));
    } else {
        for (idx, (name, portfolio)) in app.portfolio_list.iter().enumerate() {
            let is_cursor = app.node_sidebar_section == NodeSidebarSection::PortfolioList
                && app.node_sidebar_idx == idx
                && app.node_panel_focus == NodePanel::Sidebar;
            let is_selected = app.selected_portfolio_idx == Some(idx);
            let (state_label, dot_color) = match portfolio.state.as_str() {
                "running" => ("[R]", theme::FG_RUNNING),
                "paused" => ("[P]", theme::FG_AMBER),
                "flattening" => ("[F]", theme::FG_NEGATIVE),
                "starting" => ("[S]", theme::FG_RUNNING),
                _ => ("[A]", theme::FG_DIM), // available
            };
            let was = if portfolio.was_running { " *" } else { "" };
            let bg = if is_cursor {
                theme::BG_SELECTED
            } else {
                theme::BG_PRIMARY
            };
            let name_style = if is_selected {
                Style::default().fg(theme::FG_BRIGHT).bg(bg).add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(theme::FG_PRIMARY).bg(bg)
            };
            strat_lines.push(Line::from(vec![
                Span::styled(
                    format!(" {} ", state_label),
                    Style::default().fg(dot_color).bg(bg),
                ),
                Span::styled(
                    format!("{}{} ({})", name, was, portfolio.strategy_ids.len()),
                    name_style,
                ),
            ]));
        }
    }

    f.render_widget(Paragraph::new(strat_lines), strat_area);
}

// ── Summary bar ────────────────────────────────────────────────────────

fn render_summary_bar(f: &mut Frame, area: Rect, app: &App) {
    let online = app.is_active_node_online();
    let uptime = app.active_node_uptime().unwrap_or("--");

    let (status_dot, status_color) = if online {
        ("\u{25CF}", theme::FG_POSITIVE)
    } else {
        ("\u{25CB}", theme::FG_NEGATIVE)
    };

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

    let total_pnl = app
        .trading_summary
        .as_ref()
        .map(|s| s.total_realized_pnl)
        .unwrap_or(0.0);
    let pnl_color = if total_pnl > 0.001 {
        theme::FG_POSITIVE
    } else if total_pnl < -0.001 {
        theme::FG_NEGATIVE
    } else {
        theme::FG_PRIMARY
    };

    let open_count = app
        .trading_summary
        .as_ref()
        .map(|s| s.open_positions)
        .unwrap_or(0);
    let total_count = app
        .trading_summary
        .as_ref()
        .map(|s| s.total_positions)
        .unwrap_or(0);

    // Compute win rate from closed positions
    let closed_positions: Vec<_> = app.positions.iter().filter(|p| !p.is_open).collect();
    let win_count = closed_positions.iter().filter(|p| p.realized_pnl.unwrap_or(0.0) > 0.0).count();
    let win_rate = if closed_positions.is_empty() {
        None
    } else {
        Some(win_count as f64 / closed_positions.len() as f64 * 100.0)
    };

    let filter_label = match &app.selected_strategy {
        Some(tag) => tag.split('-').last().unwrap_or(tag),
        None => "ALL",
    };

    let mut spans = vec![
        Span::styled(format!(" {} ", status_dot), Style::default().fg(status_color)),
        Span::styled(
            app.active_node_type.to_uppercase(),
            Style::default()
                .fg(theme::FG_BRIGHT)
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled(format!("  up: {}", uptime), Style::default().fg(theme::FG_DIM)),
        Span::styled("  \u{2502} ", Style::default().fg(theme::FG_BORDER)),
        Span::styled("State ", Style::default().fg(theme::FG_AMBER)),
        Span::styled(
            trading_state.to_uppercase(),
            Style::default().fg(ts_color),
        ),
        Span::styled("  \u{2502} ", Style::default().fg(theme::FG_BORDER)),
        Span::styled("PnL ", Style::default().fg(theme::FG_AMBER)),
        Span::styled(
            format!("{:+.2}", total_pnl),
            Style::default().fg(pnl_color),
        ),
        Span::styled("  \u{2502} ", Style::default().fg(theme::FG_BORDER)),
        Span::styled("WR ", Style::default().fg(theme::FG_AMBER)),
        Span::styled(
            win_rate.map(|w| format!("{:.1}%", w)).unwrap_or_else(|| "--".to_string()),
            Style::default().fg(theme::FG_PRIMARY),
        ),
        Span::styled("  \u{2502} ", Style::default().fg(theme::FG_BORDER)),
        Span::styled("Pos ", Style::default().fg(theme::FG_AMBER)),
        Span::styled(
            format!("{}/{}", open_count, total_count),
            Style::default().fg(theme::FG_PRIMARY),
        ),
        Span::styled("  \u{2502} ", Style::default().fg(theme::FG_BORDER)),
        Span::styled("Filter ", Style::default().fg(theme::FG_AMBER)),
        Span::styled(filter_label, Style::default().fg(theme::FG_TAG)),
    ];
    // Pad to fill width
    let used: usize = spans.iter().map(|s| s.content.chars().count()).sum();
    if (area.width as usize) > used {
        spans.push(Span::raw(" ".repeat(area.width as usize - used)));
    }
    let line1 = Line::from(spans);

    let line2 = Line::from(Span::styled(
        "\u{2500}".repeat(area.width as usize),
        Style::default().fg(theme::FG_BORDER),
    ));

    f.render_widget(Paragraph::new(vec![line1, line2]), area);
}

// ── Positions table ────────────────────────────────────────────────────

fn render_positions(f: &mut Frame, area: Rect, app: &App) {
    let positions = filtered_positions(app);
    let focused = app.node_panel_focus == NodePanel::Positions;

    let title = if app.trading_loading {
        format!(" POSITIONS {} ", widgets::spinner(app.frame_count))
    } else {
        let open_count = positions.iter().filter(|(_, p)| p.is_open).count();
        format!(" POSITIONS ({}) ", open_count)
    };

    if positions.is_empty() {
        let msg = if app.trading_loading {
            "  Loading\u{2026}"
        } else {
            "  No positions"
        };
        let empty = Paragraph::new(Line::from(Span::styled(msg, theme::style_dim())))
            .block(titled_block(&title, focused));
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

    let rows: Vec<Row> = sorted
        .iter()
        .map(|(i, pos)| {
            let is_selected = *i == app.trading_selected;
            let base_style = if is_selected && focused {
                theme::style_selected()
            } else if !pos.is_open {
                theme::style_dim()
            } else {
                theme::style_data()
            };

            let strat_display = pos
                .strategy_id_tag
                .split('-')
                .last()
                .unwrap_or(&pos.strategy_id_tag);
            let strat_cell =
                Cell::from(strat_display.to_string()).style(Style::default().fg(theme::FG_TAG));

            let inst_display = strip_venue(&pos.instrument_id);
            let inst_cell = Cell::from(inst_display.to_string())
                .style(Style::default().fg(theme::FG_IDENTIFIER));

            let side_color = match pos.side.as_str() {
                "LONG" => theme::FG_POSITIVE,
                "SHORT" => theme::FG_NEGATIVE,
                _ => theme::FG_DIM,
            };
            let side_cell =
                Cell::from(pos.side.clone()).style(Style::default().fg(side_color));

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

            Row::new(vec![
                strat_cell, inst_cell, side_cell, qty_cell, entry_cell, pnl_cell,
            ])
            .style(base_style)
        })
        .collect();

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
        .block(titled_block(&title, focused));

    f.render_widget(table, area);
}

// ── PnL chart ──────────────────────────────────────────────────────────

fn render_pnl_chart(f: &mut Frame, area: Rect, app: &App) {
    let focused = app.node_panel_focus == NodePanel::Chart;
    let block = titled_block(" EQUITY ", focused);

    if app.equity_curve.is_empty() {
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

    let data_points = &app.equity_curve;

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
        .data(data_points)];

    let x_max = (data_points.len() as f64 - 1.0).max(1.0);

    let chart = Chart::new(datasets)
        .block(titled_block(" EQUITY ", focused))
        .x_axis(
            Axis::default()
                .bounds([0.0, x_max])
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

// ── Fills table ────────────────────────────────────────────────────────

fn render_fills(f: &mut Frame, area: Rect, app: &App) {
    let filtered = filtered_fills(app);
    let focused = app.node_panel_focus == NodePanel::Fills;

    let title = format!(" RECENT FILLS ({}) ", filtered.len());
    let block = titled_block(&title, focused);

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
        .enumerate()
        .map(|(i, fill)| {
            let is_selected = i == app.fills_selected;
            let base_style = if is_selected && focused {
                theme::style_selected()
            } else {
                theme::style_data()
            };

            let time_str = widgets::extract_time(&fill.ts_event);
            let time_cell = Cell::from(time_str).style(Style::default().fg(theme::FG_DIM));

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
            .style(base_style)
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
        .block(titled_block(&title, focused));

    f.render_widget(table, area);
}

// ── Orders table ───────────────────────────────────────────────────────

fn render_orders(f: &mut Frame, area: Rect, app: &App) {
    let orders = filtered_orders(app);
    let focused = app.node_panel_focus == NodePanel::Orders;

    let title = if app.orders_loading {
        format!(" ORDERS {} ", widgets::spinner(app.frame_count))
    } else {
        format!(" ORDERS ({}) ", orders.len())
    };

    let block = titled_block(&title, focused);

    if orders.is_empty() {
        let msg = if app.orders_loading {
            "  Loading\u{2026}"
        } else {
            "  No open orders"
        };
        let empty = Paragraph::new(Line::from(Span::styled(msg, theme::style_dim())))
            .block(block);
        f.render_widget(empty, area);
        return;
    }

    let header = Row::new(vec![
        header_cell("Type"),
        header_cell("Side"),
        header_cell("Instrument"),
        header_cell("Qty"),
        header_cell("Price"),
    ])
    .height(2);

    let rows: Vec<Row> = orders
        .iter()
        .map(|(i, order)| {
            let is_selected = *i == app.orders_selected;
            let base_style = if is_selected && focused {
                theme::style_selected()
            } else {
                theme::style_data()
            };

            let type_cell = Cell::from(order.order_type.clone())
                .style(Style::default().fg(theme::FG_TAG));

            let side_color = match order.side.as_str() {
                "BUY" => theme::FG_POSITIVE,
                "SELL" => theme::FG_NEGATIVE,
                _ => theme::FG_DIM,
            };
            let side_cell =
                Cell::from(order.side.clone()).style(Style::default().fg(side_color));

            let inst_display = strip_venue(&order.instrument_id);
            let inst_cell = Cell::from(inst_display.to_string())
                .style(Style::default().fg(theme::FG_IDENTIFIER));

            let qty_cell = Cell::from(order.quantity.clone());

            let price_str = order.price.as_deref().unwrap_or("-").to_string();
            let price_cell = Cell::from(price_str);

            Row::new(vec![type_cell, side_cell, inst_cell, qty_cell, price_cell])
                .style(base_style)
        })
        .collect();

    let widths = [
        Constraint::Length(10), // Type
        Constraint::Length(5),  // Side
        Constraint::Min(12),    // Instrument
        Constraint::Length(10), // Qty
        Constraint::Length(12), // Price
    ];

    let table = Table::new(rows, widths)
        .header(header)
        .block(titled_block(&title, focused));

    f.render_widget(table, area);
}

// ── Node stopped empty state ───────────────────────────────────────────

fn render_node_stopped(f: &mut Frame, area: Rect) {
    let block = titled_block(" TRADING DASHBOARD ", false);
    let lines = vec![
        Line::from(""),
        Line::from(""),
        Line::from(Span::styled(
            "Node is not running",
            Style::default().fg(theme::FG_DIM),
        )),
        Line::from(Span::styled(
            "Start via: docker compose --profile sandbox up -d",
            Style::default().fg(theme::FG_HINT),
        )),
    ];
    let p = Paragraph::new(lines)
        .alignment(Alignment::Center)
        .block(block);
    f.render_widget(p, area);
}

// ── Data loading ───────────────────────────────────────────────────────

pub fn fire_load_portfolios(
    client: &ApiClient,
    app: &mut App,
    tx: &mpsc::UnboundedSender<DataCmd>,
) {
    if app.portfolio_loading {
        return;
    }
    app.portfolio_loading = true;
    let client = client.clone();
    let tx = tx.clone();
    let mode = app.active_node_type.clone();
    tokio::spawn(async move {
        match client.list_portfolios(&mode).await {
            Ok(resp) => {
                let mut list: Vec<(String, _)> = resp.portfolios.into_iter().collect();
                list.sort_by(|a, b| a.0.cmp(&b.0));
                let _ = tx.send(DataCmd::PortfolioList(list));
            }
            Err(_) => {
                let _ = tx.send(DataCmd::PortfolioList(vec![]));
            }
        }
    });
}

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
    let node_type = app.active_node_type.clone();
    tokio::spawn(async move {
        let positions = client.list_positions(Some(&node_type), None, None).await;
        let _ = tx.send(DataCmd::Positions(positions));
    });
}

pub fn fire_load_fills(
    client: &ApiClient,
    app: &mut App,
    tx: &mpsc::UnboundedSender<DataCmd>,
) {
    let client = client.clone();
    let tx = tx.clone();
    let node_type = app.active_node_type.clone();
    tokio::spawn(async move {
        let fills = client.list_fills(Some(&node_type), 100, None).await;
        let _ = tx.send(DataCmd::Fills(fills));
    });
}

pub fn fire_load_orders(
    client: &ApiClient,
    app: &mut App,
    tx: &mpsc::UnboundedSender<DataCmd>,
) {
    if app.orders_loading {
        return;
    }
    app.orders_loading = true;
    let client = client.clone();
    let tx = tx.clone();
    let node_type = app.active_node_type.clone();
    tokio::spawn(async move {
        let orders = client.list_orders(Some(&node_type), None, 100).await;
        let _ = tx.send(DataCmd::Orders(orders));
    });
}
