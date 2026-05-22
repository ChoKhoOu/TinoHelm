use assert_cmd::Command;
use predicates::prelude::*;
use serde_json::json;
use wiremock::matchers::{body_partial_json, method, path, query_param};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn tino() -> Command {
    Command::cargo_bin("tino").unwrap()
}

#[test]
fn data_batch_help_exists_and_jobs_subcommand_is_gone() {
    tino()
        .args(["data", "batch", "list", "--help"])
        .assert()
        .success()
        .stdout(predicate::str::contains("--page-size"))
        .stdout(predicate::str::contains("--status"));

    tino()
        .args(["data", "jobs", "list"])
        .assert()
        .failure()
        .stderr(predicate::str::contains("unrecognized subcommand"));
}

#[tokio::test]
async fn data_batch_list_fetches_all_pages_and_prints_flat_json_array() {
    let server = MockServer::start().await;

    Mock::given(method("GET"))
        .and(path("/api/data/batches"))
        .and(query_param("page", "1"))
        .and(query_param("page_size", "2"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "batches": [
                {
                    "batch_id": "batch-2",
                    "data_type": "bar",
                    "asset_class": "um",
                    "symbols": ["BTCUSDT-PERP"],
                    "intervals": ["1m"],
                    "start_date": "2026-01-02",
                    "end_date": "2026-01-02",
                    "status": "running",
                    "progress": 50,
                    "counts": {"jobs": 1, "queued": 0, "running": 1, "completed": 0, "partial_completed": 0, "failed": 0, "cancelled": 0},
                    "created_at": "2026-01-02T09:00:00Z",
                    "started_at": "2026-01-02T09:01:00Z",
                    "completed_at": null
                },
                {
                    "batch_id": "batch-1",
                    "data_type": "bar",
                    "asset_class": "um",
                    "symbols": ["ETHUSDT-PERP"],
                    "intervals": ["5m"],
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-01",
                    "status": "completed",
                    "progress": 100,
                    "counts": {"jobs": 1, "queued": 0, "running": 0, "completed": 1, "partial_completed": 0, "failed": 0, "cancelled": 0},
                    "created_at": "2026-01-01T09:00:00Z",
                    "started_at": "2026-01-01T09:01:00Z",
                    "completed_at": "2026-01-01T09:02:00Z"
                }
            ],
            "total": 3,
            "page": 1,
            "page_size": 2
        })))
        .expect(1)
        .mount(&server)
        .await;

    Mock::given(method("GET"))
        .and(path("/api/data/batches"))
        .and(query_param("page", "2"))
        .and(query_param("page_size", "2"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "batches": [
                {
                    "batch_id": "batch-0",
                    "data_type": "trade_tick",
                    "asset_class": "um",
                    "symbols": ["SOLUSDT-PERP"],
                    "intervals": [],
                    "start_date": "2025-12-31",
                    "end_date": "2025-12-31",
                    "status": "failed",
                    "progress": 100,
                    "counts": {"jobs": 1, "queued": 0, "running": 0, "completed": 0, "partial_completed": 0, "failed": 1, "cancelled": 0},
                    "created_at": "2025-12-31T09:00:00Z",
                    "started_at": "2025-12-31T09:01:00Z",
                    "completed_at": "2025-12-31T09:02:00Z"
                }
            ],
            "total": 3,
            "page": 2,
            "page_size": 2
        })))
        .expect(1)
        .mount(&server)
        .await;

    let output = tino()
        .env("TINO_API_URL", server.uri())
        .env("TINO_API_KEY", "test")
        .args(["data", "batch", "list", "--page-size", "2"])
        .output()
        .unwrap();

    assert!(output.status.success());
    let body: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    let batches = body.as_array().expect("list output should be a flat array");
    assert_eq!(batches.len(), 3);
    assert_eq!(batches[0]["batch_id"], "batch-2");
    assert_eq!(batches[2]["batch_id"], "batch-0");
}

#[tokio::test]
async fn data_batch_get_returns_summary_and_jobs() {
    let server = MockServer::start().await;

    Mock::given(method("GET"))
        .and(path("/api/data/batches/batch-1"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "batch_id": "batch-1",
            "data_type": "bar",
            "asset_class": "um",
            "symbols": ["BTCUSDT-PERP"],
            "intervals": ["1m"],
            "start_date": "2026-01-01",
            "end_date": "2026-01-01",
            "status": "completed",
            "progress": 100,
            "counts": {"jobs": 1, "queued": 0, "running": 0, "completed": 1, "partial_completed": 0, "failed": 0, "cancelled": 0},
            "created_at": "2026-01-01T09:00:00Z",
            "started_at": "2026-01-01T09:01:00Z",
            "completed_at": "2026-01-01T09:02:00Z",
            "jobs": [
                {
                    "job_id": "job-1",
                    "symbol": "BTCUSDT-PERP",
                    "data_type": "bar",
                    "interval": "1m",
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-01",
                    "status": "completed",
                    "progress": 100,
                    "message": "done",
                    "error": null,
                    "created_at": "2026-01-01T09:00:00Z",
                    "started_at": "2026-01-01T09:01:00Z",
                    "completed_at": "2026-01-01T09:02:00Z"
                }
            ]
        })))
        .mount(&server)
        .await;

    let output = tino()
        .env("TINO_API_URL", server.uri())
        .env("TINO_API_KEY", "test")
        .args(["data", "batch", "get", "batch-1"])
        .output()
        .unwrap();

    assert!(output.status.success());
    let body: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(body["batch_id"], "batch-1");
    assert_eq!(body["jobs"][0]["job_id"], "job-1");
}

#[tokio::test]
async fn data_batch_cancel_hits_batch_endpoint() {
    let server = MockServer::start().await;

    Mock::given(method("POST"))
        .and(path("/api/data/batches/batch-1/cancel"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "batch_id": "batch-1",
            "status": "cancellation_requested",
            "cancelled_jobs": 2,
            "running_jobs": 1,
            "finished_jobs": 4
        })))
        .mount(&server)
        .await;

    let output = tino()
        .env("TINO_API_URL", server.uri())
        .env("TINO_API_KEY", "test")
        .args(["data", "batch", "cancel", "batch-1"])
        .output()
        .unwrap();

    assert!(output.status.success());
    let body: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(body["batch_id"], "batch-1");
    assert_eq!(body["cancelled_jobs"], 2);
}

#[tokio::test]
async fn backtest_estimate_sends_symbols_array_without_interval() {
    let server = MockServer::start().await;

    Mock::given(method("POST"))
        .and(path("/api/backtest/estimate"))
        .and(body_partial_json(json!({
            "strategy": "trend_pullback_v3",
            "symbols": ["BTCUSDT-PERP"],
            "start_date": "2026-01-01",
            "end_date": "2026-01-02"
        })))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "total_bars": 1440,
            "estimated_seconds": 1,
            "estimated_label": "~1s"
        })))
        .expect(1)
        .mount(&server)
        .await;

    let output = tino()
        .env("TINO_API_URL", server.uri())
        .env("TINO_API_KEY", "test")
        .args([
            "backtest",
            "estimate",
            "trend_pullback_v3",
            "--symbol",
            "BTCUSDT-PERP",
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-02",
        ])
        .output()
        .unwrap();

    assert!(output.status.success());
    let body: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(body["total_bars"], 1440);
    assert_eq!(body["estimated_label"], "~1s");

    let requests = server.received_requests().await.unwrap();
    assert_eq!(requests.len(), 1);
    let sent: serde_json::Value = serde_json::from_slice(&requests[0].body).unwrap();
    assert_eq!(sent["symbols"], json!(["BTCUSDT-PERP"]));
    assert!(sent.get("interval").is_none());
    assert!(sent.get("symbol").is_none());
}
