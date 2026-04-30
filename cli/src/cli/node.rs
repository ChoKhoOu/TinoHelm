use anyhow::{anyhow, Result};
use clap::Subcommand;
use crossterm::style::Stylize;

use crate::api::ApiClient;
use crate::cli::style::*;
use crate::output::{
    print_json, print_llm_error, print_llm_success, EnvelopeError, EnvelopeMeta, OutputFormat,
};

#[derive(Subcommand)]
pub enum NodeCmd {
    /// Show node status
    Status,
    /// Force-kill a node
    Kill {
        /// Node type: sandbox or live
        #[arg(default_value = "sandbox")]
        node_type: String,
        /// Kill escalation level (1-3). Level 1 targets one strategy and requires --strategy-id.
        #[arg(long, short, default_value = "3")]
        level: u8,
        /// Strategy ID required for level 1 kill.
        #[arg(long)]
        strategy_id: Option<String>,
    },
    /// Lifecycle control commands
    Lifecycle {
        #[command(subcommand)]
        command: LifecycleCmd,
    },
    /// Node strategy-set management on a running node.
    #[command(alias = "portfolio")]
    Strategy {
        #[command(subcommand)]
        command: PortfolioCmd,
    },
}

#[derive(Subcommand)]
pub enum PortfolioCmd {
    /// List all portfolios and their states
    List {
        #[arg(long, default_value = "live")]
        mode: String,
    },
    /// Start a portfolio's strategies
    Start {
        /// Portfolio folder name
        name: String,
        #[arg(long, default_value = "live")]
        mode: String,
    },
    /// Pause a running portfolio
    Pause {
        /// Portfolio folder name
        name: String,
        #[arg(long, default_value = "live")]
        mode: String,
    },
    /// Resume a paused portfolio
    Resume {
        /// Portfolio folder name
        name: String,
        #[arg(long, default_value = "live")]
        mode: String,
    },
    /// Flatten positions and stop a portfolio
    FlattenStop {
        /// Portfolio folder name
        name: String,
        #[arg(long, default_value = "live")]
        mode: String,
        /// Skip confirmation prompt
        #[arg(long)]
        yes: bool,
    },
}

#[derive(Subcommand)]
pub enum LifecycleCmd {
    /// Pause strategies (all or specific)
    Pause {
        /// Node mode: sandbox or live
        #[arg(long, default_value = "sandbox")]
        mode: String,
        /// Strategy ID (omit to pause all)
        #[arg(long)]
        strategy_id: Option<String>,
    },
    /// Resume strategies (all or specific)
    Resume {
        /// Node mode: sandbox or live
        #[arg(long, default_value = "sandbox")]
        mode: String,
        /// Strategy ID (omit to resume all)
        #[arg(long)]
        strategy_id: Option<String>,
    },
    /// Flatten positions (close all open positions)
    Flatten {
        /// Node mode: sandbox or live
        #[arg(long, default_value = "sandbox")]
        mode: String,
        /// Strategy ID (omit to flatten all)
        #[arg(long)]
        strategy_id: Option<String>,
        /// Skip confirmation prompt
        #[arg(long)]
        yes: bool,
    },
    /// Halt trading (reject all new orders)
    Halt {
        /// Node mode: sandbox or live
        #[arg(long, default_value = "sandbox")]
        mode: String,
        /// Skip confirmation prompt
        #[arg(long)]
        yes: bool,
    },
    /// Unhalt trading (resume order flow)
    Unhalt {
        /// Node mode: sandbox or live
        #[arg(long, default_value = "sandbox")]
        mode: String,
    },
    /// Shutdown node gracefully
    Shutdown {
        /// Node mode: sandbox or live
        #[arg(long, default_value = "sandbox")]
        mode: String,
        /// Skip confirmation prompt
        #[arg(long)]
        yes: bool,
    },
    /// Show current lifecycle state
    State {
        /// Node mode: sandbox or live
        #[arg(long, default_value = "sandbox")]
        mode: String,
    },
}

pub async fn dispatch(cmd: NodeCmd, client: &ApiClient, format: OutputFormat) -> Result<()> {
    match cmd {
        NodeCmd::Status => {
            let result = client.node_status().await?;
            if format.is_machine() {
                return print_node_machine(format, client, "node.status", result);
            }

            let nodes = result
                .get("nodes")
                .and_then(|v| v.as_object())
                .cloned()
                .unwrap_or_default();
            let risk = result
                .get("risk_metrics")
                .cloned()
                .unwrap_or(serde_json::Value::Null);
            let workers = result
                .get("backtest_workers")
                .and_then(|v| v.as_array())
                .cloned()
                .unwrap_or_default();

            if nodes.len() <= 1 {
                // Single node card
                for (mode, info) in &nodes {
                    render_node_card(mode, info, &risk);
                }
                if nodes.is_empty() {
                    println!();
                    println!("  {}", muted("No nodes configured."));
                    println!();
                }
            } else {
                // Unified table view
                render_nodes_table(&nodes, &risk, &workers);
            }
        }
        NodeCmd::Kill {
            node_type,
            level,
            strategy_id,
        } => {
            validate_kill_args(level, strategy_id.as_deref())?;
            let result = client
                .node_kill(&node_type, level, strategy_id.as_deref())
                .await?;
            if format.is_machine() {
                return print_node_machine(format, client, "node.kill", result);
            }

            header(&format!("Kill Switch  {}", mode_label(&node_type)));
            divider(50);
            kv("Mode", &mode_label(&node_type), 14);
            let level_str = if level < 3 {
                format!("{}", level.to_string().yellow().bold())
            } else {
                format!("{}", level.to_string().with(NEG).bold())
            };
            kv("Level", &level_str, 14);
            if let Some(strategy_id) = strategy_id.as_deref() {
                kv("Strategy", strategy_id, 14);
            }
            println!();
        }
        NodeCmd::Lifecycle { command } => {
            dispatch_lifecycle(command, client, format).await?;
        }
        NodeCmd::Strategy { command } => {
            dispatch_portfolio(command, client, format).await?;
        }
    }
    Ok(())
}

fn validate_kill_args(level: u8, strategy_id: Option<&str>) -> Result<()> {
    if !(1..=3).contains(&level) {
        return Err(anyhow!("node kill level must be between 1 and 3"));
    }
    if level == 1
        && strategy_id
            .filter(|value| !value.trim().is_empty())
            .is_none()
    {
        return Err(anyhow!("node kill level 1 requires --strategy-id"));
    }
    Ok(())
}

async fn dispatch_lifecycle(
    cmd: LifecycleCmd,
    client: &ApiClient,
    format: OutputFormat,
) -> Result<()> {
    match cmd {
        LifecycleCmd::Pause { mode, strategy_id } => {
            let result = client
                .lifecycle_command("pause", &mode, strategy_id.as_deref())
                .await?;
            if format.is_machine() {
                return print_node_machine(format, client, "node.lifecycle.pause", result);
            } else {
                let target = strategy_id.as_deref().unwrap_or("all strategies");
                println!(
                    "  {} Pausing {} on {} node",
                    "\u{25CF}".yellow(),
                    target,
                    mode_label(&mode)
                );
            }
        }
        LifecycleCmd::Resume { mode, strategy_id } => {
            let result = client
                .lifecycle_command("resume", &mode, strategy_id.as_deref())
                .await?;
            if format.is_machine() {
                return print_node_machine(format, client, "node.lifecycle.resume", result);
            } else {
                let target = strategy_id.as_deref().unwrap_or("all strategies");
                println!(
                    "  {} Resuming {} on {} node",
                    "\u{25CF}".green(),
                    target,
                    mode_label(&mode)
                );
            }
        }
        LifecycleCmd::Flatten {
            mode,
            strategy_id,
            yes,
        } => {
            if !yes {
                reject_machine_confirmation(format, client, "node.lifecycle.flatten")?;
                let target = strategy_id.as_deref().unwrap_or("ALL");
                eprint!("  Flatten positions for {}? [y/N] ", target);
                let mut input = String::new();
                std::io::stdin().read_line(&mut input)?;
                if !input.trim().eq_ignore_ascii_case("y") {
                    println!("  Cancelled.");
                    return Ok(());
                }
            }
            let result = client
                .lifecycle_command("flatten", &mode, strategy_id.as_deref())
                .await?;
            if format.is_machine() {
                return print_node_machine(format, client, "node.lifecycle.flatten", result);
            } else {
                println!("  {} Flatten command sent", "\u{25CF}".yellow());
            }
        }
        LifecycleCmd::Halt { mode, yes } => {
            if !yes {
                reject_machine_confirmation(format, client, "node.lifecycle.halt")?;
                eprint!("  Halt all trading on {} node? [y/N] ", mode);
                let mut input = String::new();
                std::io::stdin().read_line(&mut input)?;
                if !input.trim().eq_ignore_ascii_case("y") {
                    println!("  Cancelled.");
                    return Ok(());
                }
            }
            let result = client.lifecycle_command("halt", &mode, None).await?;
            if format.is_machine() {
                return print_node_machine(format, client, "node.lifecycle.halt", result);
            } else {
                println!(
                    "  {} Trading HALTED on {} node",
                    "\u{25CF}".red(),
                    mode_label(&mode)
                );
            }
        }
        LifecycleCmd::Unhalt { mode } => {
            let result = client.lifecycle_command("unhalt", &mode, None).await?;
            if format.is_machine() {
                return print_node_machine(format, client, "node.lifecycle.unhalt", result);
            } else {
                println!(
                    "  {} Trading resumed on {} node",
                    "\u{25CF}".green(),
                    mode_label(&mode)
                );
            }
        }
        LifecycleCmd::Shutdown { mode, yes } => {
            if !yes {
                reject_machine_confirmation(format, client, "node.lifecycle.shutdown")?;
                eprint!("  Shutdown {} node? [y/N] ", mode);
                let mut input = String::new();
                std::io::stdin().read_line(&mut input)?;
                if !input.trim().eq_ignore_ascii_case("y") {
                    println!("  Cancelled.");
                    return Ok(());
                }
            }
            let result = client.lifecycle_command("shutdown", &mode, None).await?;
            if format.is_machine() {
                return print_node_machine(format, client, "node.lifecycle.shutdown", result);
            } else {
                println!(
                    "  {} Shutdown command sent to {} node",
                    "\u{25CF}".red(),
                    mode_label(&mode)
                );
            }
        }
        LifecycleCmd::State { mode } => {
            let result = client.lifecycle_state(&mode).await?;
            if format.is_machine() {
                return print_node_machine(format, client, "node.lifecycle.state", result);
            }
            header(&format!("Lifecycle State  {}", mode_label(&mode)));
            divider(50);

            let trading_state = result
                .get("trading_state")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            let ts_colored = match trading_state {
                "active" => format!("{}", trading_state.green().bold()),
                "halted" => format!("{}", trading_state.red().bold()),
                "reducing" => format!("{}", trading_state.yellow().bold()),
                _ => trading_state.to_string(),
            };
            kv("Trading", &ts_colored, 14);

            if let Some(paused) = result.get("paused").and_then(|v| v.as_array()) {
                if paused.is_empty() {
                    kv("Paused", &muted("none"), 14);
                } else {
                    let names: Vec<&str> = paused.iter().filter_map(|v| v.as_str()).collect();
                    kv("Paused", &names.join(", "), 14);
                }
            }

            if let Some(states) = result.get("strategy_states").and_then(|v| v.as_object()) {
                if !states.is_empty() {
                    println!();
                    println!("    {}", bold("Strategies:"));
                    for (name, state) in states {
                        let st = state.as_str().unwrap_or("unknown");
                        let colored = match st {
                            "running" => format!("{}", st.green()),
                            "paused" => format!("{}", st.yellow()),
                            _ => st.to_string(),
                        };
                        println!("      {} {} {}", "-".cyan(), name, colored);
                    }
                }
            }
            println!();
        }
    }
    Ok(())
}

async fn dispatch_portfolio(
    cmd: PortfolioCmd,
    client: &ApiClient,
    format: OutputFormat,
) -> Result<()> {
    match cmd {
        PortfolioCmd::List { mode } => {
            let resp = client.list_node_strategies(&mode).await?;
            if format.is_machine() {
                return print_node_machine(format, client, "node.strategy.list", resp.strategies);
            }
            if resp.strategies.is_empty() {
                println!("  No strategies found on {} node", mode);
            } else {
                header(&format!("Strategies  {}", mode_label(&mode)));
                let t = Table::new(&[
                    ("Name", 25, "left"),
                    ("State", 12, "left"),
                    ("Strategies", 8, "right"),
                    ("Prefix", 8, "left"),
                ]);
                t.header();
                let mut names: Vec<_> = resp.strategies.keys().collect();
                names.sort();
                for name in names {
                    let p = &resp.strategies[name];
                    let was = if p.was_running { " (*)" } else { "" };
                    let state_colored = match p.state.as_str() {
                        "running" => format!("{}", p.state.clone().green()),
                        "paused" => format!("{}", p.state.clone().yellow()),
                        "flattening" => format!("{}", p.state.clone().with(NEG)),
                        "starting" => format!("{}", p.state.clone().cyan()),
                        _ => p.state.clone(),
                    };
                    t.row(&[
                        &format!("{}{}", name, was),
                        &state_colored,
                        &p.strategy_ids.len().to_string(),
                        p.order_id_tag_prefix.as_deref().unwrap_or(""),
                    ]);
                }
                t.footer();
            }
        }
        PortfolioCmd::Start { name, mode } => {
            let resp = client.start_portfolio(&name, &mode).await?;
            if format.is_machine() {
                return print_node_machine(format, client, "node.strategy.start", resp);
            } else {
                println!("  {} Starting portfolio '{}'", "\u{25CF}".green(), name);
            }
        }
        PortfolioCmd::Pause { name, mode } => {
            let resp = client.pause_portfolio(&name, &mode).await?;
            if format.is_machine() {
                return print_node_machine(format, client, "node.strategy.pause", resp);
            } else {
                println!("  {} Pausing portfolio '{}'", "\u{25CF}".yellow(), name);
            }
        }
        PortfolioCmd::Resume { name, mode } => {
            let resp = client.resume_portfolio(&name, &mode).await?;
            if format.is_machine() {
                return print_node_machine(format, client, "node.strategy.resume", resp);
            } else {
                println!("  {} Resuming portfolio '{}'", "\u{25CF}".green(), name);
            }
        }
        PortfolioCmd::FlattenStop { name, mode, yes } => {
            if !yes {
                reject_machine_confirmation(format, client, "node.strategy.flatten_stop")?;
                eprint!(
                    "  Flatten and stop portfolio '{}'? This will close all positions. [y/N] ",
                    name
                );
                use std::io::Write;
                std::io::stdout().flush()?;
                let mut input = String::new();
                std::io::stdin().read_line(&mut input)?;
                if !input.trim().eq_ignore_ascii_case("y") {
                    println!("  Cancelled.");
                    return Ok(());
                }
            }
            let resp = client.flatten_stop_portfolio(&name, &mode).await?;
            if format.is_machine() {
                return print_node_machine(format, client, "node.strategy.flatten_stop", resp);
            } else {
                println!(
                    "  {} Flatten-stop sent for portfolio '{}'",
                    "\u{25CF}".with(NEG),
                    name
                );
            }
        }
    }
    Ok(())
}

fn render_node_card(mode: &str, info: &serde_json::Value, risk: &serde_json::Value) {
    let st = info
        .get("status")
        .and_then(|v| v.as_str())
        .unwrap_or("stopped");
    let pid = info.get("pid").and_then(|v| v.as_u64());
    let restarts = info
        .get("restart_count")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let strategies = info
        .get("strategies")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let heartbeat = info.get("heartbeat");

    header(&format!(
        "{} Node Status  {}",
        node_badge(st),
        mode_label(mode),
    ));
    divider(50);
    kv("Mode", &mode_label(mode), 14);
    kv("State", &node_status_color(st), 14);
    if let Some(p) = pid {
        kv("PID", &p.to_string(), 14);
    }
    if restarts > 0 {
        kv(
            "Restarts",
            &format!("{}", restarts.to_string().yellow()),
            14,
        );
    }

    // Heartbeat uptime
    if let Some(hb) = heartbeat {
        if let Some(uptime) = hb.get("uptime").and_then(|v| v.as_str()) {
            kv("Uptime", uptime, 14);
        }
    }

    // Strategies list
    if !strategies.is_empty() {
        println!();
        println!("    {}", bold("Strategies:"));
        for s in &strategies {
            if let Some(name) = s.as_str() {
                println!("      {} {}", "-".cyan(), name);
            }
        }
    }

    // Risk metrics
    if st == "running" && risk.is_object() {
        let exposure = risk
            .get("total_exposure")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);
        let margin = risk
            .get("margin_used_pct")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);
        let leverage = risk.get("leverage").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let daily_var = risk
            .get("daily_var")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);

        if exposure > 0.0 {
            println!();
            println!("    {}", bold("Risk Metrics:"));
            kv("Exposure", &format!("{:.2} USDT", exposure), 14);
            let margin_str = format!("{:.2}%", margin);
            let margin_colored = if margin < 50.0 {
                format!("{}", margin_str.with(POS))
            } else if margin < 80.0 {
                format!("{}", margin_str.yellow())
            } else {
                format!("{}", margin_str.with(NEG))
            };
            kv("Margin Used", &margin_colored, 14);
            kv("Leverage", &format!("{:.4}x", leverage), 14);
            kv("Daily VaR", &format!("{:.2} USDT", daily_var), 14);
        }
    }

    println!();
}

fn render_nodes_table(
    nodes: &serde_json::Map<String, serde_json::Value>,
    risk: &serde_json::Value,
    workers: &[serde_json::Value],
) {
    header("Node Status (all)");

    let t = Table::new(&[
        ("Mode", 10, "left"),
        ("State", 10, "left"),
        ("PID", 8, "right"),
        ("Restarts", 8, "right"),
        ("Strategies", 24, "left"),
    ]);
    t.header();

    let mut sorted_keys: Vec<&String> = nodes.keys().collect();
    sorted_keys.sort();

    for node_type in sorted_keys {
        let info = &nodes[node_type];
        let st = info
            .get("status")
            .and_then(|v| v.as_str())
            .unwrap_or("stopped");
        let pid = info.get("pid").and_then(|v| v.as_u64());
        let restarts = info
            .get("restart_count")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);
        let strategies = info
            .get("strategies")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str())
                    .collect::<Vec<_>>()
                    .join(", ")
            })
            .unwrap_or_default();

        let strats_display = if strategies.is_empty() {
            muted("none")
        } else {
            let s = &strategies[..24.min(strategies.len())];
            s.to_string()
        };

        let restart_str = if restarts > 0 {
            format!("{}", restarts.to_string().yellow())
        } else {
            "0".to_string()
        };

        t.row(&[
            &mode_label(node_type),
            &node_status_color(st),
            &pid.map(|p| p.to_string()).unwrap_or_else(|| muted("-")),
            &restart_str,
            &strats_display,
        ]);
    }

    t.footer();

    // Backtest workers summary
    if !workers.is_empty() {
        let alive_count = workers
            .iter()
            .filter(|w| w.get("alive").and_then(|v| v.as_bool()).unwrap_or(false))
            .count();
        println!(
            "    Backtest workers: {}/{} alive",
            bold(&alive_count.to_string()),
            workers.len(),
        );
    }

    // Risk summary
    if risk.is_object() {
        let exposure = risk
            .get("total_exposure")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);
        let margin = risk
            .get("margin_used_pct")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);
        let leverage = risk.get("leverage").and_then(|v| v.as_f64()).unwrap_or(0.0);

        if exposure > 0.0 {
            let margin_str = format!("{:.2}%", margin);
            let margin_colored = if margin < 50.0 {
                format!("{}", margin_str.with(POS))
            } else if margin < 80.0 {
                format!("{}", margin_str.yellow())
            } else {
                format!("{}", margin_str.with(NEG))
            };
            println!(
                "    Risk: exposure={}  margin={}  leverage={:.4}x",
                bold(&format!("{:.2}", exposure)),
                margin_colored,
                leverage,
            );
        }
    }

    println!();
}

fn reject_machine_confirmation(
    format: OutputFormat,
    client: &ApiClient,
    command: &'static str,
) -> Result<()> {
    if !format.is_machine() {
        return Ok(());
    }

    let error = EnvelopeError {
        code: None,
        kind: "confirmation_required".to_string(),
        message: "Refusing to prompt in machine output mode; rerun with --yes to confirm this destructive command.".to_string(),
        status_code: None,
        body: Some(serde_json::json!({ "required_flag": "--yes" })),
    };
    let meta = EnvelopeMeta::new(command, client.base_url(), client.auth_label());
    if format == OutputFormat::Llm {
        print_llm_error(error, meta)?;
    } else {
        print_json(&serde_json::json!({
            "ok": false,
            "error": error,
            "meta": meta,
        }))?;
    }
    std::process::exit(1);
}

fn print_node_machine<T: serde::Serialize>(
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
        OutputFormat::Text => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kill_level_one_requires_strategy_id() {
        assert!(validate_kill_args(1, Some("strategy-a")).is_ok());
        assert!(validate_kill_args(1, None).is_err());
        assert!(validate_kill_args(1, Some("   ")).is_err());
    }

    #[test]
    fn kill_level_is_limited_to_backend_contract() {
        assert!(validate_kill_args(2, None).is_ok());
        assert!(validate_kill_args(3, None).is_ok());
        assert!(validate_kill_args(4, None).is_err());
    }
}
