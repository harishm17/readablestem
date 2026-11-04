"""The Landlock backend of the sandbox (`core/sandbox.py`, `core/landlock_exec.py`), the one used
where bubblewrap cannot start. Every test runs the real backend (named explicitly, so the tests
do not depend on which one the machine would pick) and is skipped where the kernel's Landlock is
older than the backend needs or is filtered."""

from __future__ import annotations

import os
import platform
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
    reason="needs Landlock ABI 6 or later",
)
PERL = "/usr/bin/perl"


def run_perl(code: str, tmp_path: Path, *, ro=(), args=(), **kwargs):
    """Run Perl on the Landlock backend with a writable output folder as `$ARGV[0]` (and more
    arguments after it)."""
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    log = tmp_path / "log"
    outcome = sandbox._run(
        "landlock",
        [PERL, "-e", code, str(out), *args],
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
    assert done.returncode == 125 and "abi_below_min" in done.stderr and spec_abi
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
        limits=sandbox.Limits(wall_s=30, memory_bytes=300_000_000, pids=40,
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


# --------------------------------------------------------------------- the review's probes


@pytest.fixture
def victim():
    """A process of the same user with PR_SET_DUMPABLE=0, like the web process."""
    proc = subprocess.Popen(
        [
            sys.executable, "-I", "-c",
            "import ctypes, time; ctypes.CDLL(None).prctl(4, 0, 0, 0, 0);"
            "print('ready', flush=True); time.sleep(120)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )  # fmt: skip
    assert proc.stdout is not None
    proc.stdout.readline()
    yield proc
    proc.kill()
    proc.wait()


def limits_of(pid: int) -> dict[str, str]:
    rows = Path(f"/proc/{pid}/limits").read_text().splitlines()[1:]
    return {row[:25].strip(): row[25:].split()[0] for row in rows}


def test_another_process_of_the_user_cannot_be_controlled_without_a_signal(tmp_path, victim):
    """prlimit64, the scheduler calls, priorities, ioprio and pidfds on a pid that is not the
    caller's: every one answers EPERM and the victim is left as it was."""
    before = limits_of(victim.pid)
    n = seccomp.number
    code = (
        'my $v = $ARGV[1] + 0; my %r; my $lim = pack("QQ", 1, 1); my $p = pack("i", 0);'
        ' my $nf = pack("QQ", 3, 3); my $aff = pack("Q", 1); my $attr = pack("L8", 56, 5);'
        f" $r{{prlimit_cpu}} = syscall({n('prlimit64')}, $v, 0, $lim, 0);"
        f" $r{{prlimit_nofile}} = syscall({n('prlimit64')}, $v, 7, $nf, 0);"
        f" $r{{setscheduler}} = syscall({n('sched_setscheduler')}, $v, 5, $p);"
        f" $r{{setparam}} = syscall({n('sched_setparam')}, $v, $p);"
        f" $r{{setaffinity}} = syscall({n('sched_setaffinity')}, $v, 8, $aff);"
        f" $r{{setattr}} = syscall({n('sched_setattr')}, $v, $attr, 0);"
        f" $r{{setpriority}} = syscall({n('setpriority')}, 0, $v, 19);"
        f" $r{{setpriority_user}} = syscall({n('setpriority')}, 2, 0, 19);"
        f" $r{{ioprio}} = syscall({n('ioprio_set')}, 1, $v, (3 << 13) | 7);"
        f" $r{{pidfd}} = syscall({n('pidfd_open')}, $v, 0);"
        f" $r{{pidfd_parent}} = syscall({n('pidfd_open')}, getppid(), 0);"
        ' print join(",", map { "$_=" . ($r{$_} < 0 ? ($!+0) : "OK") } sort keys %r);'
    )
    outcome, text = run_perl(code, tmp_path, args=(str(victim.pid),))
    assert outcome.ok, text
    results = dict(item.split("=") for item in text.split(","))
    assert all(value == "1" for value in results.values()), results  # EPERM
    assert limits_of(victim.pid) == before
    assert victim.poll() is None


def test_a_program_may_still_set_its_own_limits_and_affinity(tmp_path):
    n = seccomp.number
    code = (
        'my $core = pack("QQ", 0, 0); my $aff = pack("Q", 1); my $p = pack("i", 0);'
        f" my $a = syscall({n('prlimit64')}, 0, 4, $core, 0);"  # RLIMIT_CORE on itself
        f" my $b = syscall({n('sched_setaffinity')}, 0, 8, $aff);"
        f" my $c = syscall({n('sched_setscheduler')}, 0, 0, $p);"
        ' print "$a,$b,$c";'
    )
    outcome, text = run_perl(code, tmp_path)
    assert outcome.ok and text == "0,0,0", text


def test_signals_stay_inside_the_sandbox_and_the_supervisor_is_out_of_reach(tmp_path, victim):
    code = (
        "my $v = $ARGV[1] + 0; $! = 0; my $a = kill(0, $v); my $ea = $!+0;"
        ' $! = 0; my $b = kill("STOP", getppid()); my $eb = $!+0;'
        ' $! = 0; my $c = kill("KILL", $v); my $ec = $!+0;'
        ' print "$a:$ea,$b:$eb,$c:$ec";'
    )
    outcome, text = run_perl(code, tmp_path, args=(str(victim.pid),))
    assert outcome.ok, text  # the supervisor was not stopped: the run ended
    assert text == "0:1,0:1,0:1" or text.count(":1") == 3, text  # EPERM each
    assert victim.poll() is None


def test_no_anonymous_files_shared_memory_or_queues(tmp_path):
    n = seccomp.number
    code = (
        'my %r; my @k = (["memfd", ' + f'{n("memfd_create")}, "x", 0],'
        f' ["shmget", {n("shmget")}, 0, 256000000, 01000 | 0600],'
        f' ["msgget", {n("msgget")}, 0, 01000 | 0600],'
        f' ["semget", {n("semget")}, 0, 1, 01000 | 0600],'
        f' ["mq_open", {n("mq_open")}, "/rsprobe", 0102, 0600, 0]);'
        " for my $c (@k) { my ($name, $nr, @a) = @$c; $! = 0;"
        ' my $x = syscall($nr, @a); $r{$name} = $x < 0 ? ($!+0) : "OK"; }'
        ' print join(",", map { "$_=$r{$_}" } sort keys %r);'
    )
    outcome, text = run_perl(code, tmp_path)
    segments = subprocess.run(["ipcs", "-m"], capture_output=True, text=True).stdout
    assert outcome.ok, text
    assert all(item.endswith("=1") for item in text.split(",")), text
    assert "256000000" not in segments  # no segment of the size the program asked for


def test_a_program_cannot_run_a_file_it_wrote_or_an_open_file(tmp_path):
    n = seccomp.number
    code = (
        f'open(my $f, "<", "/usr/bin/true") or die; my $fd = fileno($f);'
        f' my $e = ""; $! = 0; my $r = syscall({n("execveat")}, $fd, $e, 0, 0, 0x1000);'
        ' print(($r < 0 ? ($!+0) : "OK"));'
    )
    outcome, text = run_perl(code, tmp_path)
    assert outcome.ok and text == "1", text  # EPERM, not an exec of the open file


def test_files_deleted_but_held_open_still_count_against_the_budget(tmp_path):
    code = (
        'my $out = $ARGV[0]; my @held; for my $i (1..30) { open(my $f, "+>", "$out/f$i") or last;'
        ' print $f "x" x 20_000_000; $f->flush; unlink("$out/f$i"); push @held, $f; }'
        " sleep 30;"
    )
    start = time.monotonic()
    outcome, _ = run_perl(
        code, tmp_path,
        limits=sandbox.Limits(wall_s=30, out_bytes=100_000_000, file_bytes=50_000_000),
    )  # fmt: skip
    assert outcome.over_quota and not outcome.ok
    assert time.monotonic() - start < 15


def test_the_number_of_entries_in_the_writable_folders_is_capped(tmp_path):
    code = (
        'my $out = $ARGV[0]; for my $i (1..100000) { open(my $f, ">", "$out/e$i") or last }'
        " sleep 30;"
    )
    start = time.monotonic()
    outcome, _ = run_perl(
        code, tmp_path, limits=sandbox.Limits(wall_s=30, max_entries=500)
    )  # fmt: skip
    assert outcome.over_quota and time.monotonic() - start < 15
    assert len(list((tmp_path / "out").iterdir())) < 20_000


@pytest.mark.parametrize("where", ["inside the output folder", "outside every grant"])
def test_file_metadata_cannot_be_changed(tmp_path, where):
    target = (tmp_path / "out" / "t.txt") if where.startswith("inside") else tmp_path / "keep.txt"
    (tmp_path / "out").mkdir(exist_ok=True)
    target.write_text("data")
    target.chmod(0o640)
    before = target.stat()
    n = seccomp.number
    path = str(target)
    code = (
        f'my $p = "{path}"; my %r;'
        ' $! = 0; $r{chmod} = chmod(0, $p) ? "OK" : ($!+0);'
        ' $! = 0; $r{utime} = utime(1, 1, $p) ? "OK" : ($!+0);'
        ' $! = 0; $r{chown} = chown(-1, 12345, $p) ? "OK" : ($!+0);'
        f' my ($xn, $xv) = ("user.x", "v"); $! = 0;'
        f' $r{{setxattr}} = syscall({n("setxattr")}, $p, $xn, $xv, 1, 0) < 0 ? ($!+0) : "OK";'
        ' open(my $h, "<", $p); $! = 0; $r{fchmod} = chmod(0, $h) ? "OK" : ($!+0);'
        ' print join(",", map { "$_=$r{$_}" } sort keys %r);'
    )
    outcome, text = run_perl(code, tmp_path)
    after = target.stat()
    assert outcome.ok, text
    for item in text.split(","):
        name, value = item.split("=")
        # EPERM; EACCES where Landlock answers; EBADF for fchmod when the file could not be opened
        assert value in {"1", "13"} or (name == "fchmod" and value == "9"), (name, value)
    assert (after.st_mode, after.st_mtime, after.st_uid, after.st_gid) == (
        before.st_mode, before.st_mtime, before.st_uid, before.st_gid,
    )  # fmt: skip


@pytest.mark.parametrize(
    "name", sorted(n for n in seccomp._STRICT_DENIED if seccomp.number(n) >= 0)
)
def test_every_strict_call_answers_eperm(tmp_path, name):
    code = (
        f"my $r = syscall({seccomp.number(name)}, 0, 0, 0, 0, 0); use Errno qw(EPERM);"
        'print(($r < 0 && $! == EPERM) ? "denied" : "allowed:$r:".($!+0));'
    )
    outcome, text = run_perl(code, tmp_path)
    assert outcome.ok and text == "denied", (name, text)


def test_the_launcher_needs_landlock_abi_6_for_the_signal_and_socket_scopes():
    assert sandbox.LANDLOCK_MIN_ABI == 6


def test_the_bound_is_the_tasks_times_the_address_space_and_fits_the_setting():
    from readablestem.config import Settings
    from readablestem.source import latexml

    limits = latexml.LIMITS
    tasks = limits.task_bound("landlock")
    assert tasks == 4 and tasks * limits.memory_bytes <= 2048 * 1024 * 1024
    assert (
        tasks * limits.memory_bytes // (1024 * 1024)
        <= Settings(
            _env_file=None  # type: ignore[call-arg]
        ).source_sandbox_max_total_mb
    )


def test_latexml_needs_no_more_tasks_than_the_launcher_allows(tmp_path, monkeypatch):
    """The program itself is 1 task and LaTeXML adds at most 1 (measured: the supervisor and
    perl); a conversion under the Landlock limits succeeds."""
    from readablestem.source import latexml
    from source_support import needs_latexml  # noqa: F401

    if not latexml.installed():
        pytest.skip("needs LaTeXML")
    monkeypatch.setenv(sandbox.BACKEND_ENV, "landlock")
    sandbox.backend.cache_clear()
    try:
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.tex").write_text(
            "\\documentclass{article}\\begin{document}Hi $x^2$.\\end{document}"
        )
        outcome = latexml.convert(src, "main.tex", tmp_path / "out")
    finally:
        monkeypatch.undo()
        sandbox.backend.cache_clear()
    assert outcome.ok and (tmp_path / "out" / latexml.DOC_PATH).is_file()
    assert latexml.LIMITS.landlock_pids == 2


def test_the_supervisor_watches_the_process_not_the_thread_that_started_it(tmp_path):
    """PR_SET_PDEATHSIG would end the sandbox when the starting thread exits; the supervisor
    watches the caller's pid, so a short-lived thread does not matter."""
    import json

    out = tmp_path / "out"
    out.mkdir()
    spec = sandbox._landlock_spec(
        [PERL, "-e", 'sleep 2; print "alive"'], ro=(), rw=((out, "/out"),), tools=(), env=None,
        cwd="/", limits=sandbox.Limits(wall_s=20), hide=(), scratch=out, stubs=out,
        program=seccomp.program(strict=True),
    )  # fmt: skip
    started: list[subprocess.Popen] = []
    log = (tmp_path / "log").open("wb")

    def start():
        started.append(
            subprocess.Popen(
                [sys.executable, "-I", "-S", str(sandbox.LAUNCHER), json.dumps(spec)],
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            )
        )  # fmt: skip

    thread = threading.Thread(target=start)
    thread.start()
    thread.join()  # the thread that forked the launcher is gone
    assert started[0].wait(15) == 0
    log.close()
    assert (tmp_path / "log").read_text() == "alive"


# ------------------------------------------------------------- the second review's probes


def test_space_reserved_without_a_size_is_refused(tmp_path):
    """fallocate(FALLOC_FL_KEEP_SIZE, 10 GB): blocks on disk, st_size 0, no FSIZE limit."""
    code = (
        'my $out = $ARGV[0]; open(my $h, ">", "$out/k") or die;'
        f" $! = 0; my $r = syscall({seccomp.number('fallocate')}, fileno($h), 1, 0, 10240 << 20);"
        ' my @s = stat("$out/k"); print(($r == 0 ? "RESERVED" : ($!+0)), ":$s[12]");'
    )
    outcome, text = run_perl(code, tmp_path)
    assert outcome.ok and text == "1:0", text  # EPERM, no blocks
    assert sum(f.stat().st_blocks for f in (tmp_path / "out").iterdir()) == 0


def test_the_budget_counts_blocks_as_well_as_size(tmp_path):
    import ctypes

    path = tmp_path / "reserved"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        if libc.fallocate(fd, 1, 0, ctypes.c_longlong(50 << 20)) != 0:  # KEEP_SIZE
            pytest.skip("this filesystem cannot reserve space")
    finally:
        os.close(fd)
    assert path.stat().st_size == 0 and path.stat().st_blocks * 512 >= 50 << 20
    assert sandbox._tree_usage((tmp_path,), 100)[0] >= 50 << 20


def test_a_shared_mapping_of_a_deleted_file_still_counts_against_the_budget(tmp_path):
    code = (
        'my $out = $ARGV[0]; my $chunk = "B" x (1 << 20); my @maps;'
        ' for my $i (1..30) { my $f = "$out/g$i"; open(my $h, "+>", $f) or last; binmode $h;'
        " for (1..45) { print $h $chunk } $h->flush;"
        f" my $a = syscall({9 if platform.machine() == 'x86_64' else 222},"
        " 0, 45 << 20, 1, 1, fileno($h), 0);"
        " last if $a == -1; push @maps, $a; close $h; CORE::unlink($f); }"
        " sleep 30;"
    )
    start = time.monotonic()
    outcome, _ = run_perl(
        code, tmp_path,
        limits=sandbox.Limits(wall_s=30, out_bytes=100_000_000, file_bytes=50_000_000),
    )  # fmt: skip
    assert outcome.over_quota and time.monotonic() - start < 15


def test_the_dynamic_loader_can_run_a_file_the_program_wrote_but_it_stays_confined(tmp_path):
    """Landlock does not check PROT_EXEC mappings, and the kernel needs the right to execute the
    loader for every program, so a file written to the output folder can be run through
    `ld.so`. That is native code under the same ruleset and filter as Perl: it cannot read what
    Perl cannot, and cannot make the calls Perl cannot (docs/security.md, residual risks)."""
    loader = next(
        (
            p
            for p in ("/lib64/ld-linux-x86-64.so.2", "/lib/ld-linux-aarch64.so.1")
            if Path(p).exists()
        ),
        None,
    )
    if loader is None:
        pytest.skip("no dynamic loader at the usual place")
    code = (
        'my $out = $ARGV[0]; my $dst = "$out/mycat"; open(my $s, "<", "/usr/bin/cat") or die;'
        " binmode $s; my $d = do { local $/; scalar readline($s) };"
        ' open(my $o, ">", $dst) or die; binmode $o; print $o $d; close $o; chmod(0755, $dst);'
        ' $! = 0; my $direct = system($dst, "/usr/bin/true"); my $e1 = $!+0;'
        f' my $ok = system("{loader}", $dst, "/usr/share/zoneinfo/UTC") >> 8;'
        f' my $secret = system("{loader}", $dst, "/etc/passwd") >> 8;'
        f' my $proc = system("{loader}", $dst, "/proc/1/environ") >> 8;'
        ' open(my $r, ">", "$out/result"); '
        'print $r "direct:" . ($direct == 0 ? "RAN" : "no");'
        ' print $r ",ld_ok:$ok,passwd:$secret,proc:$proc";'
    )
    outcome, text = run_perl(code, tmp_path)
    assert outcome.ok, text
    result = (tmp_path / "out" / "result").read_text()
    got = dict(item.split(":") for item in result.split(","))
    assert got["direct"] == "no"  # no execute right on the output folder
    assert got["passwd"] != "0" and got["proc"] != "0"  # still no /etc/passwd, no /proc


def test_a_program_that_prints_the_busy_text_is_not_busy(tmp_path):
    """`busy` comes from the launcher's own observation, never from what the program prints or
    how it exits: a program that fails with the errno text, or exits with any status, is a normal
    failure."""
    spoof = 'print STDERR "Cannot fork: Resource temporarily unavailable\\n"; exit 3;'
    outcome, text = run_perl(spoof, tmp_path)
    assert not outcome.ok and "Resource temporarily unavailable" in text and not outcome.busy
    loop = (
        'my @k; for (1..40) { my $p = fork(); if (!defined $p) { die "Cannot fork: $!\\n" }'
        " if ($p == 0) { sleep 5; exit 0 } push @k, $p }"
    )
    own_limit, text = run_perl(loop, tmp_path, limits=sandbox.Limits(wall_s=20, pids=4))
    assert not own_limit.ok and "Resource temporarily unavailable" in text
    assert not own_limit.busy  # the program used its own allowance: its own limit, not a busy host


def test_a_document_that_prints_the_busy_text_is_a_normal_conversion_failure(monkeypatch):
    from readablestem.config import get_settings
    from readablestem.models.fake import FakeVisionClient
    from readablestem.source import convert, envelope, latexml

    if not latexml.installed():
        pytest.skip("needs LaTeXML")
    monkeypatch.setenv(sandbox.BACKEND_ENV, "landlock")
    sandbox.backend.cache_clear()
    try:
        tex = (
            "\\documentclass{article}\\begin{document}"
            "\\typeout{Cannot fork: Resource temporarily unavailable}\\end{document}"
        )
        blob = envelope.wrap("tex", "a.tex", None, tex.encode())
        with pytest.raises(convert.SourceFailed) as caught:
            convert.convert_source(blob, settings=get_settings(), client=FakeVisionClient())
    finally:
        monkeypatch.undo()
        sandbox.backend.cache_clear()
    assert caught.value.code in {"source_unreadable", "source_failed"}  # never source_busy


def _launch(tmp_path, code: str, *, adjust: int = 0):
    """Start the launcher directly on a Perl program; (return code, busy marker exists)."""
    import json

    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    marker = tmp_path / "busy"
    spec = sandbox._landlock_spec(
        [PERL, "-e", code], ro=(), rw=((out, "/out"),), tools=(), env=None, cwd="/",
        limits=sandbox.Limits(wall_s=20, pids=4), hide=(), scratch=out, stubs=out,
        program=seccomp.program(strict=True), busy_marker=str(marker),
    )  # fmt: skip
    spec["limits"]["nproc_adjust"] = adjust
    done = subprocess.run(
        [sys.executable, "-I", "-S", str(sandbox.LAUNCHER), json.dumps(spec)],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    return done.returncode, marker.exists()


def test_a_machine_with_no_room_for_the_programs_tasks_is_busy(tmp_path):
    """The user's task count is over the limit while the program has used none of its allowance:
    its fork fails and the launcher says so (the limit is lowered by the test, so that nothing
    else on the machine is affected)."""
    code = (
        'sleep 1; my $p = fork(); die "no fork: $!\\n" unless defined $p; exit 0 if $p == 0; wait;'
    )
    status, busy = _launch(tmp_path, code, adjust=-100_000)
    assert status != 0 and busy


def test_the_same_program_is_not_busy_when_there_is_room(tmp_path):
    code = 'my $p = fork(); die "no fork: $!\\n" unless defined $p; exit 0 if $p == 0; wait;'
    assert _launch(tmp_path, code) == (0, False)


def test_a_busy_machine_is_its_own_failure_code_for_a_conversion(tmp_path, monkeypatch):
    from readablestem.source import convert, latexml

    monkeypatch.setattr(latexml, "installed", lambda: True)
    monkeypatch.setattr(latexml, "convert", lambda *a, **k: sandbox.Outcome(1, busy=True))
    with pytest.raises(convert.SourceFailed) as caught:
        convert.run_latexml(tmp_path, "main.tex", tmp_path / "out")
    assert caught.value.code == "source_busy"
    assert "source_busy" in convert.FAILURE_CODES


def test_a_filter_that_denies_fallocate_does_not_stop_a_real_conversion(tmp_path, monkeypatch):
    from readablestem.source import latexml

    if not latexml.installed():
        pytest.skip("needs LaTeXML")
    monkeypatch.setenv(sandbox.BACKEND_ENV, "landlock")
    sandbox.backend.cache_clear()
    try:
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.tex").write_text(
            "\\documentclass{article}\\usepackage{amsmath}\\begin{document}"
            "\\begin{align}a&=b\\end{align}\\end{document}"
        )
        outcome = latexml.convert(src, "main.tex", tmp_path / "out")
    finally:
        monkeypatch.undo()
        sandbox.backend.cache_clear()
    assert outcome.ok and "fallocate" in seccomp._STRICT_DENIED


# ------------------------------------------------- reaping: nothing is left for pid 1


def _alive(tag: str) -> bool:
    ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    return tag in ps


def test_told_to_stop_the_supervisor_kills_and_reaps_the_whole_tree_itself(tmp_path):
    """SIGTERM to the supervisor alone (not its group): the program, its children and a
    double-forked orphan are all gone, and the supervisor ends, with nobody else killing anyone."""
    import json
    import signal

    out = tmp_path / "out"
    out.mkdir()
    code = (
        'my $p = fork(); if ($p == 0) { my $q = fork(); if ($q == 0) { exec("/bin/sleep", "901") }'
        ' exec("/bin/sleep", "902") } exec("/bin/sleep", "903");'
    )
    spec = sandbox._landlock_spec(
        [PERL, "-e", code], ro=(), rw=((out, "/out"),), tools=(), env=None, cwd="/",
        limits=sandbox.Limits(wall_s=60, pids=8), hide=(), scratch=out, stubs=out,
        program=seccomp.program(strict=True),
    )  # fmt: skip
    proc = subprocess.Popen(
        [sys.executable, "-I", "-S", str(sandbox.LAUNCHER), json.dumps(spec)],
        stdin=subprocess.DEVNULL, start_new_session=True,
    )  # fmt: skip
    for _ in range(50):
        time.sleep(0.1)
        if _alive("sleep 901") and _alive("sleep 902") and _alive("sleep 903"):
            break
    assert _alive("sleep 901") and _alive("sleep 903")
    os.kill(proc.pid, signal.SIGTERM)  # only the supervisor
    assert proc.wait(10) == 143
    time.sleep(0.3)
    assert not (_alive("sleep 901") or _alive("sleep 902") or _alive("sleep 903"))


def test_a_timeout_asks_the_supervisor_to_stop_before_the_group_is_killed(tmp_path, monkeypatch):
    sent: list[tuple[str, int]] = []
    real_kill, real_killpg = os.kill, os.killpg
    monkeypatch.setattr(
        sandbox.os, "kill", lambda pid, sig: (sent.append(("kill", sig)), real_kill(pid, sig))[1]
    )
    monkeypatch.setattr(
        sandbox.os,
        "killpg",
        lambda pg, sig: (sent.append(("killpg", sig)), real_killpg(pg, sig))[1],
    )
    outcome, _ = run_perl('system("/bin/sleep", "904");', tmp_path, limits=sandbox.Limits(wall_s=1))
    assert outcome.timed_out
    assert sent[0] == ("kill", int(signal.SIGTERM))  # the supervisor, first
    assert not _alive("sleep 904")


# ---------------------------------------- the tree is bounded whatever the user's other tasks do


def _drop_scenario():
    """400 threads of this process exist while the launcher works out its limit, and are gone one
    second later (probe `drop154.py` of the review): the limit then gives room."""
    release = threading.Event()
    waiters = [threading.Thread(target=release.wait, daemon=True) for _ in range(400)]
    for thread in waiters:
        thread.start()
    timer = threading.Timer(1.0, release.set)
    timer.start()
    return release, timer


def test_a_program_that_waits_for_room_to_appear_is_killed_for_having_too_many_tasks(tmp_path):
    release, timer = _drop_scenario()
    code = (
        "$| = 1; sleep 2; my $n = 0;"
        " for (1..2000) { my $p = fork(); last unless defined $p;"
        " if ($p == 0) { sleep 4; exit 0 } $n++ }"
        ' print "made $n\\n"; sleep 3;'
    )
    started = time.monotonic()
    try:
        outcome, text = run_perl(code, tmp_path, limits=sandbox.Limits(wall_s=30, pids=4))
    finally:
        release.set()
        timer.cancel()
    assert outcome.over_tasks and not outcome.ok, (outcome, text)
    # (the supervisor names the step; when the caller's poll gets there first there is none)
    assert outcome.step in ("over_tasks", "") and time.monotonic() - started < 10
    # How many it made before the next check is however much room appeared in that interval (here
    # a few hundred: the review's probe saw 411); the tree is then killed, not left to 2000.
    made = int(text.split("made ")[1].split()[0]) if "made " in text else 0
    assert made < 2000


def test_the_callers_poll_is_the_backstop_when_the_supervisor_does_not_catch_it(tmp_path):
    """Without the supervisor's own check, the poll in `sandbox.run` still ends the run."""
    assert sandbox.Limits(pids=4).task_bound("landlock") == 4 + sandbox.LANDLOCK_NPROC_SLACK
    source = Path(sandbox.__file__).read_text()
    assert "_tree_tasks(tree - {proc.pid}) > limits.task_bound" in source


def test_a_program_within_its_allowance_is_not_killed(tmp_path):
    code = (
        "my @k; for (1..2) { my $p = fork(); die 'nofork' unless defined $p;"
        " if ($p == 0) { sleep 1; exit 0 } push @k, $p } waitpid($_, 0) for @k;"
    )
    outcome, text = run_perl(code, tmp_path, limits=sandbox.Limits(wall_s=20, pids=4))
    assert outcome.ok and not outcome.over_tasks, text


def test_the_probe_checks_that_growth_is_killed(tmp_path, monkeypatch):
    from readablestem.ops import sandbox_probe

    monkeypatch.setenv(sandbox.BACKEND_ENV, "landlock")
    sandbox.backend.cache_clear()
    try:
        assert sandbox_probe._task_growth_killed(tmp_path) is True
    finally:
        monkeypatch.undo()
        sandbox.backend.cache_clear()
