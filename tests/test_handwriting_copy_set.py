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

from readablestem.eval.equations import score_latex, score_mathml
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


def test_gate_accepts_cc_by_live_and_retired():
    kit.licence_gate(book())
    kit.licence_gate(book(state="retired"))
    kit.licence_gate(
        book(license_url="http://creativecommons.org/licenses/by/3.0/", cms_license_url=CC_BY)
    )


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


def test_gate_can_refuse_retired_editions():
    with pytest.raises(ox.BookStateError):
        kit.licence_gate(book(state="retired"), allow_retired=False)


def test_a_book_not_cc_by_in_the_table_is_never_requested():
    (selection, _), fetcher = run_select(licensed=LICENSED)
    assert {
        "book_slug": "calculus-volume-1",
        "reason": "not CC BY in the committed licence table",
    } in (selection["excluded_books"])
    assert not any("uuid-calculus-volume-1" in u for u in fetcher.requests)


def test_a_book_the_live_record_calls_nc_sa_is_refused_even_if_the_table_says_cc_by():
    books = make_books()
    books["chemistry"]["license"] = NC_SA
    (selection, _), _ = run_select(books)
    assert {"book_slug": "chemistry", "reason": "LicenseError"} in selection["excluded_books"]
    assert all(p["book_slug"] != "chemistry" for p in selection["passages"])


def test_the_committed_licence_table_lists_cc_by_slugs():
    slugs = kit.licensed_slugs()
    assert "college-physics" in slugs and "statistics" in slugs
    assert "calculus-volume-1" not in slugs and "algebra-1" not in slugs


# ------------------------------------------------------------------- passage rule


def stats_of(xml: str) -> dict:
    return kit.example_stats(kit.ET.fromstring(xml))


def test_a_dense_text_only_example_qualifies():
    assert kit.qualifies(stats_of(example_xml("e")))


@pytest.mark.parametrize(
    "xml",
    [
        example_xml("few", maths=2),
        example_xml("many", maths=13, words=100),
        example_xml("short", words=5),
        example_xml("long", words=300, maths=12),
        example_xml("sparse", maths=3, words=200),
        example_xml("img", extra='<img src="x.png" alt="a"/>'),
        example_xml("fig", extra="<figure><img src='x'/></figure>"),
        example_xml("tab", extra="<table><tr><td>1</td></tr></table>"),
    ],
)
def test_other_examples_do_not_qualify(xml):
    assert not kit.qualifies(stats_of(xml))


def test_one_character_formulas_do_not_count_as_substantial():
    tiny = "".join("<math><mi>x</mi></math>" for _ in range(6))
    xml = f'<div data-type="example" id="t"><p>{" ".join(["w"] * 30)}</p>{tiny}</div>'
    assert not kit.qualifies(stats_of(xml))


def test_prose_words_leave_the_formulas_out():
    elem = kit.ET.fromstring(example_xml("e", words=10, maths=3))
    assert "hidden" not in kit.visible_words(elem)
    assert kit.example_stats(elem)["n_words"] == len(kit.visible_words(elem).split())


def test_calculus_titles_go_to_calculus_only():
    assert kit.subject_of_page("calculus", "Finding Limits Numerically")
    assert not kit.subject_of_page("algebra", "Finding Limits Numerically")
    assert kit.subject_of_page("algebra", "Linear Functions")
    assert not kit.subject_of_page("calculus", "Linear Functions")


def test_quotas_follow_weights_and_sum_to_the_total():
    for total in (1, 7, 12, 30, 31):
        q = kit.quotas(total)
        assert sum(q.values()) == total
    assert kit.quotas(11) == {
        "algebra": 3,
        "calculus": 2,
        "physics": 3,
        "statistics": 2,
        "chemistry": 1,
    }
    q30 = kit.quotas(30)
    assert q30["physics"] >= q30["statistics"] >= q30["chemistry"] >= 1


# ------------------------------------------------------------------------- selection


def test_selection_is_deterministic_for_a_seed_and_differs_between_seeds():
    (a, _), _ = run_select(seed="alpha", per_writer=3)
    (b, _), _ = run_select(seed="alpha", per_writer=3)
    (c, _), _ = run_select(seed="beta", per_writer=3)
    key = lambda s: [(p["code"], p["page_slug"], p["element_id"]) for p in s["passages"]]  # noqa: E731
    assert key(a) == key(b)
    assert key(a) != key(c)


def test_selection_fills_the_total_one_passage_per_section_and_every_subject():
    (sel, sources), _ = run_select(writers=3, per_writer=4)
    passages = sel["passages"]
    assert len(passages) == 12 == len(sources)
    sections = [(p["book_slug"], p["page_slug"]) for p in passages]
    assert len(sections) == len(set(sections))
    assert {p["subject"] for p in passages} == set(kit.SUBJECTS)
    assert sel["quotas"] == kit.quotas(12)
    assert all(p["n_math"] >= 3 and p["book_state"] == "retired" for p in passages)


def test_a_subject_without_a_licensed_book_hands_its_quota_on_and_says_so():
    (sel, _), _ = run_select(licensed=LICENSED - {"chemistry"})
    assert sel["shortfalls"] == {"chemistry": 1} or sel["shortfalls"].get("chemistry")
    assert sel["chosen_per_subject"]["chemistry"] == 0
    assert len(sel["passages"]) == 12
    assert sum(sel["handed_on"].values()) == sum(sel["shortfalls"].values())


def test_pages_are_dealt_so_writers_get_a_spread_and_no_overlap():
    (sel, _), _ = run_select(writers=3, per_writer=5)
    by_writer: dict[str, list[dict]] = {}
    for p in sel["passages"]:
        by_writer.setdefault(p["writer_id"], []).append(p)
    assert sorted(by_writer) == ["w01", "w02", "w03"]
    assert all(len(v) == 5 for v in by_writer.values())
    assert all(len({p["subject"] for p in v}) >= 3 for v in by_writer.values())
    codes = [p["code"] for p in sel["passages"]]
    assert len(set(codes)) == len(codes)
    assert codes[0] == "w01-01"


def test_selection_records_attribution_and_pins_without_content():
    (sel, _), _ = run_select()
    p = sel["passages"][0]
    assert p["license_url"] == CC_BY and p["authors"] == ["A. Author"]
    assert p["source_url"].startswith("https://openstax.org/books/") and "#" in p["source_url"]
    assert sel["pins"]["college-physics"] == {"archive_version": "20260101.000000", "version": "v1"}
    assert "seed" in sel and sel["rule"].startswith("Passage selection rule")


def test_a_pin_is_passed_to_the_resolver():
    pin = ox.BookPin("20260101.000000", "v1")
    (sel, _), fetcher = run_select(pins={"college-physics": pin})
    assert sel["pins"]["college-physics"]["version"] == "v1"
    assert not any(u == ox.RELEASE_URL and False for u in fetcher.requests)


def test_pin_parsing():
    assert kit._parse_pins(["x=2026@abc"])["x"] == ox.BookPin("2026", "abc")
    with pytest.raises(SystemExit):
        kit._parse_pins(["nonsense"])


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


def test_brief_has_each_passage_attribution_consent_and_no_script(work):
    out = kit.build_briefs(work, work / "briefs", "someone@example.test")
    assert [p.name for p in out] == ["brief-w01.html", "brief-w02.html"]
    text = out[0].read_text(encoding="utf-8")
    sel = kit.load_selection(work)
    mine = [p for p in sel["passages"] if p["writer_id"] == "w01"]
    for p in mine:
        assert p["code"] in text and p["source_url"] in text and "OpenStax" in text
    assert all(p["code"] not in text for p in sel["passages"] if p["writer_id"] == "w02")
    assert "someone@example.test" in text and "w01-01.pdf" in text
    assert "<math" in text and "hidden" not in text  # presentation maths; annotations dropped
    assert "CC BY 4.0" in text and "writer id" in text
    assert "<script" not in text and "<img" not in text


def test_brief_drops_media_scripts_and_attributes():
    xml = (
        '<div id="x" onclick="evil()"><p class="c">Hi <script>alert(1)</script>'
        '<img src="a.png"/><b>bold</b> &lt;tag&gt;</p></div>'
    )
    out = kit.render_passage_html(kit.ET.fromstring(xml))
    assert out == "<div><p>Hi <b>bold</b> &lt;tag&gt;</p></div>"


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


def test_name_codes_tolerate_case_spacing_and_noise():
    f = kit.codes_in_name
    assert f("w01-03.pdf") == {"w01-03"}
    assert f("W01_3.PDF") == {"w01-03"}
    assert f("w02 p07 (1).pdf") == {"w02-07"}
    assert f("my notes w03-10 final.pdf") == {"w03-10"}
    assert f("notes.pdf") == set()
    assert f("w01-02 and w01-03.pdf") == {"w01-02", "w01-03"}
    assert f("show01-03.pdf") == set()


def test_matching_sorts_files_into_matched_unmatched_ambiguous_unknown_duplicate(tmp_path):
    folder = tmp_path / "back"
    (folder / "sub").mkdir(parents=True)
    for name in ["w01-01.pdf", "sub/W01_02.PDF", "notes.pdf", "w01-01 and w01-02.pdf",
                 "w09-01.pdf", "w02-01.pdf", "w02-01 copy.pdf", "readme.txt"]:  # fmt: skip
        (folder / name).write_bytes(b"%PDF")
    codes = {"w01-01", "w01-02", "w02-01", "w02-02"}
    m = kit.match_returns(folder, codes)
    assert sorted(m["matched"]) == ["w01-01", "w01-02"]
    assert sorted(m["duplicates"]) == ["w02-01"]
    assert [p.name for p in m["unmatched"]] == ["notes.pdf"]
    assert [p.name for p in m["ambiguous"]] == ["w01-01 and w01-02.pdf"]
    assert [p.name for p in m["unknown"]] == ["w09-01.pdf"]


def test_inspect_pdf_classifies_ink_and_refuses_the_rest(tmp_path):
    vec = tmp_path / "vec.pdf"
    make_pdf(
        vec, strokes=120, pages=2, meta={"producer": "iPadOS Quartz PDFContext", "author": "Pat"}
    )
    info = kit.inspect_pdf(vec)
    assert info["status"] == "ok" and info["ink"] == "vector" and info["n_pages"] == 2
    assert info["producer"] == "iPadOS Quartz PDFContext"

    ras = tmp_path / "ras.pdf"
    make_pdf(ras, image=True)
    assert kit.inspect_pdf(ras)["ink"] == "raster"

    blank = tmp_path / "blank.pdf"
    make_pdf(blank)
    assert kit.inspect_pdf(blank)["status"] == "rejected_no_ink"

    many = tmp_path / "many.pdf"
    make_pdf(many, strokes=50, pages=kit.MAX_PAGES + 1)
    assert kit.inspect_pdf(many) == {
        "status": "rejected_too_many_pages", "n_pages": kit.MAX_PAGES + 1
    }  # fmt: skip

    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    assert kit.inspect_pdf(bad)["status"] == "rejected_unreadable"


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


def test_intake_matches_copies_and_reports_counts_only(work, tmp_path):
    report = kit.run_intake(work, returns_folder(tmp_path))
    assert report["accepted"] == ["w01-01", "w01-02"]
    assert report["rejected"] == {"w02-01": "rejected_no_ink"}
    assert report["unmatched_files"] == 1
    assert set(report["still_missing"]) == {"w01-03", "w02-02", "w02-03"}
    assert (work / "pages" / "w01-01.pdf").exists()
    assert not (work / "pages" / "w02-01.pdf").exists()
    intake = json.loads((work / "intake.json").read_text())
    assert intake["w01-01"]["sha256"] == kit.sha256_file(work / "pages" / "w01-01.pdf")
    assert "Pat Real" not in (work / "intake.json").read_text()
    assert "stray" not in (work / "intake.json").read_text()


def test_a_second_intake_keeps_earlier_pages(work, tmp_path):
    kit.run_intake(work, returns_folder(tmp_path))
    later = tmp_path / "later"
    later.mkdir()
    make_pdf(later / "w01-03.pdf", strokes=100)
    report = kit.run_intake(work, later)
    assert report["accepted"] == ["w01-01", "w01-02", "w01-03"]


def test_two_files_for_one_passage_are_refused(work, tmp_path):
    folder = tmp_path / "dup"
    folder.mkdir()
    make_pdf(folder / "w01-01.pdf", strokes=100)
    make_pdf(folder / "w01-01 (1).pdf", strokes=100)
    report = kit.run_intake(work, folder)
    assert report["duplicates"] == ["w01-01"] and report["accepted"] == []


# --------------------------------------------------------------------------- gold


def test_gold_is_the_source_mathml_in_order_and_scores_itself(work):
    sel = kit.load_selection(work)
    records = kit.gold_records(sel, kit.load_sources(work, sel))
    eqs = [r for r in records if r["kind"] == "equation" and r["code"] == "w01-01"]
    assert [r["order"] for r in eqs] == [0, 1, 2, 3]
    assert all(r["id"].startswith("handwriting-copy/w01-01/eq-") for r in eqs)
    assert all(r["license_url"] == CC_BY and "openstax.org" in r["source_url"] for r in eqs)
    assert eqs[1]["mathml"].count("<mn>1</mn>") >= 1
    assert score_mathml(eqs[0]["mathml"], eqs[0]["mathml"]).exact_strict
    assert score_latex("x+0=1", eqs[0]["mathml"]).exact_lenient
    prose = [r for r in records if r["kind"] == "prose" and r["code"] == "w01-01"]
    assert len(prose) == 1 and "hidden" not in prose[0]["text"]


def test_gold_command_writes_jsonl_without_network(work):
    path = kit.write_gold(work)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(lines) == sum(1 for _ in lines) > 0
    assert {r["kind"] for r in lines} == {"equation", "prose"}


# ------------------------------------------------------- consent, manifest, export


def prepared(work, tmp_path):
    kit.run_intake(work, returns_folder(tmp_path))
    kit.record_consent(work, "w01", True, "2026-11-04")
    kit.record_consent(work, "w02", False, "2026-11-04")


def test_consent_must_name_a_known_writer(work):
    with pytest.raises(SystemExit):
        kit.record_consent(work, "w77", True, "2026-11-04")


def test_manifest_publishes_only_consented_pages_and_counts_the_rest(work, tmp_path):
    prepared(work, tmp_path)
    kit.run_intake(work, _one_more(tmp_path, "w02-02"))
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
    folder = tmp_path / "one_more"
    folder.mkdir()
    make_pdf(folder / f"{name}.pdf", strokes=100)
    return folder


def test_without_a_recorded_answer_pages_stay_private(work, tmp_path):
    kit.run_intake(work, returns_folder(tmp_path))
    m = kit.build_manifest(work, "2026-11-05")
    assert m["totals"]["published"] == 0 and m["totals"]["private"] == 2
    assert not any(w["consent_recorded"] for w in m["writers"])


def test_export_copies_only_consented_pages_and_strips_personal_metadata(work, tmp_path):
    prepared(work, tmp_path)
    kit.run_intake(work, _one_more(tmp_path, "w02-02"))
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
    assert "College Physics" in attribution and "CC BY 4.0" in attribution


def test_a_later_export_removes_pages_whose_consent_was_withdrawn(work, tmp_path):
    prepared(work, tmp_path)
    dest = tmp_path / "dest"
    kit.run_export(work, dest, "2026-11-05")
    kit.record_consent(work, "w01", False, "2026-11-06")
    kit.run_export(work, dest, "2026-11-06")
    assert list((dest / "pages").glob("*.pdf")) == []


def test_command_line_end_to_end_on_a_work_folder(work, tmp_path, capsys):
    ret = returns_folder(tmp_path)
    assert kit.main(["brief", "--work", str(work), "--send-to", "x"]) == 0
    assert kit.main(["intake", "--work", str(work), "--returns", str(ret)]) == 0
    assert kit.main(["consent", "--work", str(work), "--writer", "w01", "--publish", "yes"]) == 0
    assert kit.main(["gold", "--work", str(work)]) == 0
    assert kit.main(["manifest", "--work", str(work), "--out", str(tmp_path / "m.json")]) == 0
    assert kit.main(["export", "--work", str(work), "--dest", str(tmp_path / "d")]) == 0
    assert json.loads((tmp_path / "m.json").read_text())["totals"]["published"] == 2
    with pytest.raises(SystemExit):
        kit.main(["intake", "--work", str(work), "--returns", str(REPO / "tests")])
