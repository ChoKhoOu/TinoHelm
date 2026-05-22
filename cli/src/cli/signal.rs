use std::path::PathBuf;

use anyhow::Result;
use clap::Subcommand;
use reqwest::Method;

use crate::api::ApiClient;
use crate::cli::api::{call_and_print, read_json_body};
use crate::output::OutputFormat;

#[derive(Subcommand)]
pub enum SignalCmd {
    /// List registered signals.
    List {
        #[arg(long)]
        include_deprecated: bool,
    },
    /// Submit async signal evaluation. Pass a full RunSignalRequest JSON body.
    Run(BodyArgs),
    /// List signal runs.
    Runs {
        #[arg(long)]
        status: Option<String>,
        #[arg(long)]
        signal_name: Option<String>,
        #[arg(long, default_value_t = 1)]
        page: u32,
        #[arg(long, default_value_t = 20)]
        page_size: u32,
    },
    /// Get signal run report/result.
    Report { run_id: String },
    /// Cancel a signal run.
    Cancel { run_id: String },
    /// Compare 2+ completed signal runs.
    Compare {
        run_ids: Vec<String>,
        #[arg(long = "metric")]
        metrics: Vec<String>,
    },
    /// Export a completed signal run to portfolio.yaml-compatible JSON.
    Export {
        run_id: String,
        #[arg(long)]
        strategy_class: Option<String>,
    },
}

#[derive(clap::Args)]
pub struct BodyArgs {
    /// JSON request body as a string.
    #[arg(long)]
    body: Option<String>,
    /// Read JSON request body from file.
    #[arg(long = "body-file")]
    body_file: Option<PathBuf>,
    /// Read JSON request body from stdin.
    #[arg(long)]
    stdin: bool,
}

pub async fn dispatch(cmd: SignalCmd, client: &ApiClient, format: OutputFormat) -> Result<()> {
    match cmd {
        SignalCmd::List { include_deprecated } => {
            let query = vec![(
                "include_deprecated".to_string(),
                include_deprecated.to_string(),
            )];
            call_and_print(
                client,
                format,
                Method::GET,
                "/api/signal/list",
                query,
                None,
                vec![],
                "signal.list",
            )
            .await
        }
        SignalCmd::Run(args) => {
            let body = body_required(args)?;
            call_and_print(
                client,
                format,
                Method::POST,
                "/api/signal/run",
                vec![],
                Some(body),
                vec![],
                "signal.run",
            )
            .await
        }
        SignalCmd::Runs {
            status,
            signal_name,
            page,
            page_size,
        } => {
            let mut query = vec![
                ("page".to_string(), page.to_string()),
                ("page_size".to_string(), page_size.to_string()),
            ];
            if let Some(status) = status {
                query.push(("status".to_string(), status));
            }
            if let Some(name) = signal_name {
                query.push(("signal_name".to_string(), name));
            }
            call_and_print(
                client,
                format,
                Method::GET,
                "/api/signal/runs",
                query,
                None,
                vec![],
                "signal.runs",
            )
            .await
        }
        SignalCmd::Report { run_id } => {
            let path = format!("/api/signal/report/{run_id}");
            call_and_print(
                client,
                format,
                Method::GET,
                &path,
                vec![],
                None,
                vec![],
                "signal.report",
            )
            .await
        }
        SignalCmd::Cancel { run_id } => {
            let path = format!("/api/signal/cancel/{run_id}");
            call_and_print(
                client,
                format,
                Method::POST,
                &path,
                vec![],
                None,
                vec![],
                "signal.cancel",
            )
            .await
        }
        SignalCmd::Compare { run_ids, metrics } => {
            if run_ids.len() < 2 {
                anyhow::bail!("compare requires at least 2 run IDs");
            }
            let mut body = serde_json::json!({ "run_ids": run_ids });
            if !metrics.is_empty() {
                body["metrics"] = serde_json::json!(metrics);
            }
            call_and_print(
                client,
                format,
                Method::POST,
                "/api/signal/compare",
                vec![],
                Some(body),
                vec![],
                "signal.compare",
            )
            .await
        }
        SignalCmd::Export {
            run_id,
            strategy_class,
        } => {
            let path = format!("/api/signal/export/{run_id}");
            let mut query = vec![];
            if let Some(strategy_class) = strategy_class {
                query.push(("strategy_class".to_string(), strategy_class));
            }
            call_and_print(
                client,
                format,
                Method::GET,
                &path,
                query,
                None,
                vec![],
                "signal.export",
            )
            .await
        }
    }
}

fn body_required(args: BodyArgs) -> Result<serde_json::Value> {
    read_json_body(args.body, args.body_file, args.stdin)?
        .ok_or_else(|| anyhow::anyhow!("Missing JSON body. Use --body, --body-file, or --stdin"))
}
