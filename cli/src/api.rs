use anyhow::{Context, Result};
use reqwest::Client;

use crate::types::*;

#[derive(Clone)]
pub struct ApiClient {
    base_url: String,
    client: Client,
}

impl ApiClient {
    pub fn new(base_url: &str) -> Self {
        Self {
            base_url: base_url.to_string(),
            client: Client::new(),
        }
    }

    pub fn ws_url(&self) -> String {
        self.base_url
            .replace("http://", "ws://")
            .replace("https://", "wss://")
    }

    // ---- Backtest ----

    pub async fn list_backtests(&self) -> Result<BacktestRunList> {
        let resp = self
            .client
            .get(format!("{}/api/backtest/runs", self.base_url))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn get_status(&self, run_id: &str) -> Result<BacktestRunStatus> {
        let resp = self
            .client
            .get(format!("{}/api/backtest/{}/status", self.base_url, run_id))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn get_result(&self, run_id: &str) -> Result<serde_json::Value> {
        let resp = self
            .client
            .get(format!("{}/api/backtest/{}/result", self.base_url, run_id))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn run_backtest(&self, req: &BacktestRunRequest) -> Result<BacktestRunResponse> {
        let resp = self
            .client
            .post(format!("{}/api/backtest/run", self.base_url))
            .json(req)
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn cancel_backtest(&self, run_id: &str) -> Result<BacktestCancelResponse> {
        let resp = self
            .client
            .post(format!("{}/api/backtest/{}/cancel", self.base_url, run_id))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    // ---- Strategy ----

    pub async fn list_strategies(&self) -> Result<Vec<Strategy>> {
        let resp = self
            .client
            .get(format!("{}/api/strategies", self.base_url))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn get_strategy(&self, name: &str) -> Result<Strategy> {
        let resp = self
            .client
            .get(format!("{}/api/strategies/{}", self.base_url, name))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn validate_strategy(&self, name: &str) -> Result<ValidateResult> {
        let resp = self
            .client
            .post(format!("{}/api/strategies/{}/validate", self.base_url, name))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.json().await?;
        Ok(body)
    }

    pub async fn rescan_strategies(&self) -> Result<RescanResult> {
        let resp = self
            .client
            .post(format!("{}/api/strategies/rescan", self.base_url))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    // ---- Data ----

    pub async fn fetch_data(&self, req: &DataFetchRequest) -> Result<serde_json::Value> {
        let resp = self
            .client
            .post(format!("{}/api/data/fetch", self.base_url))
            .json(req)
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn list_data(&self) -> Result<serde_json::Value> {
        let resp = self
            .client
            .get(format!("{}/api/data", self.base_url))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    // ---- Node ----

    pub async fn node_status(&self) -> Result<serde_json::Value> {
        let resp = self
            .client
            .get(format!("{}/api/node/status", self.base_url))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn node_start(&self, node_type: &str) -> Result<serde_json::Value> {
        let resp = self
            .client
            .post(format!("{}/api/node/{}/start", self.base_url, node_type))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn node_stop(&self, node_type: &str) -> Result<serde_json::Value> {
        let resp = self
            .client
            .post(format!("{}/api/node/{}/stop", self.base_url, node_type))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    fn connect_hint(url: &str) -> String {
        format!(
            "Cannot connect to API at {}\nHint: is the server running? Try: docker compose up -d",
            url
        )
    }
}
