"""The Landlock backend of the sandbox (`core/sandbox.py`, `core/landlock_exec.py`), the one used
where bubblewrap cannot start. Every test runs the real backend (named explicitly, so the tests
do not depend on which one the machine would pick) and is skipped where the kernel's Landlock is
older than the backend needs or is filtered."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from readablestem.core import sandbox, seccomp

pytestmark = pytest.mark.skipif(
    not (
        sandbox.landlock_abi() >= sandbox.LANDLOCK_MIN_ABI
        and seccomp.supported()
        and sys.platform == "linux"
    ),
    reason="needs Landlock ABI 3 or later",
)
PERL = "/usr/bin/perl"


def run_perl(code: str, tmp_path: Path, *, ro=(), **kwargs):
    """Run Perl on the Landlock backend with a writable output folder as `$ARGV[0]`."""
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    log = tmp_path / "log"
    outcome = sandbox._run(
        "landlock",
        [PERL, "-e", code, str(out)],
        ro=ro,
        rw=((out, "/out"),),
        env={"HOME": "/tmp"},
        log=log,
        cgroup=False,
        **kwargs,
    )
    return outcome, log.read_text() if log.exists() else ""


def test_it_starts_and_is_chosen_when_bubblewrap_cannot(monkeypatch):
    monkeypatch.setenv(sandbox.BACKEND_ENV, "landlock")
    sandbox.backend.cache_clear()
    try:
        assert sandbox.backend() == "landlock" and sandbox.available()
    finally:
        monkeypatch.undo()
        sandbox.backend.cache_clear()
    monkeypatch.setattr(sandbox, "bwrap_path", lambda: None)  # bubblewrap cannot start
    sandbox.backend.cache_clear()
    try:
        assert sandbox.backend() == "landlock"
        assert sandbox.where(Path("/a/b"), "/inside") == "/a/b"  # no mounts: the host path
    finally:
        monkeypatch.undo()
        sandbox.backend.cache_clear()


def test_a_kernel_below_the_needed_abi_is_refused_not_bypassed(monkeypatch):
    monkeypatch.setattr(sandbox, "landlock_abi", lambda: sandbox.LANDLOCK_MIN_ABI - 1)
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox._run("landlock", ["/usr/bin/true"])


def test_no_backend_means_no_run(monkeypatch):
    monkeypatch.setattr(sandbox, "backend", lambda: None)
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.run(["/usr/bin/true"])


def test_the_launcher_refuses_to_run_half_confined(tmp_path):
    spec_abi = ["x"]  # a minimum ABI no kernel has
    out = tmp_path / "out"
    out.mkdir()
    import json

    spec = sandbox._landlock_spec(
        ["/usr/bin/touch", str(out / "ran")], ro=(), rw=((out, "/out"),), tools=(), env=None,
        cwd="/", limits=sandbox.Limits(), hide=(), scratch=out, stubs=out, program=None,
    )  # fmt: skip
    spec["min_abi"] = 99
    done = subprocess.run(
        [sys.executable, "-I", "-S", str(sandbox.LAUNCHER), json.dumps(spec)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    assert done.returncode == 125 and "ABI" in done.stderr and spec_abi
    assert not (out / "ran").exists()


def test_the_environment_is_empty_and_secrets_stay_out(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
    code = (
        'print join(",", sort grep { !/^(PATH|HOME|TMPDIR)$/ } keys %ENV), "|"; '
        'print((join("", values %ENV) =~ /sk-test/) ? "visible" : "absent");'
    )
    outcome, text = run_perl(code, tmp_path)
    assert outcome.ok and text == "|absent"


def test_files_of_the_host_outside_the_grants_cannot_be_read(tmp_path):
    secret = tmp_path.parent / f"{tmp_path.name}-secret.txt"
    secret.write_text("TOPSECRET")
    try:
        code = (
            "for my $p ('/etc/passwd','/etc/shadow','/app/pyproject.toml',"
            f"'{secret}',\"$ENV{{HOME}}/x\") "
            '{ print "$p:", (open(F,"<",$p) ? "READ" : "no"), "\\n" }'
            ' print "home:", (opendir(D, "/home") ? "LISTED" : "no"), "\\n";'
        )
        outcome, text = run_perl(code, tmp_path)
    finally:
        secret.unlink()
    assert outcome.ok
    assert "READ" not in text and "LISTED" not in text and "TOPSECRET" not in text


def test_no_proc_not_pid_1_not_the_parent_not_self(tmp_path):
    code = (
        "my @t = ('/proc/1/environ', '/proc/'.getppid().'/environ', '/proc/self/environ',"
        " '/proc/self/maps', '/proc/self/mem', '/proc/self/status');"
        'for my $p (@t) { print "$p:", (open(F,"<",$p) ? "READ" : "no"), "\\n" }'
        ' print "dir:", (opendir(D, "/proc") ? "LISTED" : "no"), "\\n";'
    )
    outcome, text = run_perl(code, tmp_path)
    assert outcome.ok and "READ" not in text and "LISTED" not in text


def test_no_network_not_even_a_local_port(tmp_path):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    hits: list[int] = []
    threading.Thread(
        target=lambda: hits.append(1) if server.accept() else None, daemon=True
    ).start()
    code = (
        "use Socket; my $r = socket(S, PF_INET, SOCK_STREAM, 0) ? 'socket' : 'nosocket:'.($!+0);"
        " if ($r eq 'socket') { $r = connect(S, sockaddr_in("
        f"{port}, inet_aton('127.0.0.1'))) ? 'CONNECTED' : 'refused' }}"
        " print $r;"
        " print '|unix:', (socket(U, PF_UNIX, SOCK_STREAM, 0) ? 'socket' : 'no');"
        " print '|netlink:', (socket(N, 16, SOCK_RAW, 0) ? 'socket' : 'no');"
    )
    outcome, text = run_perl(code, tmp_path)
    server.close()
    assert outcome.ok and "CONNECTED" not in text and "|unix:no" in text and "|netlink:no" in text
    assert hits == []


def test_only_the_job_folders_are_writable(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    code = (
        'my $out = $ARGV[0]; my $in = "' + str(src) + '"; '
        'print "out:", (open(W,">","$out/w") ? "yes" : "no"), "\\n";'
        'print "in_read:", (open(R,"<","$in/a.txt") ? "yes" : "no"), "\\n";'
        'print "in_write:", (open(X,">","$in/new") ? "yes" : "no"), "\\n";'
        'print "in_change:", (open(Y,">>","$in/a.txt") ? "yes" : "no"), "\\n";'
        'print "in_unlink:", (unlink("$in/a.txt") ? "yes" : "no"), "\\n";'
        'print "usr:", (open(Z,">","/usr/probe") ? "yes" : "no"), "\\n";'
        'print "tmp:", (open(T,">","/tmp/rs-probe-x") ? "yes" : "no"), "\\n";'
        "my $up = $out; $up =~ s{/[^/]*$}{}; "
        'print "beside:", (open(B,">","$up/beside") ? "yes" : "no"), "\\n";'
        'print "rename_out:", (rename("$out/w", "$up/moved") ? "yes" : "no"), "\\n";'
        'print "link_in:", (link("$in/a.txt", "$out/l") ? "yes" : "no"), "\\n";'
    )
    outcome, text = run_perl(code, tmp_path, ro=((src, "/work"),))
    got = dict(line.split(":", 1) for line in text.splitlines())
    assert outcome.ok
    assert got["out"] == "yes" and got["in_read"] == "yes"
    for denied in ("in_write", "in_change", "in_unlink", "usr", "tmp", "beside", "rename_out"):
        assert got[denied] == "no", denied
    assert got["link_in"] == "no"  # not a way to copy an input file out
    assert (src / "a.txt").read_text() == "hello" and not (tmp_path / "beside").exists()


def test_tmp_in_the_environment_is_a_private_scratch_folder(tmp_path):
    code = (
        'my $h = $ENV{HOME}; open(W, ">", "$h/x") or die "no"; print W "1"; close W;'
        ' print "home:$h|", (-s "$h/x" ? "wrote" : "no");'
    )
    outcome, text = run_perl(code, tmp_path)
    assert outcome.ok and text.endswith("|wrote") and text.startswith("home:")
    scratch = text.split("|")[0][5:]
    assert scratch != "/tmp" and not Path(scratch).exists()  # removed with the run


DENIED = ["ptrace", "mount", "unshare", "setns", "bpf", "keyctl", "socket", "connect", "bind",
          "listen", "accept", "socketpair", "setsid", "setpgid", "landlock_create_ruleset",
          "landlock_add_rule", "landlock_restrict_self"]  # fmt: skip


@pytest.mark.parametrize("name", DENIED)
def test_the_denied_calls_answer_eperm(tmp_path, name):
    number = seccomp.number(name)
    code = (
        f"my $r = syscall({number}, 0, 0, 0, 0, 0); use Errno qw(EPERM);"
        'print(($r < 0 && $! == EPERM) ? "denied" : "allowed:$r:".($!+0));'
    )
    outcome, text = run_perl(code, tmp_path)
    assert outcome.ok and text == "denied", text


def test_a_program_cannot_leave_its_group_to_outlive_the_kill(tmp_path):
    code = (
        "use POSIX; my $p = fork(); if ($p == 0) { my $s = POSIX::setsid(); "
        'print "setsid:", (defined $s && $s > 0 ? "yes" : "no"), "\\n"; exec("/bin/sleep", "881") }'
        " sleep 1; ; wait;"
    )
    out = tmp_path / "out"
    out.mkdir()
    outcome, text = run_perl(code, tmp_path, limits=sandbox.Limits(wall_s=3))
    assert outcome.timed_out and "setsid:yes" not in text
    time.sleep(0.5)
    assert (
        "sleep 881"
        not in subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    )


def test_what_the_program_leaves_behind_is_killed_when_it_exits(tmp_path):
    code = (
        'my $p = fork(); if ($p == 0) { exec("/bin/sleep", "882") }'
        ' print "parent exits\\n"; exit 0;'
    )
    outcome, _text = run_perl(code, tmp_path)
    assert outcome.ok
    time.sleep(0.5)
    ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    assert "sleep 882" not in ps


def test_killing_the_caller_leaves_no_sandboxed_process(tmp_path):
    inner = (
        "import sys; from pathlib import Path; from readablestem.core import sandbox as s;"
        "o = Path(sys.argv[1]); o.mkdir(exist_ok=True);"
        "s._run('landlock', ['/bin/sh', '-c', 'sleep 883 & sleep 884'], rw=((o, '/out'),),"
        " limits=s.Limits(wall_s=60), cgroup=False)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", inner, str(tmp_path / "o")], start_new_session=True,
        env={**os.environ},
    )  # fmt: skip
    for _ in range(50):
        time.sleep(0.2)
        ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
        if "sleep 884" in ps:
            break
    assert "sleep 884" in ps
    os.kill(proc.pid, signal.SIGKILL)  # what a deadline does to a conversion child
    proc.wait()
    for _ in range(30):
        time.sleep(0.2)
        ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
        if "sleep 884" not in ps and "sleep 883" not in ps:
            break
    assert "sleep 884" not in ps and "sleep 883" not in ps


def test_limits_hold(tmp_path):
    outcome, text = run_perl(
        'my $n = 300_000_000; my $x = eval { "a" x $n };'
        ' print defined($x) ? "allocated" : "refused";',
        tmp_path, limits=sandbox.Limits(memory_bytes=128_000_000),
    )  # fmt: skip
    assert "allocated" not in text and ("refused" in text or "Out of memory" in text)
    start = time.monotonic()
    outcome, _ = run_perl("sleep 60;", tmp_path, limits=sandbox.Limits(wall_s=2))
    assert outcome.timed_out and time.monotonic() - start < 15
    outcome, _ = run_perl(
        'open(W, ">", "$ARGV[0]/big"); while (1) { print W "x" x 100000 }',
        tmp_path, limits=sandbox.Limits(wall_s=30, out_bytes=1_000_000),
    )  # fmt: skip
    assert outcome.over_quota
    outcome, text = run_perl(
        "my $n = 0; for (1..200) { my $p = fork(); last unless defined $p;"
        " if ($p == 0) { sleep 3; exit 0 } $n++ } print $n;",
        tmp_path, limits=sandbox.Limits(wall_s=20, pids=8),
    )  # fmt: skip
    assert text.isdigit() and int(text) <= 8 + sandbox.LANDLOCK_NPROC_SLACK
    outcome, text = run_perl(
        'my $n = 0; for (1..600) { open(my $f, "<", "/usr/bin/perl") or last; push @h, $f; $n++ }'
        " print $n;",
        tmp_path,
    )
    assert text.isdigit() and int(text) <= sandbox.NOFILE
    outcome, text = run_perl(
        'open(F, ">", "$ARGV[0]/f"); print F "x" x 2_000_000; close F; print -s "$ARGV[0]/f";',
        tmp_path, limits=sandbox.Limits(file_bytes=100_000),
    )  # fmt: skip
    assert not outcome.ok or int(text or 0) <= 100_000


def test_the_whole_group_is_held_to_its_memory_cap(tmp_path):
    code = (
        "for (1..3) { my $p = fork(); next unless defined $p;"
        ' if ($p == 0) { my $x = "a" x 100_000_000; sleep 20; exit 0 } } sleep 20;'
    )
    start = time.monotonic()
    outcome, _ = run_perl(
        code, tmp_path,
        limits=sandbox.Limits(wall_s=30, memory_bytes=300_000_000, pids=8,
                              group_memory_bytes=150_000_000),
    )  # fmt: skip
    assert outcome.over_memory and not outcome.ok and time.monotonic() - start < 15


def test_an_inherited_file_descriptor_is_not_passed_on(tmp_path):
    read_end, write_end = os.pipe()
    os.set_inheritable(read_end, True)
    try:
        code = f'print((open(F, "<&=", {read_end}) ? "INHERITED" : "closed"));'
        outcome, text = run_perl(code, tmp_path)
    finally:
        os.close(read_end)
        os.close(write_end)
    assert outcome.ok and text == "closed"


def test_the_parent_is_not_dumpable_so_a_same_user_child_cannot_read_its_environ():
    probe = (
        "import os, subprocess, sys\n"
        "from readablestem.core import sandbox\n"
        "assert sandbox.make_undumpable()\n"
        "me = os.getpid()\n"
        "r = subprocess.run([sys.executable, '-I', '-c',"
        " f\"print(open('/proc/{me}/environ','rb').read()[:3] and 'READ')\"],"
        " capture_output=True, text=True)\n"
        "print('READ' if 'READ' in r.stdout else 'blocked')\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={**os.environ, "SECRET_PROBE": "x"},
    )
    assert done.stdout.strip() == "blocked", done.stderr


def test_the_probe_checks_it_from_inside(tmp_path, monkeypatch):
    from readablestem.ops import sandbox_probe

    monkeypatch.setenv(sandbox.BACKEND_ENV, "landlock")
    sandbox.backend.cache_clear()
    try:
        report = sandbox_probe.Report()
        sandbox_probe._check_sandbox(report, tmp_path)
        lines = report.lines()
    finally:
        monkeypatch.undo()
        sandbox.backend.cache_clear()
    for name in (
        "sandbox_start", "sandbox_no_secrets", "sandbox_no_etc", "sandbox_pid1_environ",
        "sandbox_no_proc", "sandbox_no_network", "sandbox_outside_readonly",
    ):  # fmt: skip
        assert f"{name}=ok" in lines, lines
    assert "backend=landlock" in lines and report.passed


def test_the_launcher_is_stdlib_only_and_run_by_path():
    """It runs as `python -I -S <path>` in a bare process: nothing of the app is imported."""
    import ast

    tree = ast.parse(sandbox.LAUNCHER.read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= set(sys.stdlib_module_names), roots - set(sys.stdlib_module_names)


def test_the_strict_filter_adds_the_socket_and_session_calls_to_the_plain_one():
    plain, strict = seccomp.program(), seccomp.program(strict=True)
    assert plain is not None and strict is not None and len(strict) > len(plain)
    assert {"socket", "setsid", "landlock_restrict_self"} <= set(seccomp.denied_names(strict=True))
    assert "socket" not in seccomp.denied_names()
