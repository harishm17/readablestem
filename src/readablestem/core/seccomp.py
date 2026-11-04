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
without a filter (the probe says so: `sandbox_seccomp=unsupported`). The filter is a second
lock behind the namespaces, not the reason the sandbox holds. `tests/test_sandbox.py` runs
the denied calls inside the real sandbox.
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
_CLONE = (56, 220)
_CLONE3 = (435, 435)


def supported() -> bool:
    return platform.machine() in _AUDIT_ARCH


def denied_names() -> list[str]:
    return sorted([*_DENIED, "clone(CLONE_NEW*)", "clone3"])


def number(name: str, machine: str | None = None) -> int:
    """The system call number of a denied call on this machine (for tests)."""
    index = 0 if (machine or platform.machine()) == "x86_64" else 1
    table = {**_DENIED, "clone": _CLONE, "clone3": _CLONE3}
    return table[name][index]


def program(machine: str | None = None) -> bytes | None:
    """The filter as bytes for `bwrap --seccomp`, or None on an architecture not known here."""
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
    for numbers in _DENIED.values():
        op(_JEQ_K, numbers[index], jt="deny")
    op(_JEQ_K, _CLONE[index], jt="clone")
    op(_JEQ_K, _CLONE3[index], jt="nosys")
    op(_RET_K, _ALLOW)
    steps.append("clone")
    op(_LD_W_ABS, _OFF_ARG0)
    op(_JSET_K, _CLONE_NEW, jt="deny")
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
