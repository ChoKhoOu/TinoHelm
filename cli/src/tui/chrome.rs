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
use super::widgets;

/// Render the top header bar (3 rows):
/// Row 1: Mini pixel logo (TINO) + nav tabs + WS status + clock
/// Row 2: Logo row 2 + subtitle
/// Row 3: Logo row 3 + separator line
pub fn render_header(f: &mut Frame, area: Rect, app: &App) {
    use ratatui::layout::{Constraint, Direction, Layout};
    use ratatui::style::Color;

    // Fill background
    f.render_widget(
        ratatui::widgets::Block::default().style(Style::default().bg(theme::BG_HEADER)),
        area,
    );

    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),
            Constraint::Length(1),
            Constraint::Length(1),
        ])
        .split(area);

    // Mini block-char "TINO" logo (3 rows × 19 cols)
    let mini_logo: [&str; 3] = [
        "\u{2580}\u{2580}\u{2588}\u{2580}\u{2580} \u{2580}\u{2588}\u{2580} \u{2588}\u{2584} \u{2588} \u{2584}\u{2580}\u{2580}\u{2584}",
        "  \u{2588}    \u{2588}  \u{2588} \u{2580}\u{2588} \u{2588}  \u{2588}",
        "  \u{2588}   \u{2580}\u{2588}\u{2580} \u{2588}  \u{2588} \u{2580}\u{2584}\u{2584}\u{2580}",
    ];
    let logo_w = 19;
    let logo_pad = 2; // left padding

    // Gradient colors (orange-red → orange → yellow)
    let gs: (u8, u8, u8) = (230, 60, 10);
    let ge: (u8, u8, u8) = (255, 230, 50);

    // Helper: render a logo line as gradient spans
    let gradient_logo = |line: &str| -> Vec<Span<'static>> {
        let mut spans = Vec::new();
        spans.push(Span::raw(" ".repeat(logo_pad)));
        let chars: Vec<char> = line.chars().collect();
        let max_i = (logo_w - 1).max(1) as f64;
        for (i, ch) in chars.iter().enumerate() {
            if *ch == ' ' {
                spans.push(Span::raw(" "));
            } else {
                let t = i as f64 / max_i;
                let r = (gs.0 as f64 + (ge.0 as f64 - gs.0 as f64) * t) as u8;
                let g = (gs.1 as f64 + (ge.1 as f64 - gs.1 as f64) * t) as u8;
                let b = (gs.2 as f64 + (ge.2 as f64 - gs.2 as f64) * t) as u8;
                spans.push(Span::styled(
                    ch.to_string(),
                    Style::default().fg(Color::Rgb(r, g, b)).add_modifier(Modifier::BOLD),
                ));
            }
        }
        spans
    };

    let right_start = logo_pad + logo_w + 1; // after logo + 1 gap

    // ── Row 1: Logo line 1 + WS + clock (right-aligned) ────────────────
    {
        let mut spans = gradient_logo(mini_logo[0]);

        // Right-align WS + clock
        let right_needed = 16;
        let pad = (area.width as usize).saturating_sub(right_start + right_needed);
        spans.push(Span::raw(" ".repeat(pad)));

        let (ws_dot, ws_color) = match app.ws_state {
            WsState::Connected => ("\u{25CF}", widgets::pulse_color(
                theme::FG_POSITIVE, Color::Rgb(0, 100, 0), app.frame_count,
            )),
            WsState::Connecting => ("\u{25D0}", widgets::pulse_color(
                theme::FG_QUEUED, Color::Rgb(100, 80, 0), app.frame_count,
            )),
            WsState::Disconnected => ("\u{25CB}", theme::FG_NEGATIVE),
        };
        spans.push(Span::styled(ws_dot, Style::default().fg(ws_color)));
        spans.push(Span::styled(" WS ", Style::default().fg(theme::FG_DIM)));
        spans.push(Span::styled(utc_clock(), Style::default().fg(theme::FG_SECONDARY)));
        spans.push(Span::raw(" "));

        f.render_widget(
            Paragraph::new(Line::from(spans)).style(Style::default().bg(theme::BG_HEADER)),
            rows[0],
        );
    }

    // ── Row 2: Logo line 2 + nav tabs ────────────────────────────────
    {
        let mut spans = gradient_logo(mini_logo[1]);

        // Separator
        spans.push(Span::styled("  \u{2502} ", Style::default().fg(theme::FG_BORDER)));

        // Nav tabs
        let tabs = [
            ("F1", "DASH", Workspace::Dashboard),
            ("F2", "BACK", Workspace::Backtest),
            ("F3", "STRAT", Workspace::Strategy),
            ("F4", "NODE", Workspace::Nodes),
            ("F5", "DATA", Workspace::Data),
        ];

        for (i, (key, label, ws)) in tabs.iter().enumerate() {
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
                    Style::default().fg(theme::FG_AMBER).add_modifier(Modifier::BOLD),
                ));
            } else {
                spans.push(Span::styled(
                    format!(" {} ", key),
                    Style::default().fg(theme::FG_DIM),
                ));
                spans.push(Span::styled(
                    format!("{} ", label),
                    Style::default().fg(theme::FG_SECONDARY),
                ));
            }
            if i < tabs.len() - 1 {
                spans.push(Span::styled("\u{2502}", Style::default().fg(theme::FG_BORDER)));
            }
        }

        f.render_widget(
            Paragraph::new(Line::from(spans)).style(Style::default().bg(theme::BG_HEADER)),
            rows[1],
        );
    }

    // ── Row 3: Logo line 3 + "helm" + separator line ──────────────────
    {
        let mut spans = gradient_logo(mini_logo[2]);
        spans.push(Span::styled("  helm ", Style::default().fg(theme::FG_SECONDARY)));
        let sep_len = (area.width as usize).saturating_sub(right_start + 7);
        spans.push(Span::styled(
            "\u{2500}".repeat(sep_len),
            Style::default().fg(theme::FG_BORDER),
        ));

        f.render_widget(
            Paragraph::new(Line::from(spans)).style(Style::default().bg(theme::BG_HEADER)),
            rows[2],
        );
    }
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
                ("\u{2190}/\u{2192}", "panel"),
                ("n", "new"),
                ("x", "delete"),
                ("o", "report"),
                ("d", "dir"),
                ("r", "refresh"),
            ]
        }
        (None, Workspace::Strategy) => {
            vec![
                ("j/k", "nav"),
                ("\u{2190}/\u{2192}", "panel"),
                ("v", "validate"),
                ("r", "rescan"),
            ]
        }
        (None, Workspace::Nodes) => {
            vec![
                ("j/k", "nav"),
                ("Enter", "drill"),
                ("f", "filter"),
                ("g", "group"),
                ("p", "pause"),
                ("F", "flatten"),
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
    spans.push(Span::styled("Tab", theme::style_hint_key()));
    spans.push(Span::styled(" next ws ", theme::style_hint_desc()));
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
