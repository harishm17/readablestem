"""Which setup step of the Landlock launcher failed, as a fixed code (`core/landlock_exec.py`,
`sandbox.backend_failure`, the probe's `landlock_step=`, `landlock_errno=` and `can_<call>=`).

The codes are the only thing that leaves the launcher about a failure: never free text, and never
anything the sandboxed program says."""

from __future__ import annotations

import errno
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from readablestem.core import sandbox, seccomp
from readablestem.ops import sandbox_probe

needs_landlock = pytest.mark.skipif(
    not (
        sandbox.landlock_abi() >= sandbox.LANDLOCK_MIN_ABI
        and seccomp.supported()
        and sys.platform == "linux"
    ),
    reason="needs Landlock ABI 6 or later",
)


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


class FakeLibc:
    """Every call succeeds except the ones named: `broken` maps a name to a return value."""

    def __init__(self, broken: set[str]) -> None:
        self.broken = broken

    def syscall(self, number, *args):
        if number == 444 and args and args[-1] == 1:  # the ABI query
            return 8
        name = {444: "create", 445: "add", 446: "restrict"}.get(number, "other")
        if name in self.broken:
            return -1
        return 0 if name in ("add", "restrict") else 5  # success, or a ruleset fd

    def prctl(self, option, *args):
        name = {38: "nnp", 22: "seccomp"}.get(option, "other")
        return -1 if name in self.broken else 0


# What a hostile program could print and how it could exit to look like a launcher failure.
PRETEND = 'print STDERR "exec ENOENT\\nlandlock_create_ruleset EPERM\\n"; exit 125'

SPEC = {"min_abi": 6, "exec": ["/usr"], "read": [], "write": [], "files_rw": [], "files_read": []}


@pytest.mark.parametrize(
    ("broken", "code"),
    [
        ({"create"}, "landlock_create_ruleset"),
        ({"add"}, "landlock_add_rule:exec"),
        ({"nnp"}, "no_new_privs"),
        ({"restrict"}, "landlock_restrict_self"),
    ],
)
def test_each_ruleset_step_names_itself_and_the_errno(launcher, broken, code):
    with pytest.raises(Failed) as caught:
        launcher.restrict(FakeLibc(broken), dict(SPEC))
    assert caught.value.code == code and caught.value.number == errno.EPERM


@pytest.mark.parametrize("kind", ["read", "write", "files_rw", "files_read"])
def test_an_add_rule_failure_says_which_class_of_path(launcher, kind):
    spec = {**SPEC, "exec": [], kind: ["/usr/bin/true" if kind.startswith("files") else "/usr"]}
    with pytest.raises(Failed) as caught:
        launcher.restrict(FakeLibc({"add"}), spec)
    assert caught.value.code == f"landlock_add_rule:{kind}"


def test_a_kernel_below_the_needed_abi_is_its_own_code(launcher):
    with pytest.raises(Failed) as caught:
        launcher.restrict(FakeLibc(set()), {**SPEC, "min_abi": 99})
    assert caught.value.code == "abi_below_min"


def test_the_seccomp_step_names_itself(launcher):
    with pytest.raises(Failed) as caught:
        launcher.install_seccomp(FakeLibc({"seccomp"}), seccomp.program(strict=True) or b"")
    assert caught.value.code == "seccomp_install" and caught.value.number == errno.EPERM


@pytest.mark.parametrize("name", ["as", "cpu", "fsize", "core", "nofile"])
def test_each_rlimit_step_names_its_resource(launcher, monkeypatch, name):
    monkeypatch.setattr(launcher, "restrict", lambda libc, spec: None)
    which = launcher.LIMITS[name]
    real = launcher.resource.setrlimit

    def refuse(resource_id, values):
        if resource_id == which:
            raise OSError(errno.EPERM, "no")
        real(resource_id, values)

    monkeypatch.setattr(launcher.resource, "setrlimit", refuse)
    spec = {"limits": {name: 1 << 30}, "cmd": ["/usr/bin/true"], "env": {}}
    with pytest.raises(Failed) as caught:
        launcher.confine_and_exec(FakeLibc(set()), spec, 100, os.getppid())
    assert caught.value.code == f"prlimit_{name}" and caught.value.number == errno.EPERM


def test_the_nproc_step_names_itself(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "restrict", lambda libc, spec: None)
    nproc = launcher.resource.RLIMIT_NPROC
    real = launcher.resource.setrlimit

    def refuse(resource_id, values):
        if resource_id == nproc:
            raise OSError(errno.EPERM, "no")
        real(resource_id, values)

    monkeypatch.setattr(launcher.resource, "setrlimit", refuse)
    with pytest.raises(Failed) as caught:
        launcher.confine_and_exec(
            FakeLibc(set()), {"limits": {}, "cmd": ["/x"], "env": {}}, 5, os.getppid()
        )
    assert caught.value.code == "prlimit_nproc"


def test_the_report_has_a_fixed_shape_and_an_errno_name(monkeypatch, tmp_path):
    module = launcher_module()
    report = tmp_path / "step"
    monkeypatch.setitem(module.REPORT, "file", str(report))
    monkeypatch.setitem(module.REPORT, "fd", -1)
    exits: list[int] = []

    def stop(status: int) -> None:
        exits.append(status)
        raise SystemExit(status)

    monkeypatch.setattr(module.os, "_exit", stop)
    with pytest.raises(SystemExit):
        module.fail("fork", errno.EAGAIN)
    assert report.read_text() == "fork EAGAIN" and exits == [125]


# ----------------------------------------------------- through the real launcher and sandbox


@needs_landlock
def test_a_program_that_cannot_be_started_reports_the_exec_step(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    outcome = sandbox._run("landlock", ["/nonexistent/program"], rw=((out, "/out"),), cgroup=False)
    assert not outcome.ok and outcome.returncode == 125
    assert (outcome.step, outcome.step_errno) == ("exec", "ENOENT")


@needs_landlock
def test_a_missing_working_folder_reports_the_chdir_step(tmp_path):
    outcome = sandbox._run("landlock", ["/usr/bin/true"], cwd="/nonexistent", cgroup=False)
    assert (outcome.step, outcome.step_errno) == ("chdir", "ENOENT")


@needs_landlock
def test_a_launcher_that_needs_a_newer_abi_reports_it_through_the_pipe(tmp_path):
    spec = sandbox._landlock_spec(
        ["/usr/bin/true"], ro=(), rw=(), tools=(), env=None, cwd="/", limits=sandbox.Limits(),
        hide=(), scratch=tmp_path, stubs=tmp_path, program=None,
        step_file=str(tmp_path / "step"),
    )  # fmt: skip
    spec["min_abi"] = 99
    done = subprocess.run(
        [sys.executable, "-I", "-S", str(sandbox.LAUNCHER), json.dumps(spec)],
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    assert done.returncode == 125
    assert (tmp_path / "step").read_text() == "abi_below_min"


@needs_landlock
def test_what_the_program_says_or_exits_with_is_never_a_step(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    log = tmp_path / "log"
    outcome = sandbox._run(
        "landlock",
        ["/usr/bin/perl", "-e", PRETEND],
        rw=((out, "/out"),), log=log, cgroup=False,
    )  # fmt: skip
    assert outcome.returncode == 125 and outcome.step == "" and outcome.step_errno == ""


def test_only_a_step_of_the_fixed_shape_is_read(tmp_path):
    folder = tmp_path
    assert sandbox._read_step(None) == ("", "")
    for text, expected in [
        ("exec ENOENT", ("exec", "ENOENT")),
        ("landlock_add_rule:read EACCES", ("landlock_add_rule:read", "EACCES")),
        ("abi_below_min", ("abi_below_min", "")),
        ("/etc/passwd: root:x:0:0", ("", "")),
        ("exec ENOENT\nmore", ("", "")),
        ("Exec enoent", ("", "")),
    ]:
        (folder / "step").write_text(text)
        assert sandbox._read_step(folder) == expected, text


# ------------------------------------------------------------------ backend and probe


def test_a_refused_backend_is_remembered_with_its_step(monkeypatch):
    monkeypatch.setenv(sandbox.BACKEND_ENV, "landlock")
    monkeypatch.setattr(sandbox, "landlock_abi", lambda: sandbox.LANDLOCK_MIN_ABI - 1)
    sandbox.backend.cache_clear()
    try:
        assert sandbox.backend() is None
        assert sandbox.backend_failure("landlock") == ("abi_below_min", "", 0)
        monkeypatch.setattr(
            sandbox, "_run",
            lambda name, *a, **k: sandbox.Outcome(125, step="seccomp_install", step_errno="EPERM"),
        )  # fmt: skip
        sandbox.backend.cache_clear()
        assert sandbox.backend() is None
        assert sandbox.backend_failure("landlock") == ("seccomp_install", "EPERM", 125)
        monkeypatch.setattr(sandbox, "_run", lambda name, *a, **k: sandbox.Outcome(125))
        sandbox.backend.cache_clear()
        assert sandbox.backend() is None
        assert sandbox.backend_failure("landlock") == ("no_report", "", 125)
    finally:
        monkeypatch.undo()
        sandbox.backend.cache_clear()


def test_the_probe_prints_the_step_and_the_errno(monkeypatch):
    monkeypatch.setattr(
        sandbox, "backend_failure", lambda name: ("landlock_restrict_self", "EPERM", 125)
    )
    report = sandbox_probe.Report()
    sandbox_probe._landlock_step(report, None)
    assert report.lines()[:-1] == [
        "landlock_step=landlock_restrict_self",
        "landlock_errno=EPERM",
        "landlock_exit=125",
    ]
    ok = sandbox_probe.Report()
    sandbox_probe._landlock_step(ok, "landlock")
    assert ok.lines()[0] == "landlock_step=ok"
    monkeypatch.setattr(sandbox, "backend_failure", lambda name: None)
    untried = sandbox_probe.Report()
    sandbox_probe._landlock_step(untried, "bwrap")
    assert untried.lines()[0] == "landlock_step=not_tried"


def test_none_of_these_lines_fails_the_probe_or_changes_what_it_requires(monkeypatch):
    report = sandbox_probe.Report()
    monkeypatch.setattr(sandbox, "backend_failure", lambda name: ("exec", "ENOENT", 125))
    sandbox_probe._landlock_step(report, None)
    monkeypatch.setattr(sandbox_probe, "can_do", lambda name: (False, "EPERM"))
    sandbox_probe._can_checks(report)
    assert report.passed and all(not check.required for check in report.checks)


@pytest.mark.parametrize("name", sandbox_probe._CAN)
def test_each_call_the_launcher_needs_can_be_tried_once_in_a_child(name):
    ok, why = sandbox_probe.can_do(name)
    assert isinstance(ok, bool)
    if ok:
        assert why == ""
    else:
        assert why.startswith("E")


@needs_landlock
@pytest.mark.parametrize("name", sandbox_probe._CAN)
def test_on_a_machine_that_runs_the_backend_every_call_works(name):
    assert sandbox_probe.can_do(name) == (True, "")


def test_a_call_the_container_filter_refuses_is_reported_with_its_errno(monkeypatch):
    monkeypatch.setattr(sandbox_probe, "_CAN_CHILD", 'print("no EPERM")')
    assert sandbox_probe.can_do("landlock_restrict_self") == (False, "EPERM")
    monkeypatch.setattr(sandbox_probe, "_CAN_CHILD", 'print("no /etc/passwd: root")')
    assert sandbox_probe.can_do("landlock_restrict_self") == (False, "EFAILED")
    monkeypatch.setattr(sandbox_probe, "_CAN_CHILD", "raise SystemExit(3)")
    assert sandbox_probe.can_do("landlock_restrict_self") == (False, "EFAILED")


def test_the_probe_lists_each_call_as_yes_or_no(monkeypatch):
    answers = {"landlock_create_ruleset": (True, ""), "seccomp_prctl": (False, "EPERM")}
    monkeypatch.setattr(sandbox_probe, "can_do", lambda name: answers.get(name, (True, "")))
    report = sandbox_probe.Report()
    sandbox_probe._can_checks(report)
    lines = report.lines()
    assert "can_landlock_create_ruleset=yes" in lines
    assert "can_seccomp_prctl=no" in lines and "can_seccomp_prctl_errno=EPERM" in lines
    assert not any(line.startswith("can_prlimit_self_errno") for line in lines)


def test_the_codes_reach_the_log_line_and_healthz_ops(tmp_path):
    from readablestem.web.sandbox_status import SandboxStatus, run_probe

    report = sandbox_probe.Report()
    report.add("sandbox_start", "fail")
    report.add("landlock_step", "seccomp_install", required=False)
    report.add("landlock_errno", "EPERM", required=False)
    report.add("can_seccomp_prctl", "no", required=False)
    status = SandboxStatus()
    thread = run_probe(status, timeout_s=5, probe=lambda: report, installed=lambda: True)
    assert thread is not None
    thread.join(10)
    shown = status.public()
    assert shown["result"] == "fail"
    assert shown["checks"]["landlock_step"] == "seccomp_install"  # type: ignore[index]
    assert shown["checks"]["landlock_errno"] == "EPERM"  # type: ignore[index]
    assert shown["checks"]["can_seccomp_prctl"] == "no"  # type: ignore[index]


def test_the_launcher_file_still_imports_only_the_standard_library():
    import ast

    roots = set()
    for node in ast.walk(ast.parse(Path(sandbox.LAUNCHER).read_text())):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= set(sys.stdlib_module_names)


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


@needs_landlock
def test_a_launcher_whose_parent_is_not_the_process_that_started_it_names_caller_gone(tmp_path):
    spec = _spec(tmp_path)
    spec["parent_pid"] = 1  # the real parent is this test, not pid 1
    done = _launch(spec)
    assert done.returncode == 125
    assert (tmp_path / "step").read_text() == "caller_gone"


@needs_landlock
def test_a_launcher_does_not_care_what_its_callers_pid_is(tmp_path):
    """The web process can be pid 1 in a container: the check is "my parent is the process that
    started me" (the spec names it), never "my parent is not pid 1"."""
    spec = _spec(tmp_path)
    assert spec["parent_pid"] == os.getpid()
    done = _launch(spec)
    assert done.returncode == 0 and not (tmp_path / "step").exists()
    source = Path(sandbox.LAUNCHER).read_text()
    assert "parent == 1" not in source and "getppid() == 1" not in source


@needs_landlock
def test_a_launcher_that_cannot_count_the_users_tasks_names_count_tasks(tmp_path):
    spec = _spec(tmp_path)
    empty = tmp_path / "noproc"
    empty.mkdir()
    spec["proc_root"] = str(empty)  # /proc is not readable
    done = _launch(spec)
    assert done.returncode == 125 and (tmp_path / "step").read_text() == "count_tasks"


@needs_landlock
def test_a_bug_in_the_launcher_names_itself_instead_of_ending_silently(tmp_path):
    spec = _spec(tmp_path)
    del spec["limits"]  # a malformed spec: a KeyError inside the supervisor
    done = _launch(spec)
    assert done.returncode == 125 and (tmp_path / "step").read_text() == "launcher_exception"


def test_a_supervisor_that_is_gone_is_named_by_the_child(launcher, monkeypatch):
    monkeypatch.setattr(launcher.os, "getppid", lambda: 4242)
    with pytest.raises(Failed) as caught:
        launcher.confine_and_exec(FakeLibc(set()), {"limits": {}}, 5, supervisor=1)
    assert caught.value.code == "supervisor_gone"


def test_no_launcher_path_ends_with_a_bare_exit():
    """Every exit of the launcher with its own status goes through `fail`, which names the step."""
    source = Path(sandbox.LAUNCHER).read_text()
    assert source.count("os._exit(125)") == 1  # the one inside `fail` itself


@needs_landlock
def test_without_the_subreaper_the_process_group_is_killed_and_nothing_is_left(tmp_path):
    spec = _spec(
        tmp_path,
        cmd=["/usr/bin/perl", "-e", 'my $p = fork(); if ($p == 0) { exec("/bin/sleep", "891") }'],
    )
    spec["no_subreaper"] = True
    done = _launch(spec)
    assert done.returncode == 0

    time.sleep(0.3)
    ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    assert "sleep 891" not in ps


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


def test_kill_group_kills_the_others_of_the_group_and_not_itself(tmp_path):
    code = (
        "import importlib.util, subprocess, sys, time\n"
        f"spec = importlib.util.spec_from_file_location('l', {str(sandbox.LAUNCHER)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "kids = [subprocess.Popen(['/bin/sleep', '892']) for _ in range(2)]\n"
        "m.kill_group()\n"
        "time.sleep(0.2)\n"
        "print([k.poll() is not None for k in kids])\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        start_new_session=True,  # its own group: the test's processes are not in it
        timeout=30,
    )
    assert done.stdout.strip() == "[True, True]", done.stderr


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
