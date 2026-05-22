use assert_cmd::Command;
use predicates::prelude::*;

fn tino() -> Command {
    Command::cargo_bin("tino").unwrap()
}

#[test]
fn factor_explore_rejects_flags_with_body() {
    tino()
        .args([
            "factor",
            "explore",
            "--factor",
            "RSI",
            "--body",
            r#"{"factor_name":"RSI"}"#,
        ])
        .assert()
        .failure()
        .stderr(
            predicate::str::contains("cannot be used with")
                .or(predicate::str::contains("mutually exclusive")),
        );
}

#[test]
fn factor_explore_help_shows_typed_flags() {
    tino()
        .args(["factor", "explore", "--help"])
        .assert()
        .success()
        .stdout(predicate::str::contains("--factor"))
        .stdout(predicate::str::contains("--universe"))
        .stdout(predicate::str::contains("--start"))
        .stdout(predicate::str::contains("--end"));
}
