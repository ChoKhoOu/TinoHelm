pub mod app;
pub mod views;
pub mod ws;

use std::io;
use std::time::Duration;

use anyhow::Result;
use crossterm::{
    event::{self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyModifiers},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{backend::CrosstermBackend, Terminal};
use tokio::sync::mpsc;

use crate::api::ApiClient;
use app::{App, View, WsState};
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

    // Tick interval for UI refresh (250ms)
    let tick_rate = Duration::from_millis(250);

    loop {
        // Render
        terminal.draw(|f| views::render(f, &app))?;

        // Multiplex: terminal events, WS events, tick timer
        tokio::select! {
            // Terminal input events (polled with timeout)
            _ = tokio::task::spawn_blocking({
                let tick = tick_rate;
                move || event::poll(tick)
            }) => {
                // Read all available events
                while event::poll(Duration::ZERO)? {
                    if let Event::Key(key) = event::read()? {
                        if handle_key(&mut app, &client, key.code, key.modifiers).await {
                            return Ok(());
                        }
                    }
                    // Resize is handled automatically by ratatui on next draw
                }
            }

            // WebSocket events
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

        // Tick housekeeping (dismiss errors, etc.)
        app.tick();

        if !app.running {
            return Ok(());
        }
    }
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

    // Form view has its own key handling
    if app.current_view == View::BacktestForm {
        return handle_form_key(app, client, code).await;
    }

    match code {
        // Global: quit
        KeyCode::Char('q') => return true,

        // Global: tab switching
        KeyCode::Char('1') => {
            app.navigate(View::BacktestList);
            load_backtests(client, app).await;
        }
        KeyCode::Char('2') => {
            app.navigate(View::StrategyList);
            load_strategies(client, app).await;
        }
        KeyCode::Char('3') => {
            app.navigate(View::NodeStatus);
            load_node_status(client, app).await;
        }

        // Navigation
        KeyCode::Char('j') | KeyCode::Down => match app.current_view {
            View::BacktestList => {
                if !app.backtests.is_empty() {
                    app.backtest_selected =
                        (app.backtest_selected + 1).min(app.backtests.len() - 1);
                }
            }
            View::StrategyList => {
                if !app.strategies.is_empty() {
                    app.strategy_selected =
                        (app.strategy_selected + 1).min(app.strategies.len() - 1);
                }
            }
            _ => {}
        },
        KeyCode::Char('k') | KeyCode::Up => match app.current_view {
            View::BacktestList => {
                app.backtest_selected = app.backtest_selected.saturating_sub(1);
            }
            View::StrategyList => {
                app.strategy_selected = app.strategy_selected.saturating_sub(1);
            }
            _ => {}
        },

        // Enter: open detail
        KeyCode::Enter => {
            if app.current_view == View::BacktestList && !app.backtests.is_empty() {
                app.navigate(View::BacktestDetail);
            }
        }

        // Esc: go back
        KeyCode::Esc => {
            app.go_back();
        }

        // New backtest
        KeyCode::Char('n') => {
            if app.current_view == View::BacktestList {
                app.form_strategy.clear();
                app.form_focus = 0;
                app.navigate(View::BacktestForm);
            }
        }

        // Refresh
        KeyCode::Char('r') => match app.current_view {
            View::BacktestList | View::BacktestDetail => {
                load_backtests(client, app).await;
            }
            View::StrategyList => {
                load_strategies(client, app).await;
            }
            View::NodeStatus => {
                load_node_status(client, app).await;
            }
            _ => {}
        },

        _ => {}
    }

    false
}

/// Handle keys in the backtest form view.
async fn handle_form_key(app: &mut App, client: &ApiClient, code: KeyCode) -> bool {
    match code {
        KeyCode::Esc => {
            app.go_back();
        }
        KeyCode::Tab => {
            app.form_focus = (app.form_focus + 1) % 5;
        }
        KeyCode::BackTab => {
            app.form_focus = if app.form_focus == 0 { 4 } else { app.form_focus - 1 };
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
            // Submit the form
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
                    app.go_back();
                    load_backtests(client, app).await;
                }
                Err(e) => {
                    app.set_error(format!("Failed to submit: {}", e));
                }
            }
        }
        _ => {}
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
