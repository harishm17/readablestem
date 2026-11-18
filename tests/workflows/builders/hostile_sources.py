"""Zips and LaTeX files made to hurt the source intake: names that leave the folder, links,
members that lie about their size, and documents that loop or reach for a file of the host.
Each zip holds a sound `main.tex`, so that what is refused is the member the case is about.
Made for the hostile-input set (A28) on November 11, 2026."""

from __future__ import annotations

import io
import stat
import struct
import warnings
import zipfile

from . import fixture
from .rawpdf import ZIP_DATE

ORIGIN = "hostile-input set A28, 2026-11-11"
MAIN = (
    b"\\documentclass{article}\n\\begin{document}\n"
    b"The speed of a wave equals its frequency times its wavelength.\n\\end{document}\n"
)
Member = tuple[str, bytes, int]
PLAIN = 0o644 << 16


def _zip(members: list[Member]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data, attributes in members:
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = attributes
            archive.writestr(info, data)
    return out.getvalue()


def _with(name: str, data: bytes = b"\\section{More}\n", attributes: int = PLAIN) -> bytes:
    return _zip([("main.tex", MAIN, PLAIN), (name, data, attributes)])


@fixture("zip-traversal.zip", origin=ORIGIN, shows="a member named ../evil.tex")
def zip_traversal() -> bytes:
    return _with("../evil.tex")


@fixture("zip-absolute.zip", origin=ORIGIN, shows="a member named /etc/evil.tex")
def zip_absolute() -> bytes:
    return _with("/etc/evil.tex")


@fixture("zip-backslash.zip", origin=ORIGIN, shows="a member named ..\\evil.tex")
def zip_backslash() -> bytes:
    return _with("..\\evil.tex")


@fixture("zip-symlink.zip", origin=ORIGIN, shows="a member that is a link to /etc/passwd")
def zip_symlink() -> bytes:
    return _with("notes.tex", b"/etc/passwd", (stat.S_IFLNK | 0o777) << 16)


@fixture("zip-duplicate.zip", origin=ORIGIN, shows="two members with the same name")
def zip_duplicate() -> bytes:
    out = io.BytesIO()
    # Python warns of the second name, which is the point of the file.
    with warnings.catch_warnings(), zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        warnings.simplefilter("ignore", UserWarning)
        for data in (MAIN, b"\\documentclass{article}\\begin{document}Second.\\end{document}\n"):
            info = zipfile.ZipInfo("main.tex", date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = PLAIN
            archive.writestr(info, data)
    return out.getvalue()


@fixture("zip-bomb.zip", origin=ORIGIN, shows="a 30 MB member of zeros in a 30 KB archive")
def zip_bomb() -> bytes:
    return _with("zeros.tex", b"\0" * (30 * 1024 * 1024))


@fixture(
    "zip-lying-header.zip",
    origin=ORIGIN,
    shows="a member whose headers claim 10 bytes and which holds 3 MB",
)
def zip_lying_header() -> bytes:
    data = bytearray(
        _zip([("notes.tex", b"a" * (3 * 1024 * 1024), PLAIN), ("main.tex", MAIN, PLAIN)])
    )
    claimed = struct.pack("<I", 10)
    # Uncompressed size: at 22 in the local file header, at 24 in the central directory.
    for marker, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        at = data.find(marker)
        data[at + offset : at + offset + 4] = claimed
    return bytes(data)


def _document(body: str) -> bytes:
    return f"\\documentclass{{article}}\n\\begin{{document}}\n{body}\n\\end{{document}}\n".encode()


LATEX = {
    "loop": ("a macro that calls itself and never ends", "Before. \\def\\again{x\\again}\\again"),
    "grows": (
        "a macro that doubles its argument until memory runs out",
        "Before. \\def\\twice#1{\\twice{#1#1}}\\twice{x}",
    ),
    "input-host-file": ("\\input of a file of the host, written plainly", "\\input{/etc/passwd}"),
    "input-built-name": (
        "\\input of a file of the host under a name built from pieces, which no reading of "
        "the source finds",
        "\\def\\root{/etc}\\def\\leaf{passwd}"
        "\\expandafter\\expandafter\\expandafter\\input\\expandafter\\root\\expandafter/\\leaf\\relax"
        " A28-AFTER-INPUT",
    ),
    "read-built-name": (
        "\\openin and \\read of a file of the host, the command's name built from pieces",
        "\\expandafter\\let\\expandafter\\open\\csname openin\\endcsname"
        "\\expandafter\\let\\expandafter\\take\\csname read\\endcsname"
        "\\newread\\hostfile \\open\\hostfile=/etc/passwd "
        "\\take\\hostfile to\\firstline First line: \\firstline",
    ),
    "shell": ("\\write18, which would run a program", "\\immediate\\write18{id > /tmp/a28}"),
}


def _latex(name: str, shows: str, body: str) -> None:
    @fixture(f"hostile-{name}.tex", origin=ORIGIN, shows=shows)
    def make() -> bytes:
        return _document(body)


for _name, (_shows, _body) in LATEX.items():
    _latex(_name, _shows, _body)
