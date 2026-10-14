"""T3-30: Word and PowerPoint files to a tagged PDF, in a sandboxed child process.

Checks that need no LibreOffice (the file checks, the limits, the clean-up) use a stand-in
for `soffice`; the ones that convert a real file are skipped where LibreOffice (with its
Writer component) is not installed.
"""

from __future__ import annotations

import json
import os
import random
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf
import pytest

from a11y_stem.pipeline import tagged
from a11y_stem.web import office_convert
from a11y_stem.web.office_convert import OfficeRejected, preflight
from fixtures import office

SOFFICE = office_convert.soffice_command()
HAS_WRITER = "docx" in office_convert.available_kinds(SOFFICE)
needs_writer = pytest.mark.skipif(not HAS_WRITER, reason="LibreOffice Writer is not installed")


def refused(data: bytes) -> str:
    with pytest.raises(OfficeRejected) as raised:
        preflight(data)
    return raised.value.code


# --------------------------------------------------------------------------- the file checks


def test_a_word_document_and_a_presentation_are_told_apart():
    assert preflight(office.docx()) == "docx"
    assert preflight(office.pptx()) == "pptx"


def test_the_file_name_decides_nothing_the_parts_do():
    # The same bytes under any name are the same kind: only the ZIP's parts are read.
    assert preflight(office.pptx()) == "pptx"


def test_an_old_or_password_protected_file_is_named_as_such():
    assert refused(office.OLE_HEADER) == "office_old_or_locked"


@pytest.mark.parametrize(
    "data",
    [
        b"plain text",
        b"",
        office.rebuild({"hello.txt": b"hi"}),
        office.rebuild({"[Content_Types].xml": "<Types/>", "xl/workbook.xml": "<workbook/>"}),
        office.rebuild(office.docx_parts(), add={"ppt/presentation.xml": "<p:presentation/>"}),
        office.rebuild(office.docx_parts(), drop=("[Content_Types].xml",)),
    ],
    ids=["text", "empty", "zip-of-text", "spreadsheet", "both-kinds", "no-content-types"],
)
def test_other_files_are_not_word_or_powerpoint(data):
    assert refused(data) == "office_unsupported"


def test_a_damaged_zip_is_unreadable():
    assert refused(office.docx()[:-30]) == "unreadable"
    assert refused(b"PK\x03\x04" + b"\x00" * 40) == "unreadable"


def test_macros_are_refused():
    parts = office.docx_parts()
    assert refused(office.rebuild(parts, add={"word/vbaProject.bin": b"\x00"})) == "office_unsafe"
    macro_types = str(parts["[Content_Types].xml"]).replace(
        "wordprocessingml.document.main+xml", "ms-word.document.macroEnabled.main+xml"
    )
    assert (
        refused(office.rebuild(parts, replace={"[Content_Types].xml": macro_types}))
        == "office_unsafe"
    )


def test_a_linked_picture_is_refused_but_an_ordinary_hyperlink_is_not():
    assert refused(office.with_external_picture()) == "office_unsafe"
    assert preflight(office.with_hyperlink()) == "docx"


@pytest.mark.parametrize(
    "target",
    [
        "http://example.org/x.png",
        "file:///etc/passwd",
        "\\\\\\\\host\\\\share\\\\x.png",
        "//host/x",
    ],
)
def test_every_kind_of_outside_address_in_a_picture_relationship_is_refused(target):
    assert refused(office.with_external_picture(target)) == "office_unsafe"


def test_a_target_that_names_an_address_without_saying_external_is_refused_too():
    parts = office.docx_parts()
    rels = str(parts["word/_rels/document.xml.rels"]).replace(
        'Target="media/image1.png"', 'Target="http://example.org/x.png"'
    )
    assert (
        refused(office.rebuild(parts, replace={"word/_rels/document.xml.rels": rels}))
        == "office_unsafe"
    )


def test_a_pptx_with_a_linked_object_is_refused():
    parts = office.pptx_parts()
    rels = str(parts["ppt/slides/_rels/slide2.xml.rels"]).replace(
        "</Relationships>",
        f'<Relationship Id="rId5" Type="{office.OD}/oleObject" Target="file:///etc/passwd" '
        'TargetMode="External"/></Relationships>',
    )
    assert (
        refused(office.rebuild(parts, replace={"ppt/slides/_rels/slide2.xml.rels": rels}))
        == "office_unsafe"
    )


@pytest.mark.parametrize(
    "declaration",
    [
        '<!DOCTYPE w:document [<!ENTITY x "boom">]>',
        '<!doctype w:document SYSTEM "file:///etc/passwd">',
        "<!ENTITY a 'b'>",
    ],
)
def test_a_document_type_or_entity_declaration_is_refused_in_any_part(declaration):
    parts = office.docx_parts()
    document = str(parts["word/document.xml"]).replace(
        "<w:document", declaration + "<w:document", 1
    )
    assert (
        refused(office.rebuild(parts, replace={"word/document.xml": document})) == "office_unsafe"
    )
    styles = str(parts["word/styles.xml"]).replace("<w:styles", declaration + "<w:styles", 1)
    assert refused(office.rebuild(parts, replace={"word/styles.xml": styles})) == "office_unsafe"
    custom = "<root>" + declaration + "</root>"
    assert refused(office.rebuild(parts, add={"customXml/item1.xml": custom})) == "office_unsafe"


def test_a_declaration_cannot_hide_across_a_read_boundary():
    parts = office.docx_parts()
    for pad in (office_convert._CHUNK - 5, office_convert._CHUNK - 2, office_convert._CHUNK):
        padded = "<root><!-- " + "x" * pad + " --><!DOCTYPE a></root>"
        assert (
            refused(office.rebuild(parts, add={"customXml/item1.xml": padded})) == "office_unsafe"
        ), pad


def test_utf16_xml_is_refused():
    parts = office.docx_parts()
    document = str(parts["word/document.xml"]).encode("utf-16")
    assert (
        refused(office.rebuild(parts, replace={"word/document.xml": document})) == "office_unsafe"
    )


def test_encrypted_entries_are_refused():
    data = bytearray(office.docx())
    position = data.find(b"PK\x01\x02")
    while position != -1:
        data[position + 8] |= 0x1  # the general purpose flag of a central directory entry
        position = data.find(b"PK\x01\x02", position + 4)
    assert refused(bytes(data)) == "encrypted"


def test_size_and_count_limits(monkeypatch):
    many = {f"customXml/{n}.xml": b"<a/>" for n in range(office_convert.MAX_ENTRIES + 1)}
    assert refused(office.rebuild(office.docx_parts(), add=many)) == "office_unsafe"
    monkeypatch.setattr(office_convert, "MAX_UNPACKED_BYTES", 10_000)
    big = {
        "customXml/a.xml": b"<a>" + b"x" * 8000 + b"</a>",
        "customXml/b.xml": b"<a>" + b"y" * 8000 + b"</a>",
    }
    assert refused(office.rebuild(office.docx_parts(), add=big)) == "office_unsafe"
    monkeypatch.setattr(office_convert, "MAX_ENTRY_BYTES", 5_000)
    monkeypatch.setattr(office_convert, "MAX_UNPACKED_BYTES", 10**9)
    assert refused(office.rebuild(office.docx_parts(), add={"customXml/a.xml": b"x" * 6000})) == (
        "office_unsafe"
    )


def test_a_decompression_bomb_is_refused_without_being_unpacked():
    bomb = office.rebuild(
        office.docx_parts(), add={f"media/{n}.bin": bytes(60_000_000) for n in range(4)}
    )
    assert len(bomb) < 2_000_000
    assert refused(bomb) == "office_unsafe"


def test_a_file_that_is_not_what_it_looks_like_never_escapes_as_another_exception():
    """Byte mutations of a good file, and truncations: a category or a kind, never a crash."""
    rng = random.Random(30)
    for source in (office.docx(), office.pptx()):
        for _ in range(150):
            mutated = bytearray(source)
            for _ in range(rng.randint(1, 8)):
                mutated[rng.randrange(len(mutated))] = rng.randrange(256)
            try:
                assert preflight(bytes(mutated)) in {"docx", "pptx"}
            except OfficeRejected as rejected:
                assert rejected.code.startswith(("office_", "unreadable", "encrypted"))
        for cut in (1, 10, 100, len(source) // 2):
            with pytest.raises(OfficeRejected):
                preflight(source[:-cut])


# --------------------------------------------------------------------------- the profile


def test_the_profile_turns_macros_off_and_points_every_proxy_at_a_closed_port():
    xcu = office_convert._registry_modifications()
    assert 'oor:name="MacroSecurityLevel"' in xcu and "<value>3</value>" in xcu
    assert "DisableMacrosExecution" in xcu
    for protocol in ("HTTP", "HTTPS", "FTP"):
        assert f"ooInet{protocol}ProxyPort" in xcu
    assert xcu.count("<value>9</value>") == 3
    import xml.dom.minidom

    xml.dom.minidom.parseString(xcu)  # well formed


# --------------------------------------------------------------------------- stand-ins


def stand_in(tmp_path: Path, body: str, name: str = "soffice") -> str:
    """A fake `soffice`: a Python script run by this interpreter."""
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\nimport os, sys, time\nargs = sys.argv[1:]\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


OUT_DIR = "out = args[args.index('--outdir') + 1]\nsrc = args[-1]\n"


def a_pdf() -> bytes:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "converted")
    return document.tobytes()


def run_convert(soffice: str, timeout_s: float = 20.0, data: bytes | None = None):
    return office_convert.convert(data or office.docx(), "docx", soffice, timeout_s)


def test_a_conversion_that_works_returns_the_pdf_and_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    pdf = tmp_path / "made.pdf"
    pdf.write_bytes(a_pdf())
    soffice = stand_in(
        tmp_path, OUT_DIR + f"open(out + '/input.pdf', 'wb').write(open({str(pdf)!r}, 'rb').read())"
    )
    assert run_convert(soffice).startswith(b"%PDF-")
    assert [p for p in tmp_path.iterdir() if p.name.startswith("a11y-office-")] == []


def test_the_command_line_is_fixed_and_the_file_name_is_never_the_users(tmp_path, monkeypatch):
    seen = tmp_path / "args.json"
    soffice = stand_in(
        tmp_path,
        "import json\n"
        f"listing = os.listdir(os.path.dirname(args[-1]))\n"
        f"open({str(seen)!r}, 'w').write(json.dumps([args, listing]))\n"
        "sys.exit(1)",
    )
    with pytest.raises(OfficeRejected):
        run_convert(soffice)
    args, listing = json.loads(seen.read_text())
    assert args[0].startswith("-env:UserInstallation=file://")
    assert "--headless" in args and "--norestore" in args
    assert "--convert-to" in args
    chosen = args[args.index("--convert-to") + 1]
    assert chosen.startswith("pdf:writer_pdf_Export:") and '"UseTaggedPDF"' in chosen
    assert Path(args[-1]).name == "input.docx" and "input.docx" in listing


def test_libreoffice_runs_with_no_secrets_and_in_its_own_directory(tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "SESSION_SECRET", "DATABASE_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "secret-value")
    dump = tmp_path / "env.json"
    soffice = stand_in(
        tmp_path,
        f"open({str(dump)!r}, 'w').write(__import__('json').dumps(dict(os.environ)))\nsys.exit(1)",
    )
    with pytest.raises(OfficeRejected):
        run_convert(soffice)
    environment = json.loads(dump.read_text())
    assert "secret-value" not in json.dumps(environment)
    assert environment["HOME"] == environment["TMPDIR"]
    assert "a11y-office-" in environment["HOME"]


def test_a_conversion_that_hangs_is_killed_with_its_helpers(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    pids = tmp_path / "pids"
    soffice = stand_in(
        tmp_path,
        "import subprocess\n"
        "helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
        f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{helper.pid}}')\n"
        "time.sleep(300)",
    )
    started = time_now()
    with pytest.raises(OfficeRejected) as raised:
        run_convert(soffice, timeout_s=1.5)
    assert raised.value.code == "office_failed"
    assert time_now() - started < 15
    for pid in map(int, pids.read_text().split()):
        assert not alive(pid), pid
    assert [p for p in tmp_path.iterdir() if p.name.startswith("a11y-office-")] == []


def time_now() -> float:
    import time

    return time.monotonic()


def alive(pid: int) -> bool:
    import time

    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        try:  # a zombie still answers signal 0: look at its state
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except OSError:
            return False
        if state == "Z":
            return False
        time.sleep(0.1)
    return True


def test_memory_is_capped(tmp_path):
    soffice = stand_in(
        tmp_path,
        "x = bytearray(3 * 1024 * 1024 * 1024)\n"
        f"open({str(tmp_path / 'allocated')!r}, 'w').write('yes')",
    )
    with pytest.raises(OfficeRejected) as raised:
        run_convert(soffice)
    assert raised.value.code == "office_failed"
    assert not (tmp_path / "allocated").exists()  # 3 GiB was refused, not given


@pytest.mark.parametrize(
    "body",
    [
        "pass",  # exits 0 and makes nothing
        OUT_DIR + "open(out + '/input.pdf', 'wb').write(b'not a pdf at all')",
        "sys.exit(3)",
        OUT_DIR + "open(out + '/input.pdf', 'wb').write(b'%PDF-1.4 ' + bytes(200 * 1024 * 1024))",
    ],
    ids=["nothing", "not-a-pdf", "error-exit", "too-big"],
)
def test_anything_but_a_pdf_is_a_failure(tmp_path, body):
    with pytest.raises(OfficeRejected) as raised:
        run_convert(stand_in(tmp_path, body))
    assert raised.value.code == "office_failed"


def test_a_missing_program_is_a_failure(tmp_path):
    with pytest.raises(OfficeRejected) as raised:
        run_convert(str(tmp_path / "nowhere"))
    assert raised.value.code == "office_failed"


# --------------------------------------------------------------------------- the child as a process


def child(data: bytes, soffice: str, timeout_s: float = 60) -> tuple[dict, bytes]:
    done = subprocess.run(
        [sys.executable, "-m", "a11y_stem.web.office_convert", soffice, str(timeout_s)],
        input=data,
        capture_output=True,
        timeout=timeout_s + 30,
        check=False,
    )
    header, _, body = done.stdout.partition(b"\n")
    return json.loads(header), body


def test_the_child_answers_with_a_category_for_a_refused_file(tmp_path):
    answer, body = child(office.with_external_picture(), stand_in(tmp_path, "sys.exit(9)"))
    assert answer == {"ok": False, "code": "office_unsafe"} and body == b""
    answer, _ = child(b"%PDF-1.4 hello", stand_in(tmp_path, "sys.exit(9)"))
    assert answer["code"] == "office_unsupported"
    answer, _ = child(office.OLE_HEADER, stand_in(tmp_path, "sys.exit(9)"))
    assert answer["code"] == "office_old_or_locked"


def test_the_child_says_when_a_kind_is_not_installed(tmp_path):
    # A Debian-style layout with Writer's registry file but not Impress's.
    root = tmp_path / "lo"
    (root / "share" / "registry").mkdir(parents=True)
    (root / "program").mkdir()
    (root / "share" / "registry" / "main.xcd").write_text("")
    (root / "share" / "registry" / "writer.xcd").write_text("")
    soffice = root / "program" / "soffice"
    soffice.write_text("#!/bin/sh\nexit 1\n")
    soffice.chmod(0o755)
    assert office_convert.available_kinds(str(soffice)) == ("docx",)
    answer, _ = child(office.pptx(), str(soffice))
    assert answer == {"ok": False, "code": "office_unavailable"}


def test_the_child_has_a_memory_cap_of_its_own(tmp_path):
    code = (
        "import resource; from a11y_stem.web.pdf_probe import limit_memory; "
        "from a11y_stem.web import office_convert as o; limit_memory(o.WRAPPER_MEMORY_BYTES); "
        "print(resource.getrlimit(resource.RLIMIT_AS)[0])"
    )
    soft = int(subprocess.run([sys.executable, "-c", code], capture_output=True, check=True).stdout)
    assert soft <= office_convert.WRAPPER_MEMORY_BYTES


# --------------------------------------------------------------------------- real LibreOffice


@needs_writer
def test_a_word_document_becomes_a_tagged_pdf_with_its_headings_and_alt_text():
    assert SOFFICE is not None
    answer, body = child(office.docx(), SOFFICE)
    assert answer == {"ok": True, "kind": "docx"}
    assert body.startswith(b"%PDF-")
    document = pymupdf.open(stream=body)
    assert document.page_count == 1
    text = document[0].get_text()
    assert "Kinematics in one dimension" in text and "4 m/s" in text
    found = tagged.read(body, [0])
    assert [(h.level, h.text) for h in found.headings] == [
        (1, "Kinematics in one dimension"),
        (2, "A worked example"),
    ]
    assert [f.alt for f in found.figures] == [office.ALT]


@needs_writer
def test_libreoffice_fetches_nothing_even_if_a_check_missed_a_linked_picture():
    """The profile's proxy and link settings are the second line behind `preflight`: the
    refused file is converted anyway (preflight skipped) while something listens."""
    assert SOFFICE is not None
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        listener.settimeout(8)
        port = listener.getsockname()[1]
        linked = office.with_external_picture(f"http://127.0.0.1:{port}/ext.png")
        pdf = office_convert.convert(linked, "docx", SOFFICE, 60)
        assert pdf.startswith(b"%PDF-")
        with pytest.raises(socket.timeout):
            listener.accept()


@needs_writer
def test_a_hostile_but_well_formed_document_ends_in_a_category_or_a_pdf(tmp_path):
    """A document with a very deep table nest: whatever LibreOffice does, the child answers."""
    assert SOFFICE is not None
    depth = 400
    cell = "<w:p><w:r><w:t>x</w:t></w:r></w:p>"
    for _ in range(depth):
        cell = f"<w:tbl><w:tr><w:tc>{cell}</w:tc></w:tr></w:tbl><w:p/>"
    parts = office.docx_parts()
    document = str(parts["word/document.xml"]).replace("<w:body>", "<w:body>" + cell, 1)
    answer, body = child(
        office.rebuild(parts, replace={"word/document.xml": document}), SOFFICE, 40
    )
    assert answer["ok"] is (body.startswith(b"%PDF-"))
    if not answer["ok"]:
        assert answer["code"] in {"office_failed", "office_unsafe"}
