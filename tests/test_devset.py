"""The extended development set: selection, parts, seal and the committed files.

The first half builds tiny sets in a temporary folder with synthetic pages. The second half
checks the committed set (data/dev-v2) against its own manifest and against the rules it
was built under; it reads the sealed archive only to compare hashes and section names.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from devset_support import PAGE, tiny_set, write_authored
from readablestem.gold import devset as ds
from readablestem.gold import devset_classify as dc
from readablestem.gold import heldout as heldout_registry

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "data" / "dev-v2"
# --------------------------------------------------------------------- measures


def test_overlap_measure_is_the_one_of_the_held_out_selection_script() -> None:
    path = REPO / "proof" / "selection" / "features.py"
    spec = importlib.util.spec_from_file_location("heldout_features", path)
    assert spec and spec.loader
    features = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(features)
    assert features.NEAR_DUP == ds.NEAR_DUP
    assert features.MIN_WORDS == ds.MIN_WORDS
    for text in (PAGE, "<p>One two three four five six seven eight nine ten, <b>x</b></p>"):
        assert features.words_outside_math(text) == ds.words_outside_math(text)
    words = features.words_outside_math(PAGE * 3)
    assert features.ngrams(words) == ds.ngrams(words)


def test_containment_is_the_share_of_a_pages_8_grams_found_in_the_reference() -> None:
    words = [f"w{i}" for i in range(20)]
    assert ds.containment(words, set()) == 0.0
    assert ds.containment(words, ds.ngrams(words)) == 1.0
    assert ds.containment(words, ds.ngrams(words[:10])) == pytest.approx(3 / 13)
    assert ds.containment(words[:5], ds.ngrams(words)) == 0.0  # shorter than one 8-gram


# ------------------------------------------------------------------ page records


# -------------------------------------------------------------------- the choice


def cand(key: str, pages: int, **counts: int) -> ds.Candidate:
    book_slug, slug = key.split("/")
    return ds.Candidate(
        book_slug, slug, key, words=pages * 320, equations=0, figures=0, tables=0, overlap=0.0,
        counts=counts, xhtml_grams={(key, str(i)) for i in range(20)},
    )  # fmt: skip


# --------------------------------------------------------------- archive and seal


def test_an_archive_with_a_path_that_leaves_the_folder_is_refused() -> None:
    import io
    import tarfile

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        info = tarfile.TarInfo("../evil")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ds.DevsetError, match="unsafe path"):
        ds.read_tar_gz(raw.getvalue())


def test_authored_files_keep_only_the_documents_of_the_asked_variant(tmp_path: Path) -> None:
    pool = tmp_path / "pool"
    write_authored(pool, "doc-a", "dev")
    write_authored(pool, "doc-a-chk", "check")
    assert sorted(ds.authored_files(pool, "dev")) == [
        "authored/doc-a/doc-a.pdf",
        "authored/doc-a/gold.jsonl",
    ]
    assert all("doc-a-chk" in p for p in ds.authored_files(pool, "check"))


def test_a_set_that_matches_its_manifest_and_seal_verifies(tmp_path: Path) -> None:
    assert ds.verify(tiny_set(tmp_path)) == []


def test_a_changed_dev_file_a_stray_file_and_a_missing_file_are_reported(tmp_path: Path) -> None:
    root = tiny_set(tmp_path)
    gold = root / "dev" / "authored" / "doc-a" / "gold.jsonl"
    gold.write_bytes(gold.read_bytes() + b"\n")
    (root / "dev" / "extra.txt").write_text("x", encoding="utf-8")
    (root / "dev" / "authored" / "doc-a" / "doc-a.pdf").unlink()
    problems = ds.verify(root)
    assert any("authored/doc-a/gold.jsonl does not match" in p for p in problems)
    assert any("extra.txt is not in the manifest" in p for p in problems)
    assert any("doc-a.pdf is listed in the manifest but missing" in p for p in problems)


def test_a_dev_record_from_a_held_out_section_is_reported(tmp_path: Path, monkeypatch) -> None:
    root = tiny_set(tmp_path)
    monkeypatch.setattr(ds, "is_heldout", lambda b, s: (b, s) == ("demo", "1-1-demo"))
    assert any("held-out" in p for p in ds.verify(root))


def test_a_changed_check_archive_is_reported_and_never_extracted(tmp_path: Path) -> None:
    root = tiny_set(tmp_path)
    archive = root / ds.CHECK_ARCHIVE
    archive.write_bytes(archive.read_bytes() + b"x")
    assert any("SHA-256" in p for p in ds.verify(root))
    with pytest.raises(ds.DevsetError, match="SHA-256"):
        ds.load_check_part(root, tmp_path / "cache")
    assert not (tmp_path / "cache").exists()


def test_the_check_part_is_extracted_once_outside_the_set(tmp_path: Path) -> None:
    root = tiny_set(tmp_path)
    cache = tmp_path / "cache"
    folder = ds.load_check_part(root, cache)
    assert cache in folder.parents
    assert (folder / "authored" / "doc-a-chk" / "gold.jsonl").is_file()
    assert (folder / "manifest.json").is_file()
    marker = folder / ".sealed-ok"
    stamp = marker.stat().st_mtime_ns
    assert ds.load_check_part(root, cache) == folder
    assert marker.stat().st_mtime_ns == stamp


def test_the_seal_names_no_item(tmp_path: Path) -> None:
    root = tiny_set(tmp_path)
    text = (root / ds.SEAL_FILE).read_text(encoding="utf-8")
    seal = json.loads(text)
    assert seal["records"] == 1 and seal["files"] == 2
    assert "doc-a" not in text and "authored/" not in text


# ------------------------------------------------------------- the committed set

committed = pytest.mark.skipif(
    not (ROOT / "dev" / "manifest.json").is_file(), reason="data/dev-v2 is not built"
)


@committed
def test_the_committed_dev_part_matches_its_manifest_and_the_seal_matches_the_archive() -> None:
    assert ds.verify(ROOT) == []


@committed
def test_no_dev_section_is_a_held_out_section_or_from_a_held_out_book() -> None:
    held = {(s.book, s.slug) for s in heldout_registry.REGISTERED.sections}
    books = {b for b, _ in held}
    for path in (ROOT / "dev" / "openstax").glob("*/*.jsonl"):
        book = path.parent.name
        assert book not in books and (book, path.stem) not in held


@committed
def test_selection_overlaps_with_the_held_out_text_are_below_the_threshold() -> None:
    selection = json.loads((ROOT / "selection.json").read_text(encoding="utf-8"))
    rows = selection["dev"] + selection["dev_key_terms"]
    assert rows and all(r["overlap_with_heldout"] < ds.NEAR_DUP for r in rows)
    # Every OpenStax page on disk is a selected one.
    chosen = {(r["book"], r["slug"]) for r in rows}
    on_disk = {(p.parent.name, p.stem) for p in (ROOT / "dev" / "openstax").glob("*/*.jsonl")}
    assert on_disk == chosen


@committed
def test_every_record_carries_a_known_licence_and_known_classes() -> None:
    licences = {"Creative Commons Attribution License", "MIT License"}
    for path in (ROOT / "dev").rglob("*.jsonl"):
        for record in ds.parse_jsonl(path.read_bytes()):
            assert record["license_name"] in licences
            assert record["license_url"]
            assert set(record["classes"]) <= set(dc.CLASS_IDS)
            if record["source"] == "openstax":
                assert record["license_url"] == "http://creativecommons.org/licenses/by/4.0/"
                assert record["source_url"].startswith("https://openstax.org/books/")


@committed
def test_the_check_part_shares_no_section_or_document_with_dev(tmp_path: Path) -> None:
    folder = ds.load_check_part(ROOT, tmp_path / "cache")
    check = {(p.parent.name, p.stem) for p in (folder / "openstax").glob("*/*.jsonl")}
    dev = {(p.parent.name, p.stem) for p in (ROOT / "dev" / "openstax").glob("*/*.jsonl")}
    assert check and not check & dev
    held = {(s.book, s.slug) for s in heldout_registry.REGISTERED.sections}
    assert not check & held
    seal = json.loads((ROOT / ds.SEAL_FILE).read_text(encoding="utf-8"))
    ids = sorted(r["id"] for p in folder.rglob("*.jsonl") for r in ds.parse_jsonl(p.read_bytes()))
    assert ds.sha256_bytes("\n".join(ids).encode("utf-8")) == seal["record_ids_sha256"]
