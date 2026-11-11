"""Where reading-order gold comes from: fetching a document with its source, and the rules
that pick documents for a set (`gold/layout_gold.py` turns a document and its source into gold).

Papers. A paper is an arXiv record under an accepted license (`gold/arxiv_corpus.py`) with
its PDF and its source archive. The archive is unpacked with the caps of `gold/safe_tex.py`
and LaTeXML makes the HTML inside the sandbox (`source/latexml.py`), so the tool is the pinned
one and nothing depends on another site's rendering. `draw_papers` walks the candidates in the
order of SHA-256(seed:id) and takes a paper when its class family still has room, its PDF has
6 to 120 pages, LaTeXML converts it and three or more of its pages pass the page rules. Why a
paper was passed over is recorded by a code; no rule looks at what a reader makes of a page.

Nothing here opens a page of a held-out document for a person: the builders print counts.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Collection, Iterable
from pathlib import Path
from typing import Any

import httpx
import pymupdf

from readablestem.core import sandbox
from readablestem.gold import arxiv_tex, course_corpus, layout_gold, openstax, safe_tex
from readablestem.gold.openstax import USER_AGENT
from readablestem.source import latexml

PAUSE = 3.0  # seconds after each request to arXiv
MAX_DOWNLOAD = 60_000_000
MIN_PAGES, MAX_PAGES = 6, 120
MIN_GOLD_PAGES = 3  # a paper with fewer pages that pass the page rules is not taken
# A paper is larger than an upload: more time and memory than a conversion on the site gets.
PAPER_LIMITS = dataclasses.replace(
    latexml.LIMITS,
    wall_s=900,
    cpu_s=900,
    memory_bytes=4 * 1024**3,
    group_memory_bytes=5 * 1024**3,
)


class Skip(Exception):
    """A document the rules pass over, with the code that is recorded."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT})


def download(
    http: httpx.Client,
    url: str,
    target: Path,
    *,
    pause: float = PAUSE,
    limit: int = MAX_DOWNLOAD,
) -> bytes:
    """The bytes at `url`, kept at `target` so that a second call asks nobody."""
    if target.is_file():
        return target.read_bytes()
    for _ in range(4):
        response = http.get(url, timeout=180, follow_redirects=True)
        if response.status_code == 200:
            data = response.content
            if len(data) > limit:
                raise Skip("download_too_large")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            time.sleep(pause)
            return data
        if response.status_code in (429, 503):
            time.sleep(float(response.headers.get("retry-after", 30)) + 1)
            continue
        time.sleep(pause)
        raise Skip(f"http_{response.status_code}", url)
    raise Skip("http_retries", url)


def unpack(data: bytes, folder: Path) -> None:
    """A source archive (tar, gzipped or not, or one gzipped `.tex`) into the new `folder`."""
    if data[:4] == b"%PDF":
        raise Skip("source_is_pdf")
    try:
        try:
            safe_tex.extract_tar(data, folder)
        except safe_tex.UnsafeArchive as exc:
            if exc.code != "bad_archive":
                raise
            safe_tex.extract_single_file(data, folder, "main.tex")
    except safe_tex.UnsafeArchive as exc:
        raise Skip(exc.code, exc.detail) from None


# ---------------------------------------------------------------------- the class of a paper

_CLASS = re.compile(r"^[^%\n]*\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", re.M)
_FAMILIES = (
    ("revtex", re.compile(r"^revtex|^apsrev|^aip")),
    ("ieee", re.compile(r"^ieee", re.I)),
    ("ams", re.compile(r"^ams(art|proc|book)|^smfart|^gtart")),
    (
        "journal",
        re.compile(
            r"^(elsarticle|svjour|sn-jnl|aa|mnras|emulateapj|aastex|iopart|jfm|siam|imsart"
            r"|wlscirep|interact|oup|frontiers|mdpi|achemso|rsc|pnas|sagej|ws-|birkjour)",
            re.I,
        ),
    ),
    ("conference", re.compile(r"^(acmart|llncs|lipics|eptcs|jmlr|icml|neurips|aaai|ceur)", re.I)),
    ("article", re.compile(r"^(article|scrartcl|extarticle|paper|report|scrreprt|memoir)$")),
)
FAMILIES = (*(name for name, _pattern in _FAMILIES), "other")


def document_class(text: str) -> str:
    found = _CLASS.search(text)
    return found.group(1).strip() if found else ""


def class_family(name: str) -> str:
    """The family a document class is counted under, so that a set holds several layouts."""
    base = name.rsplit("/", 1)[-1]
    for family, pattern in _FAMILIES:
        if pattern.search(base):
            return family
    return "other"


# ---------------------------------------------------------------------- one paper


@dataclasses.dataclass(frozen=True)
class Source:
    """A paper's unpacked source: the folder, its main file and that file's document class."""

    folder: Path
    main: str
    document_class: str

    @property
    def family(self) -> str:
        return class_family(self.document_class)


def read_source(folder: Path) -> Source:
    sources = arxiv_tex.read_sources(folder)
    if not sources:
        raise Skip("no_tex_files")
    main = arxiv_tex.find_main(sources)
    if main is None:
        raise Skip("no_main_file")
    return Source(folder, main, document_class(sources[main]))


def source_html(source: Source, out: Path, *, limits: sandbox.Limits = PAPER_LIMITS) -> str:
    """The HTML LaTeXML makes of the source, converted inside the sandbox and kept in `out`."""
    made = out / latexml.HTML5_PATH
    if not made.is_file():
        outcome = latexml.convert(source.folder, source.main, out, limits=limits, html5=True)
        if outcome.timed_out:
            raise Skip("latexml_timed_out")
        if not outcome.ok or not made.is_file():
            raise Skip("latexml_failed", str(outcome.returncode))
    return made.read_text(encoding="utf-8", errors="replace")


def fetch_paper(arxiv_id: str, folder: Path, http: httpx.Client, *, pause: float = PAUSE) -> Source:
    """`folder/paper.pdf` and the unpacked source in `folder/src`, downloaded once."""
    download(http, f"https://arxiv.org/pdf/{arxiv_id}", folder / "paper.pdf", pause=pause)
    archive = download(
        http, f"https://arxiv.org/e-print/{arxiv_id}", folder / "e-print.bin", pause=pause
    )
    if not (folder / "src").is_dir():
        unpack(archive, folder / "src")
    return read_source(folder / "src")


def paper_id(arxiv_id: str, family: str) -> str:
    return f"ax_{family}_{re.sub(r'[^0-9a-z]', '', arxiv_id.lower())}"


def build_paper(
    record: dict[str, Any], folder: Path, source: Source, *, limits: sandbox.Limits = PAPER_LIMITS
) -> dict[str, Any]:
    """Gold for a fetched paper, or `Skip` with the rule that passed it over."""
    try:
        pdf = pymupdf.open(folder / "paper.pdf")
    except Exception as exc:
        raise Skip("unreadable_pdf", type(exc).__name__) from None
    if not MIN_PAGES <= pdf.page_count <= MAX_PAGES:
        raise Skip("pdf_page_count", str(pdf.page_count))
    html = source_html(source, folder / "latexml", limits=limits)
    gold = layout_gold.paper_gold(
        paper_id(record["id"], source.family),
        pdf,
        html,
        source=f"arXiv:{record['id']} ({record.get('licence_name', 'license not recorded')})",
        category=source.family,
    )
    kept = layout_gold.keep_pages(gold)
    if kept is None or len(kept["pages"]) < MIN_GOLD_PAGES:
        raise Skip("few_gold_pages", str(len(kept["pages"]) if kept else 0))
    kept["document_class"] = source.document_class
    return kept


# ---------------------------------------------------------------------- papers used before

_ARXIV_ID = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5})(?!\d)")
# Where the repository names the papers a set took or visited: the manifests, selection walks
# and label files of the earlier sets, the layout registries and the workflow corpus. (The
# candidate lists under data/realworld name the whole pool and are not read.)
_USED_IN = (
    ("data", ("manifest*.json", "selection*.json", "labels*.json")),
    ("data/gold/layout", ("*.json",)),
    ("tests/workflows/corpus", ("corpus.json",)),
)


def used_papers(project: Path, cache: Path | None = None) -> set[str]:
    """Every arXiv paper an earlier set took or looked at: the ids named in the repository's
    manifests, selections, label files and layout registries, and every paper folder under
    `cache` (the builders keep one folder per paper they download)."""
    used: set[str] = set()
    for folder, patterns in _USED_IN:
        for pattern in patterns:
            for path in sorted((project / folder).rglob(pattern)):
                used.update(_ARXIV_ID.findall(path.read_text(encoding="utf-8", errors="replace")))
    if cache is not None and cache.is_dir():
        used.update(p.name for p in cache.rglob("*") if p.is_dir() and _ARXIV_ID.fullmatch(p.name))
    return used


def note_passed(root: Path, group: str, sources: list[str]) -> None:
    """Keep, next to the set, the sources a walk visited and did not take: the registry
    lists them, so that a later set leaves them out as well."""
    path = root / layout_gold.PASSED_FILE
    passed = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    passed[group] = sorted(sources)
    root.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(passed, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------- drawing a set of papers


def walk_order(ids: Iterable[str], seed: str) -> list[str]:
    return sorted(ids, key=lambda i: hashlib.sha256(f"{seed}:{i}".encode()).hexdigest())


def save_document(root: Path, gold: dict[str, Any], pdf: bytes) -> None:
    (root / "files").mkdir(parents=True, exist_ok=True)
    (root / "gold").mkdir(parents=True, exist_ok=True)
    (root / "files" / f"{gold['id']}.pdf").write_bytes(pdf)
    (root / "gold" / f"{gold['id']}.json").write_text(
        json.dumps(gold, ensure_ascii=False), encoding="utf-8"
    )


def draw_papers(
    candidates: Iterable[dict[str, Any]],
    seed: str,
    root: Path,
    cache: Path,
    *,
    used: Collection[str] = (),
    per_family: int = 4,
    visits: int = 200,
    pause: float = PAUSE,
    fetch: Callable[[str, Path], Source] | None = None,
    limits: sandbox.Limits = PAPER_LIMITS,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Walk the candidates not in `used` in the seeded order and write the papers taken into
    the set folder `root`. Stops when every family is full or after `visits` papers. Returns
    the record of the walk: who was taken, who was passed over and why. The walk is kept in
    `cache/walk.json`, so a second call goes on where the first stopped."""
    pool = {c["id"]: c for c in candidates if c["id"] not in used}
    walk_file = cache / "walk.json"
    walk: dict[str, Any] = {"seed": seed, "per_family": per_family, "taken": [], "passed": []}
    if walk_file.is_file():
        walk = json.loads(walk_file.read_text(encoding="utf-8"))
        if walk["seed"] != seed or walk["per_family"] != per_family:
            raise ValueError(f"{walk_file} was made under another seed or quota")
    seen = {row["id"] for row in walk["taken"] + walk["passed"]}
    full = {family: 0 for family in FAMILIES}
    for row in walk["taken"]:
        full[row["family"]] += 1
    with client() as http:
        get = fetch or (lambda arxiv_id, folder: fetch_paper(arxiv_id, folder, http, pause=pause))
        for arxiv_id in walk_order(pool, seed):
            if all(count >= per_family for count in full.values()) or len(seen) >= visits:
                break
            if arxiv_id in seen:
                continue
            seen.add(arxiv_id)
            folder = cache / "papers" / re.sub(r"[^0-9A-Za-z.]", "_", arxiv_id)
            family = ""
            try:
                source = get(arxiv_id, folder)
                family = source.family
                if full[family] >= per_family:
                    raise Skip("family_full", family)
                gold = build_paper(pool[arxiv_id], folder, source, limits=limits)
            except Skip as skip:
                walk["passed"].append(
                    {"id": arxiv_id, "family": family, "why": skip.code, "detail": skip.detail}
                )
                log(f"pass {arxiv_id} {family} {skip.code} {skip.detail}".rstrip())
            else:
                save_document(root, gold, (folder / "paper.pdf").read_bytes())
                full[family] += 1
                walk["taken"].append(
                    {
                        "id": arxiv_id,
                        "document": gold["id"],
                        "family": family,
                        "document_class": source.document_class,
                        "pages": len(gold["pages"]),
                    }
                )
                log(f"take {arxiv_id} {family} {len(gold['pages'])} pages")
            walk_file.parent.mkdir(parents=True, exist_ok=True)
            walk_file.write_text(json.dumps(walk, indent=1), encoding="utf-8")
            note_passed(root, "papers", [f"arXiv {row['id']}" for row in walk["passed"]])
    return walk


# ---------------------------------------------------------------------- textbooks

MAX_BOOK_BYTES = 260_000_000
BOOK_LIST_URL = (
    f"{openstax.OPENSTAX}/apps/cms/api/v2/pages/?type=books.Book&limit=250"
    "&fields=title,cnx_id,book_state,license_name,license_url,high_resolution_pdf_url,book_subjects"
)
STEM_SUBJECTS = frozenset({"Science", "Math", "Computer Science"})
# The books the development sets were built on, by family (a slug without its edition), and
# the books that share their text: none of them may enter a held-out set.
DEVELOPMENT_BOOKS = frozenset(
    {
        "algebra-and-trigonometry",
        "astronomy",
        "calculus-volume-1",
        "chemistry",
        "college-physics",
        "concepts-biology",
        "contemporary-mathematics",
        "introduction-computer-science",
        "introductory-statistics",
        "organic-chemistry",
        "principles-data-science",
        "university-physics-volume-2",
        # the same text under another title
        "biology",
        "chemistry-atoms-first",
        "college-algebra",
        "college-algebra-corequisite-support",
        "introductory-business-statistics",
        "precalculus",
        "statistics",
    }
)
_EDITION = re.compile(r"-(?:\d+e|ap-courses)(?=-|$)")
_HALF = re.compile(r"^principles-(?:micro|macro)economics$")


def book_family(slug: str) -> str:
    """A book's slug without its edition, so that two editions count as one book; the two
    halves of the economics book count as that book."""
    return _HALF.sub("principles-economics", _EDITION.sub("", slug))


def book_sections(book: openstax.Book, fetcher: openstax.Fetcher) -> dict[str, Any]:
    """The sections gold is built from, picked by position alone. With N numbered chapters,
    A is chapter ceil(N/3) and B is chapter ceil(2N/3): section 2 of A and every
    end-of-chapter page of A; the opening of B and its sections 1 and 2; the book's index."""
    pages = openstax.iter_pages(book.tree)
    chapters = sorted({page.chapter for page in pages if page.chapter is not None})
    if len(chapters) < 3:
        raise Skip("few_chapters", str(len(chapters)))
    a = chapters[math.ceil(len(chapters) / 3) - 1]
    b = chapters[math.ceil(2 * len(chapters) / 3) - 1]
    wanted = []
    for page in pages:
        lead = re.match(r"^(\d+)-", page.slug)
        chapter = int(lead.group(1)) if lead else None
        opening = page.section is None and page.slug.endswith("introduction")
        if page.chapter == a and page.section == 2:
            wanted.append(("sectionA", page))
        elif page.chapter == b and page.section in (1, 2):
            wanted.append(("sectionB", page))
        elif chapter == b and opening:
            wanted.append(("opening", page))
        elif chapter == a and page.section is None and not opening:
            wanted.append(("chapter_end", page))
        elif page.slug == "index":
            wanted.append(("index", page))
    sections = [
        {
            "role": role,
            "slug": page.slug,
            "content": fetcher.get_json(book.content_url(page.uuid)).get("content", ""),
        }
        for role, page in wanted
    ]
    return {"slug": book.slug, "version": book.version, "a": a, "b": b, "sections": sections}


def download_book(url: str, target: Path, *, limit: int = MAX_BOOK_BYTES) -> Path:
    """A book's PDF, streamed to `target` once; a file over `limit` is passed over."""
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".part")
    size = 0
    with client() as http, http.stream("GET", url, timeout=600, follow_redirects=True) as response:
        if response.status_code != 200:
            raise Skip(f"http_{response.status_code}", url)
        with part.open("wb") as out:
            for chunk in response.iter_bytes(1 << 20):
                size += len(chunk)
                if size > limit:
                    break
                out.write(chunk)
    if size > limit:
        part.unlink()
        raise Skip("download_too_large")
    part.rename(target)
    return target


def book_candidates(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The books of the publisher's list a held-out set may draw from: in English, with a
    PDF, under plain CC BY, and not a development book or one that shares its text. Each is
    `{"slug", "family", "stem", "pdf"}`."""
    found = []
    for item in items:
        meta = item.get("meta") or {}
        slug, pdf = meta.get("slug"), item.get("high_resolution_pdf_url")
        if not slug or not pdf or meta.get("locale") != "en":
            continue
        if not openstax.is_cc_by(item.get("license_url")):
            continue
        if slug in DEVELOPMENT_BOOKS or book_family(slug) in DEVELOPMENT_BOOKS:
            continue
        subjects = {subject.get("subject_name") for subject in item.get("book_subjects") or []}
        found.append(
            {
                "slug": slug,
                "family": book_family(slug),
                "stem": bool(subjects & STEM_SUBJECTS),
                "pdf": pdf,
            }
        )
    return found


def build_book(
    candidate: dict[str, Any], folder: Path, fetcher: openstax.Fetcher
) -> tuple[dict[str, Any], bytes]:
    """Gold for one book and the PDF of the pages it stands on."""
    slug = candidate["slug"]
    try:
        book = openstax.resolve_book(slug, fetcher)
        openstax.check_public_license(book)
    except openstax.LicenseError:
        raise Skip("license") from None
    except openstax.GoldError as exc:
        raise Skip("not_in_archive", type(exc).__name__) from None
    sections_file = folder / "sections.json"
    if sections_file.is_file():
        meta = json.loads(sections_file.read_text(encoding="utf-8"))
    else:
        meta = book_sections(book, fetcher)
        folder.mkdir(parents=True, exist_ok=True)
        sections_file.write_text(json.dumps(meta), encoding="utf-8")
    pdf = pymupdf.open(download_book(candidate["pdf"], folder / "book.pdf"))
    gold, cut = layout_gold.textbook_gold(
        "os_" + slug.replace("-", "_"),
        pdf,
        meta["sections"],
        source=f"OpenStax {slug} {meta['version']} ({book.license_name})",
    )
    kept = layout_gold.keep_pages(gold)
    if kept is None or len(kept["pages"]) < MIN_GOLD_PAGES:
        raise Skip("few_gold_pages", str(len(kept["pages"]) if kept else 0))
    return kept, layout_gold.cut_pages(pdf, cut)


def draw_books(
    seed: str,
    root: Path,
    cache: Path,
    *,
    count: int = 8,
    fetcher: openstax.Fetcher | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Walk the candidate books (science, math and computing first, each group in the order
    of SHA-256(seed:slug)) and write the first `count` that give gold into the set folder
    `root`, one per family. Returns the record of the walk, kept in `cache/books.json`."""
    fetcher = fetcher or openstax.HttpFetcher(cache_dir=cache / "archive", delay_seconds=1.0)
    items = fetcher.get_json(BOOK_LIST_URL).get("items", [])
    candidates = {c["slug"]: c for c in book_candidates(items)}
    order = [
        slug
        for stem in (True, False)
        for slug in walk_order((s for s, c in candidates.items() if c["stem"] is stem), seed)
    ]
    walk_file = cache / "books.json"
    walk: dict[str, Any] = {"seed": seed, "taken": [], "passed": []}
    if walk_file.is_file():
        walk = json.loads(walk_file.read_text(encoding="utf-8"))
        if walk["seed"] != seed:
            raise ValueError(f"{walk_file} was made under another seed")
    seen = {row["slug"] for row in walk["taken"] + walk["passed"]}
    families = {row["family"] for row in walk["taken"]}
    for slug in order:
        if len(walk["taken"]) >= count:
            break
        if slug in seen:
            continue
        candidate = candidates[slug]
        try:
            if candidate["family"] in families:
                raise Skip("family_taken", candidate["family"])
            gold, pdf = build_book(candidate, cache / "books" / slug, fetcher)
        except Skip as skip:
            walk["passed"].append({"slug": slug, "why": skip.code, "detail": skip.detail})
            log(f"pass {slug} {skip.code} {skip.detail}".rstrip())
        else:
            save_document(root, gold, pdf)
            families.add(candidate["family"])
            walk["taken"].append(
                {
                    "slug": slug,
                    "document": gold["id"],
                    "family": candidate["family"],
                    "stem": candidate["stem"],
                    "pages": len(gold["pages"]),
                }
            )
            log(f"take {slug} {len(gold['pages'])} pages")
        walk_file.parent.mkdir(parents=True, exist_ok=True)
        walk_file.write_text(json.dumps(walk, indent=1), encoding="utf-8")
        note_passed(root, "books", [f"OpenStax {row['slug']}" for row in walk["passed"]])
    return walk


# ---------------------------------------------------------------------- course handouts

COURSE_MAX_PAGES = 200  # the pool's own cap (`course_corpus.MAX_PDF_PAGES`)
COURSE_VISITS_PER_REPO = 3  # documents tried in one repository; one is taken at most


def used_repos(project: Path) -> set[str]:
    """Every repository an earlier set took or visited: the `repo` entries of the files under
    data/realworld, lower-cased (the pool files list every repository and are not read)."""
    used: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("repo"), str):
                used.add(node["repo"].lower())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for path in sorted((project / "data" / "realworld").glob("*.json")):
        if not path.name.startswith("pool"):
            walk(json.loads(path.read_text(encoding="utf-8")))
    return used


def course_id(repo: str, path: str) -> str:
    digest = hashlib.sha256(f"{repo.lower()}:{path}".encode()).hexdigest()[:10]
    return f"gh_{digest}"


def build_course(
    repo: course_corpus.Repo,
    doc: course_corpus.Doc,
    src: Path,
    work: Path,
    *,
    limits: sandbox.Limits = PAPER_LIMITS,
) -> tuple[dict[str, Any], bytes]:
    """Gold for one document of a course repository and the PDF compiled from it, both from
    the same source: the PDF by a TeX engine and the HTML by LaTeXML, each in its sandbox."""
    try:
        pdf_path, _engine = course_corpus.compile_document(src, doc.path, work / "pdf")
    except course_corpus.Skip as skip:
        raise Skip(skip.code, skip.detail) from None
    try:
        pdf = pymupdf.open(pdf_path)
    except Exception as exc:
        raise Skip("unreadable_pdf", type(exc).__name__) from None
    if pdf.page_count > COURSE_MAX_PAGES:
        raise Skip("pdf_page_count", str(pdf.page_count))
    text = (src / doc.path).read_text(encoding="utf-8", errors="replace")
    html = source_html(Source(src, doc.path, document_class(text)), work / "latexml", limits=limits)
    gold = layout_gold.paper_gold(
        course_id(repo.full_name, doc.path),
        pdf,
        html,
        source=f"GitHub {repo.full_name} {doc.path} ({repo.licence})",
        category=class_family(document_class(text)),
        kind="course",
    )
    kept = layout_gold.keep_pages(gold)
    if kept is None:
        raise Skip("few_gold_pages", "0")
    return kept, pdf_path.read_bytes()


def draw_course(
    repos: Iterable[course_corpus.Repo],
    seed: str,
    root: Path,
    cache: Path,
    sources: Path,
    *,
    used: Collection[str] = (),
    count: int = 15,
    visits: int = 120,
    limits: sandbox.Limits = PAPER_LIMITS,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Walk the repositories not in `used` in the seeded order, and in each its documents
    that are not slide decks in the seeded order, and write the first document of a
    repository that gives gold into the set folder `root`; at most three are tried in one
    repository. `sources/<owner>__<name>/src` holds each repository as it was downloaded
    when the pool was harvested; a repository that is not there is passed over. Stops at
    `count` documents or after `visits` repositories. The walk is kept in `cache/course.json`."""
    pool = [repo for repo in repos if repo.full_name.lower() not in used]
    walk_file = cache / "course.json"
    walk: dict[str, Any] = {"seed": seed, "taken": [], "passed": []}
    if walk_file.is_file():
        walk = json.loads(walk_file.read_text(encoding="utf-8"))
        if walk["seed"] != seed:
            raise ValueError(f"{walk_file} was made under another seed")
    seen = {row["repo"] for row in walk["taken"] + walk["passed"]}
    for repo in course_corpus.order_repos(pool, seed):
        if len(walk["taken"]) >= count or len(seen) >= visits:
            break
        if repo.full_name in seen:
            continue
        seen.add(repo.full_name)
        slug = repo.full_name.replace("/", "__")
        src = sources / slug / "src"
        tried: list[dict[str, str]] = []
        taken = None
        if course_corpus.licence_gate(repo.licence) is None:
            tried.append({"path": "", "why": "license"})
        elif not src.is_dir():
            tried.append({"path": "", "why": "not_downloaded"})
        else:
            found = {d.path: d for d in course_corpus.discover(arxiv_tex.read_sources(src))}
            papers = [path for path, doc in found.items() if not doc.beamer]
            if not papers:
                tried.append({"path": "", "why": "no_documents"})
            order = course_corpus.order_documents(papers, seed, repo.full_name)
            for path in order[:COURSE_VISITS_PER_REPO]:
                work = cache / "course" / slug / course_id(repo.full_name, path)
                try:
                    gold, pdf = build_course(repo, found[path], src, work, limits=limits)
                except Skip as skip:
                    tried.append({"path": path, "why": skip.code, "detail": skip.detail})
                    continue
                save_document(root, gold, pdf)
                taken = {
                    "repo": repo.full_name,
                    "path": path,
                    "document": gold["id"],
                    "family": gold["cat"],
                    "pages": len(gold["pages"]),
                }
                break
        if taken:
            walk["taken"].append({**taken, "tried": tried})
            log(f"take {repo.full_name} {taken['pages']} pages")
        else:
            walk["passed"].append({"repo": repo.full_name, "tried": tried})
            log(f"pass {repo.full_name} {' '.join(row['why'] for row in tried)}")
        walk_file.parent.mkdir(parents=True, exist_ok=True)
        walk_file.write_text(json.dumps(walk, indent=1), encoding="utf-8")
        note_passed(root, "course", [f"GitHub {row['repo']}" for row in walk["passed"]])
    return walk
