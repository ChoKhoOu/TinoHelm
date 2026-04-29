use serde::{Deserialize, Serialize};

// ---- Backtest ----

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct BacktestRunItem {
    pub run_id: String,
    pub strategy_name: Option<String>,
    pub symbol: String,
    pub interval: String,
    pub start_date: String,
    pub end_date: String,
    pub status: String,
    pub progress_pct: Option<u8>,
    pub created_at: Option<String>,
    pub completed_at: Option<String>,
    pub result_summary: Option<serde_json::Value>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct BacktestRunList {
    pub runs: Vec<BacktestRunItem>,
    #[allow(dead_code)]
    pub total: u64,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct BacktestRunStatus {
    pub run_id: String,
    pub status: String,
    pub error: Option<String>,
    pub progress_pct: Option<u8>,
    pub result: Option<serde_json::Value>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct BacktestRunResponse {
    pub run_id: String,
    pub status: String,
}

#[derive(Debug, Serialize)]
pub struct BacktestRunRequest {
    pub strategy: String,
    pub symbols: Vec<String>,
    pub intervals: Vec<String>,
    pub start_date: String,
    pub end_date: String,
    pub initial_capital: f64,
    pub leverage: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub params: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fill_model: Option<serde_json::Value>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct BacktestCancelResponse {
    pub run_id: String,
    pub status: String,
}

// ---- Strategy ----

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Strategy {
    pub id: Option<u64>,
    pub name: String,
    #[serde(rename = "type")]
    pub strategy_type: Option<String>,
    pub strategy_class: Option<String>,
    pub config_class: Option<String>,
    pub file_path: Option<String>,
    pub symbols: Option<Vec<String>>,
    pub actors: Option<Vec<String>>,
    pub interval: Option<String>,
    pub created_at: Option<String>,
    pub updated_at: Option<String>,
    pub versions: Option<Vec<StrategyVersion>>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct StrategyVersion {
    pub version: u32,
    pub code_hash: Option<String>,
    pub created_at: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ValidateResult {
    pub valid: bool,
    pub issues: Option<Vec<String>>,
    pub strategy_class: Option<String>,
    pub config_class: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct RescanResult {
    pub discovered: u32,
    pub strategies: Vec<String>,
}

// ---- Optimization ----

#[derive(Debug, Serialize)]
pub struct OptimizeRequest {
    pub strategy: String,
    pub symbol: String,
    pub interval: String,
    pub start_date: String,
    pub end_date: String,
    pub n_trials: u32,
    pub fitness_objective: String,
    pub train_pct: f64,
    pub initial_capital: f64,
    pub leverage: f64,
    pub n_workers: u32,
    pub walk_forward_folds: u32,
    pub pruning: bool,
    pub sampler: String,
    pub patience: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub param_ranges: Option<serde_json::Value>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct OptimizeResponse {
    pub optimization_id: u64,
    pub status: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct OptimizeStatus {
    pub status: String,
    pub trials_completed: Option<u32>,
    pub total_trials: Option<u32>,
    pub best_value: Option<f64>,
    pub pruned_trials: Option<u32>,
}

// ---- Strategy Create ----

#[derive(Debug, Serialize)]
pub struct StrategyCreateRequest {
    pub name: String,
    #[serde(rename = "type")]
    pub strategy_type: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StrategyCreateResponse {
    pub name: String,
    pub file_path: Option<String>,
    pub message: Option<String>,
}

// ---- Data ----

#[derive(Debug, Serialize)]
pub struct DataFetchRequest {
    pub symbol: String,
    pub interval: String,
    pub start: String,
    pub end: String,
}

#[derive(Debug, Serialize)]
pub struct DataFetchBatchRequest {
    pub symbols: Vec<String>,
    pub intervals: Vec<String>,
    pub start: String,
    pub end: String,
}

#[derive(Debug, Serialize)]
pub struct DataCompactRequest {
    pub symbol: String,
    pub interval: String,
}

// ---- Trading (positions & fills) ----

#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TradingPosition {
    #[serde(default)]
    pub id: u64,
    pub node_type: String,
    pub position_id: String,
    #[serde(alias = "strategy_id")]
    pub strategy_id_tag: String,
    pub instrument_id: String,
    pub side: String, // "LONG", "SHORT", "FLAT"
    pub quantity: String,
    #[serde(default)]
    pub signed_qty: f64,
    pub avg_px_open: Option<f64>,
    pub avg_px_close: Option<f64>,
    pub realized_pnl: Option<f64>,
    pub unrealized_pnl: Option<f64>,
    pub currency: Option<String>,
    pub entry_side: Option<String>,
    pub peak_qty: Option<String>,
    pub ts_opened: Option<String>,
    pub ts_closed: Option<String>,
    #[serde(alias = "duration_ns")]
    pub duration: Option<String>,
    #[serde(default)]
    pub is_open: bool,
    #[serde(default)]
    pub event_count: u32,
    pub updated_at: Option<String>,
}

#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TradingFill {
    #[serde(default)]
    pub id: u64,
    pub node_type: String,
    pub trade_id: String,
    pub position_id: Option<String>,
    #[serde(default)]
    pub client_order_id: String,
    pub venue_order_id: Option<String>,
    #[serde(alias = "strategy_id")]
    pub strategy_id_tag: Option<String>,
    pub instrument_id: String,
    pub order_side: String, // "BUY", "SELL"
    pub last_qty: String,
    pub last_px: String,
    pub commission: Option<String>,
    pub liquidity_side: Option<String>,
    #[serde(alias = "ts")]
    pub ts_event: String,
    pub created_at: Option<String>,
}

#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TradingSummary {
    pub open_positions: u32,
    pub total_positions: u32,
    pub total_fills: u32,
    pub total_realized_pnl: f64,
    pub open_instruments: Vec<String>,
}

// ---- Trading (orders) ----

#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TradingOrder {
    #[serde(default)]
    pub id: u64,
    pub node_type: String,
    pub order_id: String,
    pub instrument_id: String,
    pub side: String,       // "BUY", "SELL"
    pub order_type: String, // "LIMIT", "STOP_MARKET", etc.
    pub quantity: String,
    pub price: Option<String>,
    pub status: String, // "ACCEPTED", "SUBMITTED", "PARTIALLY_FILLED"
    #[serde(alias = "strategy_id")]
    pub strategy_id_tag: Option<String>,
    pub created_at: Option<String>,
}

// ---- Node Portfolios ----

/// Portfolio state on a trading node
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct NodePortfolio {
    pub state: String, // "available", "starting", "running", "paused", "flattening"
    pub strategy_ids: Vec<String>,
    pub source_path: Option<String>,
    pub order_id_tag_prefix: Option<String>,
    #[serde(default)]
    pub was_running: bool,
}

/// Request body for portfolio lifecycle commands
#[derive(Debug, Serialize)]
pub struct PortfolioActionRequest {
    pub name: String,
    pub mode: String,
}

/// Response from GET /api/node/portfolios
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct PortfoliosResponse {
    #[serde(default)]
    pub portfolios: std::collections::HashMap<String, NodePortfolio>,
}

// ---- WebSocket events ----

#[derive(Debug, Deserialize, Clone)]
#[serde(tag = "type")]
#[allow(dead_code)]
pub enum WsEvent {
    #[serde(rename = "backtest.progress")]
    BacktestProgress {
        run_id: String,
        pct: u8,
        elapsed_secs: Option<f64>,
    },
    #[serde(rename = "backtest.stats")]
    BacktestStats {
        run_id: String,
        trades: Option<u32>,
        pnl: Option<f64>,
        win_rate: Option<f64>,
    },
    #[serde(rename = "backtest.completed")]
    BacktestCompleted {
        run_id: String,
        status: String,
        summary: Option<serde_json::Value>,
    },
    #[serde(rename = "backtest.failed")]
    BacktestFailed {
        run_id: String,
        status: String,
        error: Option<String>,
    },
    #[serde(rename = "backtest.cancelled")]
    BacktestCancelled { run_id: String, status: String },
    #[serde(rename = "node.heartbeat")]
    NodeHeartbeat {
        node_type: String,
        ts: Option<String>,
        uptime: Option<String>,
        strategies: Option<u32>,
        positions: Option<u32>,
        trading_state: Option<String>,
        strategy_states: Option<std::collections::HashMap<String, String>>,
    },
    #[serde(rename = "system.error")]
    SystemError { message: String },

    #[serde(rename = "position.update")]
    PositionUpdate(serde_json::Value),
    #[serde(rename = "fill.new")]
    FillNew(serde_json::Value),

    #[serde(other)]
    Unknown,
}
