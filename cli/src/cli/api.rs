use std::io::Read;
use std::path::PathBuf;

use anyhow::{Context, Result};
use clap::Subcommand;
use crossterm::style::Stylize;
use reqwest::Method;

use crate::api::{ApiClient, ApiHttpError};
use crate::cli::style::{accent, dim, divider, header, kv, muted, Table};
use crate::output::{
    print_json, print_llm_error, print_llm_success, Envelope, EnvelopeError, EnvelopeMeta,
    OutputFormat,
};

#[derive(Subcommand)]
pub enum ApiCmd {
    /// Call any HTTP API endpoint. This is the canonical LLM-first escape hatch.
    Call {
        /// HTTP method: GET, POST, PUT, PATCH, DELETE.
        method: String,
        /// API path, e.g. /api/factor/list. Absolute URLs are accepted but stored Tino API keys are only sent to --api-url origin.
        path: String,
        /// Query parameter as key=value. Repeatable.
        #[arg(short = 'q', long = "query", value_parser = parse_pair)]
        query: Vec<(String, String)>,
        /// JSON request body as a string.
        #[arg(long)]
        body: Option<String>,
        /// Read JSON request body from file.
        #[arg(long = "body-file")]
        body_file: Option<PathBuf>,
        /// Read JSON request body from stdin.
        #[arg(long)]
        stdin: bool,
        /// Extra HTTP header as Name: value. Repeatable.
        #[arg(long = "header", value_parser = parse_header)]
        header: Vec<(String, String)>,
    },
    /// Convenience GET wrapper.
    Get {
        path: String,
        #[arg(short = 'q', long = "query", value_parser = parse_pair)]
        query: Vec<(String, String)>,
    },
    /// Convenience POST wrapper.
    Post {
        path: String,
        #[arg(long)]
        body: Option<String>,
        #[arg(long = "body-file")]
        body_file: Option<PathBuf>,
        #[arg(long)]
        stdin: bool,
    },
    /// Download any endpoint response body to a file. Useful for artifacts and reports.
    Download {
        /// HTTP method. Defaults to GET.
        #[arg(long, default_value = "GET")]
        method: String,
        /// API path or absolute URL.
        path: String,
        /// Query parameter as key=value. Repeatable.
        #[arg(short = 'q', long = "query", value_parser = parse_pair)]
        query: Vec<(String, String)>,
        /// Output file path. Defaults to the path basename.
        #[arg(short, long)]
        output: Option<PathBuf>,
        /// JSON request body as a string.
        #[arg(long)]
        body: Option<String>,
        /// Read JSON request body from file.
        #[arg(long = "body-file")]
        body_file: Option<PathBuf>,
        /// Read JSON request body from stdin.
        #[arg(long)]
        stdin: bool,
        /// Extra HTTP header as Name: value. Repeatable.
        #[arg(long = "header", value_parser = parse_header)]
        header: Vec<(String, String)>,
    },
    /// Fetch and list the server OpenAPI route table.
    Routes {
        /// Keep only paths containing this substring.
        #[arg(long)]
        filter: Option<String>,
    },
}

pub async fn dispatch(cmd: ApiCmd, client: &ApiClient, format: OutputFormat) -> Result<()> {
    match cmd {
        ApiCmd::Call {
            method,
            path,
            query,
            body,
            body_file,
            stdin,
            header,
        } => {
            let method = parse_method(&method)?;
            let body = read_json_body(body, body_file, stdin)?;
            call_and_print(
                client, format, method, &path, query, body, header, "api.call",
            )
            .await
        }
        ApiCmd::Get { path, query } => {
            call_and_print(
                client,
                format,
                Method::GET,
                &path,
                query,
                None,
                vec![],
                "api.get",
            )
            .await
        }
        ApiCmd::Post {
            path,
            body,
            body_file,
            stdin,
        } => {
            let body = read_json_body(body, body_file, stdin)?;
            call_and_print(
                client,
                format,
                Method::POST,
                &path,
                vec![],
                body,
                vec![],
                "api.post",
            )
            .await
        }
        ApiCmd::Download {
            method,
            path,
            query,
            output,
            body,
            body_file,
            stdin,
            header,
        } => {
            let method = parse_method(&method)?;
            let body = read_json_body(body, body_file, stdin)?;
            download_and_print(client, format, method, &path, query, body, header, output).await
        }
        ApiCmd::Routes { filter } => {
            let result = client
                .request_json(Method::GET, "/openapi.json", &[], None, &[])
                .await;
            let resp = match result {
                Ok(resp) => resp,
                Err(err) => {
                    if format == OutputFormat::Llm {
                        let mut meta =
                            EnvelopeMeta::new("api.routes", client.base_url(), client.auth_label());
                        meta.method = Some("GET");
                        meta.path = Some("/openapi.json");
                        print_api_error(err, meta)?;
                        std::process::exit(1);
                    }
                    return Err(err);
                }
            };
            let routes = extract_routes(&resp.body, filter.as_deref());
            match format {
                OutputFormat::Llm => {
                    let mut meta =
                        EnvelopeMeta::new("api.routes", client.base_url(), client.auth_label());
                    meta.method = Some("GET");
                    meta.path = Some("/openapi.json");
                    meta.status_code = Some(resp.status_code);
                    meta.elapsed_ms = Some(resp.elapsed_ms);
                    print_llm_success(serde_json::json!({ "routes": routes }), meta)
                }
                OutputFormat::Json => print_json(&routes),
                OutputFormat::Text => {
                    print_routes_table(&routes);
                    Ok(())
                }
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
pub async fn call_and_print(
    client: &ApiClient,
    format: OutputFormat,
    method: Method,
    path: &str,
    query: Vec<(String, String)>,
    body: Option<serde_json::Value>,
    headers: Vec<(String, String)>,
    command: &str,
) -> Result<()> {
    let method_s = method.as_str().to_string();
    let result = client
        .request_json(method, path, &query, body, &headers)
        .await;
    match result {
        Ok(resp) => match format {
            OutputFormat::Llm => {
                let mut meta = EnvelopeMeta::new(command, client.base_url(), client.auth_label());
                meta.method = Some(&method_s);
                meta.path = Some(path);
                meta.status_code = Some(resp.status_code);
                meta.elapsed_ms = Some(resp.elapsed_ms);
                if let Some((ok, data, error)) = unwrap_api_envelope(&resp.body, resp.status_code) {
                    return print_json(&Envelope {
                        ok,
                        data,
                        error,
                        meta,
                    });
                }
                if path.starts_with("/api/factor/report/")
                    && resp.body.get("status").and_then(|v| v.as_str()) == Some("failed")
                {
                    let code = resp
                        .body
                        .get("error_code")
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string())
                        .or_else(|| Some("factor_run_failed".to_string()));
                    let message = resp
                        .body
                        .get("error")
                        .or_else(|| resp.body.get("message"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("Factor run failed")
                        .to_string();
                    return print_json(&Envelope {
                        ok: false,
                        data: Some(resp.body.clone()),
                        error: Some(EnvelopeError {
                            code,
                            kind: "api".to_string(),
                            message,
                            status_code: Some(resp.status_code),
                            body: Some(resp.body.clone()),
                        }),
                        meta,
                    });
                }
                print_llm_success(resp.body, meta)
            }
            OutputFormat::Json => print_json(&resp.body),
            OutputFormat::Text => {
                println!("{}", serde_json::to_string_pretty(&resp.body)?);
                Ok(())
            }
        },
        Err(err) => {
            if format == OutputFormat::Llm {
                let mut meta = EnvelopeMeta::new(command, client.base_url(), client.auth_label());
                meta.method = Some(&method_s);
                meta.path = Some(path);
                print_api_error(err, meta)?;
                std::process::exit(1);
            }
            Err(err)
        }
    }
}

#[allow(clippy::too_many_arguments)]
pub async fn download_and_print(
    client: &ApiClient,
    format: OutputFormat,
    method: Method,
    path: &str,
    query: Vec<(String, String)>,
    body: Option<serde_json::Value>,
    headers: Vec<(String, String)>,
    output: Option<PathBuf>,
) -> Result<()> {
    let method_s = method.as_str().to_string();
    let output_path = output.unwrap_or_else(|| default_download_path(path));
    let result = client
        .request_bytes(method, path, &query, body, &headers)
        .await;
    match result {
        Ok(resp) => {
            if let Some(parent) = output_path.parent().filter(|p| !p.as_os_str().is_empty()) {
                std::fs::create_dir_all(parent)
                    .with_context(|| format!("Cannot create {}", parent.display()))?;
            }
            std::fs::write(&output_path, &resp.bytes)
                .with_context(|| format!("Cannot write {}", output_path.display()))?;
            let output_display = output_path.display().to_string();
            let data = serde_json::json!({
                "path": output_display,
                "bytes": resp.bytes.len(),
                "content_type": resp.content_type,
            });
            match format {
                OutputFormat::Llm => {
                    let mut meta =
                        EnvelopeMeta::new("api.download", client.base_url(), client.auth_label());
                    meta.method = Some(&method_s);
                    meta.path = Some(path);
                    meta.status_code = Some(resp.status_code);
                    meta.elapsed_ms = Some(resp.elapsed_ms);
                    print_llm_success(data, meta)
                }
                OutputFormat::Json => print_json(&data),
                OutputFormat::Text => {
                    header("Downloaded");
                    divider(50);
                    kv("Path", &accent(&output_path.display().to_string()), 12);
                    kv("Bytes", &resp.bytes.len().to_string(), 12);
                    if let Some(content_type) = resp.content_type {
                        kv("Content-Type", &dim(&content_type), 12);
                    }
                    println!();
                    Ok(())
                }
            }
        }
        Err(err) => {
            if format == OutputFormat::Llm {
                let mut meta =
                    EnvelopeMeta::new("api.download", client.base_url(), client.auth_label());
                meta.method = Some(&method_s);
                meta.path = Some(path);
                print_api_error(err, meta)?;
                std::process::exit(1);
            }
            Err(err)
        }
    }
}

fn unwrap_api_envelope(
    body: &serde_json::Value,
    status_code: u16,
) -> Option<(bool, Option<serde_json::Value>, Option<EnvelopeError>)> {
    let ok = body.get("ok").and_then(|v| v.as_bool())?;
    if !(body.get("data").is_some() || body.get("error").is_some()) {
        return None;
    }

    let data = body.get("data").cloned();
    let error = if ok {
        None
    } else {
        let body_error = body
            .get("error")
            .cloned()
            .unwrap_or(serde_json::Value::Null);
        let (code, message) = match &body_error {
            serde_json::Value::Object(obj) => {
                let code = obj
                    .get("code")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                let message = obj
                    .get("message")
                    .and_then(|v| v.as_str())
                    .unwrap_or("API response reported ok=false")
                    .to_string();
                (code, message)
            }
            serde_json::Value::String(s) => (None, s.to_string()),
            _ => (None, "API response reported ok=false".to_string()),
        };
        Some(EnvelopeError {
            code,
            kind: "api".to_string(),
            message,
            status_code: Some(status_code),
            body: Some(body_error),
        })
    };

    Some((ok, data, error))
}

fn print_api_error(err: anyhow::Error, mut meta: EnvelopeMeta<'_>) -> Result<()> {
    if let Some(http) = err.downcast_ref::<ApiHttpError>() {
        meta.status_code = Some(http.status_code);
        return print_llm_error(
            EnvelopeError {
                code: None,
                kind: "http".to_string(),
                message: http.body.to_string(),
                status_code: Some(http.status_code),
                body: Some(http.body.clone()),
            },
            meta,
        );
    }
    print_llm_error(
        EnvelopeError {
            code: None,
            kind: "client".to_string(),
            message: err.to_string(),
            status_code: None,
            body: None,
        },
        meta,
    )
}

fn default_download_path(path: &str) -> PathBuf {
    let path_part = path.split('?').next().unwrap_or(path).trim_end_matches('/');
    let name = path_part
        .rsplit('/')
        .next()
        .filter(|s| !s.is_empty() && !s.contains(':'))
        .unwrap_or("tino-download.bin");
    PathBuf::from(name)
}

fn parse_method(value: &str) -> Result<Method> {
    value
        .to_ascii_uppercase()
        .parse::<Method>()
        .with_context(|| format!("Invalid HTTP method: {value}"))
}

fn parse_pair(s: &str) -> std::result::Result<(String, String), String> {
    let pos = s
        .find('=')
        .ok_or_else(|| format!("expected key=value, got {s:?}"))?;
    Ok((s[..pos].to_string(), s[pos + 1..].to_string()))
}

fn parse_header(s: &str) -> std::result::Result<(String, String), String> {
    let pos = s
        .find(':')
        .ok_or_else(|| format!("expected 'Name: value', got {s:?}"))?;
    Ok((s[..pos].trim().to_string(), s[pos + 1..].trim().to_string()))
}

pub fn read_json_body(
    body: Option<String>,
    body_file: Option<PathBuf>,
    stdin: bool,
) -> Result<Option<serde_json::Value>> {
    let sources = body.is_some() as u8 + body_file.is_some() as u8 + stdin as u8;
    if sources > 1 {
        anyhow::bail!("Use only one of --body, --body-file, --stdin");
    }
    let Some(raw) = (if let Some(body) = body {
        Some(body)
    } else if let Some(path) = body_file {
        Some(
            std::fs::read_to_string(&path)
                .with_context(|| format!("Cannot read {}", path.display()))?,
        )
    } else if stdin {
        let mut s = String::new();
        std::io::stdin().read_to_string(&mut s)?;
        Some(s)
    } else {
        None
    }) else {
        return Ok(None);
    };
    let value = serde_json::from_str(&raw).with_context(|| "Body must be valid JSON")?;
    Ok(Some(value))
}

fn extract_routes(openapi: &serde_json::Value, filter: Option<&str>) -> Vec<serde_json::Value> {
    let mut routes = Vec::new();
    let Some(paths) = openapi.get("paths").and_then(|v| v.as_object()) else {
        return routes;
    };
    let methods = ["get", "post", "put", "patch", "delete"];
    for (path, spec) in paths {
        if let Some(f) = filter {
            if !path.contains(f) {
                continue;
            }
        }
        let Some(spec_obj) = spec.as_object() else {
            continue;
        };
        for method in methods {
            let Some(op) = spec_obj.get(method) else {
                continue;
            };
            routes.push(serde_json::json!({
                "method": method.to_ascii_uppercase(),
                "path": path,
                "operation_id": op.get("operationId").and_then(|v| v.as_str()).unwrap_or(""),
                "summary": op.get("summary").and_then(|v| v.as_str()).unwrap_or(""),
                "tags": op.get("tags").cloned().unwrap_or(serde_json::Value::Array(vec![])),
            }));
        }
    }
    routes.sort_by(|a, b| {
        let ap = a.get("path").and_then(|v| v.as_str()).unwrap_or("");
        let bp = b.get("path").and_then(|v| v.as_str()).unwrap_or("");
        ap.cmp(bp).then_with(|| {
            a.get("method")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .cmp(b.get("method").and_then(|v| v.as_str()).unwrap_or(""))
        })
    });
    routes
}

fn print_routes_table(routes: &[serde_json::Value]) {
    header("API Routes");
    if routes.is_empty() {
        println!("  {}", muted("No routes found."));
        return;
    }
    let t = Table::new(&[
        ("Method", 7, "left"),
        ("Path", 45, "left"),
        ("Summary", 40, "left"),
    ]);
    t.header();
    for route in routes {
        let method = route.get("method").and_then(|v| v.as_str()).unwrap_or("");
        let path = route.get("path").and_then(|v| v.as_str()).unwrap_or("");
        let summary = route.get("summary").and_then(|v| v.as_str()).unwrap_or("");
        let method_colored = match method {
            "GET" => format!("{}", method.green()),
            "POST" => format!("{}", method.cyan()),
            "PUT" | "PATCH" => format!("{}", method.yellow()),
            "DELETE" => format!("{}", method.red()),
            _ => method.to_string(),
        };
        let path_short = truncate_chars(path, 45);
        let summary_short = truncate_chars(summary, 40);
        t.row(&[&method_colored, &accent(&path_short), &dim(&summary_short)]);
    }
    t.footer();
    println!("    {}", muted(&format!("{} route(s)", routes.len())));
}

fn truncate_chars(value: &str, max_chars: usize) -> String {
    if max_chars == 0 {
        return String::new();
    }
    if value.chars().count() <= max_chars {
        return value.to_string();
    }
    let prefix: String = value.chars().take(max_chars - 1).collect();
    format!("{prefix}…")
}

#[cfg(test)]
mod tests {
    use super::{truncate_chars, unwrap_api_envelope};

    #[test]
    fn truncate_chars_leaves_short_strings_unchanged() {
        assert_eq!(truncate_chars("12345", 5), "12345");
    }

    #[test]
    fn truncate_chars_shortens_ascii_to_width_with_ellipsis() {
        assert_eq!(truncate_chars("123456", 5), "1234…");
    }

    #[test]
    fn truncate_chars_is_utf8_safe() {
        assert_eq!(truncate_chars("资金费率异常回落", 6), "资金费率异…");
    }

    #[test]
    fn unwrap_api_envelope_requires_envelope_shape() {
        let body = serde_json::json!({"ok": true, "message": "done"});
        assert!(unwrap_api_envelope(&body, 200).is_none());
    }

    #[test]
    fn unwrap_api_envelope_preserves_data_payload() {
        let body = serde_json::json!({"ok": true, "data": {"value": 7}, "error": null});
        let (ok, data, error) = unwrap_api_envelope(&body, 200).expect("envelope");
        assert!(ok);
        assert_eq!(data, Some(serde_json::json!({"value": 7})));
        assert!(error.is_none());
    }

    #[test]
    fn unwrap_api_envelope_accepts_string_error() {
        let body = serde_json::json!({"ok": false, "error": "bad request"});
        let (ok, data, error) = unwrap_api_envelope(&body, 422).expect("envelope");
        assert!(!ok);
        assert!(data.is_none());
        let error = error.expect("error");
        assert_eq!(error.message, "bad request");
        assert_eq!(error.status_code, Some(422));
    }
}
