use anyhow::{anyhow, Result};
use clap::Subcommand;
use crossterm::style::Stylize;

use crate::api::ApiClient;
use crate::cli::style::*;
use crate::output::{print_json, OutputFormat};

#[derive(Subcommand)]
pub enum NodeCmd {
    /// Show node status
    Status,
    /// Check node health
    Health,
    /// Measure API latency
    Latency,
    /// Force-kill a node
    Kill {
        /// Node type: sandbox or live
        #[arg(default_value = "sandbox")]
        node_type: String,
        /// Kill escalation level (1-3).
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
    /// View or update risk limits
    #[command(name = "risk-limits")]
    RiskLimits {
        #[command(subcommand)]
        command: RiskLimitsCmd,
    },
    /// Paper trading configuration
    #[command(name = "paper-config")]
    PaperConfig {
        #[command(subcommand)]
        command: PaperConfigCmd,
    },
    /// Reset paper trading state
    #[command(name = "paper-reset")]
    PaperReset {
        /// Skip confirmation
        #[arg(long)]
        yes: bool,
    },
    /// Show node data subscription status
    #[command(name = "data-status")]
    DataStatus,
    /// List active data subscriptions
    Subscriptions,
    /// Show node settings
    Settings,
}

#[derive(Subcommand)]
pub enum RiskLimitsCmd {
    /// Get current risk limits
    Get {
        /// Node mode: sandbox or live
        #[arg(long, default_value = "live")]
        mode: String,
    },
    /// Set risk limits
    Set {
        /// Node mode: sandbox or live
        #[arg(long, default_value = "live")]
        mode: String,
        /// Max position size
        #[arg(long)]
        max_position: Option<f64>,
        /// Max daily loss
        #[arg(long)]
        max_daily_loss: Option<f64>,
        /// Max leverage
        #[arg(long)]
        max_leverage: Option<f64>,
    },
}

#[derive(Subcommand)]
pub enum PaperConfigCmd {
    /// Get paper trading configuration
    Get,
    /// Set paper trading configuration
    Set {
        /// Initial balance
        #[arg(long)]
        balance: Option<f64>,
        /// Leverage
        #[arg(long)]
        leverage: Option<f64>,
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
        name: String,
        #[arg(long, default_value = "live")]
        mode: String,
    },
    /// Pause a running portfolio
    Pause {
        name: String,
        #[arg(long, default_value = "live")]
        mode: String,
    },
    /// Resume a paused portfolio
    Resume {
        name: String,
        #[arg(long, default_value = "live")]
        mode: String,
    },
    /// Flatten positions and stop a portfolio
    FlattenStop {
        name: String,
        #[arg(long, default_value = "live")]
        mode: String,
        #[arg(long)]
        yes: bool,
    },
}

#[derive(Subcommand)]
pub enum LifecycleCmd {
    /// Pause strategies
    Pause {
        #[arg(long, default_value = "sandbox")]
        mode: String,
        #[arg(long)]
        strategy_id: Option<String>,
    },
    /// Resume strategies
    Resume {
        #[arg(long, default_value = "sandbox")]
        mode: String,
        #[arg(long)]
        strategy_id: Option<String>,
    },
    /// Flatten positions
    Flatten {
        #[arg(long, default_value = "sandbox")]
        mode: String,
        #[arg(long)]
        strategy_id: Option<String>,
        #[arg(long)]
        yes: bool,
    },
    /// Halt trading
    Halt {
        #[arg(long, default_value = "sandbox")]
        mode: String,
        #[arg(long)]
        yes: bool,
    },
    /// Unhalt trading
    Unhalt {
        #[arg(long, default_value = "sandbox")]
        mode: String,
    },
    /// Shutdown node gracefully
    Shutdown {
        #[arg(long, default_value = "sandbox")]
        mode: String,
        #[arg(long)]
        yes: bool,
    },
    /// Show current lifecycle state
    State {
        #[arg(long, default_value = "sandbox")]
        mode: String,
    },
}

pub async fn dispatch(cmd: NodeCmd, client: &ApiClient, format: OutputFormat) -> Result<()> {
    match cmd {
        NodeCmd::Status => {
            let result = client.node_status().await?;
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    let nodes = result
                        .get("nodes")
                        .and_then(|v| v.as_object())
                        .cloned()
                        .unwrap_or_default();
                    if nodes.is_empty() {
                        println!("  No nodes configured.");
                    } else {
                        for (mode, info) in &nodes {
                            let st = info
                                .get("status")
                                .and_then(|v| v.as_str())
                                .unwrap_or("stopped");
                            println!(
                                "  {} {}  {}",
                                node_badge(st),
                                mode_label(mode),
                                node_status_color(st),
                            );
                        }
                    }
                    println!();
                    Ok(())
                }
            }
        }
        NodeCmd::Health => {
            let resp = client
                .request_json(reqwest::Method::GET, "/api/node/health", &[], None, &[])
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    println!("  Health: {}", serde_json::to_string_pretty(&resp.body)?);
                    Ok(())
                }
            }
        }
        NodeCmd::Latency => {
            let start = std::time::Instant::now();
            let _resp = client
                .request_json(reqwest::Method::GET, "/api/node/health", &[], None, &[])
                .await?;
            let ms = start.elapsed().as_millis();
            let data = serde_json::json!({"latency_ms": ms});
            match format {
                OutputFormat::Json => print_json(&data),
                OutputFormat::Text => {
                    println!("  API latency: {}ms", ms);
                    Ok(())
                }
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
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    header(&format!("Kill Switch  {}", mode_label(&node_type)));
                    kv("Level", &level.to_string(), 14);
                    println!();
                    Ok(())
                }
            }
        }
        NodeCmd::Lifecycle { command } => dispatch_lifecycle(command, client, format).await,
        NodeCmd::Strategy { command } => dispatch_portfolio(command, client, format).await,
        NodeCmd::RiskLimits { command } => dispatch_risk_limits(command, client, format).await,
        NodeCmd::PaperConfig { command } => dispatch_paper_config(command, client, format).await,
        NodeCmd::PaperReset { yes } => {
            if !yes {
                crate::output::print_error(
                    &anyhow::anyhow!("use --yes to confirm paper trading reset"),
                    format,
                );
                std::process::exit(1);
            }
            let resp = client
                .request_json(
                    reqwest::Method::POST,
                    "/api/node/paper/reset",
                    &[],
                    None,
                    &[],
                )
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    println!("  Paper trading state reset.");
                    Ok(())
                }
            }
        }
        NodeCmd::DataStatus => {
            let resp = client
                .request_json(
                    reqwest::Method::GET,
                    "/api/node/data-status",
                    &[],
                    None,
                    &[],
                )
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    println!("  {}", serde_json::to_string_pretty(&resp.body)?);
                    Ok(())
                }
            }
        }
        NodeCmd::Subscriptions => {
            let resp = client
                .request_json(
                    reqwest::Method::GET,
                    "/api/node/subscriptions",
                    &[],
                    None,
                    &[],
                )
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    println!("  {}", serde_json::to_string_pretty(&resp.body)?);
                    Ok(())
                }
            }
        }
        NodeCmd::Settings => {
            let resp = client
                .request_json(reqwest::Method::GET, "/api/node/settings", &[], None, &[])
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    println!("  {}", serde_json::to_string_pretty(&resp.body)?);
                    Ok(())
                }
            }
        }
    }
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
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    let target = strategy_id.as_deref().unwrap_or("all strategies");
                    println!("  Pausing {} on {} node", target, mode_label(&mode));
                    Ok(())
                }
            }
        }
        LifecycleCmd::Resume { mode, strategy_id } => {
            let result = client
                .lifecycle_command("resume", &mode, strategy_id.as_deref())
                .await?;
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    let target = strategy_id.as_deref().unwrap_or("all strategies");
                    println!("  Resuming {} on {} node", target, mode_label(&mode));
                    Ok(())
                }
            }
        }
        LifecycleCmd::Flatten {
            mode,
            strategy_id,
            yes,
        } => {
            if !yes {
                crate::output::print_error(
                    &anyhow::anyhow!("use --yes to confirm flatten"),
                    format,
                );
                std::process::exit(1);
            }
            let result = client
                .lifecycle_command("flatten", &mode, strategy_id.as_deref())
                .await?;
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    println!("  Flatten command sent");
                    Ok(())
                }
            }
        }
        LifecycleCmd::Halt { mode, yes } => {
            if !yes {
                crate::output::print_error(&anyhow::anyhow!("use --yes to confirm halt"), format);
                std::process::exit(1);
            }
            let result = client.lifecycle_command("halt", &mode, None).await?;
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    println!("  Trading HALTED on {} node", mode_label(&mode));
                    Ok(())
                }
            }
        }
        LifecycleCmd::Unhalt { mode } => {
            let result = client.lifecycle_command("unhalt", &mode, None).await?;
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    println!("  Trading resumed on {} node", mode_label(&mode));
                    Ok(())
                }
            }
        }
        LifecycleCmd::Shutdown { mode, yes } => {
            if !yes {
                crate::output::print_error(
                    &anyhow::anyhow!("use --yes to confirm shutdown"),
                    format,
                );
                std::process::exit(1);
            }
            let result = client.lifecycle_command("shutdown", &mode, None).await?;
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    println!("  Shutdown command sent to {} node", mode_label(&mode));
                    Ok(())
                }
            }
        }
        LifecycleCmd::State { mode } => {
            let result = client.lifecycle_state(&mode).await?;
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    header(&format!("Lifecycle State  {}", mode_label(&mode)));
                    divider(50);
                    let trading_state = result
                        .get("trading_state")
                        .and_then(|v| v.as_str())
                        .unwrap_or("unknown");
                    kv("Trading", trading_state, 14);
                    println!();
                    Ok(())
                }
            }
        }
    }
}

async fn dispatch_portfolio(
    cmd: PortfolioCmd,
    client: &ApiClient,
    format: OutputFormat,
) -> Result<()> {
    match cmd {
        PortfolioCmd::List { mode } => {
            let resp = client.list_node_strategies(&mode).await?;
            match format {
                OutputFormat::Json => print_json(&resp.strategies),
                OutputFormat::Text => {
                    if resp.strategies.is_empty() {
                        println!("  No strategies found on {} node", mode);
                    } else {
                        header(&format!("Strategies  {}", mode_label(&mode)));
                        let t = Table::new(&[
                            ("Name", 25, "left"),
                            ("State", 12, "left"),
                            ("Strategies", 8, "right"),
                        ]);
                        t.header();
                        let mut names: Vec<_> = resp.strategies.keys().collect();
                        names.sort();
                        for name in names {
                            let p = &resp.strategies[name];
                            let state_colored = match p.state.as_str() {
                                "running" => format!("{}", p.state.clone().green()),
                                "paused" => format!("{}", p.state.clone().yellow()),
                                _ => p.state.clone(),
                            };
                            t.row(&[name, &state_colored, &p.strategy_ids.len().to_string()]);
                        }
                        t.footer();
                    }
                    Ok(())
                }
            }
        }
        PortfolioCmd::Start { name, mode } => {
            let resp = client.start_portfolio(&name, &mode).await?;
            match format {
                OutputFormat::Json => print_json(&resp),
                OutputFormat::Text => {
                    println!("  Starting portfolio '{}'", name);
                    Ok(())
                }
            }
        }
        PortfolioCmd::Pause { name, mode } => {
            let resp = client.pause_portfolio(&name, &mode).await?;
            match format {
                OutputFormat::Json => print_json(&resp),
                OutputFormat::Text => {
                    println!("  Pausing portfolio '{}'", name);
                    Ok(())
                }
            }
        }
        PortfolioCmd::Resume { name, mode } => {
            let resp = client.resume_portfolio(&name, &mode).await?;
            match format {
                OutputFormat::Json => print_json(&resp),
                OutputFormat::Text => {
                    println!("  Resuming portfolio '{}'", name);
                    Ok(())
                }
            }
        }
        PortfolioCmd::FlattenStop { name, mode, yes } => {
            if !yes {
                crate::output::print_error(
                    &anyhow::anyhow!("use --yes to confirm flatten-stop for portfolio '{}'", name),
                    format,
                );
                std::process::exit(1);
            }
            let resp = client.flatten_stop_portfolio(&name, &mode).await?;
            match format {
                OutputFormat::Json => print_json(&resp),
                OutputFormat::Text => {
                    println!("  Flatten-stop sent for portfolio '{}'", name);
                    Ok(())
                }
            }
        }
    }
}

async fn dispatch_risk_limits(
    cmd: RiskLimitsCmd,
    client: &ApiClient,
    format: OutputFormat,
) -> Result<()> {
    match cmd {
        RiskLimitsCmd::Get { mode } => {
            let query = [("mode".to_string(), mode)];
            let resp = client
                .request_json(
                    reqwest::Method::GET,
                    "/api/node/risk-limits",
                    &query,
                    None,
                    &[],
                )
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    println!("  {}", serde_json::to_string_pretty(&resp.body)?);
                    Ok(())
                }
            }
        }
        RiskLimitsCmd::Set {
            mode,
            max_position,
            max_daily_loss,
            max_leverage,
        } => {
            let mut body = serde_json::json!({"mode": mode});
            if let Some(v) = max_position {
                body["max_position"] = serde_json::json!(v);
            }
            if let Some(v) = max_daily_loss {
                body["max_daily_loss"] = serde_json::json!(v);
            }
            if let Some(v) = max_leverage {
                body["max_leverage"] = serde_json::json!(v);
            }
            let resp = client
                .request_json(
                    reqwest::Method::POST,
                    "/api/node/risk-limits",
                    &[],
                    Some(body),
                    &[],
                )
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    println!("  Risk limits updated.");
                    Ok(())
                }
            }
        }
    }
}

async fn dispatch_paper_config(
    cmd: PaperConfigCmd,
    client: &ApiClient,
    format: OutputFormat,
) -> Result<()> {
    match cmd {
        PaperConfigCmd::Get => {
            let resp = client
                .request_json(
                    reqwest::Method::GET,
                    "/api/node/paper/config",
                    &[],
                    None,
                    &[],
                )
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    println!("  {}", serde_json::to_string_pretty(&resp.body)?);
                    Ok(())
                }
            }
        }
        PaperConfigCmd::Set { balance, leverage } => {
            let mut body = serde_json::json!({});
            if let Some(v) = balance {
                body["balance"] = serde_json::json!(v);
            }
            if let Some(v) = leverage {
                body["leverage"] = serde_json::json!(v);
            }
            let resp = client
                .request_json(
                    reqwest::Method::POST,
                    "/api/node/paper/config",
                    &[],
                    Some(body),
                    &[],
                )
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    println!("  Paper config updated.");
                    Ok(())
                }
            }
        }
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
