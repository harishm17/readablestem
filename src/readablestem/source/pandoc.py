"""The one place the product runs pandoc on a file a stranger sent: `read(kind, data)`.

pandoc parses a zip, XML and a markup language, and a document can tell it to open other files.
So this step is the sandbox of `core.sandbox` (bubblewrap with Landlock where it is available),
as LaTeXML's and TeX's are: the working copy of the upload is mounted read-only in a folder of
its own, the one writable folder takes pandoc's answer, there is no network, and `--sandbox`
turns off pandoc's own file, network and program access (no `--extract-media`, no filters, no
resource path). It runs `pandoc --sandbox -f <format> -t json`: the tree of the document, which
the reader of that format turns into elements. Pictures are not written by pandoc: the reader
takes each from the package itself.

The format string, the extensions and the options come from `FORMATS`, per kind, in code. Nothing
of them comes from the upload, and the kind of the upload is decided from its content by the gate
(`source.intake.recognize`), never by its name.

Limits are its own (`LIMITS`), not LaTeXML's: pandoc is a Haskell program with a runtime that
starts several threads and reserves a large range of address space it never touches (measured:
it does not start in 512 MB of address space or with 4 tasks), so memory is bounded by the
heap cap (`+RTS -M`) and by the group's resident memory, which the sandbox checks, and not by
address space. The number of tasks is what the runtime needs and no more.

It takes the same lock as LaTeXML (`source.convert.GATE_NAME`, held by the caller), so one source
program runs at a time on the machine.

`Result.failure` is a name and never text of the document: pandoc's own messages quote it, so
they are read into the content-free category `warnings` (a count) and no more.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from readablestem.core import sandbox

# Failures, by name (what the caller reports).
NO_PANDOC = "no_pandoc"  # pandoc is not installed here
NO_SANDBOX = "no_sandbox"  # the sandbox cannot start here
NOT_FINISHED = "not_finished"  # stopped by a limit: time, memory, tasks or output size
FAILED = "failed"  # pandoc stopped on an error, or wrote no tree
BUSY = "busy"  # the machine had no room for another task right now

# kind -> (pandoc's input format with its extensions, the name the working copy is given, extra
# options). `+styles`: every paragraph and run carries its Word style name, which is how the
# reader's own pass hands pandoc what it drops (`docx_reader`). `--track-changes=accept`:
# insertions are kept and deletions dropped, the reading the reader proposes.
FORMATS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "docx": ("docx+styles", "input.docx", ("--track-changes=accept",)),
}

# pandoc's runtime reserves address space it never touches, so the cap is far above what the
# heap cap lets it use. `group_memory_bytes` is the real bound: resident memory of the group.
LIMITS = sandbox.Limits(
    wall_s=90,
    cpu_s=90,
    memory_bytes=64 * 1024**3,
    file_bytes=50_000_000,
    out_bytes=200_000_000,
    pids=12,
    group_memory_bytes=1024 * 1024**2,
    landlock_pids=10,
)
HEAP_BYTES = 700 * 1024**2  # `+RTS -M`: pandoc stops with a heap overflow before the group does
MAX_TREE_BYTES = 150 * 1024**2  # the tree pandoc writes, as JSON


@dataclass
class Result:
    tree: dict[str, Any] | None
    failure: str = ""  # one of the names above when `tree` is None
    version: str = ""  # pandoc's own version, recorded with the result and its cache key
    timed_out: bool = False


def installed() -> str | None:
    """The pandoc program of this machine, or None."""
    return shutil.which("pandoc")


def version() -> str:
    """pandoc's version ("3.7.0.2"), read from the program itself; "" when it is not here."""
    import subprocess

    program = installed()
    if program is None:
        return ""
    try:
        done = subprocess.run(
            [program, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    first = done.stdout.splitlines()[0] if done.stdout else ""
    return first.removeprefix("pandoc").strip()


def read(kind: str, data: bytes, *, limits: sandbox.Limits | None = None) -> Result:
    """pandoc's tree of `data`, an upload the gate passed as `kind`. Never raises for what the
    document contains. The caller holds `convert.GATE_NAME`."""
    program = installed()
    if program is None:
        return Result(None, NO_PANDOC)
    spec = FORMATS.get(kind)
    if spec is None:
        raise ValueError(f"no pandoc format for {kind!r}")
    fmt, name, options = spec
    limits = limits or LIMITS
    with tempfile.TemporaryDirectory(prefix="rs-pandoc-") as scratch:
        work, out = Path(scratch, "work"), Path(scratch, "out")
        work.mkdir()
        out.mkdir()
        (work / name).write_bytes(data)
        inside = sandbox.where(work, "/work")
        out_inside = sandbox.where(out, "/out")
        try:
            outcome = sandbox.run(
                [
                    program, "+RTS", f"-M{HEAP_BYTES}", "-RTS", "--sandbox",
                    "-f", fmt, "-t", "json", *options,
                    "-o", f"{out_inside}/tree.json", f"{inside}/{name}",
                ],
                ro=((work, "/work"),), rw=((out, "/out"),),
                env={"HOME": "/tmp", "LANG": "C.UTF-8", "TZ": "UTC"},
                cwd=out_inside, limits=limits, log=out / "pandoc.out",
            )  # fmt: skip
        except sandbox.SandboxUnavailable:
            return Result(None, NO_SANDBOX)
        if outcome.busy:
            return Result(None, BUSY)
        if outcome.timed_out or outcome.over_quota or outcome.over_memory or outcome.over_tasks:
            return Result(None, NOT_FINISHED, timed_out=outcome.timed_out)
        tree_file = out / "tree.json"
        if outcome.returncode != 0 or not tree_file.is_file():
            return Result(None, FAILED)
        if tree_file.stat().st_size > MAX_TREE_BYTES:
            return Result(None, NOT_FINISHED)
        try:
            tree = json.loads(tree_file.read_bytes())
        except (ValueError, RecursionError):
            return Result(None, FAILED)
        if not isinstance(tree, dict) or not isinstance(tree.get("blocks"), list):
            return Result(None, FAILED)
        return Result(tree, version=version())
