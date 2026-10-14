"""The extended development set (dev-v2): classes the first development set cannot see.

Tier 2 accuracy work (IMPROVEMENTS.md, T2-0) needs a development set that contains the
failures the held-out run found: inline stacked fractions, systems and determinants cut
into rows, equations pasted as pictures, chemistry and nuclide notation, flattened
sub- and superscripts, large slide formulas, repeated headings and so on. The original
development set is 30 physics equations at the scorer's ceiling and shows none of them.

The set is built from two kinds of material, both licensed for publication:

- **OpenStax sections** (CC BY 4.0, retired first editions allowed, see
  proof/DECISION-retired-editions-2026-10-13.md), with the answer key taken from the
  published MathML, headings, tables, glossaries and the page's own sub- and superscripts.
- **Authored documents** (MIT, `data/dev-v2/authored/build_authored.py`): LaTeX built by
  Tectonic, Word documents exported by LibreOffice and HTML printed by Chromium, with the
  answer key known by construction.

Two rules keep it away from the held-out set. No section of a book that has a held-out
section is used, and every chosen section is checked against the registered 15 sections:
the share of its word 8-grams (visible text, MathML removed, the same measure as
proof/selection/features.py) found in the held-out text must stay below `NEAR_DUP`.

The set has two parts. `dev` is for building. `check` is small, is stored only as a
sealed archive whose SHA-256 is recorded in `SEAL.json`, and is run only for a quick
sanity check before a merge: it is never read or printed item by item.

Nothing here calls a model or reads the clock; dates are passed in.
"""

from __future__ import annotations

import gzip
import hashlib
import html
import io
import json
import math
import re
import tarfile
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from a11y_stem.gold import devset_classify as dc
from a11y_stem.gold import heldout as heldout_registry
from a11y_stem.gold import openstax as ox

DEVSET = "dev-v2"
DATA_DIR = Path("data") / "dev-v2"
SELECTION_FILE = "selection.json"
SEAL_FILE = "SEAL.json"
CHECK_ARCHIVE = "check.sealed.tar.gz"
PARTS = ("dev", "check")
NEAR_DUP = 0.05  # same threshold as proof/selection/features.py
MIN_WORDS = 300
# The pipeline refuses a PDF of more pages than this (config.Settings.max_pages_per_job).
MAX_SECTION_PAGES = 30
CHECK_SHARE = 0.15  # share of the candidate pool whose hash puts it in the check part
LICENSE_AUTHORED = {
    "license_name": "MIT License",
    "license_url": "https://opensource.org/licenses/MIT",
}


class DevsetError(RuntimeError):
    """The extended development set on disk is not what its manifest says."""


# ----------------------------------------------------------------------- hashing


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def hash_fraction(salt: str, key: str) -> float:
    """A number in [0, 1) from the SHA-256 of "salt:key": the same everywhere, no RNG."""
    digest = hashlib.sha256(f"{salt}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def part_of(salt: str, book: str, slug: str) -> str:
    """Which part of the set an OpenStax section belongs to, by hash alone."""
    return "check" if hash_fraction(salt, f"{book}/{slug}") < CHECK_SHARE else "dev"


# ------------------------------------------------------------------ text overlap


def words_outside_math(xhtml: str) -> list[str]:
    """Visible words of the page body: head, style, script and MathML removed (the measure
    of proof/selection/features.py)."""
    body = re.sub(r"<head\b.*?</head>", " ", xhtml, flags=re.S | re.I)
    body = re.sub(r"<(style|script)\b.*?</\1>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<math\b.*?</math>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    return re.findall(r"[a-z0-9]+", html.unescape(body).lower())


def ngrams(words: Sequence[str], n: int = 8) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def containment(words: Sequence[str], reference: set[tuple[str, ...]]) -> float:
    """Share of the page's word 8-grams that occur in `reference`."""
    grams = ngrams(words)
    return len(grams & reference) / len(grams) if grams else 0.0


def heldout_grams(fetcher: ox.Fetcher) -> set[tuple[str, ...]]:
    """Word 8-grams of the 15 registered held-out sections. `fetcher` must cache outside
    the repository (the held-out folder, `cli.heldout`); it reads, hashes and discards."""
    reg = heldout_registry.REGISTERED
    grams: set[tuple[str, ...]] = set()
    for book_slug, slugs in reg.pages.items():
        book = ox.resolve_book(book_slug, fetcher, pin=reg.pins[book_slug])
        pages = {p.slug: p for p in ox.iter_pages(book.tree)}
        for slug in slugs:
            xhtml = fetcher.get_json(book.content_url(pages[slug].uuid))["content"]
            grams |= ngrams(words_outside_math(xhtml))
    return grams


def heldout_books() -> frozenset[str]:
    return frozenset(s.book for s in heldout_registry.REGISTERED.sections)


def is_heldout(book: str, slug: str) -> bool:
    return heldout_registry.REGISTERED.section(slug, book) is not None


# ------------------------------------------------------------------- page records


def _local(tag: Any) -> str:
    return dc._local(tag)


def _record_id(book: str, slug: str, prefix: str, index: int) -> str:
    return f"openstax/{book}/{slug}/{prefix}-{index:03d}"


def build_page(book: ox.Book, page: ox.PageRef, xhtml: str, retrieved_at: str) -> list[dict]:
    """Every gold record of one OpenStax page: the four published kinds with their class
    ids, then prose sub/superscript runs, code blocks and glossary entries."""
    base = ox.build_page_records(book, page, xhtml, retrieved_at)
    root = ET.fromstring(xhtml)
    parents = {child: parent for parent in root.iter() for child in parent}
    maths = [e for e in root.iter() if _local(e.tag) == "math"]
    equations = [r for r in base if r["kind"] == "equation"]
    if len(maths) != len(equations):  # build_page_records and this walk must agree
        raise DevsetError(f"{page.slug}: {len(maths)} math elements, {len(equations)} records")
    headings = [r for r in base if r["kind"] == "heading"]
    heading_texts = [h["text"] for h in headings]
    out: list[dict] = []
    for record in base:
        classes: list[str] = []
        if record["kind"] == "equation":
            number = dc.opening_exercise_number(maths[record["index_on_page"]], parents)
            classes = dc.classify_equation(
                record["mathml"], display=record["display"], exercise_number=number
            )
            record = {**record, "exercise_number": number, "appearance": "text"}
        elif record["kind"] == "heading":
            classes = dc.heading_classes(record["text"], heading_texts)
        elif record["kind"] == "table":
            classes = ["table_layout" if record["is_layout"] else "table_data"]
        out.append({**record, "classes": classes})

    template = _page_template(book, page, retrieved_at)
    page_url = book.page_url(page.slug)
    extras: list[tuple[str, dict]] = []
    for entry in dc.prose_script_records(root):
        classes = sorted({run["class"] for run in entry["runs"]})
        extras.append(("script", {**entry, "classes": classes}))
    for entry in dc.code_records(root):
        extras.append(("code", {**entry, "classes": ["code_block"]}))
    for entry in dc.glossary_records(root):
        extras.append(("glossary", {**entry, "classes": ["glossary_entry"]}))
    counters: Counter[str] = Counter()
    prefixes = {"script": "sc", "code": "cd", "glossary": "gl"}
    for kind, entry in extras:
        index = counters[kind]
        counters[kind] += 1
        element_id = entry.pop("element_id", None)
        out.append(
            {
                "id": _record_id(book.slug, page.slug, prefixes[kind], index),
                **template,
                "source_url": f"{page_url}#{element_id}" if element_id else page_url,
                "kind": kind,
                "index_on_page": index,
                "element_id": element_id,
                **entry,
            }
        )
    return out


def _page_template(book: ox.Book, page: ox.PageRef, retrieved_at: str) -> dict:
    return {
        "source": "openstax",
        "book_slug": book.slug,
        "book_title": book.title,
        "book_uuid": book.uuid,
        "book_version": book.version,
        "archive_version": book.archive_version,
        "page_slug": page.slug,
        "page_title": page.title,
        "page_uuid": page.uuid,
        "content_url": book.content_url(page.uuid),
        "license_name": book.license_name,
        "license_url": book.license_url,
        "book_state": book.state,
        "retrieved_at": retrieved_at,
    }


def count_items(records: Iterable[Mapping]) -> Counter[str]:
    """Items per class. A script record counts each of its runs; every other record counts
    once per class it carries."""
    counts: Counter[str] = Counter()
    for record in records:
        if record.get("kind") == "script":
            counts.update(run["class"] for run in record.get("runs", []))
        else:
            counts.update(record.get("classes") or [])
    return counts


# ------------------------------------------------------------- candidate selection


@dataclass
class Candidate:
    book: str
    slug: str
    title: str
    words: int
    equations: int
    figures: int
    tables: int
    overlap: float
    counts: dict[str, int]
    xhtml_grams: set[tuple[str, ...]] = field(default_factory=set, repr=False)
    pdf_pages: int | None = None  # Letter pages of the printed section, once counted

    @property
    def key(self) -> str:
        return f"{self.book}/{self.slug}"

    @property
    def pages_estimate(self) -> int:
        """Letter pages when printed: the count once it has been made, else a rough guess
        (320 words a page, equations and figures add; display-heavy exercise pages run
        longer, which is why the choice counts the real print of what it takes)."""
        if self.pdf_pages is not None:
            return self.pdf_pages
        return max(2, math.ceil(self.words / 320 + 0.05 * self.equations + 0.4 * self.figures))


def candidate_from_page(
    book: ox.Book, page: ox.PageRef, xhtml: str, reference: set[tuple[str, ...]]
) -> Candidate | None:
    """Features of one page for the choice, or None when it can never qualify."""
    words = words_outside_math(xhtml)
    if len(words) < MIN_WORDS:
        return None
    records = build_page(book, page, xhtml, "2026-10-14")
    counts = count_items(records)
    kinds = Counter(r["kind"] for r in records)
    return Candidate(
        book=book.slug,
        slug=page.slug,
        title=page.title,
        words=len(words),
        equations=kinds["equation"],
        figures=kinds["figure"],
        tables=kinds["table"],
        overlap=round(containment(words, reference), 4),
        counts=dict(counts),
        xhtml_grams=ngrams(words),
    )


def greedy_choose(
    pool: Sequence[Candidate],
    targets: Mapping[str, int],
    *,
    max_sections: int,
    max_pages: int,
    max_pair_overlap: float = 0.2,
    page_counter: Callable[[Candidate], int] | None = None,
    max_section_pages: int = MAX_SECTION_PAGES,
) -> list[Candidate]:
    """Sections that reach the class targets in the fewest printed pages.

    Each round takes the candidate with the most still-needed items per estimated page
    (ties by key), skipping any whose text overlaps an already chosen section by more than
    `max_pair_overlap` (sibling books repeat each other). With a `page_counter` the
    winner is printed first and dropped when its print is longer than `max_section_pages`
    (the pipeline refuses more pages than that). Stops when every target is met, or at the
    limits.
    """
    chosen: list[Candidate] = []
    need = dict(targets)
    pages = 0
    remaining = sorted(pool, key=lambda c: c.key)
    while remaining and len(chosen) < max_sections and any(v > 0 for v in need.values()):
        best: Candidate | None = None
        best_score = 0.0
        for cand in remaining:
            if pages + cand.pages_estimate > max_pages:
                continue
            gain = sum(min(cand.counts.get(c, 0), n) / targets[c] for c, n in need.items() if n > 0)
            score = gain / cand.pages_estimate
            if score > best_score:
                best, best_score = cand, score
        if best is None:
            break
        if page_counter is not None and best.pdf_pages is None:
            best.pdf_pages = page_counter(best)
            if best.pdf_pages > max_section_pages or pages + best.pdf_pages > max_pages:
                remaining = [c for c in remaining if c is not best]
                continue
        chosen.append(best)
        pages += best.pages_estimate
        for c in need:
            need[c] = max(0, need[c] - best.counts.get(c, 0))
        remaining = [
            c
            for c in remaining
            if c is not best
            and len(best.xhtml_grams & c.xhtml_grams) / max(len(c.xhtml_grams), 1)
            <= max_pair_overlap
        ]
    return chosen


# ----------------------------------------------------------------------- archives


def make_tar_gz(files: Mapping[str, bytes]) -> bytes:
    """A reproducible .tar.gz: sorted names, fixed owner and time, gzip header time 0."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size = len(files[name])
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(files[name]))
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def read_tar_gz(data: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = member.name
            if name.startswith(("/", "..")) or ".." in Path(name).parts:
                raise DevsetError(f"unsafe path in archive: {name}")
            handle = tar.extractfile(member)
            if handle is not None:
                files[name] = handle.read()
    return files


# ---------------------------------------------------------------------- selection

# Books that candidate sections may come from. Every book with a held-out section is out,
# whole (`heldout_books`), so even the sections the registration did not draw are not used;
# books that only share a text lineage with them (Algebra and Trigonometry and College
# Algebra with Precalculus, Chemistry: Atoms First with Chemistry, the high-school Physics
# with College Physics) stay in, and every section is checked by text overlap.
CANDIDATE_BOOKS = (
    "algebra-and-trigonometry",
    "college-algebra",
    "elementary-algebra",
    "prealgebra",
    "chemistry-atoms-first",
    "physics",
)
# Sections the first development set and the earlier research already used (the scan in
# proof/PREREGISTRATION.md section 2): the extended set is meant to show new material.
EARLIER_USE = frozenset(
    {("algebra-and-trigonometry", "13-6-binomial-theorem")}
    | {
        ("physics", slug)
        for slug in (
            "1-1-physics-definitions-and-applications",
            "1-3-the-language-of-physics-physical-quantities-and-units",
            "2-1-relative-motion-distance-and-displacement",
            "2-2-speed-and-velocity",
            "2-3-position-vs-time-graphs",
            "2-4-velocity-vs-time-graphs",
            "3-1-acceleration",
            "3-2-representing-acceleration-with-equations-and-graphs",
            "4-1-force",
            "4-2-newtons-first-law-of-motion-inertia",
            "4-3-newtons-second-law-of-motion",
            "4-4-newtons-third-law-of-motion",
            "5-1-vector-addition-and-subtraction-graphical-methods",
            "5-2-vector-addition-and-subtraction-analytical-methods",
            "5-3-projectile-motion",
            "5-4-inclined-planes",
            "5-5-simple-harmonic-motion",
            "6-3-rotational-motion",
            "11-3-phase-change-and-latent-heat",
            "14-1-speed-of-sound-frequency-and-wavelength",
            "16-3-lenses",
            "22-2-nuclear-forces-and-radioactivity",
            "23-1-the-four-fundamental-forces",
            "23-2-quarks",
        )
    }
)


@dataclass(frozen=True)
class SelectionConfig:
    """The rules a selection was made under (recorded in selection.json)."""

    salt: str
    archive_version: str
    books: dict[str, str]  # book slug -> pinned version
    dev_targets: dict[str, int]
    check_targets: dict[str, int]
    max_sections: int = 22
    max_pages: int = 330


def candidate_pool(
    config: SelectionConfig,
    fetcher: ox.Fetcher,
    reference: set[tuple[str, ...]],
) -> list[Candidate]:
    """Every section that may be chosen: numbered, not held-out, not used before, in a
    candidate book, long enough, and below `NEAR_DUP` against the held-out text.

    Reads each page from `fetcher` (the repository's .cache/openstax); a held-out section
    is never requested (its page is skipped before the fetch).
    """
    pool: list[Candidate] = []
    for slug in CANDIDATE_BOOKS:
        if slug in heldout_books():
            continue
        book = ox.resolve_book(
            slug, fetcher, pin=ox.BookPin(config.archive_version, config.books[slug])
        )
        ox.check_public_license(book)
        for page in ox.iter_pages(book.tree):
            if page.section is None or is_heldout(slug, page.slug):
                continue
            if (slug, page.slug) in EARLIER_USE:
                continue
            xhtml = fetcher.get_json(book.content_url(page.uuid))["content"]
            cand = candidate_from_page(book, page, xhtml, reference)
            if cand is not None and cand.overlap < NEAR_DUP:
                pool.append(cand)
    return pool


def split_pool(config: SelectionConfig, pool: Sequence[Candidate]) -> dict[str, list[Candidate]]:
    parts: dict[str, list[Candidate]] = {"dev": [], "check": []}
    for cand in pool:
        parts[part_of(config.salt, cand.book, cand.slug)].append(cand)
    return parts


def choose_sections(
    config: SelectionConfig,
    pool: Sequence[Candidate],
    page_counter: Callable[[Candidate], int] | None = None,
) -> dict[str, list[Candidate]]:
    """The dev and check sections: the greedy choice on each part's own candidates."""
    parts = split_pool(config, pool)
    return {
        "dev": greedy_choose(
            parts["dev"],
            config.dev_targets,
            max_sections=config.max_sections,
            max_pages=config.max_pages,
            page_counter=page_counter,
        ),
        "check": greedy_choose(
            parts["check"],
            config.check_targets,
            max_sections=max(3, config.max_sections // 4),
            max_pages=config.max_pages // 4,
            page_counter=page_counter,
        ),
    }


# --------------------------------------------------------------------- part files


def jsonl_bytes(records: Iterable[Mapping]) -> bytes:
    return b"".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n" for r in records
    )


def parse_jsonl(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def openstax_files(
    pages: Mapping[tuple[str, str], list[dict]], books: Mapping[str, ox.Book], retrieved_at: str
) -> dict[str, bytes]:
    """Part files for OpenStax pages: one JSONL per page, one _book.json per book."""
    files: dict[str, bytes] = {}
    used = sorted({book for book, _ in pages})
    for (book, slug), records in sorted(pages.items()):
        files[f"openstax/{book}/{slug}.jsonl"] = jsonl_bytes(records)
    for book in used:
        meta = books[book].metadata() | {"retrieved_at": retrieved_at}
        files[f"openstax/{book}/_book.json"] = (
            json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    return files


def authored_files(pool_dir: Path, variant: str) -> dict[str, bytes]:
    """Part files for the authored documents of one variant ("dev" or "check") found in
    `pool_dir` (the builder's output: one folder per document with gold.jsonl), and the
    index the tables task needs for the documents that have tables."""
    files: dict[str, bytes] = {}
    with_tables: list[dict] = []
    for doc in sorted(p for p in pool_dir.iterdir() if p.is_dir()):
        gold = doc / "gold.jsonl"
        if not gold.is_file():
            continue
        records = parse_jsonl(gold.read_bytes())
        if not records:
            continue
        variants = {r.get("variant") for r in records}
        if variants != {variant}:
            continue
        for path in sorted(p for p in doc.rglob("*") if p.is_file()):
            files[f"authored/{doc.name}/{path.relative_to(doc).as_posix()}"] = path.read_bytes()
        if any(r["kind"] == "table" for r in records):
            pdf = records[0]["pdf"].removeprefix("authored/")
            with_tables.append(
                {
                    "id": doc.name,
                    "pdf": pdf,
                    "gold": f"{doc.name}/gold.jsonl",
                    "toolchain": records[0]["toolchain"],
                }
            )
    if with_tables:
        # The tables task reads authored documents through an index (eval.tables).
        index = {"schema_version": 1, "documents": with_tables}
        files["authored/index.json"] = (json.dumps(index, indent=2) + "\n").encode("utf-8")
    return files


def part_records(files: Mapping[str, bytes]) -> list[dict]:
    """Every gold record of a part, in path order."""
    out: list[dict] = []
    for path in sorted(files):
        if path.endswith(".jsonl"):
            out.extend(parse_jsonl(files[path]))
    return out


def part_manifest(files: Mapping[str, bytes], *, part: str, date: str) -> dict:
    """manifest.json of a part: every file with its hash, the records and the class counts."""
    entries = []
    totals: Counter[str] = Counter()
    by_source: dict[str, Counter[str]] = {"openstax": Counter(), "authored": Counter()}
    books: dict[str, dict] = {}
    docs: dict[str, dict] = {}
    for path in sorted(files):
        data = files[path]
        entry: dict[str, Any] = {"path": path, "sha256": sha256_bytes(data), "bytes": len(data)}
        if path.endswith(".jsonl"):
            records = parse_jsonl(data)
            kinds = Counter(r["kind"] for r in records)
            entry["records"] = len(records)
            entry["counts"] = dict(sorted(kinds.items()))
            totals.update(kinds)
            source = records[0]["source"] if records else "openstax"
            by_source[source].update(count_items(records))
            if source == "authored" and records:
                docs[records[0]["page_slug"]] = {
                    "id": records[0]["page_slug"],
                    "title": records[0]["page_title"],
                    "toolchain": records[0]["toolchain"],
                    "variant": records[0]["variant"],
                    "pdf": records[0]["pdf"],
                }
        if path.endswith("_book.json"):
            meta = json.loads(data)
            books[meta["slug"]] = meta
        entries.append(entry)
    return {
        "schema_version": 1,
        "set": DEVSET,
        "part": part,
        "generated_by": "a11y-stem gold build --set dev-v2",
        "date": date,
        "files": entries,
        "books": dict(sorted(books.items())),
        "authored_documents": [docs[k] for k in sorted(docs)],
        "totals": dict(sorted(totals.items())),
        "class_counts": {
            "openstax": {c: by_source["openstax"][c] for c in dc.CLASS_IDS},
            "authored": {c: by_source["authored"][c] for c in dc.CLASS_IDS},
        },
    }


def manifest_json(manifest: Mapping) -> bytes:
    return (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def total_class_counts(manifest: Mapping) -> dict[str, int]:
    counts = manifest["class_counts"]
    return {c: counts["openstax"].get(c, 0) + counts["authored"].get(c, 0) for c in dc.CLASS_IDS}


def attribution_text(manifest: Mapping) -> str:
    """ATTRIBUTION.md for the dev part: OpenStax books (CC BY 4.0) and authored documents (MIT)."""
    lines = [
        "# Attribution for data/dev-v2",
        "",
        "Generated by `a11y-stem gold build --set dev-v2`. Do not edit by hand.",
        "",
        "## OpenStax material (CC BY 4.0)",
        "",
        "The JSONL files under `dev/openstax/` hold equations (MathML), figure captions and alt",
        "text, tables, headings, glossary entries, code blocks and sub- and superscript runs",
        "extracted from the OpenStax web editions below. Changes: elements were extracted and",
        "restructured as JSON records, with class labels added; no wording was edited. The",
        "books are retired first editions or live editions, all CC BY 4.0 in both the archive",
        "and the OpenStax catalogue (decision of 2026-10-13 for retired editions).",
        "",
        "| Book | Authors (senior) | Version | State | License | Source |",
        "|---|---|---|---|---|---|",
    ]
    for meta in manifest["books"].values():
        authors = ", ".join(meta.get("authors") or []) or "see source"
        lines.append(
            f"| {meta['title']} | {authors} "
            f"| {meta['version']} (archive {meta['archive_version']}) "
            f"| {meta.get('state')} | [{meta['license_name']}]({meta['license_url']}) "
            f"| {meta['details_url']} |"
        )
    lines += [
        "",
        "## Authored documents (MIT)",
        "",
        "Written for a11y-stem by its contributors; no third-party text. Licence: MIT, like",
        "the code (`LICENSE`). The sources, PDFs and gold are under `dev/authored/`.",
        "",
        "| Document | Toolchain | Variant |",
        "|---|---|---|",
    ]
    lines += [
        f"| {d['id']} | {d['toolchain']} | {d['variant']} |" for d in manifest["authored_documents"]
    ]
    lines += ["", "a11y-stem is not affiliated with or endorsed by OpenStax.", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------------- summary

_GROUP_ORDER = {"maths": 0, "image": 1, "notation": 2, "prose": 3, "structure": 4}


def summary_rows(
    dev: Mapping, check_counts: Mapping[str, int] | None = None
) -> list[dict[str, Any]]:
    """One row per class: target, OpenStax and authored items in dev, dev total, check."""
    rows = []
    for cls in sorted(dc.CLASSES, key=lambda c: (_GROUP_ORDER[c.group], c.id)):
        openstax = dev["class_counts"]["openstax"].get(cls.id, 0)
        authored = dev["class_counts"]["authored"].get(cls.id, 0)
        rows.append(
            {
                "id": cls.id,
                "label": cls.label,
                "unit": cls.unit,
                "backlog": cls.backlog,
                "target": cls.target,
                "openstax": openstax,
                "authored": authored,
                "dev": openstax + authored,
                "check": None if check_counts is None else check_counts.get(cls.id, 0),
            }
        )
    return rows


def summary_markdown(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "| Class | Item | Backlog | Target | OpenStax | Authored | Dev | Check |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        check = "" if r["check"] is None else str(r["check"])
        lines.append(
            f"| {r['label']} (`{r['id']}`) | {r['unit']} | {r['backlog']} | {r['target']} "
            f"| {r['openstax']} | {r['authored']} | {r['dev']} | {check} |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- seal/verify


def seal_record(archive: bytes, files: Mapping[str, bytes], manifest: Mapping, date: str) -> dict:
    """SEAL.json: what is recorded about the check part, with no item names in it."""
    ids = sorted(r["id"] for r in part_records(files))
    return {
        "schema_version": 1,
        "set": DEVSET,
        "part": "check",
        "sealed_on": date,
        "archive": CHECK_ARCHIVE,
        "archive_sha256": sha256_bytes(archive),
        "archive_bytes": len(archive),
        "manifest_sha256": sha256_bytes(manifest_json(manifest)),
        "files": len(files),
        "records": len(ids),
        "record_ids_sha256": sha256_bytes("\n".join(ids).encode("utf-8")),
        "class_counts": total_class_counts(manifest),
        "rule": (
            "The check part is not read while building. Its sections are the greedy choice "
            f"among the {int(CHECK_SHARE * 100)}% of candidate sections whose SHA-256 of "
            "salt:book/slug falls below the share (selection.json, check_rule); its authored "
            "documents are fresh draws from the same generators (variant check). A run on it "
            "reports totals only."
        ),
    }


def seal_problems(root: Path) -> list[str]:
    """Why the sealed archive is not the recorded one; an empty list when it is."""
    seal_path = root / SEAL_FILE
    archive_path = root / CHECK_ARCHIVE
    if not seal_path.is_file():
        return [f"{SEAL_FILE} is missing"]
    if not archive_path.is_file():
        return [f"{CHECK_ARCHIVE} is missing"]
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    data = archive_path.read_bytes()
    problems = []
    if sha256_bytes(data) != seal["archive_sha256"]:
        problems.append(
            f"{CHECK_ARCHIVE} has SHA-256 {sha256_bytes(data)}, the seal records "
            f"{seal['archive_sha256']}"
        )
    elif len(data) != seal["archive_bytes"]:
        problems.append(
            f"{CHECK_ARCHIVE} has {len(data)} bytes, the seal records {seal['archive_bytes']}"
        )
    return problems


def dev_problems(root: Path) -> list[str]:
    """Checks of the dev part against its manifest and the held-out rules."""
    dev = root / "dev"
    manifest_path = dev / "manifest.json"
    if not manifest_path.is_file():
        return [f"{manifest_path} is missing"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    listed = {e["path"]: e for e in manifest["files"]}
    on_disk = {
        p.relative_to(dev).as_posix()
        for p in dev.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    for path in sorted(on_disk - listed.keys()):
        problems.append(f"{path} is not in the manifest")
    for path, entry in sorted(listed.items()):
        file = dev / path
        if not file.is_file():
            problems.append(f"{path} is listed in the manifest but missing")
        elif sha256_file(file) != entry["sha256"]:
            problems.append(f"{path} does not match its manifest hash")
    held_books = heldout_books()
    for path in sorted(on_disk):
        if not path.endswith(".jsonl"):
            continue
        for record in parse_jsonl((dev / path).read_bytes()):
            if record.get("source") != "openstax":
                continue
            if record["book_slug"] in held_books or is_heldout(
                record["book_slug"], record["page_slug"]
            ):
                problems.append(f"{record['id']} comes from a held-out section or book")
                break
            unknown = [c for c in record.get("classes", []) if c not in dc.CLASS_IDS]
            if unknown:
                problems.append(f"{record['id']} has unknown classes {unknown}")
    return problems


def verify(root: Path) -> list[str]:
    return dev_problems(root) + seal_problems(root)


def check_cache_dir(cache_root: Path, seal: Mapping) -> Path:
    return cache_root / f"check-{seal['archive_sha256'][:16]}"


def load_check_part(root: Path, cache_root: Path) -> Path:
    """The sealed part as a gold folder under `cache_root` (outside the repository),
    extracted once; refuses an archive that is not the recorded one."""
    problems = seal_problems(root)
    if problems:
        raise DevsetError("; ".join(problems))
    seal = json.loads((root / SEAL_FILE).read_text(encoding="utf-8"))
    target = check_cache_dir(cache_root, seal)
    marker = target / ".sealed-ok"
    if marker.is_file() and marker.read_text(encoding="utf-8") == seal["archive_sha256"]:
        return target
    files = read_tar_gz((root / CHECK_ARCHIVE).read_bytes())
    target.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    marker.write_text(seal["archive_sha256"], encoding="utf-8")
    return target
