use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use reqwest::{Client, Method, Url};
use serde::de::DeserializeOwned;

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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApiErrorDetails {
    pub code: Option<String>,
    pub message: String,
}

pub fn extract_api_error_details(body: &serde_json::Value) -> ApiErrorDetails {
    fn str_field<'a>(value: &'a serde_json::Value, name: &str) -> Option<&'a str> {
        value
            .get(name)
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
    }
    fn str_value(value: &serde_json::Value) -> Option<&str> {
        value.as_str().filter(|s| !s.is_empty())
    }

    let mut code = str_field(body, "error_code").map(str::to_string);
    let mut message = str_field(body, "message")
        .map(str::to_string)
        .or_else(|| str_field(body, "error_message").map(str::to_string));

    if let Some(detail) = body.get("detail") {
        if code.is_none() {
            code = str_field(detail, "code")
                .map(str::to_string)
                .or_else(|| str_field(detail, "error_code").map(str::to_string));
        }
        if message.is_none() {
            message = str_value(detail)
                .map(str::to_string)
                .or_else(|| str_field(detail, "message").map(str::to_string))
                .or_else(|| str_field(detail, "error_message").map(str::to_string));
        }
    }

    if let Some(error) = body.get("error") {
        if code.is_none() {
            code = str_field(error, "code")
                .map(str::to_string)
                .or_else(|| str_field(error, "error_code").map(str::to_string));
        }
        if message.is_none() {
            message = str_value(error)
                .map(str::to_string)
                .or_else(|| str_field(error, "message").map(str::to_string))
                .or_else(|| str_field(error, "error_message").map(str::to_string));
        }
    }

    ApiErrorDetails {
        code,
        message: message.unwrap_or_else(|| body.to_string()),
    }
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
                    .context("API key contains invalid HTTP header characters")?,
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

    async fn typed_json<T: DeserializeOwned>(
        &self,
        method: Method,
        path: &str,
        query: &[(String, String)],
        body: Option<serde_json::Value>,
    ) -> Result<T> {
        let response = self.request_json(method, path, query, body, &[]).await?;
        serde_json::from_value(response.body)
            .with_context(|| format!("Cannot decode API response from {path}"))
    }

    fn json_body<T: serde::Serialize>(value: &T) -> Result<serde_json::Value> {
        serde_json::to_value(value).context("Cannot serialize API request body")
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
        self.typed_json(Method::GET, "/api/backtest/runs", &[], None)
            .await
    }

    pub async fn get_status(&self, run_id: &str) -> Result<BacktestRunStatus> {
        self.typed_json(
            Method::GET,
            &format!("/api/backtest/{}/status", run_id),
            &[],
            None,
        )
        .await
    }

    pub async fn get_result(&self, run_id: &str) -> Result<serde_json::Value> {
        self.typed_json(
            Method::GET,
            &format!("/api/backtest/{}/result", run_id),
            &[],
            None,
        )
        .await
    }

    pub async fn run_backtest(&self, req: &BacktestRunRequest) -> Result<BacktestRunResponse> {
        self.typed_json(
            Method::POST,
            "/api/backtest/run",
            &[],
            Some(Self::json_body(req)?),
        )
        .await
    }

    pub async fn cancel_backtest(&self, run_id: &str) -> Result<BacktestCancelResponse> {
        self.typed_json(
            Method::POST,
            &format!("/api/backtest/{}/cancel", run_id),
            &[],
            None,
        )
        .await
    }

    pub async fn delete_backtest(&self, run_id: &str) -> Result<serde_json::Value> {
        self.typed_json(
            Method::DELETE,
            &format!("/api/backtest/{}", run_id),
            &[],
            None,
        )
        .await
    }

    // ---- Optimization ----

    pub async fn optimize_backtest(&self, req: &OptimizeRequest) -> Result<OptimizeResponse> {
        self.typed_json(
            Method::POST,
            "/api/backtest/optimize",
            &[],
            Some(Self::json_body(req)?),
        )
        .await
    }

    pub async fn optimize_status(&self, opt_id: u64) -> Result<OptimizeStatus> {
        self.typed_json(
            Method::GET,
            &format!("/api/backtest/optimize/{}/status", opt_id),
            &[],
            None,
        )
        .await
    }

    pub async fn optimize_result(&self, opt_id: u64) -> Result<serde_json::Value> {
        self.typed_json(
            Method::GET,
            &format!("/api/backtest/optimize/{}/result", opt_id),
            &[],
            None,
        )
        .await
    }

    pub async fn optimize_list(
        &self,
        limit: u32,
        strategy: Option<&str>,
    ) -> Result<serde_json::Value> {
        let mut query = vec![("limit".to_string(), limit.to_string())];
        if let Some(strategy) = strategy {
            query.push(("strategy".to_string(), strategy.to_string()));
        }
        self.typed_json(Method::GET, "/api/backtest/optimize/runs", &query, None)
            .await
    }

    // ---- Strategy ----

    pub async fn list_strategies(&self) -> Result<Vec<Strategy>> {
        self.typed_json(Method::GET, "/api/strategies", &[], None)
            .await
    }

    pub async fn get_strategy(&self, name: &str) -> Result<Strategy> {
        self.typed_json(Method::GET, &format!("/api/strategies/{}", name), &[], None)
            .await
    }

    pub async fn get_strategy_params(&self, name: &str) -> Result<serde_json::Value> {
        self.typed_json(
            Method::GET,
            &format!("/api/strategies/{}/params", name),
            &[],
            None,
        )
        .await
    }

    pub async fn validate_strategy(&self, name: &str) -> Result<ValidateResult> {
        let path = format!("/api/strategies/{}/validate", name);
        match self
            .typed_json::<ValidateResult>(Method::POST, &path, &[], None)
            .await
        {
            Ok(result) => Ok(result),
            Err(err) => {
                if let Some(http) = err.downcast_ref::<ApiHttpError>() {
                    if http.status_code == 422 {
                        if let Some(detail) = http.body.get("detail") {
                            return serde_json::from_value(detail.clone())
                                .context("Cannot decode validation detail response");
                        }
                    }
                }
                Err(err)
            }
        }
    }

    pub async fn rescan_strategies(&self) -> Result<RescanResult> {
        self.typed_json(Method::POST, "/api/strategies/rescan", &[], None)
            .await
    }

    pub async fn create_strategy(
        &self,
        req: &StrategyCreateRequest,
    ) -> Result<StrategyCreateResponse> {
        self.typed_json(
            Method::POST,
            "/api/strategies/create",
            &[],
            Some(Self::json_body(req)?),
        )
        .await
    }

    // ---- Data ----

    pub async fn fetch_data(&self, req: &DataFetchRequest) -> Result<serde_json::Value> {
        let payload = serde_json::json!({
            "symbols": [req.symbol.clone()],
            "intervals": [req.interval.clone()],
            "start": req.start,
            "end": req.end,
            "data_type": req.data_type,
            "asset_class": req.asset_class,
        });
        self.typed_json(Method::POST, "/api/data/fetch-batch", &[], Some(payload))
            .await
    }

    pub async fn list_data(&self) -> Result<serde_json::Value> {
        self.typed_json(Method::GET, "/api/data/catalog", &[], None)
            .await
    }

    pub async fn fetch_data_batch(&self, req: &DataFetchBatchRequest) -> Result<serde_json::Value> {
        self.typed_json(
            Method::POST,
            "/api/data/fetch-batch",
            &[],
            Some(Self::json_body(req)?),
        )
        .await
    }

    pub async fn compact_data(&self, req: &DataCompactRequest) -> Result<serde_json::Value> {
        self.typed_json(
            Method::POST,
            "/api/data/consolidate",
            &[],
            Some(Self::json_body(req)?),
        )
        .await
    }

    pub async fn validate_data(
        &self,
        symbol: &str,
        interval: &str,
        data_type: &str,
    ) -> Result<serde_json::Value> {
        let query = vec![("data_type".to_string(), data_type.to_string())];
        self.typed_json(
            Method::GET,
            &format!("/api/data/validate/{}/{}", symbol, interval),
            &query,
            None,
        )
        .await
    }

    pub async fn scan_data(&self) -> Result<serde_json::Value> {
        Err(anyhow::anyhow!(
            "/api/data/scan 已删除；请改用 /api/data/catalog 或 NT maintenance APIs"
        ))
    }

    // ---- Trading ----

    pub async fn list_positions(
        &self,
        node_type: Option<&str>,
        is_open: Option<bool>,
        strategy_id_tag: Option<&str>,
    ) -> Result<Vec<TradingPosition>> {
        let mut query = Vec::new();
        if let Some(node_type) = node_type {
            query.push(("node_type".to_string(), node_type.to_string()));
        }
        if let Some(is_open) = is_open {
            query.push(("is_open".to_string(), is_open.to_string()));
        }
        if let Some(strategy_id_tag) = strategy_id_tag {
            query.push(("strategy_id_tag".to_string(), strategy_id_tag.to_string()));
        }
        self.typed_json(Method::GET, "/api/trading/positions", &query, None)
            .await
    }

    pub async fn list_fills(
        &self,
        node_type: Option<&str>,
        limit: u32,
        strategy_id_tag: Option<&str>,
    ) -> Result<Vec<TradingFill>> {
        let mut query = vec![("limit".to_string(), limit.to_string())];
        if let Some(node_type) = node_type {
            query.push(("node_type".to_string(), node_type.to_string()));
        }
        if let Some(strategy_id_tag) = strategy_id_tag {
            query.push(("strategy_id_tag".to_string(), strategy_id_tag.to_string()));
        }
        self.typed_json(Method::GET, "/api/trading/fills", &query, None)
            .await
    }

    pub async fn trading_summary(&self, node_type: &str) -> Result<TradingSummary> {
        let query = [("node_type".to_string(), node_type.to_string())];
        self.typed_json(Method::GET, "/api/trading/summary", &query, None)
            .await
    }

    // ---- Orders ----

    pub async fn list_orders(
        &self,
        node_type: Option<&str>,
        status: Option<&str>,
        limit: u32,
    ) -> Result<Vec<TradingOrder>> {
        let mut query = vec![("limit".to_string(), limit.to_string())];
        if let Some(node_type) = node_type {
            query.push(("node_type".to_string(), node_type.to_string()));
        }
        if let Some(status) = status {
            query.push(("status".to_string(), status.to_string()));
        }
        self.typed_json(Method::GET, "/api/orders", &query, None)
            .await
    }

    // ---- Node Strategies ----

    pub async fn list_node_strategies(&self, mode: &str) -> Result<NodeStrategiesResponse> {
        let query = [("mode".to_string(), mode.to_string())];
        self.typed_json(Method::GET, "/api/node/strategies", &query, None)
            .await
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
        self.typed_json(
            Method::POST,
            &format!("/api/node/strategy/{}", action),
            &[],
            Some(Self::json_body(&body)?),
        )
        .await
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
        self.typed_json(Method::GET, "/api/node/status", &[], None)
            .await
    }

    pub async fn node_kill(
        &self,
        node_type: &str,
        level: u8,
        strategy_id: Option<&str>,
    ) -> Result<serde_json::Value> {
        let mut body = serde_json::json!({"mode": node_type, "level": level});
        if let Some(strategy_id) = strategy_id {
            body["strategy_id"] = serde_json::json!(strategy_id);
        }
        self.typed_json(Method::POST, "/api/node/kill", &[], Some(body))
            .await
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
        if let Some(strategy_id) = strategy_id {
            body["strategy_id"] = serde_json::Value::String(strategy_id.to_string());
        }
        self.typed_json(Method::POST, "/api/node/lifecycle", &[], Some(body))
            .await
    }

    pub async fn lifecycle_state(&self, mode: &str) -> Result<serde_json::Value> {
        let query = [("mode".to_string(), mode.to_string())];
        self.typed_json(Method::GET, "/api/node/lifecycle/state", &query, None)
            .await
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
    use super::{extract_api_error_details, ApiClient};
    use serde_json::json;

    #[test]
    fn same_origin_check_does_not_prefix_match_ports() {
        let client = ApiClient::new("http://localhost:8000", Some("secret".to_string())).unwrap();
        assert!(client.is_same_origin("http://localhost:8000/api/node/status"));
        assert!(!client.is_same_origin("http://localhost:80001/api/node/status"));
        assert!(!client.is_same_origin("https://localhost:8000/api/node/status"));
        assert!(!client.is_same_origin("http://example.com/api/node/status"));
    }

    #[test]
    fn extracts_fastapi_detail_code_and_message() {
        let details = extract_api_error_details(&json!({
            "detail": {"code": "invalid_request", "message": "Bad input"}
        }));
        assert_eq!(details.code.as_deref(), Some("invalid_request"));
        assert_eq!(details.message, "Bad input");
    }

    #[test]
    fn extracts_envelope_error_code_and_message() {
        let details = extract_api_error_details(&json!({
            "error": {"code": "factor_failed", "message": "Factor failed"}
        }));
        assert_eq!(details.code.as_deref(), Some("factor_failed"));
        assert_eq!(details.message, "Factor failed");
    }

    #[test]
    fn extracts_top_level_error_code() {
        let details = extract_api_error_details(&json!({
            "error_code": "not_found",
            "message": "Missing resource"
        }));
        assert_eq!(details.code.as_deref(), Some("not_found"));
        assert_eq!(details.message, "Missing resource");
    }

    #[test]
    fn falls_back_to_full_body_as_message() {
        let body = json!({"detail": [{"loc": ["body", "x"], "msg": "required"}]});
        let details = extract_api_error_details(&body);
        assert_eq!(details.code, None);
        assert_eq!(details.message, body.to_string());
    }
}
