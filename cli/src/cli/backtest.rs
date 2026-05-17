use crate::cli::style::{NEG, POS};
use anyhow::Result;
use clap::Subcommand;
use crossterm::style::Stylize;

use crate::api::ApiClient;
use crate::cli::style::*;
use crate::output::{print_json, OutputFormat};
use crate::types::{BacktestRunRequest, OptimizeRequest};

#[derive(Subcommand)]
pub enum BacktestCmd {
    /// Run a new backtest
    Run {
        /// Strategy name
        strategy: String,
        /// Symbol (e.g., BTCUSDT-PERP). Optional for portfolio strategies.
        #[arg(long)]
        symbol: Option<String>,
        /// Interval (e.g., 5m, 1h). Optional for portfolio strategies.
        #[arg(long)]
        interval: Option<String>,
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
        /// Bar data source type (klines, markPriceKlines, indexPriceKlines, premiumIndexKlines)
        #[arg(long = "data-type", default_value = "klines")]
        data_type: String,
        /// Optional quote/trade replay data type to inject (repeatable: bookTicker, aggTrades, trades)
        #[arg(long = "extra-data-type")]
        extra_data_types: Vec<String>,
        /// Probability of slippage (0.0-1.0)
        #[arg(long, default_value = "0")]
        slippage_prob: f64,
        /// Random seed for FillModel
        #[arg(long)]
        random_seed: Option<u64>,
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
    /// Wait for a backtest to complete
    Wait {
        /// Run ID (full or short prefix)
        run_id: String,
        /// Max seconds to wait
        #[arg(long, short, default_value = "300")]
        timeout: u64,
    },
    /// Cancel a backtest
    Cancel {
        /// Run ID (full or short prefix)
        run_id: String,
    },
    /// Delete a backtest run
    Delete {
        /// Run ID
        run_id: String,
    },
    /// Compare two backtest runs
    Compare {
        /// First run ID
        id1: String,
        /// Second run ID
        id2: String,
    },
    /// Estimate backtest duration
    Estimate {
        /// Strategy name
        strategy: String,
        /// Symbol
        #[arg(long)]
        symbol: String,
        /// Interval
        #[arg(long, default_value = "5m")]
        interval: String,
        /// Start date
        #[arg(long)]
        start: String,
        /// End date
        #[arg(long)]
        end: String,
    },
    /// Backtest artifacts management
    Artifacts {
        #[command(subcommand)]
        command: ArtifactsCmd,
    },
    /// Run hyperparameter optimization
    Optimize {
        /// Strategy name
        strategy: String,
        /// Symbol (e.g., BTCUSDT-PERP)
        #[arg(long, short)]
        symbol: String,
        /// Interval (e.g., 5m)
        #[arg(long, short, default_value = "5m")]
        interval: String,
        /// Start date (YYYY-MM-DD)
        #[arg(long)]
        start: String,
        /// End date (YYYY-MM-DD)
        #[arg(long)]
        end: String,
        /// Number of Optuna trials (0 = auto)
        #[arg(long, short = 'n', default_value = "0")]
        trials: u32,
        /// Objective: sharpe/calmar/sortino/profit
        #[arg(long, default_value = "sharpe")]
        fitness: String,
        /// Train percentage (50-99)
        #[arg(long, default_value = "85")]
        train_pct: f64,
        /// Parallel trial workers (0 = auto)
        #[arg(long, short = 'w', default_value = "0")]
        workers: u32,
        /// Walk-forward folds (0=disabled)
        #[arg(long, default_value = "0")]
        walk_forward: u32,
        /// Sampler: auto/tpe/cmaes/random
        #[arg(long, default_value = "auto")]
        sampler: String,
        /// Early stopping patience (0=disabled)
        #[arg(long, default_value = "0")]
        patience: u32,
        /// Disable trial pruning
        #[arg(long)]
        no_pruning: bool,
        /// Param range as name:min:max[:step[:type]]
        #[arg(long = "param", value_parser = parse_param_range)]
        params: Vec<(String, serde_json::Value)>,
        /// Initial capital
        #[arg(long, short = 'c', default_value = "10000")]
        capital: f64,
        /// Leverage
        #[arg(long, short = 'l', default_value = "1")]
        leverage: f64,
        /// Auto-poll progress until done
        #[arg(long, default_value = "true", action = clap::ArgAction::Set)]
        poll: bool,
        /// Max seconds to wait when polling (0 = no limit)
        #[arg(long, default_value = "3600")]
        timeout: u64,
    },
    /// Check optimization run status
    #[command(name = "optimize-status")]
    OptimizeStatus {
        /// Optimization run ID
        opt_id: u64,
    },
    /// Get full optimization results
    #[command(name = "optimize-result")]
    OptimizeResult {
        /// Optimization run ID
        opt_id: u64,
    },
    /// List optimization runs
    #[command(name = "optimize-list")]
    OptimizeList {
        /// Max results
        #[arg(long, default_value = "20")]
        limit: u32,
        /// Filter by strategy name
        #[arg(long)]
        strategy: Option<String>,
    },
}

#[derive(Subcommand)]
pub enum ArtifactsCmd {
    /// List artifacts for a backtest run
    List {
        /// Run ID
        run_id: String,
    },
    /// Download a specific artifact
    Get {
        /// Run ID
        run_id: String,
        /// Artifact filename
        filename: String,
        /// Output path
        #[arg(short, long)]
        output: Option<String>,
    },
}

fn parse_param(s: &str) -> std::result::Result<(String, String), String> {
    let pos = s
        .find('=')
        .ok_or_else(|| format!("invalid param: no '=' in '{s}'"))?;
    Ok((s[..pos].to_string(), s[pos + 1..].to_string()))
}

fn parse_param_range(s: &str) -> std::result::Result<(String, serde_json::Value), String> {
    let parts: Vec<&str> = s.split(':').collect();
    if parts.len() < 3 {
        return Err(format!(
            "invalid param range: expected name:min:max[:step[:type]], got '{s}'"
        ));
    }
    let name = parts[0].to_string();
    let min_val: f64 = parts[1]
        .parse()
        .map_err(|_| format!("invalid min for '{}'", name))?;
    let max_val: f64 = parts[2]
        .parse()
        .map_err(|_| format!("invalid max for '{}'", name))?;
    let mut spec = serde_json::json!({
        "type": "float",
        "min": min_val,
        "max": max_val,
    });
    if parts.len() >= 4 {
        if let Ok(step) = parts[3].parse::<f64>() {
            spec["step"] = serde_json::json!(step);
        }
    }
    if parts.len() >= 5 && (parts[4] == "int" || parts[4] == "float") {
        spec["type"] = serde_json::json!(parts[4]);
    }
    Ok((name, spec))
}

fn jf64(v: &serde_json::Value, key: &str) -> Option<f64> {
    v.get(key).and_then(|x| x.as_f64())
}

fn ju64(v: &serde_json::Value, key: &str) -> Option<u64> {
    v.get(key)
        .and_then(|x| x.as_u64().or_else(|| x.as_f64().map(|f| f as u64)))
}

fn jstr<'a>(v: &'a serde_json::Value, key: &str) -> &'a str {
    v.get(key).and_then(|x| x.as_str()).unwrap_or("-")
}

fn jdisplay(v: &serde_json::Value, key: &str) -> String {
    match v.get(key) {
        Some(serde_json::Value::String(s)) => s.clone(),
        Some(serde_json::Value::Number(n)) => n.to_string(),
        Some(serde_json::Value::Bool(b)) => b.to_string(),
        Some(serde_json::Value::Null) | None => "-".to_string(),
        Some(other) => other.to_string(),
    }
}

fn print_status_card(data: &crate::types::BacktestRunStatus, run_id: &str) {
    let st = &data.status;
    let pct = data.progress_pct.unwrap_or(0);

    println!();
    println!(
        "  {} Backtest {}  {}",
        status_badge(st),
        accent(&run_id[..8.min(run_id.len())]),
        color_status(st),
    );

    if st == "running" || st == "queued" {
        println!("      {}", progress_bar(pct, 30));
    }

    if st == "completed" {
        if let Some(ref result) = data.result {
            let stats = result.get("statistics").unwrap_or(&serde_json::Value::Null);
            let pnl = jf64(stats, "total_pnl");
            let ret = jf64(stats, "total_return_pct");
            let sharpe = jf64(stats, "sharpe_ratio");
            let trades = ju64(stats, "total_trades");
            let wr = jf64(stats, "win_rate");

            let parts = [
                format!("PnL: {}", color_value(pnl, "+.2f")),
                match ret {
                    Some(r) => format!("Return: {}%", color_value(Some(r), "+.2f")),
                    None => format!("Return: {}", muted("-")),
                },
                format!(
                    "Sharpe: {}",
                    sharpe
                        .map(|s| format!("{:.2}", s))
                        .unwrap_or_else(|| muted("-"))
                ),
                format!(
                    "Trades: {}",
                    trades.map(|t| t.to_string()).unwrap_or_else(|| muted("-"))
                ),
                match wr {
                    Some(w) => format!("WinRate: {:.1}%", w * 100.0),
                    None => format!("WinRate: {}", muted("-")),
                },
            ];
            println!("      {}", parts.join("  "));
        }
    }

    if let Some(ref err) = data.error {
        println!("      {} {}", "Error:".with(NEG).bold(), err);
    }
    println!();
}

fn print_result_report(data: &serde_json::Value, run_id: &str) {
    let r = data.get("result").unwrap_or(data);
    let null = serde_json::Value::Null;
    let stats = r.get("statistics").unwrap_or(&null);

    if !stats.is_object() {
        println!("{}", serde_json::to_string_pretty(data).unwrap_or_default());
        return;
    }

    let empty_vec = vec![];
    let equity_curve = r
        .get("equity_curve")
        .and_then(|v| v.as_array())
        .unwrap_or(&empty_vec);
    let trade_log = r
        .get("trade_log")
        .and_then(|v| v.as_array())
        .unwrap_or(&empty_vec);

    let pnl = jf64(stats, "total_pnl");
    let ret = jf64(stats, "total_return_pct");
    let balance = jstr(stats, "final_balance");

    let bx = BoxReport::new(62);

    println!();
    bx.top();
    let title = format!(
        "  BACKTEST REPORT  {}",
        accent(&run_id[..8.min(run_id.len())])
    );
    bx.line(&bold(&title));
    bx.mid();

    bx.line(&format!(
        "  PnL: {} USDT  ({}%)     Balance: {}",
        color_value(pnl, "+.2f"),
        color_value(ret, "+.2f"),
        balance,
    ));
    bx.mid();

    bx.line(&bold("  Risk Metrics"));
    bx.pair(
        "Sharpe",
        &format_value(jf64(stats, "sharpe_ratio"), ".2f", ""),
        "Sortino",
        &format_value(jf64(stats, "sortino_ratio"), ".2f", ""),
    );
    bx.pair(
        "Calmar",
        &format_value(jf64(stats, "calmar_ratio"), ".2f", ""),
        "Max Drawdown",
        &format_value(jf64(stats, "max_drawdown"), ".2f", "%"),
    );
    bx.mid();

    bx.line(&bold("  Trade Statistics"));
    let total_trades = ju64(stats, "total_trades").unwrap_or(0);
    let w_trades = ju64(stats, "winning_trades").unwrap_or(0);
    let l_trades = ju64(stats, "losing_trades").unwrap_or(0);
    let wr = jf64(stats, "win_rate");

    if total_trades > 0 {
        let w_pct = w_trades as f64 / total_trades as f64;
        let bar_w: usize = 28;
        let w_fill = (bar_w as f64 * w_pct) as usize;
        let l_fill = bar_w.saturating_sub(w_fill);
        let bar = format!(
            "{}{}",
            "\u{2588}".repeat(w_fill).with(POS),
            "\u{2588}".repeat(l_fill).with(NEG),
        );
        let wr_str = wr
            .map(|w| format!("{:.1}%", w * 100.0))
            .unwrap_or_else(|| "-".to_string());
        bx.line(&format!(
            "  [{}]  {}W {}L  WR: {}",
            bar, w_trades, l_trades, wr_str,
        ));
    }

    bx.pair(
        "Profit Factor",
        &format_value(jf64(stats, "profit_factor"), ".2f", ""),
        "Expectancy",
        &color_value(jf64(stats, "expectancy"), "+.2f"),
    );

    if equity_curve.len() >= 2 {
        let values: Vec<f64> = equity_curve
            .iter()
            .filter_map(|p| p.get("equity").and_then(|v| v.as_f64()))
            .filter(|v| *v > 0.0)
            .collect();
        if !values.is_empty() {
            bx.mid();
            bx.line(&bold("  Equity Curve"));
            let mn = values.iter().cloned().fold(f64::INFINITY, f64::min);
            let mx = values.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
            let rng = if mx > mn { mx - mn } else { 1.0 };
            let spark_chars = [
                '\u{2581}', '\u{2582}', '\u{2583}', '\u{2584}', '\u{2585}', '\u{2586}', '\u{2587}',
                '\u{2588}',
            ];
            let target = values.len().min(58);
            let step = (values.len() / target).max(1);
            let sampled: Vec<f64> = values.iter().step_by(step).take(target).cloned().collect();
            let spark: String = sampled
                .iter()
                .map(|v| {
                    let idx = ((v - mn) / rng * 7.0) as usize;
                    spark_chars[idx.min(7)]
                })
                .collect();
            bx.line(&format!("  {}", spark.cyan()));
        }
    }

    if trade_log.len() > 1 {
        bx.mid();
        bx.line(&bold("  Top Trades (by PnL)"));
        let mut sorted_trades: Vec<&serde_json::Value> = trade_log.iter().collect();
        sorted_trades.sort_by(|a, b| {
            let pa = trade_pnl(a);
            let pb = trade_pnl(b);
            pb.partial_cmp(&pa).unwrap_or(std::cmp::Ordering::Equal)
        });
        let top_n = sorted_trades.len().min(3);
        for t in sorted_trades.iter().take(top_n) {
            let side = match jstr(t, "side") {
                "1" | "BUY" | "LONG" => "LONG",
                _ => "SHORT",
            };
            let pnl_t = trade_pnl(t);
            let qty = jstr(t, "quantity");
            bx.line(&format!(
                "  {} {:5}  qty={}  pnl={}",
                "+".with(POS),
                side,
                qty,
                color_value(Some(pnl_t), "+.2f"),
            ));
        }
    }

    bx.bot();
    println!();
}

fn trade_pnl(t: &serde_json::Value) -> f64 {
    if let Some(v) = t.get("realized_pnl").and_then(|v| v.as_f64()) {
        return v;
    }
    jstr(t, "realized_pnl")
        .split_whitespace()
        .next()
        .and_then(|s| s.parse::<f64>().ok())
        .unwrap_or(0.0)
}

pub async fn dispatch(cmd: BacktestCmd, client: &ApiClient, format: OutputFormat) -> Result<()> {
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
            data_type,
            extra_data_types,
            slippage_prob,
            random_seed,
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

            let fill_model = if slippage_prob > 0.0 || random_seed.is_some() {
                let mut fm = serde_json::json!({"prob_slippage": slippage_prob});
                if let Some(seed) = random_seed {
                    fm["random_seed"] = serde_json::json!(seed);
                }
                Some(fm)
            } else {
                None
            };

            let symbols = match &symbol {
                Some(s) => vec![s.clone()],
                None => vec![],
            };
            let intervals = match &interval {
                Some(i) => vec![i.clone()],
                None => vec![],
            };

            let req = BacktestRunRequest {
                strategy: strategy.clone(),
                symbols: symbols.clone(),
                intervals: intervals.clone(),
                start_date: start.clone(),
                end_date: end.clone(),
                initial_capital: capital,
                leverage,
                params: param_json,
                fill_model,
                data_type,
                extra_data_types,
            };

            let resp = client.run_backtest(&req).await?;
            let data = serde_json::json!({
                "run_id": resp.run_id,
                "status": resp.status
            });
            match format {
                OutputFormat::Json => print_json(&data),
                OutputFormat::Text => {
                    header("Backtest Submitted");
                    divider(50);
                    kv("Run ID", &accent(&resp.run_id), 12);
                    kv("Strategy", &strategy, 12);
                    println!();
                    println!(
                        "    Track: {}",
                        dim(&format!("tino backtest wait {}", resp.run_id))
                    );
                    println!();
                    Ok(())
                }
            }
        }
        BacktestCmd::List => {
            let data = client.list_backtests().await?;
            match format {
                OutputFormat::Json => print_json(&data.runs),
                OutputFormat::Text => {
                    let t = Table::new(&[
                        ("ID", 8, "left"),
                        ("Strategy", 16, "left"),
                        ("Symbol", 14, "left"),
                        ("Status", 10, "right"),
                        ("PnL", 10, "right"),
                    ]);
                    t.header();
                    for r in &data.runs {
                        let id = &r.run_id[..8.min(r.run_id.len())];
                        let strat = r.strategy_name.as_deref().unwrap_or("?");
                        let pnl = r
                            .result_summary
                            .as_ref()
                            .and_then(|s| s.get("total_pnl").and_then(|v| v.as_f64()))
                            .map(|v| color_value(Some(v), "+.2f"))
                            .unwrap_or_else(|| muted("-"));
                        t.row(&[
                            &accent(id),
                            &strat[..16.min(strat.len())],
                            &r.symbol[..14.min(r.symbol.len())],
                            &color_status(&r.status),
                            &pnl,
                        ]);
                    }
                    t.footer();
                    Ok(())
                }
            }
        }
        BacktestCmd::Result { run_id } => {
            let data = client.get_result(&run_id).await?;
            match format {
                OutputFormat::Json => print_json(&data),
                OutputFormat::Text => {
                    print_result_report(&data, &run_id);
                    Ok(())
                }
            }
        }
        BacktestCmd::Status { run_id } => {
            let data = client.get_status(&run_id).await?;
            let json_data = serde_json::json!({
                "run_id": data.run_id,
                "status": data.status,
                "progress_pct": data.progress_pct,
                "error": data.error,
            });
            match format {
                OutputFormat::Json => print_json(&json_data),
                OutputFormat::Text => {
                    print_status_card(&data, &run_id);
                    Ok(())
                }
            }
        }
        BacktestCmd::Wait { run_id, timeout } => {
            use std::io::Write;
            let mut elapsed = 0u64;
            let poll_interval = 2u64;

            if format.is_json() {
                loop {
                    let data = client.get_status(&run_id).await?;
                    let st = data.status.clone();
                    if matches!(st.as_str(), "completed" | "failed" | "error" | "cancelled") {
                        print_json(&serde_json::json!(data))?;
                        if st != "completed" {
                            std::process::exit(1);
                        }
                        return Ok(());
                    }
                    if elapsed >= timeout {
                        print_json(&serde_json::json!({
                            "run_id": run_id,
                            "status": "timeout",
                            "timeout_sec": timeout,
                        }))?;
                        std::process::exit(1);
                    }
                    tokio::time::sleep(std::time::Duration::from_secs(poll_interval)).await;
                    elapsed += poll_interval;
                }
            }

            println!();
            while elapsed < timeout {
                let data = client.get_status(&run_id).await?;
                let st = &data.status;
                let pct = data.progress_pct.unwrap_or(0);

                print!("\r{}", inline_progress(pct, st, elapsed, 20));
                std::io::stdout().flush()?;

                if matches!(st.as_str(), "completed" | "failed" | "error" | "cancelled") {
                    println!();
                    print_status_card(&data, &run_id);
                    if st != "completed" {
                        std::process::exit(1);
                    }
                    return Ok(());
                }
                tokio::time::sleep(std::time::Duration::from_secs(poll_interval)).await;
                elapsed += poll_interval;
            }
            println!();
            eprintln!("  {} after {}s", "Timeout".with(NEG).bold(), timeout,);
            std::process::exit(1);
        }
        BacktestCmd::Cancel { run_id } => {
            let data = client.cancel_backtest(&run_id).await?;
            let json_data = serde_json::json!({
                "run_id": data.run_id,
                "status": data.status,
            });
            match format {
                OutputFormat::Json => print_json(&json_data),
                OutputFormat::Text => {
                    println!(
                        "  Cancelled {}",
                        accent(&data.run_id[..8.min(data.run_id.len())])
                    );
                    Ok(())
                }
            }
        }
        BacktestCmd::Delete { run_id } => {
            let data = client.delete_backtest(&run_id).await?;
            match format {
                OutputFormat::Json => print_json(&data),
                OutputFormat::Text => {
                    println!(
                        "  Deleted backtest {}",
                        accent(&run_id[..8.min(run_id.len())])
                    );
                    Ok(())
                }
            }
        }
        BacktestCmd::Compare { id1, id2 } => {
            let r1 = client.get_result(&id1).await?;
            let r2 = client.get_result(&id2).await?;
            let comparison = serde_json::json!({
                "run_a": { "run_id": id1, "result": r1 },
                "run_b": { "run_id": id2, "result": r2 },
            });
            match format {
                OutputFormat::Json => print_json(&comparison),
                OutputFormat::Text => {
                    header("Backtest Comparison");
                    divider(50);
                    println!(
                        "  A: {}  vs  B: {}",
                        accent(&id1[..8.min(id1.len())]),
                        accent(&id2[..8.min(id2.len())])
                    );
                    println!();
                    Ok(())
                }
            }
        }
        BacktestCmd::Estimate {
            strategy,
            symbol,
            interval,
            start,
            end,
        } => {
            let body = serde_json::json!({
                "strategy": strategy,
                "symbol": symbol,
                "interval": interval,
                "start_date": start,
                "end_date": end,
            });
            let resp = client
                .request_json(
                    reqwest::Method::POST,
                    "/api/backtest/estimate",
                    &[],
                    Some(body),
                    &[],
                )
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    header("Backtest Estimate");
                    divider(50);
                    println!("  {}", serde_json::to_string_pretty(&resp.body)?);
                    println!();
                    Ok(())
                }
            }
        }
        BacktestCmd::Artifacts { command } => dispatch_artifacts(command, client, format).await,
        BacktestCmd::Optimize {
            strategy,
            symbol,
            interval,
            start,
            end,
            trials,
            fitness,
            train_pct,
            workers,
            walk_forward,
            sampler,
            patience,
            no_pruning,
            params,
            capital,
            leverage,
            poll,
            timeout,
        } => {
            let param_ranges = if params.is_empty() {
                None
            } else {
                let map: serde_json::Map<String, serde_json::Value> = params.into_iter().collect();
                Some(serde_json::Value::Object(map))
            };

            let req = OptimizeRequest {
                strategy: strategy.clone(),
                symbol: symbol.clone(),
                interval: interval.clone(),
                start_date: start.clone(),
                end_date: end.clone(),
                n_trials: trials,
                fitness_objective: fitness.clone(),
                train_pct,
                initial_capital: capital,
                leverage,
                n_workers: workers,
                walk_forward_folds: walk_forward,
                pruning: !no_pruning,
                sampler: sampler.clone(),
                patience,
                param_ranges,
            };

            let resp = client.optimize_backtest(&req).await?;
            let opt_id = resp.optimization_id;

            let data = serde_json::json!({
                "optimization_id": opt_id,
                "status": resp.status,
            });

            if format.is_json() && !poll {
                return print_json(&data);
            }

            if format.is_json() {
                let poll_interval = 3u64;
                let mut elapsed = 0u64;
                loop {
                    tokio::time::sleep(std::time::Duration::from_secs(poll_interval)).await;
                    elapsed += poll_interval;
                    let sd = client.optimize_status(opt_id).await?;
                    if matches!(sd.status.as_str(), "completed" | "failed" | "error") {
                        if sd.status == "completed" {
                            let result = client.optimize_result(opt_id).await?;
                            return print_json(&result);
                        }
                        print_json(&sd)?;
                        std::process::exit(1);
                    }
                    if timeout > 0 && elapsed >= timeout {
                        print_json(&serde_json::json!({
                            "optimization_id": opt_id,
                            "status": "timeout",
                            "timeout_sec": timeout,
                        }))?;
                        std::process::exit(1);
                    }
                }
            }

            header("Optimization Started");
            divider(50);
            kv("ID", &accent(&opt_id.to_string()), 12);
            kv("Trials", &trials.to_string(), 12);
            kv("Fitness", &fitness, 12);
            println!();

            if !poll {
                return Ok(());
            }

            let poll_interval = 3u64;
            let mut elapsed = 0u64;
            loop {
                tokio::time::sleep(std::time::Duration::from_secs(poll_interval)).await;
                elapsed += poll_interval;
                let sd = client.optimize_status(opt_id).await?;
                let st = &sd.status;
                let done = sd.trials_completed.unwrap_or(0);
                let total = sd.total_trials.unwrap_or(trials);
                let best = sd.best_value;

                let pct = done.saturating_mul(100).checked_div(total).unwrap_or(0);
                let bar_w: u32 = 30;
                let filled = bar_w.saturating_mul(done).checked_div(total).unwrap_or(0);
                let bar = format!(
                    "{}{}",
                    "=".repeat(filled as usize),
                    "-".repeat((bar_w - filled) as usize),
                );
                let best_s = best
                    .map(|b| format!("  best={}", color_value(Some(b), ".4f")))
                    .unwrap_or_default();

                use std::io::Write;
                print!("\r  [{}] {}/{} ({}%){}", bar, done, total, pct, best_s);
                std::io::stdout().flush()?;

                if matches!(st.as_str(), "completed" | "failed" | "error") {
                    println!();
                    if st != "completed" {
                        eprintln!("  Optimization {}", st);
                        std::process::exit(1);
                    }
                    break;
                }
                if timeout > 0 && elapsed >= timeout {
                    println!();
                    eprintln!("  {} after {}s", "Timeout".with(NEG).bold(), timeout);
                    std::process::exit(1);
                }
            }

            let result_data = client.optimize_result(opt_id).await?;
            print_optimize_result(&result_data);
            Ok(())
        }
        BacktestCmd::OptimizeStatus { opt_id } => {
            let sd = client.optimize_status(opt_id).await?;
            match format {
                OutputFormat::Json => print_json(&sd),
                OutputFormat::Text => {
                    println!(
                        "  {} Optimization {}  {}",
                        status_badge(&sd.status),
                        accent(&opt_id.to_string()),
                        color_status(&sd.status),
                    );
                    Ok(())
                }
            }
        }
        BacktestCmd::OptimizeResult { opt_id } => {
            let data = client.optimize_result(opt_id).await?;
            match format {
                OutputFormat::Json => print_json(&data),
                OutputFormat::Text => {
                    print_optimize_result(&data);
                    Ok(())
                }
            }
        }
        BacktestCmd::OptimizeList { limit, strategy } => {
            let data = client.optimize_list(limit, strategy.as_deref()).await?;
            match format {
                OutputFormat::Json => print_json(&data),
                OutputFormat::Text => {
                    println!("{}", serde_json::to_string_pretty(&data)?);
                    Ok(())
                }
            }
        }
    }
}

async fn dispatch_artifacts(
    cmd: ArtifactsCmd,
    client: &ApiClient,
    format: OutputFormat,
) -> Result<()> {
    match cmd {
        ArtifactsCmd::List { run_id } => {
            let path = format!("/api/backtest/{}/artifacts", run_id);
            let resp = client
                .request_json(reqwest::Method::GET, &path, &[], None, &[])
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    header(&format!(
                        "Artifacts for {}",
                        accent(&run_id[..8.min(run_id.len())])
                    ));
                    println!("  {}", serde_json::to_string_pretty(&resp.body)?);
                    println!();
                    Ok(())
                }
            }
        }
        ArtifactsCmd::Get {
            run_id,
            filename,
            output,
        } => {
            let encoded_filename = urlencoding::encode(&filename);
            let path = format!("/api/backtest/{}/artifacts/{}", run_id, encoded_filename);
            let resp = client
                .request_bytes(reqwest::Method::GET, &path, &[], None, &[])
                .await?;
            let output_path = output.unwrap_or_else(|| filename.clone());
            std::fs::write(&output_path, &resp.bytes)?;
            let data = serde_json::json!({
                "path": output_path,
                "bytes": resp.bytes.len(),
            });
            match format {
                OutputFormat::Json => print_json(&data),
                OutputFormat::Text => {
                    println!(
                        "  Downloaded {} ({} bytes)",
                        accent(&output_path),
                        resp.bytes.len()
                    );
                    Ok(())
                }
            }
        }
    }
}

fn print_optimize_result(data: &serde_json::Value) {
    println!();
    divider(50);
    println!("  {}", bold("Optimization Result"));
    divider(50);

    kv("ID", &jdisplay(data, "optimization_id"), 12);
    kv("Status", &color_status(jstr(data, "status")), 12);

    if let Some(best) = jf64(data, "best_value") {
        header("Best Fitness");
        println!("    {}", color_value(Some(best), ".6f"));
    }

    if let Some(params) = data.get("best_params").and_then(|v| v.as_object()) {
        header("Best Parameters");
        let mut sorted: Vec<_> = params.iter().collect();
        sorted.sort_by_key(|(k, _)| k.to_string());
        for (k, v) in &sorted {
            kv(k, &v.to_string(), 20);
        }
    }

    println!();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jdisplay_reads_string_integer_float_and_bool_values() {
        let value = serde_json::json!({
            "id": 42,
            "name": "run",
            "score": 1.25,
            "done": true
        });

        assert_eq!(jdisplay(&value, "id"), "42");
        assert_eq!(jdisplay(&value, "name"), "run");
        assert_eq!(jdisplay(&value, "score"), "1.25");
        assert_eq!(jdisplay(&value, "done"), "true");
        assert_eq!(jdisplay(&value, "missing"), "-");
    }

    #[test]
    fn backtest_request_serializes_market_data_fields() {
        let req = BacktestRunRequest {
            strategy: "s".to_string(),
            symbols: vec!["BTCUSDT-PERP".to_string()],
            intervals: vec!["1m".to_string()],
            start_date: "2025-01-01".to_string(),
            end_date: "2025-01-02".to_string(),
            initial_capital: 10_000.0,
            leverage: 1.0,
            params: None,
            fill_model: None,
            data_type: "markPriceKlines".to_string(),
            extra_data_types: vec!["bookTicker".to_string(), "aggTrades".to_string()],
        };

        let body = serde_json::to_value(req).unwrap();
        assert_eq!(body["data_type"], "markPriceKlines");
        assert_eq!(
            body["extra_data_types"],
            serde_json::json!(["bookTicker", "aggTrades"])
        );
    }
}
