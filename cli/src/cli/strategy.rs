use anyhow::Result;
use clap::Subcommand;
use crossterm::style::Stylize;

use crate::api::ApiClient;
use crate::cli::style::*;
use crate::output::{print_json, OutputFormat};
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

pub async fn dispatch(cmd: StrategyCmd, client: &ApiClient, format: OutputFormat) -> Result<()> {
    match cmd {
        StrategyCmd::Create { name, r#type } => {
            let req = StrategyCreateRequest {
                name: name.clone(),
                strategy_type: r#type.clone(),
            };
            let resp = client.create_strategy(&req).await?;
            let data = serde_json::json!({
                "name": resp.name,
                "file_path": resp.file_path,
                "message": resp.message,
            });
            match format {
                OutputFormat::Json => print_json(&data),
                OutputFormat::Text => {
                    header("Strategy Created");
                    divider(50);
                    kv("Name", &accent(&resp.name), 12);
                    kv("Type", &r#type, 12);
                    kv("File", &dim(resp.file_path.as_deref().unwrap_or("-")), 12);
                    println!();
                    Ok(())
                }
            }
        }
        StrategyCmd::List => {
            let strategies = client.list_strategies().await?;
            match format {
                OutputFormat::Json => print_json(&strategies),
                OutputFormat::Text => {
                    if strategies.is_empty() {
                        println!("  No strategies found.");
                        return Ok(());
                    }
                    let t = Table::new(&[
                        ("Name", 20, "left"),
                        ("Type", 10, "left"),
                        ("Class", 22, "left"),
                    ]);
                    t.header();
                    for s in &strategies {
                        let stype = s.strategy_type.as_deref().unwrap_or("single");
                        let cls = s.strategy_class.as_deref().unwrap_or("-");
                        t.row(&[&bold(&accent(&s.name[..20.min(s.name.len())])), stype, cls]);
                    }
                    t.footer();
                    println!("    {} strategies", strategies.len());
                    println!();
                    Ok(())
                }
            }
        }
        StrategyCmd::Info { name } => {
            let s = client.get_strategy(&name).await?;
            match format {
                OutputFormat::Json => print_json(&s),
                OutputFormat::Text => {
                    header(&format!("Strategy: {}", accent(&s.name)));
                    divider(50);
                    kv("Name", &bold(&s.name), 16);
                    kv("Type", s.strategy_type.as_deref().unwrap_or("single"), 16);
                    kv(
                        "Strategy Class",
                        s.strategy_class.as_deref().unwrap_or("-"),
                        16,
                    );
                    kv("File", &dim(s.file_path.as_deref().unwrap_or("-")), 16);
                    println!();
                    Ok(())
                }
            }
        }
        StrategyCmd::Validate { name } => {
            let result = client.validate_strategy(&name).await?;
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    header(&format!("Validation: {}", name));
                    divider(50);
                    if result.valid {
                        println!("    {} VALID", status_badge("completed"));
                    } else {
                        println!("    {} INVALID", status_badge("failed"));
                        if let Some(ref issues) = result.issues {
                            for issue in issues {
                                println!("    {} {}", "*".with(NEG), issue);
                            }
                        }
                    }
                    println!();
                    Ok(())
                }
            }
        }
        StrategyCmd::Rescan => {
            let result = client.rescan_strategies().await?;
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    header("Strategy Rescan");
                    divider(50);
                    kv("Discovered", &result.discovered.to_string(), 14);
                    if !result.strategies.is_empty() {
                        for name in &result.strategies {
                            println!("    {} {}", "+".with(POS), name);
                        }
                    }
                    println!();
                    Ok(())
                }
            }
        }
        StrategyCmd::Params { name } => {
            let resp = client.get_strategy_params(&name).await?;
            match format {
                OutputFormat::Json => print_json(&resp),
                OutputFormat::Text => {
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
                                    p["default"].as_str().unwrap_or("-").to_string()
                                };

                                let opt_range = if let Some(range) = opt_ranges.get(pname) {
                                    let lo =
                                        range["min"].as_f64().map(format_num).unwrap_or_default();
                                    let hi =
                                        range["max"].as_f64().map(format_num).unwrap_or_default();
                                    let step_str = range["step"]
                                        .as_f64()
                                        .map(|s| format!(" (step {})", format_num(s)))
                                        .unwrap_or_default();
                                    format!("{}..{}{}", lo, hi, step_str)
                                } else {
                                    "-".to_string()
                                };

                                t.row(&[&accent(pname), &ptype_short, &pdefault, &opt_range]);
                            }
                            t.footer();
                        }
                    }

                    println!();
                    Ok(())
                }
            }
        }
    }
}
