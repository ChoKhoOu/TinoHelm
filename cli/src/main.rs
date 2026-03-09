mod api;
mod cli;
mod config;
mod tui;
mod types;

use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "tino", about = "TinoHelm — Quantitative trading platform powered by NautilusTrader")]
struct Cli {
    /// API server URL
    #[arg(long, global = true)]
    api_url: Option<String>,

    /// Output format: text or json
    #[arg(short, long, global = true, default_value = "text")]
    format: String,

    #[command(subcommand)]
    command: Option<Commands>,
}

#[derive(Subcommand)]
enum Commands {
    /// Enter interactive TUI dashboard
    Ui,
    /// Show version
    Version,
    /// Backtest management
    Backtest {
        #[command(subcommand)]
        command: cli::backtest::BacktestCmd,
    },
    /// Strategy management
    Strategy {
        #[command(subcommand)]
        command: cli::strategy::StrategyCmd,
    },
    /// Data management
    Data {
        #[command(subcommand)]
        command: cli::data::DataCmd,
    },
    /// Node management (sandbox/live)
    Node {
        #[command(subcommand)]
        command: cli::node::NodeCmd,
    },
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli_args = Cli::parse();
    let cfg = config::Config::load(cli_args.api_url.as_deref())?;
    let client = api::ApiClient::new(&cfg.api_url);

    match cli_args.command {
        None | Some(Commands::Ui) => {
            tui::run(client).await?;
        }
        Some(Commands::Version) => {
            println!("  TinoHelm v{}", env!("CARGO_PKG_VERSION"));
            println!("  Quantitative trading platform powered by NautilusTrader");
        }
        Some(Commands::Backtest { command }) => {
            cli::backtest::dispatch(command, &client, &cli_args.format).await?;
        }
        Some(Commands::Strategy { command }) => {
            cli::strategy::dispatch(command, &client, &cli_args.format).await?;
        }
        Some(Commands::Data { command }) => {
            cli::data::dispatch(command, &client, &cli_args.format).await?;
        }
        Some(Commands::Node { command }) => {
            cli::node::dispatch(command, &client, &cli_args.format).await?;
        }
    }

    Ok(())
}
