"""A zip of documents as a batch (`readablestem.web.batch_intake`): what is a document in it, what
is one project, what is left out, and every refusal. The archive rules themselves are in
tests/test_archive_intake.py; the web routes in tests/test_web_batch.py."""

from __future__ import annotations

import io
import stat
import zipfile

import pytest

from fixtures.web.support import make_pdf
from readablestem.source import intake
from readablestem.web import archive_intake as arc
from readablestem.web import batch_intake
from source_support import make_zip, png_bytes

ARTICLE = "\\documentclass{article}\n\\begin{document}\nHello $x$.\n\\end{document}\n"
PDF = make_pdf(1)


def _noise(n: int) -> bytes:
    """Bytes that do not compress (a bomb is refused by its ratio before the budget matters)."""
    import os

    return os.urandom(n)


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


def test_the_budget_is_shared_by_every_zip_of_a_request():
    budget = batch_intake.RequestBudget(5 * 1024 * 1024)
    big = make_zip({"a.pdf": b"%PDF-1.7 " + _noise(3_000_000)})  # about 3 MB
    first = batch_intake.split("one.zip", big, budget=budget)
    second = batch_intake.split("two.zip", big, budget=budget)
    assert isinstance(first, list) and isinstance(second, list)
    assert len(first[0].data) > 3_000_000  # the first zip reads its member
    assert budget.used == len(first[0].data)
    with pytest.raises(arc.ArchiveRefused) as raised:
        second[0].data  # noqa: B018 - the second would take it past the budget
    assert raised.value.code == "unpacked_too_large"
    assert budget.used <= budget.total + 3_100_000  # stopped at the budget, not after the member


def test_nothing_is_read_until_a_member_s_turn_and_only_text_for_the_projects():
    budget = batch_intake.RequestBudget(50 * 1024 * 1024)
    data = make_zip({"a.pdf": PDF, "b.pdf": PDF, "p/main.tex": ARTICLE})
    members = batch_intake.split("x.zip", data, budget=budget)
    assert isinstance(members, list) and len(members) == 3
    assert budget.used == len(ARTICLE.encode())  # only the .tex was read, to find the project
    members[0].data  # noqa: B018
    assert budget.used == len(ARTICLE.encode()) + len(PDF)
    members[0].release()
    assert members[0]._data is None


def test_a_member_is_never_read_past_the_cap_or_the_header_s_claim():
    data = make_zip({"a.pdf": b"%PDF" + _noise(3 * 1024 * 1024)})
    members = batch_intake.split("x.zip", data, member_cap=1024 * 1024)
    assert isinstance(members, list)
    with pytest.raises(arc.ArchiveRefused):
        members[0].data  # noqa: B018
    assert members[0].size > 1024 * 1024  # the caller can refuse on this before any read


def test_ten_zips_of_bombs_in_one_request_cannot_hold_more_than_the_budget():
    import tracemalloc

    member = _noise(2 * 1024 * 1024)
    zips = [make_zip({"a.pdf": member, "b.pdf": member[: 1024 * 1024]}) for _ in range(10)]
    budget = batch_intake.RequestBudget(6 * 1024 * 1024)
    tracemalloc.start()
    held = 0
    for n, data in enumerate(zips):
        members = batch_intake.split(f"z{n}.zip", data, budget=budget)
        assert isinstance(members, list)
        for member_ in members:
            try:
                held = max(held, len(member_.data))
            except arc.ArchiveRefused:
                break
            member_.release()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert budget.used <= budget.total + 2 * 1024 * 1024 + 1  # the read that crosses it is cut
    assert peak < 3 * 6 * 1024 * 1024  # never the 10 zips' 30 MB at once
    assert held <= 2 * 1024 * 1024
