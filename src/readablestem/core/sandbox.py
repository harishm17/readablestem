"""A bubblewrap sandbox for a program that reads a document someone else wrote.

LaTeXML (`source/`) is the first user: a `.tex` file is a program, and the one that runs it is
a Perl interpreter in the same container as the web app. What stops it reading the API key or
another person's file is not a setting of the program but the mount namespace this module
builds:

- the program sees a minimal root (the system's `/usr` and libraries read-only, nothing of the
  application, no `/home`, `/proc/1` hidden), one read-only input folder and one writable output
  folder, and tmpfs for `/tmp` and the home directory;
- no network (a fresh network namespace), a fresh pid namespace that dies with the caller, a
  new session, and an empty environment (only what the caller names);
- `prlimit` caps address space, CPU time and the size of any one file in front of the command,
  and the caller kills the whole group at a wall-clock limit or when the output folder grows past
  a byte budget.

When bubblewrap, user namespaces or the mounts are not available the call is refused with
`SandboxUnavailable`: there is no unsandboxed fallback (docs/security.md, "Source uploads").
`readablestem ops sandbox-probe` (ops/sandbox_probe.py) checks all of this on the deployed
image, because a platform may forbid user namespaces.

Nothing here reads or logs what the program printed: its output goes to a file the caller names.
"""

from __future__ import annotations

import contextlib
import errno
import functools
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

POLL_SECONDS = 0.2
# Processes (threads count) the program and what it starts may add to what this user already has,
# and open files. RLIMIT_NPROC counts every task of the user, the web app's threads included, so
# the limit is "now plus this much", worked out when the sandbox starts (`_nproc_limit`).
NPROC_EXTRA = 64
NOFILE = 256
TMPFS_BYTES = 128_000_000

# Read-only system trees; each is skipped when absent. No /home, /root, /var, /mnt or /srv.
_SYSTEM_TREES = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64")
# The few files and folders of /etc and /var/lib that Perl, TeX and ImageMagick read.
_SYSTEM_FILES = (
    "/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d", "/etc/alternatives",
    "/etc/perl", "/etc/texmf", "/etc/xml", "/etc/fonts", "/etc/ImageMagick-6",
    "/etc/ImageMagick-7", "/var/lib/texmf",
)  # fmt: skip


def _own_tasks() -> int:
    """Tasks (processes and threads) this user owns now: what RLIMIT_NPROC compares with."""
    uid = os.getuid()
    total = 0
    with contextlib.suppress(OSError):
        for entry in os.scandir("/proc"):
            if entry.name.isdigit():
                with contextlib.suppress(OSError):
                    if entry.stat().st_uid == uid:
                        total += len(os.listdir(f"/proc/{entry.name}/task"))
    return total


@functools.cache
def _nproc_counted_inside() -> bool:
    """Whether RLIMIT_NPROC counts a new user namespace's tasks from zero (kernels from 5.14: the
    count is per namespace), found by trying: a limit of 8 inside the sandbox lets some forks
    through and then stops. When it does, the limit can be small and absolute; when it counts the
    user's tasks outside as well (older kernels), forks would fail at once, so the limit has to
    be "tasks now plus some"."""
    bwrap, prlimit = bwrap_path(), prlimit_path()
    if bwrap is None or prlimit is None:
        return False
    program = (
        "my $n = 0; for (1..40) { my $p = fork(); last unless defined $p; "
        "if ($p == 0) { sleep 3; exit 0 } $n++; } print $n;"
    )
    argv = [
        bwrap, "--die-with-parent", "--unshare-all", *_system_mounts(), "--proc", "/proc",
        "--dev", "/dev", "--", prlimit, "--nproc=8", "--", "/usr/bin/perl", "-e", program,
    ]  # fmt: skip
    try:
        run = subprocess.run(
            argv, capture_output=True, timeout=20, stdin=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin"},
        )  # fmt: skip
        return 0 < int(run.stdout.strip() or 0) <= 8
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


@contextlib.contextmanager
def exclusive(name: str, timeout_s: float):
    """One holder at a time across every process of this machine (an `flock` on a file in the
    temporary folder): waits up to `timeout_s`, then raises `TimeoutError`. Used so that one
    source conversion runs at a time, which makes `tasks x address space` the bound for the whole
    server and not for each conversion."""
    import fcntl
    import tempfile

    path = Path(tempfile.gettempdir()) / f"readablestem-{name}.lock"
    handle = path.open("a")
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(name) from None
                time.sleep(0.1)
        yield
    finally:
        handle.close()


def nproc_limit(extra: int) -> int:
    """The `--nproc` for a sandbox: `extra` when the count starts from zero inside, else this
    user's tasks now plus `extra`."""
    return extra if _nproc_counted_inside() else _own_tasks() + extra


class SandboxUnavailable(RuntimeError):
    """The sandbox cannot be built here (no bubblewrap, no user namespaces, no prlimit)."""


@dataclass(frozen=True)
class Limits:
    wall_s: int = 120
    cpu_s: int = 120
    memory_bytes: int = 1_500_000_000
    file_bytes: int = 50_000_000
    out_bytes: int = 200_000_000  # all the program may leave in its writable folders and its log
    pids: int = NPROC_EXTRA  # tasks the program may have at once (a cgroup limit when there is one)


@dataclass
class Outcome:
    returncode: int
    timed_out: bool = False
    over_quota: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.over_quota


def bwrap_path() -> str | None:
    return shutil.which("bwrap")


def prlimit_path() -> str | None:
    return shutil.which("prlimit")


@functools.cache
def available() -> bool:
    """Whether a sandbox can be started here at all (bubblewrap, prlimit, user namespaces):
    `true` run inside one. Cached for the life of the process."""
    try:
        return run(["/usr/bin/true"], limits=Limits(wall_s=20)).ok
    except (SandboxUnavailable, OSError):
        return False


def _system_mounts() -> list[str]:
    args: list[str] = []
    for name in _SYSTEM_TREES:
        path = Path(name)
        if path.is_symlink():
            args += ["--symlink", os.readlink(path), name]
        elif path.exists():
            args += ["--ro-bind", name, name]
    for name in _SYSTEM_FILES:
        if Path(name).exists():
            args += ["--ro-bind", name, name]
    return args


def build_argv(
    command: list[str],
    *,
    ro: tuple[tuple[Path, str], ...] = (),
    rw: tuple[tuple[Path, str], ...] = (),
    env: dict[str, str] | None = None,
    cwd: str = "/",
    limits: Limits | None = None,
    seccomp_fd: int | None = None,
) -> list[str]:
    """The `bwrap` command line for `command` (a list, never a shell string). `ro` and `rw` are
    (host path, path inside) pairs; every path is absolute and `inside` never overlaps
    `/proc`, `/dev` or `/tmp`."""
    limits = limits or Limits()
    bwrap, prlimit = bwrap_path(), prlimit_path()
    if bwrap is None:
        raise SandboxUnavailable("bubblewrap (bwrap) is not installed")
    if prlimit is None:
        raise SandboxUnavailable("prlimit (util-linux) is not installed")
    args = [bwrap]
    if seccomp_fd is not None:
        args += ["--seccomp", str(seccomp_fd)]
    args += [
        "--die-with-parent", "--new-session", "--unshare-all", "--clearenv",
        *_system_mounts(), "--proc", "/proc", "--dev", "/dev",
        "--size", str(TMPFS_BYTES), "--tmpfs", "/tmp",
    ]  # fmt: skip
    for host, inside in ro:
        args += ["--ro-bind", str(host), inside]
    for host, inside in rw:
        args += ["--bind", str(host), inside]
    # bubblewrap is pid 1 of the sandbox: hide what it was started with.
    for hidden in ("/proc/1/environ", "/proc/1/cmdline"):
        args += ["--ro-bind", "/dev/null", hidden]
    for key, value in {"PATH": "/usr/local/bin:/usr/bin:/bin", **(env or {})}.items():
        args += ["--setenv", key, value]
    args += [
        "--chdir", cwd, "--",
        prlimit, f"--as={limits.memory_bytes}", f"--cpu={limits.cpu_s}",
        f"--fsize={limits.file_bytes}", "--core=0", f"--nofile={NOFILE}",
        f"--nproc={nproc_limit(limits.pids)}", "--", *command,
    ]  # fmt: skip
    return args


def _tree_bytes(*roots: Path) -> int:
    total = 0
    for root in roots:
        for folder, _dirs, files in os.walk(root):
            for name in files:
                with contextlib.suppress(OSError):
                    total += os.lstat(os.path.join(folder, name)).st_size
    return total


_CGROUP_TAG = "readablestem-sandbox"
# Writes the shell's own pid into the cgroup, then becomes the program (a failed write only means
# the limits are the per-process ones; the sandbox still runs).
JOIN_SCRIPT = '{ echo $$ > "$0"; } 2>/dev/null; exec "$@"'


def cgroup_parent() -> Path | None:
    """This process's own cgroup directory (cgroup v2), or None."""
    try:
        line = next(
            ln for ln in Path("/proc/self/cgroup").read_text().splitlines() if ln.startswith("0::")
        )
    except (OSError, StopIteration):
        return None
    path = Path("/sys/fs/cgroup") / line[3:].lstrip("/")
    return path if path.is_dir() else None


class Cgroup:
    """A child cgroup with a memory and a pids limit for one sandbox run, when the container lets
    this process make one (cgroup v2, delegated). `make` returns None when it cannot: the limits
    are then per process (`prlimit`) and the probe says `sandbox_cgroup=unavailable`."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def make(cls, limits: Limits, parent: Path | None = None) -> Cgroup | None:
        parent = parent if parent is not None else cgroup_parent()
        if parent is None:
            return None
        path = parent / f"{_CGROUP_TAG}-{os.getpid()}-{time.monotonic_ns()}"
        try:
            path.mkdir()
            (path / "memory.max").write_text(str(limits.memory_bytes))
            with contextlib.suppress(OSError):
                (path / "memory.swap.max").write_text("0")
            (path / "pids.max").write_text(str(limits.pids))
        except OSError:
            with contextlib.suppress(OSError):
                path.rmdir()
            return None
        return cls(path)

    def add(self, pid: int) -> bool:
        try:
            (self.path / "cgroup.procs").write_text(str(pid))
        except OSError:
            return False
        return True

    def remove(self) -> None:
        with contextlib.suppress(OSError):
            (self.path / "cgroup.kill").write_text("1")
        for _ in range(20):
            try:
                self.path.rmdir()
                return
            except OSError as exc:
                if exc.errno != errno.EBUSY:
                    return
                time.sleep(0.05)


def run(
    command: list[str],
    *,
    ro: tuple[tuple[Path, str], ...] = (),
    rw: tuple[tuple[Path, str], ...] = (),
    env: dict[str, str] | None = None,
    cwd: str = "/",
    limits: Limits | None = None,
    log: Path | None = None,
    seccomp: bool = True,
    cgroup: bool = True,
    cgroup_parent: Path | None = None,
) -> Outcome:
    """Run `command` in the sandbox and wait. The caller's own environment is not passed on, not
    even to bubblewrap. A wall-clock timeout, or more than `limits.out_bytes` in the `rw` host
    folders plus `log`, kills the whole group. `log` receives the program's stdout and stderr
    (never read here); without it they are discarded. `seccomp`: the filter of `core.seccomp`
    (when this machine is one it knows). `cgroup`: a memory and pids limit for the whole group
    when the container allows one."""
    from readablestem.core import seccomp as filters

    limits = limits or Limits()
    program = filters.program() if seccomp else None
    read_end = write_end = None
    if program is not None:
        read_end, write_end = os.pipe()
        os.write(write_end, program)
        os.close(write_end)
    argv = build_argv(command, ro=ro, rw=rw, env=env, cwd=cwd, limits=limits, seccomp_fd=read_end)
    sink = log.open("wb") if log is not None else None
    group = Cgroup.make(limits, cgroup_parent) if cgroup else None
    if group is not None:
        # The process joins the cgroup before it becomes bubblewrap, so everything bubblewrap
        # starts is born in it: moving a running process would leave the children it has
        # already forked outside the limits.
        argv = ["/bin/sh", "-c", JOIN_SCRIPT, str(group.path / "cgroup.procs"), *argv]
    try:
        proc = subprocess.Popen(
            argv,
            env={"PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
            stdout=sink or subprocess.DEVNULL,
            stderr=subprocess.STDOUT if sink else subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(read_end,) if read_end is not None else (),
        )
        deadline = time.monotonic() + limits.wall_s
        roots = tuple(host for host, _ in rw)
        while True:
            try:
                proc.wait(timeout=POLL_SECONDS)
                return Outcome(proc.returncode)
            except subprocess.TimeoutExpired:
                pass
            timed_out = time.monotonic() > deadline
            written = _tree_bytes(*roots) + (log.stat().st_size if log and log.exists() else 0)
            over = written > limits.out_bytes
            if timed_out or over:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                return Outcome(-9, timed_out=timed_out, over_quota=over)
    finally:
        if read_end is not None:
            os.close(read_end)
        if sink is not None:
            sink.close()
        if group is not None:
            group.remove()
