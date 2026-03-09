//! F1 — Dashboard workspace: 4-panel tiled overview.

use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    prelude::Stylize,
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Paragraph, Sparkline},
    Frame,
};

use crate::tui::app::App;
use crate::tui::theme;
use crate::tui::widgets::{self, titled_block};

const HEARTBEAT_TIMEOUT_SECS: u64 = widgets::HEARTBEAT_TIMEOUT_SECS;

pub fn render(f: &mut Frame, area: Rect, app: &App) {
    // Split into top half + active jobs + equity curve
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(10), // top panels
            Constraint::Length(5),  // active jobs
            Constraint::Min(6),    // equity curve
        ])
        .split(area);

    // Top half: System Status (left 30%) | Recent Backtests (right 70%)
    let top_cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(30), Constraint::Percentage(70)])
        .split(rows[0]);

    render_system_status(f, top_cols[0], app);
    render_recent_backtests(f, top_cols[1], app);
    render_active_jobs(f, rows[1], app);
    render_equity_curve(f, rows[2], app);
}

fn render_system_status(f: &mut Frame, area: Rect, app: &App) {
    let block = titled_block(" SYSTEM STATUS ", false);

    let now = std::time::Instant::now();

    let sandbox_status = heartbeat_status(app.sandbox_last_heartbeat, now);
    let live_status = heartbeat_status(app.live_last_heartbeat, now);
    let ws_status = match app.ws_state {
        crate::tui::app::WsState::Connected => ("Connected", theme::FG_POSITIVE),
        crate::tui::app::WsState::Connecting => ("Connecting", theme::FG_QUEUED),
        crate::tui::app::WsState::Disconnected => ("Disconnected", theme::FG_NEGATIVE),
    };

    let worker_count = app
        .node_status
        .as_ref()
        .and_then(|s| s.get("backtest_workers"))
        .and_then(|w| w.as_array())
        .map(|a| {
            let alive = a.iter().filter(|w| w.get("alive").and_then(|v| v.as_bool()).unwrap_or(false)).count();
            format!("{}/{}", alive, a.len())
        })
        .unwrap_or_else(|| "-/-".to_string());

    let lines = vec![
        Line::from(""),
        status_line("  Sandbox ", sandbox_status.0, sandbox_status.1),
        status_line("  Live    ", live_status.0, live_status.1),
        status_line("  WS      ", ws_status.0, ws_status.1),
        Line::from(vec![
            Span::styled("  Workers ", Style::default().fg(theme::FG_AMBER)),
            Span::styled(worker_count, Style::default().fg(theme::FG_PRIMARY)),
        ]),
    ];

    let p = Paragraph::new(lines).block(block);
    f.render_widget(p, area);
}

fn heartbeat_status(last_hb: Option<std::time::Instant>, now: std::time::Instant) -> (&'static str, ratatui::style::Color) {
    match last_hb {
        Some(t) if now.duration_since(t).as_secs() < HEARTBEAT_TIMEOUT_SECS => {
            ("Online", theme::FG_POSITIVE)
        }
        Some(_) => ("Stale", theme::FG_QUEUED),
        None => ("Offline", theme::FG_NEGATIVE),
    }
}

fn status_line<'a>(label: &'a str, value: &'a str, color: ratatui::style::Color) -> Line<'a> {
    let (dot, _) = if color == theme::FG_POSITIVE {
        ("\u{25CF} ", color) // ●
    } else if color == theme::FG_NEGATIVE {
        ("\u{25CB} ", color) // ○
    } else {
        ("\u{25D0} ", color) // ◐
    };
    Line::from(vec![
        Span::styled(label, Style::default().fg(theme::FG_AMBER)),
        Span::styled(dot, Style::default().fg(color)),
        Span::styled(value, Style::default().fg(color)),
    ])
}

fn render_recent_backtests(f: &mut Frame, area: Rect, app: &App) {
    let block = titled_block(" RECENT BACKTESTS ", false);

    let mut lines = vec![Line::from("")];
    let display_count = (area.height as usize).saturating_sub(3).min(app.backtests.len());

    if app.backtests.is_empty() {
        lines.push(Line::from(Span::styled(
            "  No backtests yet. Press F2 → n to create one.",
            theme::style_dim(),
        )));
    }

    for bt in app.backtests.iter().take(display_count) {
        let id_short = bt.run_id.get(..6).unwrap_or(&bt.run_id);
        let name = bt.strategy_name.as_deref().unwrap_or("-");
        let sym = bt.symbol.get(..6).unwrap_or(&bt.symbol);

        let status_color = theme::status_color(&bt.status);
        let status_short = if bt.status == "completed" {
            "\u{2713}" // ✓
        } else if bt.status == "failed" {
            "\u{2717}" // ✗
        } else if bt.status.starts_with("running") {
            "\u{29D7}" // ⧗
        } else if bt.status == "queued" {
            "\u{2026}" // …
        } else {
            "?"
        };

        // Extract PnL from result_summary if available
        let pnl_text = bt
            .result_summary
            .as_ref()
            .and_then(|s| s.get("total_pnl_pct").or_else(|| s.get("total_pnl")))
            .and_then(|v| v.as_f64())
            .map(|v| format!("{:+.1}%", v))
            .unwrap_or_default();

        let pnl_color = if pnl_text.starts_with('+') {
            theme::FG_POSITIVE
        } else if pnl_text.starts_with('-') {
            theme::FG_NEGATIVE
        } else {
            theme::FG_DIM
        };

        lines.push(Line::from(vec![
            Span::styled(format!("  {}  ", id_short), Style::default().fg(theme::FG_HINT)),
            Span::styled(format!("{:<14}", name), Style::default().fg(theme::FG_PRIMARY)),
            Span::styled(format!("{:<8}", sym), Style::default().fg(theme::FG_SECONDARY)),
            Span::styled(format!("{:<4}", &bt.interval), Style::default().fg(theme::FG_SECONDARY)),
            Span::styled(format!("{} ", status_short), Style::default().fg(status_color)),
            Span::styled(pnl_text, Style::default().fg(pnl_color)),
        ]));
    }

    let p = Paragraph::new(lines).block(block);
    f.render_widget(p, area);
}

fn render_active_jobs(f: &mut Frame, area: Rect, app: &App) {
    let block = titled_block(" ACTIVE JOBS ", false);

    let running: Vec<&BacktestRunItem> = app
        .backtests
        .iter()
        .filter(|b| b.status.starts_with("running") || b.status == "queued")
        .collect();

    use crate::types::BacktestRunItem;

    let mut lines = Vec::new();
    if running.is_empty() {
        lines.push(Line::from(Span::styled(
            "  No active jobs",
            theme::style_dim(),
        )));
    }

    for bt in running.iter().take(3) {
        let name = bt.strategy_name.as_deref().unwrap_or("-");
        // Parse percentage from status like "running (67%)"
        let pct: u16 = bt
            .status
            .split('(')
            .nth(1)
            .and_then(|s| s.trim_end_matches("%)").parse().ok())
            .unwrap_or(0);

        let bar_width = (area.width as usize).saturating_sub(40);
        let filled = (bar_width as u16 * pct / 100) as usize;
        let empty = bar_width.saturating_sub(filled);

        lines.push(Line::from(vec![
            Span::styled(format!("  {:<14}", name), Style::default().fg(theme::FG_PRIMARY)),
            Span::styled(
                "\u{2593}".repeat(filled), // ▓
                Style::default().fg(theme::FG_RUNNING),
            ),
            Span::styled(
                "\u{2591}".repeat(empty), // ░
                Style::default().fg(theme::FG_BORDER),
            ),
            Span::styled(format!(" {:>3}%", pct), Style::default().fg(theme::FG_PRIMARY)),
        ]));
    }

    let p = Paragraph::new(lines).block(block);
    f.render_widget(p, area);
}

fn render_equity_curve(f: &mut Frame, area: Rect, app: &App) {
    let block = titled_block(" EQUITY CURVE ", false);

    if app.detail_equity.is_empty() {
        let p = Paragraph::new(Line::from(Span::styled(
            "  Select a completed backtest to view equity curve",
            theme::style_dim(),
        )))
        .block(block);
        f.render_widget(p, area);
        return;
    }

    let sparkline = Sparkline::default()
        .block(block)
        .data(&app.detail_equity)
        .style(Style::default().fg(theme::FG_RUNNING))
        .add_modifier(Modifier::BOLD);

    f.render_widget(sparkline, area);
}
