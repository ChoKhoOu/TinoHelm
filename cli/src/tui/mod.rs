pub mod app;
pub mod chrome;
pub mod theme;
pub mod views;
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
use app::{App, PopupKind, Workspace, WsState};
use ws::WsClientEvent;

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

    // Load initial data
    load_backtests(&client, &mut app).await;

    loop {
        let tick_rate = Duration::from_millis(app.tick_rate_ms());

        // Render
        terminal.draw(|f| render(f, &app))?;

        // Multiplex: terminal events, WS events, tick timer
        tokio::select! {
            _ = tokio::task::spawn_blocking({
                let tick = tick_rate;
                move || event::poll(tick)
            }) => {
                while event::poll(Duration::ZERO)? {
                    if let Event::Key(key) = event::read()? {
                        if handle_key(&mut app, &client, key.code, key.modifiers).await {
                            return Ok(());
                        }
                    }
                }
            }

            Some(ws_event) = ws_rx.recv() => {
                match ws_event {
                    WsClientEvent::Connecting => {
                        app.ws_state = WsState::Connecting;
                    }
                    WsClientEvent::Connected => {
                        app.ws_state = WsState::Connected;
                        app.ws_reconnect_secs = None;
                    }
                    WsClientEvent::Disconnected { retry_secs } => {
                        app.ws_state = WsState::Disconnected;
                        app.ws_reconnect_secs = Some(retry_secs);
                    }
                    WsClientEvent::Event(event) => {
                        app.handle_ws_event(event);
                    }
                }
            }
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

    // Global layout: [header(1)] [content(fill)] [hints(1)] [error?(1)]
    let has_error = app.error_banner.is_some();
    let constraints = if has_error {
        vec![
            Constraint::Length(1),
            Constraint::Min(5),
            Constraint::Length(1),
            Constraint::Length(1),
        ]
    } else {
        vec![
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

    // Header bar
    chrome::render_header(f, chunks[0], app);

    // Main content — workspace router
    // Boot animation: skip full rendering until boot is complete
    if !app.boot_complete {
        render_boot(f, chunks[1], app);
    } else {
        match app.workspace {
            Workspace::Dashboard => workspaces::dashboard::render(f, chunks[1], app),
            Workspace::Backtest => workspaces::backtest::render(f, chunks[1], app),
            Workspace::Strategy => workspaces::strategy::render(f, chunks[1], app),
            Workspace::Nodes => workspaces::nodes::render(f, chunks[1], app),
            Workspace::Data => workspaces::data::render(f, chunks[1], app),
        }
    }

    // Hint bar
    chrome::render_hints(f, chunks[2], app);

    // Error banner
    if has_error {
        chrome::render_error(f, chunks[3], app);
    }

    // Popup overlay (rendered last, on top of everything)
    if let Some(ref popup) = app.popup {
        render_popup(f, size, app, popup);
    }
}

/// Simple boot animation — progressive panel reveal.
fn render_boot(f: &mut ratatui::Frame, area: ratatui::layout::Rect, app: &App) {
    use ratatui::text::{Line, Span};
    use ratatui::widgets::Paragraph;

    let phase = app.boot_phase;
    let mut lines = Vec::new();

    // Phase 0: blank
    if phase == 0 {
        let p = Paragraph::new("").style(Style::default().bg(theme::BG_PRIMARY));
        f.render_widget(p, area);
        return;
    }

    // Phase 1: pixel noise / scanline
    if phase >= 1 {
        let mid_y = area.height / 2;
        for row in 0..area.height {
            if row == mid_y.saturating_sub(2) && phase >= 2 {
                lines.push(Line::from(Span::styled(
                    "  \u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}",
                    Style::default().fg(theme::FG_BORDER),
                )));
            } else if row == mid_y && phase >= 2 {
                lines.push(Line::from(vec![
                    Span::styled(
                        "           [TINO]",
                        Style::default().fg(theme::FG_LOGO),
                    ),
                    Span::styled(
                        "HELM",
                        Style::default().fg(theme::FG_AMBER),
                    ),
                ]));
            } else if row == mid_y + 1 && phase >= 3 {
                lines.push(Line::from(Span::styled(
                    "       Quantitative Trading Terminal",
                    theme::style_dim(),
                )));
            } else if row == mid_y + 2 && phase >= 2 {
                lines.push(Line::from(Span::styled(
                    "  \u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}\u{2593}",
                    Style::default().fg(theme::FG_BORDER),
                )));
            } else if row % 2 == 0 && phase == 1 {
                lines.push(Line::from(Span::styled(
                    "\u{2591}".repeat(area.width as usize),
                    Style::default().fg(theme::FG_BORDER),
                )));
            } else {
                lines.push(Line::from(""));
            }
        }
    }

    let p = Paragraph::new(lines).style(Style::default().bg(theme::BG_PRIMARY));
    f.render_widget(p, area);
}

/// Render a popup overlay.
fn render_popup(
    f: &mut ratatui::Frame,
    area: ratatui::layout::Rect,
    app: &App,
    popup: &PopupKind,
) {
    use ratatui::text::{Line, Span};
    use ratatui::widgets::{Block, Borders, Clear, Paragraph};

    // Determine popup size
    let (width, height) = match popup {
        PopupKind::BacktestForm => (50, 18),
        PopupKind::DataFetchForm => (50, 14),
        PopupKind::Help => (60, 20),
        PopupKind::Confirm { .. } => (50, 8),
    };

    let popup_area = centered_rect(width, height, area);

    // Clear the popup area (removes underlying content)
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
        lines.push(Line::from(""));
    }

    lines.push(Line::from(""));
    lines.push(Line::from(vec![
        Span::styled("  Tab", theme::style_hint_key()),
        Span::styled(" next  ", theme::style_hint_desc()),
        Span::styled("Enter", theme::style_hint_key()),
        Span::styled(" submit  ", theme::style_hint_desc()),
        Span::styled("Esc", theme::style_hint_key()),
        Span::styled(" cancel", theme::style_hint_desc()),
    ]));

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
        return handle_popup_key(app, client, code).await;
    }

    match code {
        // Quit
        KeyCode::Char('q') => return true,

        // Workspace switching — F-keys
        KeyCode::F(1) | KeyCode::Char('1') => {
            app.switch_workspace(Workspace::Dashboard);
            load_backtests(client, app).await;
            load_node_status(client, app).await;
        }
        KeyCode::F(2) | KeyCode::Char('2') => {
            app.switch_workspace(Workspace::Backtest);
            load_backtests(client, app).await;
        }
        KeyCode::F(3) | KeyCode::Char('3') => {
            app.switch_workspace(Workspace::Strategy);
            load_strategies(client, app).await;
        }
        KeyCode::F(4) | KeyCode::Char('4') => {
            app.switch_workspace(Workspace::Nodes);
            load_node_status(client, app).await;
        }
        KeyCode::F(5) | KeyCode::Char('5') => {
            app.switch_workspace(Workspace::Data);
            load_data_catalog(client, app).await;
        }

        // Help
        KeyCode::Char('?') => {
            app.open_popup(PopupKind::Help);
        }

        // Tab — toggle panel focus
        KeyCode::Tab => {
            app.toggle_panel_focus();
        }

        // Navigation
        KeyCode::Char('j') | KeyCode::Down => handle_nav_down(app),
        KeyCode::Char('k') | KeyCode::Up => handle_nav_up(app),

        // Enter — open detail / load detail data
        KeyCode::Enter => {
            if app.workspace == Workspace::Backtest && !app.backtests.is_empty() {
                load_detail_result(client, app).await;
                app.panel_focus = app::PanelFocus::Right;
            }
        }

        // New backtest
        KeyCode::Char('n') => {
            if app.workspace == Workspace::Backtest || app.workspace == Workspace::Dashboard {
                app.form_strategy.clear();
                app.form_focus = 0;
                app.open_popup(PopupKind::BacktestForm);
            }
        }

        // Refresh
        KeyCode::Char('r') => match app.workspace {
            Workspace::Dashboard => {
                load_backtests(client, app).await;
                load_node_status(client, app).await;
            }
            Workspace::Backtest => {
                load_backtests(client, app).await;
            }
            Workspace::Strategy => {
                // Rescan strategies
                let _ = client.rescan_strategies().await;
                load_strategies(client, app).await;
            }
            Workspace::Nodes => {
                load_node_status(client, app).await;
            }
            Workspace::Data => {
                load_data_catalog(client, app).await;
            }
        },

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
async fn handle_popup_key(app: &mut App, client: &ApiClient, code: KeyCode) -> bool {
    match &app.popup {
        Some(PopupKind::BacktestForm) => match code {
            KeyCode::Esc => {
                app.close_popup();
            }
            KeyCode::Tab => {
                app.form_focus = (app.form_focus + 1) % 5;
            }
            KeyCode::BackTab => {
                app.form_focus = if app.form_focus == 0 {
                    4
                } else {
                    app.form_focus - 1
                };
            }
            KeyCode::Backspace => {
                let field = get_form_field_mut(app);
                field.pop();
            }
            KeyCode::Char(c) => {
                let field = get_form_field_mut(app);
                field.push(c);
            }
            KeyCode::Enter => {
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
                    symbols: vec![app.form_symbol.clone()],
                    intervals: vec![app.form_interval.clone()],
                    start_date: app.form_start.clone(),
                    end_date: app.form_end.clone(),
                    initial_capital: 10000.0,
                    leverage: 1.0,
                    params: None,
                };

                match client.run_backtest(&req).await {
                    Ok(_resp) => {
                        app.close_popup();
                        app.push_alert(
                            app::AlertKind::Info,
                            format!("Backtest '{}' submitted", app.form_strategy),
                        );
                        load_backtests(client, app).await;
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
                // TODO: execute confirmed action
            }
            KeyCode::Char('n') | KeyCode::Esc => {
                app.close_popup();
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

// ── Data loading helpers ────────────────────────────────────────────────

async fn load_backtests(client: &ApiClient, app: &mut App) {
    app.backtest_loading = true;
    match client.list_backtests().await {
        Ok(list) => {
            app.backtests = list.runs;
            if app.backtest_selected >= app.backtests.len() && !app.backtests.is_empty() {
                app.backtest_selected = app.backtests.len() - 1;
            }
        }
        Err(e) => {
            app.set_error(format!("Failed to load backtests: {}", e));
        }
    }
    app.backtest_loading = false;
}

async fn load_strategies(client: &ApiClient, app: &mut App) {
    app.strategy_loading = true;
    match client.list_strategies().await {
        Ok(list) => {
            app.strategies = list;
            if app.strategy_selected >= app.strategies.len() && !app.strategies.is_empty() {
                app.strategy_selected = app.strategies.len() - 1;
            }
        }
        Err(e) => {
            app.set_error(format!("Failed to load strategies: {}", e));
        }
    }
    app.strategy_loading = false;
}

async fn load_node_status(client: &ApiClient, app: &mut App) {
    app.node_loading = true;
    match client.node_status().await {
        Ok(status) => {
            app.node_status = Some(status);
        }
        Err(e) => {
            app.set_error(format!("Failed to load node status: {}", e));
        }
    }
    app.node_loading = false;
}

async fn load_detail_result(client: &ApiClient, app: &mut App) {
    let run_id = match app.backtests.get(app.backtest_selected) {
        Some(bt) => bt.run_id.clone(),
        None => return,
    };
    app.detail_result = None;
    app.detail_equity = Vec::new();

    match client.get_result(&run_id).await {
        Ok(result) => {
            if let Some(curve) = result.get("equity_curve").and_then(|c| c.as_array()) {
                app.detail_equity = curve
                    .iter()
                    .filter_map(|v| v.as_f64())
                    .map(|v| v.max(0.0) as u64)
                    .collect();
            }
            app.detail_result = Some(result);
        }
        Err(_) => {
            // Result not available yet
        }
    }
}

async fn load_data_catalog(client: &ApiClient, app: &mut App) {
    app.data_loading = true;
    match client.list_data().await {
        Ok(data) => {
            app.data_catalog = Some(data);
        }
        Err(e) => {
            app.set_error(format!("Failed to load data catalog: {}", e));
        }
    }
    app.data_loading = false;
}
