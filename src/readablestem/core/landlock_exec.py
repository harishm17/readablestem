"""Confine this process with Landlock and seccomp, then become the program (stdlib only).

This is the launcher of the Landlock backend of `core/sandbox.py`, for hosts where bubblewrap
cannot start (no unprivileged user namespaces). It is run by file path, never imported, as

    python -I -S landlock_exec.py '<json spec>'

in a fresh process of its own, with nothing but the spec to go on, and in this order:

0. this process is the supervisor: it forks the program as its child, is the subreaper of
   everything the program starts, watches its caller by pid and takes the group down when the
   caller is gone, and, when the program ends, kills what it left behind and reports its exit
   status. Steps 1 to 6 are the child's;
1. change into the working folder and close every inherited file descriptor above 2;
2. build a Landlock ruleset that handles every filesystem right of the kernel's ABI (and, from
   ABI 4, TCP bind and connect, and from ABI 6 abstract unix sockets and signals to other
   domains), and allow only what the spec lists: read and execute on the system trees, read on
   the input folders, read and write on the output folders, read on the few files of `/etc` a
   Perl needs. Nothing else is readable or writable: not `/app`, not `/etc/passwd`, not `/proc`,
   not other jobs' folders;
3. `PR_SET_NO_NEW_PRIVS`, `landlock_restrict_self`;
4. install the seccomp filter from the spec (it also denies the `landlock_*` calls, sockets,
   `ptrace`, `setsid`, `setpgid`, ...);
5. set the resource limits, so that this launcher's own memory does not count against them;
6. `execve` the command with exactly the environment in the spec.

Any failure before the exec ends the process with status 125 and a one-line reason on stderr: the
program never runs half-confined.
"""

from __future__ import annotations

import base64
import contextlib
import ctypes
import errno
import json
import os
import re
import resource
import signal
import struct
import sys
import time

SYS_CREATE, SYS_ADD, SYS_RESTRICT = 444, 445, 446  # the same on x86-64 and arm64
CREATE_VERSION = 1 << 0
RULE_PATH_BENEATH = 1

# Filesystem rights, by bit.
EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
REMOVE_DIR, REMOVE_FILE = 1 << 4, 1 << 5
MAKE_CHAR, MAKE_DIR, MAKE_REG, MAKE_SOCK = 1 << 6, 1 << 7, 1 << 8, 1 << 9
MAKE_FIFO, MAKE_BLOCK, MAKE_SYM = 1 << 10, 1 << 11, 1 << 12
REFER, TRUNCATE, IOCTL_DEV = 1 << 13, 1 << 14, 1 << 15
NET_BIND_TCP, NET_CONNECT_TCP = 1 << 0, 1 << 1
SCOPE_ABSTRACT_UNIX, SCOPE_SIGNAL = 1 << 0, 1 << 1

ALL_BY_ABI = {  # rights added by each ABI version
    1: EXECUTE
    | WRITE_FILE
    | READ_FILE
    | READ_DIR
    | REMOVE_DIR
    | REMOVE_FILE
    | MAKE_CHAR
    | MAKE_DIR
    | MAKE_REG
    | MAKE_SOCK
    | MAKE_FIFO
    | MAKE_BLOCK
    | MAKE_SYM,
    2: REFER,
    3: TRUNCATE,
    5: IOCTL_DEV,
}
# What a file (not a folder) takes.
FILE_RIGHTS = EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE | IOCTL_DEV

READ = READ_FILE | READ_DIR
READ_EXEC = READ | EXECUTE
WRITE_ALL = (
    WRITE_FILE | REMOVE_DIR | REMOVE_FILE | MAKE_DIR | MAKE_REG | MAKE_SYM | MAKE_FIFO | REFER
    | TRUNCATE
)  # fmt: skip

PR_SET_NO_NEW_PRIVS = 38
PR_SET_SECCOMP = 22
SECCOMP_MODE_FILTER = 2

LIMITS = {
    "as": resource.RLIMIT_AS,
    "cpu": resource.RLIMIT_CPU,
    "fsize": resource.RLIMIT_FSIZE,
    "core": resource.RLIMIT_CORE,
    "nofile": resource.RLIMIT_NOFILE,
}


def abi_version(libc) -> int:
    version = libc.syscall(SYS_CREATE, None, 0, CREATE_VERSION)
    return int(version) if version > 0 else 0


def fs_rights(abi: int) -> int:
    mask = 0
    for since, bits in ALL_BY_ABI.items():
        if abi >= since:
            mask |= bits
    return mask


# Where a failing setup step is reported: the write end of a pipe the supervisor made, which is
# close-on-exec, so the program never has it and cannot write to it. The supervisor reads it and
# makes the step file. Fixed codes only, never free text.
REPORT: dict = {"fd": -1, "file": ""}


def errno_name(number: int) -> str:
    return errno.errorcode.get(number, "EUNKNOWN") if number else ""


def fail(code: str, number: int = 0) -> None:
    """End with status 125, reporting which step failed (`code`, one of the fixed codes of
    docs/security.md "Landlock backend", and the errno's name)."""
    line = f"{code} {errno_name(number)}".strip()
    if REPORT["fd"] >= 0:  # the child: through the pipe the supervisor reads
        with contextlib.suppress(OSError):
            os.write(REPORT["fd"], line.encode() + b"\n")
    elif REPORT["file"]:  # the supervisor itself
        with contextlib.suppress(OSError), open(REPORT["file"], "w") as handle:
            handle.write(line)
    sys.stderr.write(f"landlock_exec: {line}\n")
    sys.stderr.flush()
    os._exit(125)


def own_tasks(uid: int, proc: str = "/proc") -> int:
    """Tasks of this user right now (RLIMIT_NPROC compares with every task of the user)."""
    total = 0
    try:
        for name in os.listdir(proc):
            if name.isdigit():
                try:
                    if os.stat(f"{proc}/{name}").st_uid == uid:
                        total += len(os.listdir(f"{proc}/{name}/task"))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def program_tasks(root: int) -> int:
    """Tasks of the descendants of `root` (not `root`'s own), from /proc."""
    parents: dict[int, int] = {}
    try:
        for name in os.listdir("/proc"):
            if name.isdigit():
                try:
                    with open(f"/proc/{name}/stat") as handle:
                        line = handle.read()
                    parents[int(name)] = int(line[line.rindex(")") + 2 :].split()[1])
                except (OSError, ValueError, IndexError):
                    pass
    except OSError:
        return 0
    tree = {root}
    grew = True
    while grew:
        grew = False
        for pid, parent in parents.items():
            if parent in tree and pid not in tree:
                tree.add(pid)
                grew = True
    total = 0
    for pid in tree - {root}:
        with contextlib.suppress(OSError):
            total += len(os.listdir(f"/proc/{pid}/task"))
    return total


def restrict(libc, spec: dict) -> None:
    abi = abi_version(libc)
    if abi < spec["min_abi"]:
        fail("abi_below_min")
    handled = fs_rights(abi)
    net = (NET_BIND_TCP | NET_CONNECT_TCP) if abi >= 4 else 0
    scoped = (SCOPE_ABSTRACT_UNIX | SCOPE_SIGNAL) if abi >= 6 else 0
    size = 8 if abi < 4 else (16 if abi < 6 else 24)
    attr = struct.pack("=QQQ", handled, net, scoped)[:size]
    ruleset = libc.syscall(SYS_CREATE, attr, size, 0)
    if ruleset < 0:
        fail("landlock_create_ruleset", ctypes.get_errno())
    grants = (
        ("exec", spec.get("exec", []), READ_EXEC),
        ("read", spec.get("read", []), READ),
        ("write", spec.get("write", []), READ | WRITE_ALL),
        ("files_rw", spec.get("files_rw", []), READ_FILE | WRITE_FILE | TRUNCATE | IOCTL_DEV),
        ("files_read", spec.get("files_read", []), READ_FILE | IOCTL_DEV),
    )
    for kind, paths, rights in grants:
        for path in paths:
            try:
                fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                continue  # a tree this machine does not have
            try:
                allowed = rights & handled
                if not os.path.isdir(path):
                    allowed &= FILE_RIGHTS
                if not allowed:
                    continue
                rule = struct.pack("=Qi", allowed, fd)  # a packed struct: 12 bytes
                if libc.syscall(SYS_ADD, ruleset, RULE_PATH_BENEATH, rule, 0) != 0:
                    fail(f"landlock_add_rule:{kind}", ctypes.get_errno())
            finally:
                os.close(fd)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        fail("no_new_privs", ctypes.get_errno())
    if libc.syscall(SYS_RESTRICT, ruleset, 0) != 0:
        fail("landlock_restrict_self", ctypes.get_errno())
    os.close(ruleset)


def install_seccomp(libc, program: bytes) -> None:
    count = len(program) // 8
    buffer = ctypes.create_string_buffer(program, len(program))
    # struct sock_fprog { unsigned short len; struct sock_filter *filter; }
    fprog = struct.pack("=HxxxxxxQ", count, ctypes.addressof(buffer))  # 64-bit layout
    holder = ctypes.create_string_buffer(fprog, len(fprog))
    if (
        libc.prctl(
            PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.c_void_p(ctypes.addressof(holder)), 0, 0
        )
        != 0
    ):
        fail("seccomp_install", ctypes.get_errno())


PR_SET_PDEATHSIG = 1
PR_SET_CHILD_SUBREAPER = 36


def confine_and_exec(libc, spec: dict, tasks: int, supervisor: int) -> None:
    """In the child: drop everything, then become the program. Never returns."""
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    if os.getppid() != supervisor:  # the supervisor is already gone
        fail("supervisor_gone")
    restrict(libc, spec)
    if spec.get("seccomp"):
        install_seccomp(libc, base64.b64decode(spec["seccomp"]))
    limits = spec["limits"]
    for name, which in LIMITS.items():
        if name in limits:
            try:
                resource.setrlimit(which, (limits[name], limits[name]))
            except (OSError, ValueError) as exc:
                fail(f"prlimit_{name}", getattr(exc, "errno", 0) or errno.EINVAL)
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (tasks, tasks))
    except (OSError, ValueError) as exc:
        fail("prlimit_nproc", getattr(exc, "errno", 0) or errno.EINVAL)
    try:
        os.execve(spec["cmd"][0], spec["cmd"], spec["env"])
    except OSError as exc:
        fail("exec", exc.errno or 0)


def kill_group() -> None:
    """Every other process of this process group is killed (the subreaper right was refused)."""
    me, group = os.getpid(), os.getpgrp()
    if group != me:  # not the leader of a group of its own: the group is not ours to kill
        return
    with contextlib.suppress(OSError):
        for name in os.listdir("/proc"):
            if name.isdigit() and int(name) != me:
                with contextlib.suppress(OSError, ValueError, IndexError):
                    with open(f"/proc/{name}/stat") as handle:
                        line = handle.read()
                    if int(line[line.rindex(")") + 2 :].split()[2]) == group:
                        os.kill(int(name), signal.SIGKILL)
    with contextlib.suppress(ChildProcessError, OSError):
        while os.waitpid(-1, os.WNOHANG) != (0, 0):
            pass


def kill_descendants() -> None:
    """Every process still below this one (orphans of the program become children of this
    process: it is the subreaper) is killed and reaped."""
    for _ in range(50):
        children = []
        try:
            for name in os.listdir("/proc"):
                if name.isdigit():
                    try:
                        with open(f"/proc/{name}/stat") as handle:
                            line = handle.read()
                        if int(line[line.rindex(")") + 2 :].split()[1]) == os.getpid():
                            children.append(int(name))
                    except (OSError, ValueError, IndexError):
                        pass
        except OSError:
            return
        if not children:
            return
        for pid in children:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        try:
            while True:
                if os.waitpid(-1, os.WNOHANG) == (0, 0):
                    break
        except ChildProcessError:
            return


def main() -> None:
    """The supervisor. It stays to see the program's whole tree end: when the caller dies (or
    kills this process's group) everything below it dies with it, and when the program exits
    whatever it left behind is killed before this process reports the program's exit status."""
    spec = json.loads(sys.argv[1])
    libc = ctypes.CDLL(None, use_errno=True)
    step_file = spec.get("step_file", "")
    REPORT["file"] = step_file
    try:
        os.chdir(spec.get("cwd", "/"))
        os.closerange(3, 1 << 16)
    except OSError as exc:
        fail("chdir", exc.errno or 0)
    report_read, report_write = os.pipe()  # close-on-exec: the program never has either end
    # The caller is watched by its pid (below), not by PR_SET_PDEATHSIG, which fires when the
    # *thread* that started this process exits, even while the caller lives.
    # (Not "my parent is pid 1": in a container the web process may itself be pid 1.)
    parent = os.getppid()
    if "parent_pid" in spec and parent != spec["parent_pid"]:
        fail("caller_gone")
    # Without the subreaper right the orphans of the program are not ours to find; the group (which
    # the program cannot leave: setsid and setpgid are denied) is killed instead.
    subreaper = libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
    if spec.get("no_subreaper"):  # a test of the fallback
        subreaper = False

    def on_term(_signum, _frame) -> None:  # the caller is gone: take the whole group down
        os.killpg(os.getpgrp(), signal.SIGKILL)

    signal.signal(signal.SIGTERM, on_term)
    # Counted now: after the ruleset /proc is no longer readable. This process becomes nothing;
    # the program is the child, one of the allowance, so the program may have `pids` tasks (plus
    # slack for the user's other threads starting meanwhile) beside this supervisor.
    limits = spec["limits"]
    # `nproc_adjust` is only ever negative (a test makes the limit one the user is already
    # over, to see a fork refused for lack of room without filling the machine).
    counted = own_tasks(os.getuid(), spec.get("proc_root", "/proc"))  # (the root is a test's)
    if counted == 0:  # /proc is not readable: the limit cannot be worked out
        fail("count_tasks")
    tasks = counted + limits["pids"] + limits.get("slack", 0)
    tasks += min(0, limits.get("nproc_adjust", 0))
    marker = spec.get("busy_marker", "")
    allowance = limits["pids"] + limits.get("slack", 0)
    starved = False
    last_look = 0.0
    try:
        child = os.fork()
    except OSError as exc:  # no room for the program at all
        if marker and exc.errno == errno.EAGAIN:
            open(marker, "w").close()
        fail("fork", exc.errno or 0)
    if child == 0:
        os.close(report_read)
        REPORT["fd"] = report_write
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        confine_and_exec(libc, spec, tasks, os.getppid())
        fail("launcher_exception")
    os.close(report_write)
    status = 0
    while True:
        try:
            pid, status = os.waitpid(child, os.WNOHANG)
            if pid == child:
                break
        except InterruptedError:
            continue
        except ChildProcessError:
            break
        if os.getppid() != parent:  # the caller (the process, not a thread of it) is gone
            on_term(0, None)
        now = time.monotonic()
        if marker and not starved and now - last_look > 0.25:
            # The machine is "busy" when the user's tasks are at the limit while the program has
            # not used its own allowance: what filled the room is not the program. This is the
            # launcher's own observation, never the program's output.
            last_look = now
            if own_tasks(os.getuid()) >= tasks and program_tasks(os.getpid()) < allowance:
                starved = True
        time.sleep(0.05)
    if subreaper:
        kill_descendants()
    else:
        kill_group()
    if step_file:
        # What the child reported before it became the program: read after it has ended, so
        # the pipe is at its end. Only a code of the fixed shape is passed on.
        with contextlib.suppress(OSError):
            os.set_blocking(report_read, False)
            reported = os.read(report_read, 200).decode("ascii", "replace").strip()
            if re.fullmatch(r"[a-z_]+(:[a-z_]+)? ?(E[A-Z0-9]+)?", reported):
                with open(step_file, "w") as handle:
                    handle.write(reported)
    if marker and starved and status != 0:
        with contextlib.suppress(OSError):
            open(marker, "w").close()
    if os.WIFSIGNALED(status):
        signal.signal(os.WTERMSIG(status), signal.SIG_DFL)
        os.kill(os.getpid(), os.WTERMSIG(status))
    os._exit(os.WEXITSTATUS(status))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # a bug here must still name itself, never end silently
        fail("launcher_exception")
