pub mod app;
pub mod chrome;
pub mod theme;
pub mod views;
pub mod widgets;
pub mod workspaces;
pub mod ws;

use std::io;
use std::time::Duration;

use anyhow::Result;
use crossterm::{
    event::{self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyModifiers},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    backend::CrosstermBackend,
    layout::{Constraint, Direction, Layout},
    style::Style,
    Terminal,
};
use tokio::sync::mpsc;

use crate::api::ApiClient;
use crate::types::{BacktestRunList, Strategy, TradingFill, TradingPosition, TradingSummary};
use app::{App, PopupKind, Workspace, WsState};
use ws::WsClientEvent;

/// Async data command — results from background API calls.
pub(crate) enum DataCmd {
    Backtests(anyhow::Result<BacktestRunList>),
    Strategies(anyhow::Result<Vec<Strategy>>),
    NodeStatus(anyhow::Result<serde_json::Value>),
    DataCatalog(anyhow::Result<serde_json::Value>),
    DetailResult(anyhow::Result<serde_json::Value>),
    Positions(anyhow::Result<Vec<TradingPosition>>),
    Fills(anyhow::Result<Vec<TradingFill>>),
    TradingSummary(anyhow::Result<TradingSummary>),
}

/// Run the interactive TUI dashboard.
pub async fn run(client: ApiClient) -> Result<()> {
    // Install panic hook that restores the terminal
    let original_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |panic_info| {
        let _ = disable_raw_mode();
        let _ = execute!(io::stdout(), LeaveAlternateScreen, DisableMouseCapture);
        original_hook(panic_info);
    }));

    // Setup terminal
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let result = run_app(&mut terminal, client).await;

    // Restore terminal
    disable_raw_mode()?;
    execute!(
        terminal.backend_mut(),
        LeaveAlternateScreen,
        DisableMouseCapture
    )?;
    terminal.show_cursor()?;

    result
}

async fn run_app(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    client: ApiClient,
) -> Result<()> {
    let mut app = App::new();

    // Start WebSocket client in background
    let (ws_tx, mut ws_rx) = mpsc::unbounded_channel::<WsClientEvent>();
    let ws_url = client.ws_url();
    tokio::spawn(async move {
        ws::run_ws_client(ws_url, ws_tx).await;
    });

    // Async data loading channel
    let (data_tx, mut data_rx) = mpsc::unbounded_channel::<DataCmd>();

    // Load initial data (non-blocking)
    fire_load_backtests(&client, &mut app, &data_tx);

    loop {
        let tick_rate = Duration::from_millis(app.tick_rate_ms());

        // Render
        terminal.draw(|f| render(f, &app))?;

        // Multiplex: terminal events, WS events, data results, tick timer
        tokio::select! {
            _ = tokio::task::spawn_blocking({
                let tick = tick_rate;
                move || event::poll(tick)
            }) => {
                while event::poll(Duration::ZERO)? {
                    if let Event::Key(key) = event::read()? {
                        if handle_key(&mut app, &client, &data_tx, key.code, key.modifiers).await {
                            return Ok(());
                        }
                    }
                }
            }

            Some(ws_event) = ws_rx.recv() => {
                match ws_event {
                    WsClientEvent::Connecting => {
                        app.ws_state = WsState::Connecting;
                        app.push_log("ws", "connecting\u{2026}".to_string());
                    }
                    WsClientEvent::Connected => {
                        app.ws_state = WsState::Connected;
                        app.ws_reconnect_secs = None;
                        app.push_log("ws", "connected".to_string());
                    }
                    WsClientEvent::Disconnected { retry_secs } => {
                        app.ws_state = WsState::Disconnected;
                        app.ws_reconnect_secs = Some(retry_secs);
                        app.push_log("ws", format!("disconnected, retry in {}s", retry_secs));
                    }
                    WsClientEvent::Event(event) => {
                        app.handle_ws_event(event);
                    }
                }
            }

            Some(cmd) = data_rx.recv() => {
                handle_data_cmd(&mut app, cmd);
            }
        }

        // Refresh positions/fills when dirty flag set by WS events
        if app.trading_dirty && app.workspace == Workspace::Nodes {
            app.trading_dirty = false;
            workspaces::nodes::fire_load_positions(&client, &mut app, &data_tx);
            workspaces::nodes::fire_load_fills(&client, &mut app, &data_tx);
        }

        app.tick();

        if !app.running {
            return Ok(());
        }
    }
}

/// Master render function.
fn render(f: &mut ratatui::Frame, app: &App) {
    let size = f.area();

    // Global layout: [header(3)] [gap(1)] [content(fill)] [hints(1)] [error?(1)]
    let has_error = app.error_banner.is_some();
    let constraints = if has_error {
        vec![
            Constraint::Length(3),
            Constraint::Length(1),
            Constraint::Min(5),
            Constraint::Length(1),
            Constraint::Length(1),
        ]
    } else {
        vec![
            Constraint::Length(3),
            Constraint::Length(1),
            Constraint::Min(5),
            Constraint::Length(1),
        ]
    };

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints(constraints)
        .split(size);

    // Set black background on entire frame
    f.render_widget(
        ratatui::widgets::Block::default().style(Style::default().bg(theme::BG_PRIMARY)),
        size,
    );

    // Boot animation: use full screen, skip header/hints/error
    if !app.boot_complete {
        render_boot(f, size, app);
    } else {
        // Header bar
        chrome::render_header(f, chunks[0], app);

        // chunks[1] is the gap — left as blank

        match app.workspace {
            Workspace::Dashboard => workspaces::dashboard::render(f, chunks[2], app),
            Workspace::Backtest => workspaces::backtest::render(f, chunks[2], app),
            Workspace::Strategy => workspaces::strategy::render(f, chunks[2], app),
            Workspace::Nodes => workspaces::nodes::render(f, chunks[2], app),
            Workspace::Data => workspaces::data::render(f, chunks[2], app),
        }

        // Hint bar
        chrome::render_hints(f, chunks[3], app);

        // Error banner
        if has_error {
            chrome::render_error(f, chunks[4], app);
        }
    }

    // Popup overlay (rendered last, on top of everything)
    if let Some(ref popup) = app.popup {
        render_popup(f, size, app, popup);
    }
}

/// Boot animation — CRT warm-up + pixel logo reveal.
///
/// Phase 0: CRT off (black)
/// Phase 1: CRT warm-up — bright horizontal scanline at center
/// Phase 2: Scanlines spread from center outward
/// Phase 3: Pixel logo appears with block-char frame
/// Phase 4: Subtitle fades in
/// Phase 5: "SYSTEM ONLINE" ready indicator
fn render_boot(f: &mut ratatui::Frame, area: ratatui::layout::Rect, app: &App) {
    use ratatui::layout::{Alignment, Rect};
    use ratatui::style::Color;
    use ratatui::text::{Line, Span};
    use ratatui::widgets::Paragraph;

    let phase = app.boot_phase;
    let w = area.width as usize;
    let h = area.height as usize;
    let mid_y = area.y + area.height / 2;

    // Phase 0: CRT off — pure black
    if phase == 0 {
        return;
    }

    // Phase 1: CRT warm-up — gradient horizontal scanline at center
    if phase == 1 {
        // Gradient: orange-red → orange → yellow
        let gs: (u8, u8, u8) = (230, 60, 10);
        let ge: (u8, u8, u8) = (255, 230, 50);
        let max_i = w.saturating_sub(1).max(1) as f64;

        // Dim glow line above
        if mid_y > area.y {
            let glow: Vec<Span> = (0..w).map(|i| {
                let t = i as f64 / max_i;
                let r = (gs.0 as f64 * 0.35 + (ge.0 as f64 * 0.35 - gs.0 as f64 * 0.35) * t) as u8;
                let g = (gs.1 as f64 * 0.35 + (ge.1 as f64 * 0.35 - gs.1 as f64 * 0.35) * t) as u8;
                let b = (gs.2 as f64 * 0.35 + (ge.2 as f64 * 0.35 - gs.2 as f64 * 0.35) * t) as u8;
                Span::styled("\u{2591}", Style::default().fg(Color::Rgb(r, g, b)))
            }).collect();
            f.render_widget(
                Paragraph::new(Line::from(glow)),
                Rect::new(area.x, mid_y - 1, area.width, 1),
            );
        }

        // Main scanline with gradient
        let line: Vec<Span> = (0..w).map(|i| {
            let t = i as f64 / max_i;
            let r = (gs.0 as f64 + (ge.0 as f64 - gs.0 as f64) * t) as u8;
            let g = (gs.1 as f64 + (ge.1 as f64 - gs.1 as f64) * t) as u8;
            let b = (gs.2 as f64 + (ge.2 as f64 - gs.2 as f64) * t) as u8;
            Span::styled("\u{2501}", Style::default().fg(Color::Rgb(r, g, b)))
        }).collect();
        f.render_widget(
            Paragraph::new(Line::from(line)),
            Rect::new(area.x, mid_y, area.width, 1),
        );

        // Dim glow line below
        if mid_y + 1 < area.y + area.height {
            let glow: Vec<Span> = (0..w).map(|i| {
                let t = i as f64 / max_i;
                let r = (gs.0 as f64 * 0.35 + (ge.0 as f64 * 0.35 - gs.0 as f64 * 0.35) * t) as u8;
                let g = (gs.1 as f64 * 0.35 + (ge.1 as f64 * 0.35 - gs.1 as f64 * 0.35) * t) as u8;
                let b = (gs.2 as f64 * 0.35 + (ge.2 as f64 * 0.35 - gs.2 as f64 * 0.35) * t) as u8;
                Span::styled("\u{2591}", Style::default().fg(Color::Rgb(r, g, b)))
            }).collect();
            f.render_widget(
                Paragraph::new(Line::from(glow)),
                Rect::new(area.x, mid_y + 1, area.width, 1),
            );
        }
        return;
    }

    // Phase 2+: CRT scanline ambient background
    {
        let spread = if phase == 2 { (h / 3).max(4) } else { h };
        let center = h / 2;
        let mut lines = Vec::with_capacity(h);
        for row in 0..h {
            let dist = if row >= center { row - center } else { center - row };
            if dist < spread && row % 2 == 0 {
                lines.push(Line::from(Span::styled(
                    "\u{2591}".repeat(w),
                    Style::default().fg(Color::Rgb(20, 15, 0)),
                )));
            } else {
                lines.push(Line::from(""));
            }
        }
        f.render_widget(Paragraph::new(lines), area);
    }

    if phase == 2 {
        return;
    }

    // Phase 3+: Pixel logo with block-character frame (centered)
    {
        // Block-art pixel font: 3 rows, ~42 cols
        //  T(5)  I(3)  N(4)   O(4)   H(4)  E(4)   L(4)   M(5)
        let logo_lines: &[&str] = &[
            "\u{2580}\u{2580}\u{2588}\u{2580}\u{2580} \u{2580}\u{2588}\u{2580} \u{2588}\u{2584} \u{2588} \u{2584}\u{2580}\u{2580}\u{2584} \u{2588}  \u{2588} \u{2588}\u{2580}\u{2580}\u{2580} \u{2588}    \u{2588}\u{2584}\u{2580}\u{2584}\u{2588}",
            "  \u{2588}    \u{2588}  \u{2588} \u{2580}\u{2588} \u{2588}  \u{2588} \u{2588}\u{2580}\u{2580}\u{2588} \u{2588}\u{2580}\u{2580}  \u{2588}    \u{2588} \u{2580} \u{2588}",
            "  \u{2588}   \u{2580}\u{2588}\u{2580} \u{2588}  \u{2588} \u{2580}\u{2584}\u{2584}\u{2580} \u{2588}  \u{2588} \u{2580}\u{2580}\u{2580}\u{2580} \u{2580}\u{2580}\u{2580}\u{2580} \u{2588}   \u{2588}",
        ];

        let logo_w = 40_u16;
        let frame_w = (logo_w + 6).min(area.width); // +6 for "█  " + "  █" padding
        let frame_h = 7_u16; // top bar + blank + 3 logo rows + blank + bottom bar
        let frame_y = mid_y.saturating_sub(frame_h / 2 + 2);
        let frame_x = area.x + area.width.saturating_sub(frame_w) / 2;

        let clamped_y = frame_y.max(area.y);
        let avail_h = (area.y + area.height).saturating_sub(clamped_y);
        let frame_rect = Rect::new(frame_x, clamped_y, frame_w, frame_h.min(avail_h));

        // Clear background behind frame
        f.render_widget(
            ratatui::widgets::Block::default()
                .style(Style::default().bg(theme::BG_PRIMARY)),
            frame_rect,
        );

        // Build frame lines
        let fw = frame_w as usize;
        let mut frame_lines = Vec::new();

        // Top border: gradient ░▒▓████...████▓▒░
        frame_lines.push(pixel_gradient_line(fw));

        // Blank line inside frame
        frame_lines.push(pixel_frame_blank(fw));

        // Logo lines (centered inside frame, orange→yellow horizontal gradient)
        let grad_start: (u8, u8, u8) = (230, 60, 10);    // orange-red
        let grad_end: (u8, u8, u8) = (255, 230, 50);     // warm yellow
        for logo_row in logo_lines {
            let logo_len = unicode_width(logo_row);
            let pad = fw.saturating_sub(logo_len + 4) / 2;
            let mut spans = vec![
                Span::styled("\u{2588} ", Style::default().fg(Color::Rgb(40, 30, 0))),
                Span::raw(" ".repeat(pad)),
            ];
            // Per-character gradient
            let chars: Vec<char> = logo_row.chars().collect();
            let max_i = chars.len().saturating_sub(1).max(1) as f64;
            for (i, ch) in chars.iter().enumerate() {
                let t = i as f64 / max_i;
                let r = (grad_start.0 as f64 + (grad_end.0 as f64 - grad_start.0 as f64) * t) as u8;
                let g = (grad_start.1 as f64 + (grad_end.1 as f64 - grad_start.1 as f64) * t) as u8;
                let b = (grad_start.2 as f64 + (grad_end.2 as f64 - grad_start.2 as f64) * t) as u8;
                if *ch == ' ' {
                    spans.push(Span::raw(" "));
                } else {
                    spans.push(Span::styled(
                        ch.to_string(),
                        Style::default().fg(Color::Rgb(r, g, b)),
                    ));
                }
            }
            spans.push(Span::raw(" ".repeat(fw.saturating_sub(logo_len + 4 + pad))));
            spans.push(Span::styled(" \u{2588}", Style::default().fg(Color::Rgb(40, 30, 0))));
            frame_lines.push(Line::from(spans));
        }

        // Blank line inside frame
        frame_lines.push(pixel_frame_blank(fw));

        // Bottom border
        frame_lines.push(pixel_gradient_line(fw));

        f.render_widget(Paragraph::new(frame_lines), frame_rect);
    }

    // Phase 4+: Subtitle text
    if phase >= 4 {
        let sub_y = mid_y + 2;
        if sub_y < area.y + area.height {
            f.render_widget(
                Paragraph::new(Line::from(Span::styled(
                    "Quantitative Trading Terminal",
                    Style::default().fg(theme::FG_DIM),
                )))
                .alignment(Alignment::Center),
                Rect::new(area.x, sub_y, area.width, 1),
            );
        }
    }

    // Phase 5+: System Online ready indicator
    if phase >= 5 {
        let ready_y = mid_y + 4;
        if ready_y < area.y + area.height {
            f.render_widget(
                Paragraph::new(Line::from(vec![
                    Span::styled(
                        "\u{25CF} ",
                        Style::default().fg(theme::FG_POSITIVE),
                    ),
                    Span::styled(
                        "SYSTEM ONLINE",
                        Style::default().fg(theme::FG_POSITIVE),
                    ),
                ]))
                .alignment(Alignment::Center),
                Rect::new(area.x, ready_y, area.width, 1),
            );
        }
    }
}

/// Pixel gradient border line: ░▒▓██████...██████▓▒░
fn pixel_gradient_line(w: usize) -> ratatui::text::Line<'static> {
    use ratatui::style::Color;
    use ratatui::text::Span;
    let fill = w.saturating_sub(6);
    ratatui::text::Line::from(vec![
        Span::styled("\u{2591}\u{2592}\u{2593}", Style::default().fg(Color::Rgb(80, 55, 0))),
        Span::styled(
            "\u{2588}".repeat(fill),
            Style::default().fg(Color::Rgb(40, 30, 0)),
        ),
        Span::styled("\u{2593}\u{2592}\u{2591}", Style::default().fg(Color::Rgb(80, 55, 0))),
    ])
}

/// Blank line with pixel frame borders: █ ... █
fn pixel_frame_blank(w: usize) -> ratatui::text::Line<'static> {
    use ratatui::style::Color;
    use ratatui::text::Span;
    let inner = w.saturating_sub(4);
    ratatui::text::Line::from(vec![
        Span::styled("\u{2588} ", Style::default().fg(Color::Rgb(40, 30, 0))),
        Span::raw(" ".repeat(inner)),
        Span::styled(" \u{2588}", Style::default().fg(Color::Rgb(40, 30, 0))),
    ])
}

/// Count display width for ASCII/simple Unicode strings.
fn unicode_width(s: &str) -> usize {
    s.chars().count()
}

/// Render a popup overlay.
fn render_popup(
    f: &mut ratatui::Frame,
    area: ratatui::layout::Rect,
    app: &App,
    popup: &PopupKind,
) {
    use ratatui::style::Color;
    use ratatui::text::Span;
    use ratatui::widgets::{Block, Borders, Clear, Paragraph};

    // Dim the entire background to create depth
    f.render_widget(
        Block::default().style(Style::default().bg(Color::Rgb(0, 0, 0))),
        area,
    );

    // Determine popup size
    let (width, height) = match popup {
        PopupKind::BacktestForm => (50, 24),
        PopupKind::DataFetchForm => (50, 14),
        PopupKind::Help => (60, 20),
        PopupKind::Confirm { .. } => (50, 8),
    };

    let popup_area = centered_rect(width, height, area);

    // Clear the popup area
    f.render_widget(Clear, popup_area);

    match popup {
        PopupKind::BacktestForm => render_backtest_form(f, popup_area, app),
        PopupKind::Help => render_help(f, popup_area),
        PopupKind::Confirm { message } => render_confirm(f, popup_area, message),
        PopupKind::DataFetchForm => {
            // Placeholder
            let p = Paragraph::new("Data fetch form (TODO)")
                .block(
                    Block::default()
                        .borders(Borders::ALL)
                        .border_style(theme::style_border_focused())
                        .title(Span::styled(" FETCH DATA ", theme::style_header())),
                )
                .style(Style::default().bg(theme::BG_PANEL));
            f.render_widget(p, popup_area);
        }
    }
}

fn render_backtest_form(f: &mut ratatui::Frame, area: ratatui::layout::Rect, app: &App) {
    use ratatui::text::{Line, Span};
    use ratatui::widgets::{Block, Borders, Paragraph};

    let fields = [
        ("Strategy", &app.form_strategy),
        ("Symbol  ", &app.form_symbol),
        ("Interval", &app.form_interval),
        ("Start   ", &app.form_start),
        ("End     ", &app.form_end),
    ];

    let mut lines = vec![Line::from("")];
    for (i, (label, value)) in fields.iter().enumerate() {
        let is_focused = i == app.form_focus;
        let label_style = if is_focused {
            Style::default().fg(theme::FG_AMBER)
        } else {
            Style::default().fg(theme::FG_DIM)
        };
        let value_style = if is_focused {
            Style::default().fg(theme::FG_BRIGHT)
        } else {
            Style::default().fg(theme::FG_PRIMARY)
        };
        let cursor = if is_focused { "\u{2588}" } else { "" }; // █

        lines.push(Line::from(vec![
            Span::styled(format!("  {} ", label), label_style),
            Span::styled("[", Style::default().fg(theme::FG_BORDER)),
            Span::styled(value.as_str(), value_style),
            Span::styled(cursor, Style::default().fg(theme::FG_CURSOR)),
            Span::styled("]", Style::default().fg(theme::FG_BORDER)),
        ]));

        // Show strategy suggestions below the Strategy field
        if i == 0 && is_focused && !app.form_suggestions.is_empty() {
            let max_visible = 5;
            let total = app.form_suggestions.len();
            let sel = app.form_suggestion_idx;
            // Scroll window to keep selected item visible
            let start = if sel >= max_visible {
                sel - max_visible + 1
            } else {
                0
            };
            let end = (start + max_visible).min(total);

            if start > 0 {
                lines.push(Line::from(Span::styled(
                    format!("              \u{25B4} {} above", start), // ▴
                    Style::default().fg(theme::FG_DIM),
                )));
            }
            for j in start..end {
                let name = &app.form_suggestions[j];
                let is_sel = j == sel;
                let (prefix, style) = if is_sel {
                    ("\u{25B8} ", Style::default().fg(theme::FG_AMBER)) // ▸
                } else {
                    ("  ", Style::default().fg(theme::FG_DIM))
                };
                lines.push(Line::from(Span::styled(
                    format!("            {}{}", prefix, name),
                    style,
                )));
            }
            if end < total {
                lines.push(Line::from(Span::styled(
                    format!("              \u{25BE} {} more", total - end), // ▾
                    Style::default().fg(theme::FG_DIM),
                )));
            }
        } else {
            lines.push(Line::from(""));
        }
    }

    lines.push(Line::from(""));
    let hint_line = if app.form_focus == 0 {
        Line::from(vec![
            Span::styled("  \u{2191}\u{2193}", theme::style_hint_key()),
            Span::styled(" select  ", theme::style_hint_desc()),
            Span::styled("Enter", theme::style_hint_key()),
            Span::styled(" accept  ", theme::style_hint_desc()),
            Span::styled("Tab", theme::style_hint_key()),
            Span::styled(" next  ", theme::style_hint_desc()),
            Span::styled("Esc", theme::style_hint_key()),
            Span::styled(" cancel", theme::style_hint_desc()),
        ])
    } else {
        Line::from(vec![
            Span::styled("  Tab", theme::style_hint_key()),
            Span::styled(" next  ", theme::style_hint_desc()),
            Span::styled("Enter", theme::style_hint_key()),
            Span::styled(" submit  ", theme::style_hint_desc()),
            Span::styled("Esc", theme::style_hint_key()),
            Span::styled(" cancel", theme::style_hint_desc()),
        ])
    };
    lines.push(hint_line);

    let p = Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::style_border_focused())
                .title(Span::styled(" NEW BACKTEST ", theme::style_header())),
        )
        .style(Style::default().bg(theme::BG_PANEL));
    f.render_widget(p, area);
}

fn render_help(f: &mut ratatui::Frame, area: ratatui::layout::Rect) {
    use ratatui::text::{Line, Span};
    use ratatui::widgets::{Block, Borders, Paragraph};

    let lines = vec![
        Line::from(""),
        Line::from(Span::styled("  KEYBOARD SHORTCUTS", theme::style_header())),
        Line::from(""),
        help_line("  F1-F5 / 1-5", "Switch workspace"),
        help_line("  j / \u{2193}     ", "Move cursor down"),
        help_line("  k / \u{2191}     ", "Move cursor up"),
        help_line("  Enter      ", "Open detail / select"),
        help_line("  Tab        ", "Switch panel focus"),
        help_line("  n          ", "New backtest (F2)"),
        help_line("  r          ", "Refresh / rescan"),
        help_line("  v          ", "Validate strategy (F3)"),
        help_line("  s / x      ", "Start / stop node (F4)"),
        help_line("  f          ", "Fetch data (F5)"),
        help_line("  ?          ", "Toggle this help"),
        help_line("  q / Ctrl+C ", "Quit"),
        Line::from(""),
        Line::from(Span::styled(
            "  Press Esc to close",
            theme::style_dim(),
        )),
    ];

    let p = Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::style_border_focused())
                .title(Span::styled(" HELP ", theme::style_header())),
        )
        .style(Style::default().bg(theme::BG_PANEL));
    f.render_widget(p, area);
}

fn render_confirm(f: &mut ratatui::Frame, area: ratatui::layout::Rect, message: &str) {
    use ratatui::text::{Line, Span};
    use ratatui::widgets::{Block, Borders, Paragraph};

    let lines = vec![
        Line::from(""),
        Line::from(Span::styled(
            format!("  {}", message),
            Style::default().fg(theme::FG_PRIMARY),
        )),
        Line::from(""),
        Line::from(vec![
            Span::styled("  y", theme::style_hint_key()),
            Span::styled(" confirm  ", theme::style_hint_desc()),
            Span::styled("n/Esc", theme::style_hint_key()),
            Span::styled(" cancel", theme::style_hint_desc()),
        ]),
    ];

    let p = Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::style_border_focused())
                .title(Span::styled(" CONFIRM ", theme::style_header())),
        )
        .style(Style::default().bg(theme::BG_PANEL));
    f.render_widget(p, area);
}

fn help_line<'a>(key: &'a str, desc: &'a str) -> ratatui::text::Line<'a> {
    ratatui::text::Line::from(vec![
        ratatui::text::Span::styled(key, theme::style_hint_key()),
        ratatui::text::Span::styled(format!("  {}", desc), theme::style_hint_desc()),
    ])
}

/// Create a centered rect of a given size within the parent.
fn centered_rect(width: u16, height: u16, parent: ratatui::layout::Rect) -> ratatui::layout::Rect {
    let x = parent.x + parent.width.saturating_sub(width) / 2;
    let y = parent.y + parent.height.saturating_sub(height) / 2;
    ratatui::layout::Rect::new(
        x,
        y,
        width.min(parent.width),
        height.min(parent.height),
    )
}

/// Handle a key press. Returns true if the app should quit.
async fn handle_key(
    app: &mut App,
    client: &ApiClient,
    tx: &mpsc::UnboundedSender<DataCmd>,
    code: KeyCode,
    modifiers: KeyModifiers,
) -> bool {
    // Ctrl+C always quits
    if code == KeyCode::Char('c') && modifiers.contains(KeyModifiers::CONTROL) {
        return true;
    }

    // Any key during boot → skip boot
    if !app.boot_complete {
        app.boot_complete = true;
        return false;
    }

    // Popup key handling
    if app.popup.is_some() {
        return handle_popup_key(app, client, tx, code).await;
    }

    match code {
        // Quit
        KeyCode::Char('q') => return true,

        // Workspace switching — F-keys
        KeyCode::F(1) | KeyCode::Char('1') => {
            app.switch_workspace(Workspace::Dashboard);
            fire_load_backtests(client, app, tx);
            fire_load_node_status(client, app, tx);
        }
        KeyCode::F(2) | KeyCode::Char('2') => {
            app.switch_workspace(Workspace::Backtest);
            fire_load_backtests(client, app, tx);
        }
        KeyCode::F(3) | KeyCode::Char('3') => {
            app.switch_workspace(Workspace::Strategy);
            fire_load_strategies(client, app, tx);
        }
        KeyCode::F(4) | KeyCode::Char('4') => {
            app.switch_workspace(Workspace::Nodes);
            fire_load_node_status(client, app, tx);
            workspaces::nodes::fire_load_positions(client, app, tx);
            workspaces::nodes::fire_load_fills(client, app, tx);
        }
        KeyCode::F(5) | KeyCode::Char('5') => {
            app.switch_workspace(Workspace::Data);
            fire_load_data_catalog(client, app, tx);
        }

        // Help
        KeyCode::Char('?') => {
            app.open_popup(PopupKind::Help);
        }

        // Tab — cycle to next workspace
        KeyCode::Tab => {
            let next = match app.workspace {
                Workspace::Dashboard => Workspace::Backtest,
                Workspace::Backtest => Workspace::Strategy,
                Workspace::Strategy => Workspace::Nodes,
                Workspace::Nodes => Workspace::Data,
                Workspace::Data => Workspace::Dashboard,
            };
            app.switch_workspace(next);
            fire_load_workspace_data(client, app, tx);
        }

        // Shift+Tab — cycle to previous workspace
        KeyCode::BackTab => {
            let prev = match app.workspace {
                Workspace::Dashboard => Workspace::Data,
                Workspace::Backtest => Workspace::Dashboard,
                Workspace::Strategy => Workspace::Backtest,
                Workspace::Nodes => Workspace::Strategy,
                Workspace::Data => Workspace::Nodes,
            };
            app.switch_workspace(prev);
            fire_load_workspace_data(client, app, tx);
        }

        // Left/Right — toggle panel focus in split views
        KeyCode::Left => {
            app.panel_focus = app::PanelFocus::Left;
        }
        KeyCode::Right => {
            app.panel_focus = app::PanelFocus::Right;
        }

        // Navigation — j/k with context-sensitive behavior
        KeyCode::Char('j') | KeyCode::Down => {
            // Right panel focused in Backtest → scroll detail
            if app.workspace == Workspace::Backtest
                && app.panel_focus == app::PanelFocus::Right
            {
                app.detail_scroll = app.detail_scroll.saturating_add(1);
            } else {
                let prev_sel = app.backtest_selected;
                handle_nav_down(app);
                if app.workspace == Workspace::Backtest && app.backtest_selected != prev_sel {
                    app.detail_scroll = 0;
                    fire_load_detail_result(client, app, tx);
                }
            }
        }
        KeyCode::Char('k') | KeyCode::Up => {
            if app.workspace == Workspace::Backtest
                && app.panel_focus == app::PanelFocus::Right
            {
                app.detail_scroll = app.detail_scroll.saturating_sub(1);
            } else {
                let prev_sel = app.backtest_selected;
                handle_nav_up(app);
                if app.workspace == Workspace::Backtest && app.backtest_selected != prev_sel {
                    app.detail_scroll = 0;
                    fire_load_detail_result(client, app, tx);
                }
            }
        }

        // Enter — open detail / load detail data
        KeyCode::Enter => {
            if app.workspace == Workspace::Backtest && !app.backtests.is_empty() {
                fire_load_detail_result(client, app, tx);
                app.panel_focus = app::PanelFocus::Right;
            }
        }

        // New backtest
        KeyCode::Char('n') => {
            if app.workspace == Workspace::Backtest || app.workspace == Workspace::Dashboard {
                if app.strategies.is_empty() {
                    fire_load_strategies(client, app, tx);
                }
                app.init_backtest_form();
                app.open_popup(PopupKind::BacktestForm);
            }
        }

        // Refresh
        KeyCode::Char('r') => match app.workspace {
            Workspace::Dashboard => {
                fire_load_backtests(client, app, tx);
                fire_load_node_status(client, app, tx);
            }
            Workspace::Backtest => {
                fire_load_backtests(client, app, tx);
            }
            Workspace::Strategy => {
                // Rescan strategies (action — keep blocking), then refresh list
                let _ = client.rescan_strategies().await;
                fire_load_strategies(client, app, tx);
            }
            Workspace::Nodes => {
                fire_load_node_status(client, app, tx);
                workspaces::nodes::fire_load_positions(client, app, tx);
                workspaces::nodes::fire_load_fills(client, app, tx);
            }
            Workspace::Data => {
                fire_load_data_catalog(client, app, tx);
            }
        },

        // Delete backtest (with confirmation)
        KeyCode::Char('x') | KeyCode::Delete => {
            if app.workspace == Workspace::Backtest {
                if let Some(bt) = app.backtests.get(app.backtest_selected) {
                    if bt.status == "running" || bt.status == "queued" {
                        app.set_error("Cannot delete a running/queued backtest — cancel it first".to_string());
                    } else {
                        let id_short = bt.run_id.get(..8).unwrap_or(&bt.run_id);
                        let strategy = bt.strategy_name.as_deref().unwrap_or("?");
                        app.pending_action = Some(app::PendingAction::DeleteBacktest {
                            run_id: bt.run_id.clone(),
                        });
                        app.open_popup(PopupKind::Confirm {
                            message: format!("Delete backtest #{}  ({}) ?", id_short, strategy),
                        });
                    }
                }
            } else if app.workspace == Workspace::Nodes {
                let node_type = match app.panel_focus {
                    app::PanelFocus::Left => "sandbox",
                    app::PanelFocus::Right => "live",
                };
                match client.node_stop(node_type).await {
                    Ok(_) => {
                        app.push_alert(
                            app::AlertKind::Info,
                            format!("{} node stopping…", node_type),
                        );
                        fire_load_node_status(client, app, tx);
                    }
                    Err(e) => app.set_error(format!("Failed to stop {}: {}", node_type, e)),
                }
            }
        }

        // Node start
        KeyCode::Char('s') => {
            if app.workspace == Workspace::Nodes {
                let node_type = match app.panel_focus {
                    app::PanelFocus::Left => "sandbox",
                    app::PanelFocus::Right => "live",
                };
                match client.node_start(node_type, &[]).await {
                    Ok(_) => {
                        app.push_alert(
                            app::AlertKind::Info,
                            format!("{} node starting…", node_type),
                        );
                        fire_load_node_status(client, app, tx);
                    }
                    Err(e) => app.set_error(format!("Failed to start {}: {}", node_type, e)),
                }
            }
        }

        // Strategy validate
        KeyCode::Char('v') => {
            if app.workspace == Workspace::Strategy {
                if let Some(s) = app.strategies.get(app.strategy_selected) {
                    match client.validate_strategy(&s.name).await {
                        Ok(result) => {
                            if result.valid {
                                app.push_alert(
                                    app::AlertKind::Success,
                                    format!("Strategy '{}' is valid", s.name),
                                );
                            } else {
                                let issues = result
                                    .issues
                                    .map(|i| i.join(", "))
                                    .unwrap_or_default();
                                app.set_error(format!("Validation failed: {}", issues));
                            }
                        }
                        Err(e) => app.set_error(format!("Validate error: {}", e)),
                    }
                }
            }
        }

        // Open tearsheet HTML report in browser
        KeyCode::Char('o') => {
            if app.workspace == Workspace::Backtest {
                if let Some(bt) = app.backtests.get(app.backtest_selected) {
                    if let Some(home) = dirs::home_dir() {
                        let path = home
                            .join(".tino/data/artifacts")
                            .join(&bt.run_id)
                            .join("tearsheet.html");
                        if path.exists() {
                            let _ = std::process::Command::new("open")
                                .arg(&path)
                                .spawn();
                        } else {
                            app.set_error("Tearsheet not found".to_string());
                        }
                    }
                }
            }
        }

        // Open artifacts directory
        KeyCode::Char('d') => {
            if app.workspace == Workspace::Backtest {
                if let Some(bt) = app.backtests.get(app.backtest_selected) {
                    if let Some(home) = dirs::home_dir() {
                        let path = home
                            .join(".tino/data/artifacts")
                            .join(&bt.run_id);
                        if path.exists() {
                            let _ = std::process::Command::new("open")
                                .arg(&path)
                                .spawn();
                        } else {
                            app.set_error("Artifacts directory not found".to_string());
                        }
                    }
                }
            }
        }

        // Fetch data form
        KeyCode::Char('f') => {
            if app.workspace == Workspace::Data {
                app.open_popup(PopupKind::DataFetchForm);
            }
        }

        // Esc — close popup or go to dashboard
        KeyCode::Esc => {
            if app.workspace != Workspace::Dashboard {
                app.switch_workspace(Workspace::Dashboard);
            }
        }

        _ => {}
    }

    false
}

/// Handle keys when a popup is open.
async fn handle_popup_key(app: &mut App, client: &ApiClient, tx: &mpsc::UnboundedSender<DataCmd>, code: KeyCode) -> bool {
    match &app.popup {
        Some(PopupKind::BacktestForm) => match code {
            KeyCode::Esc => {
                app.close_popup();
            }
            KeyCode::Tab => {
                // On Strategy field, accept suggestion before moving
                if app.form_focus == 0 && !app.form_suggestions.is_empty() {
                    app.accept_suggestion();
                }
                app.form_focus = (app.form_focus + 1) % 5;
                if app.form_focus == 0 {
                    app.update_form_suggestions();
                }
            }
            KeyCode::BackTab => {
                if app.form_focus == 0 && !app.form_suggestions.is_empty() {
                    app.accept_suggestion();
                }
                app.form_focus = if app.form_focus == 0 {
                    4
                } else {
                    app.form_focus - 1
                };
                if app.form_focus == 0 {
                    app.update_form_suggestions();
                }
            }
            KeyCode::Up => {
                if app.form_focus == 0 && !app.form_suggestions.is_empty() {
                    app.form_suggestion_idx = app.form_suggestion_idx
                        .checked_sub(1)
                        .unwrap_or(app.form_suggestions.len() - 1);
                }
            }
            KeyCode::Down => {
                if app.form_focus == 0 && !app.form_suggestions.is_empty() {
                    app.form_suggestion_idx =
                        (app.form_suggestion_idx + 1) % app.form_suggestions.len();
                }
            }
            KeyCode::Backspace => {
                let is_date = app.form_focus == 3 || app.form_focus == 4;
                let field = get_form_field_mut(app);
                if is_date {
                    // Remove trailing dash along with the digit
                    if field.ends_with('-') {
                        field.pop(); // remove dash
                    }
                    field.pop(); // remove digit
                } else {
                    field.pop();
                }
                if app.form_focus == 0 {
                    app.update_form_suggestions();
                }
            }
            KeyCode::Char(c) => {
                let is_date = app.form_focus == 3 || app.form_focus == 4;
                if is_date {
                    // Date fields: only accept digits, auto-insert dashes
                    if c.is_ascii_digit() {
                        let field = get_form_field_mut(app);
                        let digits: String = field.chars().filter(|ch| ch.is_ascii_digit()).collect();
                        if digits.len() < 8 {
                            let mut new_digits = digits;
                            new_digits.push(c);
                            // Format as YYYY-MM-DD
                            let formatted = format_date_digits(&new_digits);
                            let field = get_form_field_mut(app);
                            *field = formatted;
                        }
                    }
                    // Ignore non-digit input for date fields
                } else {
                    let field = get_form_field_mut(app);
                    field.push(c);
                    if app.form_focus == 0 {
                        app.update_form_suggestions();
                    }
                }
            }
            KeyCode::Enter => {
                // On Strategy field, accept suggestion first
                if app.form_focus == 0 && !app.form_suggestions.is_empty() {
                    app.accept_suggestion();
                    app.form_focus = 1; // advance to Symbol
                    return false;
                }
                if app.form_strategy.is_empty() {
                    app.set_error("Strategy name is required".to_string());
                    return false;
                }
                if app.form_start.is_empty() || app.form_end.is_empty() {
                    app.set_error("Start and end dates are required".to_string());
                    return false;
                }

                let req = crate::types::BacktestRunRequest {
                    strategy: app.form_strategy.clone(),
                    symbols: if app.form_symbol.is_empty() { vec![] } else { vec![app.form_symbol.clone()] },
                    intervals: if app.form_interval.is_empty() { vec![] } else { vec![app.form_interval.clone()] },
                    start_date: app.form_start.clone(),
                    end_date: app.form_end.clone(),
                    initial_capital: 10000.0,
                    leverage: 1.0,
                    params: None,
                    fill_model: None,
                };

                match client.run_backtest(&req).await {
                    Ok(_resp) => {
                        app.close_popup();
                        app.push_alert(
                            app::AlertKind::Info,
                            format!("Backtest '{}' submitted", app.form_strategy),
                        );
                        fire_load_backtests(client, app, tx);
                    }
                    Err(e) => {
                        app.set_error(format!("Failed to submit: {}", e));
                    }
                }
            }
            _ => {}
        },
        Some(PopupKind::Help) => {
            if code == KeyCode::Esc || code == KeyCode::Char('?') {
                app.close_popup();
            }
        }
        Some(PopupKind::Confirm { .. }) => match code {
            KeyCode::Char('y') => {
                app.close_popup();
                if let Some(action) = app.pending_action.take() {
                    match action {
                        app::PendingAction::DeleteBacktest { run_id } => {
                            let id_short = run_id.get(..8).unwrap_or(&run_id).to_string();
                            match client.delete_backtest(&run_id).await {
                                Ok(_) => {
                                    app.push_alert(
                                        app::AlertKind::Info,
                                        format!("Backtest #{} deleted", id_short),
                                    );
                                    app.detail_result = None;
                                    app.detail_equity = Vec::new();
                                    fire_load_backtests(client, app, tx);
                                }
                                Err(e) => app.set_error(format!("Delete failed: {}", e)),
                            }
                        }
                    }
                }
            }
            KeyCode::Char('n') | KeyCode::Esc => {
                app.close_popup();
                app.pending_action = None;
            }
            _ => {}
        },
        Some(PopupKind::DataFetchForm) => {
            if code == KeyCode::Esc {
                app.close_popup();
            }
            // TODO: data fetch form input handling
        }
        None => {}
    }

    false
}

fn get_form_field_mut(app: &mut App) -> &mut String {
    match app.form_focus {
        0 => &mut app.form_strategy,
        1 => &mut app.form_symbol,
        2 => &mut app.form_interval,
        3 => &mut app.form_start,
        4 => &mut app.form_end,
        _ => &mut app.form_strategy,
    }
}

/// Format raw digits into YYYY-MM-DD, inserting dashes at the right positions.
fn format_date_digits(digits: &str) -> String {
    let mut out = String::with_capacity(10);
    for (i, ch) in digits.chars().enumerate() {
        if i == 4 || i == 6 {
            out.push('-');
        }
        out.push(ch);
    }
    out
}

fn handle_nav_down(app: &mut App) {
    match app.workspace {
        Workspace::Dashboard | Workspace::Backtest => {
            if !app.backtests.is_empty() {
                app.backtest_selected =
                    (app.backtest_selected + 1).min(app.backtests.len() - 1);
            }
        }
        Workspace::Strategy => {
            if !app.strategies.is_empty() {
                app.strategy_selected =
                    (app.strategy_selected + 1).min(app.strategies.len() - 1);
            }
        }
        Workspace::Data => {
            if let Some(catalog) = app.data_catalog.as_ref().and_then(|c| c.as_array()) {
                if !catalog.is_empty() {
                    app.data_selected =
                        (app.data_selected + 1).min(catalog.len() - 1);
                }
            }
        }
        _ => {}
    }
}

fn handle_nav_up(app: &mut App) {
    match app.workspace {
        Workspace::Dashboard | Workspace::Backtest => {
            app.backtest_selected = app.backtest_selected.saturating_sub(1);
        }
        Workspace::Strategy => {
            app.strategy_selected = app.strategy_selected.saturating_sub(1);
        }
        Workspace::Data => {
            app.data_selected = app.data_selected.saturating_sub(1);
        }
        _ => {}
    }
}

// ── Non-blocking data loading ───────────────────────────────────────────

fn fire_load_backtests(
    client: &ApiClient,
    app: &mut App,
    tx: &mpsc::UnboundedSender<DataCmd>,
) {
    app.backtest_loading = true;
    let c = client.clone();
    let tx = tx.clone();
    tokio::spawn(async move {
        let result = c.list_backtests().await;
        let _ = tx.send(DataCmd::Backtests(result));
    });
}

fn fire_load_strategies(
    client: &ApiClient,
    app: &mut App,
    tx: &mpsc::UnboundedSender<DataCmd>,
) {
    app.strategy_loading = true;
    let c = client.clone();
    let tx = tx.clone();
    tokio::spawn(async move {
        let result = c.list_strategies().await;
        let _ = tx.send(DataCmd::Strategies(result));
    });
}

fn fire_load_node_status(
    client: &ApiClient,
    app: &mut App,
    tx: &mpsc::UnboundedSender<DataCmd>,
) {
    app.node_loading = true;
    let c = client.clone();
    let tx = tx.clone();
    tokio::spawn(async move {
        let result = c.node_status().await;
        let _ = tx.send(DataCmd::NodeStatus(result));
    });
}

fn fire_load_data_catalog(
    client: &ApiClient,
    app: &mut App,
    tx: &mpsc::UnboundedSender<DataCmd>,
) {
    app.data_loading = true;
    let c = client.clone();
    let tx = tx.clone();
    tokio::spawn(async move {
        let result = c.list_data().await;
        let _ = tx.send(DataCmd::DataCatalog(result));
    });
}

fn fire_load_detail_result(
    client: &ApiClient,
    app: &mut App,
    tx: &mpsc::UnboundedSender<DataCmd>,
) {
    let run_id = match app.backtests.get(app.backtest_selected) {
        Some(bt) => bt.run_id.clone(),
        None => return,
    };
    app.detail_result = None;
    app.detail_equity = Vec::new();
    let c = client.clone();
    let tx = tx.clone();
    tokio::spawn(async move {
        let result = c.get_result(&run_id).await;
        let _ = tx.send(DataCmd::DetailResult(result));
    });
}

fn fire_load_workspace_data(
    client: &ApiClient,
    app: &mut App,
    tx: &mpsc::UnboundedSender<DataCmd>,
) {
    match app.workspace {
        Workspace::Dashboard => {
            fire_load_backtests(client, app, tx);
            fire_load_node_status(client, app, tx);
        }
        Workspace::Backtest => fire_load_backtests(client, app, tx),
        Workspace::Strategy => fire_load_strategies(client, app, tx),
        Workspace::Nodes => {
            fire_load_node_status(client, app, tx);
            workspaces::nodes::fire_load_positions(client, app, tx);
            workspaces::nodes::fire_load_fills(client, app, tx);
        }
        Workspace::Data => fire_load_data_catalog(client, app, tx),
    }
}

fn handle_data_cmd(app: &mut App, cmd: DataCmd) {
    match cmd {
        DataCmd::Backtests(result) => {
            app.backtest_loading = false;
            match result {
                Ok(list) => {
                    app.backtests = list.runs;
                    if app.backtest_selected >= app.backtests.len() && !app.backtests.is_empty() {
                        app.backtest_selected = app.backtests.len() - 1;
                    }
                }
                Err(e) => app.set_error(format!("Failed to load backtests: {}", e)),
            }
        }
        DataCmd::Strategies(result) => {
            app.strategy_loading = false;
            match result {
                Ok(list) => {
                    app.strategies = list;
                    if app.strategy_selected >= app.strategies.len() && !app.strategies.is_empty() {
                        app.strategy_selected = app.strategies.len() - 1;
                    }
                }
                Err(e) => app.set_error(format!("Failed to load strategies: {}", e)),
            }
        }
        DataCmd::NodeStatus(result) => {
            app.node_loading = false;
            match result {
                Ok(status) => app.node_status = Some(status),
                Err(e) => app.set_error(format!("Failed to load node status: {}", e)),
            }
        }
        DataCmd::DataCatalog(result) => {
            app.data_loading = false;
            match result {
                Ok(data) => app.data_catalog = Some(data),
                Err(e) => app.set_error(format!("Failed to load data catalog: {}", e)),
            }
        }
        DataCmd::DetailResult(result) => {
            match result {
                Ok(detail) => {
                    if let Some(curve) = detail.get("equity_curve").and_then(|c| c.as_array()) {
                        app.detail_equity = curve
                            .iter()
                            .filter_map(|v| v.as_f64())
                            .map(|v| v.max(0.0) as u64)
                            .collect();
                    }
                    app.detail_result = Some(detail);
                }
                Err(_) => {}
            }
        }
        DataCmd::Positions(result) => {
            app.trading_loading = false;
            match result {
                Ok(list) => {
                    app.positions = list;
                    if app.trading_selected >= app.positions.len() && !app.positions.is_empty() {
                        app.trading_selected = app.positions.len() - 1;
                    }
                }
                Err(e) => app.set_error(format!("Failed to load positions: {}", e)),
            }
        }
        DataCmd::Fills(result) => {
            app.trading_loading = false;
            match result {
                Ok(list) => app.fills = list,
                Err(e) => app.set_error(format!("Failed to load fills: {}", e)),
            }
        }
        DataCmd::TradingSummary(result) => {
            match result {
                Ok(summary) => app.trading_summary = Some(summary),
                Err(e) => app.set_error(format!("Failed to load trading summary: {}", e)),
            }
        }
    }
}
