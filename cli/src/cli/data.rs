use anyhow::Result;
use clap::Subcommand;
use crossterm::style::Stylize;

use crate::api::ApiClient;
use crate::cli::style::*;
use crate::output::{print_json, print_llm_success, EnvelopeMeta, OutputFormat};
use crate::types::{DataCompactRequest, DataFetchBatchRequest, DataFetchRequest};

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
    /// Fetch data for multiple symbols x intervals in parallel
    #[command(name = "fetch-batch")]
    FetchBatch {
        /// Instrument symbols (space-separated)
        symbols: Vec<String>,
        /// Bar interval, repeatable (e.g. -i 1m -i 5m)
        #[arg(short, long)]
        interval: Vec<String>,
        /// Start date (YYYY-MM-DD)
        #[arg(short, long)]
        start: String,
        /// End date (YYYY-MM-DD)
        #[arg(short, long)]
        end: String,
    },
    /// List available data catalog
    List,
    /// Show data info for a symbol
    Info {
        /// Symbol
        symbol: String,
    },
    /// Compact stored data for a symbol/interval
    Compact {
        /// Symbol (e.g., BTCUSDT-PERP)
        symbol: String,
        /// Interval (e.g., 1m, 5m)
        interval: String,
    },
    /// Validate data integrity for a symbol/interval
    Validate {
        /// Symbol (e.g., BTCUSDT-PERP)
        symbol: String,
        /// Interval (e.g., 1m, 5m)
        interval: String,
    },
    /// Scan Parquet files on disk and sync missing entries into DB catalog
    Scan,
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

pub async fn dispatch(cmd: DataCmd, client: &ApiClient, format: OutputFormat) -> Result<()> {
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
            if format.is_machine() {
                return print_data_machine(format, client, "data.fetch", result);
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
            kv("Status", &format!("{}  {}", status_badge(st), bold(st)), 12);
            if let Some(m) = msg {
                println!();
                println!("    {}", dim(m));
            }
            println!();
        }
        DataCmd::List => {
            let result = client.list_data().await?;
            if format.is_machine() {
                return print_data_machine(format, client, "data.list", result);
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
                let sym = item.get("symbol").and_then(|v| v.as_str()).unwrap_or("?");
                let ivl = item.get("interval").and_then(|v| v.as_str()).unwrap_or("?");
                let start_d = item
                    .get("start_date")
                    .and_then(|v| v.as_str())
                    .unwrap_or("-");
                let end_d = item.get("end_date").and_then(|v| v.as_str()).unwrap_or("-");
                let size = item.get("size_bytes").and_then(|v| v.as_u64()).unwrap_or(0);

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
            let payload = data_info_payload(&result, &symbol);
            if format.is_machine() {
                return print_data_machine(format, client, "data.info", payload);
            }

            let items = payload
                .get("items")
                .and_then(|v| v.as_array())
                .cloned()
                .unwrap_or_default();

            header(&format!("Data: {}", accent(&symbol)));
            divider(50);

            if items.is_empty() {
                println!("    {}", muted("No data found for this symbol."));
            } else {
                kv("Datasets", &items.len().to_string(), 12);
                println!();
                for item in &items {
                    let ivl = item.get("interval").and_then(|v| v.as_str()).unwrap_or("?");
                    let start_d = item
                        .get("start_date")
                        .and_then(|v| v.as_str())
                        .unwrap_or("-");
                    let end_d = item.get("end_date").and_then(|v| v.as_str()).unwrap_or("-");
                    let size = item.get("size_bytes").and_then(|v| v.as_u64()).unwrap_or(0);
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
        DataCmd::FetchBatch {
            symbols,
            interval,
            start,
            end,
        } => {
            let req = DataFetchBatchRequest {
                symbols: symbols.clone(),
                intervals: interval.clone(),
                start: start.clone(),
                end: end.clone(),
            };
            let result = client.fetch_data_batch(&req).await?;
            if format.is_machine() {
                return print_data_machine(format, client, "data.fetch_batch", result);
            }

            let st = result
                .get("status")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            let count = result
                .get("count")
                .and_then(|v| v.as_u64())
                .unwrap_or((symbols.len() * interval.len()) as u64);
            let msg = result.get("message").and_then(|v| v.as_str());

            header("Batch Data Fetch Submitted");
            divider(50);
            kv("Symbols", &accent(&symbols.join(", ")), 12);
            kv("Intervals", &interval.join(", "), 12);
            kv("Period", &format!("{} ~ {}", start, end), 12);
            kv("Queued", &bold(&count.to_string()), 12);
            kv("Status", &format!("{}  {}", status_badge(st), bold(st)), 12);
            if let Some(m) = msg {
                println!();
                println!("    {}", dim(m));
            }
            println!();
        }
        DataCmd::Compact { symbol, interval } => {
            let req = DataCompactRequest {
                symbol: symbol.clone(),
                interval: interval.clone(),
            };
            let result = client.compact_data(&req).await?;
            if format.is_machine() {
                return print_data_machine(format, client, "data.compact", result);
            }

            let st = result
                .get("status")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            let badge = if st == "accepted" {
                status_badge("completed")
            } else {
                status_badge(st)
            };
            let msg = result.get("message").and_then(|v| v.as_str());

            header(&format!("Compact: {} {}", symbol, interval));
            divider(50);
            kv("Symbol", &accent(&symbol), 12);
            kv("Interval", &interval, 12);
            kv("Status", &format!("{}  {}", badge, bold(st)), 12);
            if let Some(m) = msg {
                println!("    {}", dim(m));
            }
            println!();
        }
        DataCmd::Validate { symbol, interval } => {
            let result = client.validate_data(&symbol, &interval).await?;
            if format.is_machine() {
                return print_data_machine(format, client, "data.validate", result);
            }

            header(&format!("Validate: {} {}", symbol, interval));
            divider(50);

            if let Some(obj) = result.as_object() {
                let st = obj
                    .get("status")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown");
                let issues = obj.get("issues").and_then(|v| v.as_array());
                let has_issues = issues.map(|a| !a.is_empty()).unwrap_or(false);

                if st == "ok" && !has_issues {
                    println!(
                        "    {}  {}",
                        status_badge("completed"),
                        "Data is valid".with(crossterm::style::Color::Rgb {
                            r: 34,
                            g: 197,
                            b: 94
                        }),
                    );
                } else {
                    kv("Status", &format!("{}  {}", status_badge(st), bold(st)), 12);
                }

                for key in &["bars_count", "start_date", "end_date", "gaps", "duplicates"] {
                    if let Some(val) = obj.get(*key) {
                        let label = key.replace('_', " ");
                        // Capitalize first letter
                        let label = label
                            .split_whitespace()
                            .map(|w| {
                                let mut c = w.chars();
                                match c.next() {
                                    None => String::new(),
                                    Some(f) => f.to_uppercase().to_string() + c.as_str(),
                                }
                            })
                            .collect::<Vec<_>>()
                            .join(" ");
                        kv(&label, &val.to_string(), 12);
                    }
                }

                if let Some(issues) = issues {
                    if !issues.is_empty() {
                        println!();
                        println!(
                            "    {}",
                            bold(&format!(
                                "{}",
                                "Issues:".with(crossterm::style::Color::Rgb {
                                    r: 248,
                                    g: 113,
                                    b: 113
                                })
                            )),
                        );
                        for issue in issues {
                            let text = issue.as_str().unwrap_or("?");
                            println!(
                                "      {} {}",
                                "-".with(crossterm::style::Color::Rgb {
                                    r: 248,
                                    g: 113,
                                    b: 113
                                }),
                                text
                            );
                        }
                    }
                }
            }
            println!();
        }
        DataCmd::Scan => {
            let result = client.scan_data().await?;
            if format.is_machine() {
                return print_data_machine(format, client, "data.scan", result);
            }

            let scanned = result.get("scanned").and_then(|v| v.as_u64()).unwrap_or(0);
            let created = result.get("created").and_then(|v| v.as_u64()).unwrap_or(0);
            let updated = result.get("updated").and_then(|v| v.as_u64()).unwrap_or(0);

            header("Data Catalog Scan");
            divider(50);
            kv("Scanned", &scanned.to_string(), 12);
            kv("Created", &bold(&created.to_string()), 12);
            kv("Updated", &updated.to_string(), 12);
            println!();
        }
    }
    Ok(())
}

fn catalog_items(result: &serde_json::Value) -> Vec<&serde_json::Value> {
    if let Some(items) = result.as_array() {
        return items.iter().collect();
    }
    result
        .get("items")
        .and_then(|v| v.as_array())
        .map(|items| items.iter().collect())
        .unwrap_or_default()
}

fn data_info_payload(result: &serde_json::Value, symbol: &str) -> serde_json::Value {
    let items: Vec<serde_json::Value> = catalog_items(result)
        .into_iter()
        .filter(|item| {
            item.get("symbol")
                .and_then(|v| v.as_str())
                .map(|s| s.contains(symbol))
                .unwrap_or(false)
        })
        .cloned()
        .collect();
    serde_json::json!({
        "symbol": symbol,
        "count": items.len(),
        "items": items,
    })
}

fn print_data_machine<T: serde::Serialize>(
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
    fn data_info_payload_filters_items_before_machine_output() {
        let catalog = serde_json::json!({
            "items": [
                {"symbol": "BTCUSDT-PERP", "interval": "1m"},
                {"symbol": "ETHUSDT-PERP", "interval": "1m"},
                {"symbol": "ETHUSDT-PERP", "interval": "5m"}
            ]
        });

        let payload = data_info_payload(&catalog, "ETHUSDT");

        assert_eq!(payload["symbol"], "ETHUSDT");
        assert_eq!(payload["count"], 2);
        let items = payload["items"].as_array().unwrap();
        assert!(items
            .iter()
            .all(|item| item["symbol"].as_str().unwrap().contains("ETHUSDT")));
    }
}
