"""MinerU: read `structured_content.json` from the zip `mineru-kit parse --format zip` writes.

Structured content is MinerU's block tree, one list of typed blocks per page, with text
already rendered as Markdown (maths inside text as `$...$`). Markdown in the same zip
(`markdown.md`) is the fallback when the block tree is missing or unreadable.

Mapping, from MinerU's block types (docvortex `BlockType`):
- `paragraph_title`, `doc_title` -> heading, level = the block's `level` (1 when absent).
- `equation` -> display equation, `content` is LaTeX. A trailing `\\tag{...}` (MinerU writes
  the printed equation number that way) is left in: it is the tool's output, and the
  allowlist-free and normalised rows of the plan are where its effect is shown.
- `text` -> read as Markdown (inline `$...$` becomes inline equations); `list` likewise, as
  list items; `ref_text`, `index`, `aside_text`, `page_footnote` -> para.
- `header`, `footer`, `page_number` -> furniture.
- `image`, `chart`, `code` -> figure (code is a block of text, kept as para); the image is
  kept when the block has an `image_source`; its captions become caption blocks; the text
  alternative is whatever text MinerU's image analysis wrote in `content`, with the Markdown
  image syntax stripped (empty alt text is None).
- `table` -> table, grid from the HTML in `content`.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any

from parsers_bench.adapters.builder import Builder, squash, table_grid_from_html
from parsers_bench.intermediate import Intermediate

_FURNITURE = {"header", "footer", "page_number"}
_IMAGE_MARKDOWN = re.compile(r"!\[[^\]]*]\([^)]*\)")


def adapt_zip(path: Path, *, section: str) -> Intermediate:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "structured_content.json" in names:
            try:
                content = json.loads(archive.read("structured_content.json"))
                return adapt(content, section=section)
            except (ValueError, KeyError, TypeError):
                pass
        if "markdown.md" in names:
            builder = Builder("mineru", section)
            builder.markdown(archive.read("markdown.md").decode("utf-8"))
            doc = builder.doc
            doc.source = "markdown.md (structured_content.json missing or unreadable)"
            doc.notes.append("markdown fallback: heading levels and block types are Markdown's")
            return doc
    raise ValueError(f"{path}: no structured_content.json or markdown.md in the zip")


def adapt(content: dict[str, Any], *, section: str) -> Intermediate:
    builder = Builder("mineru", section)
    for page in content["pages"]:
        for block in page.get("blocks", []):
            _block(builder, block)
    doc = builder.doc
    doc.source = "structured_content.json"
    return doc


def _block(builder: Builder, block: dict[str, Any]) -> None:
    kind = block.get("type", "")
    text = block.get("content") or ""
    if kind in ("paragraph_title", "doc_title"):
        builder.heading(int(block.get("level") or 1), _plain(text))
    elif kind == "equation":
        builder.equation("block", latex=text)
    elif kind == "text":
        builder.markdown(text)
    elif kind == "list":
        builder.markdown(text, kind="list")
    elif kind in ("ref_text", "index", "aside_text", "page_footnote"):
        builder.markdown(text)
    elif kind in _FURNITURE:
        builder.text("furniture", text)
    elif kind in ("image", "chart"):
        captions = [c.get("content", "") for c in block.get("captions", [])]
        builder.figure(
            " ".join(captions) or None,
            bool(block.get("image_source")),
            _IMAGE_MARKDOWN.sub(" ", text) or None,
        )
        for caption in captions:
            builder.text("caption", caption)
    elif kind == "table":
        builder.table(table_grid_from_html(text))
        for caption in block.get("captions", []):
            builder.text("caption", caption.get("content", ""))
    elif kind == "code":
        builder.text("para", _IMAGE_MARKDOWN.sub(" ", text))
    elif text:
        builder.markdown(text)


def _plain(markdown: str) -> str:
    """A title's text without emphasis markers (titles may hold inline maths, kept as TeX)."""
    return squash(markdown.replace("**", "").replace("$", " "))
