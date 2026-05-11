from __future__ import annotations

import re
from pathlib import Path

import yaml


_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_python_test_workflow_runs_with_uv_and_tracks_lockfile() -> None:
    workflow = yaml.safe_load(
        (_REPO_ROOT / ".github/workflows/test-python.yml").read_text()
    )

    trigger = workflow.get("on", workflow.get(True))
    push_paths = trigger["push"]["paths"]
    pr_paths = trigger["pull_request"]["paths"]
    assert "uv.lock" in push_paths
    assert "uv.lock" in pr_paths

    unit_steps = workflow["jobs"]["unit"]["steps"]
    integration_steps = workflow["jobs"]["integration"]["steps"]

    for steps in (unit_steps, integration_steps):
        rendered = "\n".join(step.get("run", "") for step in steps)
        uses = [step.get("uses", "") for step in steps]
        assert any("astral-sh/setup-uv@" in item for item in uses)
        assert "uv sync --frozen --extra test" in rendered
        assert "uv run --frozen pytest" in rendered
        assert 'pip install -e ".[test]"' not in rendered


def test_dockerfile_syncs_from_uv_lock_and_runtime_venv() -> None:
    dockerfile = (_REPO_ROOT / "Dockerfile").read_text()

    assert "COPY uv.lock ./" in dockerfile
    assert "UV_PROJECT_ENVIRONMENT=/opt/venv" in dockerfile
    assert "uv sync --frozen --no-install-project --extra optimize" in dockerfile
    assert "uv sync --frozen --no-editable --extra optimize" in dockerfile
    assert re.search(
        r"ghcr\.io/astral-sh/uv:[^\s]+,source=/uv,target=/bin/uv",
        dockerfile,
    )
    assert re.search(
        r"ghcr\.io/astral-sh/uv:[^\s]+,source=/uvx,target=/bin/uvx",
        dockerfile,
    )
    assert "COPY --from=ghcr.io/astral-sh/uv:" not in dockerfile
    assert "pip wheel --wheel-dir=/wheels" not in dockerfile
    assert "pip install --no-cache-dir --no-index /wheels/*" not in dockerfile
