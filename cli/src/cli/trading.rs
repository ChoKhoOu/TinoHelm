use anyhow::Result;
use clap::Subcommand;

use crate::api::ApiClient;
use crate::cli::style::*;
use crate::output::{print_json, OutputFormat};

#[derive(Subcommand)]
pub enum TradingCmd {
    /// List positions
    #[command(name = "positions")]
    Positions {
        #[command(subcommand)]
        command: PositionsCmd,
    },
    /// List fills
    #[command(name = "fills")]
    Fills {
        #[command(subcommand)]
        command: FillsCmd,
    },
    /// Order management
    #[command(name = "orders")]
    Orders {
        #[command(subcommand)]
        command: OrdersCmd,
    },
    /// Account summary
    Summary {
        /// Node type: sandbox or live
        #[arg(long, default_value = "sandbox")]
        node_type: String,
    },
}

#[derive(Subcommand)]
pub enum PositionsCmd {
    /// List all positions
    List {
        /// Node type: sandbox or live
        #[arg(long)]
        node_type: Option<String>,
        /// Filter open/closed
        #[arg(long)]
        open: Option<bool>,
        /// Filter by strategy
        #[arg(long)]
        strategy: Option<String>,
    },
}

#[derive(Subcommand)]
pub enum FillsCmd {
    /// List recent fills
    List {
        /// Node type: sandbox or live
        #[arg(long)]
        node_type: Option<String>,
        /// Max results
        #[arg(long, default_value = "50")]
        limit: u32,
        /// Filter by strategy
        #[arg(long)]
        strategy: Option<String>,
    },
}

#[derive(Subcommand)]
pub enum OrdersCmd {
    /// List orders
    List {
        /// Node type: sandbox or live
        #[arg(long)]
        node_type: Option<String>,
        /// Filter by status
        #[arg(long)]
        status: Option<String>,
        /// Max results
        #[arg(long, default_value = "50")]
        limit: u32,
    },
    /// Cancel an order
    Cancel {
        /// Order ID to cancel
        order_id: String,
        /// Node type: sandbox or live
        #[arg(long, default_value = "sandbox")]
        node_type: String,
    },
}

pub async fn dispatch(cmd: TradingCmd, client: &ApiClient, format: OutputFormat) -> Result<()> {
    match cmd {
        TradingCmd::Positions { command } => dispatch_positions(command, client, format).await,
        TradingCmd::Fills { command } => dispatch_fills(command, client, format).await,
        TradingCmd::Orders { command } => dispatch_orders(command, client, format).await,
        TradingCmd::Summary { node_type } => {
            let data = client.trading_summary(&node_type).await?;
            match format {
                OutputFormat::Json => print_json(&data),
                OutputFormat::Text => {
                    header("Trading Summary");
                    divider(50);
                    kv("Open Positions", &data.open_positions.to_string(), 16);
                    kv("Total Positions", &data.total_positions.to_string(), 16);
                    kv("Total Fills", &data.total_fills.to_string(), 16);
                    kv(
                        "Realized PnL",
                        &color_value(Some(data.total_realized_pnl), "+.2f"),
                        16,
                    );
                    if !data.open_instruments.is_empty() {
                        kv("Instruments", &data.open_instruments.join(", "), 16);
                    }
                    println!();
                    Ok(())
                }
            }
        }
    }
}

async fn dispatch_positions(
    cmd: PositionsCmd,
    client: &ApiClient,
    format: OutputFormat,
) -> Result<()> {
    match cmd {
        PositionsCmd::List {
            node_type,
            open,
            strategy,
        } => {
            let data = client
                .list_positions(node_type.as_deref(), open, strategy.as_deref())
                .await?;
            match format {
                OutputFormat::Json => print_json(&data),
                OutputFormat::Text => {
                    if data.is_empty() {
                        println!("  No positions found.");
                        return Ok(());
                    }
                    let t = Table::new(&[
                        ("Instrument", 18, "left"),
                        ("Side", 6, "left"),
                        ("Qty", 12, "right"),
                        ("Entry", 12, "right"),
                        ("PnL", 12, "right"),
                        ("Strategy", 16, "left"),
                    ]);
                    t.header();
                    for pos in &data {
                        let pnl = pos
                            .unrealized_pnl
                            .or(pos.realized_pnl)
                            .map(|v| color_value(Some(v), "+.2f"))
                            .unwrap_or_else(|| muted("-"));
                        let entry = pos
                            .avg_px_open
                            .map(|v| format!("{:.2}", v))
                            .unwrap_or_else(|| "-".to_string());
                        t.row(&[
                            &accent(&pos.instrument_id),
                            &pos.side,
                            &pos.quantity,
                            &entry,
                            &pnl,
                            &pos.strategy_id_tag,
                        ]);
                    }
                    t.footer();
                    Ok(())
                }
            }
        }
    }
}

async fn dispatch_fills(cmd: FillsCmd, client: &ApiClient, format: OutputFormat) -> Result<()> {
    match cmd {
        FillsCmd::List {
            node_type,
            limit,
            strategy,
        } => {
            let data = client
                .list_fills(node_type.as_deref(), limit, strategy.as_deref())
                .await?;
            match format {
                OutputFormat::Json => print_json(&data),
                OutputFormat::Text => {
                    if data.is_empty() {
                        println!("  No fills found.");
                        return Ok(());
                    }
                    let t = Table::new(&[
                        ("Time", 20, "left"),
                        ("Instrument", 18, "left"),
                        ("Side", 5, "left"),
                        ("Qty", 12, "right"),
                        ("Price", 12, "right"),
                    ]);
                    t.header();
                    for fill in &data {
                        t.row(&[
                            &fill.ts_event[..20.min(fill.ts_event.len())],
                            &accent(&fill.instrument_id),
                            &fill.order_side,
                            &fill.last_qty,
                            &fill.last_px,
                        ]);
                    }
                    t.footer();
                    Ok(())
                }
            }
        }
    }
}

async fn dispatch_orders(cmd: OrdersCmd, client: &ApiClient, format: OutputFormat) -> Result<()> {
    match cmd {
        OrdersCmd::List {
            node_type,
            status,
            limit,
        } => {
            let data = client
                .list_orders(node_type.as_deref(), status.as_deref(), limit)
                .await?;
            match format {
                OutputFormat::Json => print_json(&data),
                OutputFormat::Text => {
                    if data.is_empty() {
                        println!("  No orders found.");
                        return Ok(());
                    }
                    let t = Table::new(&[
                        ("ID", 12, "left"),
                        ("Instrument", 18, "left"),
                        ("Side", 5, "left"),
                        ("Type", 12, "left"),
                        ("Qty", 10, "right"),
                        ("Status", 12, "left"),
                    ]);
                    t.header();
                    for order in &data {
                        let id_short = &order.order_id[..12.min(order.order_id.len())];
                        t.row(&[
                            id_short,
                            &accent(&order.instrument_id),
                            &order.side,
                            &order.order_type,
                            &order.quantity,
                            &color_status(&order.status),
                        ]);
                    }
                    t.footer();
                    Ok(())
                }
            }
        }
        OrdersCmd::Cancel {
            order_id,
            node_type,
        } => {
            let body = serde_json::json!({
                "order_id": order_id,
                "node_type": node_type,
            });
            let resp = client
                .request_json(
                    reqwest::Method::POST,
                    "/api/orders/cancel",
                    &[],
                    Some(body),
                    &[],
                )
                .await?;
            match format {
                OutputFormat::Json => print_json(&resp.body),
                OutputFormat::Text => {
                    println!("  Order {} cancelled.", accent(&order_id));
                    Ok(())
                }
            }
        }
    }
}
