use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use reqwest::{Client, Method, Url};

use crate::types::*;

#[derive(Debug, Clone)]
pub struct ApiResponse {
    pub status_code: u16,
    pub elapsed_ms: u128,
    pub body: serde_json::Value,
}

#[derive(Debug, Clone)]
pub struct ApiBytesResponse {
    pub status_code: u16,
    pub elapsed_ms: u128,
    pub content_type: Option<String>,
    pub bytes: Vec<u8>,
}

#[derive(Debug)]
pub struct ApiHttpError {
    pub status_code: u16,
    pub body: serde_json::Value,
}

impl std::fmt::Display for ApiHttpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "HTTP {}: {}", self.status_code, self.body)
    }
}

impl std::error::Error for ApiHttpError {}

#[derive(Clone)]
pub struct ApiClient {
    base_url: String,
    client: Client,
    public_client: Client,
    auth_configured: bool,
}

#[allow(dead_code)]
impl ApiClient {
    pub fn new(base_url: &str, api_key: Option<String>) -> Result<Self> {
        let mut default_headers = HeaderMap::new();
        let auth_configured = api_key
            .as_ref()
            .map(|s| !s.trim().is_empty())
            .unwrap_or(false);
        if let Some(key) = api_key.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
            default_headers.insert(
                "X-API-Key",
                HeaderValue::from_str(key)
                    .context("TINO_API_KEY contains invalid HTTP header characters")?,
            );
        }
        let client = Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(30))
            .connect_timeout(Duration::from_secs(5))
            .default_headers(default_headers)
            .build()
            .unwrap_or_else(|_| Client::new());
        let public_client = Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(30))
            .connect_timeout(Duration::from_secs(5))
            .build()
            .unwrap_or_else(|_| Client::new());
        Ok(Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            client,
            public_client,
            auth_configured,
        })
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    pub fn auth_configured(&self) -> bool {
        self.auth_configured
    }

    pub fn auth_label(&self) -> &'static str {
        if self.auth_configured {
            "configured"
        } else {
            "none"
        }
    }

    pub async fn request_json(
        &self,
        method: Method,
        path: &str,
        query: &[(String, String)],
        body: Option<serde_json::Value>,
        headers: &[(String, String)],
    ) -> Result<ApiResponse> {
        let url = self.resolve_url(path);
        let mut req = self.client_for_url(&url).request(method, &url);
        if !query.is_empty() {
            req = req.query(query);
        }
        for (name, value) in headers {
            let header_name = HeaderName::from_bytes(name.as_bytes())
                .with_context(|| format!("Invalid header name: {name}"))?;
            let header_value = HeaderValue::from_str(value)
                .with_context(|| format!("Invalid header value for {name}"))?;
            req = req.header(header_name, header_value);
        }
        if let Some(body) = body {
            req = req.json(&body);
        }
        let started = Instant::now();
        let resp = req
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let status = resp.status();
        let status_code = status.as_u16();
        let text = resp.text().await?;
        let body = if text.trim().is_empty() {
            serde_json::Value::Null
        } else {
            serde_json::from_str(&text).unwrap_or_else(|_| serde_json::json!({ "raw": text }))
        };
        if !status.is_success() {
            anyhow::bail!(ApiHttpError { status_code, body });
        }
        Ok(ApiResponse {
            status_code,
            elapsed_ms: started.elapsed().as_millis(),
            body,
        })
    }

    pub async fn request_bytes(
        &self,
        method: Method,
        path: &str,
        query: &[(String, String)],
        body: Option<serde_json::Value>,
        headers: &[(String, String)],
    ) -> Result<ApiBytesResponse> {
        let url = self.resolve_url(path);
        let mut req = self.client_for_url(&url).request(method, &url);
        if !query.is_empty() {
            req = req.query(query);
        }
        for (name, value) in headers {
            let header_name = HeaderName::from_bytes(name.as_bytes())
                .with_context(|| format!("Invalid header name: {name}"))?;
            let header_value = HeaderValue::from_str(value)
                .with_context(|| format!("Invalid header value for {name}"))?;
            req = req.header(header_name, header_value);
        }
        if let Some(body) = body {
            req = req.json(&body);
        }

        let started = Instant::now();
        let resp = req
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let status = resp.status();
        let status_code = status.as_u16();
        let content_type = resp
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .map(ToString::to_string);
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            let body = if text.trim().is_empty() {
                serde_json::Value::Null
            } else {
                serde_json::from_str(&text).unwrap_or_else(|_| serde_json::json!({ "raw": text }))
            };
            anyhow::bail!(ApiHttpError { status_code, body });
        }
        let bytes = resp.bytes().await?.to_vec();
        Ok(ApiBytesResponse {
            status_code,
            elapsed_ms: started.elapsed().as_millis(),
            content_type,
            bytes,
        })
    }

    fn resolve_url(&self, path: &str) -> String {
        if path.starts_with("http://") || path.starts_with("https://") {
            path.to_string()
        } else if path.starts_with('/') {
            format!("{}{}", self.base_url, path)
        } else {
            format!("{}/{}", self.base_url, path)
        }
    }

    fn client_for_url(&self, url: &str) -> &Client {
        if self.is_same_origin(url) {
            &self.client
        } else {
            &self.public_client
        }
    }

    fn is_same_origin(&self, url: &str) -> bool {
        let Ok(base) = Url::parse(&self.base_url) else {
            return false;
        };
        let Ok(target) = Url::parse(url) else {
            return false;
        };
        base.scheme() == target.scheme()
            && base.host_str() == target.host_str()
            && base.port_or_known_default() == target.port_or_known_default()
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

    pub async fn delete_backtest(&self, run_id: &str) -> Result<serde_json::Value> {
        let resp = self
            .client
            .delete(format!("{}/api/backtest/{}", self.base_url, run_id))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    // ---- Optimization ----

    pub async fn optimize_backtest(&self, req: &OptimizeRequest) -> Result<OptimizeResponse> {
        let resp = self
            .client
            .post(format!("{}/api/backtest/optimize", self.base_url))
            .json(req)
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn optimize_status(&self, opt_id: u64) -> Result<OptimizeStatus> {
        let resp = self
            .client
            .get(format!(
                "{}/api/backtest/optimize/{}/status",
                self.base_url, opt_id
            ))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn optimize_result(&self, opt_id: u64) -> Result<serde_json::Value> {
        let resp = self
            .client
            .get(format!(
                "{}/api/backtest/optimize/{}/result",
                self.base_url, opt_id
            ))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn optimize_list(
        &self,
        limit: u32,
        strategy: Option<&str>,
    ) -> Result<serde_json::Value> {
        let mut url = format!(
            "{}/api/backtest/optimize/runs?limit={}",
            self.base_url, limit
        );
        if let Some(s) = strategy {
            url.push_str(&format!("&strategy={}", s));
        }
        let resp = self
            .client
            .get(&url)
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

    pub async fn get_strategy_params(&self, name: &str) -> Result<serde_json::Value> {
        let resp = self
            .client
            .get(format!("{}/api/strategies/{}/params", self.base_url, name))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn validate_strategy(&self, name: &str) -> Result<ValidateResult> {
        let resp = self
            .client
            .post(format!(
                "{}/api/strategies/{}/validate",
                self.base_url, name
            ))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let status = resp.status();
        if status.as_u16() == 422 {
            // FastAPI wraps HTTPException body in {"detail": ...}
            let wrapper: serde_json::Value = resp.json().await?;
            if let Some(detail) = wrapper.get("detail") {
                let result: ValidateResult = serde_json::from_value(detail.clone())?;
                return Ok(result);
            }
            anyhow::bail!("Validation failed with unexpected 422 response");
        }
        let body = resp.error_for_status()?.json().await?;
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

    pub async fn create_strategy(
        &self,
        req: &StrategyCreateRequest,
    ) -> Result<StrategyCreateResponse> {
        let resp = self
            .client
            .post(format!("{}/api/strategies/create", self.base_url))
            .json(req)
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    // ---- Data ----

    pub async fn fetch_data(&self, req: &DataFetchRequest) -> Result<serde_json::Value> {
        let payload = serde_json::json!({
            "symbols": [req.symbol.clone()],
            "intervals": [req.interval.clone()],
            "start": req.start,
            "end": req.end,
        });
        let resp = self
            .client
            .post(format!("{}/api/data/fetch-batch", self.base_url))
            .json(&payload)
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn list_data(&self) -> Result<serde_json::Value> {
        let resp = self
            .client
            .get(format!("{}/api/data/catalog", self.base_url))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn fetch_data_batch(&self, req: &DataFetchBatchRequest) -> Result<serde_json::Value> {
        let resp = self
            .client
            .post(format!("{}/api/data/fetch-batch", self.base_url))
            .json(req)
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn compact_data(&self, req: &DataCompactRequest) -> Result<serde_json::Value> {
        let resp = self
            .client
            .post(format!("{}/api/data/compact", self.base_url))
            .json(req)
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn validate_data(&self, symbol: &str, interval: &str) -> Result<serde_json::Value> {
        let resp = self
            .client
            .get(format!(
                "{}/api/data/validate/{}/{}",
                self.base_url, symbol, interval
            ))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn scan_data(&self) -> Result<serde_json::Value> {
        let resp = self
            .client
            .post(format!("{}/api/data/scan", self.base_url))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    // ---- Trading ----

    pub async fn list_positions(
        &self,
        node_type: Option<&str>,
        is_open: Option<bool>,
        strategy_id_tag: Option<&str>,
    ) -> Result<Vec<TradingPosition>> {
        let mut url = format!("{}/api/trading/positions?", self.base_url);
        if let Some(nt) = node_type {
            url.push_str(&format!("node_type={}&", nt));
        }
        if let Some(open) = is_open {
            url.push_str(&format!("is_open={}&", open));
        }
        if let Some(tag) = strategy_id_tag {
            url.push_str(&format!("strategy_id_tag={}&", Self::url_encode(tag)));
        }
        let resp = self
            .client
            .get(&url)
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn list_fills(
        &self,
        node_type: Option<&str>,
        limit: u32,
        strategy_id_tag: Option<&str>,
    ) -> Result<Vec<TradingFill>> {
        let mut url = format!("{}/api/trading/fills?limit={}", self.base_url, limit);
        if let Some(nt) = node_type {
            url.push_str(&format!("&node_type={}", Self::url_encode(nt)));
        }
        if let Some(tag) = strategy_id_tag {
            url.push_str(&format!("&strategy_id_tag={}", Self::url_encode(tag)));
        }
        let resp = self
            .client
            .get(&url)
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn trading_summary(&self, node_type: &str) -> Result<TradingSummary> {
        let resp = self
            .client
            .get(format!(
                "{}/api/trading/summary?node_type={}",
                self.base_url, node_type
            ))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    // ---- Orders ----

    pub async fn list_orders(
        &self,
        node_type: Option<&str>,
        status: Option<&str>,
        limit: u32,
    ) -> Result<Vec<TradingOrder>> {
        let mut url = format!("{}/api/orders?limit={}", self.base_url, limit);
        if let Some(nt) = node_type {
            url.push_str(&format!("&node_type={}", Self::url_encode(nt)));
        }
        if let Some(s) = status {
            url.push_str(&format!("&status={}", Self::url_encode(s)));
        }
        let resp = self
            .client
            .get(&url)
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    // ---- Node Strategies ----

    pub async fn list_portfolios(&self, mode: &str) -> Result<PortfoliosResponse> {
        let resp = self
            .client
            .get(format!("{}/api/node/strategies", self.base_url))
            .query(&[("mode", mode)])
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    async fn portfolio_action(
        &self,
        name: &str,
        mode: &str,
        action: &str,
    ) -> Result<serde_json::Value> {
        let body = PortfolioActionRequest {
            name: name.to_string(),
            mode: mode.to_string(),
        };
        let resp = self
            .client
            .post(format!("{}/api/node/strategy/{}", self.base_url, action))
            .json(&body)
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn start_portfolio(&self, name: &str, mode: &str) -> Result<serde_json::Value> {
        self.portfolio_action(name, mode, "start").await
    }

    pub async fn pause_portfolio(&self, name: &str, mode: &str) -> Result<serde_json::Value> {
        self.portfolio_action(name, mode, "pause").await
    }

    pub async fn resume_portfolio(&self, name: &str, mode: &str) -> Result<serde_json::Value> {
        self.portfolio_action(name, mode, "resume").await
    }

    pub async fn flatten_stop_portfolio(
        &self,
        name: &str,
        mode: &str,
    ) -> Result<serde_json::Value> {
        self.portfolio_action(name, mode, "flatten-stop").await
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

    pub async fn node_kill(&self, node_type: &str, level: u8) -> Result<serde_json::Value> {
        let resp = self
            .client
            .post(format!("{}/api/node/kill", self.base_url))
            .json(&serde_json::json!({"mode": node_type, "level": level}))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn lifecycle_command(
        &self,
        action: &str,
        mode: &str,
        strategy_id: Option<&str>,
    ) -> Result<serde_json::Value> {
        let mut body = serde_json::json!({
            "action": action,
            "mode": mode,
        });
        if let Some(sid) = strategy_id {
            body["strategy_id"] = serde_json::Value::String(sid.to_string());
        }
        let resp = self
            .client
            .post(format!("{}/api/node/lifecycle", self.base_url))
            .json(&body)
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    pub async fn lifecycle_state(&self, mode: &str) -> Result<serde_json::Value> {
        let resp = self
            .client
            .get(format!(
                "{}/api/node/lifecycle/state?mode={}",
                self.base_url,
                Self::url_encode(mode)
            ))
            .send()
            .await
            .context(Self::connect_hint(&self.base_url))?;
        let body = resp.error_for_status()?.json().await?;
        Ok(body)
    }

    /// Minimal percent-encoding for query parameter values.
    fn url_encode(s: &str) -> String {
        s.replace('%', "%25")
            .replace('&', "%26")
            .replace('=', "%3D")
            .replace('+', "%2B")
            .replace('#', "%23")
            .replace(' ', "%20")
    }

    fn connect_hint(url: &str) -> String {
        format!(
            "Cannot connect to API at {}\nHint: is the server running? Try: docker compose up -d",
            url
        )
    }
}

#[cfg(test)]
mod tests {
    use super::ApiClient;

    #[test]
    fn same_origin_check_does_not_prefix_match_ports() {
        let client = ApiClient::new("http://localhost:8000", Some("secret".to_string())).unwrap();
        assert!(client.is_same_origin("http://localhost:8000/api/node/status"));
        assert!(!client.is_same_origin("http://localhost:80001/api/node/status"));
        assert!(!client.is_same_origin("https://localhost:8000/api/node/status"));
        assert!(!client.is_same_origin("http://example.com/api/node/status"));
    }
}
