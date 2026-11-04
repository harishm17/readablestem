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

import os
import shutil
import time
from pathlib import Path

from readablestem.core import sandbox

DEV_ROOT_VAR = "READABLESTEM_LATEXML_ROOT"
_DEV_MOUNT = "/opt/latexml-root"

# What a conversion may use: a wall-clock cap well under the web request's patience, memory and
# CPU caps from the plan (docs/security.md, "Source uploads"), and no single file over 50 MB.
LIMITS = sandbox.Limits(
    wall_s=120, cpu_s=120, memory_bytes=1_500_000_000, file_bytes=50_000_000, out_bytes=200_000_000
)

# LaTeXML finds a package's own TeX with `kpsewhich`. With TeX Live's base installed (the image's
# `texlive-latex-base`) it then reads raw TeX for a package it has a binding for and tries to run
# expl3 under its own interpreter: `\\usepackage{siunitx}` took 72 s and ended in over 100 errors.
# Without `kpsewhich` it uses its own bindings only, as it does where TeX Live is not installed:
# 2 s and the right answer. So the program is hidden from LaTeXML (an empty file in the sandbox).
HIDDEN_FILES = ("/usr/bin/kpsewhich", "/usr/local/bin/kpsewhich", "/bin/kpsewhich")

DOC_NAME = "doc.xhtml"
XML_NAME = "doc.xml"
# Where `convert` leaves them, relative to its `out` folder.
XML_PATH = f"xml/{XML_NAME}"
DOC_PATH = f"html/{DOC_NAME}"


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


def convert(
    src: Path, main: str, out: Path, *, limits: sandbox.Limits | None = None
) -> sandbox.Outcome:
    """Convert `src/main` (a path relative to `src`, no `..`): the XML at `out/XML_PATH`, then
    the XHTML at `out/DOC_PATH`.

    Each step writes into a folder of its own and the second reads the first's read-only, so
    what a hostile document writes in the first step cannot stand in for the second's answer.
    Raises `sandbox.SandboxUnavailable` when the sandbox cannot start. The two steps share the
    wall-clock budget."""
    limits = limits or LIMITS  # read now, so a test can lower the module's limits
    xml_dir, html_dir = out / "xml", out / "html"
    xml_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    ro = ((src, "/work"),)

    prefix, extra, mounts = _program("latexml")
    first = sandbox.run(
        [
            *prefix, "--nocomments", "--path=/work", f"--destination=/out/{XML_NAME}",
            "--log=/out/latexml.log", f"/work/{main}",
        ],
        ro=ro + mounts, rw=((xml_dir, "/out"),), env=_env(extra), cwd="/work", limits=limits,
        log=out / "latexml.out", hide=HIDDEN_FILES,
    )  # fmt: skip
    # latexml exits 0 for "no obvious problems" and for warnings; 10 or more means fatal.
    if not first.ok or not (xml_dir / XML_NAME).is_file():
        return first if not first.ok else sandbox.Outcome(1)

    prefix, extra, mounts = _program("latexmlpost")
    remaining = max(5, limits.wall_s - int(time.monotonic() - started))
    second_limits = sandbox.Limits(
        wall_s=remaining,
        cpu_s=limits.cpu_s,
        memory_bytes=limits.memory_bytes,
        file_bytes=limits.file_bytes,
        out_bytes=limits.out_bytes,
    )
    return sandbox.run(
        [
            *prefix, "--format=xhtml", "--omitdoctype", "--pmml", "--nomathimages",
            "--nographicimages", "--nopictureimages", "--nodefaultresources",
            "--sourcedirectory=/work", f"--destination=/out/{DOC_NAME}",
            "--log=/out/post.log", f"/xml/{XML_NAME}",
        ],
        ro=ro + mounts + ((xml_dir, "/xml"),), rw=((html_dir, "/out"),), env=_env(extra),
        cwd="/out", limits=second_limits, log=out / "post.out", hide=HIDDEN_FILES,
    )  # fmt: skip
