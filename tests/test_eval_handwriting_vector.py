"""The vector-ink handwriting set builder (eval/handwriting_vector.py): deterministic rebuilds,
what `page_type` says of the built pages, and what `ink_strokes` recovers from them. Offline, from
synthetic InkML files; no MathWriting data is needed."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pymupdf
import pytest

from readablestem.eval import handwriting_set as hw
from readablestem.eval import handwriting_vector as hv
from readablestem.pipeline import page_type
from readablestem.pipeline.ink_strokes import extract_ink

SEED = "vector-test-seed"


def _traces(i: int) -> str:
    """Six strokes like pen letters: open arcs of 250 to 330 degrees of different size and
    wobble, a few overlapping, the last a dot. (Smooth sine waves are not taken for pen strokes.)"""
    out = []
    for k in range(5):
        r = 9 + 2.3 * k + (i % 5)
        cx, cy = 14 + 26 * k + (i % 4), 30 + 4 * ((k * 3 + i) % 5)
        a0 = 20 * k + 7 * (i % 7)
        sweep = 250 + 17 * ((k + i) % 6)
        pts = []
        for t in range(41):
            angle = math.radians(a0 + sweep * t / 40)
            wobble = 1 + 0.07 * math.sin(5 * angle + k) + 0.04 * math.sin(11 * angle + i)
            pts.append((cx + 0.8 * r * wobble * math.cos(angle), cy + r * wobble * math.sin(angle)))
        out.append(
            f'<trace id="{k}">'
            + ",".join(f"{x:.2f} {y:.2f} {t}.0" for t, (x, y) in enumerate(pts))
            + "</trace>"
        )
    out.append('<trace id="9">100.0 15.0 0.0</trace>')
    return "\n".join(out)


def _inkml(label: str, sample_id: str, i: int) -> str:
    return f"""<ink xmlns="http://www.w3.org/2003/InkML">
  <annotation type="label">{label}</annotation>
  <annotation type="splitTagOriginal">test</annotation>
  <annotation type="inkCreationMethod">human</annotation>
  <annotation type="sampleId">{sample_id}</annotation>
  <annotation type="normalizedLabel">{label}</annotation>
  {_traces(i)}
</ink>"""


def _write_inks(folder: Path, n: int = 120) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        sample = f"{i:016x}"
        label = f"x_{{{i % 9}}}+y^{{{i // 9}}}=z"
        (folder / f"{sample}.inkml").write_text(_inkml(label, sample, i), encoding="utf-8")


@pytest.fixture(scope="module")
def inks(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("raw") / "test"
    _write_inks(folder)
    return folder


@pytest.fixture(scope="module")
def candidates(inks: Path) -> dict[str, hw.Candidate]:
    found = hv.pick_candidates(inks, SEED, 100, exclude_ids=set(), exclude_labels=set())
    assert len(found) == 100
    return found


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory, candidates: dict[str, hw.Candidate]):
    out = tmp_path_factory.mktemp("set")
    return out, hv.build_set(candidates, out, seed=SEED, units=6)


# ------------------------------------------------------------------------------ rebuilds


def test_rebuild_hash_is_deterministic(built, candidates, tmp_path: Path) -> None:
    out, manifest = built
    again = hv.build_set(candidates, tmp_path, seed=SEED, units=6)
    assert again["rebuild_hash"] == manifest["rebuild_hash"]
    assert (tmp_path / "manifest.json").read_text() == (out / "manifest.json").read_text()
    for unit in manifest["units"]:
        name = f"pdfs/{unit['unit']}.pdf"
        assert hv.page_content_hash((tmp_path / name).read_bytes()) == unit["pdf_content_sha256"]
        assert (tmp_path / f"{unit['unit']}.jsonl").read_text() == (
            out / f"{unit['unit']}.jsonl"
        ).read_text()


def test_rebuild_hash_follows_the_seed_and_the_ink(built, candidates, tmp_path: Path) -> None:
    _, manifest = built
    other = hv.build_set(candidates, tmp_path / "a", seed="another-seed", units=6)
    assert other["rebuild_hash"] != manifest["rebuild_hash"]
    first = next(iter(candidates))
    changed = dict(candidates)
    ink = changed[first].ink
    changed[first] = hw.Candidate(
        hw.Ink(ink.sample_id, ink.split, ink.label, ink.strokes, "0" * 64),
        changed[first].mathml_inline,
        changed[first].mathml_display,
        changed[first].tokens,
        changed[first].inline_ok,
    )
    same_layout = hv.build_set(changed, tmp_path / "b", seed=SEED, units=6)
    assert same_layout["rebuild_hash"] != manifest["rebuild_hash"]


def test_manifest_and_gold_describe_the_set_without_holding_ink(built) -> None:
    out, manifest = built
    assert manifest["n_units"] == 6 and manifest["n_pages"] == 12
    assert {u["style"] for u in manifest["units"]} == set(hv.STYLES)
    assert {u["ruled"] for u in manifest["units"]} == {True, False}
    assert {u["typed_header"] for u in manifest["units"]} == {True, False}
    assert manifest["source"]["license"] == hw.LICENSE_NAME
    ids = [e["sample_id"] for u in manifest["units"] for e in u["expressions"]]
    assert len(ids) == len(set(ids)) == manifest["n_expressions"]
    for unit in manifest["units"]:
        lines = (out / f"{unit['unit']}.jsonl").read_text().splitlines()
        records = [json.loads(line) for line in lines]
        assert [r["sample_id"] for r in records] == [e["sample_id"] for e in unit["expressions"]]
        for r, e in zip(records, unit["expressions"], strict=True):
            assert r["kind"] == "equation" and r["part"] == hv.PART and r["latex"]
            assert r["ink_style"] == unit["style"] and r["strokes"] == e["strokes"]
            assert r["pdf"] == f"pdfs/{unit['unit']}.pdf"
            assert r["pdf_sha256"] == unit["pdf_content_sha256"]
    assert "points" not in (out / "manifest.json").read_text()


def test_pages_have_no_text_layer_but_the_short_typed_header(built) -> None:
    out, manifest = built
    for unit in manifest["units"]:
        doc = pymupdf.open(out / "pdfs" / f"{unit['unit']}.pdf")
        assert doc.page_count == hv.PAGES_PER_UNIT
        for number, page in enumerate(doc, 1):
            text = page.get_text().strip()
            assert not page.get_images()
            if number == 1 and unit["typed_header"]:
                assert 0 < len(text) <= page_type.BODY_MAX
            else:
                assert text == ""


# ------------------------------------------------------------------------------ page_type


def _kinds(out: Path, manifest: dict, style: str) -> list[str]:
    kinds = []
    for unit in manifest["units"]:
        if unit["style"] != style:
            continue
        for page in pymupdf.open(out / "pdfs" / f"{unit['unit']}.pdf"):
            result = page_type.classify(
                page, page.get_text("rawdict")["blocks"], page.get_cdrawings()
            )
            kinds.append(result.kind)
    return kinds


@pytest.mark.parametrize("style", ["path", "outline"])
def test_page_type_calls_built_pages_handwritten(built, style: str) -> None:
    kinds = _kinds(*built, style)
    assert len(kinds) == 4 and set(kinds) == {"handwritten"}, kinds


@pytest.mark.xfail(
    strict=True,
    reason="page_type does not take per-sample filled discs for pen strokes: such a page is "
    "vector_no_text. Remove the marker when it does (the P0 probe settles what real exports are).",
)
def test_page_type_calls_per_sample_pages_handwritten(built) -> None:
    assert set(_kinds(*built, "samples")) == {"handwritten"}


def test_per_sample_pages_are_at_least_not_taken_for_typed_or_scanned(built) -> None:
    assert set(_kinds(*built, "samples")) <= {"handwritten", "vector_no_text"}


# ------------------------------------------------------------------------------ ink_strokes


def _recovered(out: Path, unit: dict) -> tuple[int, int, int]:
    """(strokes drawn, strokes recovered inside the drawn boxes, expressions recovered exactly)."""
    drawn = found = exact = 0
    for number, page in enumerate(pymupdf.open(out / "pdfs" / f"{unit['unit']}.pdf"), 1):
        ink = extract_ink(page)
        assert ink.analysed
        for e in (e for e in unit["expressions"] if e["page"] == number):
            x0, y0, x1, y1 = e["box_pt"]
            inside = [
                s
                for s in ink.strokes
                if x0 - 2 <= s.bbox[0] and s.bbox[2] <= x1 + 2
                and y0 - 2 <= s.bbox[1] and s.bbox[3] <= y1 + 2
            ]  # fmt: skip
            drawn += e["strokes"]
            found += len(inside)
            exact += len(inside) == e["strokes"]
    return drawn, found, exact


@pytest.mark.parametrize("style", list(hv.STYLES))
def test_ink_strokes_recovers_the_built_strokes(built, style: str) -> None:
    out, manifest = built
    drawn = found = exact = n = 0
    for unit in (u for u in manifest["units"] if u["style"] == style):
        d, f, x = _recovered(out, unit)
        drawn, found, exact, n = drawn + d, found + f, exact + x, n + len(unit["expressions"])
    assert drawn > 100
    if style == "samples":  # fast curves and crossings split or join a few strokes
        assert abs(found - drawn) <= 0.1 * drawn and exact >= 0.6 * n, (drawn, found, exact, n)
    else:
        assert (found, exact) == (drawn, n), style


def test_ink_strokes_drops_the_ruled_paper_and_keeps_the_ink(built) -> None:
    out, manifest = built
    ruled = [u for u in manifest["units"] if u["ruled"]]
    assert ruled
    page = pymupdf.open(out / "pdfs" / f"{ruled[0]['unit']}.pdf")[0]
    ink = extract_ink(page)
    assert ink.dropped.get("rule", 0) >= 20
    assert (
        len(ink.strokes) >= sum(e["strokes"] for e in ruled[0]["expressions"] if e["page"] == 1) - 5
    )


# -------------------------------------------------------------------------------- picking


def test_pick_candidates_skips_registered_ids_and_labels(inks: Path, tmp_path: Path) -> None:
    first = hv.pick_candidates(inks, SEED, 10, exclude_ids=set(), exclude_labels=set())
    skip_id = next(iter(first))
    skip_label = hw.read_ink(inks / f"{list(first)[1]}.inkml").label
    again = hv.pick_candidates(inks, SEED, 10, exclude_ids={skip_id}, exclude_labels={skip_label})
    assert skip_id not in again
    assert skip_label not in {c.ink.label for c in again.values()}
    assert len(again) == 10


def test_used_samples_reads_the_registered_manifests(inks: Path, tmp_path: Path) -> None:
    sample = f"{3:016x}"
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"parts": {"p": {"units": [{"expressions": [{"sample_id": sample}]}]}}})
    )
    ids, labels = hv.used_samples([manifest], inks.parent)
    assert ids == {sample}
    assert labels == {hw.read_ink(inks / f"{sample}.inkml").label}


def test_unknown_style_is_refused() -> None:
    doc = pymupdf.open()
    shape = doc.new_page().new_shape()
    with pytest.raises(ValueError, match="unknown ink style"):
        hv.draw_stroke(shape, [(0, 0), (5, 5)], "crayon", 1.4, (0, 0, 0))
