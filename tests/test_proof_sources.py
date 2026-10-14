"""Fetching the answer key of a proof set from OpenStax (a fake archive, no network)."""

from __future__ import annotations

import pytest

from readablestem.gold import openstax
from readablestem.gold.openstax import fetch_gold
from readablestem.proof import engine, registry, sources
from readablestem.proof.errors import ProofError
from test_openstax import FakeFetcher

DATE = "2026-10-06"
PAGES = ["1-1-first-section", "2-1-third-section"]


def pset_for(manifest_sha: str, *, gold_dir: str | None = None, **fetch_extra) -> registry.ProofSet:
    return registry.parse_set(
        {
            "schema_version": 1,
            "id": "fake",
            "title": "Fake archive",
            "held_out": False,
            "gold": {
                "dir": gold_dir,
                "manifest_sha256": manifest_sha,
                "fetch": {
                    "date": DATE,
                    "sections": [{"book": "physics", "page": p} for p in PAGES],
                    **fetch_extra,
                },
            },
            "bundle": "proof/bundles/fake",
        }
    )


@pytest.fixture
def pinned_manifest(tmp_path) -> str:
    """The hash `gold fetch` gives these two sections: what a proof set pins."""
    out = tmp_path / "reference"
    fetch_gold("physics", 2, out, FakeFetcher(), page_slugs=PAGES, retrieved_at=DATE)
    return registry.sha256_file(out / "manifest.json")


def test_a_fetch_rebuilds_the_pinned_answer_key_byte_for_byte(tmp_path, pinned_manifest):
    pset = pset_for(pinned_manifest)
    said: list[str] = []
    out = sources.fetch_gold_set(
        pset,
        tmp_path / "gold-fetched-x",
        tmp_path / "cache",
        fetcher=FakeFetcher(),
        say=said.append,
    )
    assert said == ["fetching 2 section(s) of physics from OpenStax"]
    checks = registry.check_gold(out, pset)
    assert [c.status for c in checks] == ["ok", "ok", "ok"]


def test_a_fetch_that_gives_other_bytes_does_not_match_the_pin(tmp_path, pinned_manifest):
    pset = pset_for("0" * 64)
    out = sources.fetch_gold_set(
        pset, tmp_path / "gold-fetched-x", tmp_path / "cache", fetcher=FakeFetcher()
    )
    assert registry.check_gold(out, pset)[0].status == "mismatch"


def test_a_fetch_empties_only_a_folder_it_owns(tmp_path):
    keep = tmp_path / "my-data"
    keep.mkdir()
    (keep / "file").write_text("x")
    with pytest.raises(ProofError, match="refusing to empty my-data"):
        sources.fetch_gold_set(pset_for("0" * 64), keep, tmp_path / "c", fetcher=FakeFetcher())
    assert (keep / "file").read_text() == "x"


def test_a_book_that_is_not_cc_by_cannot_be_fetched(tmp_path):
    pset = pset_for("0" * 64)
    with pytest.raises(ProofError, match="could not fetch physics from OpenStax"):
        sources.fetch_gold_set(
            pset,
            tmp_path / "gold-fetched-x",
            tmp_path / "c",
            fetcher=FakeFetcher(book_license=openstax_nc_sa()),
        )


def openstax_nc_sa() -> str:
    return "https://creativecommons.org/licenses/by-nc-sa/4.0/"


def test_a_set_without_a_committed_copy_fetches_its_answer_key(tmp_path, pinned_manifest):
    pset = pset_for(pinned_manifest)
    opts = engine.ProofOptions(root=tmp_path, fetcher=FakeFetcher())
    gold = engine.resolve_gold(pset, opts, None, tmp_path / "work", lambda _m: None)
    assert gold.name == f"{sources.FETCHED_PREFIX}-fake"
    assert registry.sha256_file(gold / "manifest.json") == pinned_manifest
    assert engine.books_of(gold)[0]["title"] == "Physics"


def test_a_committed_copy_is_used_before_anything_is_fetched(tmp_path, pinned_manifest):
    committed = tmp_path / "data" / "gold"
    fetch_gold("physics", 2, committed, FakeFetcher(), page_slugs=PAGES, retrieved_at=DATE)
    pset = pset_for(pinned_manifest, gold_dir="data/gold")
    opts = engine.ProofOptions(root=tmp_path, fetcher=None)  # a fetch here would hit the network
    assert engine.resolve_gold(pset, opts, None, tmp_path / "w", lambda _m: None) == committed


# ----------------------------------------------------------------- archive pinning


def test_a_pin_is_passed_to_the_fetch_when_this_checkout_can_read_a_pinned_release(
    tmp_path, monkeypatch
):
    class Pin:
        def __init__(self, archive_version: str, version: str) -> None:
            self.archive_version, self.version = archive_version, version

    seen: list[object] = []

    def fake_fetch(book, n, out, http, **kwargs):
        seen.append(kwargs.get("pin"))
        out.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(openstax, "BookPin", Pin, raising=False)
    monkeypatch.setattr(sources, "fetch_gold", fake_fetch)
    pset = pset_for("0" * 64, pins={"physics": {"archive_version": "2026", "version": "abc"}})
    sources.fetch_gold_set(pset, tmp_path / "gold-fetched-x", tmp_path / "c", fetcher=object())
    (pin,) = seen
    assert isinstance(pin, Pin) and (pin.archive_version, pin.version) == ("2026", "abc")


def test_a_pinned_set_is_refused_by_a_checkout_that_cannot_read_a_pinned_release(
    tmp_path, monkeypatch
):
    monkeypatch.delattr(openstax, "BookPin", raising=False)
    pset = pset_for("0" * 64, pins={"physics": {"archive_version": "2026", "version": "abc"}})
    with pytest.raises(ProofError, match="cannot read a pinned release"):
        sources.fetch_gold_set(
            pset, tmp_path / "gold-fetched-x", tmp_path / "c", fetcher=FakeFetcher()
        )


def test_the_text_fingerprint_follows_the_words_and_not_the_bytes():
    import pymupdf

    def pdf(text: str, creation: str) -> bytes:
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), text)
        doc.set_metadata({"creationDate": creation})
        data = doc.tobytes()
        doc.close()
        return data

    a, b = pdf("same words", "D:20260101000000"), pdf("same words", "D:20270202000000")
    assert a != b
    assert sources.text_fingerprint(a) == sources.text_fingerprint(b)
    assert sources.text_fingerprint(pdf("other words", "D:20260101000000")) != (
        sources.text_fingerprint(a)
    )
