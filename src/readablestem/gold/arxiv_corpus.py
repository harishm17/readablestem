"""Corpus selection for the real-world evaluation set (docs/eval-realworld.md).

Papers come from arXiv, under licences that allow derived data to be redistributed (CC BY of any
version and CC0). Nothing here reads the wall clock or a random source that is not seeded: given the
same harvested records (kept under `cache_dir`, outside the repository) the candidate pool, its
order and the draw are the same.

Steps, each a function here and a subcommand of `scripts/realworld_select.py`:

1. `harvest`: OAI-PMH `ListRecords` (format `arXiv`) of a set and a datestamp window. The record
   carries the licence, so no paper is fetched before its licence is known. Only records with an
   accepted licence are kept; the totals per set are counted.
2. `rank`: every record gets a style (`tutorial`: lecture notes, tutorial, introduction to, primer,
   course, review; or `research`) from its title, abstract and comments, and a page count when the
   comments give one. A paper with fewer than `MIN_PAGES` or more than `MAX_PAGES` pages is out.
3. `order`: a seeded shuffle (SHA-256 of seed, set and id) inside each stratum, then the strata
   interleaved in a fixed pattern, so the first N candidates are the draw and a candidate that
   later fails a gate (no source, macros the extractor cannot expand, too few equations) is skipped
   in order, never replaced by choice.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

OAI_URL = "https://oaipmh.arxiv.org/oai"
USER_AGENT = "readablestem-eval/0.1 (research; contact: harishm6250@gmail.com)"
NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "ax": "http://arxiv.org/OAI/arXiv/",
}
# Licence URL (as arXiv writes it) -> short name. Everything else (CC BY-SA, NC, ND, the arXiv
# non-exclusive licence) is skipped: derived data of those papers may not be redistributed freely.
ACCEPTED_LICENCES = {
    "http://creativecommons.org/licenses/by/4.0/": "CC BY 4.0",
    "http://creativecommons.org/licenses/by/3.0/": "CC BY 3.0",
    "http://creativecommons.org/publicdomain/zero/1.0/": "CC0 1.0",
}
MIN_PAGES = 8
MAX_PAGES = 120
# Title (and comments): lecture-note and tutorial wording. Abstract: only the narrow wording, so
# that "we review previous work" does not make a research paper a tutorial.
TUTORIAL_TITLE = re.compile(
    r"\b(lecture notes?|lectures? on|lecture course|tutorial|introduction to|an introduction"
    r"|primer|course notes|notes on|textbook|a gentle|for beginners|a review|review of"
    r"|overview of)\b",
    re.I,
)
TUTORIAL_ABSTRACT = re.compile(
    r"\b(lecture notes?|these notes|lectures? (?:given|delivered)|tutorial|primer|textbook"
    r"|lecture course|pedagogical)\b",
    re.I,
)
PAGES = re.compile(r"\b(\d{1,3})\s*(?:pages|pp\b|p\.)", re.I)


@dataclass
class Record:
    id: str
    title: str
    categories: str
    license: str
    licence_name: str
    created: str
    set_spec: str
    comments: str = ""
    abstract: str = ""
    authors: str = ""
    style: str = "research"
    pages: int | None = None

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Window:
    set_spec: str
    start: str
    end: str


# Sets and the datestamp window harvested for each (kept to a size that finishes in minutes).
# Low-volume sets get a longer window, so that lecture-style papers are not drowned out.
SETS: tuple[Window, ...] = (
    Window("math:math:PR", "2024-07-01", "2025-06-30"),
    Window("stat:stat:TH", "2024-07-01", "2025-06-30"),
    Window("stat:stat:ME", "2025-01-01", "2025-06-30"),
    Window("stat:stat:ML", "2025-03-01", "2025-04-30"),
    Window("cs:cs:LG", "2025-04-01", "2025-04-30"),
    Window("cs:cs:DS", "2024-07-01", "2025-06-30"),
    Window("econ:econ:EM", "2024-01-01", "2025-06-30"),
    Window("q-bio:q-bio:QM", "2024-01-01", "2025-06-30"),
    Window("physics:physics:class-ph", "2024-01-01", "2025-06-30"),
    Window("physics:physics:ed-ph", "2023-01-01", "2025-06-30"),
    Window("physics:physics:flu-dyn", "2025-01-01", "2025-06-30"),
    Window("physics:physics:data-an", "2024-01-01", "2025-06-30"),
    Window("physics:cond-mat:stat-mech", "2025-03-01", "2025-06-30"),
    Window("math:math:NA", "2025-01-01", "2025-06-30"),
    Window("math:math:HO", "2023-01-01", "2025-06-30"),
)


class HarvestError(RuntimeError):
    pass


def _get(client: httpx.Client, params: dict[str, str], pause: float, tries: int = 8) -> str:
    for _ in range(tries):
        response = client.get(OAI_URL, params=params, timeout=120, follow_redirects=True)
        if response.status_code == 200:
            time.sleep(pause)
            return response.text
        if response.status_code in (429, 503):
            wait = float(response.headers.get("retry-after", 20)) + 1
            time.sleep(min(wait, 120))
            continue
        response.raise_for_status()
    raise HarvestError(f"no answer after {tries} tries for {params}")


def _text(node: ET.Element | None, tag: str) -> str:
    found = node.find(f"ax:{tag}", NS) if node is not None else None
    return " ".join((found.text or "").split()) if found is not None else ""


def parse_page(xml: str, set_spec: str) -> tuple[list[Record], int, str | None]:
    """(records with an accepted licence, records seen, resumption token or None)."""
    root = ET.fromstring(xml)
    kept: list[Record] = []
    seen = 0
    for rec in root.iter(f"{{{NS['oai']}}}record"):
        meta = rec.find("oai:metadata/ax:arXiv", NS)
        if meta is None:  # a deleted record
            continue
        seen += 1
        licence = _text(meta, "license")
        if licence not in ACCEPTED_LICENCES:
            continue
        authors = ", ".join(
            f"{_text(a, 'forenames')} {_text(a, 'keyname')}".strip()
            for a in meta.findall("ax:authors/ax:author", NS)
        )
        kept.append(
            Record(
                id=_text(meta, "id"),
                title=_text(meta, "title"),
                categories=_text(meta, "categories"),
                license=licence,
                licence_name=ACCEPTED_LICENCES[licence],
                created=_text(meta, "created"),
                set_spec=set_spec,
                comments=_text(meta, "comments"),
                abstract=_text(meta, "abstract"),
                authors=authors,
            )
        )
    token_node = root.find(".//oai:resumptionToken", NS)
    token = (token_node.text or "").strip() if token_node is not None else ""
    return kept, seen, token or None


def harvest(
    window: Window, cache_dir: Path, *, pause: float = 3.0, log: Callable[[str], None] = print
) -> tuple[list[Record], int]:
    """Records with an accepted licence in the window, and how many records the window held.
    Raw answers are cached under `cache_dir` (keyed by the request), so a second call is offline."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    kept: list[Record] = []
    seen = 0
    params: dict[str, str] = {
        "verb": "ListRecords",
        "metadataPrefix": "arXiv",
        "set": window.set_spec,
        "from": window.start,
        "until": window.end,
    }
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        page = 0
        while True:
            key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:20]
            path = cache_dir / f"{key}.xml"
            if path.is_file():
                xml = path.read_text(encoding="utf-8")
            else:
                xml = _get(client, params, pause)
                path.write_text(xml, encoding="utf-8")
            records, count, token = parse_page(xml, window.set_spec)
            kept += records
            seen += count
            page += 1
            log(f"{window.set_spec} page {page}: {count} records, {len(records)} accepted")
            if not token:
                return kept, seen
            params = {"verb": "ListRecords", "resumptionToken": token}


# ------------------------------------------------------------------------------- ranking


def style_of(record: Record) -> str:
    """`tutorial`: lecture-note wording in the title, comments or (narrowly) the abstract."""
    if TUTORIAL_TITLE.search(f"{record.title}. {record.comments}"):
        return "tutorial"
    return "tutorial" if TUTORIAL_ABSTRACT.search(record.abstract[:800]) else "research"


def pages_of(comments: str) -> int | None:
    found = PAGES.search(comments)
    return int(found.group(1)) if found else None


def annotate(record: Record) -> Record:
    record.style = style_of(record)
    record.pages = pages_of(record.comments)
    return record


def eligible(record: Record) -> bool:
    """A paper with a stated length outside the range is out; one with none is kept (the PDF is
    counted when it is fetched)."""
    return record.pages is None or MIN_PAGES <= record.pages <= MAX_PAGES


def stable_key(seed: str, *parts: str) -> str:
    return hashlib.sha256("|".join((seed, *parts)).encode()).hexdigest()


def order_candidates(records: list[Record], seed: str, pattern: str = "TRTRTRTR") -> list[Record]:
    """Seeded order of the pool: inside each (style, set) stratum by the seeded hash, then the
    sets are taken in turn, and the styles follow `pattern` (T tutorial, R research). Tutorials
    are scarcer than research papers: once the tutorial list is empty the research list goes on."""
    strata: dict[str, dict[str, list[Record]]] = {"tutorial": {}, "research": {}}
    for record in records:
        strata[record.style].setdefault(record.set_spec, []).append(record)
    for style in strata.values():
        for items in style.values():
            items.sort(key=lambda r: stable_key(seed, r.set_spec, r.id))

    def round_robin(style: str) -> Iterator[Record]:
        queues = {s: list(v) for s, v in sorted(strata[style].items())}
        # sets in a seeded order, one paper from each in turn
        names = sorted(queues, key=lambda s: stable_key(seed, style, s))
        while any(queues.values()):
            for name in names:
                if queues[name]:
                    yield queues[name].pop(0)

    streams = {"T": round_robin("tutorial"), "R": round_robin("research")}
    out: list[Record] = []
    position = 0
    while streams:
        letter = pattern[position % len(pattern)]
        position += 1
        stream = streams.get(letter)
        if stream is None:
            letter = next(iter(streams))
            stream = streams[letter]
        try:
            out.append(next(stream))
        except StopIteration:
            del streams[letter]
    return out


@dataclass
class Pool:
    seed: str
    windows: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)


def pool_sha256(pool: dict) -> str:
    return hashlib.sha256(json.dumps(pool, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
