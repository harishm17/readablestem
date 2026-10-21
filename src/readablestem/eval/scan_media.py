"""`ScannedMedia`: the document task's inputs as scans (`eval pipeline --scanned`, ADR 0007).

Wraps the run's media (`pipeline_eval.CachedMedia`, or a test's): every page PDF it prints
from the gold records is turned into the image-only PDF of a degraded scan of it
(`eval.scan.scan_pdf`), and the answer key for the prose is the page's own printable HTML,
mathematics taken out (`eval.prose_wer.gold_words`). Everything else (the equation answer
key, the headings, the figures) is the gold's and is scored by the existing document scorers,
which look at what the pipeline made of the page and never at where it sits on it.

The scans are deterministic from `seed` (`eval.scan`), so the requests a model run makes are
the same on every replay of a recorded answers file.
"""

from __future__ import annotations

from typing import Any

from readablestem.eval import prose_wer, render, scan


class ScannedMedia:
    def __init__(self, inner: Any, seed: int = 0) -> None:
        self._inner = inner
        self.seed = seed
        self.degradations: dict[str, list[scan.Degradation]] = {}

    def __enter__(self) -> ScannedMedia:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def equation_png(self, record: dict) -> bytes:
        return self._inner.equation_png(record)

    def equation_text(self, record: dict) -> str | None:
        return self._inner.equation_text(record)

    def equation_context(self, record: dict) -> str | None:
        return self._inner.equation_context(record)

    def figure_png(self, url: str) -> bytes:
        return self._inner.figure_png(url)

    def page_pdf(self, page_records: list[dict]) -> bytes:
        printed = self._inner.page_pdf(page_records)
        scanned, made = scan.scan_pdf(printed, self.seed)
        first = page_records[0]
        self.degradations[f"{first['book_slug']}/{first['page_slug']}"] = made
        return scanned

    def gold_words(self, page_records: list[dict]) -> list[str] | None:
        """The words of the page as printed, mathematics out (the prose answer key); None
        for an authored document, which has no printable page to take them from."""
        inner = self._inner
        if page_records[0].get("source") == "authored":
            return None
        book = page_records[0]["book_slug"]
        source = render.page_source_from_records(
            page_records, inner.fetcher, authors=render.book_authors(inner.gold_dir, book)
        )
        return prose_wer.gold_words(render.printable_html(source, lambda _url: ""))

    def close(self) -> None:
        self._inner.close()
