use crate::types::{BacktestRunItem, Strategy, WsEvent};

/// Which TUI view is currently active.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum View {
    BacktestList,
    BacktestDetail,
    BacktestForm,
    StrategyList,
    NodeStatus,
}

/// WebSocket connection state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WsState {
    Disconnected,
    Connecting,
    Connected,
}

/// Central application state — Elm architecture model.
pub struct App {
    pub current_view: View,
    pub previous_view: Option<View>,
    pub running: bool,

    // Backtest state
    pub backtests: Vec<BacktestRunItem>,
    pub backtest_selected: usize,
    pub backtest_loading: bool,
    pub detail_result: Option<serde_json::Value>,
    pub detail_equity: Vec<u64>,

    // Backtest form state
    pub form_strategy: String,
    pub form_symbol: String,
    pub form_interval: String,
    pub form_start: String,
    pub form_end: String,
    pub form_focus: usize,

    // Strategy state
    pub strategies: Vec<Strategy>,
    pub strategy_selected: usize,
    pub strategy_loading: bool,

    // Node state
    pub node_status: Option<serde_json::Value>,
    pub node_loading: bool,
    pub sandbox_last_heartbeat: Option<std::time::Instant>,
    pub live_last_heartbeat: Option<std::time::Instant>,

    // WebSocket
    pub ws_state: WsState,
    pub ws_reconnect_secs: Option<u64>,

    // Error banner
    pub error_banner: Option<String>,
    pub error_dismiss_at: Option<std::time::Instant>,
}

impl App {
    pub fn new() -> Self {
        Self {
            current_view: View::BacktestList,
            previous_view: None,
            running: true,

            backtests: Vec::new(),
            backtest_selected: 0,
            backtest_loading: false,
            detail_result: None,
            detail_equity: Vec::new(),

            form_strategy: String::new(),
            form_symbol: "BTCUSDT-PERP".to_string(),
            form_interval: "5m".to_string(),
            form_start: String::new(),
            form_end: String::new(),
            form_focus: 0,

            strategies: Vec::new(),
            strategy_selected: 0,
            strategy_loading: false,

            node_status: None,
            node_loading: false,
            sandbox_last_heartbeat: None,
            live_last_heartbeat: None,

            ws_state: WsState::Disconnected,
            ws_reconnect_secs: None,

            error_banner: None,
            error_dismiss_at: None,
        }
    }

    pub fn navigate(&mut self, view: View) {
        self.previous_view = Some(self.current_view);
        self.current_view = view;
    }

    pub fn go_back(&mut self) {
        if let Some(prev) = self.previous_view.take() {
            self.current_view = prev;
        }
    }

    pub fn set_error(&mut self, msg: String) {
        self.error_banner = Some(msg);
        self.error_dismiss_at =
            Some(std::time::Instant::now() + std::time::Duration::from_secs(5));
    }

    pub fn tick(&mut self) {
        // Auto-dismiss error banner
        if let Some(dismiss_at) = self.error_dismiss_at {
            if std::time::Instant::now() >= dismiss_at {
                self.error_banner = None;
                self.error_dismiss_at = None;
            }
        }
    }

    /// Handle an incoming WebSocket event.
    pub fn handle_ws_event(&mut self, event: WsEvent) {
        match event {
            WsEvent::BacktestProgress { run_id, pct, .. } => {
                if let Some(bt) = self.backtests.iter_mut().find(|b| b.run_id == run_id) {
                    bt.status = format!("running ({}%)", pct);
                }
            }
            WsEvent::BacktestCompleted {
                run_id, status, ..
            } => {
                if let Some(bt) = self.backtests.iter_mut().find(|b| b.run_id == run_id) {
                    bt.status = status;
                }
            }
            WsEvent::BacktestStats { .. } => {
                // Stats updates can be used for detail view in the future
            }
            WsEvent::NodeHeartbeat { node_type, .. } => {
                let now = std::time::Instant::now();
                match node_type.as_str() {
                    "sandbox" => self.sandbox_last_heartbeat = Some(now),
                    "live" => self.live_last_heartbeat = Some(now),
                    _ => {}
                }
            }
            WsEvent::SystemError { message } => {
                self.set_error(message);
            }
        }
    }
}
