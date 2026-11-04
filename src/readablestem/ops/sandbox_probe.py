"""Does the sandbox for uploaded TeX work on this machine? (`readablestem ops sandbox-probe`.)

A LaTeX upload is a program written by a stranger. `core/sandbox.py` runs LaTeXML inside
bubblewrap: no network, a minimal root, no environment, memory and time caps. That needs
unprivileged user namespaces, which a hosting platform may forbid, so the answer has to come from
the deployed image, not from a laptop. Run this once after each image change that touches the
sandbox or the base image:

    railway ssh -s web -- readablestem ops sandbox-probe

It prints one `name=value` line per check and a last line that is `PASS` or `FAIL`. The exit
status is 0 for PASS and 1 for FAIL. Nothing it prints is document content.

What it checks, and what each answer means:

- `backend`: which sandbox this machine uses, `bwrap` or, where bubblewrap cannot start (no
  unprivileged user namespaces), `landlock` (docs/security.md, "Landlock backend"); `bwrap`,
  `prlimit` and `landlock_abi` say what is installed. `sandbox_start`: the chosen one can start.
  On the Landlock backend also `sandbox_no_proc`, `sandbox_outside_readonly`,
  `sandbox_seccomp_strict` and `dumpable0_protects_environ`.
- `sandbox_env`, `sandbox_no_secrets`, `sandbox_no_etc`, `sandbox_no_network`: inside it, a
  secret set in this process's environment is absent, `/etc/passwd` and the application are not
  visible, `/proc/1/environ` is empty, and a socket cannot reach an address.
- `sandbox_memory_cap`, `sandbox_wall_cap`, `sandbox_output_cap`: the limits are enforced (an
  allocation past the address-space cap fails; a sleeping program is killed at the wall limit; a
  program that fills its output folder is killed).
- `latexml`: a one-line document converted inside the sandbox, with the time it took
  (`missing` when the image was built without LaTeXML; that does not fail the probe unless
  `--require-latexml` is given).
- Information only: the kernel's seccomp mode of this process, the Landlock ABI, whether a
  same-UID process can read `/proc/<pid>/environ` of one that has and has not called
  `PR_SET_DUMPABLE=0`, and whether a plain child of this process could unshare a network
  namespace without bubblewrap (the fallback the plan considered; `fallback=` says what it would
  and would not give: it shows no files, so it is never accepted and is never used).

PASS means every sandbox check held. If it fails, uploaded TeX must not be run in this container
(the alternative is a separate service with no secrets: docs/deploy-railway.md).
"""

from __future__ import annotations

import ctypes
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from readablestem.core import sandbox, seccomp

SECRET_NAME = "READABLESTEM_PROBE_SECRET"
SECRET_VALUE = "probe-secret-value-1234"

_PR_SET_DUMPABLE = 4
_SYS_LANDLOCK_CREATE_RULESET = 444  # x86-64 and arm64 share the number
_LANDLOCK_CREATE_RULESET_VERSION = 1
_CLONE_NEWUSER = 0x10000000
_CLONE_NEWNET = 0x40000000

# Run inside the sandbox: one "name:value" line per fact.
INSIDE_PERL = r"""
use Socket;
my $out = $ARGV[0];
my %seen = map { $_ => 1 } grep { !/^(PATH|PWD|HOME|SHLVL|_|TMPDIR)$/ } keys %ENV;
print "keys:", join(",", sort keys %seen), "\n";
my $all = join("", values %ENV);
print "secret:", ($all =~ /probe-secret/ ? "visible" : "absent"), "\n";
for my $p ("/etc/passwd", "/etc/shadow", "/app/pyproject.toml", "/proc/self/environ") {
    if ($p eq "/proc/self/environ") { next }
    print "vis_$p:", (open(V, "<", $p) ? "yes" : "no"), "\n";
}
my $n = 0;
if (open(F, "<", "/proc/1/environ")) { local $/; my $x = <F>; $n = length($x // ""); }
print "pid1_environ_bytes:", $n, "\n";
my $m = 0;
my $pp = getppid();
if (open(G, "<", "/proc/$pp/environ")) { local $/; my $x = <G>; $m = length($x // ""); }
print "ppid_environ_bytes:", $m, "\n";
my $net = "none";
if (socket(S, PF_INET, SOCK_STREAM, 0)) {
    $net = connect(S, sockaddr_in(80, inet_aton("1.1.1.1"))) ? "reached" : "unreachable";
}
print "net:", $net, "\n";
open(W, ">", "$out/w") && print W "x"; close W;
print "out_writable:", (-s "$out/w" ? "yes" : "no"), "\n";
print "root_writable:", (open(R, ">", "/usr/probe") ? "yes" : "no"), "\n";
my $beside = $out; $beside =~ s{/[^/]*$}{};
print "outside_writable:", (open(X, ">", "$beside/escape-probe") ? "yes" : "no"), "\n";
"""

TINY_TEX = (
    "\\documentclass{article}\n\\begin{document}\n\\section{Probe}\n"
    "A line with $x^2+\\frac{a}{b}$.\n\\end{document}\n"
)


@dataclass
class Check:
    name: str
    value: str  # "ok", "fail", "skip", or an informational value
    required: bool = True

    @property
    def failed(self) -> bool:
        return self.required and self.value == "fail"

    def line(self) -> str:
        return f"{self.name}={self.value}"


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, value: str, *, required: bool = True) -> None:
        self.checks.append(Check(name, value, required))

    @property
    def passed(self) -> bool:
        return not any(c.failed for c in self.checks)

    def lines(self) -> list[str]:
        return [c.line() for c in self.checks] + ["PASS" if self.passed else "FAIL"]


# ---------------------------------------------------------------------------- inside the sandbox


def _perl(
    code: str,
    *,
    tmp: Path,
    wall_s: int = 20,
    memory_bytes: int | None = None,
    limits: sandbox.Limits | None = None,
):
    """Run a Perl one-liner in the sandbox with a writable output folder, its path the first
    argument (`$ARGV[0]`); returns (outcome, its output).
    Perl is in every Debian image (perl-base)."""
    out = tmp / "out"
    out.mkdir(exist_ok=True)
    log = tmp / "perl.log"
    limits = limits or sandbox.Limits(
        wall_s=wall_s, **({"memory_bytes": memory_bytes} if memory_bytes else {})
    )
    outcome = sandbox.run(
        ["/usr/bin/perl", "-e", code, sandbox.where(out, "/out")],
        rw=((out, "/out"),),
        env={"HOME": "/tmp"},
        limits=limits,
        log=log,
    )
    text = log.read_text(errors="replace") if log.exists() else ""
    return outcome, text


def _check_sandbox(report: Report, tmp: Path) -> bool:
    report.add("bwrap", "ok" if sandbox.bwrap_path() else "missing", required=False)
    report.add("prlimit", "ok" if sandbox.prlimit_path() else "missing", required=False)
    abi = sandbox.landlock_abi()
    report.add("landlock_abi", str(abi) if abi else "unavailable", required=False)
    name = sandbox.backend()
    report.add("backend", name or "none", required=False)
    _landlock_step(report, name)
    if name is None:  # neither bubblewrap nor Landlock can start here
        report.add("sandbox_start", "fail")
        return False
    # os.environ is passed to nobody: bubblewrap gets only PATH, the program only what we name.
    os.environ[SECRET_NAME] = SECRET_VALUE
    try:
        outcome, text = _perl(INSIDE_PERL, tmp=tmp)
    finally:
        os.environ.pop(SECRET_NAME, None)
    start_ok = outcome.ok
    report.add("sandbox_start", "ok" if start_ok else "fail")
    if not start_ok:
        # The first line of bubblewrap's complaint is a category, never document content.
        first = (text.strip().splitlines() or ["no output"])[0][:160]
        report.add("sandbox_start_error", first, required=False)
        return False
    seen = dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
    report.add("sandbox_env", "ok" if seen.get("keys") == "" else "fail")
    report.add("sandbox_no_secrets", "ok" if seen.get("secret") == "absent" else "fail")
    visible = [k for k in seen if k.startswith("vis_") and seen[k] != "no"]
    report.add("sandbox_no_etc", "ok" if not visible else "fail")
    report.add("sandbox_pid1_environ", "ok" if seen.get("pid1_environ_bytes") == "0" else "fail")
    report.add("sandbox_no_proc", "ok" if seen.get("ppid_environ_bytes") == "0" else "fail")
    report.add("sandbox_no_network", "ok" if seen.get("net") in ("unreachable", "none") else "fail")
    report.add("sandbox_output_writable", "ok" if seen.get("out_writable") == "yes" else "fail")
    report.add("sandbox_root_readonly", "ok" if seen.get("root_writable") == "no" else "fail")
    # Landlock: nothing beside the job's folder is writable. (Under bubblewrap the root is a
    # throwaway tmpfs the program may write to; it is not the host's.)
    outside_ok = seen.get("outside_writable") == "no"
    report.add(
        "sandbox_outside_readonly",
        "ok" if outside_ok else ("fail" if name == "landlock" else "tmpfs"),
        required=name == "landlock",
    )
    return True


def _check_limits(report: Report, tmp: Path) -> None:
    # Memory: ask for 300 MB under a 128 MB address-space cap; Perl must fail to allocate.
    outcome, text = _perl(
        'my $x = eval { "a" x 300_000_000 }; print defined($x) ? "allocated" : "refused", "\\n";',
        tmp=tmp,
        memory_bytes=128_000_000,
    )
    refused = "refused" in text or not outcome.ok
    report.add("sandbox_memory_cap", "ok" if refused and "allocated" not in text else "fail")
    # Wall clock: a program that sleeps is killed at the limit.
    start = time.monotonic()
    outcome, _ = _perl("sleep 60;", tmp=tmp, wall_s=2)
    elapsed = time.monotonic() - start
    report.add("sandbox_wall_cap", "ok" if outcome.timed_out and elapsed < 15 else "fail")
    # Output budget: a program that writes past the budget is killed.
    out = tmp / "out2"
    out.mkdir(exist_ok=True)
    limits = sandbox.Limits(wall_s=30, out_bytes=1_000_000, file_bytes=50_000_000)
    outcome = sandbox.run(
        [
            "/usr/bin/perl",
            "-e",
            'open(W, ">", "$ARGV[0]/big"); while (1) { print W "x" x 100000; }',
            sandbox.where(out, "/out"),
        ],
        rw=((out, "/out"),),
        limits=limits,
    )
    report.add("sandbox_output_cap", "ok" if outcome.over_quota or not outcome.ok else "fail")
    # Tasks: a program that forks without end is stopped at the limit.
    _outcome, text = _perl(
        "my $n = 0; for (1..200) { my $p = fork(); last unless defined $p; "
        "if ($p == 0) { sleep 3; exit 0 } $n++; } print $n;",
        tmp=tmp,
        limits=sandbox.Limits(wall_s=20, pids=16),
    )
    # (Landlock: the user's own threads start and stop meanwhile, so a few more are allowed.)
    allowed = sandbox.Limits(pids=16).task_bound(sandbox.backend())
    report.add(
        "sandbox_task_cap", "ok" if text.strip().isdigit() and int(text) <= allowed else "fail"
    )
    # Files: opens stop at the limit.
    _outcome, text = _perl(
        'my $n = 0; for (1..600) { open(my $f, "<", "/usr/bin/perl") or last; push @h, $f; $n++ }'
        " print $n;",
        tmp=tmp,
    )
    report.add(
        "sandbox_file_cap",
        "ok" if text.strip().isdigit() and int(text) <= sandbox.NOFILE else "fail",
    )
    # Memory of the group together: three processes that each hold 100 MB under a group cap of
    # 150 MB are killed (the poll in `sandbox.run`, which bounds the sum where no cgroup does).
    outcome, _ = _perl(
        "my @k; for (1..3) { my $p = fork(); next unless defined $p; if ($p == 0) "
        '{ my $x = "a" x 100_000_000; sleep 20; exit 0 } push @k, $p } sleep 20;',
        tmp=tmp,
        limits=sandbox.Limits(
            wall_s=20, memory_bytes=300_000_000, pids=8, group_memory_bytes=150_000_000
        ),
    )
    report.add("sandbox_group_memory_cap", "ok" if outcome.over_memory else "fail")
    # seccomp: ptrace answers EPERM inside (the filter, `core/seccomp.py`).
    if seccomp.supported():
        _outcome, text = _perl(
            f"my $r = syscall({seccomp.number('ptrace')}, 0, 0, 0, 0); use Errno qw(EPERM);"
            'print(($r < 0 && $! == EPERM) ? "denied" : "allowed");',
            tmp=tmp,
        )
        report.add("sandbox_seccomp", "ok" if text.strip() == "denied" else "fail")
        if sandbox.backend() == "landlock":  # the strict filter: no sockets, no setsid
            _outcome, text = _perl(
                f"my $r = syscall({seccomp.number('socket')}, 2, 1, 0); use Errno qw(EPERM);"
                'print(($r < 0 && $! == EPERM) ? "denied" : "allowed");',
                tmp=tmp,
            )
            report.add("sandbox_seccomp_strict", "ok" if text.strip() == "denied" else "fail")
    else:
        report.add("sandbox_seccomp", "unsupported", required=False)
    # cgroup v2: a child cgroup with memory and pids limits, when the container delegates one.
    group = sandbox.Cgroup.make(sandbox.Limits())
    report.add("sandbox_cgroup", "ok" if group is not None else "unavailable", required=False)
    if group is not None:
        group.remove()
    _check_total_bound(report, cgroup=group is not None)


def total_bound_mb(*, cgroup: bool) -> int:
    """What a source conversion can map in all, in MB: the cgroup's memory limit when there is a
    cgroup, else the tasks it may have (`Limits.task_bound` for the backend in use) times the
    address space each may map: a hard bound on what the kernel will give, though a loose one
    (address space is more than memory in use). The resident-memory poll
    (`Limits.group_memory_bytes`, `sandbox_group_poll_cap_mb`) kills the group earlier than that,
    every 0.1 s, so a burst between two polls is what the tasks can touch in 0.1 s."""
    from readablestem.source import latexml

    limits = latexml.LIMITS
    total = (
        limits.memory_bytes
        if cgroup
        else limits.task_bound(sandbox.backend()) * limits.memory_bytes
    )
    return -(-total // (1024 * 1024))


def _check_total_bound(report: Report, *, cgroup: bool) -> None:
    """`sandbox_total_bound_mb`, checked against `SOURCE_SANDBOX_MAX_TOTAL_MB`
    (`sandbox_total_bound` fails when it is over; only `--require-latexml` makes that fatal)."""
    from readablestem.config import get_settings

    bound = total_bound_mb(cgroup=cgroup)
    report.add("sandbox_total_bound_mb", str(bound), required=False)
    from readablestem.source import latexml

    poll_cap = latexml.LIMITS.group_memory_bytes // (1024 * 1024)
    report.add("sandbox_group_poll_cap_mb", str(poll_cap), required=False)
    allowed = get_settings().source_sandbox_max_total_mb
    report.add("sandbox_total_bound", "ok" if bound <= allowed else "fail", required=False)


# ------------------------------------------------------------------------------- LaTeXML run


def _check_latexml(report: Report, tmp: Path, *, require: bool) -> None:
    from readablestem.source import latexml  # imported late: the module is part of the source path

    if not latexml.installed():
        report.add("latexml", "fail" if require else "missing", required=require)
        return
    work = tmp / "tex"
    work.mkdir(exist_ok=True)
    (work / "main.tex").write_text(TINY_TEX, encoding="utf-8")
    out = tmp / "latexml-out"
    start = time.monotonic()
    result = latexml.convert(work, "main.tex", out)
    elapsed = time.monotonic() - start
    html = out / latexml.DOC_PATH
    good = result.ok and html.is_file() and "<math" in html.read_text(errors="replace")
    report.add("latexml", "ok" if good else "fail")
    report.add("latexml_seconds", f"{elapsed:.1f}", required=False)


# ------------------------------------------------------------------------------- information


def landlock_abi() -> str:
    """The Landlock ABI version the kernel reports, or the reason it does not."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        rc = libc.syscall(_SYS_LANDLOCK_CREATE_RULESET, None, 0, _LANDLOCK_CREATE_RULESET_VERSION)
    except (OSError, AttributeError):
        return "unknown"
    if rc >= 0:
        return str(rc)
    return f"unavailable-errno-{ctypes.get_errno()}"


def seccomp_mode() -> str:
    """The seccomp mode of this process from /proc/self/status (0 none, 1 strict, 2 filter)."""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("Seccomp:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def _readable_environ(dumpable: bool) -> bool:
    """Start a same-UID process holding a marker variable, with PR_SET_DUMPABLE left alone or set
    to 0, and report whether this process can read its /proc/<pid>/environ."""
    code = (
        "import ctypes,sys,time\n"
        + ("" if dumpable else f"ctypes.CDLL(None).prctl({_PR_SET_DUMPABLE}, 0, 0, 0, 0)\n")
        + "print('ready', flush=True)\ntime.sleep(30)\n"
    )
    env = {"PATH": os.environ.get("PATH", "/usr/bin"), SECRET_NAME: SECRET_VALUE}
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", code],
        env=env,
        stdout=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    try:
        assert proc.stdout is not None
        proc.stdout.readline()
        try:
            data = Path(f"/proc/{proc.pid}/environ").read_bytes()
        except OSError:
            return False
        return SECRET_VALUE.encode() in data
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait()


def _plain_child_can_unshare_net() -> str:
    """Whether a child of this process, with no bubblewrap, can give itself a new network
    namespace. If it can, a plain child could be cut off the network; it still sees every file."""
    code = (
        "import ctypes, sys\n"
        f"sys.exit(0 if ctypes.CDLL(None).unshare({_CLONE_NEWUSER | _CLONE_NEWNET}) == 0 else 1)\n"
    )
    try:
        run = subprocess.run(
            [sys.executable, "-I", "-c", code], stdin=subprocess.DEVNULL, capture_output=True,
            timeout=20, env={"PATH": os.environ.get("PATH", "/usr/bin")},
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return "no"
    return "yes" if run.returncode == 0 else "no"


def _landlock_step(report: Report, name: str | None) -> None:
    """Why the Landlock backend does not start, when it does not: the step the launcher named (a
    fixed code, `sandbox.backend_failure`), the errno's name and the launcher's exit status."""
    failure = sandbox.backend_failure("landlock")
    if name == "landlock":
        report.add("landlock_step", "ok", required=False)
    elif failure is None:
        report.add("landlock_step", "not_tried", required=False)
    else:
        step, errno_name, status = failure
        report.add("landlock_step", step, required=False)
        if errno_name:
            report.add("landlock_errno", errno_name, required=False)
        report.add("landlock_exit", str(status), required=False)


# Each runs once in a throwaway child (the children inherit this container's own seccomp filter, so
# the answer is what the launcher would meet): the system calls the Landlock launcher needs.
_CAN_CHILD = r"""
import ctypes, errno, platform, struct, sys
libc = ctypes.CDLL(None, use_errno=True)
arm = platform.machine() != "x86_64"
SECCOMP, PRLIMIT = (277, 261) if arm else (317, 302)
what = sys.argv[1]
def result(rc):
    print("yes" if rc >= 0 else "no " + errno.errorcode.get(ctypes.get_errno(), "EUNKNOWN"))
def nnp():
    return libc.prctl(38, 1, 0, 0, 0)
def filt():
    f = struct.pack("=HBBI", 0x06, 0, 0, 0x7FFF0000)  # return ALLOW
    buf = ctypes.create_string_buffer(f, len(f))
    prog = struct.pack("=HxxxxxxQ", 1, ctypes.addressof(buf))
    return ctypes.create_string_buffer(prog, len(prog)), buf
if what == "landlock_create_ruleset":
    result(libc.syscall(444, None, 0, 1))
elif what == "no_new_privs":
    result(nnp())
elif what == "landlock_restrict_self":
    attr = struct.pack("=Q", 1)  # handles EXECUTE; no rule: an empty ruleset
    fd = libc.syscall(444, attr, 8, 0)
    if fd < 0:
        result(fd)
    elif nnp() < 0:
        result(-1)
    else:
        result(libc.syscall(446, fd, 0))
elif what == "seccomp_syscall":
    nnp()
    holder, buf = filt()
    result(libc.syscall(SECCOMP, 1, 0, ctypes.c_void_p(ctypes.addressof(holder))))
elif what == "seccomp_prctl":
    nnp()
    holder, buf = filt()
    result(libc.prctl(22, 2, ctypes.c_void_p(ctypes.addressof(holder)), 0, 0))
elif what == "prlimit_self":
    cur = ctypes.create_string_buffer(16)
    if libc.syscall(PRLIMIT, 0, 4, None, cur) < 0:
        result(-1)
    else:
        result(libc.syscall(PRLIMIT, 0, 4, cur, None))
"""
_CAN = (
    "landlock_create_ruleset",
    "landlock_restrict_self",
    "no_new_privs",
    "seccomp_syscall",
    "seccomp_prctl",
    "prlimit_self",
)


def can_do(name: str) -> tuple[bool, str]:
    """Whether the system call behind `name` works here, tried once in a child of its own:
    (answer, errno's name when not)."""
    try:
        run = subprocess.run(
            [sys.executable, "-I", "-S", "-c", _CAN_CHILD, name],
            capture_output=True,
            text=True,
            timeout=20,
            stdin=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "ESTART"
    answer = run.stdout.strip()
    if answer == "yes":
        return True, ""
    if answer.startswith("no E") and answer[3:].replace(" ", "").isalnum():
        return False, answer[3:].strip()
    return False, "EFAILED"


def _can_checks(report: Report) -> None:
    for name in _CAN:
        ok, errno_name = can_do(name)
        report.add(f"can_{name}", "yes" if ok else "no", required=False)
        if not ok:
            report.add(f"can_{name}_errno", errno_name, required=False)


def _information(report: Report) -> None:
    _can_checks(report)
    report.add("seccomp_mode", seccomp_mode(), required=False)
    report.add("user", f"uid-{os.getuid()}", required=False)
    report.add("pid1_environ_readable", "yes" if _can_read_pid1() else "no", required=False)
    report.add("init_process", _init_process(), required=False)
    report.add(
        "same_uid_environ_readable_default",
        "yes" if _readable_environ(True) else "no",
        required=False,
    )
    # On the Landlock backend the program shares this user: a process that called
    # PR_SET_DUMPABLE=0 must be unreadable to it, and this process must have called it.
    landlock = sandbox.backend() == "landlock"
    unreadable = not _readable_environ(False)
    report.add(
        "same_uid_environ_readable_dumpable0",
        "no" if unreadable else "yes",
        required=False,
    )
    if landlock:
        report.add("dumpable0_protects_environ", "ok" if unreadable else "fail")
    report.add("process_dumpable", _dumpable(), required=False)
    report.add("fallback_unshare_net", _plain_child_can_unshare_net(), required=False)
    report.add("fallback", "never-used-shows-all-files", required=False)


def _init_process() -> str:
    """The name of pid 1 in this container (`tini` when the image's ENTRYPOINT is in force; the
    app's own name when it is pid 1 and nothing reaps the orphans of the sandbox)."""
    try:
        name = Path("/proc/1/comm").read_text().strip()
    except OSError:
        return "unknown"
    return name if name.replace("-", "").replace("_", "").isalnum() and len(name) <= 16 else "other"


def _dumpable() -> str:
    """PR_GET_DUMPABLE of this process: 0 once `sandbox.make_undumpable` was called."""
    try:
        return str(ctypes.CDLL(None).prctl(3, 0, 0, 0, 0))
    except OSError:
        return "unknown"


def _can_read_pid1() -> bool:
    try:
        Path("/proc/1/environ").read_bytes()
    except OSError:
        return False
    return True


# ------------------------------------------------------------------------------------- entry


def probe(*, require_latexml: bool = False) -> Report:
    report = Report()
    tmp = Path(tempfile.mkdtemp(prefix="sandbox-probe-"))
    try:
        started = _check_sandbox(report, tmp)
        if started:
            _check_limits(report, tmp)
            _check_latexml(report, tmp, require=require_latexml)
        else:
            report.add("latexml", "skip", required=False)
        _information(report)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if require_latexml:  # a total the settings do not allow is a failure when LaTeX is wanted
        for check in report.checks:
            if check.name == "sandbox_total_bound":
                check.required = True
    return report
