use anyhow::Result;
use clap::Subcommand;
use crossterm::style::Stylize;
use crate::cli::style::{POS, NEG};

use crate::api::ApiClient;
use crate::cli::style::*;
use crate::types::StrategyCreateRequest;

#[derive(Subcommand)]
pub enum StrategyCmd {
    /// Create a new strategy scaffold
    Create {
        /// Strategy name
        name: String,
        /// Strategy type: bar or tick
        #[arg(long, short, default_value = "bar")]
        r#type: String,
    },
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
    /// Show strategy parameters and optimization ranges
    Params {
        /// Strategy name
        name: String,
    },
}

fn format_num(v: f64) -> String {
    if v == v.floor() {
        format!("{}", v as i64)
    } else {
        format!("{}", v)
    }
}

pub async fn dispatch(cmd: StrategyCmd, client: &ApiClient, format: &str) -> Result<()> {
    match cmd {
        StrategyCmd::Create { name, r#type } => {
            let req = StrategyCreateRequest {
                name: name.clone(),
                strategy_type: r#type.clone(),
            };
            let resp = client.create_strategy(&req).await?;
            if format == "json" {
                println!(
                    "{}",
                    serde_json::to_string_pretty(&serde_json::json!({
                        "name": resp.name,
                        "file_path": resp.file_path,
                        "message": resp.message,
                    }))?
                );
                return Ok(());
            }

            header("Strategy Created");
            divider(50);
            kv("Name", &accent(&resp.name), 12);
            kv("Type", &r#type, 12);
            kv("File", &dim(resp.file_path.as_deref().unwrap_or("-")), 12);
            println!();
            if let Some(msg) = &resp.message {
                println!("    {}", muted(msg));
            }
            println!();
        }
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
                ("Class", 22, "left"),
                ("Sym", 4, "right"),
                ("Updated", 8, "left"),
            ]);
            t.header();

            for s in &strategies {
                let stype = s.strategy_type.as_deref().unwrap_or("single");
                let cls = s.strategy_class.as_deref().unwrap_or("-");
                let sym_count = s.symbols.as_ref().map(|v| v.len()).unwrap_or(1);
                let updated = s
                    .updated_at
                    .as_deref()
                    .map(|t| {
                        // Compact date: "Mar 05" from "2026-03-05T..."
                        let clean = t[..10.min(t.len())].to_string();
                        let parts: Vec<&str> = clean.split('-').collect();
                        if parts.len() == 3 {
                            let month = match parts[1] {
                                "01" => "Jan", "02" => "Feb", "03" => "Mar",
                                "04" => "Apr", "05" => "May", "06" => "Jun",
                                "07" => "Jul", "08" => "Aug", "09" => "Sep",
                                "10" => "Oct", "11" => "Nov", "12" => "Dec",
                                _ => parts[1],
                            };
                            format!("{} {}", month, parts[2])
                        } else {
                            clean
                        }
                    })
                    .unwrap_or_else(|| "-".to_string());

                let type_display = if stype == "portfolio" {
                    format!("{}", "portfolio".with(POS).bold())
                } else {
                    format!("{}", stype.magenta())
                };

                let sym_display = if sym_count > 1 {
                    format!("{}", sym_count.to_string().with(POS))
                } else {
                    format!("{}", "1".magenta())
                };

                t.row(&[
                    &bold(&accent(&s.name[..20.min(s.name.len())])),
                    &type_display,
                    &format!("{}", cls[..22.min(cls.len())].yellow()),
                    &sym_display,
                    &accent(&updated),
                ]);
            }

            t.footer();
            println!("    {} strategies", strategies.len());
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
        StrategyCmd::Params { name } => {
            let resp = client.get_strategy_params(&name).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&resp)?);
                return Ok(());
            }

            header(&format!("Parameters: {}", accent(&name)));
            divider(60);

            let params = resp["config_params"].as_array();
            let opt_ranges = &resp["optimize_ranges"];

            if let Some(params) = params {
                if params.is_empty() {
                    println!("  No configurable parameters found.");
                } else {
                    let t = Table::new(&[
                        ("Parameter", 22, "left"),
                        ("Type", 10, "left"),
                        ("Default", 12, "left"),
                        ("Optimize Range", 20, "left"),
                    ]);
                    t.header();

                    for p in params {
                        let pname = p["name"].as_str().unwrap_or("-");
                        let ptype = p["type"].as_str().unwrap_or("Any");
                        let ptype_short = ptype
                            .rsplit("::")
                            .next()
                            .unwrap_or(ptype)
                            .trim_start_matches("typing.")
                            .replace("'", "");
                        let pdefault = if p["required"].as_bool().unwrap_or(false) {
                            format!("{}", "required".with(NEG))
                        } else {
                            p["default"]
                                .as_str()
                                .unwrap_or("-")
                                .to_string()
                        };

                        let opt_range = if let Some(range) = opt_ranges.get(pname) {
                            let lo = range["min"].as_f64().map(|v| format_num(v)).unwrap_or_default();
                            let hi = range["max"].as_f64().map(|v| format_num(v)).unwrap_or_default();
                            let step_str = range["step"]
                                .as_f64()
                                .map(|s| format!(" (step {})", format_num(s)))
                                .unwrap_or_default();
                            format!("{}", format!("{}..{}{}", lo, hi, step_str).with(POS))
                        } else {
                            format!("{}", "-".dark_grey())
                        };

                        t.row(&[
                            &accent(pname),
                            &ptype_short,
                            &pdefault,
                            &opt_range,
                        ]);
                    }
                    t.footer();
                }
            }

            println!();
        }
    }
    Ok(())
}
