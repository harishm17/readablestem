"""`readablestem ops sandbox-probe` (`readablestem.ops.sandbox_probe`): the report's verdict and
the command's exit status. The real probe runs where bubblewrap works; a failing sandbox is
simulated by making the sandbox refuse to start."""

from __future__ import annotations

import shutil

import pytest

from readablestem.cli.main import main
from readablestem.core import sandbox
from readablestem.ops import sandbox_probe
from readablestem.source import latexml

needs_sandbox = pytest.mark.skipif(
    not (shutil.which("bwrap") and shutil.which("prlimit") and sandbox.available()),
    reason="needs bubblewrap with user namespaces",
)


def test_report_passes_only_when_every_required_check_held():
    report = sandbox_probe.Report()
    report.add("a", "ok")
    report.add("info", "fail", required=False)
    assert report.passed and report.lines()[-1] == "PASS"
    report.add("b", "fail")
    assert not report.passed and report.lines()[-1] == "FAIL"
    assert "b=fail" in report.lines()


def test_a_sandbox_that_cannot_start_fails_the_probe(monkeypatch, capsys):
    def refuse(*args, **kwargs):
        raise sandbox.SandboxUnavailable("no user namespaces")

    monkeypatch.setattr(sandbox, "bwrap_path", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(sandbox, "prlimit_path", lambda: "/usr/bin/prlimit")
    monkeypatch.setattr(sandbox, "run", refuse)
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox_probe.probe()


def test_a_bubblewrap_that_exits_nonzero_fails_and_says_why(monkeypatch, capsys):
    monkeypatch.setattr(sandbox, "bwrap_path", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(sandbox, "prlimit_path", lambda: "/usr/bin/prlimit")
    monkeypatch.setattr(sandbox, "run", lambda *a, **k: sandbox.Outcome(1))
    assert main(["ops", "sandbox-probe"]) == 1
    out = capsys.readouterr().out
    assert "sandbox_start=fail" in out
    assert out.strip().splitlines()[-1] == "FAIL"


def test_no_bubblewrap_fails(monkeypatch, capsys):
    monkeypatch.setattr(sandbox, "bwrap_path", lambda: None)
    assert main(["ops", "sandbox-probe"]) == 1
    out = capsys.readouterr().out
    assert "bwrap=fail" in out and out.strip().splitlines()[-1] == "FAIL"


@needs_sandbox
def test_real_probe_passes_and_prints_no_secret(monkeypatch, capsys):
    monkeypatch.delenv("READABLESTEM_LATEXML_ROOT", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-key")
    code = main(["ops", "sandbox-probe"])
    out = capsys.readouterr().out
    assert code == 0 and out.strip().splitlines()[-1] == "PASS"
    names = [
        "sandbox_task_cap", "sandbox_file_cap", "sandbox_no_secrets", "sandbox_no_network",
        "sandbox_memory_cap", "sandbox_wall_cap", "sandbox_output_cap",
    ]  # fmt: skip
    for name in names:
        assert f"{name}=ok" in out
    assert "sk-secret-key" not in out and sandbox_probe.SECRET_VALUE not in out
    assert "seccomp_mode=" in out and "landlock_abi=" in out


@needs_sandbox
def test_require_latexml_fails_when_it_is_missing(monkeypatch, capsys):
    monkeypatch.delenv("READABLESTEM_LATEXML_ROOT", raising=False)
    monkeypatch.setattr(latexml, "installed", lambda: False)
    assert main(["ops", "sandbox-probe"]) == 0
    assert "latexml=missing" in capsys.readouterr().out
    assert main(["ops", "sandbox-probe", "--require-latexml"]) == 1


@pytest.mark.skipif(not latexml.installed(), reason="needs LaTeXML")
@needs_sandbox
def test_probe_times_a_latexml_run(capsys):
    assert main(["ops", "sandbox-probe", "--require-latexml"]) == 0
    out = capsys.readouterr().out
    assert "latexml=ok" in out and "latexml_seconds=" in out


def test_the_probe_reports_the_cgroup_and_the_filter(monkeypatch, capsys):
    from readablestem.core import seccomp

    if not (shutil.which("bwrap") and shutil.which("prlimit") and sandbox.available()):
        pytest.skip("needs bubblewrap with user namespaces")
    monkeypatch.delenv("READABLESTEM_LATEXML_ROOT", raising=False)
    main(["ops", "sandbox-probe"])
    out = capsys.readouterr().out
    assert ("sandbox_cgroup=ok" in out) or ("sandbox_cgroup=unavailable" in out)
    assert ("sandbox_seccomp=ok" in out) == seccomp.supported()


def test_a_total_bound_over_the_setting_fails_the_probe_when_latex_is_required(monkeypatch):
    from readablestem.config import get_settings

    monkeypatch.setenv("SOURCE_SANDBOX_MAX_TOTAL_MB", "1024")
    get_settings.cache_clear()
    try:
        report = sandbox_probe.Report()
        sandbox_probe._check_total_bound(report, cgroup=False)
        lines = report.lines()
        assert "sandbox_total_bound_mb=2048" in lines and "sandbox_total_bound=fail" in lines
        assert report.passed  # information until LaTeX is required
        for check in report.checks:
            if check.name == "sandbox_total_bound":
                check.required = True
        assert not report.passed
        cgroup_report = sandbox_probe.Report()
        sandbox_probe._check_total_bound(cgroup_report, cgroup=True)
        assert "sandbox_total_bound=ok" in cgroup_report.lines()  # 512 MB under a cgroup
    finally:
        get_settings.cache_clear()
