use std::io::Read;

use anyhow::Result;
use clap::Subcommand;
use serde_json::json;

use crate::cli::style::{accent, dim, divider, header, kv, muted};
use crate::config::{ApiKeySource, Config};
use crate::output::{print_json, print_llm_success, EnvelopeMeta, OutputFormat};

#[derive(Subcommand)]
pub enum AuthCmd {
    /// Show API authentication status without printing secrets.
    Status,
    /// Store an API key in ~/.tino/credentials/api_key with mode 0600.
    Login {
        /// API key. Prefer --stdin in shell history sensitive environments.
        #[arg(long)]
        api_key: Option<String>,
        /// Read API key from stdin.
        #[arg(long)]
        stdin: bool,
    },
    /// Remove the stored API key file.
    Logout,
}

pub fn dispatch(cmd: AuthCmd, cfg: &Config, format: OutputFormat) -> Result<()> {
    match cmd {
        AuthCmd::Status => print_status(cfg, format),
        AuthCmd::Login { api_key, stdin } => {
            let key = read_key(api_key, stdin)?;
            let path = Config::write_credentials(&key)?;
            let data = json!({
                "stored": true,
                "path": path,
                "mode": "0600",
            });
            match format {
                OutputFormat::Llm => print_llm_success(
                    data,
                    EnvelopeMeta::new("auth.login", &cfg.api_url, cfg.auth_label()),
                ),
                OutputFormat::Json => print_json(&data),
                OutputFormat::Text => {
                    header("API key stored");
                    divider(50);
                    kv("Path", &accent(&path.display().to_string()), 10);
                    kv("Mode", "0600", 10);
                    println!();
                    Ok(())
                }
            }
        }
        AuthCmd::Logout => {
            let removed = Config::remove_credentials()?;
            let data = json!({
                "removed": removed.is_some(),
                "path": removed,
            });
            match format {
                OutputFormat::Llm => print_llm_success(
                    data,
                    EnvelopeMeta::new("auth.logout", &cfg.api_url, cfg.auth_label()),
                ),
                OutputFormat::Json => print_json(&data),
                OutputFormat::Text => {
                    header("API key removed");
                    divider(50);
                    match removed {
                        Some(path) => kv("Path", &accent(&path.display().to_string()), 10),
                        None => kv("Path", &muted("not found"), 10),
                    }
                    println!();
                    Ok(())
                }
            }
        }
    }
}

fn print_status(cfg: &Config, format: OutputFormat) -> Result<()> {
    let source_path = match &cfg.api_key_source {
        ApiKeySource::CredentialsFile(path) => Some(path.display().to_string()),
        _ => None,
    };
    let data = json!({
        "api_url": cfg.api_url,
        "auth": cfg.auth_label(),
        "api_key_source": cfg.api_key_source.label(),
        "api_key_file": source_path,
    });
    match format {
        OutputFormat::Llm => print_llm_success(
            data,
            EnvelopeMeta::new("auth.status", &cfg.api_url, cfg.auth_label()),
        ),
        OutputFormat::Json => print_json(&data),
        OutputFormat::Text => {
            header("Auth Status");
            divider(50);
            kv("API URL", &accent(&cfg.api_url), 16);
            kv("Auth", cfg.auth_label(), 16);
            kv("Key Source", cfg.api_key_source.label(), 16);
            if let Some(path) = source_path {
                kv("Key File", &dim(&path), 16);
            }
            println!();
            Ok(())
        }
    }
}

fn read_key(api_key: Option<String>, stdin: bool) -> Result<String> {
    if api_key.is_some() && stdin {
        anyhow::bail!("Use only one of --api-key or --stdin");
    }
    let key = if let Some(key) = api_key {
        key
    } else if stdin {
        let mut s = String::new();
        std::io::stdin().read_to_string(&mut s)?;
        s
    } else {
        anyhow::bail!("Missing API key. Pass --api-key or --stdin");
    };
    let key = key.trim().to_string();
    if key.is_empty() {
        anyhow::bail!("API key is empty");
    }
    Ok(key)
}
