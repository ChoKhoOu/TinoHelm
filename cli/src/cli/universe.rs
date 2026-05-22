use anyhow::Result;
use clap::Subcommand;
use reqwest::Method;

use crate::api::ApiClient;
use crate::cli::api::call_and_print;
use crate::output::OutputFormat;

#[derive(Subcommand)]
pub enum UniverseCmd {
    /// List universe CSV stems known by the factor module.
    List,
    /// Sync a universe CSV into the DB.
    Sync { csv_path: String },
    /// Get a universe DB record by ID.
    Get { universe_id: u64 },
}

pub async fn dispatch(cmd: UniverseCmd, client: &ApiClient, format: OutputFormat) -> Result<()> {
    match cmd {
        UniverseCmd::List => {
            call_and_print(
                client,
                format,
                Method::GET,
                "/api/factor/universes",
                vec![],
                None,
                vec![],
                "universe.list",
            )
            .await
        }
        UniverseCmd::Sync { csv_path } => {
            let body = serde_json::json!({ "csv_path": csv_path });
            call_and_print(
                client,
                format,
                Method::POST,
                "/api/factor/universes/sync",
                vec![],
                Some(body),
                vec![],
                "universe.sync",
            )
            .await
        }
        UniverseCmd::Get { universe_id } => {
            let path = format!("/api/factor/universes/{universe_id}");
            call_and_print(
                client,
                format,
                Method::GET,
                &path,
                vec![],
                None,
                vec![],
                "universe.get",
            )
            .await
        }
    }
}
