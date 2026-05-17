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
    about = "TinoHelm — typed subcommand CLI for quantitative trading control",
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

    /// Output format: json (default, machine-readable) or text (human-friendly).
    #[arg(short, long, global = true, value_enum, default_value_t = OutputFormat::Json)]
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
    /// Show CLI + API + factor engine versions.
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
    /// Trading positions, fills, orders, and analytics.
    Trading {
        #[command(subcommand)]
        command: cli::trading::TradingCmd,
    },
}

fn command_requires_api_client(command: &Commands) -> bool {
    !matches!(command, Commands::Auth { .. } | Commands::Version)
}

fn command_resolves_api_key(command: &Commands) -> bool {
    command_requires_api_client(command)
        || matches!(
            command,
            Commands::Auth {
                command: cli::auth::AuthCmd::Status
            }
        )
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli_args = Cli::parse();
    let format = cli_args.format;
    let cfg = if command_resolves_api_key(&cli_args.command) {
        match config::Config::load(cli_args.api_url.as_deref(), cli_args.api_key.as_deref()) {
            Ok(cfg) => cfg,
            Err(err) => {
                output::print_error(&err, format);
                std::process::exit(1);
            }
        }
    } else {
        config::Config::load_url_only(cli_args.api_url.as_deref())
    };
    let api_client = if command_requires_api_client(&cli_args.command) {
        Some(api::ApiClient::new(&cfg.api_url, cfg.api_key.clone()))
    } else {
        None
    };

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

    let result: anyhow::Result<()> = match cli_args.command {
        Commands::Auth { command } => cli::auth::dispatch(command, &cfg, format),
        Commands::Version => {
            let data = serde_json::json!({
                "cli_version": env!("CARGO_PKG_VERSION"),
                "git_sha": option_env!("GIT_SHA").or(option_env!("TINO_GIT_SHA")),
                "build_time": option_env!("BUILD_TIME").or(option_env!("TINO_BUILD_TIME")),
            });
            match format {
                OutputFormat::Json => output::print_json(&data),
                OutputFormat::Text => {
                    println!("tino v{}", env!("CARGO_PKG_VERSION"));
                    Ok(())
                }
            }
        }
        Commands::Backtest { command } => {
            dispatch_with_client!(
                client,
                cli::backtest::dispatch(command, client, format).await
            )
        }
        Commands::Strategy { command } => {
            dispatch_with_client!(
                client,
                cli::strategy::dispatch(command, client, format).await
            )
        }
        Commands::Data { command } => {
            dispatch_with_client!(client, cli::data::dispatch(command, client, format).await)
        }
        Commands::Node { command } => {
            dispatch_with_client!(client, cli::node::dispatch(command, client, format).await)
        }
        Commands::Factor { command } => {
            dispatch_with_client!(client, cli::factor::dispatch(command, client, format).await)
        }
        Commands::Signal { command } => {
            dispatch_with_client!(client, cli::signal::dispatch(command, client, format).await)
        }
        Commands::Universe { command } => {
            dispatch_with_client!(
                client,
                cli::universe::dispatch(command, client, format).await
            )
        }
        Commands::Trading { command } => {
            dispatch_with_client!(
                client,
                cli::trading::dispatch(command, client, format).await
            )
        }
    };

    if let Err(err) = result {
        output::print_error(&err, format);
        std::process::exit(1);
    }

    Ok(())
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
    fn only_api_backed_commands_and_auth_status_resolve_api_keys() {
        assert!(command_resolves_api_key(&Commands::Auth {
            command: cli::auth::AuthCmd::Status,
        }));
        assert!(!command_resolves_api_key(&Commands::Auth {
            command: cli::auth::AuthCmd::Login {
                api_key: Some("secret".to_string()),
                stdin: false,
            },
        }));
        assert!(!command_resolves_api_key(&Commands::Auth {
            command: cli::auth::AuthCmd::Logout,
        }));
        assert!(!command_resolves_api_key(&Commands::Version));
    }
}
