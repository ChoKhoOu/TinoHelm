use anyhow::Result;
use clap::Subcommand;
use crossterm::style::Stylize;
use crate::cli::style::{POS, NEG};

use crate::api::ApiClient;
use crate::cli::style::*;

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

            if strategies.is_empty() {
                println!("  No strategies found.");
                return Ok(());
            }

            let t = Table::new(&[
                ("Name", 20, "left"),
                ("Type", 10, "left"),
                ("Class", 20, "left"),
                ("Symbols", 8, "right"),
                ("Updated", 19, "left"),
            ]);
            t.header();

            for s in &strategies {
                let stype = s.strategy_type.as_deref().unwrap_or("single");
                let cls = s.strategy_class.as_deref().unwrap_or("-");
                let sym_count = s.symbols.as_ref().map(|v| v.len()).unwrap_or(1);
                let updated = s
                    .updated_at
                    .as_deref()
                    .map(|t| t[..19.min(t.len())].replace('T', " "))
                    .unwrap_or_else(|| "-".to_string());

                let type_display = if stype == "portfolio" {
                    format!("{}", "portfolio".with(POS).bold())
                } else {
                    dim(stype)
                };

                let sym_display = if sym_count > 1 {
                    accent(&sym_count.to_string())
                } else {
                    "1".to_string()
                };

                t.row(&[
                    &bold(&accent(&s.name[..20.min(s.name.len())])),
                    &type_display,
                    cls[..20.min(cls.len())].to_string().as_str(),
                    &sym_display,
                    &dim(&updated),
                ]);
            }

            t.footer();
            println!("    {}", dim(&format!("{} strategies", strategies.len())));
            println!();
        }
        StrategyCmd::Info { name } => {
            let s = client.get_strategy(&name).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&s)?);
                return Ok(());
            }

            let stype = s.strategy_type.as_deref().unwrap_or("single");
            header(&format!("Strategy: {}", accent(&s.name)));
            divider(50);
            kv("ID", &s.id.map(|i| i.to_string()).unwrap_or_else(|| "-".to_string()), 16);
            kv("Name", &bold(&s.name), 16);
            kv(
                "Type",
                &if stype == "portfolio" {
                    accent(stype)
                } else {
                    muted(stype)
                },
                16,
            );
            kv("Strategy Class", s.strategy_class.as_deref().unwrap_or("-"), 16);
            kv("Config Class", &muted(s.config_class.as_deref().unwrap_or("-")), 16);
            kv("File", &dim(s.file_path.as_deref().unwrap_or("-")), 16);

            if let Some(ref created) = s.created_at {
                kv("Created", &muted(&created[..19.min(created.len())].replace('T', " ")), 16);
            }
            if let Some(ref updated) = s.updated_at {
                kv("Updated", &muted(&updated[..19.min(updated.len())].replace('T', " ")), 16);
            }

            // Portfolio details
            if stype == "portfolio" {
                if let Some(ref symbols) = s.symbols {
                    println!();
                    println!("    {}", bold("Portfolio Details"));
                    divider(40);
                    kv("Interval", &s.interval.as_deref().unwrap_or("-").to_string(), 16);
                    kv("Symbols", &symbols.len().to_string(), 16);
                    for sym in symbols {
                        println!("      {}", accent(sym));
                    }
                }
                if let Some(ref actors) = s.actors {
                    if !actors.is_empty() {
                        kv("Actors", &actors.len().to_string(), 16);
                        for actor in actors {
                            println!("      {}", accent(actor));
                        }
                    }
                }
            }

            // Version history
            if let Some(ref versions) = s.versions {
                if !versions.is_empty() {
                    println!();
                    println!("    {}", bold("Version History"));
                    divider(40);
                    for v in versions {
                        let hash = v
                            .code_hash
                            .as_deref()
                            .map(|h| &h[..12.min(h.len())])
                            .unwrap_or("-");
                        let ts = v
                            .created_at
                            .as_deref()
                            .map(|t| muted(&t[..19.min(t.len())].replace('T', " ")))
                            .unwrap_or_else(|| muted("-"));
                        println!("      v{}  {}  {}", v.version, muted(hash), ts);
                    }
                }
            }

            println!();
        }
        StrategyCmd::Validate { name } => {
            let result = client.validate_strategy(&name).await?;
            if format == "json" {
                println!(
                    "{}",
                    serde_json::to_string_pretty(&serde_json::json!(result))?
                );
                return Ok(());
            }

            header(&format!("Validation: {}", name));
            divider(50);

            if result.valid {
                println!(
                    "    {} {}",
                    status_badge("completed"),
                    format!("{}", "VALID".with(POS).bold()),
                );
            } else {
                println!(
                    "    {} {}",
                    status_badge("failed"),
                    format!("{}", "INVALID".with(NEG).bold()),
                );
            }

            if let Some(ref issues) = result.issues {
                println!();
                for issue in issues {
                    println!("    {} {}", "*".with(NEG), issue);
                }
            }

            if let Some(ref cls) = result.strategy_class {
                kv("Strategy Class", cls, 16);
            }
            if let Some(ref cls) = result.config_class {
                kv("Config Class", cls, 16);
            }

            println!();
        }
        StrategyCmd::Rescan => {
            let result = client.rescan_strategies().await?;
            if format == "json" {
                println!(
                    "{}",
                    serde_json::to_string_pretty(&serde_json::json!(result))?
                );
                return Ok(());
            }

            header("Strategy Rescan");
            divider(50);
            kv("Discovered", &accent(&result.discovered.to_string()), 14);

            if !result.strategies.is_empty() {
                println!();
                for name in &result.strategies {
                    println!("    {} {}", "+".with(POS), name);
                }
            }

            println!();
        }
    }
    Ok(())
}
