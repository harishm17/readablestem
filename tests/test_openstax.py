"""OpenStax gold-set fetcher: extraction, license gate, page selection, files (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from readablestem.eval.equations import canonical
from readablestem.gold import openstax
from readablestem.gold.openstax import (
    LicenseError,
    extract_elements,
    fetch_gold,
)

FIXTURE = (Path(__file__).parent / "fixtures" / "openstax_page.xhtml").read_text(encoding="utf-8")
ARCHIVE = "/apps/archive/20990101.000000"
CONTENT_URL = f"https://openstax.org{ARCHIVE}/contents/book-uuid@v1:page-uuid.json"

CC_BY = "http://creativecommons.org/licenses/by/4.0/"
CC_BY_NC_SA = "https://creativecommons.org/licenses/by-nc-sa/4.0/"

TREE = {
    "id": "book-uuid@v1",
    "slug": "physics",
    "title": "Physics",
    "contents": [
        {"id": "pref@", "slug": "preface", "title": "Preface"},
        {
            "id": "ch1@v1",
            "slug": "1-chapter",
            "title": "Chapter 1",
            "contents": [
                {"id": "p11@", "slug": "1-introduction", "title": "Introduction"},
                {
                    "id": "p12@",
                    "slug": "1-1-first-section",
                    "title": '<span class="os-number">1.1</span> <span>First &amp; Best</span>',
                },
                {"id": "p13@", "slug": "1-2-second-section", "title": "1.2 Second"},
                {"id": "p1k@v1", "slug": "1-key-terms", "title": "Key Terms"},
            ],
        },
        {
            "id": "ch2@v1",
            "slug": "2-chapter",
            "title": "Chapter 2",
            "contents": [{"id": "p21@", "slug": "2-1-third-section", "title": "2.1 Third"}],
        },
    ],
}


class FakeFetcher:
    def __init__(
        self,
        book_license: str | None = CC_BY,
        cms_license: str | None = CC_BY,
        state: str = "live",
    ) -> None:
        cms_book = {
            "meta": {"slug": "physics"},
            "title": "Physics",
            "cnx_id": "book-uuid",
            "book_state": state,
            "license_name": "CMS name",
            "license_url": cms_license,
            "authors": [
                {"value": {"name": "Senior Author", "senior_author": True}},
                {"value": {"name": "Contributor", "senior_author": False}},
            ],
        }
        nc_book = {
            "meta": {"slug": "calculus-volume-1"},
            "title": "Calculus Volume 1",
            "cnx_id": "calc-uuid",
            "book_state": "live",
            "license_name": "Creative Commons Attribution-NonCommercial-ShareAlike License",
            "license_url": CC_BY_NC_SA,
        }
        self.responses = {
            openstax.CMS_BOOKS_URL: {"items": [cms_book, nc_book]},
            openstax.RELEASE_URL: {
                "archiveUrl": ARCHIVE,
                "books": {
                    "book-uuid": {"defaultVersion": "v1"},
                    "calc-uuid": {"defaultVersion": "c1"},
                },
            },
            f"https://openstax.org{ARCHIVE}/contents/book-uuid@v1.json": {
                "title": "Physics",
                "tree": TREE,
                "license": {"name": "Creative Commons Attribution License", "url": book_license},
            },
            f"https://openstax.org{ARCHIVE}/contents/calc-uuid@c1.json": {
                "title": "Calculus Volume 1",
                "tree": TREE,
                "license": {"name": "BY-NC-SA", "url": CC_BY_NC_SA},
            },
        }
        for page in ("p12", "p13", "p21"):
            url = f"https://openstax.org{ARCHIVE}/contents/book-uuid@v1:{page}.json"
            self.responses[url] = {"content": FIXTURE}
        self.calls: list[str] = []

    def get_json(self, url: str):
        self.calls.append(url)
        return self.responses[url]


# ------------------------------------------------------------------------ extraction


@pytest.fixture(scope="module")
def records() -> list[dict]:
    return extract_elements(FIXTURE, CONTENT_URL)


def by_kind(records, kind):
    return [r for r in records if r["kind"] == kind]


def test_extracted_mathml_feeds_the_scorer(records):
    eq = by_kind(records, "equation")[1]
    assert canonical(eq["mathml"]) == (
        "<math><mi>v</mi><mo>=</mo><mfrac><mi>d</mi><mi>t</mi></mfrac></math>"
    )


# ------------------------------------------------------------------- license / pages


# ---------------------------------------------------------------------- end to end


def test_fetch_gold_writes_jsonl_manifest_and_attribution(tmp_path):
    fetcher = FakeFetcher()
    book, results = fetch_gold("physics", 2, tmp_path, fetcher, retrieved_at="2026-10-06")
    assert book.authors == ["Senior Author"]
    assert [r.page.slug for r in results] == ["1-1-first-section", "1-2-second-section"]
    assert results[0].counts == {"equation": 3, "figure": 2, "table": 2}

    lines = results[0].path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    assert first["id"] == "openstax/physics/1-1-first-section/eq-000"
    assert first["license_url"] == CC_BY
    assert first["book_state"] == "live"
    assert first["retrieved_at"] == "2026-10-06"
    assert first["archive_version"] == "20990101.000000"
    assert first["source_url"] == "https://openstax.org/books/physics/pages/1-1-first-section"
    fig = next(json.loads(x) for x in lines if '"kind": "figure"' in x)
    assert fig["source_url"].endswith("1-1-first-section#Figure_A")

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["totals"] == {"equation": 6, "figure": 4, "table": 4}
    assert manifest["books"]["physics"]["license_url"] == CC_BY
    assert len(manifest["files"]) == 2
    assert all(len(f["sha256"]) == 64 for f in manifest["files"])

    attribution = (tmp_path / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "Physics" in attribution and "Senior Author" in attribution and CC_BY in attribution


def test_non_cc_by_book_is_refused_before_any_page_fetch(tmp_path):
    fetcher = FakeFetcher()
    with pytest.raises(LicenseError, match="not CC BY"):
        fetch_gold("calculus-volume-1", 1, tmp_path, fetcher)
    assert not any(":" in url.rsplit("/", 1)[-1] for url in fetcher.calls)
    assert not (tmp_path / "openstax").exists()


def test_license_mismatch_between_cms_and_archive_is_refused(tmp_path):
    fetcher = FakeFetcher(book_license=CC_BY, cms_license=CC_BY_NC_SA)
    with pytest.raises(LicenseError):
        fetch_gold("physics", 1, tmp_path, fetcher)


# ------------------------------------------------------------------------- live API
