use futures_util::StreamExt;
use tokio::sync::mpsc;
use tokio_tungstenite::{connect_async, tungstenite::Message};
use tracing::{debug, warn};

use crate::types::WsEvent;

/// Run a WebSocket client that connects to the unified `/ws/events` endpoint,
/// auto-reconnects with exponential backoff, and sends parsed events on the channel.
pub async fn run_ws_client(
    ws_base_url: String,
    event_tx: mpsc::UnboundedSender<WsClientEvent>,
) {
    let url = format!("{}/ws/events", ws_base_url);
    let mut backoff_secs: u64 = 1;
    let max_backoff: u64 = 30;

    loop {
        let _ = event_tx.send(WsClientEvent::Connecting);
        debug!("Connecting to WebSocket: {}", url);

        match connect_async(&url).await {
            Ok((stream, _)) => {
                let _ = event_tx.send(WsClientEvent::Connected);
                backoff_secs = 1; // Reset backoff on successful connect

                let (mut _sink, mut read) = stream.split();

                loop {
                    match read.next().await {
                        Some(Ok(Message::Text(text))) => {
                            match serde_json::from_str::<WsEvent>(&text) {
                                Ok(event) => {
                                    let _ = event_tx.send(WsClientEvent::Event(event));
                                }
                                Err(_) => {
                                    debug!("Unknown WS message type, ignoring: {}", &text[..text.len().min(100)]);
                                }
                            }
                        }
                        Some(Ok(Message::Ping(_))) => {
                            // Tungstenite handles pong automatically
                        }
                        Some(Ok(Message::Close(_))) => {
                            debug!("WebSocket server sent close frame");
                            break;
                        }
                        Some(Ok(_)) => {}
                        Some(Err(e)) => {
                            warn!("WebSocket read error: {}", e);
                            break;
                        }
                        None => {
                            debug!("WebSocket stream ended");
                            break;
                        }
                    }
                }
            }
            Err(e) => {
                warn!("WebSocket connect failed: {}", e);
            }
        }

        // Disconnected — schedule reconnect with backoff
        let _ = event_tx.send(WsClientEvent::Disconnected {
            retry_secs: backoff_secs,
        });
        tokio::time::sleep(std::time::Duration::from_secs(backoff_secs)).await;
        backoff_secs = (backoff_secs * 2).min(max_backoff);
    }
}

/// Events emitted by the WebSocket client to the TUI event loop.
#[derive(Debug)]
pub enum WsClientEvent {
    Connecting,
    Connected,
    Disconnected { retry_secs: u64 },
    Event(WsEvent),
}
