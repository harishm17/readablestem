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


# ------------------------------------------------------------------------------ ink_strokes


# -------------------------------------------------------------------------------- picking


def test_used_samples_reads_the_registered_manifests(inks: Path, tmp_path: Path) -> None:
    sample = f"{3:016x}"
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"parts": {"p": {"units": [{"expressions": [{"sample_id": sample}]}]}}})
    )
    ids, labels = hv.used_samples([manifest], inks.parent)
    assert ids == {sample}
    assert labels == {hw.read_ink(inks / f"{sample}.inkml").label}
