"""Figure gold v2: figures for long descriptions and chart data tables (docs/eval-figures-long.md).

The v1 figure gold is 13 OpenStax Physics figures. Too few for a pass-rate claim (a Wilson
interval on 13 spans 0.58 to 0.96) and too few charts. v2 draws 40-60 figures from CC BY
sources by a written, seeded rule, stratified, each with its source, licence, a reference long
description and, for charts, a reference data table where the source has the plotted data.

This module is offline: it classifies candidates, applies the licence and exclusion gates,
makes the seeded draw and validates the gold file. Fetching the pools is
`scripts/figures_v2_pool.py`; the build is `scripts/figures_v2_build.py`.

The rule (also written in docs/eval-figures-long.md; changing either changes the draw):

1. Licence gate. OpenStax: the book's licence must be plain CC BY in both the archive and the
   CMS (`openstax.check_public_license`). arXiv: the OAI record's licence must be CC BY 3.0 or
   4.0. NC, SA, ND, CC0-only and the arXiv non-exclusive licence are out. A record without a
   licence URL is out.
2. Exclusion gate. No figure from a page whose slug is one of the held-out sections (in any
   book, so a later edition of a held-out section is out too), none from a page that already
   has figures in the v1 gold, tables gold or development set (`DEV_PAGE_SLUGS` passed in).
2b. Credit gate. The book's CC BY does not override a figure's own credit line: a
   caption credit naming another licence, permission, copyright or Wikimedia Commons or Flickr
   (`third_party_licence`) takes the figure out; so does a credit to a person or a body not on
   a short free list (US government bodies, ESO). Every credit is recorded in ATTRIBUTION.md.
3. Reference gate. OpenStax figures need one image and a reference text of at least
   `MIN_REFERENCE_CHARS` characters (the OpenStax alt text is the reference long description;
   a shorter one cannot grade a 100-1,200 character description); a chart's reference must
   also state at least `MIN_CHART_NUMBERS` numbers (readable values). arXiv figures need the
   plotted numbers in the TeX source.
4. Stratum. `classify_openstax` from the alt text (a fixed keyword list) and the book.
5. Draw. Inside each stratum, order by SHA-256 of `seed|stratum|id` (charts: arXiv charts
   with plotted data first, since only they have an exact reference table). Walk the order and
   keep a figure unless its page already gave one, or its book (or arXiv paper) already gave
   `MAX_PER_SOURCE` (6 for chemistry) to this stratum. Stop at the stratum's quota. A figure
   that later fails to download or render is skipped in order, never replaced by choice.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

SEED = "figures-v2-2026-11-02"

CHART = "chart"
DIAGRAM = "diagram"
PHOTO = "photo"
CHEMISTRY = "chemistry"
STRATA = (CHART, DIAGRAM, PHOTO, CHEMISTRY)

# 50 figures: 14 data charts (arXiv charts carry a reference table), 14 diagrams, 10 photos
# and illustrations, 12 chemistry structures.
QUOTAS: Mapping[str, int] = {CHART: 14, DIAGRAM: 14, PHOTO: 10, CHEMISTRY: 12}
MAX_PER_SOURCE = 3
# Chemistry structures come from two books only, so each may give more of them.
MAX_PER_SOURCE_BY_STRATUM: Mapping[str, int] = {CHEMISTRY: 6}
MIN_REFERENCE_CHARS = 120
# "Charts with readable values": an OpenStax chart's reference text must state at least this
# many numbers of two or more digits or with a decimal point (axis ranges, ticks, plotted
# values; single digits are mostly panel labels), a proxy that the figure shows numbers.
MIN_CHART_NUMBERS = 4
_PLAIN_NUMBER = re.compile(r"(?<![\w.])(?:\d{2,}|\d+[.,]\d+)(?![\w])")

# Plain CC BY of any version, https or http; BY-SA, BY-NC, BY-ND, CC0 do not match.
CC_BY_URL = re.compile(r"^https?://creativecommons\.org/licenses/by/(3\.0|4\.0)/?$")

# OpenStax books whose licence is plain CC BY (data/openstax-licenses.csv), chosen for figures a
# student must read. Only two live STEM books are CC BY (Physics, Statistics); the others are
# retired editions, which are CC BY but whose use as ground truth is still an open decision
# (docs/decisions/0001-scope.md, question 2), so each record carries `book_state` and the
# pre-registration asks for a sign-off. Chemistry structures can only come from the
# retired Chemistry. Held-out and development pages are removed by the exclusion gate.
OPENSTAX_BOOKS: Mapping[str, str] = {
    "physics": "physics",
    "statistics": "statistics",
    "college-physics": "physics",
    "chemistry": "chemistry",
    "chemistry-atoms-first": "chemistry",
    "biology": "life",
    "astronomy": "astronomy",
    "anatomy-and-physiology": "life",
    "principles-macroeconomics-2e": "economics",
}

# Alt-text openings, checked in this order; OpenStax alt text names the figure type first.
_CHART_WORDS = re.compile(
    r"\b(bar graph|bar chart|line graph|scatter ?plot|histogram|pie chart|box plot|"
    r"graph|plot|curve|chart)\b",
    re.I,
)
_PHOTO_WORDS = re.compile(
    r"\b(photograph|photo|picture|image of|micrograph|illustration|drawing|painting|"
    r"sketch|cartoon|satellite image|map)\b",
    re.I,
)
_CHEM_WORDS = re.compile(
    r"\b(structure|structural formula|lewis|molecule|molecular|skeletal|ring|bond|"
    r"compound|isomer|reaction|mechanism|orbital)\b",
    re.I,
)
_CHEM_STRICT = re.compile(
    r"(chemical structure|structural formula|molecular structure|skeletal (?:structure|formula)|"
    r"lewis (?:structure|symbol)|ball-and-stick|space-filling|"
    r"structure of (?:a |an |the )?(?:\w+ ){0,2}(?:molecule|acid|nucleotide|sugar|lipid|amino))",
    re.I,
)
_LEAD = 90  # the figure type is named in the first sentence or so


def classify_openstax(book: str, alt: str) -> str | None:
    """The stratum of an OpenStax figure from its alt text, or None when it is none of them
    (a table image, a screenshot, a figure of text)."""
    lead = alt[:_LEAD]
    if re.search(r"\bflow ?charts?\b", lead, re.I):
        return DIAGRAM
    field_name = OPENSTAX_BOOKS.get(book)
    if not _CHART_WORDS.search(lead) and (
        (field_name == "chemistry" and _CHEM_WORDS.search(alt[:200]))
        or _CHEM_STRICT.search(alt[:200])
    ):
        return CHEMISTRY
    if _CHART_WORDS.search(lead) and not _PHOTO_WORDS.search(lead):
        return CHART
    if _PHOTO_WORDS.search(lead):
        return PHOTO
    if re.search(
        r"\b(diagram|schematic|circuit|flowchart|flow chart|free-body|apparatus|cycle|"
        r"process|pathway|structure of|cross-section|labeled|labelled)\b",
        alt[:200],
        re.I,
    ):
        return DIAGRAM
    return None


@dataclass(frozen=True)
class Candidate:
    """One figure that passed the gates, with no text of the source beyond what the gold keeps."""

    id: str  # "openstax/<book>/<page>/fig-NNN" or "arxiv/<paper>/<name>"
    stratum: str
    source: str  # "openstax" or "arxiv"
    source_key: str  # the book slug or the arXiv id (for the per-source cap)
    page_key: str  # the page slug, or the arXiv id (one figure per page)

    def to_json(self) -> dict[str, str]:
        return {
            "id": self.id,
            "stratum": self.stratum,
            "source": self.source,
            "source_key": self.source_key,
            "page_key": self.page_key,
        }


def stable_key(seed: str, *parts: str) -> str:
    return hashlib.sha256("|".join((seed, *parts)).encode()).hexdigest()


def licence_ok(source: str, licence_url: str | None) -> bool:
    """The licence gate. A missing licence is a refusal."""
    if not licence_url:
        return False
    url = licence_url.strip()
    if source in ("openstax", "arxiv"):
        return bool(CC_BY_URL.match(url))
    return False


# A figure's own credit line can name a licence that is not the book's. These credits are out:
# an explicit licence other than plain CC BY, "permission", "copyright", or a source whose
# images carry their own licence terms (Wikimedia Commons, Flickr) with none stated. Credits to
# Credits that name a person or a body not on the free list are out too (their terms are not
# stated); credits to US government bodies and ESO are kept, and every credit is recorded.
_CREDIT = re.compile(r"\(credit[^)]*(?:\([^)]*\)[^)]*)*\)", re.I)
_NON_CC_BY_CREDIT = re.compile(
    r"by-?(?:sa|nc|nd)|share ?alike|non-?commercial|permission|copyright|\u00a9|"
    r"wikimedia|wikipedia|flickr|cc0|public domain|getty|shutterstock|istock",
    re.I,
)


_FREE_CREDIT_BODIES = re.compile(
    r"\b(NASA|NOAA|CDC|USFWS|USDA|USGS|NIH|NIST|NSSDC|JPL|ESO|U\.?S\.? (?:Army|Navy|Air Force)|"
    r"Department of Energy|DOE|FEMA|EPA)\b"
)


def credits_of(caption: str | None) -> list[str]:
    """The `(credit ...)` parts of a caption, as written."""
    return [m.group(0).strip("()") for m in _CREDIT.finditer(caption or "")]


def third_party_licence(caption: str | None) -> bool:
    """True when a credit line points to terms other than the book's CC BY, or names a source
    whose terms cannot be told from the caption: every credit must name a body on
    `_FREE_CREDIT_BODIES` (US government bodies, whose works are public domain, and ESO, CC BY)."""
    for credit in credits_of(caption):
        if _NON_CC_BY_CREDIT.search(credit):
            return True
        if not all(_FREE_CREDIT_BODIES.search(part) for part in credit.split(";")):
            return True
    return False


def is_held_out(page_slug: str, held_out_slugs: Iterable[str]) -> bool:
    return page_slug in set(held_out_slugs)


def openstax_candidates(
    book: str,
    page_slug: str,
    records: Iterable[Mapping[str, Any]],
    *,
    licence_url: str | None,
    excluded_pages: Iterable[str],
) -> list[tuple[Candidate, Mapping[str, Any]]]:
    """Candidates of one page, with the figure record each came from. Applies the licence,
    exclusion and reference gates; nothing here touches the network."""
    if not licence_ok("openstax", licence_url) or book not in OPENSTAX_BOOKS:
        return []
    if page_slug in set(excluded_pages):
        return []
    out = []
    for record in records:
        if record.get("kind") != "figure":
            continue
        images = record.get("images") or []
        if len(images) != 1 or not images[0].get("url"):
            continue
        alt = " ".join((images[0].get("alt") or "").split())
        if len(alt) < MIN_REFERENCE_CHARS:
            continue
        if record.get("caption_has_math"):
            continue
        if third_party_licence(record.get("caption")):
            continue
        stratum = classify_openstax(book, alt)
        if stratum is None:
            continue
        if stratum == CHART and len(_PLAIN_NUMBER.findall(alt)) < MIN_CHART_NUMBERS:
            continue  # values not readable: the reference states too few numbers
        out.append(
            (
                Candidate(
                    id=str(record["id"]),
                    stratum=stratum,
                    source="openstax",
                    source_key=book,
                    page_key=page_slug,
                ),
                record,
            )
        )
    return out


def order_stratum(
    candidates: Iterable[Candidate], stratum: str, seed: str = SEED
) -> list[Candidate]:
    pool = [c for c in candidates if c.stratum == stratum]
    # arXiv charts first: only they have an exact reference table (the plotted data).
    return sorted(pool, key=lambda c: (c.source != "arxiv", stable_key(seed, stratum, c.id), c.id))


def select(
    candidates: Iterable[Candidate],
    *,
    quotas: Mapping[str, int] = QUOTAS,
    seed: str = SEED,
    skip: Iterable[str] = (),
) -> list[Candidate]:
    """The draw. `skip` holds ids that failed later (download, render): skipped in order.
    Returns the chosen figures, strata in `STRATA` order, each in draw order."""
    skipped = set(skip)
    pool = list(candidates)
    chosen: list[Candidate] = []
    pages: set[tuple[str, str]] = set()  # one figure per page across all strata
    for stratum in STRATA:
        quota = quotas.get(stratum, 0)
        per_source: Counter[str] = Counter()
        taken = 0
        for cand in order_stratum(pool, stratum, seed):
            if taken >= quota:
                break
            if cand.id in skipped:
                continue
            if (cand.source, cand.page_key) in pages:
                continue
            if per_source[cand.source_key] >= MAX_PER_SOURCE_BY_STRATUM.get(
                stratum, MAX_PER_SOURCE
            ):
                continue
            pages.add((cand.source, cand.page_key))
            per_source[cand.source_key] += 1
            chosen.append(cand)
            taken += 1
    return chosen


def assign_splits(chosen: Iterable[Candidate]) -> dict[str, str]:
    """`analysis` or `held` per figure id. Within each stratum, in draw order, the figures
    alternate analysis, held, analysis, ...: the failures of the analysis half may be read to
    revise a prompt once; the held half is only ever scored (pre-registration, section 5)."""
    splits: dict[str, str] = {}
    seen: Counter[str] = Counter()
    for cand in chosen:
        splits[cand.id] = "analysis" if seen[cand.stratum] % 2 == 0 else "held"
        seen[cand.stratum] += 1
    return splits


def strata_counts(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(str(item["stratum"]) for item in items)
    return {stratum: counts.get(stratum, 0) for stratum in STRATA}


def pool_sha256(candidates: Iterable[Candidate]) -> str:
    rows = sorted((c.to_json() for c in candidates), key=lambda r: r["id"])
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


# ----------------------------------------------------------------------------- gold file

REQUIRED_FIELDS = (
    "id",
    "stratum",
    "source",
    "source_url",
    "licence_name",
    "licence_url",
    "attribution",
    "image",
    "reference_long_description",
    "reference_long_source",
)
TABLE_SOURCES = {"source-data", "alt-text"}
MAX_TABLE_ROWS = 40  # the figure prompt asks for at most 40 rows and 8 columns
MAX_TABLE_COLS = 8


class GoldFigureError(ValueError):
    pass


def validate_record(record: Mapping[str, Any]) -> list[str]:
    """Problems with one gold figure (empty when it is fine). The licence gate runs again here,
    so a hand-edited file cannot carry a figure the draw would have refused."""
    problems: list[str] = []
    rid = record.get("id", "?")
    for name in REQUIRED_FIELDS:
        if not record.get(name):
            problems.append(f"{rid}: missing {name}")
    if record.get("stratum") not in STRATA:
        problems.append(f"{rid}: stratum {record.get('stratum')!r} is not one of {STRATA}")
    if not licence_ok(str(record.get("source")), record.get("licence_url")):
        problems.append(f"{rid}: licence {record.get('licence_url')!r} fails the CC BY gate")
    if record.get("split") not in ("analysis", "held"):
        problems.append(f"{rid}: split must be analysis or held")
    if len(str(record.get("reference_long_description") or "")) < MIN_REFERENCE_CHARS:
        problems.append(f"{rid}: reference long description under {MIN_REFERENCE_CHARS} characters")
    image = record.get("image") or {}
    if not image.get("sha256") or not (image.get("url") or image.get("path")):
        problems.append(f"{rid}: image needs a sha256 and a url or path")
    table = record.get("reference_table")
    if table is not None:
        if record.get("stratum") != CHART:
            problems.append(f"{rid}: only charts carry a reference table")
        problems += _table_problems(rid, table)
    return problems


def _table_problems(rid: Any, table: Mapping[str, Any]) -> list[str]:
    header = table.get("header") or []
    rows = table.get("rows") or []
    problems = []
    if table.get("source") not in TABLE_SOURCES:
        problems.append(
            f"{rid}: table source {table.get('source')!r} not in {sorted(TABLE_SOURCES)}"
        )
    if not header or not rows:
        problems.append(f"{rid}: table needs a header and rows")
    if len(rows) > MAX_TABLE_ROWS or len(header) > MAX_TABLE_COLS:
        problems.append(f"{rid}: table over {MAX_TABLE_ROWS} rows or {MAX_TABLE_COLS} columns")
    if any(len(row) != len(header) for row in rows):
        problems.append(f"{rid}: table rows are not all {len(header)} cells wide")
    return problems


def validate_gold(records: Iterable[Mapping[str, Any]]) -> list[str]:
    """Problems with the whole file: each record, duplicate ids, duplicate pages."""
    problems: list[str] = []
    seen_ids: set[str] = set()
    pages: defaultdict[tuple[str, str], int] = defaultdict(int)
    for record in records:
        problems += validate_record(record)
        rid = str(record.get("id"))
        if rid in seen_ids:
            problems.append(f"{rid}: duplicate id")
        seen_ids.add(rid)
        pages[(str(record.get("source")), str(record.get("page_key")))] += 1
    problems += [
        f"{src}:{page}: more than one figure from one page"
        for (src, page), n in pages.items()
        if n > 1
    ]
    return problems
