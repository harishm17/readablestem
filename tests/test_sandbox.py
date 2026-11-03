"""The sandbox for uploaded TeX (`readablestem.core.sandbox`).

`build_argv` is checked without a sandbox. The run tests start the real bubblewrap, with no
mocks, and are skipped where it is missing or cannot make a user namespace (some CI hosts)."""

from __future__ import annotations

import os
import shutil
import time

import pytest

from readablestem.core import sandbox

needs_sandbox = pytest.mark.skipif(
    not (shutil.which("bwrap") and shutil.which("prlimit") and sandbox.available()),
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


def test_argv_has_no_network_clean_env_and_limits(tmp_path):
    if not (shutil.which("bwrap") and shutil.which("prlimit")):
        pytest.skip("needs bubblewrap and prlimit to build the command")
    argv = sandbox.build_argv(
        ["/usr/bin/x", "a"],
        ro=((tmp_path, "/work"),),
        env={"K": "v"},
        limits=sandbox.Limits(memory_bytes=123, cpu_s=7, file_bytes=9),
    )
    assert "--unshare-all" in argv and "--clearenv" in argv and "--die-with-parent" in argv
    assert "--share-net" not in argv
    assert argv[argv.index("--ro-bind", argv.index("/work") - 2) + 2] == "/work"
    assert argv[argv.index("K") - 1 : argv.index("K") + 2] == ["--setenv", "K", "v"]
    assert "--as=123" in argv and "--cpu=7" in argv and "--fsize=9" in argv
    assert argv[-2:] == ["/usr/bin/x", "a"]
    assert str(tmp_path) in argv  # only the folder named for it is shared
    for private in ("/home", "/root", "/app", "/etc/passwd"):
        assert private not in argv


def test_missing_bubblewrap_is_refused_not_bypassed(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.build_argv(["/usr/bin/true"])
    sandbox.available.cache_clear()
    try:
        assert sandbox.available() is False
    finally:
        sandbox.available.cache_clear()


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
def test_no_application_home_passwd_or_proc1(tmp_path):
    outcome, text = perl(
        'for my $p ("/app", "/home", "/root", "/etc/passwd", "/etc/shadow") '
        '{ print "$p:", (-e $p ? "visible" : "absent"), "\\n"; }'
        'my $n = 0; if (open(F, "<", "/proc/1/environ")) { local $/; $n = length(<F> // ""); }'
        'print "pid1:$n\\n";',
        tmp_path,
    )
    assert outcome.ok
    assert "visible" not in text
    assert "pid1:0" in text


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
def test_cpu_cap_kills_a_busy_loop_before_the_wall_clock(tmp_path):
    outcome, _ = perl("1 while 1;", tmp_path, limits=sandbox.Limits(wall_s=60, cpu_s=1))
    assert not outcome.ok and not outcome.timed_out


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
def test_children_die_with_the_group(tmp_path):
    marker = tmp_path / "out" / "alive"
    outcome, _ = perl(
        'if (fork() == 0) { sleep 3; open(M, ">", "/out/alive"); close M; exit 0; } sleep 60;',
        tmp_path,
        limits=sandbox.Limits(wall_s=1),
    )
    assert outcome.timed_out
    time.sleep(4)
    assert not marker.exists()


def test_parent_environment_is_not_passed_to_bubblewrap(monkeypatch, tmp_path):
    seen = {}

    class Fake:
        returncode = 0

        def wait(self, timeout=None):
            return 0

    def popen(argv, **kwargs):
        seen.update(kwargs)
        return Fake()

    monkeypatch.setattr(sandbox, "bwrap_path", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(sandbox, "prlimit_path", lambda: "/usr/bin/prlimit")
    monkeypatch.setattr(sandbox.subprocess, "Popen", popen)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    sandbox.run(["/usr/bin/true"])
    assert seen["env"] == {"PATH": "/usr/bin:/bin"}
    assert seen["start_new_session"] is True
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test"
