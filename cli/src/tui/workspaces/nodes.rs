//! F4 — Nodes workspace: compact node cards + worker summary + live event log.

use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::Paragraph,
    Frame,
};

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

    let restart_count = extra
        .and_then(|info| info.get("restart_count"))
        .and_then(|v| v.as_i64())
        .unwrap_or(0);

    let lines = vec![
        Line::from(vec![
            Span::styled(
                format!(" {} ", dot),
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
            Span::styled(" Status ", Style::default().fg(theme::FG_AMBER)),
            Span::styled(&status_text, Style::default().fg(status_color)),
        ]),
        Line::from(vec![
            Span::styled(" Restart ", Style::default().fg(theme::FG_AMBER)),
            Span::styled(format!("{}", restart_count), Style::default().fg(theme::FG_PRIMARY)),
        ]),
        Line::from(""),
        Line::from(vec![
            Span::styled(" [s]", theme::style_hint_key()),
            Span::styled(" start ", theme::style_hint_desc()),
            Span::styled("[x]", theme::style_hint_key()),
            Span::styled(" stop", theme::style_hint_desc()),
        ]),
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
            lines.push(Line::from(vec![
                Span::styled(format!(" {} ", dot), Style::default().fg(color)),
                Span::styled(format!("w:{} ", pid), Style::default().fg(theme::FG_PRIMARY)),
                Span::styled(status, Style::default().fg(color)),
            ]));
        }
    }

    if lines.is_empty() {
        if app.node_loading {
            lines.push(Line::from(Span::styled(
                " Loading\u{2026}",
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
            Line::from(vec![
                Span::styled(
                    format!(" {} ", entry.timestamp),
                    Style::default().fg(theme::FG_DIM),
                ),
                Span::styled(
                    format!("{:<8}", entry.event_type),
                    Style::default().fg(type_color),
                ),
                Span::styled(
                    format!(" {}", entry.message),
                    Style::default().fg(theme::FG_SECONDARY),
                ),
            ])
        })
        .collect();

    let log_p = Paragraph::new(lines).block(block);
    f.render_widget(log_p, area);
}
