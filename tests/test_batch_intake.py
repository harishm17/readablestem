"""A zip of documents as a batch (`readablestem.web.batch_intake`): what is a document in it, what
is one project, what is left out, and every refusal. The archive rules themselves are in
tests/test_archive_intake.py; the web routes in tests/test_web_batch.py."""

from __future__ import annotations

import io
import stat
import zipfile

from fixtures.web.support import make_pdf
from readablestem.source import intake
from readablestem.web import archive_intake as arc
from readablestem.web import batch_intake
from source_support import make_zip, png_bytes

ARTICLE = "\\documentclass{article}\n\\begin{document}\nHello $x$.\n\\end{document}\n"
PDF = make_pdf(1)


def names(result) -> list[str]:
    assert isinstance(result, list), result
    return [f.filename for f in result]


def test_a_zip_of_pdfs_is_a_batch_of_documents():
    data = make_zip({"a.pdf": PDF, "Unit 1/b.pdf": PDF, "Unit 1/Week 2/c.pdf": PDF})
    result = batch_intake.split("notes.zip", data)
    assert names(result) == ["a.pdf", "b.pdf", "c.pdf"]
    assert all(f.data == PDF for f in result)  # type: ignore[union-attr]


def test_folders_with_a_main_file_are_projects_and_zipped_again_from_their_own_files():
    data = make_zip(
        {
            "lecture1/main.tex": ARTICLE,
            "lecture1/figs/a.png": png_bytes(),
            "lecture2/paper.tex": ARTICLE,
            "slides.pdf": PDF,
        }
    )
    result = batch_intake.split("term.zip", data)
    assert names(result) == ["slides.pdf", "lecture1.zip", "lecture2.zip"]
    project = result[1].data  # type: ignore[index]
    with zipfile.ZipFile(io.BytesIO(project)) as inner:
        assert sorted(inner.namelist()) == [
            "figs/a.png",
            "main.tex",
        ]  # paths relative to the folder
    assert intake.inspect("lecture1.zip", project).main == "main.tex"  # the source path takes it


def test_a_pdf_inside_a_project_is_its_figure_not_a_document():
    data = make_zip({"p1/main.tex": ARTICLE, "p1/fig.pdf": PDF, "other.pdf": PDF})
    assert names(batch_intake.split("x.zip", data)) == ["other.pdf", "p1.zip"]


def test_a_zip_with_a_main_file_at_its_root_is_one_project_not_a_batch():
    data = make_zip({"main.tex": ARTICLE, "fig.png": png_bytes(), "extra.pdf": PDF})
    assert batch_intake.split("project.zip", data) is None


def test_nested_projects_are_one_project_under_the_outer_folder():
    data = make_zip({"p/main.tex": ARTICLE, "p/old/draft.tex": ARTICLE})
    assert names(batch_intake.split("x.zip", data)) == ["p.zip"]


def test_stray_files_are_left_out_not_refused():
    data = make_zip(
        {"a.pdf": PDF, "notes.txt": "x", "loose.tex": "\\section{x}", "pic.png": png_bytes()}
    )
    assert names(batch_intake.split("x.zip", data)) == ["a.pdf"]


def test_a_zip_with_no_document_is_refused():
    assert batch_intake.split("x.zip", make_zip({"notes.txt": "x"})) == "zip_empty"
    assert batch_intake.split("x.zip", make_zip({"loose.tex": "\\section{x}"})) == "zip_empty"


def test_anything_that_is_not_a_zip_is_not_a_batch():
    assert batch_intake.split("a.zip", b"%PDF-1.7") is None
    assert batch_intake.split("a.zip", b"") is None


def test_the_archive_rules_apply_and_the_refusal_is_a_category():
    assert batch_intake.split("x.zip", make_zip({"../evil.pdf": PDF})) == "zip_unsafe"
    link = (stat.S_IFLNK | 0o777) << 16
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("a.pdf")
        info.external_attr = link
        archive.writestr(info, "/etc/passwd")
    assert batch_intake.split("x.zip", buffer.getvalue()) == "zip_unsafe"
    assert (
        batch_intake.split("x.zip", make_zip({"a.pdf": PDF, "inner.zip": "x"})) == "zip_unsupported"
    )
    assert (
        batch_intake.split("x.zip", make_zip({"a.pdf": PDF, "fig.eps": "x"})) == "zip_unsupported"
    )
    broken = b"PK\x03\x04" + b"\0" * 30
    assert batch_intake.split("x.zip", broken) == "zip_bad"
    many = make_zip({f"f{i}.pdf": b"x" for i in range(501)})
    assert batch_intake.split("x.zip", many) == "zip_limits"


def test_a_bomb_and_an_oversize_zip_are_refused_before_anything_is_read():
    bomb = make_zip({"a.pdf": PDF, "pad.txt": b"\0" * (60 * 1024 * 1024)})
    assert batch_intake.split("x.zip", bomb) == "zip_limits"
    assert batch_intake.split("x.zip", b"PK\x03\x04" + b"0" * (21 * 1024 * 1024)) == "zip_limits"


def test_the_batch_limit_is_tighter_than_a_single_project_s():
    assert batch_intake.BATCH_LIMITS.max_unpacked_bytes < arc.DEFAULT_LIMITS.max_unpacked_bytes


def test_a_header_that_understates_its_size_cannot_smuggle_bytes_in():
    real = b"\0" * (3 * 1024 * 1024)
    data = bytearray(make_zip({"a.pdf": real}))
    for marker, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        at = data.find(marker)
        data[at + offset : at + offset + 4] = (10).to_bytes(4, "little")
    outcome = batch_intake.split("x.zip", bytes(data))
    assert outcome in {"zip_limits", "zip_bad"} or (
        isinstance(outcome, list) and len(outcome[0].data) <= 3 * 1024 * 1024
    )
