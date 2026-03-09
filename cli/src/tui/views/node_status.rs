use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Paragraph},
    Frame,
};

use crate::tui::app::App;

const HEARTBEAT_TIMEOUT_SECS: u64 = 30;

pub fn render(f: &mut Frame, area: Rect, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(8), Constraint::Length(8), Constraint::Min(1)])
        .split(area);

    // Sandbox node
    render_node(f, chunks[0], "Sandbox", app.sandbox_last_heartbeat, app);

    // Live node
    render_node(f, chunks[1], "Live", app.live_last_heartbeat, app);

    // Additional status from API (workers, risk metrics)
    let api_lines = if let Some(ref status) = app.node_status {
        let mut lines = Vec::new();
        if let Some(workers) = status.get("backtest_workers") {
            if let Some(arr) = workers.as_array() {
                lines.push(Line::from(vec![
                    Span::styled("Backtest Workers: ", Style::default().fg(Color::Yellow)),
                    Span::raw(format!("{}", arr.len())),
                ]));
                for w in arr {
                    let pid = w.get("pid").and_then(|v| v.as_i64()).unwrap_or(0);
                    let alive = w.get("alive").and_then(|v| v.as_bool()).unwrap_or(false);
                    let dot = if alive { "●" } else { "○" };
                    let color = if alive { Color::Green } else { Color::Red };
                    lines.push(Line::from(vec![
                        Span::raw("  "),
                        Span::styled(dot, Style::default().fg(color)),
                        Span::raw(format!(" Worker (pid: {})", pid)),
                    ]));
                }
            }
        }
        if lines.is_empty() {
            lines.push(Line::from("No additional status data"));
        }
        lines
    } else if app.node_loading {
        vec![Line::from("Loading...")]
    } else {
        vec![Line::from("Press 'r' to fetch status")]
    };

    let api_status = Paragraph::new(api_lines)
        .block(Block::default().borders(Borders::ALL).title(" Workers "));
    f.render_widget(api_status, chunks[2]);
}

fn render_node(
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
            (format!("Online ({}s ago)", ago), Color::Green, "●")
        }
        Some(t) => {
            let ago = now.duration_since(t).as_secs();
            (format!("Stale ({}s ago)", ago), Color::Yellow, "◐")
        }
        None => ("Stopped".to_string(), Color::Red, "○"),
    };

    // Extract extra info from node_status API response
    let extra = app
        .node_status
        .as_ref()
        .and_then(|s| s.get("nodes"))
        .and_then(|n| n.get(name.to_lowercase().as_str()));

    let mut lines = vec![
        Line::from(vec![
            Span::styled(
                format!(" {} ", dot),
                Style::default().fg(status_color),
            ),
            Span::styled(
                format!("{} Node", name),
                Style::default().add_modifier(Modifier::BOLD),
            ),
        ]),
        Line::from(vec![
            Span::styled("  Status:    ", Style::default().fg(Color::Yellow)),
            Span::styled(status_text, Style::default().fg(status_color)),
        ]),
    ];

    if let Some(info) = extra {
        if let Some(pid) = info.get("pid").and_then(|v| v.as_i64()) {
            lines.push(Line::from(vec![
                Span::styled("  PID:       ", Style::default().fg(Color::Yellow)),
                Span::raw(format!("{}", pid)),
            ]));
        }
        if let Some(restarts) = info.get("restart_count").and_then(|v| v.as_i64()) {
            lines.push(Line::from(vec![
                Span::styled("  Restarts:  ", Style::default().fg(Color::Yellow)),
                Span::raw(format!("{}", restarts)),
            ]));
        }
    }

    let block = Block::default()
        .borders(Borders::ALL)
        .title(format!(" {} ", name));
    let paragraph = Paragraph::new(lines).block(block);
    f.render_widget(paragraph, area);
}

pub fn hints() -> Vec<(&'static str, &'static str)> {
    vec![("r", "refresh")]
}
