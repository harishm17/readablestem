"""Word and PowerPoint files to a tagged PDF, in a child process (T3-30).

An instructor's own `.docx` or `.pptx` is the best source there is: the author's headings and
picture descriptions are in it. LibreOffice, run headless, turns it into a *tagged* PDF (a PDF
that carries its structure tree), and the normal pipeline takes over from there; the tags are
read by `pipeline.tagged` exactly as for a PDF exported from Word (T2-18).

LibreOffice is a large program reading a complicated format from the internet, so it is run
the way the PDF probe is (`web.pdf_probe`, `web.uploads`):

- `python -m readablestem.web.office_convert SOFFICE TIMEOUT_S` reads the file from stdin and
  writes one JSON line and then, when it worked, the PDF bytes. It has a minimal
  environment (`core.childenv`: no keys, no database URL) and caps its own memory.
- **Checks before LibreOffice sees the file** (`preflight`, all in this child, none of them
  trusting the file name): it must be a ZIP of a sane size (entry count, unpacked size, no
  encrypted entries), it must be exactly a Word document or exactly a PowerPoint
  presentation, with no macros (`vbaProject.bin`, macro-enabled content types), no document
  type declarations or entity declarations in any XML part (so no entity bombs and no
  external entities), no UTF-16 XML (what Office never writes), and no relationship that points
  outside the file except an ordinary hyperlink. LibreOffice does fetch a linked picture's
  address when it converts (checked: it connects to the address in the file), so a linked
  picture, linked object, template or sub-document is refused rather than followed.
- **Limits on LibreOffice itself**: its own session and process group, killed at `TIMEOUT_S`
  (and by a CPU-time limit as a backstop); a 2 GiB address-space limit; at most
  `MAX_PDF_BYTES` written; at most 256 open files; a private profile in a private temporary
  directory that is removed afterwards, with macros disabled, links never updated, and an
  unreachable proxy set for every protocol, so that a fetch a check missed has nowhere to go.
- The file is written to a private temporary directory (mode 0700) for the seconds the
  conversion takes, because LibreOffice reads files and not pipes; the directory is
  removed in a `finally`. On Cloud Run that file system is memory.
- A file that fails any check, or that LibreOffice cannot open, makes no PDF. Nothing is
  logged by the child; the parent logs a category code only.

The PDF it makes is then probed like any upload (`web.uploads.inspect_pdf_isolated`): page
and size limits, scanned and blank refusals.

What is not done: the fields of a Word document are not evaluated (an `INCLUDETEXT` field
did not read the local file it names when tested), macros never run, and the original Office
file is not kept; only the PDF made from it is stored, as for any upload.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import zipfile
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from readablestem.core.childenv import child_env

if TYPE_CHECKING:
    from readablestem.config import Settings

ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # a Word 97 .doc, or a password-protected file

KINDS = ("docx", "pptx")
MAX_ENTRIES = 3000
MAX_UNPACKED_BYTES = 200 * 1024 * 1024
MAX_ENTRY_BYTES = 100 * 1024 * 1024
MAX_RELS_BYTES = 2 * 1024 * 1024
MAX_PDF_BYTES = 100 * 1024 * 1024
# Address space for the wrapper's own checks (a 20 MB upload and the zip's bookkeeping),
# and for LibreOffice. LibreOffice maps a lot of address space it never touches.
WRAPPER_MEMORY_BYTES = 768 * 1024 * 1024
SOFFICE_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
SOFFICE_CPU_SECONDS = 120
SOFFICE_OPEN_FILES = 256
DEFAULT_TIMEOUT_S = 90.0

# The PDF export filter per kind, with tagging on (JSON filter options, LibreOffice 7.4+).
_FILTER = {"docx": "writer_pdf_Export", "pptx": "impress_pdf_Export"}
_FILTER_OPTIONS = '{"UseTaggedPDF":{"type":"boolean","value":"true"}}'

_HYPERLINK = "/relationships/hyperlink"
_MACRO_CONTENT_TYPE = re.compile(rb"macroenabled", re.IGNORECASE)
_DTD = re.compile(rb"<!\s*(?:doctype|entity)", re.IGNORECASE)
_XML_PARTS = (".xml", ".rels", ".vml")
_CHUNK = 1 << 20


class OfficeRejected(Exception):
    """The file is refused (or could not be converted). `code` is a category."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --------------------------------------------------------------------------- checks


def looks_like_office(data: bytes) -> bool:
    """A ZIP, or an old-style Office container: worth sending to the converter."""
    return data[:4] == ZIP_MAGIC or data[:8] == OLE_MAGIC


def preflight(data: bytes) -> str:
    """The kind ("docx" or "pptx") of a safe-looking Office file, else raise `OfficeRejected`.

    Codes: "office_old_or_locked" (an old binary format or a password-protected file),
    "unreadable" (not a readable ZIP), "encrypted", "office_unsupported" (a ZIP that is not
    one Word or one PowerPoint file), "office_unsafe" (macros, DTDs, outside references,
    sizes past the limits).
    """
    if data[:8] == OLE_MAGIC:
        raise OfficeRejected("office_old_or_locked")
    if data[:4] != ZIP_MAGIC:
        raise OfficeRejected("office_unsupported")
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
        raise OfficeRejected("unreadable") from None
    with archive:
        try:
            return _check_archive(archive)
        except OfficeRejected:
            raise
        except Exception:  # a zip that fails in a way nobody planned for fails closed
            raise OfficeRejected("unreadable") from None


def _check_archive(archive: zipfile.ZipFile) -> str:
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise OfficeRejected("office_unsafe")
    total = 0
    names: set[str] = set()
    for info in infos:
        if info.flag_bits & 0x1:
            raise OfficeRejected("encrypted")
        if info.file_size > MAX_ENTRY_BYTES or "\x00" in info.filename:
            raise OfficeRejected("office_unsafe")
        total += info.file_size
        if total > MAX_UNPACKED_BYTES:
            raise OfficeRejected("office_unsafe")
        names.add(info.filename)
    kind = _kind(names)
    if any(name.lower().endswith("vbaproject.bin") for name in names):
        raise OfficeRejected("office_unsafe")
    content_types = _read(archive, "[Content_Types].xml", MAX_RELS_BYTES)
    if _MACRO_CONTENT_TYPE.search(content_types):
        raise OfficeRejected("office_unsafe")
    for info in infos:
        lowered = info.filename.lower()
        if lowered.endswith(".rels"):
            _check_relationships(_read(archive, info.filename, MAX_RELS_BYTES))
        if lowered.endswith(_XML_PARTS):
            _check_xml_part(archive, info)
    return kind


def _kind(names: set[str]) -> str:
    """Exactly one of the two: a Word document or a PowerPoint presentation."""
    if "[Content_Types].xml" not in names:
        raise OfficeRejected("office_unsupported")
    word, slides = "word/document.xml" in names, "ppt/presentation.xml" in names
    if word == slides:  # neither (a spreadsheet, a plain ZIP), or both
        raise OfficeRejected("office_unsupported")
    return "docx" if word else "pptx"


def _read(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    with archive.open(name) as part:
        data = part.read(limit + 1)
    if len(data) > limit:
        raise OfficeRejected("office_unsafe")
    return data


def _check_xml_part(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> None:
    """No UTF-16 and no `<!DOCTYPE` / `<!ENTITY` anywhere in the part (read in chunks, with
    an overlap so a declaration cannot hide on a boundary)."""
    with archive.open(info) as part:
        head = part.read(4)
        if head[:2] in (b"\xff\xfe", b"\xfe\xff") or head[:4] in (b"<\x00?\x00", b"\x00<\x00?"):
            raise OfficeRejected("office_unsafe")
        tail = head
        while True:
            chunk = part.read(_CHUNK)
            if not chunk:
                break
            window = tail + chunk
            if _DTD.search(window):
                raise OfficeRejected("office_unsafe")
            tail = window[-16:]
        if _DTD.search(tail):
            raise OfficeRejected("office_unsafe")


def _check_relationships(data: bytes) -> None:
    """A relationship may point outside the file only as an ordinary hyperlink."""
    from defusedxml import ElementTree
    from defusedxml.common import DefusedXmlException

    try:
        root = ElementTree.fromstring(data, forbid_dtd=True)
    except (ElementTree.ParseError, DefusedXmlException, ValueError):
        raise OfficeRejected("office_unsafe") from None
    for rel in root.iter():
        if not rel.tag.endswith("Relationship"):
            continue
        target = rel.get("Target", "")
        external = rel.get("TargetMode", "").lower() == "external"
        looks_remote = "://" in target or target.startswith(("\\\\", "//"))
        if (external or looks_remote) and not rel.get("Type", "").endswith(_HYPERLINK):
            raise OfficeRejected("office_unsafe")


# --------------------------------------------------------------------------- LibreOffice


def _registry_modifications() -> str:
    """The profile's settings: macros off, links never updated, and every proxy set to a
    port nothing listens on."""

    def item(path: str, name: str, value: str) -> str:
        return (
            f'<item oor:path="{path}"><prop oor:name="{name}" oor:op="fuse">'
            f"<value>{value}</value></prop></item>"
        )

    inet = "/org.openoffice.Inet/Settings"
    items = [item(inet, "ooInetProxyType", "1"), item(inet, "ooInetNoProxy", "")]
    for protocol in ("HTTP", "HTTPS", "FTP"):
        items.append(item(inet, f"ooInet{protocol}ProxyName", "127.0.0.1"))
        items.append(item(inet, f"ooInet{protocol}ProxyPort", "9"))
    scripting = "/org.openoffice.Office.Common/Security/Scripting"
    items += [
        item(scripting, "MacroSecurityLevel", "3"),
        item(scripting, "DisableMacrosExecution", "true"),
        item("/org.openoffice.Office.Writer/Content/Update", "Link", "2"),
    ]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        + "\n".join(items)
        + "\n</oor:items>\n"
    )


def _limits() -> None:  # pragma: no cover - runs in the forked child, before exec
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (SOFFICE_MEMORY_BYTES, SOFFICE_MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (SOFFICE_CPU_SECONDS, SOFFICE_CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_PDF_BYTES, MAX_PDF_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (SOFFICE_OPEN_FILES, SOFFICE_OPEN_FILES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:  # if the wrapper dies, so does LibreOffice (Linux)
        import ctypes

        ctypes.CDLL(None).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


def convert(data: bytes, kind: str, soffice: str, timeout_s: float) -> bytes:
    """The tagged PDF LibreOffice makes of `data` (already `preflight`ed), else raise
    `OfficeRejected("office_failed")`."""
    work = Path(tempfile.mkdtemp(prefix="readablestem-office-"))
    try:
        work.chmod(0o700)
        profile, out = work / "profile", work / "out"
        (profile / "user").mkdir(parents=True)
        out.mkdir()
        (profile / "user" / "registrymodifications.xcu").write_text(
            _registry_modifications(), encoding="utf-8"
        )
        source = work / f"input.{kind}"
        source.write_bytes(data)
        env = child_env() | {"HOME": str(work), "TMPDIR": str(work)}
        command = [
            soffice,
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--invisible",
            "--norestore",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--convert-to",
            f"pdf:{_FILTER[kind]}:{_FILTER_OPTIONS}",
            "--outdir",
            str(out),
            str(source),
        ]
        process = subprocess.Popen(
            command,
            env=env,
            cwd=work,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            preexec_fn=_limits,
        )
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            raise OfficeRejected("office_failed") from None
        finally:
            kill_group(process)
        produced = out / "input.pdf"
        if process.returncode != 0 or not produced.is_file():
            raise OfficeRejected("office_failed")
        if produced.stat().st_size > MAX_PDF_BYTES:
            raise OfficeRejected("office_failed")
        pdf = produced.read_bytes()
        if not pdf.startswith(b"%PDF-"):
            raise OfficeRejected("office_failed")
        return pdf
    except OSError:
        raise OfficeRejected("office_failed") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def kill_group(process: subprocess.Popen[bytes]) -> None:
    """End a child's whole process group (LibreOffice starts helpers), whatever happened,
    and reap the child. The child must have been started with `start_new_session=True`."""
    with contextlib.suppress(OSError):
        os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)


# --------------------------------------------------------------------------- what is installed


def soffice_command(configured: str | None = None) -> str | None:
    """The LibreOffice program: the configured path, else `soffice` on PATH."""
    return shutil.which(configured or "soffice")


def input_kinds(settings: Settings) -> tuple[str, ...]:
    """The Office kinds this server accepts: none when OFFICE_INPUT_ENABLED is off or
    LibreOffice is not installed."""
    if not settings.office_input_enabled:
        return ()
    return available_kinds(soffice_command(settings.soffice_path))


@lru_cache(maxsize=4)
def available_kinds(soffice: str | None) -> tuple[str, ...]:
    """Which of "docx" and "pptx" this installation can convert. A Debian-style install
    has one registry file per component (`writer.xcd`, `impress.xcd`); a layout that does
    not (macOS, a bundle) is assumed to have both and fails per file if not."""
    if soffice is None:
        return ()
    registry = Path(os.path.realpath(soffice)).parent.parent / "share" / "registry"
    if not (registry / "main.xcd").is_file():
        return KINDS
    found = []
    for kind, component in (("docx", "writer.xcd"), ("pptx", "impress.xcd")):
        if (registry / component).is_file():
            found.append(kind)
    return tuple(found)


_NAMES = {"docx": "Word (.docx)", "pptx": "PowerPoint (.pptx)"}


def accepted_phrases(kinds: tuple[str, ...]) -> tuple[str, str]:
    """("a PDF, Word (.docx) or PowerPoint (.pptx) file", "a PDF, .docx or .pptx file"):
    what the upload takes, in words for a sentence, and in file endings for "Choose ..."."""
    names = ["a PDF", *(_NAMES[k] for k in kinds)]
    endings = ["a PDF", *(f".{k}" for k in kinds)]

    def join(parts: list[str]) -> str:
        return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " or " + parts[-1]

    if not kinds:
        return "a PDF", "a PDF file"
    return join(names) + " file", join(endings) + " file"


# --------------------------------------------------------------------------- the child


def main(argv: list[str] | None = None) -> int:
    """`SOFFICE TIMEOUT_S`: file on stdin; a JSON line, then the PDF when it worked."""
    from readablestem.web.pdf_probe import limit_memory

    args = sys.argv[1:] if argv is None else argv
    limit_memory(WRAPPER_MEMORY_BYTES)
    out = sys.stdout.buffer
    try:
        soffice = args[0]
        timeout_s = float(args[1]) if len(args) > 1 else DEFAULT_TIMEOUT_S
        data = sys.stdin.buffer.read()
        kind = preflight(data)
        if kind not in available_kinds(soffice):
            raise OfficeRejected("office_unavailable")
        pdf = convert(data, kind, soffice, timeout_s)
    except OfficeRejected as rejected:
        out.write(json.dumps({"ok": False, "code": rejected.code}).encode() + b"\n")
        return 0
    except Exception:  # fails closed, with no message that could carry file content
        out.write(json.dumps({"ok": False, "code": "office_failed"}).encode() + b"\n")
        return 0
    out.write(json.dumps({"ok": True, "kind": kind}).encode() + b"\n" + pdf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
