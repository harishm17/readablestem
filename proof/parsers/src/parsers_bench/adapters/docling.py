"""Docling: read the DoclingDocument JSON (`docling --to json`), in body order.

The JSON is the tool's lossless output. Its HTML and Markdown exports are serialisers on top
of it (they write every heading at one level, for example), so they are not scored here.
The JSON is read as plain data: Docling itself is not needed to adapt its output.

Mapping, from the schema ("DoclingDocument", version 1.x):
- `section_header` -> heading, level = `level` + 1 (what Docling's HTML export writes: its
  headings start at <h2>); `title` -> level 1.
- `formula` -> equation. `text` holds LaTeX only when formula enrichment ran; otherwise it is
  empty and the formula is an equation with no latex, whose `raw` is `orig` (the PDF text
  layer's form of it, which is what the default HTML puts in the formula picture's alt text).
  Inline when the formula sits in an `inline` group, display otherwise.
- `list_item` -> list; `caption` -> caption; `page_header`, `page_footer` -> furniture; every
  other text label (`text`, `paragraph`, `footnote`, `code`, `reference`, ...) -> para.
- A text whose parent is a picture (Docling OCRs the words inside a figure) is
  `figure_text`, kept apart from prose.
- `picture` -> figure. `image_kept` is true when the JSON carries the cropped image. The text
  alternative is a picture description if Docling wrote one (off by default), else None.
- `table` -> table, from `data.grid`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from parsers_bench.adapters.builder import Builder
from parsers_bench.intermediate import Intermediate

_FURNITURE = {"page_header", "page_footer"}


def adapt_file(path: Path, *, section: str) -> Intermediate:
    return adapt(json.loads(path.read_text(encoding="utf-8")), section=section)


def adapt(doc: dict[str, Any], *, section: str) -> Intermediate:
    builder = Builder("docling", section)
    seen: set[str] = set()

    def resolve(ref: str) -> dict[str, Any] | None:
        _, kind, index = ref.split("/")
        items = doc.get(kind) or []
        return items[int(index)] if int(index) < len(items) else None

    def walk(node: dict[str, Any], inline: bool) -> None:
        for child in node.get("children", []):
            ref = child["$ref"]
            if ref in seen:
                continue
            seen.add(ref)
            item = resolve(ref)
            if item is None:
                continue
            kind = ref.split("/")[1]
            if kind == "groups":
                walk(item, inline or item.get("label") == "inline")
            elif kind == "texts":
                text_item(item, inline)
            elif kind == "pictures":
                picture(item)
            elif kind == "tables":
                table(item)

    def text_item(item: dict[str, Any], inline: bool) -> None:
        label = item.get("label", "text")
        text = item.get("text") or ""
        parent = (item.get("parent") or {}).get("$ref", "")
        if label == "section_header":
            builder.heading(int(item.get("level", 1)) + 1, text)
        elif label == "title":
            builder.heading(1, text)
        elif label == "formula":
            builder.equation(
                "inline" if inline else "block",
                latex=text or None,
                raw=item.get("orig") if not text else None,
            )
        elif label == "caption":
            builder.text("caption", text)
        elif label == "list_item":
            builder.text("list", text)
        elif label in _FURNITURE:
            builder.text("furniture", text)
        elif parent.startswith("#/pictures/"):
            builder.text("figure_text", text)
        else:
            builder.text("para", text)

    def picture(item: dict[str, Any]) -> None:
        captions = [resolve(c["$ref"]) for c in item.get("captions", [])]
        caption = " ".join((c or {}).get("text", "") for c in captions)
        for c in item.get("captions", []):
            seen.add(c["$ref"])
        description = None
        for annotation in item.get("annotations", []) or []:
            if annotation.get("kind") == "description" and annotation.get("text"):
                description = annotation["text"]
        builder.figure(caption or None, bool(item.get("image")), description)
        builder.text("caption", caption)
        walk(item, False)

    def table(item: dict[str, Any]) -> None:
        grid = [[cell.get("text", "") for cell in row] for row in item["data"].get("grid", [])]
        builder.table(grid)
        for c in item.get("captions", []):
            captioned = resolve(c["$ref"])
            seen.add(c["$ref"])
            if captioned:
                builder.text("caption", captioned.get("text", ""))

    walk(doc["body"], False)
    out = builder.doc
    out.versions["docling_document_schema"] = str(doc.get("version", ""))
    out.source = "DoclingDocument JSON"
    return out
