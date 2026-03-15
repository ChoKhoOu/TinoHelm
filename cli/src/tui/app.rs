use std::collections::VecDeque;

use crate::types::{BacktestRunItem, Strategy, TradingFill, TradingPosition, TradingSummary, WsEvent};

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

/// Action pending user confirmation via the Confirm popup.
#[derive(Debug, Clone)]
pub enum PendingAction {
    DeleteBacktest { run_id: String },
    LifecycleAction {
        action: String,
        mode: String,
        strategy_id: Option<String>,
    },
}

/// WebSocket connection state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WsState {
    Disconnected,
    Connecting,
    Connected,
}

/// Which view is active in the Nodes workspace.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NodeView {
    Overview,
    StrategyDetail,
}

/// An alert for the ticker bar.
#[derive(Debug, Clone)]
pub struct Alert {
    pub timestamp: String,
    pub message: String,
    pub kind: AlertKind,
    pub created_at: std::time::Instant,
}

/// How long alerts stay visible before auto-pruning (seconds).
const ALERT_TTL_SECS: u64 = 5;

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
    pub pending_action: Option<PendingAction>,

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
    pub form_suggestions: Vec<String>,
    pub form_suggestion_idx: usize,

    // ── Strategy state ──────────────────────────────────────────────────
    pub strategies: Vec<Strategy>,
    pub strategy_selected: usize,
    pub strategy_loading: bool,

    // ── Node state ──────────────────────────────────────────────────────
    pub node_status: Option<serde_json::Value>,
    pub node_loading: bool,
    pub sandbox_last_heartbeat: Option<std::time::Instant>,
    pub live_last_heartbeat: Option<std::time::Instant>,
    pub node_view: NodeView,
    pub lifecycle_state: Option<serde_json::Value>,
    pub sandbox_filter_instrument: Option<String>,
    pub selected_strategy_tag: Option<String>,
    pub sandbox_shutting_down: bool,
    pub sandbox_group_by_strategy: bool,

    // ── Data catalog state ──────────────────────────────────────────────
    pub data_catalog: Option<serde_json::Value>,
    pub data_selected: usize,
    pub data_loading: bool,

    // ── Trading (positions & fills) ───────────────────────────────────
    pub positions: Vec<TradingPosition>,
    pub fills: Vec<TradingFill>,
    pub trading_summary: Option<TradingSummary>,
    pub trading_loading: bool,
    pub trading_dirty: bool,      // set by WS events to trigger data refresh
    pub trading_selected: usize,  // selected position index

    // ── Event log ─────────────────────────────────────────────────────
    pub event_log: VecDeque<EventLogEntry>,

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
            pending_action: None,

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
            form_suggestions: Vec::new(),
            form_suggestion_idx: 0,

            strategies: Vec::new(),
            strategy_selected: 0,
            strategy_loading: false,

            node_status: None,
            node_loading: false,
            sandbox_last_heartbeat: None,
            live_last_heartbeat: None,
            node_view: NodeView::Overview,
            lifecycle_state: None,
            sandbox_filter_instrument: None,
            selected_strategy_tag: None,
            sandbox_shutting_down: false,
            sandbox_group_by_strategy: false,

            data_catalog: None,
            data_selected: 0,
            data_loading: false,

            positions: Vec::new(),
            fills: Vec::new(),
            trading_summary: None,
            trading_loading: false,
            trading_dirty: false,
            trading_selected: 0,

            event_log: VecDeque::with_capacity(200),

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
        self.node_view = NodeView::Overview;
        self.selected_strategy_tag = None;
        self.sandbox_filter_instrument = None;
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
            created_at: std::time::Instant::now(),
        });
        // Keep last 50 alerts
        while self.alerts.len() > 50 {
            self.alerts.pop_front();
        }
    }

    /// Most recent alert (for ticker display).
    pub fn latest_alert(&self) -> Option<&Alert> {
        self.alerts.back()
    }

    /// Initialize backtest form with defaults and strategy suggestions.
    pub fn init_backtest_form(&mut self) {
        self.form_strategy.clear();
        self.form_focus = 0;
        self.form_suggestion_idx = 0;
        self.update_form_suggestions();

        // Pre-fill dates: Start = 3 months ago, End = today
        let (today, three_months_ago) = default_date_range();
        if self.form_start.is_empty() {
            self.form_start = three_months_ago;
        }
        if self.form_end.is_empty() {
            self.form_end = today;
        }
    }

    /// Update strategy suggestions based on current input.
    pub fn update_form_suggestions(&mut self) {
        let query = self.form_strategy.to_lowercase();
        self.form_suggestions = self
            .strategies
            .iter()
            .map(|s| s.name.clone())
            .filter(|name| query.is_empty() || name.to_lowercase().contains(&query))
            .collect();
        if self.form_suggestion_idx >= self.form_suggestions.len() {
            self.form_suggestion_idx = 0;
        }
    }

    /// Accept the currently selected suggestion into the strategy field.
    pub fn accept_suggestion(&mut self) {
        if let Some(name) = self.form_suggestions.get(self.form_suggestion_idx) {
            self.form_strategy = name.clone();
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
            .any(|b| b.status == "running" || b.status == "queued" || b.progress_pct.is_some())
    }

    pub fn has_active_animations(&self) -> bool {
        !self.alerts.is_empty()
            || self.backtest_loading
            || self.strategy_loading
            || self.node_loading
            || self.data_loading
            || self.trading_loading
            || self.sandbox_last_heartbeat.is_some()
            || self.live_last_heartbeat.is_some()
            || matches!(self.ws_state, WsState::Connected | WsState::Connecting)
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

        // Prune expired alerts, then advance ticker offset
        self.alerts
            .retain(|a| a.created_at.elapsed().as_secs() < ALERT_TTL_SECS);
        if !self.alerts.is_empty() {
            self.ticker_offset = self.ticker_offset.wrapping_add(1);
        }

        // Reset shutting_down flag if heartbeat has timed out (node is dead)
        if self.sandbox_shutting_down {
            let timed_out = match self.sandbox_last_heartbeat {
                Some(t) => std::time::Instant::now().duration_since(t).as_secs() >= 30,
                None => true,
            };
            if timed_out {
                self.sandbox_shutting_down = false;
            }
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
                    bt.status = "running".to_string();
                    bt.progress_pct = Some(pct);
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
                    bt.progress_pct = None; // Clear progress on completion
                }
            }
            WsEvent::NodeHeartbeat { node_type, trading_state, strategy_states, .. } => {
                let now = std::time::Instant::now();
                match node_type.as_str() {
                    "sandbox" => {
                        self.sandbox_last_heartbeat = Some(now);
                        self.sandbox_shutting_down = false; // heartbeat received = still alive
                        // Update lifecycle state from heartbeat (avoids polling)
                        if trading_state.is_some() || strategy_states.is_some() {
                            let mut state = serde_json::Map::new();
                            if let Some(ts) = &trading_state {
                                state.insert("trading_state".to_string(), serde_json::Value::String(ts.clone()));
                            }
                            if let Some(ss) = &strategy_states {
                                let ss_val: serde_json::Map<String, serde_json::Value> = ss.iter()
                                    .map(|(k, v)| (k.clone(), serde_json::Value::String(v.clone())))
                                    .collect();
                                state.insert("strategy_states".to_string(), serde_json::Value::Object(ss_val));
                                // Derive paused list
                                let paused: Vec<serde_json::Value> = ss.iter()
                                    .filter(|(_, v)| v.as_str() == "paused")
                                    .map(|(k, _)| serde_json::Value::String(k.clone()))
                                    .collect();
                                state.insert("paused".to_string(), serde_json::Value::Array(paused));
                            }
                            self.lifecycle_state = Some(serde_json::Value::Object(state));
                        }
                    }
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
            WsEvent::PositionUpdate(val) => {
                let id = val.get("position_id").and_then(|v| v.as_str()).unwrap_or("?");
                self.push_log("pos.upd", format!("Position {} updated", id));
                // Try inline update from WS payload
                if let Ok(mut pos) = serde_json::from_value::<TradingPosition>(val.clone()) {
                    // WS uses "strategy_id", DB/REST uses "strategy_id_tag" — map if needed
                    if pos.strategy_id_tag.is_empty() {
                        if let Some(sid) = val.get("strategy_id").and_then(|v| v.as_str()) {
                            pos.strategy_id_tag = sid.to_string();
                        }
                    }
                    // Upsert by position_id
                    if let Some(existing) = self.positions.iter_mut().find(|p| p.position_id == pos.position_id) {
                        *existing = pos;
                    } else {
                        self.positions.push(pos);
                    }
                } else {
                    // Fallback to full refresh
                    self.trading_dirty = true;
                }
            }
            WsEvent::FillNew(val) => {
                let id = val.get("trade_id").and_then(|v| v.as_str()).unwrap_or("?");
                self.push_log("fill.new", format!("Fill {}", id));
                // Try inline prepend from WS payload
                if let Ok(mut fill) = serde_json::from_value::<TradingFill>(val.clone()) {
                    // WS uses "strategy_id", DB/REST uses "strategy_id_tag" — map if needed
                    if fill.strategy_id_tag.is_none() {
                        if let Some(sid) = val.get("strategy_id").and_then(|v| v.as_str()) {
                            fill.strategy_id_tag = Some(sid.to_string());
                        }
                    }
                    // Prepend (most recent first) and dedup by trade_id
                    if !self.fills.iter().any(|f| f.trade_id == fill.trade_id) {
                        self.fills.insert(0, fill);
                        // Keep max 200 fills in memory
                        if self.fills.len() > 200 {
                            self.fills.truncate(200);
                        }
                    }
                } else {
                    // Fallback to full refresh
                    self.trading_dirty = true;
                }
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
    let hms = chrono_lite_now();
    format!("{} UTC", &hms[..5])
}

/// Return (today, 3_months_ago) as YYYY-MM-DD strings.
fn default_date_range() -> (String, String) {
    use std::time::{SystemTime, UNIX_EPOCH};
    let total_days = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
        / 86400;

    let today = days_to_ymd(total_days);
    let ago = days_to_ymd(total_days.saturating_sub(90));
    (today, ago)
}

/// Convert days since epoch to YYYY-MM-DD.
fn days_to_ymd(days: u64) -> String {
    // Civil calendar from days since 1970-01-01
    let z = days as i64 + 719468;
    let era = z.div_euclid(146097);
    let doe = z.rem_euclid(146097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = (yoe as i64) + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{:04}-{:02}-{:02}", y, m, d)
}
