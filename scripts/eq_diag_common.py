"""Shared helpers of the equation diagnostics (`eq_diag_tables.py`, `eq_diag_glyphs.py`,
`eq_diag_unflagged.py`; docs/eval-equation-diagnostics.md).

Everything here reads recorded runs, the cached gold and the cached PDFs of the real-world
splits (`~/.cache/readablestem/realworld`); nothing calls a model.

- `load_split`: a split's gold, where each equation is typed (`float kinds`) and the recorded
  items of one run, read as an HTML reader counts them (the maths cells of a rebuilt table are
  equations, `realworld_report.apply_reading`).
- `page_chars` / `Stream` / `locate`: the characters of a PDF page with their fonts, sizes and
  origins (PyMuPDF `rawdict`), as one normalised stream, and where a gold equation's text sits in
  it. A gold record carries no coordinates, only its page, so it is found by its text.
"""

from __future__ import annotations

import difflib
import importlib.util
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from readablestem.eval import pipeline_eval as pe

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
REPO = Path(__file__).resolve().parent.parent

# The run of each split that is the reference (the one the reports of docs/eval-equation-round3.md
# and docs/eval-equation-rootcause.md read); `--run PART=NAME` picks another.
DEFAULT_RUN = {
    "dev": "rs-allow",
    "dev2": "main0",
    "heldout": "rs",
    "fresh": "fresh-on",
    "fresh2": "after",
    "fresh3": "after",
    "fresh4": "after",
}
PARTS = tuple(DEFAULT_RUN)


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def pooled(results: list[dict]) -> list[dict]:
    """The results to add up: `dev` is left out when `dev2` is there, since dev2 is the same 25
    papers with the gold made again (`realworld_refresh_gold.py`) and a later run."""
    parts = {r["part"] for r in results}
    return [r for r in results if not (r["part"] == "dev" and "dev2" in parts)]


POOL_NOTE = "dev is not added up with dev2 (the same 25 papers, gold made again)"


def parse_runs(specs: list[str]) -> dict[str, str]:
    runs = dict(DEFAULT_RUN)
    for spec in specs:
        part, _, name = spec.partition("=")
        if part not in runs or not name:
            raise SystemExit(f"--run wants PART=NAME with PART one of {', '.join(PARTS)}")
        runs[part] = name
    return runs


@dataclass
class Split:
    part: str
    run: str
    gold: dict[str, dict]
    kinds: dict[str, str]  # gold id -> "text", "table", "figure", "algorithm", ...
    items: list[dict]  # one per paper, the recorded run's items
    pdfs: Path

    def pdf(self, record: dict) -> Path:
        return self.pdfs / f"{record['page_slug']}.pdf"


def load_split(part: str, run: str | None = None, reading: str = "cells") -> Split:
    """The split's gold and the recorded items of `run`. `reading` is `cells` (table maths cells
    count as equations, the reading the plan's numbers use) or `registered`."""
    report = _load_script("realworld_report")
    run = run or DEFAULT_RUN[part]
    gold = {r["id"]: r for r in pe.load_gold(ROOT / "gold" / part) if r.get("kind") == "equation"}
    items_path = ROOT / "runs" / part / run / "items.jsonl"
    items = [
        report.apply_reading(json.loads(line), reading)
        for line in items_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return Split(
        part, run, gold, report.float_kinds(part, gold), items, ROOT / "gold" / part / "pdfs"
    )


def length_class(record: dict) -> str:
    if record["display"] == "block":
        return "display"
    if re.fullmatch(r"[\d.,]+", pe.math_signature(record["mathml"])):
        return "bare number"
    if record["tokens"] == 1:
        return "1 symbol"
    return "2-3 symbols" if record["tokens"] <= 3 else "longer"


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}%" if d else "-"


def table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


# ============================================================================ page characters


_SAME = {"\u2217": "*", "\u2013": "--", "\u2014": "---"}


def signature(text: str) -> str:
    """`pipeline_eval.text_signature` with what a text layer and a gold equation write
    differently made equal: the asterisk operator, dashes, and accents (modifier letters,
    combining marks and spacing marks such as a hat or a bar, which a text layer keeps apart
    from their letter or drops)."""
    sig = pe.text_signature(text)
    for old, new in _SAME.items():
        sig = sig.replace(old, new)
    return "".join(ch for ch in sig if unicodedata.category(ch) not in ("Lm", "Sk", "Mn"))


@dataclass(frozen=True)
class Ch:
    c: str
    font: str  # without the subset tag
    flags: int
    size: float
    origin: tuple[float, float]
    bbox: tuple[float, float, float, float]
    line: int  # index of the text line on the page
    direction: tuple[float, float]


def font_name(raw: str) -> str:
    return raw.split("+", 1)[-1]


def page_chars(page: pymupdf.Page) -> list[Ch]:
    """Every character of the page, in the file's own order (blocks, lines, spans), spaces out."""
    out: list[Ch] = []
    line_no = 0
    for block in page.get_text("rawdict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            direction = tuple(line.get("dir", (1.0, 0.0)))
            for span in line["spans"]:
                for ch in span["chars"]:
                    if not ch["c"].strip():
                        continue
                    out.append(
                        Ch(
                            ch["c"],
                            font_name(span["font"]),
                            int(span["flags"]),
                            float(span["size"]),
                            tuple(ch["origin"]),
                            tuple(ch["bbox"]),
                            line_no,
                            direction,  # type: ignore[arg-type]
                        )
                    )
            line_no += 1
    return out


@dataclass
class Stream:
    """The page's characters as one signature string (`pipeline_eval.text_signature` per
    character) and the character each signature letter came from."""

    chars: list[Ch]
    sig: str = ""
    owner: list[int] = field(default_factory=list)

    @classmethod
    def of(cls, chars: list[Ch]) -> Stream:
        stream = cls(chars)
        parts: list[str] = []
        for i, ch in enumerate(chars):
            piece = signature(ch.c)
            parts.append(piece)
            stream.owner.extend([i] * len(piece))
        stream.sig = "".join(parts)
        return stream

    def window(self, start: int, end: int) -> list[Ch]:
        """The characters whose signature letters are in [start, end), each once."""
        seen = dict.fromkeys(self.owner[start:end])
        return [self.chars[i] for i in seen]

    def line_bounds(self) -> list[tuple[int, int]]:
        """(start, end) in `sig` of every text line."""
        bounds: list[tuple[int, int]] = []
        start = 0
        for i in range(1, len(self.owner) + 1):
            if (
                i == len(self.owner)
                or self.chars[self.owner[i]].line != self.chars[self.owner[i - 1]].line
            ):
                bounds.append((start, i))
                start = i
        return bounds


def gold_signature(record: dict) -> str:
    return signature(pe.math_signature(record["mathml"]))


FUZZY_MIN = 0.9  # share of a text found, in order, in a line (or two) of the page
PARTIAL_MIN = 0.5  # the least share for a partial match: glyphs the text layer lacks


def locate(stream: Stream, sig: str) -> tuple[list[tuple[int, int]], str]:
    """Where `sig` (a signature) sits in the page: every exact occurrence, else the best line
    or pair of lines that holds at least `FUZZY_MIN` of it in order ("fuzzy"), else at least
    `PARTIAL_MIN` ("partial": a floor bracket drawn as a rule, a hat or star the text layer
    lacks); the window is from the first to the last character matched. Texts under four
    letters are looked for exactly only. Returns ([(start, end), ...], how), how being
    "exact", "fuzzy", "partial" or "none"."""
    if not sig:
        return [], "none"
    found: list[tuple[int, int]] = []
    at = stream.sig.find(sig)
    while at != -1:
        found.append((at, at + len(sig)))
        at = stream.sig.find(sig, at + 1)
    if found:
        return found, "exact"
    if len(sig) < 4:
        return [], "none"
    bounds = stream.line_bounds()
    best: tuple[float, tuple[int, int]] | None = None
    for k, (s, e) in enumerate(bounds):
        for end in (e, bounds[k + 1][1] if k + 1 < len(bounds) else e):
            blocks = [
                m
                for m in difflib.SequenceMatcher(
                    None, sig, stream.sig[s:end], autojunk=False
                ).get_matching_blocks()
                if m.size
            ]
            score = sum(m.size for m in blocks) / len(sig)
            if score >= PARTIAL_MIN and (best is None or score > best[0]):
                best = (score, (s + blocks[0].b, s + blocks[-1].b + blocks[-1].size))
    if best is None:
        return [], "none"
    return [best[1]], "fuzzy" if best[0] >= FUZZY_MIN else "partial"


def locate_in_order(
    stream: Stream, records: list[dict]
) -> dict[str, tuple[list[tuple[int, int]], str, tuple[int, int] | None]]:
    """For the gold equations of one page, in source order: the candidates, how they were found
    and the one chosen. A gold equation takes the first occurrence after the previous one's end
    (the source order is the page's order for running text); with none after it, the first."""
    out = {}
    cursor = 0
    for record in sorted(records, key=lambda r: r.get("index_on_page", 0)):
        sig = gold_signature(record)
        cands, how = locate(stream, sig)
        chosen = next((c for c in cands if c[0] >= cursor), cands[0] if cands else None)
        if chosen is not None and any(c[0] >= cursor for c in cands):
            cursor = chosen[1]
        out[record["id"]] = (cands, how, chosen)
    return out


# ============================================================================ glyph evidence

# Fonts that carry maths only (parse.MATH_FONT is the layout's own list), by the family the
# plan names; anything else the layout's list matches is "other maths font".
TEX_FAMILIES = ("CMMI", "CMSY", "CMEX", "MSBM")


def math_family(font: str) -> str | None:
    """CMMI (includes CMMIB), CMSY (includes CMBSY), CMEX, MSBM, or "other" for any other font
    the layout rules already count as a maths font; None for a text font."""
    from readablestem.pipeline.parse import MATH_FONT

    upper = font.upper()
    for family in TEX_FAMILIES:
        if family in upper:
            return family
    if "CMBSY" in upper:
        return "CMSY"
    return "other" if MATH_FONT.search(font) else None


_ITALIC_NAME = re.compile(r"italic|oblique|-it\b|-ital|-slanted|cmti|cmsl", re.I)


def is_italic_text(ch: Ch) -> bool:
    """Italic in a text font (not a maths font): the style flag or an italic name."""
    if math_family(ch.font):
        return False
    return bool(ch.flags & 2) or bool(_ITALIC_NAME.search(ch.font))


class Frame:
    """The page's body size (the most common character size) and an index of its body-size
    characters, to find the baseline a script glyph sits against."""

    def __init__(self, chars: list[Ch]) -> None:
        sizes = Counter(round(c.size, 1) for c in chars)
        self.body = sizes.most_common(1)[0][0] if sizes else 10.0
        self.grid: dict[int, list[Ch]] = {}
        for c in chars:
            if c.size >= SMALLER * self.body:
                self.grid.setdefault(int(c.origin[1] // self.body), []).append(c)

    def baseline(self, ch: Ch) -> float | None:
        """Origin y of the body-size character nearest to `ch` along the line, within three body
        sizes sideways and under three quarters of one up or down: the line a script hangs on.
        None when there is none."""
        row = int(ch.origin[1] // self.body)
        best: tuple[float, float] | None = None
        for r in (row - 1, row, row + 1):
            for other in self.grid.get(r, ()):
                dy = abs(other.origin[1] - ch.origin[1])
                dx = abs(other.origin[0] - ch.origin[0])
                if (
                    dy <= 0.75 * self.body
                    and dx <= 3 * self.body
                    and (best is None or dx < best[0])
                ):
                    best = (dx, other.origin[1])
        return best[1] if best else None


RAISE = 0.12  # a glyph this share of the body size above (or below) the baseline ...
SMALLER = 0.85  # ... and set smaller than this share of the body size is a script


def page_frame(chars: list[Ch]) -> Frame:
    return Frame(chars)


def script_offset(ch: Ch, frame: Frame) -> str | None:
    """ "raised" or "lowered": a script glyph against the baseline it hangs on; None otherwise."""
    if ch.size >= SMALLER * frame.body:
        return None
    base = frame.baseline(ch)
    if base is None:
        return None
    dy = base - ch.origin[1]
    if dy >= RAISE * frame.body:
        return "raised"
    if dy <= -RAISE * frame.body:
        return "lowered"
    return None


ANY = "any evidence"
EVIDENCE = (
    ANY,
    "any maths font",
    *TEX_FAMILIES,
    "other maths font",
    "italic text font",
    "raised",
    "lowered",
)


def evidence_of(chars: list[Ch], frame: Frame) -> dict[str, int]:
    """How many of `chars` carry each kind of glyph evidence."""
    count: Counter[str] = Counter()
    for ch in chars:
        family = math_family(ch.font)
        if family:
            count["any maths font"] += 1
            count["other maths font" if family == "other" else family] += 1
        elif is_italic_text(ch):
            count["italic text font"] += 1
        offset = script_offset(ch, frame)
        if offset:
            count[offset] += 1
        if family or offset or is_italic_text(ch):
            count[ANY] += 1
    return dict(count)


def difflib_ops(a: list[str], b: list[str]) -> list[tuple[str, int, int, int, int]]:
    return [
        op
        for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
        if op[0] != "equal"
    ]
