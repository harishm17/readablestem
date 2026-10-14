"""Inputs for the extended development set: OpenStax pages are printed like any gold page,
authored documents are the PDFs that were committed with their gold.

`CachedMedia` rebuilds every input from an OpenStax gold record. An authored document has
no archive page to print: its PDF (built by Tectonic, LibreOffice or Chromium, see
`data/dev-v2/authored/build_authored.py`) is the input, and its records carry the context
the page XHTML would give an OpenStax equation.
"""

from __future__ import annotations

from a11y_stem.eval.pipeline_eval import CachedMedia
from a11y_stem.eval.render import RenderError


class DevSetMedia(CachedMedia):
    def page_pdf(self, page_records: list[dict]) -> bytes:
        first = page_records[0]
        if first.get("source") != "authored":
            return super().page_pdf(page_records)
        path = self.gold_dir / first["pdf"]
        if not path.is_file():
            raise RenderError(f"authored PDF {first['pdf']} is missing under {self.gold_dir}")
        return path.read_bytes()

    def equation_context(self, record: dict) -> str | None:
        if record.get("source") == "authored":
            return record.get("context")
        return super().equation_context(record)
