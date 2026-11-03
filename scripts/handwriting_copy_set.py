"""Copy set kit: volunteers copy CC BY OpenStax worked examples by hand on their iPads.

The set measures how well handwriting is read on real iPad exports (vector ink from several notes
apps), with gold that nobody types: the gold is the published MathML of the passage the writer
copied. Writers' pages are published only with each writer's recorded consent. The README next to
the data (`data/handwriting-copy/README.md`) explains the whole flow.

    S=handwriting-copy-2026-11
    W=/path/outside/the/repo/work
    uv run python scripts/handwriting_copy_set.py select --work $W --seed $S --writers 3
    uv run python scripts/handwriting_copy_set.py brief --work $W --send-to "an address or link"
    uv run python scripts/handwriting_copy_set.py intake --work $W --returns /path/outside/repo/back
    uv run python scripts/handwriting_copy_set.py consent --work $W --writer w01 --publish yes
    uv run python scripts/handwriting_copy_set.py gold --work $W
    uv run python scripts/handwriting_copy_set.py manifest --work $W --out $W/manifest.json
    uv run python scripts/handwriting_copy_set.py export --work $W --dest data/handwriting-copy

Everything here is free: `select` reads public OpenStax pages (a small, polite request rate, cached
on disk), nothing calls a model. The work folder and the returned PDFs live outside the repository
(the script refuses a folder inside it): they hold people's handwriting. Only `export` writes
into the repository, and only the pages whose writer said yes.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf

from readablestem.gold import openstax as ox

REPO = Path(__file__).resolve().parent.parent
LICENCE_TABLE = REPO / "data" / "openstax-licenses.csv"
DEFAULT_CACHE = Path.home() / ".cache" / "readablestem" / "handwriting-copy" / "archive"
DEFAULT_SEED = "handwriting-copy-2026-11"
RULE_VERSION = 1

# ------------------------------------------------------------------------------ the rule

RULE = """\
Passage selection rule, version 1 (written down before any passage is drawn).

1. Books. Only books in the subject table below. A book is used only if the OpenStax licence
   table in this repository (data/openstax-licenses.csv) says CC BY and the live archive book
   record and the live CMS record both say plain CC BY (any version). Anything else is refused,
   and the refusal and its reason are recorded in selection.json. Books that are live or retired
   are allowed (retired first editions are CC BY; each passage records the book state);
   deprecated or unlisted books are not.
2. Passages. A passage is one worked example (a div with data-type="example") on a numbered
   section page. It qualifies if it has no image, figure or table (a writer must be able to copy
   all of it), 3 to 12 formulas, at least 3 of them with 3 or more characters, 15 to 220 words of
   prose, and at least 5 formulas per 100 words of prose.
3. Subjects and quotas. The number of passages is writers x passages per writer. Subjects get
   weights algebra 3, calculus 2, physics 3, statistics 2, chemistry 1, rounded by largest
   remainder (ties: the order just given). A section whose title is about limits, derivatives,
   integrals or rates of change belongs to calculus, whatever book it is in.
4. The seeded draw. Within a subject, the sections of its licensed books are put in the order of
   sha256("<seed>|<subject>|<book slug>|<section slug>"). Walking that order, a section gives
   its qualifying example with the smallest sha256("<seed>|<section slug>|<example id>"), or
   nothing if none qualifies. At most one passage per section, and a section is used by one
   subject only. The walk stops at the subject's quota.
5. Shortfalls. A subject that runs out (or has no licensed book: CC BY calculus books do not
   exist in the table) hands its missing passages, one at a time, to the subjects that still
   have candidates, heaviest weight first. The shortfall is recorded.
6. Writers. Passages are interleaved by subject, then dealt to the writers in turn, so every
   writer gets a spread and no passage goes to two writers.
"""

# Order matters: it breaks ties in the quota rounding and in the handing-on of shortfalls.
SUBJECTS: dict[str, dict[str, Any]] = {
    "algebra": {
        "weight": 3,
        "books": [
            "college-algebra",
            "algebra-and-trigonometry",
            "intermediate-algebra",
            "elementary-algebra",
            "prealgebra",
            "precalculus",
        ],
    },
    "calculus": {
        "weight": 2,
        "books": ["calculus-volume-1", "calculus-volume-2", "calculus-volume-3", "precalculus"],
    },
    "physics": {
        "weight": 3,
        "books": ["college-physics", "college-physics-ap-courses", "physics"],
    },
    "statistics": {
        "weight": 2,
        "books": ["introductory-statistics", "introductory-business-statistics", "statistics"],
    },
    "chemistry": {"weight": 1, "books": ["chemistry", "chemistry-atoms-first"]},
}
CALCULUS_TITLE = re.compile(
    r"\b(limits?|derivatives?|differentiat\w*|integra\w*|rates? of change|tangent lines?|"
    r"calculus|antiderivatives?)\b",
    re.IGNORECASE,
)
ALLOWED_STATES = {"live", "retired"}
MEDIA_TAGS = {"img", "figure", "table", "svg", "iframe", "audio", "video", "object", "canvas"}
MIN_MATH, MAX_MATH, MIN_BIG_MATH = 3, 12, 3
MIN_WORDS, MAX_WORDS, MIN_MATH_PER_100_WORDS = 15, 220, 5.0

# ------------------------------------------------------------------------- user-facing copy

PAGES_LICENCE = "CC BY 4.0"
PAGES_LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"

CONSENT_QUESTION = (
    "May we publish your handwriting pages? If you say yes, they go in the open ReadableSTEM "
    "repository under the CC BY 4.0 licence, so anyone may reuse them if they credit the source. "
    "They are credited to your writer id, not your name, and we remove the file name, author and "
    "other details from each PDF first. Handwriting can still be recognised by people who know "
    "it. If you say no, your pages stay private and we only count them (how many pages and "
    "equations). Reply yes or no. You can change your answer until we publish; after that, "
    "copies other people have made can't be taken back."
)

BRIEF_TITLE = "Copy worked examples by hand"
BRIEF_INTRO = (
    "Thank you for helping. You have {n} short passages from free OpenStax textbooks. Copy each "
    "one by hand on your iPad, as you would copy it into your own notes. We use your pages to "
    "test how well ReadableSTEM reads handwritten maths. The right answers come from the "
    "textbook, so you don't type anything."
)
BRIEF_STEPS = (
    "Open any notes app you already use, with your usual pen and page.",
    "Copy one passage at a time: the question, then the solution, every line of working and "
    "every equation, in the order printed. Write the way you normally write. Don't print "
    "neatly for us.",
    "If you slip, fix it the way you normally would, for example by crossing it out.",
    "Export that passage as a PDF. Choose PDF, not a picture. If the app offers to flatten the "
    "page or turn it into an image, leave that off.",
    "Send one PDF per passage, named with its code, for example {example}.pdf. Send them to: "
    "{send_to}",
)
BRIEF_PLEASE_DONT = (
    "Send only the pages of the passage. A whole notebook may hold things you don't want shared.",
    "Don't copy by tracing or from a typed file. Look at the passage and write it.",
    "Don't skip lines. If a line is hard to write, write it as well as you can.",
)
BRIEF_CONSENT_TITLE = "Your pages and your name"
BRIEF_FOOT = (
    "The passages are from OpenStax textbooks, licensed CC BY 4.0, with credit under each one. "
    "OpenStax is a service of Rice University."
)

# --------------------------------------------------------------------------- small helpers


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_outside_repo(path: Path, what: str) -> Path:
    """Refuse a folder inside the repository: it would hold people's handwriting."""
    resolved = path.expanduser().resolve()
    if resolved == REPO or REPO in resolved.parents:
        raise SystemExit(
            f"{what} must be outside the repository ({REPO}): it holds people's handwriting. "
            "Only `export` writes into the repository, and only consented pages."
        )
    return resolved


def writer_id(index: int) -> str:
    return f"w{index:02d}"


def _local(tag: Any) -> str:
    return ox._local(tag)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", "utf-8")


# ----------------------------------------------------------------------- licence gate (1)


def licensed_slugs(table: Path = LICENCE_TABLE) -> set[str]:
    """Slugs the committed licence table marks CC BY (the first, offline half of the gate)."""
    with table.open(encoding="utf-8", newline="") as fh:
        return {row["slug"] for row in csv.DictReader(fh) if row["cc_by"] == "True"}


def licence_gate(book: ox.Book, *, allow_retired: bool = True) -> None:
    """Raise unless the archive record and the CMS both say plain CC BY and the state is allowed.

    Reuses the public gold set's licence check (a missing licence on either side is refused) and
    adds the state rule of this kit: live, or retired when allowed; never deprecated or unlisted.
    """
    ox.check_public_license(book)
    allowed = {"live"} | ({"retired"} if allow_retired else set())
    if book.state not in allowed & ALLOWED_STATES:
        raise ox.BookStateError(f"{book.slug!r} has state {book.state!r}, which this kit refuses")


# ------------------------------------------------------------- finding passages (rule 2)


def visible_words(elem: ET.Element) -> str:
    """The prose of an element with every formula left out."""
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        if _local(node.tag) == "math":
            parts.append(" ")
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)
        parts.append(" ")

    walk(elem)
    return " ".join("".join(parts).split())


def example_stats(example: ET.Element) -> dict[str, Any]:
    maths = [e for e in example.iter() if _local(e.tag) == "math"]
    sizes = [len("".join(m.itertext()).strip()) for m in maths]
    words = len(visible_words(example).split())
    return {
        "n_math": len(maths),
        "n_big_math": sum(1 for s in sizes if s >= 3),
        "n_words": words,
        "n_display": sum(1 for m in maths if m.get("display") == "block"),
        "has_media": any(_local(e.tag) in MEDIA_TAGS for e in example.iter()),
    }


def qualifies(stats: dict[str, Any]) -> bool:
    if stats["has_media"]:
        return False
    if not MIN_MATH <= stats["n_math"] <= MAX_MATH or stats["n_big_math"] < MIN_BIG_MATH:
        return False
    if not MIN_WORDS <= stats["n_words"] <= MAX_WORDS:
        return False
    return stats["n_math"] * 100 / stats["n_words"] >= MIN_MATH_PER_100_WORDS


def page_examples(xhtml: str) -> list[tuple[ET.Element, dict[str, Any]]]:
    root = ET.fromstring(xhtml)
    out = []
    for elem in root.iter():
        if _local(elem.tag) == "div" and elem.get("data-type") == "example" and elem.get("id"):
            out.append((elem, example_stats(elem)))
    return out


def subject_of_page(subject: str, page_title: str) -> bool:
    """Calculus takes sections about calculus topics; the other subjects never do."""
    is_calculus_title = bool(CALCULUS_TITLE.search(page_title))
    return is_calculus_title if subject == "calculus" else not is_calculus_title


def section_order(seed: str, subject: str, book_slug: str, page_slug: str) -> str:
    return sha256_text(f"{seed}|{subject}|{book_slug}|{page_slug}")


def pick_example(
    seed: str, page_slug: str, examples: list[tuple[ET.Element, dict[str, Any]]]
) -> tuple[ET.Element, dict[str, Any]] | None:
    good = [(e, s) for e, s in examples if qualifies(s)]
    if not good:
        return None
    return min(good, key=lambda es: sha256_text(f"{seed}|{page_slug}|{es[0].get('id')}"))


def quotas(total: int) -> dict[str, int]:
    """Largest remainder over the subject weights; ties go to the earlier subject."""
    weights = {name: spec["weight"] for name, spec in SUBJECTS.items()}
    whole = sum(weights.values())
    base = {name: total * w // whole for name, w in weights.items()}
    remainders = sorted(
        weights, key=lambda n: (-(total * weights[n] % whole), list(weights).index(n))
    )
    for name in remainders[: total - sum(base.values())]:
        base[name] += 1
    return base


# ----------------------------------------------------------------- selection (rules 3 to 6)


def select_passages(
    fetcher: ox.Fetcher,
    *,
    seed: str,
    writers: int,
    per_writer: int,
    date: str,
    licensed: set[str],
    allow_retired: bool = True,
    pins: dict[str, ox.BookPin] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Apply the written rule. Returns (selection record, {code: example source XHTML})."""
    pins = pins or {}
    total = writers * per_writer
    books: dict[str, ox.Book] = {}
    excluded: list[dict[str, str]] = []
    refused: dict[str, str] = {}

    def book_for(slug: str) -> ox.Book | None:
        if slug in books:
            return books[slug]
        if slug in refused:
            return None
        if slug not in licensed:
            refused[slug] = "not CC BY in the committed licence table"
        else:
            try:
                book = ox.resolve_book(slug, fetcher, pins.get(slug))
                licence_gate(book, allow_retired=allow_retired)
            except ox.GoldError as exc:
                refused[slug] = type(exc).__name__
            else:
                books[slug] = book
                return book
        excluded.append({"book_slug": slug, "reason": refused[slug]})
        return None

    used_sections: set[tuple[str, str]] = set()

    def stream(subject: str):
        """Passages of one subject in the seeded order, lazily (one page fetched per step)."""
        sections = []
        for slug in SUBJECTS[subject]["books"]:
            book = book_for(slug)
            if book is None:
                continue
            for page in ox.iter_pages(book.tree):
                if page.section is not None and subject_of_page(subject, page.title):
                    key = section_order(seed, subject, slug, page.slug)
                    sections.append((key, book, page))
        for _, book, page in sorted(sections, key=lambda s: s[0]):
            if (book.slug, page.slug) in used_sections:
                continue
            content = fetcher.get_json(book.content_url(page.uuid)).get("content") or ""
            picked = pick_example(seed, page.slug, page_examples(content))
            if picked is None:
                continue
            used_sections.add((book.slug, page.slug))
            example, stats = picked
            yield {
                "subject": subject,
                "book": book,
                "page": page,
                "element_id": example.get("id"),
                "stats": stats,
                "source": ox._serialize_html(example),
            }

    streams = {name: stream(name) for name in SUBJECTS}
    want = quotas(total)
    chosen: dict[str, list[dict[str, Any]]] = {name: [] for name in SUBJECTS}
    exhausted: set[str] = set()

    def take(subject: str) -> bool:
        nxt = next(streams[subject], None)
        if nxt is None:
            exhausted.add(subject)
            return False
        chosen[subject].append(nxt)
        return True

    for subject in SUBJECTS:
        for _ in range(want[subject]):
            if not take(subject):
                break
    missing = total - sum(len(v) for v in chosen.values())
    by_weight = sorted(SUBJECTS, key=lambda n: (-SUBJECTS[n]["weight"], list(SUBJECTS).index(n)))
    while missing > 0 and len(exhausted) < len(SUBJECTS):
        for subject in by_weight:
            if missing > 0 and subject not in exhausted and take(subject):
                missing -= 1
    shortfalls = {
        name: want[name] - len(chosen[name]) for name in SUBJECTS if len(chosen[name]) < want[name]
    }
    handed_on = {
        name: len(chosen[name]) - want[name] for name in SUBJECTS if len(chosen[name]) > want[name]
    }

    # Interleave by subject, then deal to the writers in turn.
    queues = [list(chosen[name]) for name in SUBJECTS]
    ordered: list[dict[str, Any]] = []
    while any(queues):
        for queue in queues:
            if queue:
                ordered.append(queue.pop(0))
    counters: Counter[str] = Counter()
    passages: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    for position, item in enumerate(ordered):
        wid = writer_id(position % writers + 1)
        counters[wid] += 1
        code = f"{wid}-{counters[wid]:02d}"
        book: ox.Book = item["book"]
        page: ox.PageRef = item["page"]
        sources[code] = item["source"]
        passages.append(
            {
                "code": code,
                "writer_id": wid,
                "subject": item["subject"],
                "book_slug": book.slug,
                "book_title": book.title,
                "book_state": book.state,
                "authors": book.authors,
                "license_name": book.license_name,
                "license_url": book.license_url,
                "archive_version": book.archive_version,
                "book_version": book.version,
                "page_slug": page.slug,
                "page_title": page.title,
                "page_uuid": page.uuid,
                "element_id": item["element_id"],
                "source_url": f"{book.page_url(page.slug)}#{item['element_id']}",
                "details_url": book.details_url,
                "retrieved_at": date,
                **item["stats"],
            }
        )
    selection = {
        "kit": "handwriting-copy",
        "rule_version": RULE_VERSION,
        "rule": RULE,
        "seed": seed,
        "selected_on": date,
        "writers": writers,
        "per_writer": per_writer,
        "allow_retired": allow_retired,
        "quotas": want,
        "chosen_per_subject": {name: len(chosen[name]) for name in SUBJECTS},
        "shortfalls": shortfalls,
        "handed_on": handed_on,
        "excluded_books": excluded,
        "pins": {
            slug: {"archive_version": b.archive_version, "version": b.version}
            for slug, b in sorted(books.items())
        },
        "passages": passages,
    }
    return selection, sources


def write_selection(work: Path, selection: dict[str, Any], sources: dict[str, str]) -> None:
    _write_json(work / "selection.json", selection)
    for code, xhtml in sources.items():
        path = work / "passages" / f"{code}.xhtml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(xhtml + "\n", encoding="utf-8")


def load_selection(work: Path) -> dict[str, Any]:
    selection = _read_json(work / "selection.json")
    if selection is None:
        raise SystemExit(f"{work}/selection.json not found: run `select` first")
    return selection


# ---------------------------------------------------------------------- writer brief (2)

BRIEF_CSS = """
body{font:18px/1.5 system-ui,sans-serif;max-width:46rem;margin:0 auto;padding:1rem 1.25rem;
color:#111}h1{font-size:1.7rem}h2{font-size:1.25rem;margin-top:1.8rem}
.passage{border:1px solid #888;border-radius:6px;padding:.75rem 1rem;margin:1.25rem 0;
break-inside:avoid-page}.code{font-weight:700;font-size:1.1rem}
.src{font-size:.85rem;color:#333}.consent{border-left:4px solid #444;padding-left:1rem}
math{font-size:1.25em}math[display=block]{margin:.5rem 0}
@media print{.passage{page-break-after:always}}
"""

HTML_KEEP = {"p", "div", "span", "em", "strong", "b", "i", "sub", "sup", "ol", "ul", "li", "br"}
HTML_HEADINGS = {"h3", "h4", "h5"}
MATH_ATTRS = {
    "display", "mathvariant", "width", "height", "stretchy", "fence", "separator", "accent",
    "columnalign", "rowspan", "columnspan", "linethickness", "mathsize", "scriptlevel",
    "displaystyle", "lspace", "rspace", "movablelimits", "symmetric", "largeop", "open", "close",
}  # fmt: skip
MATH_TAGS = {
    "math", "mrow", "mi", "mn", "mo", "mtext", "mspace", "msup", "msub", "msubsup", "mfrac",
    "msqrt", "mroot", "mover", "munder", "munderover", "mtable", "mtr", "mtd", "mfenced",
    "mstyle", "mpadded", "menclose", "mphantom", "mmultiscripts", "mprescripts", "none",
}  # fmt: skip


def render_passage_html(elem: ET.Element) -> str:
    """The passage as safe HTML: whitelisted tags, no attributes except on maths, no media."""

    def emit(node: ET.Element) -> str:
        tag = _local(node.tag)
        if tag in MEDIA_TAGS | {"script", "style", "annotation", "annotation-xml"}:
            return ""
        inner = html.escape(node.text or "", quote=False)
        if tag == "semantics":  # presentation form only: its first child
            kids = list(node)[:1]
            inner = ""
        else:
            kids = list(node)
        for child in kids:
            inner += emit(child) + html.escape(child.tail or "", quote=False)
        if tag in MATH_TAGS:
            attrs = "".join(
                f' {k}="{html.escape(v)}"'
                for k, v in sorted(node.attrib.items())
                if k in MATH_ATTRS and "{" not in k
            )
            return f"<{tag}{attrs}>{inner}</{tag}>"
        if tag in HTML_KEEP | HTML_HEADINGS:
            return f"<{tag}>{inner}</{tag}>"
        return inner  # an unknown tag: keep its text, drop the tag

    return emit(elem)


def attribution_line(p: dict[str, Any]) -> str:
    authors = ", ".join(p.get("authors") or []) or "OpenStax"
    return (
        f"{p['book_title']}, {p['page_title']}. {authors} and others, OpenStax, "
        f"{p['license_name'] or 'CC BY'} (CC BY 4.0). {p['details_url']}"
    )


def brief_html(
    writer: str, passages: list[dict[str, Any]], xhtml_for: dict[str, str], send_to: str
) -> str:
    esc = html.escape
    first = passages[0]["code"] if passages else f"{writer}-01"
    steps = "".join(
        f"<li>{esc(s.format(example=first, send_to=send_to))}</li>" for s in BRIEF_STEPS
    )
    dont = "".join(f"<li>{esc(s)}</li>" for s in BRIEF_PLEASE_DONT)
    blocks = []
    for p in passages:
        body = render_passage_html(ET.fromstring(xhtml_for[p["code"]]))
        blocks.append(
            f'<section class="passage"><p class="code">{esc(p["code"])}</p>{body}'
            f'<p class="src">{esc(attribution_line(p))}<br>{esc(p["source_url"])}</p></section>'
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(BRIEF_TITLE)} ({esc(writer)})</title><style>{BRIEF_CSS}</style></head><body>"
        f"<h1>{esc(BRIEF_TITLE)}</h1><p>Your writer id: <strong>{esc(writer)}</strong></p>"
        f"<p>{esc(BRIEF_INTRO.format(n=len(passages)))}</p>"
        f"<h2>What to do</h2><ol>{steps}</ol><h2>Please don't</h2><ul>{dont}</ul>"
        f'<h2>{esc(BRIEF_CONSENT_TITLE)}</h2><p class="consent">{esc(CONSENT_QUESTION)}</p>'
        f'<h2>Your passages</h2>{"".join(blocks)}<p class="src">{esc(BRIEF_FOOT)}</p>'
        "</body></html>\n"
    )


def build_briefs(work: Path, out: Path, send_to: str) -> list[Path]:
    selection = load_selection(work)
    out.mkdir(parents=True, exist_ok=True)
    xhtml_for = {
        p["code"]: (work / "passages" / f"{p['code']}.xhtml").read_text("utf-8")
        for p in selection["passages"]
    }
    written = []
    for index in range(1, selection["writers"] + 1):
        wid = writer_id(index)
        mine = [p for p in selection["passages"] if p["writer_id"] == wid]
        path = out / f"brief-{wid}.html"
        path.write_text(brief_html(wid, mine, xhtml_for, send_to), encoding="utf-8")
        written.append(path)
    return written


# ----------------------------------------------------------------------------- intake (3)

PDF_META_KEYS = (
    "title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate",
    "trapped",
)  # fmt: skip
MAX_PAGES = 4
MAX_BYTES = 60 * 1024 * 1024
MIN_VECTOR_PATHS = 20
CODE_IN_NAME = re.compile(r"(?<![a-z0-9])(w\d{2})[\s_.\-]*p?[\s_.\-]*(\d{1,2})(?!\d)")


def codes_in_name(filename: str) -> set[str]:
    """Passage codes a file name mentions, normalised to `w01-03`; tolerant of case and spacing."""
    stem = Path(filename).stem.lower()
    return {f"{w}-{int(n):02d}" for w, n in CODE_IN_NAME.findall(stem)}


def match_returns(folder: Path, codes: set[str]) -> dict[str, Any]:
    """Match each PDF in `folder` (and its subfolders) to a passage code by its file name."""
    by_code: dict[str, list[Path]] = {}
    unmatched: list[Path] = []
    ambiguous: list[Path] = []
    unknown: list[Path] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        found = codes_in_name(path.name)
        if len(found) == 1:
            code = next(iter(found))
            if code in codes:
                by_code.setdefault(code, []).append(path)
            else:
                unknown.append(path)
        elif len(found) > 1:
            ambiguous.append(path)
        else:
            unmatched.append(path)
    duplicates = {c: ps for c, ps in by_code.items() if len(ps) > 1}
    return {
        "matched": {c: ps[0] for c, ps in by_code.items() if len(ps) == 1},
        "duplicates": duplicates,
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "unknown": unknown,
    }


def inspect_pdf(path: Path) -> dict[str, Any]:
    """Counts only (never content): pages, ink kind, producer. `status` is ok or a refusal."""
    if path.stat().st_size > MAX_BYTES:
        return {"status": "rejected_too_large"}
    try:
        doc = pymupdf.open(path)
    except Exception:  # corrupt files raise several exception types
        return {"status": "rejected_unreadable"}
    with doc:
        if doc.needs_pass or doc.page_count < 1:
            return {"status": "rejected_unreadable"}
        n_pages = doc.page_count
        if n_pages > MAX_PAGES:
            return {"status": "rejected_too_many_pages", "n_pages": n_pages}
        paths = sum(len(page.get_drawings()) for page in doc)
        images = sum(len(page.get_images()) for page in doc)
        meta = doc.metadata or {}
    ink = "vector" if paths >= MIN_VECTOR_PATHS else "raster" if images else "none"
    return {
        "status": "ok" if ink != "none" else "rejected_no_ink",
        "n_pages": n_pages,
        "ink": ink,
        "vector_paths": paths,
        "images": images,
        "producer": meta.get("producer") or None,
        "creator": meta.get("creator") or None,
    }


def run_intake(work: Path, returns: Path) -> dict[str, Any]:
    selection = load_selection(work)
    codes = {p["code"] for p in selection["passages"]}
    result = match_returns(returns, codes)
    intake = _read_json(work / "intake.json", {})
    for code, path in sorted(result["matched"].items()):
        info = inspect_pdf(path)
        if info["status"] == "ok":
            dest = work / "pages" / f"{code}.pdf"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
            info["sha256"] = sha256_file(dest)
        intake[code] = info
    for code in result["duplicates"]:
        intake[code] = {"status": "rejected_duplicate"}
    _write_json(work / "intake.json", intake)
    return {
        "matched": sorted(result["matched"]),
        "duplicates": sorted(result["duplicates"]),
        "unmatched_files": len(result["unmatched"]),
        "ambiguous_files": len(result["ambiguous"]),
        "unknown_code_files": len(result["unknown"]),
        "accepted": sorted(c for c, i in intake.items() if i["status"] == "ok"),
        "rejected": {c: i["status"] for c, i in sorted(intake.items()) if i["status"] != "ok"},
        "still_missing": sorted(codes - set(intake)),
    }


# --------------------------------------------------------------------------- consent (5)


def record_consent(work: Path, writer: str, publish: bool, date: str) -> None:
    selection = load_selection(work)
    if writer not in {p["writer_id"] for p in selection["passages"]}:
        raise SystemExit(f"unknown writer id {writer!r}")
    consent = _read_json(work / "consent.json", {})
    consent[writer] = {
        "publish": publish,
        "recorded_on": date,
        "question_sha256": sha256_text(CONSENT_QUESTION),
    }
    _write_json(work / "consent.json", consent)


# ------------------------------------------------------------------------------- gold (4)


def gold_records(selection: dict[str, Any], xhtml_for: dict[str, str]) -> list[dict[str, Any]]:
    """Gold from the passage source only: the published MathML in reading order, no typing."""
    records: list[dict[str, Any]] = []
    for p in selection["passages"]:
        xhtml = xhtml_for[p["code"]]
        equations = [
            r for r in ox.extract_elements(xhtml, p["source_url"]) if r["kind"] == "equation"
        ]
        base = {
            "source": "openstax",
            "code": p["code"],
            "book_slug": p["book_slug"],
            "page_slug": p["page_slug"],
            "element_id": p["element_id"],
            "license_url": p["license_url"],
            "source_url": p["source_url"],
        }
        for rec in equations:
            records.append(
                {
                    **base,
                    "id": f"handwriting-copy/{p['code']}/eq-{rec['index_on_page']:03d}",
                    "kind": "equation",
                    "order": rec["index_on_page"],
                    "display": rec["display"],
                    "context": rec["context"],
                    "mathml": rec["mathml"],
                }
            )
        text = visible_words(ET.fromstring(xhtml))
        records.append(
            {**base, "id": f"handwriting-copy/{p['code']}/prose", "kind": "prose", "text": text}
        )
    return records


def load_sources(work: Path, selection: dict[str, Any]) -> dict[str, str]:
    return {
        p["code"]: (work / "passages" / f"{p['code']}.xhtml").read_text("utf-8")
        for p in selection["passages"]
    }


def write_gold(work: Path, out: Path | None = None) -> Path:
    selection = load_selection(work)
    records = gold_records(selection, load_sources(work, selection))
    out = out or work / "gold.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return out


# ---------------------------------------------------------------------------- manifest (5)


def build_manifest(work: Path, date: str) -> dict[str, Any]:
    """The publishable manifest. Pages without a recorded yes appear as counts only."""
    selection = load_selection(work)
    intake = _read_json(work / "intake.json", {})
    consent = _read_json(work / "consent.json", {})
    gold = gold_records(selection, load_sources(work, selection))
    eq_count = Counter(r["code"] for r in gold if r["kind"] == "equation")
    eq_hash = {
        code: sha256_text(
            "\n".join(r["mathml"] for r in gold if r["code"] == code and r["kind"] == "equation")
        )
        for code in eq_count
    }
    passages = []
    for p in selection["passages"]:
        code, wid = p["code"], p["writer_id"]
        got = intake.get(code)
        agreed = bool(consent.get(wid, {}).get("publish"))
        if got is None:
            page: dict[str, Any] = {"status": "missing"}
        elif got["status"] != "ok":
            page = {"status": "rejected", "reason": got["status"]}
        else:
            page = {
                "status": "published" if agreed else "private",
                "n_pages": got["n_pages"],
                "ink": got["ink"],
                "producer": got.get("producer"),
            }
            if agreed:
                page["file"] = f"pages/{code}.pdf"
        passages.append(
            {
                "code": code,
                "writer_id": wid,
                "subject": p["subject"],
                "book_slug": p["book_slug"],
                "book_title": p["book_title"],
                "book_state": p["book_state"],
                "section": p["page_title"],
                "source_url": p["source_url"],
                "license_url": p["license_url"],
                "archive_version": p["archive_version"],
                "book_version": p["book_version"],
                "n_equations": eq_count.get(code, 0),
                "gold_sha256": eq_hash.get(code),
                "page": page,
            }
        )
    writers = []
    for index in range(1, selection["writers"] + 1):
        wid = writer_id(index)
        mine = [p for p in passages if p["writer_id"] == wid]
        record = consent.get(wid)
        writers.append(
            {
                "writer_id": wid,
                "consent_recorded": record is not None,
                "consent_publish": bool(record and record["publish"]),
                "consent_recorded_on": record["recorded_on"] if record else None,
                "passages": len(mine),
                "pages_published": sum(p["page"]["status"] == "published" for p in mine),
                "pages_private": sum(p["page"]["status"] == "private" for p in mine),
            }
        )
    return {
        "kit": "handwriting-copy",
        "manifest_version": 1,
        "generated_on": date,
        "seed": selection["seed"],
        "rule_version": selection["rule_version"],
        "gold": "published OpenStax MathML of each copied passage; nothing typed by a person",
        "gold_licence": "CC BY 4.0 (OpenStax)",
        "pages_licence": PAGES_LICENCE,
        "pages_licence_url": PAGES_LICENCE_URL,
        "consent_question_sha256": sha256_text(CONSENT_QUESTION),
        "consent_question": CONSENT_QUESTION,
        "pins": selection["pins"],
        "writers": writers,
        "passages": passages,
        "totals": {
            "passages": len(passages),
            "equations": sum(p["n_equations"] for p in passages),
            "published": sum(p["page"]["status"] == "published" for p in passages),
            "private": sum(p["page"]["status"] == "private" for p in passages),
            "missing": sum(p["page"]["status"] == "missing" for p in passages),
            "rejected": sum(p["page"]["status"] == "rejected" for p in passages),
        },
    }


def strip_pdf_metadata(src: Path, dst: Path) -> None:
    """Copy a PDF without file name, author, title or dates; keep the app strings and the ink."""
    with pymupdf.open(src) as doc:
        meta = doc.metadata or {}
        keep = {"producer": meta.get("producer") or "", "creator": meta.get("creator") or ""}
        # Every key is set: keys left out of the dict would keep their old values.
        doc.set_metadata({k: keep.get(k, "") for k in PDF_META_KEYS})
        doc.del_xml_metadata()
        dst.parent.mkdir(parents=True, exist_ok=True)
        doc.save(dst, garbage=3, deflate=True)


def render_attribution(selection: dict[str, Any]) -> str:
    lines = [
        "# Attribution",
        "",
        "The passages copied for this set come from OpenStax textbooks, licensed CC BY 4.0. "
        "Each page of handwriting in `pages/` is a hand copy of one passage, published by its "
        f"writer (a pseudonymous id) under {PAGES_LICENCE} ({PAGES_LICENCE_URL}). The gold in "
        "`gold.jsonl` is the textbook's own MathML.",
        "",
        "| Book | Authors | Licence | Passages |",
        "|---|---|---|---|",
    ]
    by_book: dict[str, list[dict[str, Any]]] = {}
    for p in selection["passages"]:
        by_book.setdefault(p["book_slug"], []).append(p)
    for ps in (by_book[s] for s in sorted(by_book)):
        first = ps[0]
        authors = ", ".join(first.get("authors") or []) or "OpenStax"
        lines.append(
            f"| [{first['book_title']}]({first['details_url']}) ({first['book_state']}) | "
            f"{authors} and others | {first['license_url']} | {len(ps)} |"
        )
    lines += [
        "",
        "Access for free at https://openstax.org. OpenStax is a service of Rice University.",
        "",
    ]
    return "\n".join(lines)


def run_export(work: Path, dest: Path, date: str) -> dict[str, Any]:
    manifest = build_manifest(work, date)
    selection = load_selection(work)
    dest.mkdir(parents=True, exist_ok=True)
    pages = dest / "pages"
    if pages.exists():
        for old in pages.glob("*.pdf"):
            old.unlink()
    for p in manifest["passages"]:
        if p["page"]["status"] == "published":
            strip_pdf_metadata(work / "pages" / f"{p['code']}.pdf", dest / p["page"]["file"])
            p["page"]["sha256"] = sha256_file(dest / p["page"]["file"])
    _write_json(dest / "manifest.json", manifest)
    write_gold(work, dest / "gold.jsonl")
    (dest / "ATTRIBUTION.md").write_text(render_attribution(selection), encoding="utf-8")
    return manifest["totals"]


# -------------------------------------------------------------------------------- command line


def _parse_pins(items: list[str]) -> dict[str, ox.BookPin]:
    pins = {}
    for item in items:
        slug, _, rest = item.partition("=")
        archive, _, version = rest.partition("@")
        if not (slug and archive and version):
            raise SystemExit(f"--pin wants slug=ARCHIVE@VERSION, got {item!r}")
        pins[slug] = ox.BookPin(archive, version)
    return pins


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--work", type=Path, required=True, help="work folder, outside the repo")

    p = sub.add_parser("select", help="draw the passages with the written rule")
    common(p)
    p.add_argument("--seed", default=DEFAULT_SEED)
    p.add_argument("--writers", type=int, default=3)
    p.add_argument("--per-writer", type=int, default=10)
    p.add_argument("--date", default=dt.date.today().isoformat())
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    p.add_argument("--live-only", action="store_true", help="refuse retired editions")
    p.add_argument("--pin", action="append", default=[], metavar="SLUG=ARCHIVE@VERSION")
    p = sub.add_parser("brief", help="write one printable writer brief per writer")
    common(p)
    p.add_argument("--send-to", required=True, help="where writers send their PDFs")
    p.add_argument("--out", type=Path)
    p = sub.add_parser("intake", help="match returned PDFs to passages")
    common(p)
    p.add_argument("--returns", type=Path, required=True, help="folder outside the repo")
    p = sub.add_parser("consent", help="record a writer's answer to the publishing question")
    common(p)
    p.add_argument("--writer", required=True)
    p.add_argument("--publish", choices=["yes", "no"], required=True)
    p.add_argument("--date", default=dt.date.today().isoformat())
    p = sub.add_parser("gold", help="build the gold from the passage source")
    common(p)
    p.add_argument("--out", type=Path)
    p = sub.add_parser("manifest", help="write the publishable manifest")
    common(p)
    p.add_argument("--out", type=Path)
    p.add_argument("--date", default=dt.date.today().isoformat())
    p = sub.add_parser("export", help="write manifest, gold, attribution and consented pages")
    common(p)
    p.add_argument("--dest", type=Path, default=REPO / "data" / "handwriting-copy")
    p.add_argument("--date", default=dt.date.today().isoformat())
    args = parser.parse_args(argv)

    work = ensure_outside_repo(args.work, "--work")
    if args.cmd == "select":
        selection, sources = select_passages(
            ox.HttpFetcher(cache_dir=args.cache),
            seed=args.seed,
            writers=args.writers,
            per_writer=args.per_writer,
            date=args.date,
            licensed=licensed_slugs(),
            allow_retired=not args.live_only,
            pins=_parse_pins(args.pin),
        )
        write_selection(work, selection, sources)
        print(
            f"{len(selection['passages'])} passages for {args.writers} writers; "
            f"per subject {selection['chosen_per_subject']}; shortfalls {selection['shortfalls']}; "
            f"excluded books {[e['book_slug'] for e in selection['excluded_books']]}"
        )
    elif args.cmd == "brief":
        for path in build_briefs(work, args.out or work / "briefs", args.send_to):
            print(path)
    elif args.cmd == "intake":
        returns = ensure_outside_repo(args.returns, "--returns")
        print(json.dumps(run_intake(work, returns), indent=2))
    elif args.cmd == "consent":
        record_consent(work, args.writer, args.publish == "yes", args.date)
        print(f"{args.writer}: publish={args.publish}")
    elif args.cmd == "gold":
        print(write_gold(work, args.out))
    elif args.cmd == "manifest":
        out = args.out or work / "manifest.json"
        _write_json(out, build_manifest(work, args.date))
        print(out)
    elif args.cmd == "export":
        print(json.dumps(run_export(work, args.dest, args.date), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
