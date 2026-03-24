//! F1 — Dashboard workspace: 4-panel tiled overview.

use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::Style,
    symbols,
    text::{Line, Span},
    widgets::{Axis, Chart, Dataset, GraphType, LineGauge, Paragraph},
    Frame,
};
use ratatui_macros::{line, span};

use crate::tui::app::App;
use crate::tui::theme;
use crate::tui::widgets::{self, titled_block};

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
        line![
            span!(Style::default().fg(theme::FG_AMBER); "  Workers "),
            span!(Style::default().fg(theme::FG_PRIMARY); "{}", worker_count),
        ],
    ];

    let p = Paragraph::new(lines).block(block);
    f.render_widget(p, area);
}

fn heartbeat_status(last_hb: Option<std::time::Instant>, now: std::time::Instant) -> (&'static str, ratatui::style::Color) {
    match last_hb {
        Some(t) if now.duration_since(t).as_secs() < widgets::HEARTBEAT_TIMEOUT_SECS => {
            ("Online", theme::FG_POSITIVE)
        }
        Some(_) => ("Stale", theme::FG_QUEUED),
        None => ("Offline", theme::FG_NEGATIVE),
    }
}

fn status_line<'a>(label: &'a str, value: &'a str, color: ratatui::style::Color) -> Line<'a> {
    let dot = if color == theme::FG_POSITIVE {
        "\u{25CF} " // ●
    } else if color == theme::FG_NEGATIVE {
        "\u{25CB} " // ○
    } else {
        "\u{25D0} " // ◐
    };
    line![
        span!(Style::default().fg(theme::FG_AMBER); "{}", label),
        span!(Style::default().fg(color); "{}", dot),
        span!(Style::default().fg(color); "{}", value),
    ]
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

        lines.push(line![
            span!(Style::default().fg(theme::FG_HINT); "  {}  ", id_short),
            span!(Style::default().fg(theme::FG_PRIMARY); "{:<14}", name),
            span!(Style::default().fg(theme::FG_SECONDARY); "{:<8}", sym),
            span!(Style::default().fg(theme::FG_SECONDARY); "{:<4}", &bt.interval),
            span!(Style::default().fg(status_color); "{} ", status_short),
            span!(Style::default().fg(pnl_color); "{}", pnl_text),
        ]);
    }

    let p = Paragraph::new(lines).block(block);
    f.render_widget(p, area);
}

fn render_active_jobs(f: &mut Frame, area: Rect, app: &App) {
    use crate::types::BacktestRunItem;

    let block = titled_block(" ACTIVE JOBS ", false);
    let inner = block.inner(area);
    f.render_widget(block, area);

    let running: Vec<&BacktestRunItem> = app
        .backtests
        .iter()
        .filter(|b| b.status.starts_with("running") || b.status == "queued")
        .collect();

    if running.is_empty() {
        let p = Paragraph::new(Span::styled("  No active jobs", theme::style_dim()));
        f.render_widget(p, inner);
        return;
    }

    let constraints: Vec<Constraint> = running
        .iter()
        .take(3)
        .map(|_| Constraint::Length(1))
        .collect();
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints(constraints)
        .split(inner);

    for (i, bt) in running.iter().take(3).enumerate() {
        let name = bt.strategy_name.as_deref().unwrap_or("-");
        let pct: u16 = bt
            .status
            .split('(')
            .nth(1)
            .and_then(|s| s.trim_end_matches("%)").parse().ok())
            .unwrap_or(0);

        let gauge = LineGauge::default()
            .label(span!(Style::default().fg(theme::FG_PRIMARY); " {:<14} {:>3}%", name, pct))
            .ratio(pct as f64 / 100.0)
            .filled_style(Style::default().fg(theme::FG_RUNNING))
            .unfilled_style(Style::default().fg(theme::FG_BORDER));

        f.render_widget(gauge, rows[i]);
    }
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

    let data: Vec<(f64, f64)> = app
        .detail_equity
        .iter()
        .enumerate()
        .map(|(i, &v)| (i as f64, v as f64))
        .collect();

    let x_max = data.len().saturating_sub(1) as f64;
    let y_min = data.iter().map(|d| d.1).fold(f64::INFINITY, f64::min);
    let y_max = data.iter().map(|d| d.1).fold(f64::NEG_INFINITY, f64::max);
    let y_pad = (y_max - y_min).max(1.0) * 0.05;

    let dataset = Dataset::default()
        .marker(symbols::Marker::Braille)
        .graph_type(GraphType::Line)
        .style(Style::default().fg(theme::FG_RUNNING))
        .data(&data);

    let chart = Chart::new(vec![dataset])
        .block(block)
        .x_axis(
            Axis::default()
                .style(Style::default().fg(theme::FG_DIM))
                .bounds([0.0, x_max]),
        )
        .y_axis(
            Axis::default()
                .style(Style::default().fg(theme::FG_DIM))
                .bounds([y_min - y_pad, y_max + y_pad])
                .labels(vec![
                    Span::styled(format!("{:.0}", y_min), Style::default().fg(theme::FG_SECONDARY)),
                    Span::styled(format!("{:.0}", y_max), Style::default().fg(theme::FG_SECONDARY)),
                ]),
        );

    f.render_widget(chart, area);
}
