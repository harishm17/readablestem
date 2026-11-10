"""The handwriting set builder (eval/handwriting_set.py): reading ink, picking labels, composing
pages, gold records. Offline, with a few synthetic InkML files; no MathWriting data is needed."""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from readablestem.eval import handwriting_set as hw

needs_font = pytest.mark.skipif(not hw.FONT_PATH.is_file(), reason="DejaVu Serif not installed")


def inkml(label: str, *, method: str = "human", sample_id: str = "0123456789abcdef") -> str:
    traces = "\n".join(
        f'<trace id="{i}">{x} 10 0.0,{x + 30} 60 10.0,{x + 60} 10 20.0</trace>'
        for i, x in enumerate((10, 90, 170, 250))
    )
    return f"""<ink xmlns="http://www.w3.org/2003/InkML">
  <annotation type="label">{label}</annotation>
  <annotation type="splitTagOriginal">test</annotation>
  <annotation type="inkCreationMethod">{method}</annotation>
  <annotation type="sampleId">{sample_id}</annotation>
  <annotation type="normalizedLabel">{label}</annotation>
  <trace id="x">1 2 3</trace>
  {traces}
</ink>"""


def write(tmp_path: Path, label: str, **kw: str) -> Path:
    path = tmp_path / f"{kw.get('sample_id', 'a')}.inkml"
    path.write_text(inkml(label, **kw), encoding="utf-8")
    return path


def test_read_ink_refuses_machine_ink(tmp_path: Path) -> None:
    with pytest.raises(hw.InkError):
        hw.read_ink(write(tmp_path, "x", method="boundingBoxes"))


def _pool(tmp_path: Path, n: int = 60) -> hw.Pool:
    items = []
    for i in range(n):
        sample = f"{i:016x}"
        label = f"x_{{{i % 9}}}+y^{{{i % 7}}}=z"
        cand = hw.candidate(hw.read_ink(write(tmp_path, label, sample_id=sample)))
        assert cand is not None
        items.append(cand)
    return hw.Pool(items)


@needs_font
def test_unit_pdf_is_an_image_only_scan_and_gold_follows_the_plan(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    images, plan = hw.compose_unit("hw-x-02", pool, "seed")
    pdf, made = hw.unit_pdf(images, 7)
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    assert doc.page_count == len(images) == len(made)
    for page in doc:
        assert page.get_text().strip() == ""  # no text layer: `page_type` calls it a scan
        assert len(page.get_images()) == 1
        Image.open(io.BytesIO(doc.extract_image(page.get_images()[0][0])["image"])).load()
    by_id = {c.ink.sample_id: c for c in pool.items}
    records = hw.gold_records(plan, by_id, part="dev", pdf_name="pdfs/x.pdf", pdf_sha256="0" * 64)
    assert [r["index_on_page"] for r in records] == list(range(len(plan.slots)))
    assert {r["display"] for r in records} == {"block", "inline"}
    assert all(r["kind"] == "equation" and r["license_name"] == hw.LICENSE_NAME for r in records)
    assert [r["pdf_page"] for r in records] == sorted(r["pdf_page"] for r in records)
    # the same plan and seed give the same pictures, byte for byte
    assert hw.pdf_content_sha256(hw.unit_pdf(images, 7)[0]) == hw.pdf_content_sha256(pdf)
