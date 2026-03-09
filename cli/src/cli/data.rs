use anyhow::Result;
use clap::Subcommand;

use crate::api::ApiClient;
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
    /// List available data
    List,
    /// Show data info for a symbol
    Info {
        /// Symbol
        symbol: String,
    },
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
                symbol,
                interval,
                start,
                end,
            };
            let result = client.fetch_data(&req).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                println!("  {}", result);
            }
        }
        DataCmd::List => {
            let result = client.list_data().await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                println!("  {}", result);
            }
        }
        DataCmd::Info { symbol } => {
            // Reuse list with symbol filter for now
            let result = client.list_data().await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                println!("  Data info for {}: {}", symbol, result);
            }
        }
    }
    Ok(())
}
