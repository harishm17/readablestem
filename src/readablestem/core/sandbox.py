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

Where bubblewrap cannot start (a platform without unprivileged user namespaces) a second backend
takes over automatically: Landlock plus a stricter seccomp filter, applied by a launcher
(`core/landlock_exec.py`) in a process of its own just before the program is exec'd. It has no
mount namespace, so the program sees the host's real paths: callers name a path with `where()`,
which is the path inside for bubblewrap and the host path for Landlock. When neither backend can
run the call is refused with `SandboxUnavailable`: there is no unsandboxed fallback
(docs/security.md, "Running uploaded TeX").
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
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

POLL_SECONDS = 0.1
# Processes (threads count) the program and what it starts may add to what this user already has,
# and open files. RLIMIT_NPROC counts every task of the user, the web app's threads included, so
# the limit is "now plus this much", worked out when the sandbox starts (`_nproc_limit`).
NPROC_EXTRA = 64
NOFILE = 256
# Landlock backend: RLIMIT_NPROC counts every task of the user, the web app's threads included,
# which come and go while a conversion runs; this many more than asked for keep a thread started
# meanwhile from making LaTeXML's own forks fail. It adds to the tasks of the bound
# (`Limits.task_bound`), so it is small.
LANDLOCK_NPROC_SLACK = 2
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
    # Resident memory of the whole group together, checked every poll (0: not checked). Where a
    # cgroup cannot be made it is what bounds the sum over tasks that the per-process address
    # space limit does not.
    group_memory_bytes: int = 0
    # Entries (files and folders) the program may have made in its writable folders; over it the
    # group is killed (a million empty files fill the inodes, not the bytes).
    max_entries: int = 5000
    # Tasks the program may have on the Landlock backend, whose first task is the program
    # itself (there is no init and no `prlimit` in front of it): None means `pids`.
    landlock_pids: int | None = None

    def task_bound(self, backend_name: str | None) -> int:
        """The most tasks the program and what it starts can be, on a backend."""
        if backend_name == "landlock":
            return (self.landlock_pids or self.pids) + LANDLOCK_NPROC_SLACK
        return self.pids


@dataclass
class Outcome:
    returncode: int
    timed_out: bool = False
    over_quota: bool = False
    over_memory: bool = False
    # The program could not fork or start because the machine had no room for more tasks
    # (the user's task count was at the limit): not the document's fault, try again.
    busy: bool = False

    @property
    def ok(self) -> bool:
        return (
            self.returncode == 0
            and not self.timed_out
            and not self.over_quota
            and not self.over_memory
        )


def bwrap_path() -> str | None:
    return shutil.which("bwrap")


def prlimit_path() -> str | None:
    return shutil.which("prlimit")


BACKEND_ENV = "READABLESTEM_SANDBOX_BACKEND"  # "bwrap" or "landlock": try only that one
# Path rights plus REFER (2) and TRUNCATE (3), TCP rules (4), IOCTL_DEV (5), and the scopes that
# keep a signal or an abstract unix socket inside the sandbox (6).
LANDLOCK_MIN_ABI = 6
LAUNCHER = Path(__file__).with_name("landlock_exec.py")
# Shown in place of a file of the system that must look absent (`hide`), on the Landlock backend:
# an executable that does nothing, first on PATH.
_STUB = "#!/bin/sh\nexit 0\n"


@functools.cache
def backend() -> str | None:
    """The sandbox this machine can start: "bwrap", else "landlock", else None. Found by running
    `true` inside each; cached for the life of the process. `READABLESTEM_SANDBOX_BACKEND` limits
    the choice to one (for tests and for the probe)."""
    forced = os.environ.get(BACKEND_ENV)
    for name in (forced,) if forced in ("bwrap", "landlock") else ("bwrap", "landlock"):
        try:
            if _run(name, ["/usr/bin/true"], limits=Limits(wall_s=20)).ok:
                return name
        except (SandboxUnavailable, OSError):
            continue
    return None


def available() -> bool:
    """Whether a sandbox can be started here at all."""
    return backend() is not None


def make_undumpable() -> bool:
    """`PR_SET_DUMPABLE=0` for this process: another process of the same user (a sandboxed
    program on the Landlock backend, which shares the user and the pid namespace) can no longer
    read this process's `/proc/<pid>/environ`, `mem` or `maps`, which hold the keys. Called at
    start by the web process and the conversion child (their parents of the sandbox). It is
    not inherited by a program that is exec'd. False when the call failed."""
    import ctypes

    try:
        return ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0) == 0  # PR_SET_DUMPABLE
    except OSError:
        return False


def where(host: Path, inside: str) -> str:
    """The path the sandboxed program uses for `host`: `inside` under bubblewrap (which mounts
    it there), the host path itself under Landlock (which has no mounts)."""
    return inside if backend() == "bwrap" else str(host)


def landlock_abi() -> int:
    """The Landlock ABI of this kernel (0: none, or the call is filtered), and 0 when seccomp is
    not known on this machine (the backend needs its filter)."""
    import ctypes

    from readablestem.core import seccomp

    if not seccomp.supported():
        return 0
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        value = libc.syscall(444, None, 0, 1)  # landlock_create_ruleset(NULL, 0, VERSION)
    except OSError:
        return 0
    return int(value) if value > 0 else 0


def system_paths() -> tuple[list[str], list[str]]:
    """(trees to read and execute, files and folders to read) of the system that exist here: the
    same list the bubblewrap root is built from."""
    trees = [n for n in _SYSTEM_TREES if Path(n).exists()]
    reads = [n for n in _SYSTEM_FILES if Path(n).exists()]
    return trees, reads


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
    hide: tuple[str, ...] = (),
    seccomp_fd: int | None = None,
) -> list[str]:
    """The `bwrap` command line for `command` (a list, never a shell string). `ro` and `rw` are
    (host path, path inside) pairs; every path is absolute and `inside` never overlaps
    `/proc`, `/dev` or `/tmp`. `hide`: files of the system that exist here and are replaced by an
    empty file inside (the program sees them as present and empty)."""
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
    for path in hide:
        if Path(path).is_file():  # an absent file needs no hiding (and cannot be mounted over)
            args += ["--ro-bind", "/dev/null", path]
    for key, value in {"PATH": "/usr/local/bin:/usr/bin:/bin", **(env or {})}.items():
        args += ["--setenv", key, value]
    args += [
        "--chdir", cwd, "--",
        prlimit, f"--as={limits.memory_bytes}", f"--cpu={limits.cpu_s}",
        f"--fsize={limits.file_bytes}", "--core=0", f"--nofile={NOFILE}",
        f"--nproc={nproc_limit(limits.pids)}", "--", *command,
    ]  # fmt: skip
    return args


def process_tree(root: int) -> tuple[set[int], int]:
    """(pids of `root` and every descendant of it, their resident memory in bytes), from /proc:
    the tree by parent, which holds whatever the program does to its own group or session."""
    page = os.sysconf("SC_PAGE_SIZE")
    parents: dict[int, int] = {}
    pages: dict[int, int] = {}
    with contextlib.suppress(OSError):
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                with open(f"/proc/{entry.name}/stat") as handle:
                    text = handle.read()
                fields = text[text.rindex(")") + 2 :].split()
                parents[int(entry.name)] = int(fields[1])
                pages[int(entry.name)] = int(fields[21])  # rss, in pages
            except (OSError, ValueError, IndexError):
                continue
    tree = {root}
    grew = True
    while grew:
        grew = False
        for pid, parent in parents.items():
            if parent in tree and pid not in tree:
                tree.add(pid)
                grew = True
    return tree, sum(pages.get(pid, 0) for pid in tree) * page


def group_rss_bytes(root: int) -> int:
    """Resident memory, in bytes, of `root` and every descendant of it."""
    return process_tree(root)[1]


def deleted_open_bytes(pids: set[int]) -> int:
    """Bytes held by regular files that these processes still have open but that are no longer
    in any folder (unlinked): the folder counters cannot see them."""
    total = 0
    for pid in pids:
        try:
            names = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for name in names:
            try:
                info = os.stat(f"/proc/{pid}/fd/{name}")
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 0:
                total += info.st_blocks * 512
    return total


def deleted_mapped_bytes(pids: set[int]) -> int:
    """Bytes of files that these processes have mapped but that are no longer in any folder: a
    mapping keeps a deleted file's blocks after its descriptor is closed. Each file once, by its
    size on disk (read through `map_files`) or else by the length of the mapping."""
    seen: dict[tuple[str, str], int] = {}
    for pid in pids:
        try:
            with open(f"/proc/{pid}/maps") as handle:
                lines = handle.read().splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.endswith("(deleted)"):
                continue
            parts = line.split(None, 5)
            if len(parts) < 6 or parts[4] == "0" or not parts[5].startswith("/"):
                continue
            start, end = (int(x, 16) for x in parts[0].split("-"))
            size = end - start
            with contextlib.suppress(OSError):
                info = os.stat(f"/proc/{pid}/map_files/{parts[0]}")
                size = max(info.st_size, info.st_blocks * 512)
            key = (parts[3], parts[4])
            seen[key] = max(seen.get(key, 0), size)
    return sum(seen.values())


def _tree_usage(roots: tuple[Path, ...], cap: int) -> tuple[int, int]:
    """(bytes, entries) under `roots`; stops counting once entries pass `cap`."""
    total = entries = 0
    for root in roots:
        for folder, dirs, files in os.walk(root):
            entries += len(dirs) + len(files)
            if entries > cap:
                return total, entries
            for name in files:
                with contextlib.suppress(OSError):
                    info = os.lstat(os.path.join(folder, name))
                    # Blocks as well as size: space can be reserved without a size.
                    total += max(info.st_size, info.st_blocks * 512)
    return total, entries


def _tree_bytes(*roots: Path) -> int:
    return _tree_usage(roots, 1 << 62)[0]


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
    tools: tuple[tuple[Path, str], ...] = (),
    env: dict[str, str] | None = None,
    cwd: str = "/",
    limits: Limits | None = None,
    log: Path | None = None,
    hide: tuple[str, ...] = (),
    seccomp: bool = True,
    cgroup: bool = True,
    cgroup_parent: Path | None = None,
) -> Outcome:
    """Run `command` in the sandbox this machine has (`backend()`) and wait. The caller's own
    environment is not passed on, not even to the launcher. A wall-clock timeout, or more than
    `limits.out_bytes` in the `rw` host folders plus `log`, kills the whole group. `ro`: read-only
    inputs; `tools`: read-only and executable (programs the command runs); `rw`: writable
    folders. All are (host path, path inside) pairs: the second is where bubblewrap mounts it,
    and Landlock ignores it (`where()`). The same goes for `cwd` and for the paths in `command`
    and `env`; an environment value of exactly "/tmp" is a private scratch folder on the Landlock
    backend. `log` receives the program's stdout and stderr (never read here); without it they
    are discarded. `seccomp`: the filter of `core.seccomp`. `cgroup`: a memory and pids limit for
    the whole group when the container allows one."""
    name = backend()
    if name is None:
        raise SandboxUnavailable("no sandbox can start here (bubblewrap and Landlock both failed)")
    return _run(
        name, command, ro=ro, rw=rw, tools=tools, env=env, cwd=cwd, limits=limits, log=log,
        hide=hide, seccomp=seccomp, cgroup=cgroup, cgroup_parent=cgroup_parent,
    )  # fmt: skip


def _landlock_spec(
    command: list[str],
    *,
    ro: tuple[tuple[Path, str], ...],
    rw: tuple[tuple[Path, str], ...],
    tools: tuple[tuple[Path, str], ...],
    env: dict[str, str] | None,
    cwd: str,
    limits: Limits,
    hide: tuple[str, ...],
    scratch: Path,
    stubs: Path,
    program: bytes | None,
    busy_marker: str = "",
) -> dict:
    import base64

    trees, reads = system_paths()
    # The files of /dev a Perl uses; nothing else of /dev, and nothing of /proc.
    names = ("/dev/null", "/dev/zero", "/dev/urandom", "/dev/random")
    devices = [d for d in names if Path(d).exists()]
    for path in hide:  # must look absent: a do-nothing program of that name, first on PATH
        if Path(path).is_file():
            stub = stubs / Path(path).name
            stub.write_text(_STUB)
            stub.chmod(0o755)
    wanted = {"PATH": f"{stubs}:/usr/local/bin:/usr/bin:/bin", **(env or {})}
    environment = {k: (str(scratch) if v == "/tmp" else v) for k, v in wanted.items()}
    return {
        "min_abi": LANDLOCK_MIN_ABI,
        "cwd": cwd,
        "exec": [*trees, str(stubs), *(str(h) for h, _ in tools)],
        "read": [*reads, *(str(h) for h, _ in ro)],
        "write": [str(scratch), *(str(h) for h, _ in rw)],
        "files_rw": ["/dev/null"],
        "files_read": [d for d in devices if d != "/dev/null"],
        "env": environment,
        "limits": {
            "as": limits.memory_bytes, "cpu": limits.cpu_s, "fsize": limits.file_bytes,
            "core": 0, "nofile": NOFILE, "pids": limits.landlock_pids or limits.pids,
            "slack": LANDLOCK_NPROC_SLACK,
        },
        "seccomp": base64.b64encode(program).decode() if program else "",
        "cmd": command,
        "busy_marker": busy_marker,
    }  # fmt: skip


def _run(
    name: str,
    command: list[str],
    *,
    ro: tuple[tuple[Path, str], ...] = (),
    rw: tuple[tuple[Path, str], ...] = (),
    tools: tuple[tuple[Path, str], ...] = (),
    env: dict[str, str] | None = None,
    cwd: str = "/",
    limits: Limits | None = None,
    log: Path | None = None,
    hide: tuple[str, ...] = (),
    seccomp: bool = True,
    cgroup: bool = True,
    cgroup_parent: Path | None = None,
) -> Outcome:
    """`run` on a named backend ("bwrap" or "landlock")."""
    import json

    from readablestem.core import seccomp as filters

    limits = limits or Limits()
    landlock = name == "landlock"
    read_end = write_end = None
    scratch = stubs = marker_dir = marker = None
    sink = None
    group = None
    if landlock:
        if landlock_abi() < LANDLOCK_MIN_ABI:
            raise SandboxUnavailable("Landlock is not available here (kernel too old or filtered)")
        program = filters.program(strict=True) if seccomp else None
        if program is None:
            raise SandboxUnavailable("the Landlock backend needs its seccomp filter")
        scratch = Path(tempfile.mkdtemp(prefix="rs-sandbox-"))
        stubs = Path(tempfile.mkdtemp(prefix="rs-stubs-"))
        # Where the launcher says the machine was busy: in no folder the program is granted, so
        # only the launcher can make the file.
        marker_dir = Path(tempfile.mkdtemp(prefix="rs-marker-"))
        marker = marker_dir / "busy"
        spec = _landlock_spec(
            command, ro=ro, rw=rw, tools=tools, env=env, cwd=cwd, limits=limits, hide=hide,
            scratch=scratch, stubs=stubs, program=program, busy_marker=str(marker),
        )  # fmt: skip
        argv = [sys.executable, "-I", "-S", str(LAUNCHER), json.dumps(spec)]
        rw_hosts = (*(host for host, _ in rw), scratch)
    else:
        program = filters.program() if seccomp else None
        if program is not None:
            read_end, write_end = os.pipe()
            os.write(write_end, program)
            os.close(write_end)
        argv = build_argv(
            command, ro=(*ro, *tools), rw=rw, env=env, cwd=cwd, limits=limits, hide=hide,
            seccomp_fd=read_end,
        )  # fmt: skip
        rw_hosts = tuple(host for host, _ in rw)
    try:
        sink = log.open("wb") if log is not None else None
        group = Cgroup.make(limits, cgroup_parent) if cgroup else None
        if group is not None:
            # The process joins the cgroup before it becomes the sandbox, so everything it starts
            # is born in it: moving a running process would leave the children it has already
            # forked outside the limits.
            argv = ["/bin/sh", "-c", JOIN_SCRIPT, str(group.path / "cgroup.procs"), *argv]
        proc = subprocess.Popen(
            argv,
            env={"PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
            stdout=sink or subprocess.DEVNULL,
            stderr=subprocess.STDOUT if sink else subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            pass_fds=(read_end,) if read_end is not None else (),
        )
        deadline = time.monotonic() + limits.wall_s
        while True:
            try:
                proc.wait(timeout=POLL_SECONDS)
                return Outcome(
                    proc.returncode,
                    busy=bool(proc.returncode) and marker is not None and marker.exists(),
                )
            except subprocess.TimeoutExpired:
                pass
            timed_out = time.monotonic() > deadline
            size, entries = _tree_usage(rw_hosts, limits.max_entries)
            tree, resident = process_tree(proc.pid)
            written = (
                size
                + deleted_open_bytes(tree)
                + deleted_mapped_bytes(tree)
                + (log.stat().st_size if log and log.exists() else 0)
            )
            over = written > limits.out_bytes or entries > limits.max_entries
            heavy = bool(limits.group_memory_bytes) and resident > limits.group_memory_bytes
            if timed_out or over or heavy:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                return Outcome(-9, timed_out=timed_out, over_quota=over, over_memory=heavy)
    finally:
        if landlock:
            # No pid namespace to end with the program: whatever it left in its group (it cannot
            # leave the group: setsid and setpgid are denied) is killed with it.
            with contextlib.suppress(ProcessLookupError, UnboundLocalError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
        if read_end is not None:
            os.close(read_end)
        if sink is not None:
            sink.close()
        if group is not None:
            group.remove()
        for folder in (scratch, stubs, marker_dir):
            if folder is not None:
                shutil.rmtree(folder, ignore_errors=True)
