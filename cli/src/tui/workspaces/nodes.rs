//! F4 — Nodes workspace: compact node cards + worker summary + live event log.

use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::Paragraph,
    Frame,
};
use ratatui_macros::{line, span};

use crate::tui::app::{App, PanelFocus};
use crate::tui::theme;
use crate::tui::widgets::{self, titled_block};

const HEARTBEAT_TIMEOUT_SECS: u64 = widgets::HEARTBEAT_TIMEOUT_SECS;

pub fn render(f: &mut Frame, area: Rect, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(7), Constraint::Min(5)])
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

    // Bottom: live event log
    render_event_log(f, chunks[1], app);
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

fn render_event_log(f: &mut Frame, area: Rect, app: &App) {
    let count = app.event_log.len();
    let title = format!(" EVENT LOG ({}) ", count);

    let block = titled_block(&title, false);

    if app.event_log.is_empty() {
        let lines = vec![Line::from(Span::styled(
            "  Waiting for events\u{2026}",
            theme::style_dim(),
        ))];
        let empty_p = Paragraph::new(lines).block(block);
        f.render_widget(empty_p, area);
        return;
    }

    // Show most recent events, newest at bottom (auto-scroll)
    let inner_h = area.height.saturating_sub(2) as usize;
    let lines: Vec<Line> = app
        .event_log
        .iter()
        .rev()
        .take(inner_h)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .map(|entry| {
            let type_color = match entry.event_type.as_str() {
                "bt.prog" => theme::FG_RUNNING,
                "bt.stat" => theme::FG_HINT,
                "bt.done" => theme::FG_POSITIVE,
                "sys.err" => theme::FG_NEGATIVE,
                _ => theme::FG_DIM,
            };
            line![
                span!(Style::default().fg(theme::FG_DIM); " {} ", entry.timestamp),
                span!(Style::default().fg(type_color); "{:<8}", entry.event_type),
                span!(Style::default().fg(theme::FG_SECONDARY); " {}", entry.message),
            ]
        })
        .collect();

    let log_p = Paragraph::new(lines).block(block);
    f.render_widget(log_p, area);
}
