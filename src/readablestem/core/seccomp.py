"""A seccomp filter for the sandbox, built here without libseccomp.

bubblewrap takes `--seccomp FD`, a raw classic-BPF program (an array of `struct sock_filter`).
This module writes one: allow everything except a short list of system calls a converter of TeX
never needs and that widen what a sandbox escape could do, which answer EPERM (and ENOSYS for
`clone3`, so glibc falls back to `clone`, whose flags the filter can read):

- `ptrace`, `process_vm_readv/writev` (reading another process);
- `mount`, `umount2`, `pivot_root`, `chroot`, and the new mount API (`open_tree`, `move_mount`,
  `fsopen`, `fsconfig`, `fsmount`, `fspick`, `mount_setattr`), `open_by_handle_at`;
- `unshare`, `setns`, and `clone` with any `CLONE_NEW*` flag;
- `keyctl`, `add_key`, `request_key`;
- `bpf`, `perf_event_open`, `userfaultfd`, `io_uring_setup/enter/register`;
- `kexec_load`, `kexec_file_load`, `init_module`, `finit_module`, `delete_module`, `reboot`,
  `swapon`, `swapoff`.

Another architecture than the program's own (and x32 calls on x86-64) answers EPERM. Only
x86-64 and arm64 are known; on any other machine `program()` returns None and the sandbox runs
without a filter (the probe says so: `sandbox_seccomp=unsupported`). On the bubblewrap backend
the filter is a second lock behind the namespaces, not the reason the sandbox holds;
`program(strict=True)` (the Landlock backend) is a first-line control: it adds sockets, other
processes of the user, memory and disk outside the counters, and file metadata (see
`_STRICT_DENIED` and `_SELF_ONLY`). `tests/test_sandbox.py` and `tests/test_sandbox_landlock.py`
run the denied calls inside the real sandboxes.
"""

from __future__ import annotations

import errno
import platform
import struct

# Classic BPF opcodes and seccomp return values.
_LD_W_ABS = 0x20
_JEQ_K = 0x15
_JSET_K = 0x45
_RET_K = 0x06
_ALLOW = 0x7FFF0000
_ERRNO = 0x00050000

_AUDIT_ARCH = {"x86_64": 0xC000003E, "aarch64": 0xC00000B7}
_X32_BIT = 0x40000000
# `seccomp_data`: nr at 0, arch at 4, args[0] at 16 (the low word is clone's flags).
_OFF_NR, _OFF_ARCH, _OFF_ARG0 = 0, 4, 16
_CLONE_NEW = (
    0x00020000  # NS
    | 0x02000000  # CLONE_NEWCGROUP
    | 0x04000000  # CLONE_NEWUTS
    | 0x08000000  # CLONE_NEWIPC
    | 0x10000000  # CLONE_NEWUSER
    | 0x20000000  # CLONE_NEWPID
    | 0x40000000  # CLONE_NEWNET
)

# name -> (x86-64 number, arm64 number)
_DENIED: dict[str, tuple[int, int]] = {
    "ptrace": (101, 117),
    "process_vm_readv": (310, 270),
    "process_vm_writev": (311, 271),
    "mount": (165, 40),
    "umount2": (166, 39),
    "pivot_root": (155, 41),
    "chroot": (161, 51),
    "open_tree": (428, 428),
    "move_mount": (429, 429),
    "fsopen": (430, 430),
    "fsconfig": (431, 431),
    "fsmount": (432, 432),
    "fspick": (433, 433),
    "mount_setattr": (442, 442),
    "open_by_handle_at": (304, 265),
    "unshare": (272, 97),
    "setns": (308, 268),
    "keyctl": (250, 219),
    "add_key": (248, 217),
    "request_key": (249, 218),
    "bpf": (321, 280),
    "perf_event_open": (298, 241),
    "userfaultfd": (323, 282),
    "io_uring_setup": (425, 425),
    "io_uring_enter": (426, 426),
    "io_uring_register": (427, 427),
    "kexec_load": (246, 104),
    "kexec_file_load": (320, 294),
    "init_module": (175, 105),
    "finit_module": (313, 273),
    "delete_module": (176, 106),
    "reboot": (169, 142),
    "swapon": (167, 224),
    "swapoff": (168, 225),
}
# Added by `program(strict=True)`, for the Landlock backend, which has no network namespace and no
# pid namespace, shares the user with the web app, and whose filesystem rules do not cover
# everything: every way to open a socket or to leave the process group; the Landlock calls
# themselves (the launcher is finished with them); another process of the user (pidfds,
# priorities, scheduling, `kcmp`); memory and disk that the byte counters do not see (anonymous
# files, System V and POSIX shared memory and queues); and changes to the metadata of files
# (mode, owner, times, extended attributes), which Landlock (to ABI 8) cannot refuse. -1: the
# architecture has no such call.
_STRICT_DENIED: dict[str, tuple[int, int]] = {
    "socket": (41, 198),
    "socketpair": (53, 199),
    "connect": (42, 203),
    "bind": (49, 200),
    "listen": (50, 201),
    "accept": (43, 202),
    "accept4": (288, 242),
    "setsid": (112, 157),
    "setpgid": (109, 154),
    "landlock_create_ruleset": (444, 444),
    "landlock_add_rule": (445, 445),
    "landlock_restrict_self": (446, 446),
    # another process of the same user
    "pidfd_open": (434, 434),
    "pidfd_getfd": (438, 438),
    "pidfd_send_signal": (424, 424),
    "process_madvise": (440, 440),
    "process_mrelease": (448, 448),
    "kcmp": (312, 272),
    "setpriority": (141, 140),
    "ioprio_set": (251, 30),
    # memory and disk outside the counters
    "memfd_create": (319, 279),
    "fallocate": (285, 47),  # reserves blocks with an apparent size of 0 (FALLOC_FL_KEEP_SIZE)
    "memfd_secret": (447, 447),
    "shmget": (29, 194),
    "shmat": (30, 196),
    "shmctl": (31, 195),
    "shmdt": (67, 197),
    "msgget": (68, 186),
    "msgsnd": (69, 189),
    "msgrcv": (70, 188),
    "msgctl": (71, 187),
    "semget": (64, 190),
    "semop": (65, 193),
    "semctl": (66, 191),
    "semtimedop": (220, 192),
    "mq_open": (240, 180),
    "mq_unlink": (241, 181),
    "mq_timedsend": (242, 182),
    "mq_timedreceive": (243, 183),
    "mq_notify": (244, 184),
    "mq_getsetattr": (245, 185),
    # metadata of files
    "chmod": (90, -1),
    "fchmod": (91, 52),
    "fchmodat": (268, 53),
    "fchmodat2": (452, 452),
    "chown": (92, -1),
    "fchown": (93, 55),
    "lchown": (94, -1),
    "fchownat": (260, 54),
    "utime": (132, -1),
    "utimes": (235, -1),
    "futimesat": (261, -1),
    "utimensat": (280, 88),
    "setxattr": (188, 5),
    "lsetxattr": (189, 6),
    "fsetxattr": (190, 7),
    "removexattr": (197, 14),
    "lremovexattr": (198, 15),
    "fremovexattr": (199, 16),
    "setxattrat": (463, 463),
    "removexattrat": (466, 466),
}
# Allowed only on the caller itself: the first argument (a pid) must be 0. `prlimit64` and the
# scheduler calls would otherwise change another process of the user (a web process that has
# set PR_SET_DUMPABLE=0 is still its user's to prlimit).
_SELF_ONLY: dict[str, tuple[int, int]] = {
    "prlimit64": (302, 261),
    "sched_setscheduler": (144, 119),
    "sched_setparam": (142, 118),
    "sched_setattr": (314, 274),
    "sched_setaffinity": (203, 122),
}
_EXECVEAT = (322, 281)
_AT_EMPTY_PATH = 0x1000
_OFF_ARG4 = 16 + 4 * 8
_CLONE = (56, 220)
_CLONE3 = (435, 435)


def supported() -> bool:
    return platform.machine() in _AUDIT_ARCH


def denied_names(strict: bool = False) -> list[str]:
    extra = [*_STRICT_DENIED, *(f"{n}(other pid)" for n in _SELF_ONLY), "execveat(AT_EMPTY_PATH)"]
    return sorted([*_DENIED, *(extra if strict else []), "clone(CLONE_NEW*)", "clone3"])


def number(name: str, machine: str | None = None) -> int:
    """The system call number of a denied call on this machine (for tests)."""
    index = 0 if (machine or platform.machine()) == "x86_64" else 1
    table = {
        **_DENIED, **_STRICT_DENIED, **_SELF_ONLY, "execveat": _EXECVEAT,
        "clone": _CLONE, "clone3": _CLONE3,
    }  # fmt: skip
    return table[name][index]


def program(machine: str | None = None, *, strict: bool = False) -> bytes | None:
    """The filter as bytes for `bwrap --seccomp` (or the Landlock launcher), or None on an
    architecture not known here. `strict` adds the socket, process-group and Landlock calls."""
    machine = machine or platform.machine()
    if machine not in _AUDIT_ARCH:
        return None
    index = 0 if machine == "x86_64" else 1
    # Each entry: (code, jt label or None, jf label or None, k). Labels resolve to indexes.
    steps: list[tuple[int, str | None, str | None, int] | str] = []

    def op(code: int, k: int, jt: str | None = None, jf: str | None = None) -> None:
        steps.append((code, jt, jf, k))

    op(_LD_W_ABS, _OFF_ARCH)
    op(_JEQ_K, _AUDIT_ARCH[machine], jt="arch_ok", jf="deny")
    steps.append("arch_ok")
    op(_LD_W_ABS, _OFF_NR)
    if machine == "x86_64":
        op(_JSET_K, _X32_BIT, jt="deny")  # x32 calls are another table
    for numbers in (*_DENIED.values(), *(_STRICT_DENIED.values() if strict else ())):
        if numbers[index] >= 0:
            op(_JEQ_K, numbers[index], jt="deny")
    if strict:
        for numbers in _SELF_ONLY.values():
            op(_JEQ_K, numbers[index], jt="self_only")
        op(_JEQ_K, _EXECVEAT[index], jt="execveat")
    op(_JEQ_K, _CLONE[index], jt="clone")
    op(_JEQ_K, _CLONE3[index], jt="nosys")
    op(_RET_K, _ALLOW)
    steps.append("clone")
    op(_LD_W_ABS, _OFF_ARG0)
    op(_JSET_K, _CLONE_NEW, jt="deny")
    op(_RET_K, _ALLOW)
    if strict:
        steps.append("self_only")  # the pid argument (an int: its low word) must be 0 = the caller
        op(_LD_W_ABS, _OFF_ARG0)
        op(_JEQ_K, 0, jf="deny")
        op(_RET_K, _ALLOW)
        steps.append("execveat")  # no fexecve of an open file (a memfd, a file written and kept)
        op(_LD_W_ABS, _OFF_ARG4)
        op(_JSET_K, _AT_EMPTY_PATH, jt="deny")
        op(_RET_K, _ALLOW)
    steps.append("deny")
    op(_RET_K, _ERRNO | errno.EPERM)
    steps.append("nosys")
    op(_RET_K, _ERRNO | errno.ENOSYS)

    labels: dict[str, int] = {}
    position = 0
    for step in steps:
        if isinstance(step, str):
            labels[step] = position
        else:
            position += 1
    out = bytearray()
    position = 0
    for step in steps:
        if isinstance(step, str):
            continue
        code, jt, jf, k = step
        jump_true = labels[jt] - position - 1 if jt else 0
        jump_false = labels[jf] - position - 1 if jf else 0
        if not (0 <= jump_true <= 255 and 0 <= jump_false <= 255):
            raise ValueError("a jump does not fit a classic BPF instruction")
        out += struct.pack("=HBBI", code, jump_true, jump_false, k)
        position += 1
    return bytes(out)
