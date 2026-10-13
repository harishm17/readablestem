"""PyMuPDF4LLM: read the Markdown `pymupdf4llm.to_markdown(path)` returns.

Markdown is this tool's only output in the plan (no JSON), so the Markdown is read with a
CommonMark parser (markdown-it-py) rather than with patterns:
- `#` headings -> headings at their level; `**` around a heading is dropped.
- Paragraphs and list items -> para / list. Raw HTML such as the `<u>` underline the tool
  writes for a fraction bar is dropped, and the text around it kept.
- The tool writes no formula recognition, so there are no equations, whatever the page held.
  `$...$` is still read if it ever appears.
- Images are not written in the plan's settings (`write_images` off): no figures.
- Pipe tables -> tables.
"""

from __future__ import annotations

from pathlib import Path

from parsers_bench.adapters.builder import Builder
from parsers_bench.intermediate import Intermediate


def adapt_file(path: Path, *, section: str) -> Intermediate:
    builder = Builder("pymupdf4llm", section)
    builder.markdown(path.read_text(encoding="utf-8"))
    doc = builder.doc
    doc.source = "to_markdown output"
    return doc
