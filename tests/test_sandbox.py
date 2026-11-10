"""The sandbox for uploaded TeX (`readablestem.core.sandbox`).

`build_argv` is checked without a sandbox. The run tests start the real bubblewrap, with no
mocks, and are skipped where it is missing or cannot make a user namespace (some CI hosts)."""

from __future__ import annotations

import os
import shutil
import time

import pytest

from readablestem.core import sandbox, seccomp

needs_sandbox = pytest.mark.skipif(
    not (shutil.which("bwrap") and shutil.which("prlimit") and sandbox.backend() == "bwrap"),
    reason="needs bubblewrap with user namespaces",
)
PERL = "/usr/bin/perl"


def perl(code: str, tmp_path, **kwargs):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    log = tmp_path / "log"
    outcome = sandbox.run(
        [PERL, "-e", code], rw=((out, "/out"),), env={"HOME": "/tmp"}, log=log, **kwargs
    )
    return outcome, log.read_text() if log.exists() else ""


def test_missing_bubblewrap_is_refused_not_bypassed(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.build_argv(["/usr/bin/true"])
    monkeypatch.setenv(sandbox.BACKEND_ENV, "bwrap")  # only bubblewrap: no Landlock fallback
    sandbox.backend.cache_clear()
    try:
        assert sandbox.available() is False
    finally:
        monkeypatch.undo()
        sandbox.backend.cache_clear()


@needs_sandbox
def test_environment_is_empty_and_secrets_stay_out(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
    monkeypatch.setenv("SESSION_SECRET", "another-secret")
    outcome, text = perl(
        'print join(",", sort keys %ENV), "\\n"; print join("", values %ENV), "\\n";', tmp_path
    )
    assert outcome.ok
    assert "secret" not in text
    assert "ANTHROPIC" not in text and "SESSION" not in text


@needs_sandbox
def test_host_files_outside_the_mounts_cannot_be_read(tmp_path):
    secret = tmp_path / "private.txt"
    secret.write_text("do not read")
    _outcome, text = perl(
        f'print((-e "{secret}") ? "visible" : "absent");',
        tmp_path,
    )
    assert "absent" in text


@needs_sandbox
def test_no_network(tmp_path):
    _outcome, text = perl(
        "use Socket; my $r = 'nosocket';"
        "if (socket(S, PF_INET, SOCK_STREAM, 0)) "
        '{ $r = connect(S, sockaddr_in(80, inet_aton("1.1.1.1"))) ? "reached" : "unreachable"; }'
        "print $r;",
        tmp_path,
    )
    assert text in ("unreachable", "nosocket")


@needs_sandbox
def test_root_is_read_only_and_out_is_writable(tmp_path):
    _outcome, text = perl(
        'print((open(W, ">", "/usr/x") ? "usr-writable" : "usr-readonly"), "\\n");'
        'open(O, ">", "/out/f") or die; print O "hi"; close O; print "out-ok\\n";',
        tmp_path,
    )
    assert "usr-readonly" in text and "out-ok" in text
    assert (tmp_path / "out" / "f").read_text() == "hi"


@needs_sandbox
def test_input_mount_is_read_only(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.tex").write_text("x")
    out = tmp_path / "out"
    out.mkdir()
    log = tmp_path / "log"
    sandbox.run(
        [PERL, "-e", 'print((open(W, ">>", "/work/a.tex") ? "writable" : "readonly"));'],
        ro=((work, "/work"),),
        rw=((out, "/out"),),
        log=log,
    )
    assert log.read_text() == "readonly"
    assert (work / "a.tex").read_text() == "x"


@needs_sandbox
def test_memory_cap_is_enforced(tmp_path):
    _outcome, text = perl(
        'my $x = eval { "a" x 400_000_000 }; print defined($x) ? "allocated" : "refused";',
        tmp_path,
        limits=sandbox.Limits(memory_bytes=150_000_000, wall_s=30),
    )
    assert "allocated" not in text


@needs_sandbox
def test_wall_clock_kills_an_infinite_loop(tmp_path):
    start = time.monotonic()
    outcome, _ = perl("1 while 1;", tmp_path, limits=sandbox.Limits(wall_s=2, cpu_s=60))
    assert outcome.timed_out and not outcome.ok
    assert time.monotonic() - start < 15


@needs_sandbox
def test_runaway_output_is_killed_at_the_budget(tmp_path):
    outcome, _ = perl(
        'open(W, ">", "/out/big");'
        'while (1) { print W "x" x 100000; select(undef, undef, undef, 0.02); }',
        tmp_path,
        limits=sandbox.Limits(wall_s=30, out_bytes=500_000, file_bytes=100_000_000),
    )
    assert outcome.over_quota and not outcome.ok
    assert (tmp_path / "out" / "big").stat().st_size <= 100_000_000  # file cap bounds the rush


@needs_sandbox
def test_a_single_file_over_the_file_cap_stops(tmp_path):
    outcome, _ = perl(
        'open(W, ">", "/out/big"); for (1..100) { print W "x" x 100000; } close W;',
        tmp_path,
        limits=sandbox.Limits(wall_s=30, file_bytes=1_000_000),
    )
    assert not outcome.ok
    assert (tmp_path / "out" / "big").stat().st_size <= 1_000_000


@needs_sandbox
def test_a_file_can_be_hidden_so_the_program_sees_it_empty(tmp_path):
    program = (
        'print(((-e "/usr/bin/true") ? "present" : "absent")'
        ' . ((-s "/usr/bin/true") ? "+content" : "+empty"));'
    )
    out = tmp_path / "out"
    out.mkdir()
    shown = tmp_path / "shown.log"
    sandbox.run([PERL, "-e", program], rw=((out, "/out"),), log=shown)
    assert shown.read_text() == "present+content"
    hidden = tmp_path / "hidden.log"
    sandbox.run(
        [PERL, "-e", program],
        rw=((out, "/out"),),
        log=hidden,
        hide=("/usr/bin/true", "/nonexistent/file"),  # a file that is not there is skipped
    )
    assert hidden.read_text() == "present+empty"


# ---------------------------------------------------------------- seccomp, task and file limits


def _syscall_errno(tmp_path, number_expr: str) -> str:
    """The errno name a system call gets inside the sandbox ("ok" when it returned 0 or more)."""
    program = (
        f"my $r = syscall({number_expr}); use Errno qw(EPERM ENOSYS);"
        'print(($r >= 0) ? "ok" : ($! == EPERM ? "EPERM" : '
        '($! == ENOSYS ? "ENOSYS" : "other:$!")));'
    )
    _outcome, text = perl(program, tmp_path)
    return text


DENIED = [
    "ptrace", "mount", "umount2", "pivot_root", "keyctl", "add_key", "request_key", "bpf",
    "perf_event_open", "userfaultfd", "unshare", "setns", "kexec_load", "kexec_file_load",
    "init_module", "finit_module", "process_vm_readv", "process_vm_writev", "io_uring_setup",
    "io_uring_enter", "io_uring_register", "chroot", "reboot", "open_by_handle_at",
]  # fmt: skip
needs_filter = pytest.mark.skipif(
    not seccomp.supported(), reason="the filter knows x86-64 and arm64 only"
)


@needs_sandbox
@needs_filter
@pytest.mark.parametrize("name", DENIED)
def test_the_denied_calls_answer_eperm_inside_the_sandbox(tmp_path, name):
    assert _syscall_errno(tmp_path, f"{seccomp.number(name)}, 0, 0, 0, 0") == "EPERM"


@needs_sandbox
@needs_filter
def test_clone_with_namespace_flags_is_denied_and_clone3_falls_back(tmp_path):
    clone = seccomp.number("clone")
    assert _syscall_errno(tmp_path, f"{clone}, 0x40000000, 0, 0, 0") == "EPERM"  # CLONE_NEWNET
    assert _syscall_errno(tmp_path, f"{seccomp.number('clone3')}, 0, 0") == "ENOSYS"
    _outcome, text = perl(
        'my $p = fork(); if ($p == 0) { exit 0 } waitpid($p, 0); print "forked";', tmp_path
    )
    assert text == "forked"  # an ordinary fork is a clone without namespace flags


def test_the_filter_is_whole_and_unknown_machines_get_none():
    blob = seccomp.program("x86_64")
    assert blob is not None and len(blob) % 8 == 0 and len(blob) // 8 < 255
    assert seccomp.program("aarch64") is not None
    assert seccomp.program("riscv64") is None
    assert {"ptrace", "mount", "bpf", "io_uring_setup", "clone3"} <= set(seccomp.denied_names())


@needs_sandbox
def test_a_fork_bomb_stops_at_the_task_limit(tmp_path):
    program = (
        "my $n = 0; for (1..300) { my $p = fork(); last unless defined $p; "
        "if ($p == 0) { sleep 5; exit 0 } $n++; } print $n;"
    )
    _outcome, text = perl(program, tmp_path, limits=sandbox.Limits(wall_s=30, pids=16))
    assert text.isdigit() and int(text) <= 16


@needs_sandbox
def test_killing_the_caller_leaves_no_sandboxed_process():
    import signal
    import subprocess
    import sys
    import time

    marker = f"sleep {os.getpid()}1"
    inner = (
        "from readablestem.core import sandbox as s;"
        f"s.run(['/bin/sh', '-c', {marker!r}], limits=s.Limits(wall_s=60))"
    )
    caller = subprocess.Popen([sys.executable, "-I", "-c", inner], start_new_session=True)
    time.sleep(2)
    os.killpg(caller.pid, signal.SIGKILL)
    caller.wait()
    time.sleep(1)
    survivors = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout
    assert survivors.strip() == ""


def test_a_cgroup_gets_a_memory_and_a_pids_limit_when_one_can_be_made(tmp_path):
    group = sandbox.Cgroup.make(sandbox.Limits(memory_bytes=123456, pids=7), parent=tmp_path)
    assert group is not None
    assert (group.path / "memory.max").read_text() == "123456"
    assert (group.path / "pids.max").read_text() == "7"
    assert group.add(4242) and (group.path / "cgroup.procs").read_text() == "4242"
    assert sandbox.Cgroup.make(sandbox.Limits(), parent=tmp_path / "missing") is None


# ----------------------------------------------------------- cgroup before exec, one at a time


@needs_sandbox
def test_the_process_joins_the_cgroup_before_it_becomes_bubblewrap(tmp_path, monkeypatch):
    seen: dict[str, object] = {}
    real = sandbox.subprocess.Popen

    def spy(argv, **kwargs):
        proc = real(argv, **kwargs)
        seen["argv"], seen["pid"] = argv, proc.pid
        return proc

    monkeypatch.setattr(sandbox.subprocess, "Popen", spy)
    out = tmp_path / "out"
    out.mkdir()
    parent = tmp_path / "cg"
    parent.mkdir()
    outcome = sandbox.run(
        [PERL, "-e", "print 1"], rw=((out, "/out"),), cgroup_parent=parent, limits=sandbox.Limits()
    )
    assert outcome.ok
    argv = seen["argv"]
    assert argv[:3] == ["/bin/sh", "-c", sandbox.JOIN_SCRIPT]  # type: ignore[index]
    groups = list(parent.glob("readablestem-sandbox-*"))
    assert len(groups) == 1
    # the shell wrote its own pid before exec: that pid is the process that became bubblewrap
    assert (groups[0] / "cgroup.procs").read_text().strip() == str(seen["pid"])
    assert (groups[0] / "memory.max").read_text() == str(sandbox.Limits().memory_bytes)


@needs_sandbox
def test_a_failed_join_does_not_stop_the_sandbox(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    parent = tmp_path / "cg"
    parent.mkdir()
    real_make = sandbox.Cgroup.make

    def make(limits, where=None):
        group = real_make(limits, where)
        assert group is not None
        (group.path / "cgroup.procs").mkdir()  # a directory: `echo > it` fails
        return group

    monkeypatch.setattr(sandbox.Cgroup, "make", staticmethod(make))
    log = tmp_path / "log"
    outcome = sandbox.run(
        [PERL, "-e", "print 'ran'"], rw=((out, "/out"),), cgroup_parent=parent, log=log
    )
    assert outcome.ok and log.read_text() == "ran"  # under the per-process limits


def test_the_exclusive_gate_lets_one_holder_in_at_a_time():
    with (
        sandbox.exclusive("test-gate", 1),
        pytest.raises(TimeoutError),
        sandbox.exclusive("test-gate", 0.3),
    ):
        pass
    with sandbox.exclusive("test-gate", 1):  # free again
        pass


def test_both_steps_of_a_conversion_keep_the_task_and_memory_limits(tmp_path, monkeypatch):
    from readablestem.source import latexml

    seen: list[sandbox.Limits] = []

    def fake_run(command, **kwargs):
        seen.append(kwargs["limits"])
        for folder in (
            tmp_path / "out",
            tmp_path / "out" / "xml",
        ):  # where the XML goes, by version
            folder.mkdir(parents=True, exist_ok=True)
            (folder / latexml.XML_NAME).write_text("<x/>")
        return sandbox.Outcome(0)

    monkeypatch.setattr(sandbox, "run", fake_run)
    latexml.convert(tmp_path, "main.tex", tmp_path / "out")
    assert len(seen) == 2
    assert {(limit.pids, limit.memory_bytes) for limit in seen} == {(4, 512 * 1024 * 1024)}
