"""`readablestem proof`: arguments, exit codes, and what it prints. Offline."""

from __future__ import annotations

import pytest

from proof_support import make_project
from readablestem.cli import main as cli_main


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = make_project(tmp_path)
    (root / "pyproject.toml").write_text('[project]\nname = "readablestem"\n')
    monkeypatch.chdir(root)
    return root


def test_a_missing_recording_is_a_clear_error_with_exit_code_3(project, capsys):
    assert cli_main.main(["proof"]) == 3
    err = capsys.readouterr().err
    assert err.startswith("error: no recorded run at")
    assert "proof --live --max-usd" in err


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
