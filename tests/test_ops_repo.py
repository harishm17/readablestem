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
PINNED = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d+(\.\d+)*$")


def workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def test_expected_workflows_exist():
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "eval-live.yml", "uptime.yml"}


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
        assert "permissions" not in job or job["permissions"] == {"contents": "read"}, name


def test_ci_runs_type_checks_and_the_marked_suites():
    jobs = workflow(ROOT / ".github" / "workflows" / "ci.yml")["jobs"]
    assert {"lint-test", "secret-scan", "postgres", "browser", "docker", "eval-gate"} <= set(jobs)
    runs = "\n".join(s.get("run", "") for job in jobs.values() for s in job["steps"])
    for command in (
        "uv run ty check src",
        "uv run pytest -m postgres",
        "uv run pytest -m browser",
        "uv run playwright install --with-deps chromium",
        "docker build",
    ):
        assert command in runs
    assert jobs["lint-test"]["strategy"]["matrix"]["python-version"] == ["3.12", "3.14"]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_expressions_inside_run_scripts(path):
    """Inputs and variables reach scripts through `env:`; `${{ }}` pasted into a script
    is a template-injection hole."""
    for job in workflow(path)["jobs"].values():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), step.get("name")


def test_live_eval_is_manual_and_capped():
    data = workflow(ROOT / ".github" / "workflows" / "eval-live.yml")
    triggers = data[True] if True in data else data["on"]  # YAML 1.1 reads `on` as True
    assert set(triggers) == {"workflow_dispatch"}
    runs = [s.get("run", "") for s in data["jobs"]["equations"]["steps"]]
    assert any('--task equations --max-usd "$MAX_USD"' in r for r in runs)
    assert any("v <= 10" in r for r in runs)


def test_dependabot_covers_dependencies_actions_and_images():
    data = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text())
    ecosystems = {u["package-ecosystem"]: u for u in data["updates"]}
    assert {"uv", "github-actions"} <= set(ecosystems)
    for update in ecosystems.values():
        assert update["schedule"]["interval"] == "weekly"
        groups = update["groups"].values()
        assert any(set(g["update-types"]) == {"minor", "patch"} for g in groups)


def test_dockerfile_builds_a_locked_non_root_image():
    text = (ROOT / "Dockerfile").read_text()
    for line in (ln for ln in text.splitlines() if ln.startswith("FROM ")):
        assert re.search(r"@sha256:[0-9a-f]{64}", line), line
    assert "ghcr.io/astral-sh/uv:" in text
    assert text.count("uv sync --locked --no-dev --no-editable") == 2
    assert "--group" not in text and "--all-groups" not in text  # no browser group
    assert "USER 10001:10001" in text
    assert 'CMD ["a11y-stem", "serve", "--host", "0.0.0.0", "--port", "8080"]' in text
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
    ["docs/deploy.md", "docs/operations.md", "deploy/deploy.sh", "Dockerfile"],
)
def test_ops_files_never_claim_compliance(path):
    text = (ROOT / path).read_text().lower()
    assert "compliant" not in text
    assert "ensures compliance" not in text
