"""Selection of the fifth fresh real-world split: seeded determinism and the licence gate
(`scripts/fresh5_select.py`, `readablestem.gold.course_corpus`). Offline: no network, no model, no
TeX engine."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from readablestem.gold import course_corpus as cc

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fresh5_select.py"


@pytest.fixture(scope="module")
def sel():
    spec = importlib.util.spec_from_file_location("fresh5_select_for_tests", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------------------- licence


@pytest.mark.parametrize(
    "spdx",
    [
        None,
        "",
        "NOASSERTION",
        "CC-BY-NC-4.0",
        "CC-BY-NC-SA-4.0",
        "CC-BY-ND-4.0",
        "GPL-3.0",
        "other",
    ],
)
def test_licence_gate_refuses_everything_else(spdx):
    assert cc.licence_gate(spdx) is None
    with pytest.raises(cc.Skip) as info:
        cc.require_licence(spdx)
    assert info.value.code == "licence_refused"


def item(name: str, spdx: str | None, **extra) -> dict:
    base = {
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "default_branch": "main",
        "size": 500,
        "stargazers_count": 3,
        "pushed_at": "2025-01-01T00:00:00Z",
        "license": {"spdx_id": spdx} if spdx else None,
    }
    return base | extra


# ------------------------------------------------------------------- course documents


DECK = "\\documentclass[10pt]{beamer}\n\\begin{document}\n\\end{document}\n"


def test_a_missing_sandbox_stops_the_run_instead_of_skipping(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "engines", lambda: [("pdflatex", "v")])

    def refuse(*args, **kwargs):
        raise cc.safe_tex.SandboxUnavailable("bubblewrap (bwrap) is required")

    monkeypatch.setattr(cc.safe_tex, "compile_tex", refuse)
    with pytest.raises(cc.CourseError):
        cc.compile_document(tmp_path, "a.tex", tmp_path / "out")


# ----------------------------------------------------------------- stratum A: arXiv


# --------------------------------------------------------------- stratum B: OpenStax


# ------------------------------------------------------------- stratum D: chemistry


class FakeFetcher:
    """Serves a CMS list and one archive book JSON per slug; no network."""

    def __init__(self, books: dict[str, tuple[str, str]]):
        self.books = books  # slug -> (licence name, licence url)

    def get_json(self, url: str):
        from readablestem.gold import openstax as ox

        if url == ox.CMS_BOOKS_URL:
            return {
                "items": [
                    {
                        "title": slug,
                        "meta": {"slug": slug},
                        "cnx_id": f"uuid-{slug}",
                        "book_state": "retired",
                        "license_name": name,
                        "license_url": link,
                    }
                    for slug, (name, link) in self.books.items()
                ]
            }
        if url == ox.RELEASE_URL:
            return {
                "archiveUrl": "/apps/archive/20260604.144757",
                "books": {f"uuid-{s}": {"defaultVersion": "v1"} for s in self.books},
            }
        slug = url.rsplit("uuid-", 1)[1].split("@", 1)[0]
        name, link = self.books[slug]
        return {"title": slug, "license": {"name": name, "url": link}, "tree": {}}


CC_BY = ("Creative Commons Attribution License", "https://creativecommons.org/licenses/by/4.0/")
CC_BY_NC_SA = (
    "Creative Commons Attribution-NonCommercial-ShareAlike License",
    "https://creativecommons.org/licenses/by-nc-sa/4.0/",
)


def _book(slug, licence, cms=None):
    from readablestem.gold import openstax as ox

    fetcher = FakeFetcher({slug: licence})
    book = ox.resolve_book(slug, fetcher)
    if cms is not None:
        book.cms_license_url = cms
    return book


def test_the_sealed_check_part_is_a_superset_by_the_registered_hash_rule(sel):
    from readablestem.gold import devset
    from readablestem.gold import openstax as ox

    tree = {
        "contents": [
            {"id": f"u{i}@1", "slug": f"{i}-1-s{i}", "title": f"<span>{i}.1</span> S{i}"}
            for i in range(1, 61)
        ]
    }
    book = ox.Book(
        slug="chemistry-atoms-first",
        title="t",
        uuid="u",
        version="v",
        archive_path="/apps/archive/x",
        state="retired",
        license_name=None,
        license_url=None,
        cms_license_name=None,
        cms_license_url=None,
        tree=tree,
    )
    pages = [p for p in ox.iter_pages(book.tree) if p.section is not None]
    sealed = sel.sealed_check_sections({"chemistry-atoms-first": book}, "salt")
    assert sealed == {
        ("chemistry-atoms-first", p.slug)
        for p in pages
        if devset.hash_fraction("salt", f"chemistry-atoms-first/{p.slug}") < devset.CHECK_SHARE
    }


# the chemistry test: arrows, state symbols, formulas and ions, not a lone variable or sign
M = '<math xmlns="http://www.w3.org/1998/Math/MathML">{}</math>'
