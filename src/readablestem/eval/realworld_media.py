"""Inputs of the real-world evaluation set: the cut PDF of each paper, as it is stored.

An arXiv paper has no archive page to print: the input of the document task is the PDF made from
the chosen pages of the arXiv-provided PDF (`gold/arxiv_gold.cut_pdf`), which sits beside the gold
(`pdf` of every record, relative to the gold folder). Only the document task is supported.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from readablestem.eval.render import RenderError


class RealWorldMedia:
    def __init__(
        self, gold_dir: Path, cache_dir: Path, fetcher: Any = None, *, paper: str = ""
    ) -> None:
        self.gold_dir = gold_dir

    def __enter__(self) -> RealWorldMedia:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        return None

    def page_pdf(self, page_records: list[dict]) -> bytes:
        path = self.gold_dir / page_records[0]["pdf"]
        if not path.is_file():
            raise RenderError(f"PDF {page_records[0]['pdf']} is missing under {self.gold_dir}")
        return path.read_bytes()

    def equation_png(self, record: dict) -> bytes:
        raise RenderError("the real-world set has the document task only")

    def equation_text(self, record: dict) -> str | None:
        return None

    def equation_context(self, record: dict) -> str | None:
        return None

    def figure_png(self, url: str) -> bytes:
        raise RenderError("the real-world set has the document task only")
