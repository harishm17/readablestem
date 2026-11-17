"""Picking the documents of a reading-order gold set: the seeded walks and their rules."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from readablestem.gold import course_corpus, layout_gold, layout_sources, safe_tex
from source_support import needs_latexml

SYLLABLES = ("ba", "ke", "li", "mo", "nu", "ra", "se", "ti", "vo", "zu")
SEED = "a test seed"


def word(n: int) -> str:
    return SYLLABLES[n // 100 % 10] + SYLLABLES[n // 10 % 10] + SYLLABLES[n % 10]


def paragraph(k: int) -> list[str]:
    """Paragraph `k` of a made-up paper: 30 words no other paragraph has, in four lines."""
    words = [word(30 * k + i) for i in range(30)]
    return [" ".join(words[i : i + 8]) for i in range(0, 30, 8)]


def write_paper(folder: Path, pages: int) -> None:
    """A paper as the fetcher leaves it: the PDF and the HTML LaTeXML made of its source."""
    document = pymupdf.open()
    html = ['<article class="ltx_document">']
    for number in range(pages):
        page = document.new_page(width=612, height=792)
        y = 90.0
        for k in range(3 * number, 3 * number + 3):
            for line in paragraph(k):
                page.insert_text((72, y), line, fontsize=10)
                y += 14
            y += 14
            html.append(
                f'<div class="ltx_para"><p class="ltx_p">{" ".join(paragraph(k))}</p></div>'
            )
    html.append("</article>")
    made = folder / "latexml" / "html"
    made.mkdir(parents=True)
    (made / "doc.html").write_text("\n".join(html), encoding="utf-8")
    document.save(folder / "paper.pdf")


def test_a_walk_takes_papers_in_the_seeded_order_and_records_why_it_passed_one_over(tmp_path):
    classes = {"2401.00001": "article", "2401.00002": "article", "2401.00003": "revtex4-2"}
    pages = {"2401.00001": 6, "2401.00002": 6, "2401.00003": 4}
    fetched = []

    def fetch(arxiv_id: str, folder: Path) -> layout_sources.Source:
        fetched.append(arxiv_id)
        write_paper(folder, pages[arxiv_id])
        return layout_sources.Source(folder / "src", "main.tex", classes[arxiv_id])

    candidates = [{"id": i, "licence_name": "CC BY 4.0"} for i in (*classes, "2401.00009")]
    root, work = tmp_path / "set", tmp_path / "work"

    def draw() -> dict:
        return layout_sources.draw_papers(
            candidates,
            SEED,
            root,
            work,
            used={"2401.00009"},
            per_family=1,
            fetch=fetch,
            log=lambda line: None,
        )

    walk = draw()
    order = layout_sources.walk_order(classes, SEED)
    first, second = (i for i in order if classes[i] == "article")
    assert sorted(fetched) == sorted(classes)  # the used paper is never asked for
    assert [row["id"] for row in walk["taken"]] == [first]
    assert {row["id"]: row["why"] for row in walk["passed"]} == {
        second: "family_full",
        "2401.00003": "pdf_page_count",
    }

    registry = layout_gold.registry_of(root, "t", "a test", "held-out")
    (document,) = registry["documents"]
    assert (document["kind"], document["pages"], registry["side"]) == ("paper", 5, "held-out")
    assert layout_gold.verify_set(root, registry) == []
    assert registry["passed"] == {"papers": sorted(f"arXiv {i}" for i in (second, "2401.00003"))}

    draw()  # a second call goes on from the record and asks for nothing again
    assert len(fetched) == 3


def test_papers_named_by_an_earlier_set_are_used(tmp_path):
    project, cache = tmp_path / "project", tmp_path / "cache"
    (project / "data" / "realworld").mkdir(parents=True)
    (project / "data" / "gold" / "layout").mkdir(parents=True)
    manifest = {"papers": [{"id": "2403.06350"}]}
    (project / "data" / "realworld" / "manifest-dev.json").write_text(json.dumps(manifest))
    pool = {"candidates": [{"id": "2501.11111"}]}  # a list of the whole pool names no used paper
    (project / "data" / "realworld" / "candidates.json").write_text(json.dumps(pool))
    registry = {
        "documents": [{"source": "arXiv 2403.06409"}],
        "passed": {"papers": ["arXiv 2403.1"]},
    }
    (project / "data" / "gold" / "layout" / "layout-dev-1.json").write_text(json.dumps(registry))
    (cache / "tables" / "2502.22222" / "src").mkdir(parents=True)
    assert layout_sources.used_papers(project, cache) == {"2403.06350", "2403.06409", "2502.22222"}


@pytest.mark.parametrize(
    ("document_class", "family"),
    [
        ("revtex4-2", "revtex"),
        ("IEEEtran", "ieee"),
        ("amsart", "ams"),
        ("acmart", "conference"),
        ("mnras", "journal"),
        ("article", "article"),
        ("a-class-of-the-authors", "other"),
    ],
)
def test_a_document_class_counts_under_its_family(document_class, family):
    source = f"% \\documentclass{{book}}\n\\documentclass[10pt, twocolumn]{{{document_class}}}\n"
    assert layout_sources.class_family(layout_sources.document_class(source)) == family


def book(slug: str, **changes) -> dict:
    item = {
        "meta": {"slug": slug, "locale": "en"},
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "high_resolution_pdf_url": f"https://example.org/{slug}.pdf",
        "book_subjects": [{"subject_name": "Science"}],
    }
    return {**item, **changes}


def test_a_held_out_book_is_cc_by_in_english_with_a_pdf_and_no_development_book():
    nc_sa = "https://creativecommons.org/licenses/by-nc-sa/4.0/"
    items = [
        book("anatomy-and-physiology"),
        book("psychology", book_subjects=[{"subject_name": "Social Sciences"}]),
        book("principles-microeconomics-2e", book_subjects=[]),
        book("biology-2e", license_url=nc_sa),
        book("prealgebra", high_resolution_pdf_url=None),
        book("fisica", meta={"slug": "fisica", "locale": "es"}),
        book("astronomy-2e"),  # a later edition of a development book
        book("college-physics-ap-courses"),
        book("precalculus"),  # the text of a development book under another title
    ]
    found = layout_sources.book_candidates(items)
    assert [(c["slug"], c["family"], c["stem"]) for c in found] == [
        ("anatomy-and-physiology", "anatomy-and-physiology", True),
        ("psychology", "psychology", False),
        ("principles-microeconomics-2e", "principles-economics", False),
    ]


class Archive:
    """The publisher's archive, for a book whose pages hold their own slug."""

    def get_json(self, url: str) -> dict:
        return {"content": url.rsplit(":", 1)[-1]}


def test_the_sections_of_a_book_are_picked_by_position():
    from readablestem.gold import openstax

    def leaf(slug: str) -> dict:
        return {"id": f"{slug}@1", "slug": slug, "title": slug}

    chapters = [
        {
            "contents": [
                leaf(f"{n}-introduction"),
                leaf(f"{n}-1-first"),
                leaf(f"{n}-2-second"),
                leaf(f"{n}-3-third"),
                leaf(f"{n}-key-terms"),
                leaf(f"{n}-exercises"),
            ]
        }
        for n in range(1, 7)
    ]
    tree = {"contents": [leaf("preface"), *chapters, leaf("index")]}
    a_book = openstax.Book(
        slug="a-book",
        title="A book",
        uuid="b",
        version="v1",
        archive_path="/apps/archive/1",
        state="live",
        license_name="CC BY",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        cms_license_name="CC BY",
        cms_license_url="https://creativecommons.org/licenses/by/4.0/",
        authors=[],
        tree=tree,
    )
    picked = layout_sources.book_sections(a_book, Archive())
    assert (picked["a"], picked["b"]) == (2, 4)
    assert [(s["role"], s["slug"]) for s in picked["sections"]] == [
        ("sectionA", "2-2-second"),
        ("chapter_end", "2-key-terms"),
        ("chapter_end", "2-exercises"),
        ("opening", "4-introduction"),
        ("sectionB", "4-1-first"),
        ("sectionB", "4-2-second"),
        ("index", "index"),
    ]
    assert all(s["content"].startswith(s["slug"]) for s in picked["sections"])


PAPER = r"""\documentclass{article}
\begin{document}
\title{Rods and rails}
\maketitle
\section{Introduction}
A metal rod grows longer when it is heated, and $x$ measures the growth.
\begin{figure}
\caption{A heated rod in a steam jacket.}
\end{figure}
\begin{itemize}
\item Brass grows most.
\end{itemize}
\end{document}
"""


@needs_latexml
def test_latexml_in_the_sandbox_gives_the_units_the_matcher_reads(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.tex").write_text(PAPER, encoding="utf-8")
    html = layout_sources.source_html(layout_sources.read_source(source), tmp_path / "out")
    units = layout_gold.units_latexml(html)
    assert [(unit["role"], unit["text"]) for unit in units] == [
        ("heading", "Rods and rails"),
        ("heading", "1 Introduction"),
        (
            "body",
            "A metal rod grows longer when it is heated, and "
            f"{layout_gold.BRK} measures the growth.",
        ),
        ("caption", "Figure 1: A heated rod in a steam jacket."),
        ("list", "Brass grows most."),
    ]
    assert [bool(unit.get("float")) for unit in units] == [False, False, False, True, False]


HANDOUT = r"""\documentclass{article}
\begin{document}
\section*{Problem set four: thermal expansion}
A metal rod grows longer when it is heated, and the growth is in proportion to its length
and to the change in temperature. Engineers leave gaps between the rails of a railway for
this reason, and a bridge rests on rollers at one end.

In the laboratory you will clamp one end of a brass rod and read the other end with a dial
gauge while steam passes through a jacket around it. Write down the reading every minute
until it no longer changes.

Before the session, read the notes on the dial gauge and bring a pocket calculator. Work
in pairs, and hand in one sheet for each pair at the end of the afternoon.
\end{document}
"""
SLIDES = r"""\documentclass{beamer}
\begin{document}
\begin{frame}{Thermal expansion} A rod grows longer when it is heated. \end{frame}
\end{document}
"""


@needs_latexml
@pytest.mark.skipif(safe_tex.texlive_bin() is None, reason="needs a TeX Live to compile with")
def test_a_course_walk_takes_one_handout_of_a_repository_and_never_a_slide_deck(tmp_path):
    def repository(name: str, licence: str = "MIT") -> course_corpus.Repo:
        folder = tmp_path / "sources" / name.replace("/", "__") / "src"
        folder.mkdir(parents=True)
        (folder / "handout.tex").write_text(HANDOUT, encoding="utf-8")
        (folder / "slides.tex").write_text(SLIDES, encoding="utf-8")
        return course_corpus.Repo(name, licence, "main", 10, 0, "2026-01-01", f"https://x/{name}")

    repos = [
        repository("a/notes"),
        repository("b/used-before"),
        repository("c/all-rights", "GPL-3.0"),
    ]
    root = tmp_path / "set"
    walk = layout_sources.draw_course(
        repos,
        SEED,
        root,
        tmp_path / "work",
        tmp_path / "sources",
        used={"b/used-before"},
        log=lambda line: None,
    )
    assert [(row["repo"], row["path"], row["pages"]) for row in walk["taken"]] == [
        ("a/notes", "handout.tex", 1)
    ]
    assert [(row["repo"], row["tried"][0]["why"]) for row in walk["passed"]] == [
        ("c/all-rights", "license")
    ]
    (document,) = layout_gold.registry_of(root, "t", "a test", "held-out")["documents"]
    assert (document["kind"], document["blocks"]) == ("course", 4)
