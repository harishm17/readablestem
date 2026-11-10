"""The handwriting copy kit (scripts/handwriting_copy_set.py): the selection rule, the licence gate,
intake matching with synthetic PDFs, gold from source and the consent-aware manifest. Offline: a
fake OpenStax fetcher and PDFs drawn here."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pymupdf
import pytest

from readablestem.gold import openstax as ox

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "handwriting_copy_set", REPO / "scripts" / "handwriting_copy_set.py"
)
assert _spec is not None and _spec.loader is not None
kit = importlib.util.module_from_spec(_spec)
sys.modules["handwriting_copy_set"] = kit
_spec.loader.exec_module(kit)

CC_BY = "https://creativecommons.org/licenses/by/4.0/"
NC_SA = "https://creativecommons.org/licenses/by-nc-sa/4.0/"


# ----------------------------------------------------------------------- fake OpenStax


def math_xml(n: int) -> str:
    return (
        f'<math display="block"><semantics><mrow><mi>x</mi><mo>+</mo><mn>{n}</mn>'
        f'<mo>=</mo><mn>{n + 1}</mn></mrow><annotation-xml encoding="MathML-Content">'
        "<mi>hidden</mi></annotation-xml></semantics></math>"
    )


def example_xml(eid: str, *, maths: int = 4, words: int = 40, extra: str = "") -> str:
    prose = " ".join(["word"] * words)
    eqs = "".join(f"<div data-type='equation'>{math_xml(i)}</div>" for i in range(maths))
    return (
        f'<div data-type="example" id="{eid}"><h3><span>Example </span>1.1</h3>'
        f'<h4 data-type="title">Solve</h4><p>{prose}</p>{eqs}{extra}</div>'
    )


def page_xml(*examples: str) -> str:
    return f"<div><p>intro</p>{''.join(examples)}</div>"


class FakeFetcher:
    """Serves a CMS list, a release file, book trees and page contents from dictionaries."""

    def __init__(self, books: dict[str, dict]) -> None:
        self.books = books
        self.requests: list[str] = []

    def get_json(self, url: str):
        self.requests.append(url)
        if url == ox.CMS_BOOKS_URL:
            return {
                "items": [
                    {
                        "meta": {"slug": slug},
                        "title": b["title"],
                        "cnx_id": f"uuid-{slug}",
                        "book_state": b.get("state", "live"),
                        "license_name": "lic",
                        "license_url": b.get("cms_license", CC_BY),
                        "authors": [{"value": {"senior_author": True, "name": "A. Author"}}],
                    }
                    for slug, b in self.books.items()
                ]
            }
        if url == ox.RELEASE_URL:
            return {
                "archiveUrl": "/apps/archive/20260101.000000",
                "books": {f"uuid-{s}": {"defaultVersion": "v1"} for s in self.books},
            }
        for slug, b in self.books.items():
            base = f"{ox.OPENSTAX}/apps/archive/20260101.000000/contents/uuid-{slug}@v1"
            if url == f"{base}.json":
                return {
                    "title": b["title"],
                    "license": {"name": "lic", "url": b.get("license", CC_BY)},
                    "tree": {
                        "contents": [
                            {"id": f"{slug}-{key}@1", "slug": key, "title": title}
                            for key, (title, _) in b["pages"].items()
                        ]
                    },
                }
            for key, (_, content) in b["pages"].items():
                if url == f"{base}:{slug}-{key}.json":
                    return {"content": content}
        raise ox.GoldError(f"no such url {url}")


def make_books(n_pages: int = 12) -> dict[str, dict]:
    def pages(prefix: str, titles: list[str] | None = None):
        return {
            f"{i}-{i}-{prefix}{i}": (
                titles[i - 1] if titles else f"Section {prefix}{i}",
                page_xml(example_xml(f"ex-{prefix}{i}-a"), example_xml(f"ex-{prefix}{i}-b")),
            )
            for i in range(1, n_pages + 1)
        }

    return {
        "college-algebra": {"title": "College Algebra", "state": "retired", "pages": pages("a")},
        "precalculus": {
            "title": "Precalculus",
            "state": "retired",
            "pages": pages(
                "p",
                [f"Finding Limits {i}" if i % 2 else f"Lines {i}" for i in range(1, n_pages + 1)],
            ),
        },
        "college-physics": {"title": "College Physics", "state": "retired", "pages": pages("f")},
        "introductory-statistics": {
            "title": "Introductory Statistics",
            "state": "retired",
            "pages": pages("s"),
        },
        "chemistry": {"title": "Chemistry", "state": "retired", "pages": pages("c")},
        # In the committed table as NC-SA, so refused before any request.
        "calculus-volume-1": {
            "title": "Calculus 1",
            "license": NC_SA,
            "cms_license": NC_SA,
            "pages": pages("k"),
        },
    }


LICENSED = {
    "college-algebra",
    "precalculus",
    "college-physics",
    "introductory-statistics",
    "chemistry",
}


def run_select(books=None, *, seed="s1", writers=3, per_writer=4, licensed=LICENSED, **kw):
    fetcher = FakeFetcher(books or make_books())
    return (
        kit.select_passages(
            fetcher,
            seed=seed,
            writers=writers,
            per_writer=per_writer,
            date="2026-11-03",
            licensed=licensed,
            **kw,
        ),
        fetcher,
    )


# ----------------------------------------------------------------------- licence gate


def book(**over) -> ox.Book:
    fields = dict(
        slug="b", title="B", uuid="u", version="v", archive_path="/apps/archive/1", state="live",
        license_name="CC BY", license_url=CC_BY, cms_license_name="CC BY", cms_license_url=CC_BY,
    )  # fmt: skip
    fields.update(over)
    return ox.Book(**fields)


@pytest.mark.parametrize(
    "over",
    [
        {"license_url": NC_SA},
        {"cms_license_url": NC_SA},
        {"license_url": None},
        {"cms_license_url": None},
        {"license_url": "https://creativecommons.org/licenses/by-sa/4.0/"},
        {"state": "deprecated"},
        {"state": None},
    ],
)
def test_gate_refuses_everything_else(over):
    with pytest.raises(ox.GoldError):
        kit.licence_gate(book(**over))


def test_a_book_not_cc_by_in_the_table_is_never_requested():
    (selection, _), fetcher = run_select(licensed=LICENSED)
    assert {
        "book_slug": "calculus-volume-1",
        "reason": "not CC BY in the committed licence table",
    } in (selection["excluded_books"])
    assert not any("uuid-calculus-volume-1" in u for u in fetcher.requests)


# ------------------------------------------------------------------- passage rule


# ------------------------------------------------------------------------- selection


# --------------------------------------------------------------------- work folder


@pytest.fixture
def work(tmp_path) -> Path:
    (sel, sources), _ = run_select(writers=2, per_writer=3)
    w = tmp_path / "work"
    kit.write_selection(w, sel, sources)
    return w


def test_the_work_folder_must_be_outside_the_repo(tmp_path):
    assert kit.ensure_outside_repo(tmp_path, "x") == tmp_path.resolve()
    with pytest.raises(SystemExit):
        kit.ensure_outside_repo(REPO / "data" / "handwriting-copy", "x")
    with pytest.raises(SystemExit):
        kit.ensure_outside_repo(REPO, "x")


# ------------------------------------------------------------------------- brief


# ------------------------------------------------------------------ synthetic PDFs


def make_pdf(path: Path, *, strokes: int = 0, image: bool = False, pages: int = 1, meta=None):
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        for i in range(strokes):
            x = 40 + (i % 50) * 8
            y = 60 + (i // 50) * 9
            page.draw_rect(pymupdf.Rect(x, y, x + 3, y + 3), color=None, fill=(0, 0, 0))
        if image:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
            pix.clear_with(200)
            page.insert_image(page.rect, pixmap=pix)
    if meta:
        doc.set_metadata(meta)
    doc.save(path)
    doc.close()


def returns_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "returned"
    folder.mkdir()
    make_pdf(
        folder / "w01-01.pdf", strokes=100, meta={"author": "Pat Real", "title": "My notebook"}
    )
    make_pdf(folder / "W01_02.pdf", strokes=100)
    make_pdf(folder / "w02-01.pdf")  # blank
    make_pdf(folder / "stray.pdf", strokes=100)
    return folder


# --------------------------------------------------------------------------- gold


# ------------------------------------------------------- consent, manifest, export


def approve_all(work, reviewer="r01"):
    intake = json.loads((work / "intake.json").read_text())
    codes = [c for c, i in intake.items() if i["status"] == "ok"]
    kit.record_review(work, codes, reviewer, "2026-11-04", True)


def prepared(work, tmp_path):
    kit.run_intake(work, returns_folder(tmp_path))
    kit.record_consent(work, "w01", True, "2026-11-04")
    kit.record_consent(work, "w02", False, "2026-11-04")
    approve_all(work)


def test_manifest_publishes_only_consented_pages_and_counts_the_rest(work, tmp_path):
    prepared(work, tmp_path)
    kit.run_intake(work, _one_more(tmp_path, "w02-02"))
    approve_all(work)
    m = kit.build_manifest(work, "2026-11-05")
    pages = {p["code"]: p["page"] for p in m["passages"]}
    assert (
        pages["w01-01"]["status"] == "published" and pages["w01-01"]["file"] == "pages/w01-01.pdf"
    )
    assert pages["w02-02"]["status"] == "private"
    assert "file" not in pages["w02-02"] and "sha256" not in pages["w02-02"]
    assert pages["w02-01"] == {"status": "rejected", "reason": "rejected_no_ink"}
    assert pages["w01-03"] == {"status": "missing"}
    writers = {w["writer_id"]: w for w in m["writers"]}
    assert writers["w01"]["consent_publish"] and writers["w01"]["pages_published"] == 2
    assert not writers["w02"]["consent_publish"] and writers["w02"]["pages_private"] == 1
    assert m["totals"]["published"] == 2 and m["totals"]["private"] == 1
    assert m["pages_licence"] == "CC BY 4.0" and m["consent_question"] == kit.CONSENT_QUESTION
    blob = json.dumps(m)
    assert "Pat Real" not in blob and "stray" not in blob


def _one_more(tmp_path, name):
    folder = tmp_path / f"one_more_{name}"
    folder.mkdir()
    make_pdf(folder / f"{name}.pdf", strokes=100)
    return folder


def test_export_copies_only_consented_pages_and_strips_personal_metadata(work, tmp_path):
    prepared(work, tmp_path)
    kit.run_intake(work, _one_more(tmp_path, "w02-02"))
    approve_all(work)
    dest = tmp_path / "dest"
    totals = kit.run_export(work, dest, "2026-11-05")
    assert totals["published"] == 2 and totals["private"] == 1
    assert sorted(p.name for p in (dest / "pages").glob("*.pdf")) == ["w01-01.pdf", "w01-02.pdf"]
    assert (dest / "gold.jsonl").exists() and (dest / "ATTRIBUTION.md").exists()
    with pymupdf.open(dest / "pages" / "w01-01.pdf") as doc:
        assert not (doc.metadata.get("author") or doc.metadata.get("title"))
        assert sum(len(p.get_drawings()) for p in doc) >= 100  # the ink is kept
    manifest = json.loads((dest / "manifest.json").read_text())
    entry = next(p for p in manifest["passages"] if p["code"] == "w01-01")
    assert entry["page"]["sha256"] == kit.sha256_file(dest / "pages" / "w01-01.pdf")
    assert b"Pat Real" not in (dest / "pages" / "w01-01.pdf").read_bytes()
    attribution = (dest / "ATTRIBUTION.md").read_text()
    assert "College Physics" in attribution and "lic, " + CC_BY in attribution


def test_a_later_export_removes_pages_whose_consent_was_withdrawn(work, tmp_path):
    prepared(work, tmp_path)
    dest = tmp_path / "dest"
    kit.run_export(work, dest, "2026-11-05")
    kit.record_consent(work, "w01", False, "2026-11-06")
    kit.run_export(work, dest, "2026-11-06")
    assert list((dest / "pages").glob("*.pdf")) == []


# ----------------------------------------------- hardened stripping, review, consent text


def craft_identifying_pdf(path: Path) -> None:
    """A PDF with every kind of identifying material a notes app or a person can leave."""
    doc = pymupdf.open()
    page = doc.new_page()
    for i in range(40):
        page.draw_rect(pymupdf.Rect(40 + i * 5, 60, 43 + i * 5, 63), fill=(0, 0, 0), color=None)
    note = page.add_text_annot((50, 50), "secret content")
    note.set_info(title="Pat Real", content="secret content")
    note.update()
    page.insert_link(
        {
            "kind": pymupdf.LINK_URI,
            "from": pymupdf.Rect(10, 10, 50, 30),
            "uri": "https://pat.example/x",
        }
    )
    doc.set_toc([[1, "Pat notebook", 1]])
    doc.embfile_add("secret.txt", b"Pat Real private")
    doc.set_metadata(
        {
            "author": "Pat Real",
            "title": "Pat notes",
            "subject": "Pat",
            "keywords": "Pat",
            "producer": "GoodNotes 6 on Pat's iPad",
            "creator": "Pat Real's iPad",
        }
    )
    info = int(doc.xref_get_key(-1, "Info")[1].split()[0])
    doc.xref_set_key(info, "Owner", "(Pat Real)")
    doc.xref_set_key(page.xref, "PieceInfo", "<</App (Pat Real)>>")
    doc.xref_set_key(page.xref, "Thumb", "(Pat Real)")
    doc.xref_set_key(page.xref, "Metadata", "(Pat Real)")
    doc.save(path)
    doc.close()


def all_objects(path: Path) -> str:
    with pymupdf.open(path) as doc:
        return "\n".join(doc.xref_object(x) for x in range(1, doc.xref_length()))


def test_the_checker_sees_everything_in_a_crafted_pdf(tmp_path):
    src = tmp_path / "crafted.pdf"
    craft_identifying_pdf(src)
    found = kit.verify_publishable(src)
    for kind in [
        "info:author",
        "info:title",
        "producer",
        "outline",
        "embedded_files",
        "annotations",
        "links",
        "page_keys",
        "info_keys",
    ]:
        assert kind in found


def test_stripping_a_crafted_pdf_leaves_ink_and_nothing_identifying(tmp_path):
    src, dst = tmp_path / "crafted.pdf", tmp_path / "clean.pdf"
    craft_identifying_pdf(src)
    kit.strip_pdf_metadata(src, dst)
    assert kit.verify_publishable(dst) == []
    for needle in [b"Pat", b"secret", b"pat.example", b"Owner", b"notebook"]:
        assert needle not in dst.read_bytes()
    assert "Pat" not in all_objects(dst)
    with pymupdf.open(dst) as doc:
        assert sum(len(p.get_drawings()) for p in doc) >= 40
        assert doc.metadata["producer"] == "GoodNotes"
        assert doc.metadata["creator"] == "" and doc.get_toc() == [] and doc.embfile_count() == 0


def test_an_unknown_producer_is_published_only_as_other(tmp_path):
    src, dst = tmp_path / "a.pdf", tmp_path / "b.pdf"
    make_pdf(src, strokes=60, meta={"producer": "Pat's private tool"})
    kit.strip_pdf_metadata(src, dst)
    with pymupdf.open(dst) as doc:
        assert doc.metadata["producer"] == "other"
    assert kit.app_label(None, "") is None
    assert kit.app_label("GoodNotes 6", None) == "GoodNotes"


def test_export_fails_closed_when_a_pdf_cannot_be_cleaned(work, tmp_path, monkeypatch):
    prepared(work, tmp_path)
    monkeypatch.setattr(kit, "verify_publishable", lambda path: ["annotations"])
    dest = tmp_path / "dest"
    with pytest.raises(kit.ExportError, match="still holds identifying material"):
        kit.run_export(work, dest, "2026-11-05")
    assert list((dest / "pages").glob("*.pdf")) == []
    assert not (dest / "manifest.json").exists()


def test_review_renders_pngs_and_reports_hidden_text(work, tmp_path):
    folder = tmp_path / "r"
    folder.mkdir()
    make_pdf(folder / "w01-01.pdf", strokes=60, pages=2)
    doc = pymupdf.open(folder / "w01-01.pdf")
    doc[0].insert_text((50, 700), "hidden words here", fontsize=8)
    doc.saveIncr()
    doc.close()
    kit.run_intake(work, folder)
    summary = kit.render_review(work)
    assert summary["w01-01"] == {"pngs": 2, "hidden_text_words": 3}
    assert (work / "review" / "w01-01-1.png").stat().st_size > 0
    assert (work / "review" / "w01-01-2.png").exists()
    assert "hidden" in (work / "review" / "w01-01.txt").read_text()


def test_export_refuses_consented_pages_nobody_has_looked_at(work, tmp_path):
    kit.run_intake(work, returns_folder(tmp_path))
    kit.record_consent(work, "w01", True, "2026-11-04")
    m = kit.build_manifest(work, "2026-11-05")
    assert m["totals"]["awaiting_review"] == 2 and m["totals"]["published"] == 0
    with pytest.raises(kit.ExportError, match="w01-01, w01-02"):
        kit.run_export(work, tmp_path / "dest", "2026-11-05")
    assert not (tmp_path / "dest" / "pages").exists()


def test_a_review_records_a_pseudonymous_reviewer_and_date_in_the_manifest(work, tmp_path):
    prepared(work, tmp_path)
    passages = kit.build_manifest(work, "d")["passages"]
    page = next(p for p in passages if p["code"] == "w01-01")["page"]
    assert page["reviewed_by"] == "r01" and page["reviewed_on"] == "2026-11-04"
    with pytest.raises(kit.ExportError):
        kit.record_review(work, ["w01-01"], "Pat Real", "2026-11-04", True)
    with pytest.raises(kit.ExportError):
        kit.record_review(work, ["w01-03"], "r01", "2026-11-04", True)  # nothing returned


def test_a_rejected_review_keeps_the_page_private(work, tmp_path):
    prepared(work, tmp_path)
    kit.record_review(work, ["w01-02"], "r02", "2026-11-05", False)
    pages = {p["code"]: p["page"]["status"] for p in kit.build_manifest(work, "d")["passages"]}
    assert pages["w01-01"] == "published" and pages["w01-02"] == "private"


def test_a_page_replaced_after_review_must_be_reviewed_again(work, tmp_path):
    prepared(work, tmp_path)
    folder = tmp_path / "again"
    folder.mkdir()
    make_pdf(folder / "w01-01.pdf", strokes=77)
    kit.run_intake(work, folder)
    pages = {p["code"]: p["page"]["status"] for p in kit.build_manifest(work, "d")["passages"]}
    assert pages["w01-01"] == "awaiting_review" and pages["w01-02"] == "published"


def test_a_yes_given_to_an_older_question_is_not_consent(work, tmp_path, monkeypatch):
    prepared(work, tmp_path)
    monkeypatch.setattr(kit, "CONSENT_QUESTION", kit.CONSENT_QUESTION + " (changed)")
    m = kit.build_manifest(work, "d")
    assert m["totals"]["published"] == 0 and m["totals"]["private"] == 2
    w01 = next(w for w in m["writers"] if w["writer_id"] == "w01")
    assert w01["consent_out_of_date"] and not w01["consent_publish"]
    kit.record_consent(work, "w01", True, "2026-11-06")  # asked again
    assert kit.build_manifest(work, "d")["totals"]["published"] == 2


def test_attribution_uses_each_books_own_licence(work, tmp_path):
    sel = kit.load_selection(work)
    sel["passages"][0]["license_name"] = "Creative Commons Attribution License 3.0"
    sel["passages"][0]["license_url"] = "https://creativecommons.org/licenses/by/3.0/"
    text = kit.render_attribution(sel)
    assert "Attribution License 3.0, https://creativecommons.org/licenses/by/3.0/" in text
    prepared(work, tmp_path)
    m = kit.build_manifest(work, "d")
    assert m["gold_licence"] == ["lic (" + CC_BY + ")"]


def test_the_consent_text_says_who_asks_ages_withdrawal_retention_and_review():
    text = kit.CONSENT_QUESTION
    for phrase in [
        "ReadableSTEM project, maintained by Harish Manoharan",
        "18 or over",
        "{send_to}",
        f"{kit.RETAIN_DAYS} days",
        "A person looks at every page",
        "withdraw",
        "can't be taken back",
        "CC BY 4.0",
    ]:
        assert phrase in text
    assert len(kit.CONSENT_PARTS) >= 5 and all(len(part) < 400 for part in kit.CONSENT_PARTS)


def test_purge_deletes_a_no_now_and_old_unpublished_pages_only(work, tmp_path):
    prepared(work, tmp_path)  # w01 yes (reviewed), w02 no
    kit.run_intake(work, _one_more(tmp_path, "w02-02"), "2026-11-01")
    kit.run_intake(work, _one_more(tmp_path, "w01-03"), "2026-11-01")
    kit.record_review(work, ["w01-03"], "r01", "2026-11-02", False)  # not published
    kit.run_export(work, tmp_path / "dest", "2026-11-05")  # publishes w01-01 and w01-02
    assert kit.run_purge(work, "2026-11-10") == ["w02-02"]  # a no goes now; w01-03 is recent
    assert kit.run_purge(work, "2027-02-10") == ["w01-03"]  # 90 days on
    assert (work / "pages" / "w01-01.pdf").exists()  # published pages are never purged
    m = kit.build_manifest(work, "d")
    assert {p["code"]: p["page"]["status"] for p in m["passages"]}["w02-02"] == "deleted"
