use anyhow::Result;
use clap::Subcommand;

use crate::api::ApiClient;
use crate::types::BacktestRunRequest;

#[derive(Subcommand)]
pub enum BacktestCmd {
    /// Run a new backtest
    Run {
        /// Strategy name
        strategy: String,
        /// Symbol (e.g., BTCUSDT-PERP)
        #[arg(long)]
        symbol: String,
        /// Interval (e.g., 5m, 1h)
        #[arg(long, default_value = "1m")]
        interval: String,
        /// Start date (YYYY-MM-DD)
        #[arg(long)]
        start: String,
        /// End date (YYYY-MM-DD)
        #[arg(long)]
        end: String,
        /// Initial capital
        #[arg(long, default_value = "10000")]
        capital: f64,
        /// Leverage
        #[arg(long, default_value = "1")]
        leverage: f64,
        /// Strategy parameters (key=value)
        #[arg(long = "param", value_parser = parse_param)]
        params: Vec<(String, String)>,
    },
    /// List backtest runs
    List,
    /// Get backtest result
    Result {
        /// Run ID (full or short prefix)
        run_id: String,
    },
    /// Get backtest status
    Status {
        /// Run ID (full or short prefix)
        run_id: String,
    },
    /// Cancel a backtest
    Cancel {
        /// Run ID (full or short prefix)
        run_id: String,
    },
}

fn parse_param(s: &str) -> std::result::Result<(String, String), String> {
    let pos = s.find('=').ok_or_else(|| format!("invalid param: no '=' in '{s}'"))?;
    Ok((s[..pos].to_string(), s[pos + 1..].to_string()))
}

pub async fn dispatch(cmd: BacktestCmd, client: &ApiClient, format: &str) -> Result<()> {
    match cmd {
        BacktestCmd::Run {
            strategy,
            symbol,
            interval,
            start,
            end,
            capital,
            leverage,
            params,
        } => {
            let param_json = if params.is_empty() {
                None
            } else {
                let map: serde_json::Map<String, serde_json::Value> = params
                    .into_iter()
                    .map(|(k, v)| (k, serde_json::Value::String(v)))
                    .collect();
                Some(serde_json::Value::Object(map))
            };

            let req = BacktestRunRequest {
                strategy,
                symbols: vec![symbol],
                intervals: vec![interval],
                start_date: start,
                end_date: end,
                initial_capital: capital,
                leverage,
                params: param_json,
            };

            let resp = client.run_backtest(&req).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&serde_json::json!({
                    "run_id": resp.run_id,
                    "status": resp.status
                }))?);
            } else {
                println!("  Backtest queued: {}", &resp.run_id[..8]);
            }
        }
        BacktestCmd::List => {
            let data = client.list_backtests().await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&data.runs)?);
                return Ok(());
            }
            // Table header
            println!(
                "  {:<10} {:<18} {:<15} {:>4}  {:<25} {:>8}  {:>6}  {:>12} {:>8} {:>7} {:>7}",
                "ID", "Strategy", "Symbol", "Ivl", "Period", "Status", "Trades", "PnL", "Ret%", "Sharpe", "WinRate"
            );
            println!("  {}", "-".repeat(109));
            for r in &data.runs {
                let id = &r.run_id[..8.min(r.run_id.len())];
                let strategy = r.strategy_name.as_deref().unwrap_or("-");
                let period = format!("{} ~ {}", r.start_date, r.end_date);
                let summary = r.result_summary.as_ref();
                let trades = summary
                    .and_then(|s| s.get("total_trades").and_then(|v| v.as_u64()))
                    .map(|v| v.to_string())
                    .unwrap_or_else(|| "-".into());
                let pnl = summary
                    .and_then(|s| s.get("total_pnl").and_then(|v| v.as_f64()))
                    .map(|v| format!("{:+.2}", v))
                    .unwrap_or_else(|| "-".into());
                let ret_pct = summary
                    .and_then(|s| s.get("return_pct").and_then(|v| v.as_f64()))
                    .map(|v| format!("{:+.2}", v))
                    .unwrap_or_else(|| "-".into());
                let sharpe = summary
                    .and_then(|s| s.get("sharpe_ratio").and_then(|v| v.as_f64()))
                    .map(|v| format!("{:.2}", v))
                    .unwrap_or_else(|| "-".into());
                let win_rate = summary
                    .and_then(|s| s.get("win_rate").and_then(|v| v.as_f64()))
                    .map(|v| format!("{:.1}%", v * 100.0))
                    .unwrap_or_else(|| "-".into());

                println!(
                    "  {:<10} {:<18} {:<15} {:>4}  {:<25} {:>8}  {:>6}  {:>12} {:>8} {:>7} {:>7}",
                    id,
                    &strategy[..18.min(strategy.len())],
                    &r.symbol[..15.min(r.symbol.len())],
                    &r.interval,
                    period,
                    r.status,
                    trades,
                    pnl,
                    ret_pct,
                    sharpe,
                    win_rate,
                );
            }
        }
        BacktestCmd::Result { run_id } => {
            let data = client.get_result(&run_id).await?;
            println!("{}", serde_json::to_string_pretty(&data)?);
        }
        BacktestCmd::Status { run_id } => {
            let data = client.get_status(&run_id).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&serde_json::json!({
                    "run_id": data.run_id,
                    "status": data.status,
                    "progress_pct": data.progress_pct,
                    "error": data.error,
                }))?);
            } else {
                println!("  Run:      {}", &data.run_id[..8]);
                println!("  Status:   {}", data.status);
                if let Some(pct) = data.progress_pct {
                    println!("  Progress: {}%", pct);
                }
                if let Some(err) = &data.error {
                    println!("  Error:    {}", err);
                }
            }
        }
        BacktestCmd::Cancel { run_id } => {
            let data = client.cancel_backtest(&run_id).await?;
            println!("  Cancelled: {} ({})", &data.run_id[..8], data.status);
        }
    }
    Ok(())
}
