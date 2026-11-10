"""Repository rules for CI, the image and the ops docs, checked on every test run.

These are the properties a reviewer would otherwise have to re-check by hand: actions
pinned to commit SHAs, least-privilege workflow tokens, a non-root image built from the
lock file, and nothing private in the Docker build context.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")  # PyYAML arrives with uvicorn[standard]

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
PINNED = re.compile(r"^[\w.-]+/[\w.-]+(/[\w.-]+)*@[0-9a-f]{40} # v\d+(\.\d+)*$")


def workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_to_commit_shas(path):
    uses = [
        line.strip().removeprefix("- ").removeprefix("uses: ")
        for line in path.read_text().splitlines()
        if line.strip().removeprefix("- ").startswith("uses:")
    ]
    for ref in uses:  # uptime.yml needs none: curl is on the runner
        assert PINNED.match(ref), f"{path.name}: not pinned to a SHA with a tag comment: {ref}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflows_are_least_privilege_on_a_fixed_runner(path):
    data = workflow(path)
    assert data["permissions"] == {"contents": "read"}
    for name, job in data["jobs"].items():
        assert job["runs-on"] == "ubuntu-24.04", name
        allowed = {"contents": "read"}
        if path.name == "codeql.yml":  # code scanning results are uploaded with this scope
            allowed = {"contents": "read", "security-events": "write", "actions": "read"}
        assert "permissions" not in job or job["permissions"] == allowed, name


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_expressions_inside_run_scripts(path):
    """Inputs and variables reach scripts through `env:`; `${{ }}` pasted into a script
    is a template-injection hole."""
    for job in workflow(path)["jobs"].values():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), step.get("name")


def test_live_eval_is_weekly_or_manual_capped_and_skips_without_a_key():
    import json

    data = workflow(ROOT / ".github" / "workflows" / "eval-live.yml")
    triggers = data[True] if True in data else data["on"]  # YAML 1.1 reads `on` as True
    assert set(triggers) == {"schedule", "workflow_dispatch"}  # never on a pull request
    assert len(triggers["schedule"]) == 1
    tasks = json.loads(data["env"]["TASKS"])
    assert {t["task"] for t in tasks} == {"equations", "headings", "figures", "document", "tables"}
    assert all(0 < t["max_usd"] <= 1.0 for t in tasks)  # each task has its own small hard cap
    assert sum(t["max_usd"] for t in tasks) <= float(data["env"]["MAX_WEEKLY_USD"]) <= 3.0
    # Production provider; every task has a baseline except tables (floors alone).
    assert data["env"]["PROVIDER"] == "anthropic"
    assert {t["task"] for t in tasks if not t["baseline"]} == {"tables"}
    plan = "\n".join(s.get("run", "") for s in data["jobs"]["plan"]["steps"])
    assert "ANTHROPIC_API_KEY" in str(data["jobs"]["plan"]["steps"])
    assert "skipped" in plan and "::notice::" in plan  # a scheduled run without the key
    assert data["jobs"]["live"]["if"] == "needs.plan.outputs.run == 'true'"
    runs = "\n".join(s.get("run", "") for s in data["jobs"]["live"]["steps"])
    assert '--max-usd "$MAX_USD"' in runs and "eval gate" in runs and "--require-current" in runs
    uploads = [s for s in data["jobs"]["live"]["steps"] if "upload-artifact" in s.get("uses", "")]
    assert uploads and uploads[0]["if"] == "always()"
    assert "git commit" not in runs and "git push" not in runs  # reports are never committed
    assert data["permissions"] == {"contents": "read"}


def test_dockerfile_builds_a_locked_non_root_image():
    text = (ROOT / "Dockerfile").read_text()
    for line in (ln for ln in text.splitlines() if ln.startswith("FROM ")):
        assert re.search(r"@sha256:[0-9a-f]{64}", line), line
    assert "ghcr.io/astral-sh/uv:" in text
    assert text.count("uv sync --locked --no-dev --no-editable") == 2
    assert "--group" not in text and "--all-groups" not in text  # no browser group
    assert "USER 10001:10001" in text
    assert 'CMD ["readablestem", "serve", "--host", "0.0.0.0", "--port", "8080"]' in text
    assert "EXPOSE 8080" in text


def test_dockerignore_keeps_private_and_local_files_out():
    entries = {
        line.strip().rstrip("/")
        for line in (ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    for required in (".env", ".local", ".cache", "private-inputs", "tests", ".git", ".venv"):
        assert required in entries, required


@pytest.mark.parametrize(
    "path",
    [
        "docs/deploy.md",
        "docs/deploy-railway.md",
        "docs/operations.md",
        "deploy/deploy.sh",
        "Dockerfile",
    ],
)
def test_ops_files_never_claim_compliance(path):
    text = (ROOT / path).read_text().lower()
    assert "compliant" not in text
    assert "ensures compliance" not in text


CI = ROOT / ".github" / "workflows" / "ci.yml"
