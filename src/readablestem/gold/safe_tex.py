"""Safe handling of downloaded TeX: unpacking archives and compiling a document.

Anything fetched from the network (a repository tarball, an arXiv source) is untrusted data.
A TeX source can run code (latexmk reads a `.latexmkrc` as Perl; `\\write18`), read any file the
process can read (`\\input{/abs/path}` pulls it into the PDF), and write or fill the disk. Two
rules follow, and every place in the repository that unpacks or compiles downloaded files goes
through this module:

1. `extract_tar` unpacks into a temporary folder and renames it into place only when the whole
   archive was read: no absolute paths, no `..`, no links or devices, a cap on the unpacked bytes
   (also on the decompressed stream, so a gzip bomb stops early) and on the number of files.
2. `compile_tex` runs the engine inside bubblewrap with a minimal root (the system libraries, the
   TeX Live tree read-only, the source as a throw-away overlay, an output folder), no network, a
   temporary home, a clean environment and its own process group, which a timeout kills whole.
   The engine is also told, by environment, to refuse reads and writes outside the working tree
   (`openin_any=p`, `openout_any=p`, `shell_escape=f`); latexmk is started with `-norc`, so no
   rc file from the source tree or the home directory is read; Tectonic gets `--untrusted`.
   When bubblewrap is missing the compile is refused: there is no unsandboxed fallback.
"""

from __future__ import annotations

import contextlib
import gzip
import io
import os
import resource
import shutil
import signal
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

MAX_UNPACKED_BYTES = 400_000_000
MAX_FILES = 20_000
MAX_OUTPUT_BYTES = 500_000_000  # the largest file a compile may write


class UnsafeArchive(ValueError):
    """An archive that is refused; `code` is a short category for the candidate log."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class SandboxUnavailable(RuntimeError):
    pass


# ------------------------------------------------------------------------------- archives


class _CappedReader(io.RawIOBase):
    """A read-only stream that fails once more than `limit` bytes have been read from `inner`."""

    def __init__(self, inner: io.BufferedIOBase | gzip.GzipFile, limit: int) -> None:
        self.inner, self.limit, self.count = inner, limit, 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:  # type: ignore[override]
        data = self.inner.read(len(buffer))
        self.count += len(data)
        if self.count > self.limit:
            raise UnsafeArchive("unpacked_too_large", str(self.limit))
        buffer[: len(data)] = data
        return len(data)


def _check_name(name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or name.startswith(("/", "\\")) or "\x00" in name:
        raise UnsafeArchive("unsafe_path", "absolute")
    if ".." in path.parts:
        raise UnsafeArchive("unsafe_path", "parent")
    return path


def extract_tar(
    data: bytes,
    folder: Path,
    *,
    max_bytes: int = MAX_UNPACKED_BYTES,
    max_files: int = MAX_FILES,
    strip_top: bool = False,
) -> None:
    """Unpack the tar (plain or gzip) `data` into `folder`, which must not exist yet.

    Written to a sibling temporary folder and renamed, so a refused or broken archive leaves no
    folder behind. `strip_top` drops a single top-level directory (a repository tarball's)."""
    if folder.exists():
        raise FileExistsError(str(folder))
    folder.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=folder.name + ".part-", dir=folder.parent))
    try:
        raw: io.BufferedIOBase | gzip.GzipFile
        raw = (
            gzip.GzipFile(fileobj=io.BytesIO(data)) if data[:2] == b"\x1f\x8b" else io.BytesIO(data)
        )
        stream = io.BufferedReader(_CappedReader(raw, max_bytes))
        files = 0
        total = 0
        tops: set[str] = set()
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            for member in archive:
                rel = _check_name(member.name)
                if not rel.parts:
                    continue
                if member.isdir():
                    tops.add(rel.parts[0])
                    (tmp / rel).mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():  # symlink, hard link, device, fifo
                    raise UnsafeArchive("unsafe_member", "link or special file")
                files += 1
                total += member.size
                if files > max_files:
                    raise UnsafeArchive("too_many_files", str(max_files))
                if total > max_bytes:
                    raise UnsafeArchive("unpacked_too_large", str(max_bytes))
                tops.add(rel.parts[0])
                target = tmp / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise UnsafeArchive("unsafe_member", "no data")
                with target.open("wb") as out:
                    shutil.copyfileobj(source, out, 1 << 20)
        root = tmp
        if strip_top and len(tops) == 1 and (tmp / next(iter(tops))).is_dir():
            root = tmp / next(iter(tops))
        root.rename(folder)
    except tarfile.TarError as exc:
        raise UnsafeArchive("bad_archive", type(exc).__name__) from None
    except (EOFError, OSError, gzip.BadGzipFile) as exc:
        if isinstance(exc, UnsafeArchive):
            raise
        raise UnsafeArchive("bad_archive", type(exc).__name__) from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def extract_single_file(
    data: bytes, folder: Path, name: str, *, max_bytes: int = MAX_UNPACKED_BYTES
) -> None:
    """A single gzipped file (arXiv serves a one-file source this way) as `folder/name`, written
    to a temporary folder and renamed, with the decompressed size capped."""
    if folder.exists():
        raise FileExistsError(str(folder))
    if data[:2] != b"\x1f\x8b":
        raise UnsafeArchive("bad_archive", "not gzip")
    folder.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=folder.name + ".part-", dir=folder.parent))
    try:
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as handle:
                raw = handle.read(max_bytes + 1)
        except (OSError, EOFError) as exc:
            raise UnsafeArchive("bad_archive", type(exc).__name__) from None
        if len(raw) > max_bytes:
            raise UnsafeArchive("unpacked_too_large", str(max_bytes))
        (tmp / name).write_bytes(raw)
        tmp.rename(folder)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------------------- engines


def texlive_bin() -> Path | None:
    """The `bin` folder of the `latexmk` on PATH, or of a user-level TeX Live
    (`~/.local/texlive/<year>/bin/<arch>`), or None."""
    found = shutil.which("latexmk")
    if found:
        return Path(found).resolve().parent
    for candidate in sorted((Path.home() / ".local" / "texlive").glob("*/bin/*"), reverse=True):
        if (candidate / "latexmk").is_file():
            return candidate
    return None


def texlive_root(bin_dir: Path) -> Path:
    """`.../texlive` for `.../texlive/2026/bin/x86_64-linux`: what has to be visible read-only."""
    return bin_dir.parents[2] if bin_dir.parents[1].name.isdigit() else bin_dir.parents[1]


def tectonic_path() -> str | None:
    found = shutil.which("tectonic") or str(Path.home() / ".local" / "bin" / "tectonic")
    return found if Path(found).is_file() else None


def tectonic_cache() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "tectonic"


def _first_line(command: list[str], env: dict[str, str]) -> str:
    try:
        run = subprocess.run(
            command, capture_output=True, text=True, timeout=30, env=env, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    lines = (run.stdout or run.stderr).strip().splitlines()
    return lines[0] if lines else ""


def engines() -> list[tuple[str, str]]:
    """(name, version line) of the engines to try, in order: pdflatex and xelatex from TeX Live
    (through latexmk), then Tectonic. The version is read with a clean environment."""
    out: list[tuple[str, str]] = []
    bin_dir = texlive_bin()
    clean = {"PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin" if bin_dir else "/usr/bin:/bin"}
    if bin_dir is not None:
        for name in ("pdflatex", "xelatex"):
            out.append((name, _first_line([str(bin_dir / name), "--version"], clean)))
    exe = tectonic_path()
    if exe:
        out.append(("tectonic", _first_line([exe, "--version"], clean)))
    if not out:
        raise SandboxUnavailable(
            "no TeX engine: install TeX Live or Tectonic (docs/development.md)"
        )
    return out


# ------------------------------------------------------------------------------- sandbox


def _bwrap() -> str:
    found = shutil.which("bwrap")
    if not found:
        raise SandboxUnavailable("bubblewrap (bwrap) is required to compile downloaded TeX")
    return found


def _system_mounts() -> list[str]:
    """A minimal root: the system libraries and binaries, fonts config and certificates only.
    No /home, /root, /var, /mnt or the rest of /etc."""
    args: list[str] = []
    for name in ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64"):
        path = Path(name)
        if path.is_symlink():
            args += ["--symlink", os.readlink(path), name]
        elif path.exists():
            args += ["--ro-bind", name, name]
    for name in (
        "/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d", "/etc/fonts",
        "/etc/alternatives", "/etc/ssl", "/etc/ca-certificates",
    ):  # fmt: skip
        if Path(name).exists():
            args += ["--ro-bind", name, name]
    return args


def _limits() -> None:  # runs in the child before exec
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


@dataclass
class Result:
    returncode: int
    timed_out: bool


def tex_env(home: str, docdir: str, root: str) -> dict[str, str]:
    """The only environment an engine sees. The texmf.cnf switches are set here: reads and
    writes only below the working tree, no shell escape."""
    return {
        "HOME": home,
        "TEXMFHOME": f"{home}/texmf",
        "TEXMFVAR": f"{home}/texmf-var",
        "TEXMFCONFIG": f"{home}/texmf-config",
        "XDG_CACHE_HOME": f"{home}/.cache",
        "TEXINPUTS": f"{docdir}{os.pathsep}{root}{os.pathsep}",
        "openin_any": "p",
        "openout_any": "p",
        "shell_escape": "f",
        "SOURCE_DATE_EPOCH": "1",
        "TZ": "UTC",
        "LANG": "C.UTF-8",
    }


def latexmk_command(engine: str, main: str) -> list[str]:
    flag = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}[engine]
    return [
        "latexmk", "-norc", flag, "-interaction=nonstopmode", "-halt-on-error",
        "-no-shell-escape", "-outdir=/out", main,
    ]  # fmt: skip


def run_in_sandbox(
    command: list[str],
    *,
    src: Path,
    out: Path,
    cwd_rel: str,
    env: dict[str, str],
    bin_dir: Path | None,
    timeout: int,
    ro_extra: tuple[tuple[Path, str], ...] = (),
) -> Result:
    """Run `command` with the source at /work (a throw-away overlay: writes are discarded) and
    `out` at /out, no network, the TeX Live tree read-only. A timeout kills the whole group."""
    out.mkdir(parents=True, exist_ok=True)
    args = [
        _bwrap(), "--die-with-parent", "--new-session", "--unshare-all", "--clearenv",
        *_system_mounts(), "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--overlay-src", str(src), "--tmp-overlay", "/work", "--bind", str(out), "/out",
    ]  # fmt: skip
    path = "/usr/bin:/bin"
    if bin_dir is not None:
        root = texlive_root(bin_dir)
        args += ["--ro-bind", str(root), str(root)]
        path = f"{bin_dir}:{path}"
    home = env["HOME"]
    args += ["--tmpfs", home]  # before the extra mounts: one of them sits below it
    for host, inside in ro_extra:
        args += ["--ro-bind", str(host), inside]
    for key, value in {**env, "PATH": path}.items():
        args += ["--setenv", key, value]
    args += ["--chdir", f"/work/{cwd_rel}".rstrip("/"), *command]
    proc = subprocess.Popen(
        args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, preexec_fn=_limits,
    )  # fmt: skip
    try:
        proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return Result(-9, True)
    return Result(proc.returncode, False)


def compile_tex(src: Path, main: str, out: Path, engine: str, *, timeout: int) -> Result:
    """Compile `src/main` into `out` with `engine` ("pdflatex", "xelatex" or "tectonic")."""
    cwd_rel = Path(main).parent.as_posix()
    cwd_rel = "" if cwd_rel == "." else cwd_rel
    docdir = f"/work/{cwd_rel}".rstrip("/")
    home = "/tmp/home"
    env = tex_env(home, docdir, "/work")
    name = Path(main).name
    if engine == "tectonic":
        exe = tectonic_path()
        if exe is None:
            raise SandboxUnavailable("tectonic is not installed")
        cache = tectonic_cache()
        command = ["/opt/tectonic", "-X", "compile", "--untrusted", "--only-cached"]
        command += ["--outdir", "/out", name]
        extra = ((Path(exe), "/opt/tectonic"), (cache, f"{home}/.cache/tectonic"))
        return run_in_sandbox(
            command, src=src, out=out, cwd_rel=cwd_rel, env=env, bin_dir=None, timeout=timeout,
            ro_extra=extra,
        )  # fmt: skip
    bin_dir = texlive_bin()
    if bin_dir is None:
        raise SandboxUnavailable("TeX Live is not installed")
    return run_in_sandbox(
        latexmk_command(engine, name), src=src, out=out, cwd_rel=cwd_rel, env=env,
        bin_dir=bin_dir, timeout=timeout,
    )  # fmt: skip
