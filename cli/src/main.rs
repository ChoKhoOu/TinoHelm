mod api;
mod cli;
mod config;
mod output;
mod types;

use clap::{Parser, Subcommand};
use output::OutputFormat;

#[derive(Parser)]
#[command(
    name = "tino",
    about = "TinoHelm — LLM-first CLI for local and remote trading/research control",
    arg_required_else_help = true,
    disable_help_subcommand = true
)]
struct Cli {
    /// API server URL. Priority: flag > TINO_API_URL > ~/.tino/config/user.yaml > default.
    #[arg(long, global = true)]
    api_url: Option<String>,

    /// API key sent as X-API-Key. Priority: flag > TINO_API_KEY > ~/.tino/credentials/api_key > user.yaml.
    #[arg(long, global = true)]
    api_key: Option<String>,

    /// Output format: text for humans, json for raw API JSON, llm for stable envelope.
    #[arg(short, long, global = true, value_enum, default_value_t = OutputFormat::Text)]
    format: OutputFormat,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Authentication helpers for X-API-Key based local/remote API access.
    Auth {
        #[command(subcommand)]
        command: cli::auth::AuthCmd,
    },
    /// Generic API caller; covers every FastAPI operation immediately.
    Api {
        #[command(subcommand)]
        command: cli::api::ApiCmd,
    },
    /// Show version.
    Version,
    /// Backtest management.
    Backtest {
        #[command(subcommand)]
        command: cli::backtest::BacktestCmd,
    },
    /// Strategy management.
    Strategy {
        #[command(subcommand)]
        command: cli::strategy::StrategyCmd,
    },
    /// Data management.
    Data {
        #[command(subcommand)]
        command: cli::data::DataCmd,
    },
    /// Node management (sandbox/live).
    Node {
        #[command(subcommand)]
        command: cli::node::NodeCmd,
    },
    /// Factor research workflows.
    Factor {
        #[command(subcommand)]
        command: cli::factor::FactorCmd,
    },
    /// Signal research/execution workflows.
    Signal {
        #[command(subcommand)]
        command: cli::signal::SignalCmd,
    },
    /// Universe DB helpers for factor/signal research.
    Universe {
        #[command(subcommand)]
        command: cli::universe::UniverseCmd,
    },
}

fn command_requires_api_client(command: &Commands) -> bool {
    !matches!(command, Commands::Auth { .. } | Commands::Version)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_commands_do_not_require_api_client() {
        assert!(!command_requires_api_client(&Commands::Auth {
            command: cli::auth::AuthCmd::Status,
        }));
        assert!(!command_requires_api_client(&Commands::Auth {
            command: cli::auth::AuthCmd::Logout,
        }));
        assert!(!command_requires_api_client(&Commands::Version));
    }

    #[test]
    fn api_backed_commands_require_api_client() {
        assert!(command_requires_api_client(&Commands::Api {
            command: cli::api::ApiCmd::Routes { filter: None },
        }));
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli_args = Cli::parse();
    let cfg = config::Config::load(cli_args.api_url.as_deref(), cli_args.api_key.as_deref())?;
    let api_client = if command_requires_api_client(&cli_args.command) {
        Some(api::ApiClient::new(&cfg.api_url, cfg.api_key.clone()))
    } else {
        None
    };

    let format = cli_args.format;
    macro_rules! dispatch_with_client {
        ($client:ident, $body:expr) => {{
            match api_client
                .as_ref()
                .expect("API-backed command must initialize ApiClient")
            {
                Ok($client) => $body,
                Err(err) => Err(anyhow::anyhow!(err.to_string())),
            }
        }};
    }

    let (command_name, result): (&'static str, anyhow::Result<()>) = match cli_args.command {
        Commands::Auth { command } => ("auth", cli::auth::dispatch(command, &cfg, format)),
        Commands::Api { command } => (
            "api",
            dispatch_with_client!(client, cli::api::dispatch(command, client, format).await),
        ),
        Commands::Version => {
            let data = serde_json::json!({
                "name": "tino",
                "version": env!("CARGO_PKG_VERSION"),
                "description": env!("CARGO_PKG_DESCRIPTION"),
                "tui": false,
            });
            let result = match format {
                OutputFormat::Llm => output::print_llm_success(
                    data,
                    output::EnvelopeMeta::new("version", &cfg.api_url, cfg.auth_label()),
                ),
                OutputFormat::Json => output::print_json(&data),
                OutputFormat::Text => {
                    println!("  TinoHelm v{}", env!("CARGO_PKG_VERSION"));
                    println!("  LLM-first CLI; TUI removed");
                    Ok(())
                }
            };
            ("version", result)
        }
        Commands::Backtest { command } => (
            "backtest",
            dispatch_with_client!(
                client,
                cli::backtest::dispatch(command, client, format).await
            ),
        ),
        Commands::Strategy { command } => (
            "strategy",
            dispatch_with_client!(
                client,
                cli::strategy::dispatch(command, client, format).await
            ),
        ),
        Commands::Data { command } => (
            "data",
            dispatch_with_client!(client, cli::data::dispatch(command, client, format).await),
        ),
        Commands::Node { command } => (
            "node",
            dispatch_with_client!(client, cli::node::dispatch(command, client, format).await),
        ),
        Commands::Factor { command } => (
            "factor",
            dispatch_with_client!(client, cli::factor::dispatch(command, client, format).await),
        ),
        Commands::Signal { command } => (
            "signal",
            dispatch_with_client!(client, cli::signal::dispatch(command, client, format).await),
        ),
        Commands::Universe { command } => (
            "universe",
            dispatch_with_client!(
                client,
                cli::universe::dispatch(command, client, format).await
            ),
        ),
    };

    if let Err(err) = result {
        if format.is_machine() {
            let error = output::EnvelopeError {
                kind: "command".to_string(),
                message: err.to_string(),
                status_code: None,
                body: None,
            };
            if format == OutputFormat::Llm {
                output::print_llm_error(
                    error,
                    output::EnvelopeMeta::new(command_name, &cfg.api_url, cfg.auth_label()),
                )?;
            } else {
                output::print_json(&serde_json::json!({
                    "ok": false,
                    "error": error,
                    "meta": output::EnvelopeMeta::new(command_name, &cfg.api_url, cfg.auth_label()),
                }))?;
            }
            std::process::exit(1);
        }
        return Err(err);
    }

    Ok(())
}
