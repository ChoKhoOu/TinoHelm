use std::io::Read;
use std::path::PathBuf;

use anyhow::{Context, Result};
use reqwest::Method;

use crate::api::ApiClient;
use crate::output::{print_json, OutputFormat};

/// Internal helper: make an HTTP call and render the response.
/// Used by factor/signal commands that still proxy through generic API paths.
#[allow(clippy::too_many_arguments)]
pub async fn call_and_print(
    client: &ApiClient,
    format: OutputFormat,
    method: Method,
    path: &str,
    query: Vec<(String, String)>,
    body: Option<serde_json::Value>,
    headers: Vec<(String, String)>,
    _command: &str,
) -> Result<()> {
    let resp = client
        .request_json(method, path, &query, body, &headers)
        .await?;
    match format {
        OutputFormat::Json => print_json(&resp.body),
        OutputFormat::Text => {
            println!("{}", serde_json::to_string_pretty(&resp.body)?);
            Ok(())
        }
    }
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
