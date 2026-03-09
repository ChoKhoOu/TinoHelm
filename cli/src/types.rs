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
    pub created_at: Option<String>,
    pub completed_at: Option<String>,
    pub result_summary: Option<serde_json::Value>,
}

#[derive(Debug, Deserialize)]
pub struct BacktestRunList {
    pub runs: Vec<BacktestRunItem>,
    #[allow(dead_code)]
    pub total: u64,
}

#[derive(Debug, Deserialize)]
pub struct BacktestRunStatus {
    pub run_id: String,
    pub status: String,
    pub error: Option<String>,
    pub progress_pct: Option<u8>,
    pub result: Option<serde_json::Value>,
}

#[derive(Debug, Deserialize)]
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

#[derive(Debug, Deserialize)]
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
    pub symbols: Vec<String>,
    pub intervals: Vec<String>,
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

#[derive(Debug, Deserialize)]
pub struct OptimizeResponse {
    pub optimization_id: u64,
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

#[derive(Debug, Deserialize)]
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
    #[serde(rename = "node.heartbeat")]
    NodeHeartbeat {
        node_type: String,
        ts: Option<String>,
    },
    #[serde(rename = "system.error")]
    SystemError { message: String },

    #[serde(other)]
    Unknown,
}
