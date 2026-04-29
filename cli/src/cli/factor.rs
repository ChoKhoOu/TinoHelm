use std::path::PathBuf;

use anyhow::Result;
use clap::Subcommand;
use reqwest::Method;

use crate::api::ApiClient;
use crate::cli::api::{call_and_print, read_json_body};
use crate::output::OutputFormat;

#[derive(Subcommand)]
pub enum FactorCmd {
    /// List registered factors.
    List {
        #[arg(long)]
        include_experimental: bool,
    },
    /// List universe CSV stems known by the factor module.
    Universes,
    /// List symbols with catalog bar data.
    Symbols,
    /// Synchronous quick factor exploration. Pass a full ExploreRequest JSON body.
    Explore(BodyArgs),
    /// Submit async deep diagnostic factor run. Pass a full RunRequest JSON body.
    Run(BodyArgs),
    /// List factor runs.
    Runs {
        #[arg(long, default_value_t = 20)]
        limit: u32,
        #[arg(long)]
        factor_name: Option<String>,
    },
    /// Get factor run report/result.
    Report { run_id: String },
    /// Cancel a factor run.
    Cancel { run_id: String },
    /// Create a custom factor template.
    Create {
        name: String,
        #[arg(long, default_value = "自定义")]
        category: String,
        #[arg(long)]
        template: Option<String>,
    },
    /// Synchronous factor parameter grid search. Pass a full ParamsGridRequest JSON body.
    #[command(name = "params-grid")]
    ParamsGrid(BodyArgs),
    /// Compare two completed factor runs.
    Compare {
        #[arg(long)]
        a: String,
        #[arg(long)]
        b: String,
        #[arg(long, default_value_t = 1000)]
        n_bootstrap: u32,
        #[arg(long, default_value_t = 0.95)]
        confidence: f64,
    },
    /// Compare 2+ completed factor runs.
    #[command(name = "compare-multi")]
    CompareMulti {
        run_ids: Vec<String>,
        #[arg(long, default_value_t = 1000)]
        n_bootstrap: u32,
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

pub async fn dispatch(cmd: FactorCmd, client: &ApiClient, format: OutputFormat) -> Result<()> {
    match cmd {
        FactorCmd::List {
            include_experimental,
        } => {
            let query = vec![(
                "include_experimental".to_string(),
                include_experimental.to_string(),
            )];
            call_and_print(
                client,
                format,
                Method::GET,
                "/api/factor/list",
                query,
                None,
                vec![],
                "factor.list",
            )
            .await
        }
        FactorCmd::Universes => {
            call_and_print(
                client,
                format,
                Method::GET,
                "/api/factor/universes",
                vec![],
                None,
                vec![],
                "factor.universes",
            )
            .await
        }
        FactorCmd::Symbols => {
            call_and_print(
                client,
                format,
                Method::GET,
                "/api/factor/symbols",
                vec![],
                None,
                vec![],
                "factor.symbols",
            )
            .await
        }
        FactorCmd::Explore(args) => {
            let body = body_required(args)?;
            call_and_print(
                client,
                format,
                Method::POST,
                "/api/factor/explore",
                vec![],
                Some(body),
                vec![],
                "factor.explore",
            )
            .await
        }
        FactorCmd::Run(args) => {
            let body = body_required(args)?;
            call_and_print(
                client,
                format,
                Method::POST,
                "/api/factor/run",
                vec![],
                Some(body),
                vec![],
                "factor.run",
            )
            .await
        }
        FactorCmd::Runs { limit, factor_name } => {
            let mut query = vec![("limit".to_string(), limit.to_string())];
            if let Some(name) = factor_name {
                query.push(("factor_name".to_string(), name));
            }
            call_and_print(
                client,
                format,
                Method::GET,
                "/api/factor/runs",
                query,
                None,
                vec![],
                "factor.runs",
            )
            .await
        }
        FactorCmd::Report { run_id } => {
            let path = format!("/api/factor/report/{run_id}");
            call_and_print(
                client,
                format,
                Method::GET,
                &path,
                vec![],
                None,
                vec![],
                "factor.report",
            )
            .await
        }
        FactorCmd::Cancel { run_id } => {
            let path = format!("/api/factor/cancel/{run_id}");
            call_and_print(
                client,
                format,
                Method::POST,
                &path,
                vec![],
                None,
                vec![],
                "factor.cancel",
            )
            .await
        }
        FactorCmd::Create {
            name,
            category,
            template,
        } => {
            let mut body = serde_json::json!({ "name": name, "category": category });
            if let Some(template) = template {
                body["template"] = serde_json::Value::String(template);
            }
            call_and_print(
                client,
                format,
                Method::POST,
                "/api/factor/create",
                vec![],
                Some(body),
                vec![],
                "factor.create",
            )
            .await
        }
        FactorCmd::ParamsGrid(args) => {
            let body = body_required(args)?;
            call_and_print(
                client,
                format,
                Method::POST,
                "/api/factor/params_grid",
                vec![],
                Some(body),
                vec![],
                "factor.params_grid",
            )
            .await
        }
        FactorCmd::Compare {
            a,
            b,
            n_bootstrap,
            confidence,
        } => {
            let body = serde_json::json!({
                "eval_a_run_id": a,
                "eval_b_run_id": b,
                "n_bootstrap": n_bootstrap,
                "confidence": confidence,
            });
            call_and_print(
                client,
                format,
                Method::POST,
                "/api/factor/compare",
                vec![],
                Some(body),
                vec![],
                "factor.compare",
            )
            .await
        }
        FactorCmd::CompareMulti {
            run_ids,
            n_bootstrap,
        } => {
            if run_ids.len() < 2 {
                anyhow::bail!("compare-multi requires at least 2 run IDs");
            }
            let body = serde_json::json!({
                "eval_run_ids": run_ids,
                "n_bootstrap": n_bootstrap,
            });
            call_and_print(
                client,
                format,
                Method::POST,
                "/api/factor/compare/multi",
                vec![],
                Some(body),
                vec![],
                "factor.compare_multi",
            )
            .await
        }
    }
}

fn body_required(args: BodyArgs) -> Result<serde_json::Value> {
    read_json_body(args.body, args.body_file, args.stdin)?
        .ok_or_else(|| anyhow::anyhow!("Missing JSON body. Use --body, --body-file, or --stdin"))
}
