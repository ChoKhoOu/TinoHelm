use anyhow::Result;
use clap::Subcommand;
use crossterm::style::Stylize;

use crate::api::ApiClient;
use crate::cli::style::*;
use crate::types::DataFetchRequest;

#[derive(Subcommand)]
pub enum DataCmd {
    /// Fetch historical data
    Fetch {
        /// Symbol (e.g., BTCUSDT-PERP)
        symbol: String,
        /// Interval (e.g., 1m, 5m)
        interval: String,
        /// Start date (YYYY-MM-DD)
        start: String,
        /// End date (YYYY-MM-DD)
        end: String,
    },
    /// List available data catalog
    List,
    /// Show data info for a symbol
    Info {
        /// Symbol
        symbol: String,
    },
}

fn fmt_size(size_bytes: u64) -> String {
    if size_bytes < 1024 {
        format!("{} B", size_bytes)
    } else if size_bytes < 1024 * 1024 {
        format!("{:.1} KB", size_bytes as f64 / 1024.0)
    } else if size_bytes < 1024 * 1024 * 1024 {
        format!("{:.1} MB", size_bytes as f64 / (1024.0 * 1024.0))
    } else {
        format!("{:.2} GB", size_bytes as f64 / (1024.0 * 1024.0 * 1024.0))
    }
}

pub async fn dispatch(cmd: DataCmd, client: &ApiClient, format: &str) -> Result<()> {
    match cmd {
        DataCmd::Fetch {
            symbol,
            interval,
            start,
            end,
        } => {
            let req = DataFetchRequest {
                symbol: symbol.clone(),
                interval: interval.clone(),
                start: start.clone(),
                end: end.clone(),
            };
            let result = client.fetch_data(&req).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
                return Ok(());
            }

            let st = result
                .get("status")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            let msg = result.get("message").and_then(|v| v.as_str());

            header("Data Fetch Submitted");
            divider(50);
            kv("Symbol", &accent(&symbol), 12);
            kv("Interval", &interval, 12);
            kv("Period", &format!("{} ~ {}", start, end), 12);
            kv(
                "Status",
                &format!("{}  {}", status_badge(st), bold(st)),
                12,
            );
            if let Some(m) = msg {
                println!();
                println!("    {}", dim(m));
            }
            println!();
        }
        DataCmd::List => {
            let result = client.list_data().await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
                return Ok(());
            }

            let empty_vec = vec![];
            let items = if result.is_array() {
                result.as_array().unwrap_or(&empty_vec)
            } else {
                result
                    .get("items")
                    .and_then(|v| v.as_array())
                    .unwrap_or(&empty_vec)
            };

            if items.is_empty() {
                println!();
                println!("  {}", muted("No data in catalog."));
                println!();
                return Ok(());
            }

            let t = Table::new(&[
                ("Symbol", 18, "left"),
                ("Interval", 10, "left"),
                ("Start", 12, "left"),
                ("End", 12, "left"),
                ("Size", 10, "right"),
            ]);
            t.header();

            for item in items {
                let sym = item
                    .get("symbol")
                    .and_then(|v| v.as_str())
                    .unwrap_or("?");
                let ivl = item
                    .get("interval")
                    .and_then(|v| v.as_str())
                    .unwrap_or("?");
                let start_d = item
                    .get("start_date")
                    .and_then(|v| v.as_str())
                    .unwrap_or("-");
                let end_d = item
                    .get("end_date")
                    .and_then(|v| v.as_str())
                    .unwrap_or("-");
                let size = item
                    .get("size_bytes")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0);

                t.row(&[
                    &bold(&accent(sym)),
                    &format!("{}", ivl.yellow()),
                    &start_d[..10.min(start_d.len())],
                    &end_d[..10.min(end_d.len())],
                    &accent(&fmt_size(size)),
                ]);
            }

            t.footer();
            println!("    {}", muted(&format!("{} dataset(s)", items.len())));
            println!();
        }
        DataCmd::Info { symbol } => {
            let result = client.list_data().await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
                return Ok(());
            }

            // Filter items for this symbol
            let empty_vec = vec![];
            let all_items = if result.is_array() {
                result.as_array().unwrap_or(&empty_vec)
            } else {
                result
                    .get("items")
                    .and_then(|v| v.as_array())
                    .unwrap_or(&empty_vec)
            };

            let items: Vec<&serde_json::Value> = all_items
                .iter()
                .filter(|item| {
                    item.get("symbol")
                        .and_then(|v| v.as_str())
                        .map(|s| s.contains(&symbol))
                        .unwrap_or(false)
                })
                .collect();

            header(&format!("Data: {}", accent(&symbol)));
            divider(50);

            if items.is_empty() {
                println!("    {}", muted("No data found for this symbol."));
            } else {
                kv("Datasets", &items.len().to_string(), 12);
                println!();
                for item in &items {
                    let ivl = item
                        .get("interval")
                        .and_then(|v| v.as_str())
                        .unwrap_or("?");
                    let start_d = item
                        .get("start_date")
                        .and_then(|v| v.as_str())
                        .unwrap_or("-");
                    let end_d = item
                        .get("end_date")
                        .and_then(|v| v.as_str())
                        .unwrap_or("-");
                    let size = item
                        .get("size_bytes")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(0);
                    println!(
                        "    {}  {} ~ {}  {}",
                        accent(ivl),
                        &start_d[..10.min(start_d.len())],
                        &end_d[..10.min(end_d.len())],
                        muted(&fmt_size(size)),
                    );
                }
            }

            println!();
        }
    }
    Ok(())
}
