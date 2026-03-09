//! Global chrome: top header bar and bottom hint bar.

use ratatui::{
    layout::Rect,
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::Paragraph,
    Frame,
};

use super::app::{utc_clock, App, PopupKind, Workspace, WsState};
use super::theme;

/// Render the top header bar: [TINO]HELM + workspace tabs + WS dot + clock.
pub fn render_header(f: &mut Frame, area: Rect, app: &App) {
    let mut spans = Vec::new();

    // Brand logo
    spans.push(Span::styled(
        " [TINO]",
        Style::default()
            .fg(theme::FG_LOGO)
            .add_modifier(Modifier::BOLD),
    ));
    spans.push(Span::styled(
        "HELM ",
        Style::default()
            .fg(theme::FG_AMBER)
            .add_modifier(Modifier::BOLD),
    ));

    // Workspace tabs
    let tabs = [
        ("F1", "DASH", Workspace::Dashboard),
        ("F2", "BACK", Workspace::Backtest),
        ("F3", "STRAT", Workspace::Strategy),
        ("F4", "NODE", Workspace::Nodes),
        ("F5", "DATA", Workspace::Data),
    ];

    for (key, label, ws) in &tabs {
        let is_active = app.workspace == *ws;
        if is_active {
            spans.push(Span::styled(
                format!(" {} ", key),
                Style::default()
                    .fg(theme::BG_PRIMARY)
                    .bg(theme::FG_AMBER)
                    .add_modifier(Modifier::BOLD),
            ));
            spans.push(Span::styled(
                format!("{} ", label),
                Style::default()
                    .fg(theme::FG_AMBER)
                    .add_modifier(Modifier::BOLD),
            ));
        } else {
            spans.push(Span::styled(
                format!(" {} ", key),
                Style::default().fg(theme::FG_DIM),
            ));
            spans.push(Span::styled(
                format!("{} ", label),
                Style::default().fg(theme::FG_DIM),
            ));
        }
    }

    // Right side: WS status + clock
    let ws_text = area.width as usize;
    let left_len = 10 + tabs.len() * 10; // approximate
    let pad = ws_text.saturating_sub(left_len + 16);
    spans.push(Span::raw(" ".repeat(pad)));

    // WS connection dot
    let (ws_dot, ws_color) = match app.ws_state {
        WsState::Connected => ("\u{25CF}", theme::FG_POSITIVE),    // ●
        WsState::Connecting => ("\u{25D0}", theme::FG_QUEUED),     // ◐
        WsState::Disconnected => ("\u{25CB}", theme::FG_NEGATIVE), // ○
    };
    spans.push(Span::styled(ws_dot, Style::default().fg(ws_color)));
    spans.push(Span::styled("WS ", Style::default().fg(theme::FG_DIM)));

    // UTC clock
    spans.push(Span::styled(
        utc_clock(),
        Style::default().fg(theme::FG_SECONDARY),
    ));
    spans.push(Span::raw(" "));

    let line = Line::from(spans);
    let p = Paragraph::new(line).style(Style::default().bg(theme::BG_HEADER));
    f.render_widget(p, area);
}

/// Render the bottom hint bar with context-sensitive key shortcuts.
pub fn render_hints(f: &mut Frame, area: Rect, app: &App) {
    let view_hints = match (&app.popup, &app.workspace) {
        (Some(PopupKind::BacktestForm), _) | (Some(PopupKind::DataFetchForm), _) => {
            vec![
                ("Tab", "next"),
                ("Enter", "submit"),
                ("Esc", "cancel"),
            ]
        }
        (Some(PopupKind::Help), _) => {
            vec![("Esc", "close")]
        }
        (Some(PopupKind::Confirm { .. }), _) => {
            vec![("y", "confirm"), ("n/Esc", "cancel")]
        }
        (None, Workspace::Dashboard) => {
            vec![
                ("j/k", "nav"),
                ("Enter", "detail"),
                ("r", "refresh"),
            ]
        }
        (None, Workspace::Backtest) => {
            vec![
                ("j/k", "nav"),
                ("Enter", "detail"),
                ("n", "new"),
                ("Tab", "panel"),
                ("r", "refresh"),
            ]
        }
        (None, Workspace::Strategy) => {
            vec![
                ("j/k", "nav"),
                ("Tab", "panel"),
                ("v", "validate"),
                ("r", "rescan"),
            ]
        }
        (None, Workspace::Nodes) => {
            vec![
                ("s", "start"),
                ("x", "stop"),
                ("r", "refresh"),
            ]
        }
        (None, Workspace::Data) => {
            vec![
                ("j/k", "nav"),
                ("f", "fetch"),
                ("r", "refresh"),
            ]
        }
    };

    let mut spans = Vec::new();
    spans.push(Span::styled(" \u{25B8} ", Style::default().fg(theme::FG_AMBER))); // ▸

    for (key, desc) in &view_hints {
        spans.push(Span::styled(*key, theme::style_hint_key()));
        spans.push(Span::styled(format!(" {} ", desc), theme::style_hint_desc()));
        spans.push(Span::styled("\u{2502}", theme::style_dim())); // │
        spans.push(Span::raw(" "));
    }

    // Global hints
    spans.push(Span::styled("F1-F5", theme::style_hint_key()));
    spans.push(Span::styled(" ws ", theme::style_hint_desc()));
    spans.push(Span::styled("\u{2502}", theme::style_dim()));
    spans.push(Span::raw(" "));
    spans.push(Span::styled("?", theme::style_hint_key()));
    spans.push(Span::styled(" help ", theme::style_hint_desc()));
    spans.push(Span::styled("\u{2502}", theme::style_dim()));
    spans.push(Span::raw(" "));
    spans.push(Span::styled("q", theme::style_hint_key()));
    spans.push(Span::styled(" quit", theme::style_hint_desc()));

    let line = Line::from(spans);
    let p = Paragraph::new(line).style(Style::default().bg(theme::BG_HEADER));
    f.render_widget(p, area);
}

/// Render the error banner (if present).
pub fn render_error(f: &mut Frame, area: Rect, app: &App) {
    if let Some(ref msg) = app.error_banner {
        let line = Line::from(vec![
            Span::styled(" ERROR ", theme::style_error()),
            Span::raw(" "),
            Span::styled(msg.as_str(), Style::default().fg(theme::FG_NEGATIVE)),
        ]);
        f.render_widget(Paragraph::new(line), area);
    }
}
