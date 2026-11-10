"""Which setup step of the Landlock launcher failed, as a fixed code (`core/landlock_exec.py`,
`sandbox.backend_failure`, the probe's `landlock_step=`, `landlock_errno=` and `can_<call>=`).

The codes are the only thing that leaves the launcher about a failure: never free text, and never
anything the sandboxed program says."""

from __future__ import annotations

import errno
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from readablestem.core import sandbox, seccomp
from readablestem.ops import sandbox_probe


def launcher_module():
    """The launcher as a module (it is run by path in production; here its steps are called with
    a fake libc)."""
    spec = importlib.util.spec_from_file_location("landlock_exec_under_test", sandbox.LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Failed(Exception):
    def __init__(self, code: str, number: int = 0) -> None:
        super().__init__(code)
        self.code, self.number = code, number


@pytest.fixture
def launcher(monkeypatch):
    module = launcher_module()

    def fail(code: str, number: int = 0) -> None:
        raise Failed(code, number)

    monkeypatch.setattr(module, "fail", fail)
    monkeypatch.setattr(module.ctypes, "get_errno", lambda: errno.EPERM)
    return module


# ----------------------------------------------------- through the real launcher and sandbox


# ------------------------------------------------------------------ backend and probe


# ------------------------------------------- the launcher's own (supervisor-side) checks


def _spec(tmp_path: Path, cmd=None, **limits) -> dict:
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return sandbox._landlock_spec(
        cmd or ["/usr/bin/true"], ro=(), rw=((out, "/out"),), tools=(), env=None, cwd="/",
        limits=sandbox.Limits(wall_s=20, **limits), hide=(), scratch=out, stubs=out,
        program=seccomp.program(strict=True), busy_marker=str(tmp_path / "busy"),
        step_file=str(tmp_path / "step"),
    )  # fmt: skip


def _launch(spec: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", "-S", str(sandbox.LAUNCHER), json.dumps(spec)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
        start_new_session=True,  # as `sandbox.run` starts it: the leader of a group of its own
    )


def test_a_launcher_that_is_not_its_groups_leader_kills_no_group(tmp_path):
    """Run in the caller's own group it would be the caller it killed: it does nothing then."""
    code = (
        "import importlib.util, subprocess, sys, time\n"
        f"spec = importlib.util.spec_from_file_location('l', {str(sandbox.LAUNCHER)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "kid = subprocess.Popen(['/bin/sleep', '893'])\n"
        "m.kill_group()\n"
        "time.sleep(0.2)\n"
        "print(kid.poll() is None); kid.kill()\n"
    )
    # started from a shell-less child in a new session, then made a non-leader by a fork
    wrapper = (
        "import os, subprocess, sys\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    os.execv(sys.executable, [sys.executable, '-I', '-c', sys.argv[1]])\n"
        "os.waitpid(pid, 0)\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", wrapper, code],
        capture_output=True,
        text=True,
        start_new_session=True,
        timeout=30,
    )
    assert done.stdout.strip() == "True", done.stderr


@pytest.mark.skipif(
    shutil.which("podman") is None
    or subprocess.run(
        ["podman", "image", "exists", "localhost/mini-latexml"], capture_output=True
    ).returncode
    != 0,
    reason="needs podman and the localhost/mini-latexml image (docs/deploy-railway.md)",
)
def test_the_backend_starts_in_a_container_where_the_caller_is_pid_1():
    """Railway's shape: a non-root user, a read-only root, the default seccomp profile, and the
    process that starts the sandbox is pid 1 of the container."""
    root = Path(sandbox.LAUNCHER).parents[2]
    code = (
        "import os, sys; sys.path.insert(0, '/src');"
        "os.environ['READABLESTEM_SANDBOX_BACKEND'] = 'landlock';"
        "from readablestem.core import sandbox;"
        "print(os.getpid(), sandbox.backend(), sandbox.backend_failure('landlock'))"
    )
    done = subprocess.run(
        [
            "podman", "run", "--rm", "--user", "10001:10001", "--read-only",
            "--tmpfs", "/tmp:rw,exec", "--cap-drop=ALL", "-v", f"{root}:/src:ro",
            "localhost/mini-latexml", "python3", "-c", code,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )  # fmt: skip
    assert done.stdout.strip() == "1 landlock None", (done.stdout, done.stderr)


# ----------------------------------------- the task limit is the kernel's count, not /proc's


PROBE_SCRIPT = r"""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, "/src")
os.environ["READABLESTEM_SANDBOX_BACKEND"] = "landlock"
from readablestem.core import sandbox
from readablestem.source import latexml
out, src = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
(src / "main.tex").write_text(r"\documentclass{article}\begin{document}Hi $x$.\end{document}")
fork = sandbox._run(
    "landlock",
    ["/usr/bin/perl", "-e", "my $p = fork(); die 'nofork' unless defined $p; exit 0 if !$p; wait;"],
    cgroup=False,
)
done = latexml.convert(src, "main.tex", out)
visible = sum(1 for e in os.scandir("/proc") if e.name.isdigit())
html = (out / "html" / "doc.xhtml").is_file()
print("visible", visible, "fork", fork.ok, "latexml", done.ok and html)
"""


@pytest.mark.skipif(
    shutil.which("podman") is None
    or subprocess.run(
        ["podman", "image", "exists", "localhost/mini-latexml"], capture_output=True
    ).returncode
    != 0,
    reason="needs podman and the localhost/mini-latexml image (docs/deploy-railway.md)",
)
def test_a_program_can_fork_and_latexml_converts_when_most_of_the_users_tasks_are_hidden(
    tmp_path,
):
    """The shape that failed on Railway: 400 tasks of the same user that this container's /proc
    does not show (another pid namespace of the same user namespace) are counted by the kernel
    against RLIMIT_NPROC. The old limit, worked out from /proc, was below the real count: every
    fork failed, LaTeXML waited and retried until the wall clock, and the probe's memory test
    never started its tasks."""
    root = Path(sandbox.LAUNCHER).parents[2]
    (tmp_path / "probe.py").write_text(PROBE_SCRIPT)
    tmp_path.chmod(0o755)  # readable by the container's uid 10001
    (tmp_path / "probe.py").chmod(0o644)
    inner = (
        'setpriv --reuid=10001 --regid=10001 --clear-groups python3 -c "import subprocess, time;'
        " [subprocess.Popen(['sleep','300']) for _ in range(400)]; time.sleep(300)\" & sleep 3;"
        " unshare --pid --fork --mount-proc setpriv --reuid=10001 --regid=10001 --clear-groups"
        " env HOME=/tmp python3 /rail/probe.py"
    )
    done = subprocess.run(
        [
            "podman", "run", "--rm", "--cap-add=SYS_ADMIN", "--security-opt", "seccomp=unconfined",
            "--tmpfs", "/tmp:rw,exec", "-v", f"{root}:/src:ro", "-v", f"{tmp_path}:/rail:ro",
            "localhost/mini-latexml", "sh", "-c", inner,
        ],
        capture_output=True,
        text=True,
        timeout=200,
    )  # fmt: skip
    line = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else done.stderr[-300:]
    assert line.endswith("fork True latexml True"), line
    assert line.startswith("visible 1 ") or line.startswith("visible 2 "), line  # really hidden


def test_the_latexml_log_tail_is_fixed_size_plain_text_without_paths():
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        log = Path(folder) / "latexml.out"
        noisy = "Fatal:perl:die at /tmp/rs-sandbox-ab/x.pm line 9\n\x1b[31m" + "word " * 200
        log.write_text(noisy)
        tail = sandbox_probe._log_tail(log)
        assert len(tail) <= sandbox_probe.LOG_TAIL_CHARS and "/" not in tail and "\x1b" not in tail
        assert " " not in tail  # one token, so it fits `name=value`
        log.write_text("error at /home/someone/secret/file.tex line 3")
        assert sandbox_probe._log_tail(log) == "error_at_<path>_line_3"
        assert sandbox_probe._log_tail(Path(folder) / "missing") == "none"
