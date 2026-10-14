"""Getting the sources: the answer key from OpenStax, and the printed PDFs.

`fetch_gold_set` rebuilds the gold set of a proof set from the OpenStax archive with the
repository's own `gold fetch` code, for the retrieval date the set pins, so the result
must have the pinned manifest hash (the archive release is fixed by the pinned date in
every record; a section OpenStax changed later in the same release would show up as a
mismatch). `reprint_check` prints the section PDFs again with headless Chromium and
compares them with the recorded ones: byte for byte they differ (Chromium stamps a
creation date, and fonts differ between machines), so the check reports page counts and
whether the text layer is the same; scoring never uses the new prints.

OpenStax content is CC BY 4.0. Every PDF carries the attribution in its footer and its
closing block (`eval.render`).
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pymupdf

from readablestem.eval import pipeline_eval as pe
from readablestem.eval.render import RenderError
from readablestem.gold import openstax
from readablestem.gold.openstax import GoldError, HttpFetcher, fetch_gold
from readablestem.proof.bundle import Bundle
from readablestem.proof.errors import ProofError
from readablestem.proof.goldinfo import section_of_record
from readablestem.proof.registry import ProofSet

FETCHED_PREFIX = "gold-fetched"


def fetch_gold_set(
    pset: ProofSet,
    out_dir: Path,
    cache_dir: Path,
    *,
    fetcher: Any = None,
    say: Callable[[str], None] = lambda _msg: None,
) -> Path:
    """Fetch the set's sections into `out_dir` (emptied first; it must be ours)."""
    if not out_dir.name.startswith(FETCHED_PREFIX):
        raise ProofError(f"refusing to empty {out_dir.name}: not a {FETCHED_PREFIX}* folder")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    by_book: dict[str, list[str]] = {}
    for section in pset.sections:
        by_book.setdefault(section.book, []).append(section.page)
    http = fetcher or HttpFetcher(cache_dir=cache_dir)
    for book, pages in by_book.items():
        say(f"fetching {len(pages)} section(s) of {book} from OpenStax")
        try:
            fetch_gold(
                book,
                len(pages),
                out_dir,
                http,
                page_slugs=pages,
                retrieved_at=pset.fetch.date,
                allow_retired=pset.fetch.allow_retired,
                **_pin_argument(pset, book),
            )
        except GoldError as exc:
            raise ProofError(f"could not fetch {book} from OpenStax: {exc}") from exc
    return out_dir


def _pin_argument(pset: ProofSet, book: str) -> dict[str, Any]:
    """`pin=` for `fetch_gold` when the set pins the book's archive release.

    The pinned fetch is a separate change to `gold.openstax` (`BookPin`); a checkout that
    does not have it cannot rebuild a pinned set, and says so instead of fetching the
    current release, which would give other bytes.
    """
    pin = pset.fetch.pins.get(book)
    if pin is None:
        return {}
    book_pin = getattr(openstax, "BookPin", None)
    if book_pin is None:
        raise ProofError(
            f"the set pins an archive release for {book}, and this checkout's gold fetch "
            "cannot read a pinned release (gold.openstax.BookPin is missing)"
        )
    return {"pin": book_pin(*pin)}


def text_fingerprint(pdf: bytes) -> str:
    """SHA-256 of the words of every page, in reading order: the text layer, not the bytes."""
    digest = hashlib.sha256()
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for page in doc:
            words = page.get_text("words")
            digest.update(" ".join(str(w[4]) for w in words).encode("utf-8"))
            digest.update(b"\f")
    return digest.hexdigest()


def reprint_check(
    pset: ProofSet,
    gold_dir: Path,
    bundle: Bundle,
    cache_dir: Path,
    *,
    fetcher: Any = None,
) -> list[dict[str, Any]]:
    """Print every section again and compare with the bundle's PDFs."""
    records = pe.load_gold(gold_dir)
    by_section: dict[str, list[dict]] = {}
    for record in records:
        by_section.setdefault(section_of_record(record), []).append(record)
    http = fetcher or HttpFetcher(cache_dir=cache_dir / "openstax")
    rows = []
    with pe.CachedMedia(gold_dir, cache_dir / "reprint", http) as media:
        for section in pset.sections:
            recorded = bundle.pdf_bytes(section.id)
            try:
                fresh = media.page_pdf(by_section[section.id])
            except RenderError as exc:
                raise ProofError(f"could not print {section.id}: {exc}") from exc
            with pymupdf.open(stream=recorded, filetype="pdf") as a:
                pages_recorded = a.page_count
            with pymupdf.open(stream=fresh, filetype="pdf") as b:
                pages_fresh = b.page_count
            rows.append(
                {
                    "section": section.id,
                    "same_bytes": fresh == recorded,
                    "pages_recorded": pages_recorded,
                    "pages_now": pages_fresh,
                    "same_text": text_fingerprint(fresh) == text_fingerprint(recorded),
                }
            )
    return rows
