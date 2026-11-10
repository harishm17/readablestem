"""`readablestem ops sandbox-probe` (`readablestem.ops.sandbox_probe`): the report's verdict and
the command's exit status. The real probe runs where bubblewrap works; a failing sandbox is
simulated by making the sandbox refuse to start."""

from __future__ import annotations

import pytest

from readablestem.cli.main import main
from readablestem.core import sandbox
from readablestem.ops import sandbox_probe

needs_sandbox = pytest.mark.skipif(
    not sandbox.available(),
    reason="needs a working sandbox (bubblewrap with user namespaces, or Landlock)",
)


def test_a_bubblewrap_that_exits_nonzero_fails_and_says_why(monkeypatch, capsys):
    monkeypatch.setattr(sandbox, "bwrap_path", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(sandbox, "prlimit_path", lambda: "/usr/bin/prlimit")
    monkeypatch.setattr(sandbox, "run", lambda *a, **k: sandbox.Outcome(1))
    assert main(["ops", "sandbox-probe"]) == 1
    out = capsys.readouterr().out
    assert "sandbox_start=fail" in out
    assert out.strip().splitlines()[-1] == "FAIL"


def test_no_sandbox_backend_at_all_fails(monkeypatch, capsys):
    monkeypatch.setattr(sandbox, "bwrap_path", lambda: None)
    monkeypatch.setattr(sandbox, "backend", lambda: None)
    assert main(["ops", "sandbox-probe"]) == 1
    out = capsys.readouterr().out
    assert "backend=none" in out and "sandbox_start=fail" in out
    assert out.strip().splitlines()[-1] == "FAIL"


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
