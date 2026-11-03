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
import functools
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

POLL_SECONDS = 0.2
TMPFS_BYTES = 128_000_000

# Read-only system trees; each is skipped when absent. No /home, /root, /var, /mnt or /srv.
_SYSTEM_TREES = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64")
# The few files and folders of /etc and /var/lib that Perl, TeX and ImageMagick read.
_SYSTEM_FILES = (
    "/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d", "/etc/alternatives",
    "/etc/perl", "/etc/texmf", "/etc/xml", "/etc/fonts", "/etc/ImageMagick-6",
    "/etc/ImageMagick-7", "/var/lib/texmf",
)  # fmt: skip


class SandboxUnavailable(RuntimeError):
    """The sandbox cannot be built here (no bubblewrap, no user namespaces, no prlimit)."""


@dataclass(frozen=True)
class Limits:
    wall_s: int = 120
    cpu_s: int = 120
    memory_bytes: int = 1_500_000_000
    file_bytes: int = 50_000_000
    out_bytes: int = 200_000_000  # all the program may leave in its writable folders


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
    args = [
        bwrap, "--die-with-parent", "--new-session", "--unshare-all", "--clearenv",
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
        f"--fsize={limits.file_bytes}", "--core=0", "--", *command,
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


def run(
    command: list[str],
    *,
    ro: tuple[tuple[Path, str], ...] = (),
    rw: tuple[tuple[Path, str], ...] = (),
    env: dict[str, str] | None = None,
    cwd: str = "/",
    limits: Limits | None = None,
    log: Path | None = None,
) -> Outcome:
    """Run `command` in the sandbox and wait. The caller's own environment is not passed on, not
    even to bubblewrap. A wall-clock timeout, or more than `limits.out_bytes` in the `rw` host
    folders, kills the whole group. `log` receives the program's stdout and stderr (never
    read here); without it they are discarded."""
    limits = limits or Limits()
    argv = build_argv(command, ro=ro, rw=rw, env=env, cwd=cwd, limits=limits)
    sink = log.open("wb") if log is not None else None
    try:
        proc = subprocess.Popen(
            argv,
            env={"PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
            stdout=sink or subprocess.DEVNULL,
            stderr=subprocess.STDOUT if sink else subprocess.DEVNULL,
            start_new_session=True,
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
            over = _tree_bytes(*roots) > limits.out_bytes
            if timed_out or over:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                return Outcome(-9, timed_out=timed_out, over_quota=over)
    finally:
        if sink is not None:
            sink.close()
