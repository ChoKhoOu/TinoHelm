use anyhow::Result;
use clap::Subcommand;
use crossterm::style::Stylize;

use crate::api::ApiClient;
use crate::cli::style::*;

#[derive(Subcommand)]
pub enum NodeCmd {
    /// Show node status
    Status,
    /// Start a node
    Start {
        /// Node type: sandbox or live
        #[arg(default_value = "sandbox")]
        node_type: String,
        /// Strategy name(s) to run
        #[arg(long = "strategy", short = 's')]
        strategies: Vec<String>,
    },
    /// Stop a node
    Stop {
        /// Node type: sandbox or live
        #[arg(default_value = "sandbox")]
        node_type: String,
    },
    /// Force-kill a node
    Kill {
        /// Node type: sandbox or live
        #[arg(default_value = "sandbox")]
        node_type: String,
        /// Kill escalation level (1-5)
        #[arg(long, short, default_value = "3")]
        level: u8,
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
        NodeCmd::Start {
            node_type,
            strategies,
        } => {
            let result = client.node_start(&node_type, &strategies).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
                return Ok(());
            }

            header(&format!("Node Starting  {}", mode_label(&node_type)));
            divider(50);
            kv("Mode", &mode_label(&node_type), 14);
            kv("Status", &node_status_color("starting"), 14);

            if !strategies.is_empty() {
                println!();
                println!("    {}", bold("Strategies:"));
                for s in &strategies {
                    println!("      {} {}", "-".cyan(), s);
                }
            }
            println!();
        }
        NodeCmd::Stop { node_type } => {
            let result = client.node_stop(&node_type).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
                return Ok(());
            }

            header(&format!("Node Stopping  {}", mode_label(&node_type)));
            divider(50);
            kv("Mode", &mode_label(&node_type), 14);
            kv(
                "Status",
                &format!("{}", "graceful stop".yellow().bold()),
                14,
            );
            println!();
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
                format!("{}", level.to_string().red().bold())
            };
            kv("Level", &level_str, 14);
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
                format!("{}", margin_str.green())
            } else if margin < 80.0 {
                format!("{}", margin_str.yellow())
            } else {
                format!("{}", margin_str.red())
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
                format!("{}", margin_str.green())
            } else if margin < 80.0 {
                format!("{}", margin_str.yellow())
            } else {
                format!("{}", margin_str.red())
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
