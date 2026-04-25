"""Architectural invariant static guard tests.

These tests use grep to enforce two invariants:

1. NT BacktestEngine(...) may only be instantiated inside runner.py / runner_cli.py.
   API, consumer, and core code must NOT create BacktestEngine directly.

2. No code may import from deleted modules:
   process_manager / watchdog / backtest.worker.

Additionally, a frontend consumer-point audit ensures the removed
`backtest_workers` / `backtestWorkers` shape key is not referenced in
any .tsx / .ts frontend file.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_no_backtest_engine_in_api_or_consumer():
    """Architectural invariant:
    One BacktestEngine instance == one fresh Python subprocess.
    API / consumer / core code must NOT instantiate BacktestEngine directly.
    """
    forbidden = [
        str(ROOT / "src/tinohelm/api"),
        str(ROOT / "src/tinohelm/backtest/consumer.py"),
        str(ROOT / "src/tinohelm/core"),
    ]
    result = subprocess.run(
        ["grep", "-rn", "BacktestEngine(", *forbidden],
        capture_output=True,
        text=True,
    )
    # grep returns 1 when no matches, 0 when matches found
    assert result.returncode != 0, (
        f"Architectural invariant violated:\n{result.stdout}\n"
        "BacktestEngine(...) may only appear in runner.py / runner_cli.py."
    )


def test_no_dangling_imports_of_deleted_modules():
    """No code may import from deleted modules: process_manager / watchdog / backtest.worker."""
    result = subprocess.run(
        [
            "grep",
            "-rn",
            r"from tinohelm.core.process_manager\|"
            r"from tinohelm.core.watchdog\|"
            r"from tinohelm.backtest.worker\b\|"
            r"import watchdog\b\|"
            r"backtest_worker\b",
            "src/",
            "tests/",
            "--include=*.py",
            # Exclude this guard file itself — it contains the pattern strings
            # as grep arguments, which would otherwise self-match.
            "--exclude=test_subprocess_invariant.py",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        f"Dangling imports to deleted modules:\n{result.stdout}"
    )


def test_frontend_no_backtest_workers_reference():
    """Frontend must not consume the removed `backtest_workers` / `backtestWorkers` shape key."""
    web_dir = ROOT / "src/web"
    if not web_dir.exists():
        # frontend not present in this checkout — skip
        import pytest

        pytest.skip("src/web/ not present")
    result = subprocess.run(
        [
            "grep",
            "-rn",
            r"backtest_workers\|backtestWorkers",
            str(web_dir),
            "--include=*.tsx",
            "--include=*.ts",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        f"Frontend references removed shape key:\n{result.stdout}\n"
        "Either re-add `backtest_workers: []` to NodeController.get_status() "
        "or remove the frontend consumer."
    )
