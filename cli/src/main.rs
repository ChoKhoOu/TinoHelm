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

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli_args = Cli::parse();
    let cfg = config::Config::load(cli_args.api_url.as_deref(), cli_args.api_key.as_deref())?;
    let client = api::ApiClient::new(&cfg.api_url, cfg.api_key.clone())?;

    let format = cli_args.format;
    let (command_name, result): (&'static str, anyhow::Result<()>) = match cli_args.command {
        Commands::Auth { command } => ("auth", cli::auth::dispatch(command, &cfg, format)),
        Commands::Api { command } => ("api", cli::api::dispatch(command, &client, format).await),
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
            cli::backtest::dispatch(command, &client, format).await,
        ),
        Commands::Strategy { command } => (
            "strategy",
            cli::strategy::dispatch(command, &client, format).await,
        ),
        Commands::Data { command } => ("data", cli::data::dispatch(command, &client, format).await),
        Commands::Node { command } => ("node", cli::node::dispatch(command, &client, format).await),
        Commands::Factor { command } => (
            "factor",
            cli::factor::dispatch(command, &client, format).await,
        ),
        Commands::Signal { command } => (
            "signal",
            cli::signal::dispatch(command, &client, format).await,
        ),
        Commands::Universe { command } => (
            "universe",
            cli::universe::dispatch(command, &client, format).await,
        ),
    };

    if let Err(err) = result {
        if format == OutputFormat::Llm {
            output::print_llm_error(
                output::EnvelopeError {
                    kind: "command".to_string(),
                    message: err.to_string(),
                    status_code: None,
                    body: None,
                },
                output::EnvelopeMeta::new(command_name, &cfg.api_url, cfg.auth_label()),
            )?;
            std::process::exit(1);
        }
        return Err(err);
    }

    Ok(())
}
