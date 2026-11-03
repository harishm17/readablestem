"""Repository rules for CI, the image and the ops docs, checked on every test run.

These are the properties a reviewer would otherwise have to re-check by hand: actions
pinned to commit SHAs, least-privilege workflow tokens, a non-root image built from the
lock file, and nothing private in the Docker build context.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")  # PyYAML arrives with uvicorn[standard]

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
PINNED = re.compile(r"^[\w.-]+/[\w.-]+(/[\w.-]+)*@[0-9a-f]{40} # v\d+(\.\d+)*$")


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
        allowed = {"contents": "read"}
        if path.name == "codeql.yml":  # code scanning results are uploaded with this scope
            allowed = {"contents": "read", "security-events": "write", "actions": "read"}
        assert "permissions" not in job or job["permissions"] == allowed, name


def test_ci_runs_type_checks_and_the_marked_suites():
    jobs = workflow(ROOT / ".github" / "workflows" / "ci.yml")["jobs"]
    assert {
        "static",
        "pytest",
        "shard-coverage",
        "ci-passed",
        "secret-scan",
        "postgres",
        "browser",
        "docker",
        "eval-gate",
    } <= set(jobs)
    runs = "\n".join(s.get("run", "") for job in jobs.values() for s in job["steps"])
    for command in (
        "uv run ty check src",
        "uv run pytest -m postgres",
        "uv run pytest -m browser",
        "uv run playwright install --with-deps chromium",
        "docker build",
    ):
        assert command in runs
    assert jobs["static"]["strategy"]["matrix"]["python-version"] == ["3.12", "3.14"]


def test_ci_installs_what_the_type_check_imports():
    """`ty check src` resolves `playwright.sync_api` (eval/render.py, html_checks.py). Without
    the browser group it reports unresolved imports and the job fails on both legs."""
    jobs = workflow(ROOT / ".github" / "workflows" / "ci.yml")["jobs"]
    for name in ("static", "pytest"):
        install = next(s["run"] for s in jobs[name]["steps"] if s.get("name") == "Install (locked)")
        assert "--locked" in install, name
        assert "--group browser" in install or "--all-groups" in install, name


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


def test_eval_guard_runs_on_pull_requests_and_reads_labels_through_env():
    data = workflow(ROOT / ".github" / "workflows" / "eval-guard.yml")
    triggers = data[True] if True in data else data["on"]
    assert {"labeled", "unlabeled", "synchronize"} <= set(triggers["pull_request"]["types"])
    steps = data["jobs"]["guard"]["steps"]
    assert steps[0]["with"]["fetch-depth"] == 0
    guard = steps[-1]
    assert "scripts/eval_guard.py" in guard["run"] and "${{" not in guard["run"]
    assert "labels" in guard["env"]["LABELS_JSON"]


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
    assert 'CMD ["readablestem", "serve", "--host", "0.0.0.0", "--port", "8080"]' in text
    assert "EXPOSE 8080" in text


def test_dockerfile_builds_without_buildkit():
    """deploy.sh builds with `gcloud builds submit --tag`, a plain `docker build` in Cloud
    Build's docker 20.10 image that may use the legacy builder, which rejects these."""
    lines = [ln.strip() for ln in (ROOT / "Dockerfile").read_text().splitlines()]
    instructions = [ln for ln in lines if ln and not ln.startswith("#")]
    for line in instructions:
        assert "--mount=" not in line, line
        assert not re.match(r"(COPY|ADD)\b.*--(chmod|link|parents|exclude)\b", line), line
        assert "<<" not in line, line  # heredocs
        assert not re.match(r"RUN\s+--(network|security)\b", line), line
    assert not any(ln.lower().startswith("# syntax=") for ln in lines)

    steps = workflow(ROOT / ".github" / "workflows" / "ci.yml")["jobs"]["docker"]["steps"]
    assert any(
        s.get("env", {}).get("DOCKER_BUILDKIT") == "0" and "docker build" in s.get("run", "")
        for s in steps
    )


def test_type_check_gate_is_green():
    """CI's static job fails on any ty diagnostic, warnings included."""
    ty = shutil.which("ty")
    if ty is None:
        pytest.skip("ty is not installed (dev group)")
    result = subprocess.run(
        [ty, "check", "src"], cwd=ROOT, capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


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


def test_ci_does_not_turn_no_tests_collected_into_a_pass_and_audits_the_lock():
    jobs = workflow(ROOT / ".github" / "workflows" / "ci.yml")["jobs"]
    for name in ("postgres", "browser"):
        runs = "\n".join(s.get("run", "") for s in jobs[name]["steps"])
        assert "-eq 5" not in runs and "exit 0" not in runs, name
    pg = "\n".join(s.get("run", "") for s in jobs["postgres"]["steps"])
    assert "alembic upgrade head" in pg and "alembic check" in pg
    assert jobs["postgres"]["services"]["postgres"]["image"] == "postgres:18"
    audit = "\n".join(s.get("run", "") for s in jobs["dependency-audit"]["steps"])
    assert "uv export" in audit and "pip-audit" in audit


CI = ROOT / ".github" / "workflows" / "ci.yml"


def test_pull_request_runs_are_cancelled_when_superseded_but_main_runs_are_not():
    """Every workflow that runs on pull requests and on main cancels only the pull request
    runs, so each main commit keeps its result."""
    for path in WORKFLOWS:
        group = workflow(path).get("concurrency")
        if group is None or path.name == "eval-guard.yml":  # eval-guard is pull-request only
            continue
        cancel = group["cancel-in-progress"]
        assert cancel in (False, "${{ github.event_name == 'pull_request' }}"), path.name
    assert workflow(CI)["concurrency"] == {
        "group": "ci-${{ github.workflow }}-${{ github.ref }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    }


def test_python_314_runs_in_full_on_main_nightly_and_dependency_changes():
    """docs/development.md ("CI") states the rule; the plan step is where it lives."""
    data = workflow(CI)
    assert "schedule" in data[True]  # PyYAML reads the key `on` as True
    assert data[True]["push"]["branches"] == ["main"]
    plan = next(s["run"] for s in data["jobs"]["changes"]["steps"] if s.get("id") == "plan")
    for name in ("pyproject", "uv", ".python-version", "ci"):
        assert name in plan
    assert "full=true" in plan and "full=false" in plan
    assert 'if [ "$EVENT" = "pull_request" ]' in plan  # every other event runs in full
    matrix = data["jobs"]["pytest"]["strategy"]["matrix"]
    assert matrix == "${{ fromJSON(needs.changes.outputs.matrix) }}"


def test_every_shard_runs_and_the_union_is_checked():
    data = workflow(CI)
    run = next(s["run"] for s in data["jobs"]["pytest"]["steps"] if "pytest" in s.get("run", ""))
    assert '--shard "$SHARD"' in run and "--shard-report" in run
    assert (
        data["jobs"]["changes"]["env"]["SHARDS"] == data["jobs"]["shard-coverage"]["env"]["SHARDS"]
    )
    runs = "\n".join(s.get("run", "") for s in data["jobs"]["shard-coverage"]["steps"])
    assert "scripts/check_shards.py" in runs


def test_ci_passed_waits_for_every_other_job():
    jobs = workflow(CI)["jobs"]
    assert set(jobs["ci-passed"]["needs"]) == set(jobs) - {"ci-passed"}


def test_browser_job_caches_what_it_downloads_and_runs_in_parallel():
    steps = workflow(CI)["jobs"]["browser"]["steps"]
    cached = {s["with"]["path"] for s in steps if s.get("uses", "").startswith("actions/cache@")}
    assert {"~/.cache/ms-playwright", ".cache/axe-core"} <= cached
    run = next(s["run"] for s in steps if "pytest -m browser" in s.get("run", ""))
    assert "-n 3" in run and "--dist loadfile" in run and '--shard "$SHARD"' in run
