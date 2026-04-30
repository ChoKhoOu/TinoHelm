use crate::cli::style::{LONG_COLOR, NEG, POS, SHORT_COLOR};
use anyhow::Result;
use clap::Subcommand;
use crossterm::style::Stylize;

use crate::api::ApiClient;
use crate::cli::style::*;
use crate::output::{
    print_json, print_llm_error, print_llm_success, EnvelopeError, EnvelopeMeta, OutputFormat,
};
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

// ── JSON helpers ─────────────────────────────────────────────────────────

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

// ── Status card ──────────────────────────────────────────────────────────

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

    // Summary for completed runs
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

// ── Result report (box-drawn) ────────────────────────────────────────────

fn print_result_report(data: &serde_json::Value, run_id: &str) {
    let r = data.get("result").unwrap_or(data);
    let null = serde_json::Value::Null;
    let stats = r.get("statistics").unwrap_or(&null);

    if !stats.is_object() {
        // No statistics, fall back to raw JSON
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
    let monthly = r
        .get("monthly_returns")
        .and_then(|v| v.as_array())
        .unwrap_or(&empty_vec);
    let drawdowns = r
        .get("drawdown_periods")
        .and_then(|v| v.as_array())
        .unwrap_or(&empty_vec);

    let pnl = jf64(stats, "total_pnl");
    let ret = jf64(stats, "total_return_pct");
    let balance = jstr(stats, "final_balance");

    let bx = BoxReport::new(62);

    println!();
    bx.top();

    // ── Header ──
    let title = format!(
        "  BACKTEST REPORT  {}",
        accent(&run_id[..8.min(run_id.len())])
    );
    bx.line(&bold(&title));
    bx.mid();

    // ── Hero: PnL ──
    bx.line(&format!(
        "  PnL: {} USDT  ({}%)     Balance: {}",
        color_value(pnl, "+.2f"),
        color_value(ret, "+.2f"),
        balance,
    ));
    bx.mid();

    // ── Risk Metrics ──
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
    bx.pair(
        "Volatility",
        &format_value(jf64(stats, "returns_volatility"), ".2f", ""),
        "Ann. Return",
        &format_value(jf64(stats, "annual_return"), ".2f", "%"),
    );
    bx.mid();

    // ── Trade Statistics ──
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
    } else {
        bx.line(&muted("  No closed trades"));
    }

    bx.pair(
        "Profit Factor",
        &format_value(jf64(stats, "profit_factor"), ".2f", ""),
        "Expectancy",
        &color_value(jf64(stats, "expectancy"), "+.2f"),
    );
    bx.pair(
        "Largest Win",
        &color_value(jf64(stats, "largest_win"), "+.2f"),
        "Largest Loss",
        &color_value(jf64(stats, "largest_loss"), "+.2f"),
    );
    bx.pair(
        "Avg Win",
        &format_value(jf64(stats, "avg_win"), ".2f", ""),
        "Avg Loss",
        &format_value(jf64(stats, "avg_loss"), ".2f", ""),
    );
    bx.pair(
        "W/L Ratio",
        &format_value(jf64(stats, "avg_win_loss_ratio"), ".2f", ""),
        "Streaks",
        &format!(
            "{}W / {}L",
            ju64(stats, "winning_streak").unwrap_or(0),
            ju64(stats, "losing_streak").unwrap_or(0),
        ),
    );
    bx.mid();

    // ── Position ──
    bx.line(&bold("  Position"));
    let long_pct = jf64(stats, "long_pct");
    let short_pct = jf64(stats, "short_pct");
    if let (Some(lp), Some(sp)) = (long_pct, short_pct) {
        let bar_w: usize = 28;
        let l_fill = (bar_w as f64 * lp) as usize;
        let s_fill = bar_w.saturating_sub(l_fill);
        let dir_bar = format!(
            "{}{}",
            "\u{2588}".repeat(l_fill).with(LONG_COLOR),
            "\u{2588}".repeat(s_fill).with(SHORT_COLOR),
        );
        bx.line(&format!(
            "  [{}]  Long {:.0}% / Short {:.0}%",
            dir_bar,
            lp * 100.0,
            sp * 100.0,
        ));
    }

    bx.pair(
        "Avg Hold",
        jstr(stats, "avg_holding_time"),
        "Total Fees",
        &format_value(jf64(stats, "total_fees"), ".4f", ""),
    );
    bx.pair(
        "Orders",
        &ju64(stats, "total_orders")
            .map(|v| v.to_string())
            .unwrap_or_else(|| "-".to_string()),
        "Filled",
        &ju64(stats, "filled_orders")
            .map(|v| v.to_string())
            .unwrap_or_else(|| "-".to_string()),
    );

    // ── Equity Curve Sparkline ──
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
            let low_s = format!("Low: {:.2}", mn);
            let high_s = format!("High: {:.2}", mx);
            let gap = 58usize.saturating_sub(low_s.len() + high_s.len());
            bx.line(&format!(
                "  {}{}{}",
                muted(&low_s),
                " ".repeat(gap),
                muted(&high_s),
            ));
        }
    }

    // ── Monthly Returns ──
    if !monthly.is_empty() {
        bx.mid();
        bx.line(&bold("  Monthly Returns"));
        for m in monthly {
            let period = jstr(m, "period");
            if let Some(ret_m) = jf64(m, "return_pct") {
                let bar_len = (ret_m.abs() * 5.0).min(30.0) as usize;
                let bar_vis = if ret_m >= 0.0 {
                    format!("{}", "+".repeat(bar_len).with(POS))
                } else {
                    format!("{}", "-".repeat(bar_len).with(NEG))
                };
                bx.line(&format!(
                    "  {:>10}  {:>8}%  {}",
                    period,
                    color_value(Some(ret_m), "+.2f"),
                    bar_vis,
                ));
            }
        }
    }

    // ── Drawdown Periods ──
    if !drawdowns.is_empty() {
        bx.mid();
        bx.line(&bold("  Notable Drawdowns"));
        for dd in drawdowns.iter().take(5) {
            let dd_pct = jf64(dd, "max_drawdown_pct");
            let start_d = &jstr(dd, "start")[..10.min(jstr(dd, "start").len())];
            let dur = jstr(dd, "duration_days");
            let rec = ju64(dd, "recovery_days");
            let rec_s = rec
                .map(|r| format!("rec {}d", r))
                .unwrap_or_else(|| "no recovery".to_string());
            bx.line(&format!(
                "  {}  {:>8}%  {}d  {}",
                start_d,
                color_value(dd_pct, "+.2f"),
                dur,
                muted(&rec_s),
            ));
        }
    }

    // ── Top Trades ──
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

        // Best trades
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

        // Worst trades
        for t in sorted_trades.iter().rev().take(top_n) {
            let pnl_t = trade_pnl(t);
            if pnl_t < 0.0 {
                let side = if jstr(t, "side") == "1" {
                    "LONG"
                } else {
                    "SHORT"
                };
                let qty = jstr(t, "quantity");
                bx.line(&format!(
                    "  {} {:5}  qty={}  pnl={}",
                    "-".with(NEG),
                    side,
                    qty,
                    color_value(Some(pnl_t), "+.2f"),
                ));
            }
        }
    }

    bx.bot();
    println!();
}

fn trade_pnl(t: &serde_json::Value) -> f64 {
    // Try as number first (JSON numeric), then as string ("114.60 USDT")
    if let Some(v) = t.get("realized_pnl").and_then(|v| v.as_f64()) {
        return v;
    }
    jstr(t, "realized_pnl")
        .split_whitespace()
        .next()
        .and_then(|s| s.parse::<f64>().ok())
        .unwrap_or(0.0)
}

// ── Dispatch ─────────────────────────────────────────────────────────────

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
            if format.is_machine() {
                let data = serde_json::json!({
                    "run_id": resp.run_id,
                    "status": resp.status
                });
                return print_backtest_machine(format, client, "backtest.run", data);
            }

            let sym_display = if symbols.is_empty() {
                "(portfolio)".to_string()
            } else {
                symbols.join(", ")
            };

            let rid = &resp.run_id;
            header("Backtest Submitted");
            divider(50);
            kv("Run ID", &accent(rid), 12);
            kv("Strategy", &strategy, 12);
            let ivl_display = interval.as_deref().unwrap_or("(portfolio)");
            kv("Symbol", &sym_display, 12);
            kv("Interval", ivl_display, 12);
            kv("Period", &format!("{} ~ {}", start, end), 12);
            println!();
            println!("    Track: {}", dim(&format!("tino backtest wait {}", rid)));
            println!();
        }
        BacktestCmd::List => {
            let data = client.list_backtests().await?;
            if format.is_machine() {
                return print_backtest_machine(format, client, "backtest.list", data.runs);
            }

            let t = Table::new(&[
                ("ID", 8, "left"),
                ("Strategy", 16, "left"),
                ("Symbol", 14, "left"),
                ("Ivl", 4, "right"),
                ("Period", 23, "left"),
                ("Status", 10, "right"),
                ("Trades", 6, "right"),
                ("PnL", 10, "right"),
                ("Ret%", 8, "right"),
                ("Sharpe", 7, "right"),
                ("WinRate", 7, "right"),
            ]);
            t.header();

            for r in &data.runs {
                let id = &r.run_id[..8.min(r.run_id.len())];
                let strat = r.strategy_name.as_deref().unwrap_or("?");
                let period = format!("{} ~ {}", r.start_date, r.end_date);
                let summary = r.result_summary.as_ref();

                let trades = summary
                    .and_then(|s| s.get("total_trades").and_then(|v| v.as_u64()))
                    .map(|v| v.to_string())
                    .unwrap_or_else(|| muted("-"));
                let pnl = summary
                    .and_then(|s| s.get("total_pnl").and_then(|v| v.as_f64()))
                    .map(|v| color_value(Some(v), "+.2f"))
                    .unwrap_or_else(|| muted("-"));
                let ret_pct = summary
                    .and_then(|s| s.get("total_return_pct").and_then(|v| v.as_f64()))
                    .map(|v| color_value(Some(v), "+.2f"))
                    .unwrap_or_else(|| muted("-"));
                let sharpe = summary
                    .and_then(|s| s.get("sharpe_ratio").and_then(|v| v.as_f64()))
                    .map(|v| format!("{:.2}", v))
                    .unwrap_or_else(|| muted("-"));
                let win_rate = summary
                    .and_then(|s| s.get("win_rate").and_then(|v| v.as_f64()))
                    .map(|v| format!("{:.1}%", v * 100.0))
                    .unwrap_or_else(|| muted("-"));

                let strat_display = bold(&strat[..16.min(strat.len())]);
                let sym_display = accent(&r.symbol[..14.min(r.symbol.len())]);
                let ivl_display = format!("{}", r.interval.as_str().yellow());

                let status_display = if let Some(pct) = r.progress_pct {
                    format!("{}", format!("{}%", pct).cyan())
                } else {
                    color_status(&r.status)
                };

                t.row(&[
                    &accent(id),
                    &strat_display,
                    &sym_display,
                    &ivl_display,
                    &period,
                    &status_display,
                    &trades,
                    &pnl,
                    &ret_pct,
                    &sharpe,
                    &win_rate,
                ]);
            }
            t.footer();
        }
        BacktestCmd::Result { run_id } => {
            let data = client.get_result(&run_id).await?;
            if format.is_machine() {
                return print_backtest_machine(format, client, "backtest.result", data);
            }
            print_result_report(&data, &run_id);
        }
        BacktestCmd::Status { run_id } => {
            let data = client.get_status(&run_id).await?;
            if format.is_machine() {
                let data = serde_json::json!({
                    "run_id": data.run_id,
                    "status": data.status,
                    "progress_pct": data.progress_pct,
                    "error": data.error,
                });
                return print_backtest_machine(format, client, "backtest.status", data);
            }
            print_status_card(&data, &run_id);
        }
        BacktestCmd::Wait { run_id, timeout } => {
            use std::io::Write;

            let mut elapsed = 0u64;
            let poll_interval = 2u64;

            if format.is_machine() {
                let mut last_status = String::from("unknown");
                while elapsed < timeout {
                    let data = client.get_status(&run_id).await?;
                    let st = data.status.clone();
                    last_status = st.clone();
                    if matches!(st.as_str(), "completed" | "failed" | "error" | "cancelled") {
                        if st == "completed" {
                            print_backtest_machine(
                                format,
                                client,
                                "backtest.wait",
                                serde_json::json!(data),
                            )?;
                            return Ok(());
                        }
                        print_backtest_error_machine(
                            format,
                            client,
                            "backtest.wait",
                            "backtest_failed",
                            format!("Backtest finished with status '{st}'"),
                            serde_json::json!(data),
                        )?;
                        std::process::exit(1);
                    }
                    tokio::time::sleep(std::time::Duration::from_secs(poll_interval)).await;
                    elapsed += poll_interval;
                }
                print_backtest_error_machine(
                    format,
                    client,
                    "backtest.wait",
                    "backtest_timeout",
                    format!("Backtest did not finish within {timeout}s"),
                    serde_json::json!({
                        "run_id": run_id,
                        "status": "timeout",
                        "last_status": last_status,
                        "timeout_sec": timeout,
                    }),
                )?;
                std::process::exit(1);
            }

            println!();
            let mut last_status = String::from("unknown");

            while elapsed < timeout {
                let data = client.get_status(&run_id).await?;
                let st = &data.status;
                let pct = data.progress_pct.unwrap_or(0);

                print!("\r{}", inline_progress(pct, st, elapsed, 20));
                std::io::stdout().flush()?;
                last_status = st.clone();

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
            eprintln!(
                "  {} after {}s. Last status: {}",
                "Timeout".with(NEG).bold(),
                timeout,
                last_status,
            );
            std::process::exit(1);
        }
        BacktestCmd::Cancel { run_id } => {
            let data = client.cancel_backtest(&run_id).await?;
            if format.is_machine() {
                let data = serde_json::json!({
                    "run_id": data.run_id,
                    "status": data.status,
                });
                return print_backtest_machine(format, client, "backtest.cancel", data);
            }
            println!();
            println!(
                "  {} Cancelled {} ({})",
                status_badge("cancelled"),
                accent(&data.run_id[..8.min(data.run_id.len())]),
                color_status(&data.status),
            );
            println!();
        }
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

            if format.is_machine() {
                let data = serde_json::json!({
                    "optimization_id": opt_id,
                    "status": resp.status,
                });
                return print_backtest_machine(format, client, "backtest.optimize", data);
            }

            header("Optimization Started");
            divider(50);
            kv("ID", &accent(&opt_id.to_string()), 12);
            kv("Trials", &trials.to_string(), 12);
            kv("Fitness", &fitness, 12);
            kv("Sampler", &sampler, 12);
            if walk_forward > 0 {
                kv("Walk-Forward", &format!("{} folds", walk_forward), 12);
            }
            println!();

            if !poll {
                return Ok(());
            }

            // Poll progress
            loop {
                tokio::time::sleep(std::time::Duration::from_secs(3)).await;
                let sd = client.optimize_status(opt_id).await?;
                let st = &sd.status;
                let done = sd.trials_completed.unwrap_or(0);
                let total = sd.total_trials.unwrap_or(trials);
                let best = sd.best_value;
                let pruned = sd.pruned_trials.unwrap_or(0);

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
                let pruned_s = if pruned > 0 {
                    format!("  pruned={}", pruned)
                } else {
                    String::new()
                };

                use std::io::Write;
                print!(
                    "\r  [{}] {}/{} ({}%){}{}  ",
                    bar, done, total, pct, best_s, pruned_s
                );
                std::io::stdout().flush()?;

                if matches!(st.as_str(), "completed" | "failed" | "error") {
                    println!();
                    if st != "completed" {
                        println!("  {}", "FAILED".with(NEG).bold());
                        let sd_json = serde_json::to_string_pretty(&serde_json::json!({
                            "status": st, "trials_completed": done,
                        }))?;
                        println!("{}", sd_json);
                        std::process::exit(1);
                    }
                    break;
                }
            }

            // Fetch and display result
            let result_data = client.optimize_result(opt_id).await?;
            print_optimize_result(&result_data);
        }
        BacktestCmd::OptimizeStatus { opt_id } => {
            let sd = client.optimize_status(opt_id).await?;
            if format.is_machine() {
                return print_backtest_machine(format, client, "backtest.optimize_status", sd);
            }
            let st = &sd.status;
            let done = sd.trials_completed.unwrap_or(0);
            let total = sd.total_trials.unwrap_or(0);
            let best = sd.best_value;

            println!();
            println!(
                "  {} Optimization {}  {}",
                status_badge(st),
                accent(&opt_id.to_string()),
                color_status(st),
            );
            println!(
                "      Trials: {}/{}  Best: {}",
                done,
                total,
                best.map(|b| color_value(Some(b), ".4f"))
                    .unwrap_or_else(|| muted("-")),
            );
            println!();
        }
        BacktestCmd::OptimizeResult { opt_id } => {
            let data = client.optimize_result(opt_id).await?;
            if format.is_machine() {
                return print_backtest_machine(format, client, "backtest.optimize_result", data);
            }
            print_optimize_result(&data);
        }
        BacktestCmd::OptimizeList { limit, strategy } => {
            let data = client.optimize_list(limit, strategy.as_deref()).await?;
            if format.is_machine() {
                return print_backtest_machine(format, client, "backtest.optimize_list", data);
            }

            let empty_vec = vec![];
            let runs = if data.is_array() {
                data.as_array().unwrap_or(&empty_vec)
            } else {
                data.get("runs")
                    .and_then(|v| v.as_array())
                    .unwrap_or(&empty_vec)
            };

            if runs.is_empty() {
                println!("No optimization runs found.");
                return Ok(());
            }

            let t = Table::new(&[
                ("ID", 6, "right"),
                ("Strategy", 16, "left"),
                ("Symbol", 14, "left"),
                ("Trials", 8, "right"),
                ("Fitness", 8, "left"),
                ("Status", 10, "right"),
                ("Best", 10, "right"),
                ("Done", 6, "right"),
            ]);
            t.header();

            for r in runs {
                let id = r
                    .get("optimization_id")
                    .and_then(|v| v.as_u64())
                    .map(|v| v.to_string())
                    .unwrap_or_default();
                let strat = jstr(r, "strategy_name");
                let sym = jstr(r, "symbol");
                let n_trials = jdisplay(r, "n_trials");
                let fit = jstr(r, "fitness_objective");
                let st = jstr(r, "status");
                let best = r.get("best_value").and_then(|v| v.as_f64());
                let done = jdisplay(r, "trials_completed");

                t.row(&[
                    &id,
                    &strat[..16.min(strat.len())],
                    &sym[..14.min(sym.len())],
                    &n_trials,
                    fit,
                    &color_status(st),
                    &best
                        .map(|b| color_value(Some(b), ".4f"))
                        .unwrap_or_else(|| muted("-")),
                    &done,
                ]);
            }
            t.footer();
        }
    }
    Ok(())
}

fn print_backtest_machine<T: serde::Serialize>(
    format: OutputFormat,
    client: &ApiClient,
    command: &'static str,
    data: T,
) -> Result<()> {
    match format {
        OutputFormat::Llm => print_llm_success(
            data,
            EnvelopeMeta::new(command, client.base_url(), client.auth_label()),
        ),
        OutputFormat::Json => print_json(&data),
        OutputFormat::Text => unreachable!("text output is handled by the caller"),
    }
}

fn print_backtest_error_machine(
    format: OutputFormat,
    client: &ApiClient,
    command: &'static str,
    kind: impl Into<String>,
    message: impl Into<String>,
    body: serde_json::Value,
) -> Result<()> {
    let error = EnvelopeError {
        kind: kind.into(),
        message: message.into(),
        status_code: None,
        body: Some(body),
    };
    match format {
        OutputFormat::Llm => print_llm_error(
            error,
            EnvelopeMeta::new(command, client.base_url(), client.auth_label()),
        ),
        OutputFormat::Json => print_json(&serde_json::json!({
            "ok": false,
            "error": error,
            "meta": EnvelopeMeta::new(command, client.base_url(), client.auth_label()),
        })),
        OutputFormat::Text => unreachable!("text output is handled by the caller"),
    }
}

fn print_optimize_result(data: &serde_json::Value) {
    println!();
    divider(50);
    println!("  {}", bold("Optimization Result"));
    divider(50);

    kv("ID", &jdisplay(data, "optimization_id"), 12);
    kv("Status", &color_status(jstr(data, "status")), 12);
    kv("Objective", jstr(data, "fitness_objective"), 12);

    let sampler_v = data.get("sampler").and_then(|v| v.as_str());
    if let Some(s) = sampler_v {
        kv("Sampler", s, 12);
    }
    if let Some(pruned) = data.get("total_pruned").and_then(|v| v.as_u64()) {
        if pruned > 0 {
            kv("Pruned", &pruned.to_string(), 12);
        }
    }

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

    if let Some(imp) = data.get("param_importances").and_then(|v| v.as_object()) {
        header("Parameter Importances");
        let mut sorted: Vec<_> = imp.iter().collect();
        sorted.sort_by(|a, b| {
            let va = a.1.as_f64().unwrap_or(0.0);
            let vb = b.1.as_f64().unwrap_or(0.0);
            vb.partial_cmp(&va).unwrap_or(std::cmp::Ordering::Equal)
        });
        for (k, v) in &sorted {
            let importance = v.as_f64().unwrap_or(0.0);
            let bar_len = (importance * 40.0) as usize;
            let bar = format!("{}", "#".repeat(bar_len).cyan());
            kv(k, &format!("{:.4} {}", importance, bar), 20);
        }
    }

    if let Some(wf) = data.get("walk_forward_results").and_then(|v| v.as_array()) {
        if !wf.is_empty() {
            header("Walk-Forward Folds");
            for (i, fold) in wf.iter().enumerate() {
                if fold.is_object() {
                    let tv = jf64(fold, "test_value");
                    let ts = jstr(fold, "test_start");
                    let te = jstr(fold, "test_end");
                    println!(
                        "    Fold {}: {} ~ {}  value={}",
                        i + 1,
                        ts,
                        te,
                        tv.map(|v| color_value(Some(v), ".4f"))
                            .unwrap_or_else(|| muted("-")),
                    );
                }
            }
        }
    }

    if let Some(tm) = data.get("test_metrics").and_then(|v| v.as_object()) {
        if !tm.is_empty() {
            header("Validation (Test Period)");
            for (k, v) in tm {
                kv(k, &v.to_string(), 20);
            }
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
