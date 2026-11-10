"""Unpacking an uploaded zip (`readablestem.web.archive_intake`): every refusal, and that a
plain project unpacks as it is. No LaTeXML and no sandbox here: this is the archive layer."""

from __future__ import annotations

import io
import os
import stat
import struct
import tempfile
import zipfile
from pathlib import Path

import pytest

from readablestem.web import archive_intake as arc

LIMITS = arc.ArchiveLimits()


def zip_with(members: list[tuple[str, bytes, int | None]], *, method=zipfile.ZIP_DEFLATED) -> bytes:
    """(name, data, external_attr or None) members as a zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method) as archive:
        for name, data, attr in members:
            info = zipfile.ZipInfo(name)
            if attr is not None:
                info.external_attr = attr
            archive.writestr(info, data, compress_type=method)
    return buffer.getvalue()


def plain(*names: str) -> bytes:
    return zip_with([(n, b"x", None) for n in names])


def refused(data: bytes, tmp_path: Path, limits: arc.ArchiveLimits = LIMITS) -> str:
    dest = Path(tempfile.mkdtemp(dir=tmp_path))
    with pytest.raises(arc.ArchiveRefused) as raised:
        arc.extract(data, dest, limits)
    return raised.value.code


def test_a_project_unpacks_with_its_own_paths(tmp_path):
    data = zip_with(
        [
            ("main.tex", b"\\documentclass{article}", None),
            ("chapters/one.tex", b"one", None),
            ("figs/a.png", b"\x89PNG", None),
            ("chapters/", b"", None),
        ]
    )
    dest = tmp_path / "p"
    dest.mkdir()
    written = arc.extract(data, dest)
    assert sorted(e.path for e in written) == ["chapters/one.tex", "figs/a.png", "main.tex"]
    assert (dest / "chapters" / "one.tex").read_bytes() == b"one"
    assert oct((dest / "main.tex").stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize(
    "name",
    [
        "../evil.tex",
        "a/../../evil.tex",
        "/etc/passwd.tex",
        "C:/evil.tex",
        "C:\\evil.tex",
        "a\\b.tex",
        "a//b.tex",
        "./a.tex",
        "a/./b.tex",
        "a\nb.tex",
        "x/" * 20 + "a.tex",
        "a" * 300 + ".tex",
    ],
)
def test_unsafe_paths_are_refused(name, tmp_path):
    assert refused(plain(name), tmp_path) == "unsafe_path"


def test_nothing_is_written_outside_the_folder_even_for_a_traversal(tmp_path):
    outside = tmp_path / "evil.tex"
    assert refused(plain("../evil.tex"), tmp_path) == "unsafe_path"
    assert not outside.exists()
    assert [p.name for p in tmp_path.iterdir()] != ["evil.tex"]
    assert not any(p.name == "evil.tex" for p in tmp_path.rglob("*"))


def test_a_symlink_member_is_refused(tmp_path):
    link = (stat.S_IFLNK | 0o777) << 16
    data = zip_with([("main.tex", b"/etc/passwd", link)])
    assert refused(data, tmp_path) == "symlink"


@pytest.mark.parametrize("kind", [stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK])
def test_devices_fifos_and_sockets_are_refused(kind, tmp_path):
    data = zip_with([("a.tex", b"", (kind | 0o644) << 16)])
    assert refused(data, tmp_path) == "symlink"


def test_a_symlink_already_in_the_target_is_not_followed(tmp_path):
    dest = tmp_path / "p"
    dest.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("keep")
    os.symlink(secret, dest / "main.tex")  # what a hostile earlier member could have left
    with pytest.raises(arc.ArchiveRefused):
        arc.extract(plain("main.tex"), dest)
    assert secret.read_text() == "keep"


def test_a_file_used_as_a_folder_is_refused(tmp_path):
    assert refused(plain("a.txt", "a.txt/b.tex"), tmp_path) == "unsafe_path"
    assert refused(plain("a.txt/b.tex", "a.txt"), tmp_path) == "unsafe_path"


def test_a_zip_over_the_archive_limit_is_refused_before_it_is_opened(tmp_path):
    small = arc.ArchiveLimits(max_zip_bytes=100)
    assert refused(plain("a.tex", "b.tex", "c.tex"), tmp_path, small) == "too_large"


def test_declared_unpacked_size_over_the_limit(tmp_path):
    limits = arc.ArchiveLimits(max_unpacked_bytes=1000, max_file_bytes=900)
    data = zip_with([("a.tex", b"a" * 600, None), ("b.tex", b"b" * 600, None)])
    assert refused(data, tmp_path, limits) == "unpacked_too_large"
    one = zip_with([("a.tex", b"a" * 950, None)])
    assert refused(one, tmp_path, limits) == "unpacked_too_large"


def test_a_zip_bomb_is_refused_by_its_ratio(tmp_path):
    bomb = zip_with([("a.tex", b"\0" * (30 * 1024 * 1024), None)])
    assert len(bomb) < 100_000
    limits = arc.ArchiveLimits(max_file_bytes=50 * 1024 * 1024)
    assert refused(bomb, tmp_path, limits) == "bomb"


def lying_zip(real: bytes, claimed: int) -> bytes:
    """A zip whose central directory and local header claim `claimed` bytes for a member that
    really holds `real` compressed to nothing much."""
    data = bytearray(zip_with([("a.tex", real, None)]))
    packed = struct.pack("<I", claimed)
    # local file header: crc(14) comp(18) uncomp(22); central directory: uncomp at +24
    for marker, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        at = data.find(marker)
        data[at + offset : at + offset + 4] = packed
    return bytes(data)


def test_a_header_that_understates_its_size_still_stops_at_the_limit(tmp_path):
    real = b"a" * (3 * 1024 * 1024)
    lying = lying_zip(real, 10)  # claims 10 bytes, holds 3 MB
    limits = arc.ArchiveLimits(max_file_bytes=1024 * 1024, max_unpacked_bytes=2 * 1024 * 1024)
    dest = Path(tempfile.mkdtemp(dir=tmp_path))
    with pytest.raises(arc.ArchiveRefused) as raised:
        arc.extract(lying, dest, limits)
    assert raised.value.code in {"unpacked_too_large", "bad_zip", "bomb"}
    assert sum(p.stat().st_size for p in dest.rglob("*") if p.is_file()) <= 1024 * 1024 + 65536


def test_an_encrypted_member_is_refused(tmp_path):
    data = bytearray(plain("a.tex"))
    for marker, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = data.find(marker)
        data[at + offset] |= 0x01  # general purpose flag bit 0: encrypted
    assert refused(bytes(data), tmp_path) == "encrypted"


@pytest.mark.parametrize("data", [b"", b"not a zip at all", b"PK\x03\x04" + b"\0" * 10])
def test_not_a_zip_or_a_damaged_one(data, tmp_path):
    assert refused(data, tmp_path) in {"not_zip", "bad_zip"}


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("fig.eps", "eps"),
        ("fig.EPS", "eps"),
        ("a.ps", "eps"),
        ("nested.zip", "unsupported_file"),
        ("run.sh", "unsupported_file"),
        ("evil.exe", "unsupported_file"),
        ("figure.svg", "unsupported_file"),
        ("noextension", "unsupported_file"),
        (".latexmkrc", "unsupported_file"),
    ],
)
def test_extensions_off_the_list_are_refused(name, code, tmp_path):
    assert refused(plain("main.tex", name), tmp_path) == code


def test_build_leftovers_are_skipped_not_refused(tmp_path):
    names = ["main.tex", "main.aux", "main.log", "main.synctex.gz", ".DS_Store",
             "__MACOSX/._main.tex", ".git/config", "main.fdb_latexmk"]  # fmt: skip
    dest = tmp_path / "p"
    dest.mkdir()
    assert [e.path for e in arc.extract(plain(*names), dest)] == ["main.tex"]


def test_ignored_files_still_count_against_the_limits(tmp_path):
    limits = arc.ArchiveLimits(max_unpacked_bytes=100, max_file_bytes=100)
    data = zip_with([("main.tex", b"x" * 60, None), ("main.aux", b"y" * 60, None)])
    assert refused(data, tmp_path, limits) == "unpacked_too_large"


def test_a_hostile_name_inside_an_ignored_folder_is_still_checked(tmp_path):
    assert refused(plain("__MACOSX/../x.tex"), tmp_path) == "unsafe_path"
