use std::path::PathBuf;

use anyhow::{anyhow, Result};
use clap::Subcommand;
use reqwest::Method;
use serde_json::Value;

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
    /// Show factor runner capabilities.
    Capabilities,
    /// Synchronous quick factor exploration.
    Explore(ApiBodyArgs),
    /// Submit async deep diagnostic factor run.
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
        #[arg(long)]
        summary: bool,
        #[arg(long)]
        detail: bool,
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
    /// Synchronous factor parameter grid search.
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
    /// Factor name (typed flag, mutually exclusive with --body/--body-file/--stdin).
    #[arg(long)]
    factor: Option<String>,
    /// Universe name.
    #[arg(long)]
    universe: Option<String>,
    /// Start date (YYYY-MM-DD).
    #[arg(long)]
    start: Option<String>,
    /// End date (YYYY-MM-DD).
    #[arg(long)]
    end: Option<String>,
    /// Factor parameter as key=value. Repeatable.
    #[arg(long = "param", value_parser = parse_param)]
    params: Vec<(String, String)>,
    /// Request concise summary payload where supported.
    #[arg(long)]
    summary: bool,
    /// Request full/detail payload where supported.
    #[arg(long)]
    detail: bool,
    /// Comma-separated fields to keep in the API response/request.
    #[arg(long)]
    fields: Option<String>,
}

fn parse_param(s: &str) -> std::result::Result<(String, String), String> {
    let pos = s
        .find('=')
        .ok_or_else(|| format!("expected key=value, got '{s}'"))?;
    Ok((s[..pos].to_string(), s[pos + 1..].to_string()))
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
            let body = api_body_required(args)?;
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
            let body = api_body_required(args)?;
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
            if summary {
                query.push(("summary".to_string(), "true".to_string()));
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
            let body = api_body_required(args)?;
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

fn api_body_required(args: ApiBodyArgs) -> Result<serde_json::Value> {
    let has_body_input =
        args.body_args.body.is_some() || args.body_args.body_file.is_some() || args.body_args.stdin;
    let has_typed_flags = args.factor.is_some()
        || args.universe.is_some()
        || args.start.is_some()
        || args.end.is_some()
        || !args.params.is_empty();

    if has_body_input && has_typed_flags {
        anyhow::bail!(
            "Typed flags (--factor, --universe, --start, --end, --param) are mutually exclusive with --body/--body-file/--stdin"
        );
    }

    let mut body = if has_body_input {
        body_required(args.body_args)?
    } else if has_typed_flags {
        let mut obj = serde_json::Map::new();
        if let Some(factor) = args.factor {
            obj.insert("factor_name".to_string(), Value::String(factor));
        }
        if let Some(universe) = args.universe {
            obj.insert("universe".to_string(), Value::String(universe));
        }
        if let Some(start) = args.start {
            obj.insert("start".to_string(), Value::String(start));
        }
        if let Some(end) = args.end {
            obj.insert("end".to_string(), Value::String(end));
        }
        if !args.params.is_empty() {
            let config: serde_json::Map<String, Value> = args
                .params
                .into_iter()
                .map(|(k, v)| {
                    let parsed: Value = serde_json::from_str(&v).unwrap_or(Value::String(v));
                    (k, parsed)
                })
                .collect();
            obj.insert("config".to_string(), Value::Object(config));
        }
        Value::Object(obj)
    } else {
        return Err(anyhow!(
            "Provide either typed flags (--factor, --universe, etc.) or a JSON body (--body, --body-file, --stdin)"
        ));
    };

    let object = body
        .as_object_mut()
        .ok_or_else(|| anyhow!("JSON body must be an object"))?;
    if args.summary {
        object.insert("summary".to_string(), Value::Bool(true));
        if !args.detail {
            object.insert("detail".to_string(), Value::Bool(false));
        }
    }
    if args.detail {
        object.insert("detail".to_string(), Value::Bool(true));
    }
    if let Some(fields) = args.fields {
        object.insert(
            "fields".to_string(),
            Value::Array(
                fields
                    .split(',')
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(|s| Value::String(s.to_string()))
                    .collect(),
            ),
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
            factor: None,
            universe: None,
            start: None,
            end: None,
            params: vec![],
            summary: false,
            detail: false,
            fields: None,
        }
    }

    #[test]
    fn api_body_rejects_non_object_json() {
        let err = api_body_required(body_args(serde_json::json!([])))
            .expect_err("array body must be rejected");
        assert!(err.to_string().contains("JSON body must be an object"));
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
    }
}
