use anyhow::Result;
use clap::Subcommand;
use crossterm::style::Stylize;
use crate::cli::style::{POS, NEG};

use crate::api::ApiClient;
use crate::cli::style::*;

#[derive(Subcommand)]
pub enum NodeCmd {
    /// Show node status
    Status,
    /// Force-kill a node
    Kill {
        /// Node type: sandbox or live
        #[arg(default_value = "sandbox")]
        node_type: String,
        /// Kill escalation level (1-5)
        #[arg(long, short, default_value = "3")]
        level: u8,
    },
    /// Lifecycle control commands
    Lifecycle {
        #[command(subcommand)]
        command: LifecycleCmd,
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

pub async fn dispatch(cmd: NodeCmd, client: &ApiClient, format: &str) -> Result<()> {
    match cmd {
        NodeCmd::Status => {
            let result = client.node_status().await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
                return Ok(());
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
        NodeCmd::Kill { node_type, level } => {
            let result = client.node_kill(&node_type, level).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
                return Ok(());
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
            println!();
        }
        NodeCmd::Lifecycle { command } => {
            dispatch_lifecycle(command, client, format).await?;
        }
    }
    Ok(())
}

async fn dispatch_lifecycle(cmd: LifecycleCmd, client: &ApiClient, format: &str) -> Result<()> {
    match cmd {
        LifecycleCmd::Pause { mode, strategy_id } => {
            let result = client.lifecycle_command("pause", &mode, strategy_id.as_deref()).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                let target = strategy_id.as_deref().unwrap_or("all strategies");
                println!("  {} Pausing {} on {} node", "\u{25CF}".yellow(), target, mode_label(&mode));
            }
        }
        LifecycleCmd::Resume { mode, strategy_id } => {
            let result = client.lifecycle_command("resume", &mode, strategy_id.as_deref()).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                let target = strategy_id.as_deref().unwrap_or("all strategies");
                println!("  {} Resuming {} on {} node", "\u{25CF}".green(), target, mode_label(&mode));
            }
        }
        LifecycleCmd::Flatten { mode, strategy_id, yes } => {
            if !yes {
                let target = strategy_id.as_deref().unwrap_or("ALL");
                eprint!("  Flatten positions for {}? [y/N] ", target);
                let mut input = String::new();
                std::io::stdin().read_line(&mut input)?;
                if !input.trim().eq_ignore_ascii_case("y") {
                    println!("  Cancelled.");
                    return Ok(());
                }
            }
            let result = client.lifecycle_command("flatten", &mode, strategy_id.as_deref()).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                println!("  {} Flatten command sent", "\u{25CF}".yellow());
            }
        }
        LifecycleCmd::Halt { mode, yes } => {
            if !yes {
                eprint!("  Halt all trading on {} node? [y/N] ", mode);
                let mut input = String::new();
                std::io::stdin().read_line(&mut input)?;
                if !input.trim().eq_ignore_ascii_case("y") {
                    println!("  Cancelled.");
                    return Ok(());
                }
            }
            let result = client.lifecycle_command("halt", &mode, None).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                println!("  {} Trading HALTED on {} node", "\u{25CF}".red(), mode_label(&mode));
            }
        }
        LifecycleCmd::Unhalt { mode } => {
            let result = client.lifecycle_command("unhalt", &mode, None).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                println!("  {} Trading resumed on {} node", "\u{25CF}".green(), mode_label(&mode));
            }
        }
        LifecycleCmd::Shutdown { mode, yes } => {
            if !yes {
                eprint!("  Shutdown {} node? [y/N] ", mode);
                let mut input = String::new();
                std::io::stdin().read_line(&mut input)?;
                if !input.trim().eq_ignore_ascii_case("y") {
                    println!("  Cancelled.");
                    return Ok(());
                }
            }
            let result = client.lifecycle_command("shutdown", &mode, None).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                println!("  {} Shutdown command sent to {} node", "\u{25CF}".red(), mode_label(&mode));
            }
        }
        LifecycleCmd::State { mode } => {
            let result = client.lifecycle_state(&mode).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
                return Ok(());
            }
            header(&format!("Lifecycle State  {}", mode_label(&mode)));
            divider(50);

            let trading_state = result.get("trading_state").and_then(|v| v.as_str()).unwrap_or("unknown");
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
        let leverage = risk
            .get("leverage")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);
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
        let leverage = risk
            .get("leverage")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);

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
