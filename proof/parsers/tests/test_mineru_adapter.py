"""The MinerU adapter on hand-made structured content and zips. Invented text only."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from parsers_bench.adapters import mineru


def structured() -> dict[str, Any]:
    return {
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {"type": "paragraph_title", "level": 2, "content": "Intro **Part**"},
                    {"type": "text", "content": "Speed is $v = d / t$ for a trip of 40 m."},
                    {"type": "equation", "content": "x = y\\tag{2.5}"},
                    {
                        "type": "chart",
                        "image_source": "images/a.jpg",
                        "content": "",
                        "captions": [{"content": "Figure 1 A chart."}],
                        "footnotes": [],
                    },
                    {
                        "type": "image",
                        "image_source": "images/b.jpg",
                        "content": "![](images/b.jpg)",
                        "captions": [],
                    },
                    {
                        "type": "table",
                        "content": "<table><tr><td>a</td><td>b</td></tr></table>",
                        "captions": [{"content": "Table 1 Values."}],
                    },
                    {"type": "list", "content": "- one\n- two"},
                    {"type": "footer", "content": "Access for free"},
                    {"type": "doc_title", "content": "Whole Title"},
                ],
            }
        ]
    }


def test_blocks_map_onto_the_shared_kinds() -> None:
    doc = mineru.adapt(structured(), section="s")
    assert [(h.level, h.text) for h in doc.headings] == [(2, "Intro Part"), (1, "Whole Title")]
    assert [(e.display, e.latex) for e in doc.equations] == [
        ("inline", "v = d / t"),
        ("block", "x = y\\tag{2.5}"),
    ]
    kinds = {b.text: b.kind for b in doc.blocks}
    assert kinds["Speed is for a trip of 40 m."] == "para"
    assert kinds["one"] == kinds["two"] == "list"
    assert kinds["Access for free"] == "furniture"
    assert kinds["Figure 1 A chart."] == "caption"


def test_equation_tag_is_left_in_the_adapted_latex() -> None:
    # Normalising it is the scorer's job (the `normalised_plus` row), not the adapter's.
    doc = mineru.adapt(structured(), section="s")
    assert "\\tag{2.5}" in doc.equations[1].latex


def test_images_are_kept_with_empty_alt() -> None:
    doc = mineru.adapt(structured(), section="s")
    assert [(f.image_kept, f.text_alternative, f.caption) for f in doc.figures] == [
        (True, None, "Figure 1 A chart."),
        (True, None, None),
    ]


def test_table_grid_and_caption() -> None:
    doc = mineru.adapt(structured(), section="s")
    assert [t.grid for t in doc.tables] == [[["a", "b"]]]
    assert any(b.kind == "caption" and b.text == "Table 1 Values." for b in doc.blocks)


def write_zip(path: Path, files: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def test_zip_reads_structured_content(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "a.zip", {"structured_content.json": json.dumps(structured())})
    doc = mineru.adapt_zip(archive, section="s")
    assert doc.source == "structured_content.json"
    assert len(doc.headings) == 2


def test_zip_falls_back_to_markdown_when_structured_content_is_broken(tmp_path: Path) -> None:
    archive = write_zip(
        tmp_path / "b.zip",
        {"structured_content.json": "{not json", "markdown.md": "## Heading\n\nSome words here.\n"},
    )
    doc = mineru.adapt_zip(archive, section="s")
    assert doc.source.startswith("markdown.md")
    assert [h.text for h in doc.headings] == ["Heading"]
    assert doc.notes


def test_zip_with_neither_file_is_an_error(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "c.zip", {"other.txt": "x"})
    with pytest.raises(ValueError, match="no structured_content"):
        mineru.adapt_zip(archive, section="s")
