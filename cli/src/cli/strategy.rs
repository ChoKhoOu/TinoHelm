use anyhow::Result;
use clap::Subcommand;

use crate::api::ApiClient;

#[derive(Subcommand)]
pub enum StrategyCmd {
    /// List all strategies
    List,
    /// Show strategy details
    Info {
        /// Strategy name
        name: String,
    },
    /// Validate a strategy
    Validate {
        /// Strategy name
        name: String,
    },
    /// Re-scan strategies directory
    Rescan,
}

pub async fn dispatch(cmd: StrategyCmd, client: &ApiClient, format: &str) -> Result<()> {
    match cmd {
        StrategyCmd::List => {
            let strategies = client.list_strategies().await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&strategies)?);
                return Ok(());
            }
            println!(
                "  {:<20} {:<10} {:<20} {:>8} {:<19}",
                "Name", "Type", "Class", "Symbols", "Updated"
            );
            println!("  {}", "-".repeat(79));
            for s in &strategies {
                let stype = s.strategy_type.as_deref().unwrap_or("single");
                let cls = s.strategy_class.as_deref().unwrap_or("-");
                let sym_count = s.symbols.as_ref().map(|v| v.len()).unwrap_or(1);
                let updated = s
                    .updated_at
                    .as_deref()
                    .map(|t| &t[..19.min(t.len())])
                    .unwrap_or("-");
                println!(
                    "  {:<20} {:<10} {:<20} {:>8} {:<19}",
                    &s.name[..20.min(s.name.len())],
                    stype,
                    &cls[..20.min(cls.len())],
                    sym_count,
                    updated,
                );
            }
            println!("    {} strategies", strategies.len());
        }
        StrategyCmd::Info { name } => {
            let s = client.get_strategy(&name).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&s)?);
                return Ok(());
            }
            println!("  Name:     {}", s.name);
            println!("  Type:     {}", s.strategy_type.as_deref().unwrap_or("-"));
            println!("  Class:    {}", s.strategy_class.as_deref().unwrap_or("-"));
            println!("  Config:   {}", s.config_class.as_deref().unwrap_or("-"));
            println!("  File:     {}", s.file_path.as_deref().unwrap_or("-"));
            if let Some(symbols) = &s.symbols {
                println!("  Symbols:  {}", symbols.join(", "));
            }
        }
        StrategyCmd::Validate { name } => {
            let result = client.validate_strategy(&name).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&serde_json::json!(result))?);
                return Ok(());
            }
            if result.valid {
                println!("  VALID");
            } else {
                println!("  INVALID");
                if let Some(issues) = &result.issues {
                    for issue in issues {
                        println!("    * {}", issue);
                    }
                }
            }
        }
        StrategyCmd::Rescan => {
            let result = client.rescan_strategies().await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&serde_json::json!(result))?);
                return Ok(());
            }
            println!("  Discovered: {}", result.discovered);
            for name in &result.strategies {
                println!("    + {}", name);
            }
        }
    }
    Ok(())
}
