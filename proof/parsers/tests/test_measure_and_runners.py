"""Process measurement and the shape of each arm's command. Nothing here needs a parser."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from parsers_bench import arms, measure, runners
from parsers_bench.arms import ARMS, NOT_RUN


def test_a_clean_exit_is_ok_and_its_output_goes_to_the_log(tmp_path: Path) -> None:
    log = tmp_path / "tool.log"
    result = measure.run_measured(
        [sys.executable, "-c", "print('hello')"], env={}, log_path=log, timeout_seconds=30
    )
    assert result.status == "ok"
    assert result.exit_code == 0
    assert log.read_text().strip() == "hello"
    assert result.wall_seconds > 0
    assert result.max_rss_kb > 1000


def test_a_nonzero_exit_is_crashed(tmp_path: Path) -> None:
    result = measure.run_measured(
        [sys.executable, "-c", "raise SystemExit(3)"],
        env={},
        log_path=tmp_path / "l",
        timeout_seconds=30,
    )
    assert (result.status, result.exit_code) == ("crashed", 3)


def test_a_run_past_the_limit_is_killed_and_reported_as_timeout(tmp_path: Path) -> None:
    result = measure.run_measured(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        env={},
        log_path=tmp_path / "l",
        timeout_seconds=0.5,
    )
    assert result.status == "timeout"
    assert result.exit_code is None
    assert result.wall_seconds < 10


def test_peak_memory_follows_what_the_process_allocates(tmp_path: Path) -> None:
    code = "x = bytearray(150 * 1024 * 1024); x[::4096] = b'a' * len(x[::4096])"
    result = measure.run_measured(
        [sys.executable, "-c", code], env={}, log_path=tmp_path / "l", timeout_seconds=30
    )
    assert result.max_rss_kb > 140 * 1024


def test_host_info_names_the_cpu() -> None:
    info = measure.host_info()
    assert info["logical_cpus"]
    assert info["cpu_model"]


def test_every_arm_has_a_pinned_lock_and_an_existing_project() -> None:
    for arm in ARMS.values():
        assert (arm.project_dir / "pyproject.toml").is_file(), arm.id
        assert (arm.project_dir / "uv.lock").is_file(), arm.id
        text = (arm.project_dir / "pyproject.toml").read_text()
        # The tool itself is pinned with ==, so a lock refresh cannot move it.
        assert "==" in text, arm.id


def test_pinned_versions_in_the_projects_match_the_arm_names() -> None:
    docling = (arms.ARMS_DIR / "docling" / "pyproject.toml").read_text()
    assert '"docling==2.138.0"' in docling
    assert "2.138.0" in ARMS["docling"].name
    assert '"mineru==4.0.12"' in (arms.ARMS_DIR / "mineru" / "pyproject.toml").read_text()
    assert "4.0.12" in ARMS["mineru-basic"].name
    assert '"pymupdf4llm==1.28.2"' in (arms.ARMS_DIR / "pymupdf4llm" / "pyproject.toml").read_text()
    assert "1.28.2" in ARMS["pymupdf4llm"].name


@pytest.mark.parametrize("arm_id", sorted(ARMS))
def test_commands_use_the_arms_own_environment_and_write_into_native(arm_id: str) -> None:
    arm = ARMS[arm_id]
    native = Path("/scratch/native")
    argv, env = runners.command(arm, Path("/in/page.pdf"), native)
    assert argv[0].startswith(str(arm.env_dir / "bin"))
    assert any(str(native) in part for part in argv)
    assert "/in/page.pdf" in argv
    assert all(isinstance(v, str) for v in env.values())


def test_docling_formula_arm_differs_from_default_by_one_switch() -> None:
    plain, _ = runners.command(ARMS["docling"], Path("/in/p.pdf"), Path("/n"))
    formula, _ = runners.command(ARMS["docling-formula"], Path("/in/p.pdf"), Path("/n"))
    assert [a for a in formula if a not in plain] == ["--enrich-formula"]


def test_mineru_runs_the_basic_tier_with_its_models_in_the_home_dir() -> None:
    argv, env = runners.command(ARMS["mineru-basic"], Path("/in/p.pdf"), Path("/n"))
    assert argv[argv.index("--tier") + 1] == "basic"
    assert argv[argv.index("--format") + 1] == "zip"
    assert env["MINERU_HOME"] == str(arms.mineru_home())


def test_native_output_names() -> None:
    native = Path("/n")
    assert runners.native_output(ARMS["docling"], native, "x") == native / "x.json"
    assert runners.native_output(ARMS["docling-formula"], native, "x") == native / "x.json"
    assert runners.native_output(ARMS["mineru-basic"], native, "x") == native / "x.zip"
    assert runners.native_output(ARMS["pymupdf4llm"], native, "x") == native / "x.md"


def test_arms_that_do_not_run_say_why_and_have_no_runner() -> None:
    assert set(NOT_RUN) == {"marker", "nougat", "olmocr", "mathpix"}
    for name, info in NOT_RUN.items():
        assert info["reason"], name
        assert name not in ARMS
    with pytest.raises(SystemExit, match="not run"):
        arms.get("marker")
    with pytest.raises(SystemExit, match="unknown arm"):
        arms.get("nope")


def test_a_missing_environment_stops_before_anything_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PARSERS_ENVS", str(tmp_path / "no-envs"))
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(SystemExit, match="environment is missing"):
        runners.run_arm(ARMS["pymupdf4llm"], pdf, tmp_path / "run", section="s")
