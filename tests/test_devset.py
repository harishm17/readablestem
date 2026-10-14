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

from devset_support import PAGE, book, page, tiny_set, write_authored
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


def test_the_part_of_a_section_depends_on_the_salt_and_its_name_only() -> None:
    assert ds.part_of("s", "book", "1-1-x") == ds.part_of("s", "book", "1-1-x")
    parts = [ds.part_of("salt", "b", f"{n}-{n}-x") for n in range(2000)]
    share = parts.count("check") / len(parts)
    assert abs(share - ds.CHECK_SHARE) < 0.03
    assert [ds.part_of("a", "b", f"{n}") for n in range(300)] != [
        ds.part_of("z", "b", f"{n}") for n in range(300)
    ]


# ------------------------------------------------------------------ page records


def test_build_page_labels_every_kind_and_adds_the_extra_records() -> None:
    records = ds.build_page(book(), page(), PAGE, "2026-10-14")
    by_kind: dict[str, list[dict]] = {}
    for r in records:
        by_kind.setdefault(r["kind"], []).append(r)
    inline, system = by_kind["equation"]
    assert inline["classes"] == ["inline_fraction", "numbered_exercise_eq"]
    assert inline["exercise_number"] == "3" and inline["appearance"] == "text"
    assert system["classes"] == ["system_braced"]
    assert [h["classes"] for h in by_kind["heading"]] == [
        [],
        ["heading_repeated_label"],
        ["heading_repeated_label"],
    ]
    (script,) = by_kind["script"]
    assert script["text"].startswith("A sample of H_{2}O at 1.0 g/cm^{3}")
    assert [r["class"] for r in script["runs"]] == [
        "prose_chem_script",
        "prose_unit_power",
        "prose_nuclide",
    ]
    assert by_kind["code"][0]["text"] == "print(1)"
    assert by_kind["glossary"][0]["term"] == "term"
    assert by_kind["table"][0]["classes"] == ["table_data"]
    ids = [r["id"] for r in records]
    assert len(ids) == len(set(ids)) and all(i.startswith("openstax/demo/1-1-demo/") for i in ids)


def test_count_items_counts_each_script_run_and_each_class_of_the_other_records() -> None:
    records = ds.build_page(book(), page(), PAGE, "2026-10-14")
    counts = ds.count_items(records)
    assert counts["prose_chem_script"] == counts["prose_unit_power"] == 1
    assert counts["heading_repeated_label"] == 2
    assert counts["code_block"] == counts["glossary_entry"] == 1


# -------------------------------------------------------------------- the choice


def cand(key: str, pages: int, **counts: int) -> ds.Candidate:
    book_slug, slug = key.split("/")
    return ds.Candidate(
        book_slug, slug, key, words=pages * 320, equations=0, figures=0, tables=0, overlap=0.0,
        counts=counts, xhtml_grams={(key, str(i)) for i in range(20)},
    )  # fmt: skip


def test_the_greedy_choice_takes_the_most_needed_items_per_page() -> None:
    pool = [
        cand("b/small", 2, matrix=10),
        cand("b/big", 20, matrix=30),
        cand("b/other", 2, determinant=10),
    ]
    chosen = ds.greedy_choose(
        pool, {"matrix": 10, "determinant": 10}, max_sections=5, max_pages=100
    )
    assert [c.slug for c in chosen] == ["other", "small"]  # tie: by name; big adds nothing


def test_the_greedy_choice_respects_the_page_and_section_limits() -> None:
    pool = [cand(f"b/s{i}", 10, matrix=5) for i in range(6)]
    assert len(ds.greedy_choose(pool, {"matrix": 100}, max_sections=3, max_pages=100)) == 3
    assert len(ds.greedy_choose(pool, {"matrix": 100}, max_sections=9, max_pages=25)) == 2


def test_siblings_that_repeat_a_chosen_section_are_skipped() -> None:
    first = cand("b/a", 4, matrix=10)
    twin = cand("c/a", 4, matrix=10)
    twin.xhtml_grams = set(first.xhtml_grams)
    other = cand("c/z", 4, matrix=9)
    chosen = ds.greedy_choose([first, twin, other], {"matrix": 100}, max_sections=5, max_pages=100)
    assert [c.key for c in chosen] == ["b/a", "c/z"]


# --------------------------------------------------------------- archive and seal


def test_the_check_archive_is_reproducible_and_reads_back() -> None:
    files = {"b.txt": b"two", "a/c.txt": b"one"}
    first = ds.make_tar_gz(files)
    assert first == ds.make_tar_gz(dict(reversed(list(files.items()))))
    assert ds.read_tar_gz(first) == files


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


def test_manifest_counts_items_per_class_and_source(tmp_path: Path) -> None:
    root = tiny_set(tmp_path)
    manifest = json.loads((root / "dev" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["class_counts"]["authored"]["heading_sentence"] == 1
    assert manifest["class_counts"]["openstax"]["code_block"] == 1
    assert manifest["authored_documents"][0]["id"] == "doc-a"
    rows = ds.summary_rows(manifest, {"heading_sentence": 3})
    row = next(r for r in rows if r["id"] == "heading_sentence")
    assert (row["authored"], row["check"], row["dev"]) == (1, 3, row["openstax"] + 1)
    assert "| Headings that are sentences" in ds.summary_markdown(rows)


# ------------------------------------------------------------- the committed set

committed = pytest.mark.skipif(
    not (ROOT / "dev" / "manifest.json").is_file(), reason="data/dev-v2 is not built"
)


@committed
def test_the_committed_dev_part_matches_its_manifest_and_the_seal_matches_the_archive() -> None:
    assert ds.verify(ROOT) == []


@committed
def test_the_committed_set_reaches_every_class_target() -> None:
    manifest = json.loads((ROOT / "dev" / "manifest.json").read_text(encoding="utf-8"))
    counts = ds.total_class_counts(manifest)
    short = {c.id: (counts[c.id], c.target) for c in dc.CLASSES if counts[c.id] < c.target}
    assert short == {}


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
