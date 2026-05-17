use assert_cmd::Command;
use predicates::prelude::*;

fn tino() -> Command {
    Command::cargo_bin("tino").unwrap()
}

#[test]
fn version_json_contains_cli_version() {
    let output = tino().arg("version").output().unwrap();
    let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert!(json["cli_version"].is_string());
    assert!(!json["cli_version"].as_str().unwrap().is_empty());
}

#[test]
fn tino_api_subcommand_does_not_exist() {
    tino()
        .args(["api", "call", "GET", "/test"])
        .assert()
        .failure()
        .stderr(predicate::str::contains("unrecognized subcommand"));
}

#[test]
fn help_shows_typed_subcommands() {
    tino()
        .arg("--help")
        .assert()
        .success()
        .stdout(predicate::str::contains("trading"))
        .stdout(predicate::str::contains("backtest"))
        .stdout(predicate::str::contains("node"))
        .stdout(predicate::str::contains("data"));
}

#[test]
fn error_on_stderr_as_json_with_nonzero_exit() {
    // Trying to connect to non-existent API
    tino()
        .env("TINO_API_URL", "http://127.0.0.1:1")
        .env("TINO_API_KEY", "test")
        .args(["backtest", "list"])
        .assert()
        .failure()
        .stderr(predicate::str::contains("error"));
}

#[test]
fn version_defaults_to_json_output() {
    tino()
        .arg("version")
        .assert()
        .success()
        .stdout(predicate::str::starts_with("{"));
}

#[test]
fn version_text_format_is_human_friendly() {
    tino()
        .args(["-f", "text", "version"])
        .assert()
        .success()
        .stdout(predicate::str::contains("tino v"));
}

#[test]
fn llm_format_is_rejected() {
    tino()
        .args(["-f", "llm", "version"])
        .assert()
        .failure()
        .stderr(predicate::str::contains("invalid value 'llm'"));
}
