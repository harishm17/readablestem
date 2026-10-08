"""Build a small gold set from OpenStax textbooks: equations, figures and tables.

OpenStax publishes expert-written MathML and alt text, so a page of an OpenStax book is
an answer key: render it, strip it, rebuild it, compare. This module only collects the
answer key; it does not render or rebuild anything.

Endpoints (checked 2026-10-06; none needs a key):

| What | URL |
|---|---|
| Books: slug, UUID, state, license | {site}/apps/cms/api/v2/pages/?type=books.Book&fields=... |
| Current archive, book default versions | {site}/rex/release.json |
| Book metadata, contents, license | {archive}/contents/{book_uuid}@{version}.json |
| One page (XHTML in "content") | {archive}/contents/{book_uuid}@{version}:{page_uuid}.json |

where {site} is https://openstax.org and {archive} is {site} + release.json["archiveUrl"]
(e.g. /apps/archive/20260604.144757).

License rule: the public gold set only takes books whose license is CC BY in BOTH the
archive book JSON and the CMS (a missing license in either place is refused). Most current
OpenStax editions are CC BY-NC-SA; `a11y-stem gold licenses` prints the full table.

State rule: only books whose CMS state is "live" are fetched by default. Retired (and
deprecated or unlisted) editions need `allow_retired=True` (`--allow-retired`), because
whether a retired edition is acceptable ground truth is an open decision
(docs/decisions/0001-scope.md, question 2). Every record carries `book_state`.
"""

from __future__ import annotations

import copy
import datetime as dt
import difflib
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin

OPENSTAX = "https://openstax.org"
RELEASE_URL = f"{OPENSTAX}/rex/release.json"
CMS_BOOKS_URL = (
    f"{OPENSTAX}/apps/cms/api/v2/pages/?type=books.Book"
    "&fields=title,cnx_id,book_state,license_name,license_url,authors&limit=250"
)
USER_AGENT = "a11y-stem-gold-fetcher/0.1 (small research sample; low request rate)"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"
CC_BY_URL = re.compile(r"^https?://creativecommons\.org/licenses/by/\d+(\.\d+)?/?$")
SECTION_SLUG = re.compile(r"^(\d+)-(\d+)-")
NOTE_TYPES = {"note", "example", "exercise", "solution", "problem", "commentary"}


class GoldError(RuntimeError):
    """Base error for gold-set fetching."""


class BookNotFoundError(GoldError):
    pass


class LicenseError(GoldError):
    pass


class BookStateError(GoldError):
    """The book is not live on OpenStax (retired, deprecated or unlisted)."""


# ----------------------------------------------------------------------------- HTTP


class Fetcher(Protocol):
    def get_json(self, url: str) -> Any: ...


class HttpFetcher:
    """Small JSON fetcher: polite delay, retries, and an on-disk cache for archive URLs.

    Only versioned archive URLs (/apps/archive/<version>/...) are cached, since they never
    change. release.json and the CMS list are always fetched fresh.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        delay_seconds: float = 0.5,
        timeout: float = 30.0,
        retries: int = 2,
    ) -> None:
        self.cache_dir = cache_dir
        self.delay_seconds = delay_seconds
        self.timeout = timeout
        self.retries = retries
        self.requests_made = 0
        self._last_request = 0.0

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None or "/apps/archive/" not in url:
            return None
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")

    def get_json(self, url: str) -> Any:
        cache_path = self._cache_path(url)
        if cache_path is not None and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            wait = self.delay_seconds - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            request = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
            try:
                self._last_request = time.monotonic()
                self.requests_made += 1
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code < 500 and exc.code != 429:
                    raise GoldError(f"GET {url} failed: HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
            time.sleep(2**attempt)
        else:
            raise GoldError(f"GET {url} failed after retries: {last_error}")

        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(body, encoding="utf-8")
        return json.loads(body)


# ---------------------------------------------------------------------------- books


@dataclass
class Book:
    slug: str
    title: str
    uuid: str
    version: str
    archive_path: str  # e.g. /apps/archive/20260604.144757
    state: str | None
    license_name: str | None  # from the archive book JSON (tied to this version)
    license_url: str | None
    cms_license_name: str | None
    cms_license_url: str | None
    authors: list[str] = field(default_factory=list)
    tree: dict = field(default_factory=dict, repr=False)

    @property
    def archive_version(self) -> str:
        return self.archive_path.rstrip("/").rsplit("/", 1)[-1]

    @property
    def details_url(self) -> str:
        return f"{OPENSTAX}/details/books/{self.slug}"

    def content_url(self, page_uuid: str | None = None) -> str:
        suffix = f":{page_uuid}" if page_uuid else ""
        return f"{OPENSTAX}{self.archive_path}/contents/{self.uuid}@{self.version}{suffix}.json"

    def page_url(self, page_slug: str) -> str:
        return f"{OPENSTAX}/books/{self.slug}/pages/{page_slug}"

    def metadata(self) -> dict:
        data = asdict(self)
        data.pop("tree")
        data["archive_version"] = self.archive_version
        data["details_url"] = self.details_url
        return data


def is_cc_by(license_url: str | None) -> bool:
    """True only for plain CC BY (any version); BY-SA, BY-NC, BY-NC-SA and so on are False."""
    return bool(license_url and CC_BY_URL.match(license_url.strip()))


def _cms_items(fetcher: Fetcher) -> list[dict]:
    return fetcher.get_json(CMS_BOOKS_URL).get("items", [])


def resolve_book(slug: str, fetcher: Fetcher) -> Book:
    """Look a book up by slug in the CMS, then load its current archive version."""
    items = _cms_items(fetcher)
    by_slug = {item["meta"]["slug"]: item for item in items if item.get("meta")}
    item = by_slug.get(slug)
    if item is None:
        close = difflib.get_close_matches(slug, list(by_slug), n=5)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise BookNotFoundError(f"No OpenStax book with slug {slug!r}.{hint}")
    uuid = item.get("cnx_id")
    release = fetcher.get_json(RELEASE_URL)
    entry = release.get("books", {}).get(uuid) if uuid else None
    if not entry or not uuid:
        raise BookNotFoundError(f"{slug!r} (uuid {uuid}) is not in the current archive release.")
    archive_path = release["archiveUrl"]
    version = entry["defaultVersion"]
    book_json = fetcher.get_json(
        f"{OPENSTAX}{archive_path}/contents/{uuid}@{version}.json",
    )
    archive_license = book_json.get("license") or {}
    authors = [
        a["value"]["name"]
        for a in item.get("authors") or []
        if a.get("value", {}).get("senior_author") and a["value"].get("name")
    ]
    return Book(
        slug=slug,
        title=book_json.get("title") or item.get("title", slug),
        uuid=uuid,
        version=version,
        archive_path=archive_path,
        state=item.get("book_state"),
        license_name=archive_license.get("name"),
        license_url=archive_license.get("url"),
        cms_license_name=item.get("license_name"),
        cms_license_url=item.get("license_url"),
        authors=authors,
        tree=book_json.get("tree", {}),
    )


def check_public_license(book: Book) -> None:
    """Raise LicenseError unless both the archive and the CMS say CC BY (None is refused)."""
    if not (is_cc_by(book.license_url) and is_cc_by(book.cms_license_url)):
        raise LicenseError(
            f"{book.slug!r} is not CC BY (archive: {book.license_name} {book.license_url}; "
            f"cms: {book.cms_license_name} {book.cms_license_url}). The public gold set "
            "only takes CC BY books. Run `a11y-stem gold licenses --cc-by-only` for options."
        )


def check_book_state(book: Book, allow_retired: bool = False) -> None:
    """Raise BookStateError for non-live books unless the caller opted in."""
    if book.state == "live" or allow_retired:
        return
    raise BookStateError(
        f"{book.slug!r} has OpenStax state {book.state!r}, not 'live'. Whether a retired "
        "edition is acceptable as ground truth is still open (docs/decisions/0001-scope.md, "
        "'Questions this raises for you', question 2). Pass --allow-retired to fetch it anyway; "
        "each record then carries book_state so it stays identifiable."
    )


def license_table(fetcher: Fetcher) -> list[dict]:
    """Every book in the CMS with its license, whether it is CC BY, and archive presence."""
    release_books = fetcher.get_json(RELEASE_URL).get("books", {})
    rows = []
    for item in _cms_items(fetcher):
        uuid = item.get("cnx_id")
        rows.append(
            {
                "slug": item["meta"]["slug"],
                "title": item.get("title"),
                "state": item.get("book_state"),
                "uuid": uuid,
                "license_name": item.get("license_name"),
                "license_url": item.get("license_url"),
                "cc_by": is_cc_by(item.get("license_url")),
                "in_archive_release": bool(uuid and uuid in release_books),
            }
        )
    return sorted(rows, key=lambda r: r["slug"])


# ---------------------------------------------------------------------------- pages


@dataclass(frozen=True)
class PageRef:
    uuid: str
    slug: str
    title: str
    chapter: int | None
    section: int | None


def _plain_title(raw: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw or "")).split())


def iter_pages(tree: dict) -> list[PageRef]:
    """Leaf nodes of the book tree, in reading order."""
    pages: list[PageRef] = []

    def walk(node: dict) -> None:
        children = node.get("contents")
        if children:
            for child in children:
                walk(child)
            return
        slug = node.get("slug", "")
        match = SECTION_SLUG.match(slug)
        pages.append(
            PageRef(
                uuid=node["id"].split("@", 1)[0],
                slug=slug,
                title=_plain_title(node.get("title", "")),
                chapter=int(match.group(1)) if match else None,
                section=int(match.group(2)) if match else None,
            )
        )

    walk(tree)
    return pages


def select_pages(
    pages: list[PageRef],
    n: int,
    chapter: int | None = None,
    slugs: Iterable[str] | None = None,
) -> list[PageRef]:
    """Explicit slugs win; otherwise the first `n` numbered sections (optionally one chapter)."""
    if slugs:
        by_slug = {p.slug: p for p in pages}
        missing = [s for s in slugs if s not in by_slug]
        if missing:
            raise GoldError(f"Unknown page slug(s): {', '.join(missing)}")
        return [by_slug[s] for s in slugs]
    numbered = [
        p for p in pages if p.section is not None and (chapter is None or p.chapter == chapter)
    ]
    return numbered[:n]


# ----------------------------------------------------------------------- extraction


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _is_footnote(elem: ET.Element) -> bool:
    return _local(elem.tag) == "aside" or elem.get("role") in {"doc-noteref", "doc-footnote"}


def _text(elem: ET.Element | None) -> str:
    """Visible text with whitespace collapsed; footnote markers and bodies are skipped."""
    if elem is None:
        return ""
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            if not _is_footnote(child):
                walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(elem)
    return " ".join("".join(parts).split())


def _classes(elem: ET.Element) -> set[str]:
    return set((elem.get("class") or "").split())


def _strip_ns_copy(elem: ET.Element) -> ET.Element:
    clone = copy.deepcopy(elem)
    for node in clone.iter():
        node.tag = _local(node.tag)
        for key in list(node.attrib):
            if key.startswith("{"):
                del node.attrib[key]
    return clone


def _serialize_math(elem: ET.Element) -> str:
    clone = _strip_ns_copy(elem)
    clone.set("xmlns", MATHML_NS)
    clone.tail = None
    return ET.tostring(clone, encoding="unicode")


def _serialize_html(elem: ET.Element) -> str:
    clone = _strip_ns_copy(elem)
    clone.tail = None
    return ET.tostring(clone, encoding="unicode")


def _find_first(elem: ET.Element, local: str, cls: str | None = None) -> ET.Element | None:
    for node in elem.iter():
        if _local(node.tag) == local and (cls is None or cls in _classes(node)):
            return node
    return None


def _caption_parts(target: ET.Element, parents: dict) -> dict:
    """Caption from <figcaption>/<caption>, or from OpenStax's baked os-caption-container."""
    for child in target:
        if _local(child.tag) in {"figcaption", "caption"}:
            return {"caption": _text(child), "number": None, "title": None}
    parent = parents.get(target)
    if parent is None:
        return {"caption": None, "number": None, "title": None}
    container = next(
        (c for c in parent if "os-caption-container" in _classes(c)),
        None,
    )
    if container is None:
        return {"caption": None, "number": None, "title": None}
    caption = _find_first(container, "span", "os-caption")
    number = _find_first(container, "span", "os-number")
    title = _find_first(container, "span", "os-title")
    return {
        "caption": _text(caption) or None,
        "number": _text(number) or None,
        "title": _text(title) or None,
    }


def _context(elem: ET.Element, parents: dict) -> str | None:
    """Nearest enclosing note/example/exercise, e.g. 'worked-example' or 'os-teacher'."""
    node = parents.get(elem)
    while node is not None:
        data_type = node.get("data-type")
        if data_type in NOTE_TYPES:
            classes = [c for c in (node.get("class") or "").split() if c]
            return classes[0] if classes else data_type
        node = parents.get(node)
    return None


def _ancestor_with(elem: ET.Element, parents: dict, data_type: str) -> ET.Element | None:
    node = parents.get(elem)
    while node is not None:
        if node.get("data-type") == data_type:
            return node
        node = parents.get(node)
    return None


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def extract_elements(xhtml: str, content_url: str) -> list[dict]:
    """Pull equations, figures and tables out of one page's XHTML (no network)."""
    root = ET.fromstring(xhtml)
    parents = {child: parent for parent in root.iter() for child in parent}
    records: list[dict] = []

    maths = [e for e in root.iter() if _local(e.tag) == "math"]
    for index, math in enumerate(maths):
        container = _ancestor_with(math, parents, "equation")
        records.append(
            {
                "kind": "equation",
                "index_on_page": index,
                "element_id": container.get("id") if container is not None else None,
                "display": math.get("display") or ("block" if container is not None else "inline"),
                "context": _context(math, parents),
                "mathml": _serialize_math(math),
            }
        )

    figures = [e for e in root.iter() if _local(e.tag) == "figure"]
    for index, figure in enumerate(figures):
        images = []
        for img in figure.iter():
            if _local(img.tag) != "img":
                continue
            src = img.get("src")
            alt = img.get("alt")
            images.append(
                {
                    "url": urljoin(content_url, src) if src else None,
                    "media_type": img.get("data-media-type"),
                    "alt": alt,
                    "alt_is_empty": not (alt or "").strip(),
                    "width": _int_or_none(img.get("width")),
                    "height": _int_or_none(img.get("height")),
                }
            )
        caption = _caption_parts(figure, parents)
        records.append(
            {
                "kind": "figure",
                "index_on_page": index,
                "element_id": figure.get("id"),
                "context": _context(figure, parents),
                "figure_number": caption["number"],
                "caption": caption["caption"],
                "caption_has_math": bool(
                    caption["caption"] and any(_local(e.tag) == "math" for e in figure.iter())
                ),
                "images": images,
            }
        )

    tables = [e for e in root.iter() if _local(e.tag) == "table"]
    for index, table in enumerate(tables):
        rows = [e for e in table.iter() if _local(e.tag) == "tr"]
        header_cells = []
        n_cols = 0
        for r, row in enumerate(rows):
            col = 0
            for cell in row:
                if _local(cell.tag) not in {"th", "td"}:
                    continue
                if _local(cell.tag) == "th":
                    header_cells.append(
                        {"row": r, "col": col, "scope": cell.get("scope"), "text": _text(cell)}
                    )
                col += _int_or_none(cell.get("colspan")) or 1
            n_cols = max(n_cols, col)
        caption = _caption_parts(table, parents)
        role = table.get("role")
        records.append(
            {
                "kind": "table",
                "index_on_page": index,
                "element_id": table.get("id"),
                "context": _context(table, parents),
                "role": role,
                "is_layout": role == "presentation",
                "summary": table.get("data-summary") or table.get("summary"),
                "table_number": caption["number"],
                "caption": caption["title"] or caption["caption"],
                "has_thead": any(_local(e.tag) == "thead" for e in table),
                "header_cells": header_cells,
                "n_rows": len(rows),
                "n_cols": n_cols,
                "html": _serialize_html(table),
            }
        )
    return records


# ------------------------------------------------------------------------- writing


def _record_id(book: Book, page: PageRef, record: dict) -> str:
    prefix = {"equation": "eq", "figure": "fig", "table": "tab"}[record["kind"]]
    return f"openstax/{book.slug}/{page.slug}/{prefix}-{record['index_on_page']:03d}"


def build_page_records(book: Book, page: PageRef, xhtml: str, retrieved_at: str) -> list[dict]:
    content_url = book.content_url(page.uuid)
    page_url = book.page_url(page.slug)
    out = []
    for record in extract_elements(xhtml, content_url):
        element_id = record.get("element_id")
        out.append(
            {
                "id": _record_id(book, page, record),
                "source": "openstax",
                "book_slug": book.slug,
                "book_title": book.title,
                "book_uuid": book.uuid,
                "book_version": book.version,
                "archive_version": book.archive_version,
                "page_slug": page.slug,
                "page_title": page.title,
                "page_uuid": page.uuid,
                "source_url": f"{page_url}#{element_id}" if element_id else page_url,
                "content_url": content_url,
                "license_name": book.license_name,
                "license_url": book.license_url,
                "book_state": book.state,
                "retrieved_at": retrieved_at,
                **record,
            }
        )
    return out


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


@dataclass
class PageResult:
    page: PageRef
    path: Path
    counts: dict[str, int]


def fetch_gold(
    book_slug: str,
    n_pages: int,
    out_dir: Path,
    fetcher: Fetcher,
    chapter: int | None = None,
    page_slugs: list[str] | None = None,
    retrieved_at: str | None = None,
    allow_retired: bool = False,
) -> tuple[Book, list[PageResult]]:
    """Fetch pages of one live CC BY book and write one JSONL file per page plus metadata."""
    if n_pages < 1:
        raise GoldError(f"n_pages must be at least 1 (got {n_pages}).")
    retrieved_at = retrieved_at or dt.date.today().isoformat()
    book = resolve_book(book_slug, fetcher)
    check_public_license(book)
    check_book_state(book, allow_retired=allow_retired)
    pages = select_pages(iter_pages(book.tree), n_pages, chapter=chapter, slugs=page_slugs)
    if not pages:
        raise GoldError("No pages matched. Check --chapter, --pages or --page.")

    book_dir = out_dir / "openstax" / book.slug
    book_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for page in pages:
        payload = fetcher.get_json(book.content_url(page.uuid))
        records = build_page_records(book, page, payload["content"], retrieved_at)
        path = book_dir / f"{page.slug}.jsonl"
        _write_jsonl(path, records)
        results.append(PageResult(page, path, dict(Counter(r["kind"] for r in records))))

    meta = book.metadata() | {"retrieved_at": retrieved_at}
    (book_dir / "_book.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_manifest(out_dir)
    return book, results


# ------------------------------------------------------------------------ manifest


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(gold_dir: Path) -> dict:
    books = {}
    for meta_path in sorted(gold_dir.glob("openstax/*/_book.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        books[meta["slug"]] = meta
    files = []
    totals: Counter[str] = Counter()
    for path in sorted(gold_dir.glob("openstax/*/*.jsonl")):
        counts: Counter[str] = Counter()
        retrieved = set()
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                record = json.loads(line)
                counts[record["kind"]] += 1
                retrieved.add(record["retrieved_at"])
        totals.update(counts)
        files.append(
            {
                "path": path.relative_to(gold_dir).as_posix(),
                "sha256": _sha256(path),
                "records": sum(counts.values()),
                "counts": dict(sorted(counts.items())),
                "retrieved_at": sorted(retrieved),
            }
        )
    return {
        "schema_version": 1,
        "generated_by": "a11y-stem gold fetch",
        "source": "OpenStax (https://openstax.org), CC BY books only",
        "books": books,
        "files": files,
        "totals": dict(sorted(totals.items())),
    }


def write_manifest(gold_dir: Path) -> dict:
    manifest = build_manifest(gold_dir)
    (gold_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (gold_dir / "ATTRIBUTION.md").write_text(render_attribution(manifest), encoding="utf-8")
    return manifest


def render_attribution(manifest: dict) -> str:
    lines = [
        "# Attribution for data/gold",
        "",
        "Generated by `a11y-stem gold fetch` from manifest.json. Do not edit by hand.",
        "",
        "The JSONL files contain equations (MathML), figure alt text and captions, and tables",
        "extracted from the OpenStax web editions listed below. Changes: elements were",
        "extracted and restructured as JSON records; no wording was edited.",
        "",
        "| Book | Authors (senior) | Version | License | Source |",
        "|---|---|---|---|---|",
    ]
    for meta in manifest["books"].values():
        authors = ", ".join(meta.get("authors") or []) or "see source"
        lines.append(
            f"| {meta['title']} | {authors} | {meta['version']} "
            f"(archive {meta['archive_version']}) | [{meta['license_name']}]"
            f"({meta['license_url']}) | {meta['details_url']} |"
        )
    lines += ["", "a11y-stem is not affiliated with or endorsed by OpenStax.", ""]
    return "\n".join(lines)
