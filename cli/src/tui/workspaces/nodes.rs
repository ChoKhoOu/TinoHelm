//! F4 — Nodes workspace: Sandbox/Live side-by-side + worker table.

use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Paragraph},
    Frame,
};

use crate::tui::app::App;
use crate::tui::theme;

const HEARTBEAT_TIMEOUT_SECS: u64 = 30;

pub fn render(f: &mut Frame, area: Rect, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(10), Constraint::Min(5)])
        .split(area);

    // Top: side-by-side node cards
    let node_cols = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
        .split(chunks[0]);

    render_node_card(f, node_cols[0], "SANDBOX", app.sandbox_last_heartbeat, app);
    render_node_card(f, node_cols[1], "LIVE", app.live_last_heartbeat, app);

    // Bottom: worker status
    render_workers(f, chunks[1], app);
}

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
            (format!("Online ({}s)", ago), theme::FG_POSITIVE, "\u{25CF}") // ●
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

    let mut lines = vec![
        Line::from(""),
        Line::from(vec![
            Span::styled(
                format!("  {} ", dot),
                Style::default().fg(status_color),
            ),
            Span::styled(
                name,
                Style::default()
                    .fg(theme::FG_PRIMARY)
                    .add_modifier(Modifier::BOLD),
            ),
        ]),
        Line::from(vec![
            Span::styled("  Status  ", Style::default().fg(theme::FG_AMBER)),
            Span::styled(&status_text, Style::default().fg(status_color)),
        ]),
    ];

    if let Some(info) = extra {
        if let Some(pid) = info.get("pid").and_then(|v| v.as_i64()) {
            lines.push(Line::from(vec![
                Span::styled("  PID     ", Style::default().fg(theme::FG_AMBER)),
                Span::styled(format!("{}", pid), Style::default().fg(theme::FG_PRIMARY)),
            ]));
        }
        if let Some(restarts) = info.get("restart_count").and_then(|v| v.as_i64()) {
            lines.push(Line::from(vec![
                Span::styled("  Restart ", Style::default().fg(theme::FG_AMBER)),
                Span::styled(format!("{}", restarts), Style::default().fg(theme::FG_PRIMARY)),
            ]));
        }
    }

    // Action hints
    lines.push(Line::from(""));
    lines.push(Line::from(vec![
        Span::styled("  [s]", theme::style_hint_key()),
        Span::styled(" start  ", theme::style_hint_desc()),
        Span::styled("[x]", theme::style_hint_key()),
        Span::styled(" stop", theme::style_hint_desc()),
    ]));

    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::style_border())
        .title(Span::styled(
            format!(" {} ", name),
            theme::style_header(),
        ));

    let p = Paragraph::new(lines).block(block);
    f.render_widget(p, area);
}

fn render_workers(f: &mut Frame, area: Rect, app: &App) {
    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::style_border())
        .title(Span::styled(" BACKTEST WORKERS ", theme::style_header()));

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
                ("\u{25CF}", theme::FG_POSITIVE) // ●
            } else {
                ("\u{25CB}", theme::FG_NEGATIVE) // ○
            };
            let status = if alive { "idle" } else { "offline" };
            lines.push(Line::from(vec![
                Span::styled(format!("  {} ", dot), Style::default().fg(color)),
                Span::styled(
                    format!("Worker (pid: {})  ", pid),
                    Style::default().fg(theme::FG_PRIMARY),
                ),
                Span::styled(status, Style::default().fg(color)),
            ]));
        }
    }

    if lines.is_empty() {
        if app.node_loading {
            lines.push(Line::from(Span::styled(
                "  Loading\u{2026}",
                theme::style_dim(),
            )));
        } else {
            lines.push(Line::from(Span::styled(
                "  Press 'r' to fetch status",
                theme::style_dim(),
            )));
        }
    }

    let p = Paragraph::new(lines).block(block);
    f.render_widget(p, area);
}
