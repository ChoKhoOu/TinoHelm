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
    /// Show factor runner capabilities for LLM/API clients.
    Capabilities,
    /// Synchronous quick factor exploration. Pass a full ExploreRequest JSON body.
    Explore(ApiBodyArgs),
    /// Submit async deep diagnostic factor run. Pass a full RunRequest JSON body.
    Run(ApiBodyArgs),
    /// List factor runs.
    Runs {
        #[arg(long, default_value_t = 20)]
        limit: u32,
        #[arg(long)]
        factor_name: Option<String>,
    },
    /// Get factor run report/result.
    Report {
        run_id: String,
        /// Request concise LLM summary payload.
        #[arg(long)]
        summary: bool,
        /// Request full/detail payload.
        #[arg(long)]
        detail: bool,
        /// Comma-separated fields to keep in the API response.
        #[arg(long)]
        fields: Option<String>,
    },
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
    ParamsGrid(ApiBodyArgs),
    /// Compare two completed factor runs.
    Compare(CompareArgs),

    /// Compare 2+ completed factor runs.
    #[command(name = "compare-multi")]
    CompareMulti {
        run_ids: Vec<String>,
        #[arg(long, default_value_t = 1000)]
        n_bootstrap: u32,
    },
}

#[derive(clap::Args)]
pub struct CompareArgs {
    #[arg(long)]
    a: Option<String>,
    #[arg(long)]
    b: Option<String>,
    #[arg(long, default_value_t = 1000)]
    n_bootstrap: u32,
    #[arg(long, default_value_t = 0.95)]
    confidence: f64,
    #[command(flatten)]
    body_args: BodyArgs,
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

#[derive(clap::Args)]
pub struct ApiBodyArgs {
    #[command(flatten)]
    body_args: BodyArgs,
    /// Request concise LLM summary payload where supported.
    #[arg(long)]
    summary: bool,
    /// Request full/detail payload where supported.
    #[arg(long)]
    detail: bool,
    /// Comma-separated fields to keep in the API response/request.
    #[arg(long)]
    fields: Option<String>,
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
        FactorCmd::Capabilities => {
            call_and_print(
                client,
                format,
                Method::GET,
                "/api/factor/capabilities",
                vec![],
                None,
                vec![],
                "factor.capabilities",
            )
            .await
        }
        FactorCmd::Explore(args) => {
            let body = api_body_required(args, format == OutputFormat::Llm)?;
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
            let body = api_body_required(args, false)?;
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
        FactorCmd::Report {
            run_id,
            summary,
            detail,
            fields,
        } => {
            let path = format!("/api/factor/report/{run_id}");
            let mut query = Vec::new();
            let default_llm_summary =
                format == OutputFormat::Llm && !summary && !detail && fields.is_none();
            if summary || default_llm_summary {
                query.push(("summary".to_string(), "true".to_string()));
                if !detail {
                    query.push(("detail".to_string(), "false".to_string()));
                }
            }
            if detail {
                query.push(("detail".to_string(), "true".to_string()));
            }
            if let Some(fields) = fields {
                query.push(("fields".to_string(), fields));
            }
            call_and_print(
                client,
                format,
                Method::GET,
                &path,
                query,
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
            let body = api_body_required(args, false)?;
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
        FactorCmd::Compare(args) => {
            let body = compare_body(args)?;
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

fn api_body_required(args: ApiBodyArgs, default_summary: bool) -> Result<serde_json::Value> {
    let mut body = body_required(args.body_args)?;
    let body_has_result_controls = body.get("summary").is_some()
        || body.get("detail").is_some()
        || body.get("fields").is_some();
    if default_summary
        && !args.summary
        && !args.detail
        && args.fields.is_none()
        && !body_has_result_controls
    {
        body["summary"] = serde_json::Value::Bool(true);
        body["detail"] = serde_json::Value::Bool(false);
    }
    if args.summary {
        body["summary"] = serde_json::Value::Bool(true);
        if !args.detail {
            body["detail"] = serde_json::Value::Bool(false);
        }
    }
    if args.detail {
        body["detail"] = serde_json::Value::Bool(true);
    }
    if let Some(fields) = args.fields {
        body["fields"] = serde_json::Value::Array(
            fields
                .split(',')
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(|s| serde_json::Value::String(s.to_string()))
                .collect(),
        );
    }
    Ok(body)
}

fn compare_body(args: CompareArgs) -> Result<serde_json::Value> {
    if let Some(body) = read_json_body(
        args.body_args.body,
        args.body_args.body_file,
        args.body_args.stdin,
    )? {
        return Ok(body);
    }
    let a = args
        .a
        .ok_or_else(|| anyhow::anyhow!("compare requires --a/--b or --body/--body-file/--stdin"))?;
    let b = args
        .b
        .ok_or_else(|| anyhow::anyhow!("compare requires --a/--b or --body/--body-file/--stdin"))?;
    Ok(serde_json::json!({
        "eval_a_run_id": a,
        "eval_b_run_id": b,
        "n_bootstrap": args.n_bootstrap,
        "confidence": args.confidence,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn body_args(body: serde_json::Value) -> ApiBodyArgs {
        ApiBodyArgs {
            body_args: BodyArgs {
                body: Some(body.to_string()),
                body_file: None,
                stdin: false,
            },
            summary: false,
            detail: false,
            fields: None,
        }
    }

    #[test]
    fn llm_default_body_is_summary_unless_body_controls_output() {
        let body = api_body_required(
            body_args(serde_json::json!({"factor_name":"x","config":{}})),
            true,
        )
        .expect("body parses");

        assert_eq!(body["summary"], serde_json::Value::Bool(true));
        assert_eq!(body["detail"], serde_json::Value::Bool(false));
    }

    #[test]
    fn llm_default_body_preserves_explicit_detail() {
        let body = api_body_required(
            body_args(serde_json::json!({"factor_name":"x","config":{},"detail":true})),
            true,
        )
        .expect("body parses");

        assert_eq!(body["detail"], serde_json::Value::Bool(true));
        assert!(body.get("summary").is_none());
    }

    #[test]
    fn compare_body_supports_typed_flags() {
        let body = compare_body(CompareArgs {
            a: Some("run-a".to_string()),
            b: Some("run-b".to_string()),
            n_bootstrap: 123,
            confidence: 0.9,
            body_args: BodyArgs {
                body: None,
                body_file: None,
                stdin: false,
            },
        })
        .expect("compare body builds");

        assert_eq!(body["eval_a_run_id"], "run-a");
        assert_eq!(body["eval_b_run_id"], "run-b");
        assert_eq!(body["n_bootstrap"], 123);
        assert_eq!(body["confidence"], 0.9);
    }
}
