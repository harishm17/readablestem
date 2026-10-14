"""`readablestem proof`: arguments, exit codes, and what it prints. Offline."""

from __future__ import annotations

import pytest

from proof_support import SET_ID, make_project
from readablestem.cli import main as cli_main
from readablestem.proof import engine
from readablestem.proof.errors import BundleError, Drift, PinMismatch, ReplayMiss


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = make_project(tmp_path)
    (root / "pyproject.toml").write_text('[project]\nname = "readablestem"\n')
    monkeypatch.chdir(root)
    return root


def test_list_names_the_sets_and_marks_the_default(project, capsys):
    assert cli_main.main(["proof", "--list"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"{SET_ID} (default): development, 2 sections, Synthetic set")


def test_a_missing_recording_is_a_clear_error_with_exit_code_3(project, capsys):
    assert cli_main.main(["proof"]) == 3
    err = capsys.readouterr().err
    assert err.startswith("error: no recorded run at")
    assert "proof --live --max-usd" in err


def test_check_reports_each_pin_and_runs_nothing(project, capsys):
    assert cli_main.main(["proof", "--check"]) == 0
    out = capsys.readouterr().out
    assert "absent   recorded run" in out
    assert "ok       gold manifest" in out
    assert not (project / "PROOF.md").exists()


def test_an_unknown_set_is_exit_code_2(project, capsys):
    assert cli_main.main(["proof", "--set", "nope"]) == 2
    assert "no proof set 'nope'" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["proof", "--max-usd", "0"],
        ["proof", "--max-usd", "-1"],
        ["proof", "--max-usd", "inf"],
        ["proof", "--max-usd", "abc"],
        ["proof", "--max-calls", "0"],
        ["proof", "--date", "13-10-2026"],
        ["proof", "--bootstrap", "x"],
    ],
)
def test_bad_numbers_and_dates_are_refused_by_the_parser(argv, project, capsys):
    with pytest.raises(SystemExit) as stop:
        cli_main.main(argv)
    assert stop.value.code == 2
    assert "error" in capsys.readouterr().err


def test_a_live_run_without_a_cap_is_exit_code_2(project, capsys):
    assert cli_main.main(["proof", "--live"]) == 2
    assert "--max-usd is required" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (PinMismatch("pin"), 3),
        (BundleError("bundle"), 3),
        (Drift("drift"), 4),
        (ReplayMiss("miss"), 5),
    ],
)
def test_each_failure_has_its_own_exit_code(error, code, project, monkeypatch, capsys):
    def boom(_opts, _say):
        raise error

    monkeypatch.setattr(engine, "run_proof", boom)
    assert cli_main.main(["proof"]) == code
    assert capsys.readouterr().err == f"error: {error}\n"


def test_options_reach_the_engine(project, monkeypatch, capsys):
    seen = {}

    def fake_run(opts, _say):
        seen["opts"] = opts
        return engine.ProofResult(0, project / "PROOF.md", "", [], ["Invented-content rate"])

    monkeypatch.setattr(engine, "run_proof", fake_run)
    argv = ["proof", "--set", SET_ID, "--allow-drift", "--verify-sources", "--require-complete"]
    assert cli_main.main(argv) == 0
    opts = seen["opts"]
    assert opts.set_id == SET_ID and opts.allow_drift and opts.verify_sources
    assert opts.require_complete and not opts.live and opts.max_usd is None
    assert opts.n_boot == 10_000
    out = capsys.readouterr().out
    assert "wrote PROOF.md" in out
    assert "not computed (stub): Invented-content rate" in out


def test_the_exit_code_of_an_incomplete_run_is_passed_on(project, monkeypatch):
    monkeypatch.setattr(
        engine, "run_proof", lambda _o, _s: engine.ProofResult(1, None, "", ["a drift"], [])
    )
    assert cli_main.main(["proof", "--require-complete"]) == 1
