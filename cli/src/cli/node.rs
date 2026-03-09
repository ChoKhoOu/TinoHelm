use anyhow::Result;
use clap::Subcommand;

use crate::api::ApiClient;

#[derive(Subcommand)]
pub enum NodeCmd {
    /// Show node status
    Status,
    /// Start a node
    Start {
        /// Node type: sandbox or live
        #[arg(default_value = "sandbox")]
        node_type: String,
    },
    /// Stop a node
    Stop {
        /// Node type: sandbox or live
        #[arg(default_value = "sandbox")]
        node_type: String,
    },
}

pub async fn dispatch(cmd: NodeCmd, client: &ApiClient, format: &str) -> Result<()> {
    match cmd {
        NodeCmd::Status => {
            let result = client.node_status().await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                println!("  {}", result);
            }
        }
        NodeCmd::Start { node_type } => {
            let result = client.node_start(&node_type).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                println!("  Started: {}", node_type);
            }
        }
        NodeCmd::Stop { node_type } => {
            let result = client.node_stop(&node_type).await?;
            if format == "json" {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                println!("  Stopped: {}", node_type);
            }
        }
    }
    Ok(())
}
