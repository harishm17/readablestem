"""Run LaTeXML (`latexml`, then `latexmlpost`) on a project folder, inside the sandbox.

`convert(src, main, out)` mounts `src` read-only at `/work` and `out` writable at `/out`, runs
`latexml` (TeX to XML) and `latexmlpost` (XML to HTML5 with Presentation MathML), and leaves
`out/doc.html` and the logs `out/latexml.log` and `out/post.log`. It returns an `Outcome`
(core/sandbox.py); a refused, timed-out or failed run is reported as such and never raises for
what the document contains. Graphics are not converted here (`--nographicimages`,
`--nopictureimages`, `--nomathimages`): the pipeline's own figure step handles images.

Development: LaTeXML is the distribution's `latexml` package (Dockerfile, `INSTALL_LATEXML`).
Where it is not installed, `READABLESTEM_LATEXML_ROOT` may name a folder holding the unpacked
packages (`dpkg -x`, with a `usr/` folder); it is mounted read-only inside the sandbox and
Perl is pointed at it. Production never sets it.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import time
from pathlib import Path

from readablestem.core import sandbox

DEV_ROOT_VAR = "READABLESTEM_LATEXML_ROOT"
_DEV_MOUNT = "/opt/latexml-root"

# What a conversion may use: a wall-clock cap well under the web request's patience, CPU time, no
# single file over 50 MB, and two numbers that bound the worst case where there is no cgroup:
# at most 4 tasks (measured: LaTeXML needs 3 in the sandbox, with kpsewhich hidden and image
# conversion off: the sandbox's init, `prlimit` becoming perl, and perl; 4 leaves one spare) and
# 512 MB of address space each (measured on the fixtures and a 87 KB equation-heavy document of
# about 29 pages, the page limit: it needs between 256 and 384 MB; 192 MB is enough for the small
# fixtures). So 4 x 512 MB = 2 GB for the whole sandbox, which `ops sandbox-probe` prints as
# `sandbox_total_bound_mb` and checks against `SOURCE_SANDBOX_MAX_TOTAL_MB`.
LIMITS = sandbox.Limits(
    wall_s=120,
    cpu_s=120,
    memory_bytes=512 * 1024 * 1024,
    file_bytes=50_000_000,
    out_bytes=200_000_000,
    pids=4,
)

DOC_NAME = "doc.html"
XML_NAME = "doc.xml"


def _dev_root() -> Path | None:
    value = os.environ.get(DEV_ROOT_VAR)
    if not value:
        return None
    root = Path(value)
    return root if (root / "usr" / "bin" / "latexml").is_file() else None


def installed() -> bool:
    """Whether LaTeXML can be run here (on PATH, or under the development root)."""
    if _dev_root() is not None:
        return True
    return bool(shutil.which("latexml") and shutil.which("latexmlpost"))


def _program(name: str) -> tuple[list[str], dict[str, str], tuple[tuple[Path, str], ...]]:
    """(argv prefix, extra environment, extra read-only mounts) to run program `name` inside."""
    root = _dev_root()
    if root is None:
        return [f"/usr/bin/{name}"], {}, ()
    inside = f"{_DEV_MOUNT}/usr"
    perl_dirs = [f"{inside}/share/perl5"]
    arch = root / "usr" / "lib"
    for lib in sorted(arch.glob("*/perl5/*")):
        perl_dirs.append(f"{inside}/lib/{lib.relative_to(arch).as_posix()}")
    env = {"PERL5LIB": ":".join(perl_dirs)}
    return ["/usr/bin/perl", f"{inside}/bin/{name}"], env, ((root / "usr", inside),)


def _env(extra: dict[str, str]) -> dict[str, str]:
    return {"HOME": "/tmp", "LANG": "C.UTF-8", "TZ": "UTC", "SOURCE_DATE_EPOCH": "1", **extra}


def convert(src: Path, main: str, out: Path, *, limits: sandbox.Limits = LIMITS) -> sandbox.Outcome:
    """Convert `src/main` (a path relative to `src`, no `..`) into `out/doc.html`.

    Raises `sandbox.SandboxUnavailable` when the sandbox cannot start. The two steps share the
    wall-clock budget."""
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    ro = ((src, "/work"),)
    rw = ((out, "/out"),)

    prefix, extra, mounts = _program("latexml")
    first = sandbox.run(
        [
            *prefix, "--nocomments", "--path=/work", f"--destination=/out/{XML_NAME}",
            "--log=/out/latexml.log", f"/work/{main}",
        ],
        ro=ro + mounts, rw=rw, env=_env(extra), cwd="/work", limits=limits,
        log=out / "latexml.out",
    )  # fmt: skip
    # latexml exits 0 for "no obvious problems" and for warnings; 10 or more means fatal.
    if not first.ok or not (out / XML_NAME).is_file():
        return first if not first.ok else sandbox.Outcome(1)

    prefix, extra, mounts = _program("latexmlpost")
    remaining = max(5, limits.wall_s - int(time.monotonic() - started))
    second_limits = dataclasses.replace(limits, wall_s=remaining)
    return sandbox.run(
        [
            *prefix, "--format=html5", "--pmml", "--nomathimages", "--nographicimages",
            "--nopictureimages", "--nodefaultresources",
            "--sourcedirectory=/work", f"--destination=/out/{DOC_NAME}",
            "--log=/out/post.log", f"/out/{XML_NAME}",
        ],
        ro=ro + mounts, rw=rw, env=_env(extra), cwd="/out", limits=second_limits,
        log=out / "post.out",
    )  # fmt: skip
