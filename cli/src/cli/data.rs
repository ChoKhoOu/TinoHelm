use anyhow::Result;
use clap::Subcommand;

use crate::api::ApiClient;
use crate::cli::style::*;
use crate::output::{print_json, OutputFormat};
use crate::types::{DataBatchItem, DataCompactRequest, DataFetchBatchRequest, DataFetchRequest};

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
        /// Binance data type (e.g., klines, aggTrades, trades, bookTicker)
        #[arg(long, default_value = "klines")]
        data_type: String,
        /// Binance asset class (um or cm)
        #[arg(long, default_value = "um")]
        asset_class: String,
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
        /// Binance data type
        #[arg(long, default_value = "klines")]
        data_type: String,
        /// Binance asset class (um or cm)
        #[arg(long, default_value = "um")]
        asset_class: String,
    },
    /// List available data catalog
    List,
    /// Show data info for a symbol
    Info { symbol: String },
    /// Consolidate stored data for a symbol/interval
    Consolidate {
        /// Symbol (e.g., BTCUSDT-PERP)
        symbol: String,
        /// Interval (e.g., 1m, 5m)
        interval: String,
        /// Binance bar data type
        #[arg(long, default_value = "klines")]
        data_type: String,
    },
    /// Validate data integrity for a symbol/interval
    Validate {
        /// Symbol (e.g., BTCUSDT-PERP)
        symbol: String,
        /// Interval (e.g., 1m, 5m)
        interval: String,
        /// Binance bar data type
        #[arg(long, default_value = "klines")]
        data_type: String,
    },
    /// Check data coverage for a symbol
    Coverage {
        /// Symbol (e.g., BTCUSDT-PERP)
        symbol: String,
    },
    /// List available symbols in catalog
    Symbols,
    /// List available data types
    Types,
    /// Batch fetch status
    Batch {
        #[command(subcommand)]
        command: BatchCmd,
    },
    /// Delete data for a symbol/interval range
    #[command(name = "delete-range")]
    DeleteRange {
        /// Symbol
        symbol: String,
        /// Interval
        interval: String,
        /// Start date
        #[arg(long)]
        start: String,
        /// End date
        #[arg(long)]
        end: String,
        /// Skip confirmation
        #[arg(long)]
        yes: bool,
    },
    /// Delete all data for a symbol
    Delete {
        /// Symbol
        symbol: String,
        /// Skip confirmation
        #[arg(long)]
        yes: bool,
    },
}

#[derive(Subcommand)]
pub enum BatchCmd {
    /// List active/recent batches
    List {
        /// Filter by aggregated batch status
        #[arg(long)]
        status: Option<String>,
        /// Page size for API paging
        #[arg(long = "page-size", default_value_t = 200)]
        page_size: u64,
    },
    /// Get status of a specific batch
    Get { batch_id: String },
    /// Cancel a batch
    Cancel { batch_id: String },
}

fn is_bar_data_type(data_type: &str) -> bool {
    matches!(
        data_type,
        "klines" | "markPriceKlines" | "indexPriceKlines" | "premiumIndexKlines"
    )
}

fn fetch_batch_intervals_for_request(data_type: &str, intervals: &[String]) -> Vec<String> {
    if intervals.is_empty() && is_bar_data_type(data_type) {
        return vec!["1m".to_string()];
    }
    intervals.to_vec()
}

pub async fn dispatch(cmd: DataCmd, client: &ApiClient, format: OutputFormat) -> Result<()> {
    match cmd {
        DataCmd::Fetch {
            symbol,
            interval,
            start,
            end,
            data_type,
            asset_class,
        } => {
            let req = DataFetchRequest {
                symbol: symbol.clone(),
                interval: interval.clone(),
                start: start.clone(),
                end: end.clone(),
                data_type: data_type.clone(),
                asset_class: asset_class.clone(),
            };
            let result = client.fetch_data(&req).await?;
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    header("Data Fetch Submitted");
                    divider(50);
                    kv("Symbol", &accent(&symbol), 12);
                    kv("Interval", &interval, 12);
                    kv("Period", &format!("{} ~ {}", start, end), 12);
                    println!();
                    Ok(())
                }
            }
        }
        DataCmd::FetchBatch {
            symbols,
            interval,
            start,
            end,
            data_type,
            asset_class,
        } => {
            let effective_intervals = fetch_batch_intervals_for_request(&data_type, &interval);
            let req = DataFetchBatchRequest {
                symbols: symbols.clone(),
                intervals: effective_intervals,
                start: start.clone(),
                end: end.clone(),
                data_type,
                asset_class,
            };
            let result = client.fetch_data_batch(&req).await?;
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    header("Batch Data Fetch Submitted");
                    divider(50);
                    kv("Batch", result["batch_id"].as_str().unwrap_or("-"), 12);
                    kv("Symbols", &symbols.join(", "), 12);
                    kv("Period", &format!("{} ~ {}", start, end), 12);
                    println!();
                    Ok(())
                }
            }
        }
        DataCmd::List => {
            let result = client.list_data().await?;
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    let items = catalog_items(&result);
                    if items.is_empty() {
                        println!("  No data in catalog.");
                        return Ok(());
                    }
                    let t = Table::new(&[
                        ("Symbol", 18, "left"),
                        ("Interval", 10, "left"),
                        ("Start", 12, "left"),
                        ("End", 12, "left"),
                    ]);
                    t.header();
                    for item in &items {
                        let sym = item.get("symbol").and_then(|v| v.as_str()).unwrap_or("?");
                        let ivl = item.get("interval").and_then(|v| v.as_str()).unwrap_or("?");
                        let start_d = item
                            .get("start_date")
                            .and_then(|v| v.as_str())
                            .unwrap_or("-");
                        let end_d = item.get("end_date").and_then(|v| v.as_str()).unwrap_or("-");
                        t.row(&[&accent(sym), ivl, start_d, end_d]);
                    }
                    t.footer();
                    Ok(())
                }
            }
        }
        DataCmd::Info { symbol } => {
            let result = client.list_data().await?;
            let payload = data_info_payload(&result, &symbol);
            match format {
                OutputFormat::Json => print_json(&payload),
                OutputFormat::Text => {
                    header(&format!("Data: {}", accent(&symbol)));
                    println!("  {}", serde_json::to_string_pretty(&payload)?);
                    println!();
                    Ok(())
                }
            }
        }
        DataCmd::Consolidate {
            symbol,
            interval,
            data_type,
        } => {
            let req = DataCompactRequest {
                symbol: symbol.clone(),
                interval: interval.clone(),
                data_type: data_type.clone(),
            };
            let result = client.compact_data(&req).await?;
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    header(&format!("Consolidate: {} {}", symbol, interval));
                    println!("  Done.");
                    println!();
                    Ok(())
                }
            }
        }
        DataCmd::Validate {
            symbol,
            interval,
            data_type,
        } => {
            let result = client.validate_data(&symbol, &interval, &data_type).await?;
            match format {
                OutputFormat::Json => print_json(&result),
                OutputFormat::Text => {
                    header(&format!("Validate: {} {}", symbol, interval));
                    println!("  {}", serde_json::to_string_pretty(&result)?);
                    println!();
                    Ok(())
                }
            }
        }
        DataCmd::Coverage { symbol } => {
            let path = format!("/api/data/coverage/{}", symbol);
            let resp = client
                .request_json(reqwest::Method::GET, &path, &[], None, &[])
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    header(&format!("Coverage: {}", accent(&symbol)));
                    println!("  {}", serde_json::to_string_pretty(&resp.body)?);
                    println!();
                    Ok(())
                }
            }
        }
        DataCmd::Symbols => {
            let resp = client
                .request_json(reqwest::Method::GET, "/api/data/symbols", &[], None, &[])
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    if let Some(symbols) = resp.body.as_array() {
                        for s in symbols {
                            if let Some(name) = s.as_str() {
                                println!("  {}", name);
                            }
                        }
                    }
                    Ok(())
                }
            }
        }
        DataCmd::Types => {
            let resp = client
                .request_json(reqwest::Method::GET, "/api/data/types", &[], None, &[])
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    println!("  {}", serde_json::to_string_pretty(&resp.body)?);
                    Ok(())
                }
            }
        }
        DataCmd::Batch { command } => dispatch_batch(command, client, format).await,
        DataCmd::DeleteRange {
            symbol,
            interval,
            start,
            end,
            yes,
        } => {
            if !yes {
                crate::output::print_error(
                    &anyhow::anyhow!("use --yes to confirm delete-range"),
                    format,
                );
                std::process::exit(1);
            }
            let body = serde_json::json!({
                "symbol": symbol,
                "interval": interval,
                "start": start,
                "end": end,
            });
            let resp = client
                .request_json(
                    reqwest::Method::POST,
                    "/api/data/delete-range",
                    &[],
                    Some(body),
                    &[],
                )
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    println!("  Deleted data range for {} {}", symbol, interval);
                    Ok(())
                }
            }
        }
        DataCmd::Delete { symbol, yes } => {
            if !yes {
                crate::output::print_error(&anyhow::anyhow!("use --yes to confirm delete"), format);
                std::process::exit(1);
            }
            let body = serde_json::json!({"symbol": symbol});
            let resp = client
                .request_json(
                    reqwest::Method::DELETE,
                    &format!("/api/data/{}", symbol),
                    &[],
                    Some(body),
                    &[],
                )
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    println!("  Deleted all data for {}", symbol);
                    Ok(())
                }
            }
        }
    }
}

async fn dispatch_batch(cmd: BatchCmd, client: &ApiClient, format: OutputFormat) -> Result<()> {
    match cmd {
        BatchCmd::List { status, page_size } => {
            let mut page = 1;
            let mut batches: Vec<DataBatchItem> = Vec::new();
            loop {
                let resp = client
                    .list_data_batches(status.as_deref(), page, page_size)
                    .await?;
                let page_len = resp.batches.len() as u64;
                batches.extend(resp.batches);
                if batches.len() as u64 >= resp.total || page_len == 0 || page_len < page_size {
                    break;
                }
                page += 1;
            }
            match format {
                OutputFormat::Json => print_json(&batches),
                OutputFormat::Text => {
                    if batches.is_empty() {
                        println!("  No batches.");
                        return Ok(());
                    }
                    let t = Table::new(&[
                        ("Batch", 12, "left"),
                        ("Type", 10, "left"),
                        ("Syms", 8, "left"),
                        ("Ivl", 10, "left"),
                        ("Jobs", 6, "right"),
                        ("Status", 16, "left"),
                        ("Progress", 9, "right"),
                        ("Created", 12, "left"),
                    ]);
                    t.header();
                    for batch in &batches {
                        let syms = if batch.symbols.len() <= 2 {
                            batch.symbols.join(",")
                        } else {
                            format!("{}+{}", batch.symbols[0], batch.symbols.len() - 1)
                        };
                        let ivls = if batch.intervals.is_empty() {
                            "-".to_string()
                        } else {
                            batch.intervals.join(",")
                        };
                        t.row(&[
                            &accent(&batch.batch_id.chars().take(8).collect::<String>()),
                            &batch.data_type,
                            &syms,
                            &ivls,
                            &batch.counts.jobs.to_string(),
                            &color_status(&batch.status),
                            &format!("{}%", batch.progress),
                            batch.created_at.as_deref().unwrap_or("-"),
                        ]);
                    }
                    t.footer();
                    Ok(())
                }
            }
        }
        BatchCmd::Get { batch_id } => {
            let resp = client.get_data_batch(&batch_id).await?;
            match format {
                OutputFormat::Json => print_json(&resp),
                OutputFormat::Text => {
                    println!("  {}", serde_json::to_string_pretty(&resp)?);
                    Ok(())
                }
            }
        }
        BatchCmd::Cancel { batch_id } => {
            let resp = client.cancel_data_batch(&batch_id).await?;
            match format {
                OutputFormat::Json => print_json(&resp),
                OutputFormat::Text => {
                    println!(
                        "  Batch {} cancellation requested (cancelled {}, running {}, finished {}).",
                        batch_id, resp.cancelled_jobs, resp.running_jobs, resp.finished_jobs
                    );
                    Ok(())
                }
            }
        }
    }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fetch_batch_defaults_empty_bar_intervals_to_one_minute() {
        assert_eq!(
            fetch_batch_intervals_for_request("klines", &[]),
            vec!["1m".to_string()]
        );
    }

    #[test]
    fn fetch_batch_keeps_empty_raw_tick_intervals_empty() {
        assert!(fetch_batch_intervals_for_request("aggTrades", &[]).is_empty());
    }

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
        assert_eq!(payload["count"], 2);
    }
}
