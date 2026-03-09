use std::collections::VecDeque;

use crate::types::{BacktestRunItem, Strategy, WsEvent};

/// Bloomberg-style workspace model — each F-key opens a dedicated workspace.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Workspace {
    Dashboard,  // F1
    Backtest,   // F2
    Strategy,   // F3
    Nodes,      // F4
    Data,       // F5
}

/// Which panel is focused in a split-panel workspace.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PanelFocus {
    Left,
    Right,
}

/// Active popup/modal overlay.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PopupKind {
    BacktestForm,
    DataFetchForm,
    Help,
    Confirm { message: String },
}

/// WebSocket connection state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WsState {
    Disconnected,
    Connecting,
    Connected,
}

/// An alert for the scrolling ticker.
#[derive(Debug, Clone)]
pub struct Alert {
    pub timestamp: String,
    pub message: String,
    pub kind: AlertKind,
}

/// A single entry in the real-time event log.
#[derive(Debug, Clone)]
pub struct EventLogEntry {
    pub timestamp: String,
    pub event_type: String,
    pub message: String,
}

#[derive(Debug, Clone, Copy)]
pub enum AlertKind {
    Info,
    Success,
    Warning,
    Error,
}

/// Central application state — Elm architecture model.
pub struct App {
    // ── Workspace ───────────────────────────────────────────────────────
    pub workspace: Workspace,
    pub running: bool,

    // ── Panel focus (for split-panel workspaces) ────────────────────────
    pub panel_focus: PanelFocus,

    // ── Popup/Modal ─────────────────────────────────────────────────────
    pub popup: Option<PopupKind>,

    // ── Animation state ─────────────────────────────────────────────────
    pub frame_count: u64,
    pub boot_complete: bool,
    pub boot_phase: u8,

    // ── Alert ticker ────────────────────────────────────────────────────
    pub alerts: VecDeque<Alert>,
    pub ticker_offset: usize,

    // ── Backtest state ──────────────────────────────────────────────────
    pub backtests: Vec<BacktestRunItem>,
    pub backtest_selected: usize,
    pub backtest_loading: bool,
    pub detail_result: Option<serde_json::Value>,
    pub detail_equity: Vec<u64>,
    pub detail_scroll: u16,

    // ── Backtest form state ─────────────────────────────────────────────
    pub form_strategy: String,
    pub form_symbol: String,
    pub form_interval: String,
    pub form_start: String,
    pub form_end: String,
    pub form_focus: usize,

    // ── Strategy state ──────────────────────────────────────────────────
    pub strategies: Vec<Strategy>,
    pub strategy_selected: usize,
    pub strategy_loading: bool,

    // ── Node state ──────────────────────────────────────────────────────
    pub node_status: Option<serde_json::Value>,
    pub node_loading: bool,
    pub sandbox_last_heartbeat: Option<std::time::Instant>,
    pub live_last_heartbeat: Option<std::time::Instant>,

    // ── Data catalog state ──────────────────────────────────────────────
    pub data_catalog: Option<serde_json::Value>,
    pub data_selected: usize,
    pub data_loading: bool,

    // ── Event log ─────────────────────────────────────────────────────
    pub event_log: VecDeque<EventLogEntry>,
    pub log_scroll: u16,

    // ── WebSocket ───────────────────────────────────────────────────────
    pub ws_state: WsState,
    pub ws_reconnect_secs: Option<u64>,

    // ── Error banner ────────────────────────────────────────────────────
    pub error_banner: Option<String>,
    pub error_dismiss_at: Option<std::time::Instant>,
}

impl App {
    pub fn new() -> Self {
        Self {
            workspace: Workspace::Dashboard,
            running: true,

            panel_focus: PanelFocus::Left,

            popup: None,

            frame_count: 0,
            boot_complete: false,
            boot_phase: 0,

            alerts: VecDeque::with_capacity(50),
            ticker_offset: 0,

            backtests: Vec::new(),
            backtest_selected: 0,
            backtest_loading: false,
            detail_result: None,
            detail_equity: Vec::new(),
            detail_scroll: 0,

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

            data_catalog: None,
            data_selected: 0,
            data_loading: false,

            event_log: VecDeque::with_capacity(200),
            log_scroll: 0,

            ws_state: WsState::Disconnected,
            ws_reconnect_secs: None,

            error_banner: None,
            error_dismiss_at: None,
        }
    }

    /// Switch to a workspace.
    pub fn switch_workspace(&mut self, ws: Workspace) {
        self.workspace = ws;
        self.panel_focus = PanelFocus::Left;
        self.popup = None;
    }

    /// Toggle panel focus between left and right.
    pub fn toggle_panel_focus(&mut self) {
        self.panel_focus = match self.panel_focus {
            PanelFocus::Left => PanelFocus::Right,
            PanelFocus::Right => PanelFocus::Left,
        };
    }

    /// Open a popup overlay.
    pub fn open_popup(&mut self, kind: PopupKind) {
        self.popup = Some(kind);
    }

    /// Close the active popup.
    pub fn close_popup(&mut self) {
        self.popup = None;
    }

    pub fn set_error(&mut self, msg: String) {
        self.error_banner = Some(msg);
        self.error_dismiss_at =
            Some(std::time::Instant::now() + std::time::Duration::from_secs(5));
    }

    /// Push an alert to the ticker.
    pub fn push_alert(&mut self, kind: AlertKind, message: String) {
        let ts = chrono_lite_now();
        self.alerts.push_back(Alert {
            timestamp: ts,
            message,
            kind,
        });
        // Keep last 50 alerts
        while self.alerts.len() > 50 {
            self.alerts.pop_front();
        }
    }

    /// Adaptive tick rate in milliseconds.
    pub fn tick_rate_ms(&self) -> u64 {
        if !self.boot_complete {
            100 // Fast during boot animation
        } else if self.has_active_animations() {
            100 // 10 FPS during animations
        } else if self.has_running_backtests() {
            250 // 4 FPS when monitoring
        } else {
            500 // 2 FPS when idle
        }
    }

    pub fn has_running_backtests(&self) -> bool {
        self.backtests
            .iter()
            .any(|b| b.status.starts_with("running") || b.status == "queued")
    }

    pub fn has_active_animations(&self) -> bool {
        // For now, just check if ticker is scrolling
        !self.alerts.is_empty()
    }

    pub fn tick(&mut self) {
        self.frame_count = self.frame_count.wrapping_add(1);

        // Boot animation progression
        if !self.boot_complete {
            if self.boot_phase < 6 {
                // Advance boot phase every 5 frames (500ms at 100ms tick)
                // Total boot: ~3s across 6 phases
                if self.frame_count % 5 == 0 {
                    self.boot_phase += 1;
                }
            } else {
                self.boot_complete = true;
            }
        }

        // Scroll ticker
        if !self.alerts.is_empty() {
            self.ticker_offset = self.ticker_offset.wrapping_add(1);
        }

        // Auto-dismiss error banner
        if let Some(dismiss_at) = self.error_dismiss_at {
            if std::time::Instant::now() >= dismiss_at {
                self.error_banner = None;
                self.error_dismiss_at = None;
            }
        }
    }

    /// Push an entry to the event log.
    pub fn push_log(&mut self, event_type: &str, message: String) {
        let ts = chrono_lite_now();
        self.event_log.push_back(EventLogEntry {
            timestamp: ts,
            event_type: event_type.to_string(),
            message,
        });
        while self.event_log.len() > 200 {
            self.event_log.pop_front();
        }
    }

    /// Handle an incoming WebSocket event.
    pub fn handle_ws_event(&mut self, event: WsEvent) {
        match event {
            WsEvent::BacktestProgress { run_id, pct, .. } => {
                let id_short = run_id.get(..6).unwrap_or(&run_id).to_string();
                self.push_log("bt.prog", format!("#{} {}%", id_short, pct));
                if let Some(bt) = self.backtests.iter_mut().find(|b| b.run_id == run_id) {
                    bt.status = format!("running ({}%)", pct);
                }
            }
            WsEvent::BacktestStats { run_id, trades, pnl, win_rate } => {
                let id_short = run_id.get(..6).unwrap_or(&run_id).to_string();
                self.push_log(
                    "bt.stat",
                    format!(
                        "#{} trades={} pnl={:.2} wr={:.1}%",
                        id_short,
                        trades.unwrap_or(0),
                        pnl.unwrap_or(0.0),
                        win_rate.unwrap_or(0.0) * 100.0,
                    ),
                );
            }
            WsEvent::BacktestCompleted {
                run_id, status, ..
            } => {
                let id_short = run_id.get(..8).unwrap_or(&run_id).to_string();
                let msg = format!("Backtest #{} {}", id_short, &status);
                let kind = if status == "completed" {
                    AlertKind::Success
                } else {
                    AlertKind::Error
                };
                self.push_log("bt.done", msg.clone());
                self.push_alert(kind, msg);
                if let Some(bt) = self.backtests.iter_mut().find(|b| b.run_id == run_id) {
                    bt.status = status;
                }
            }
            WsEvent::NodeHeartbeat { node_type, .. } => {
                let now = std::time::Instant::now();
                match node_type.as_str() {
                    "sandbox" => self.sandbox_last_heartbeat = Some(now),
                    "live" => self.live_last_heartbeat = Some(now),
                    _ => {}
                }
                // Heartbeats are too frequent for the log — skip
            }
            WsEvent::SystemError { message } => {
                self.push_log("sys.err", message.clone());
                self.push_alert(AlertKind::Error, message.clone());
                self.set_error(message);
            }
            WsEvent::Unknown => {
                self.push_log("unknown", "unrecognized event".to_string());
            }
        }
    }
}

/// Lightweight UTC time string (HH:MM:SS) without pulling in chrono.
fn chrono_lite_now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let h = (secs % 86400) / 3600;
    let m = (secs % 3600) / 60;
    let s = secs % 60;
    format!("{:02}:{:02}:{:02}", h, m, s)
}

/// Get current UTC time as HH:MM for display.
pub fn utc_clock() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let h = (secs % 86400) / 3600;
    let m = (secs % 3600) / 60;
    format!("{:02}:{:02} UTC", h, m)
}
