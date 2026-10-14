"""Build the static reviewer demo site.

    uv run python site/build.py [--out site/_build]

Everything on the site comes from files committed in this repository: the stored results
of past conversions (site/examples/*, and the demo under src/a11y_stem/web/static/demo),
the committed PROOF.md, the text and the stylesheet in site/. The build calls no model,
fetches nothing, reads no clock and reads no environment variable, so the same inputs give
the same bytes. docs/site.md describes the pages, the inputs and how to add an example.

The module is loaded by path in tests (a folder called `site` would shadow the standard
library module of that name if it were put on the import path).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import jinja2
import pymupdf

from a11y_stem.core.types import DocumentResult, Element, Fix, Kind
from a11y_stem.web import messages, speech_diff
from a11y_stem.web.compare import band_clip, compare_equation, context_clip
from a11y_stem.web.review import group_fixes, item_labels
from a11y_stem.web.routes.review import reject_words
from a11y_stem.web.views import KIND_LABELS

SITE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SITE_DIR.parent
DEFAULT_OUT = SITE_DIR / "_build"

REPO_URL = "https://github.com/harishm17/a11y-stem"
STEMCHECK_URL = "https://github.com/harishm17/stemcheck"
AUTHOR = "Harish Manoharan"
CC_BY = "https://creativecommons.org/licenses/by/4.0/"

# Rendering. A page is shown about 600 px wide; 1.5x keeps it sharp on a high-density screen.
PAGE_ZOOM = 1.5
CROP_MAX_SIDE_PX = 1400
CROP_MAX_ZOOM = 4.0
CROP_MIN_ZOOM = 2.0
CROP_CSS_PX_PER_PT = 2.0
# The demo wrapper links back to the app ("/"); on this site it goes back to the example page.
APP_HOME_LINK = '<a href="/">Back to a11y-stem</a>'
SITE_BACK_LINK = '<a href="index.html">Back to the example page</a>'
HEADING_BASE = 4  # converted headings are shown from <h4>: the page has h1, h2 and h3 above

VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
     "track", "wbr"}
)  # fmt: skip

# Plain words for the page-level checks (pipeline/html_checks.py lists the same rules).
HTML_CHECK_WORDS = {
    "html.lang": "The page declares its language.",
    "html.title": "The page has a title.",
    "html.main": "There is exactly one main landmark.",
    "html.heading_order": "No heading skips a level.",
    "html.heading_text": "Every heading has text.",
    "html.img_alt": "Every image has an alt attribute.",
    "html.unique_ids": "No id is used twice.",
    "html.list_structure": "Lists contain only list items.",
    "html.math_display": "Every equation says whether it is inline or displayed.",
    "html.self_contained": "Nothing is loaded from outside the file.",
    "html.wording": "The tool's own notes make no claim of conformance.",
    "html.link_text": "Every link has text.",
    "html.link_href": "Every link goes somewhere safe.",
    "html.lang_tags": "Language tags are well formed.",
    "html.text_contrast": "Coloured text has at least 4.5:1 contrast.",
}


# =============================================================================== markdown


def plain(text: str) -> str:
    """Markdown emphasis and code marks removed: table cells and bullets as plain text."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return text.strip()


@dataclass
class MdTable:
    section: str  # the nearest heading above
    caption: str  # the nearest bold line above, if any
    header: list[str]
    rows: list[list[str]]


@dataclass
class MdDoc:
    title: str = ""
    banners: list[str] = field(default_factory=list)
    tables: list[MdTable] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)  # heading -> its lines


def _cells(line: str) -> list[str]:
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") else inner
    return [plain(c) for c in inner.split("|")]


def parse_markdown(text: str) -> MdDoc:
    """The few things the site reads from PROOF.md: the title, the opening quotes, every
    table (with its heading and bold caption) and the lines under each heading."""
    doc = MdDoc()
    lines = text.splitlines()
    heading = ""
    caption = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("# ") and not doc.title:
            doc.title = plain(line[2:])
            heading = doc.title
        elif line.startswith("#"):
            heading = plain(line.lstrip("#"))
            caption = ""
        elif line.startswith("> ") and not heading.startswith("Status"):
            if heading == doc.title:
                doc.banners.append(plain(line[2:]))
        elif re.fullmatch(r"\*\*[^*]+\*\*", line.strip()):
            caption = plain(line)
        elif (
            line.lstrip().startswith("|")
            and i + 1 < len(lines)
            and re.fullmatch(r"\s*\|?[\s:|-]+\|[\s:|-]*", lines[i + 1])
        ):
            header = _cells(line)
            rows: list[list[str]] = []
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            doc.tables.append(MdTable(heading, caption, header, rows))
            continue
        doc.sections.setdefault(heading, []).append(line)
        i += 1
    return doc


@dataclass
class Headline:
    label: str
    value: str
    count: str
    interval: str
    metric: str
    note: str = ""


# (what the number is, in plain words; where to find it: table text, first cell; note)
HEADLINE_SPECS: list[tuple[str, str, str, str]] = [
    (
        "Equations that match the answer key after the whole conversion",
        "document task",
        "end_to_end_exact_lenient (all gold equations)",
        "Exact match of the MathML, with a lenient scorer. Strict, and a wrong digit in a long "
        "equation counts as wrong.",
    ),
    (
        "The same, leaving out equations where the answer key's own markup defeats exact match",
        "document task",
        "end_to_end_exact_lenient, without G1 equations",
        "G1: the answer key packs operators into text, so a correct answer can fail on markup.",
    ),
    (
        "Wrong equations that no decision pointed at, per 100 equations",
        "document task",
        "unflagged_wrong_per_100",
        "Wrong, and not among the places the review screen asks a person about.",
    ),
    (
        "Equations in the PDF that the tool found",
        "document task",
        "detection_recall",
        "Finding an equation is not the same as getting it right.",
    ),
    (
        "Headings: F1 against the answer key",
        "Headings found",
        "heading F1",
        "Made without a model. The rules were tuned on these pages.",
    ),
    (
        "Figures whose alt text a model judge accepted",
        "Alt text for each figure",
        "judge_pass_rate",
        "Agreement with a model judge, not accuracy. The judge is not yet checked against people.",
    ),
    (
        "Decisions asked of a person, per PDF page",
        "Review load",
        "Required decisions per PDF page",
        "A decision is one thing a person must look at. Repeated headings count as one.",
    ),
]


def _col(header: list[str], *names: str) -> int | None:
    for name in names:
        for i, h in enumerate(header):
            if h.lower().startswith(name.lower()):
                return i
    return None


def find_headline(doc: MdDoc, table_hint: str, first_cell: str) -> Headline | None:
    for table in doc.tables:
        if table_hint.lower() not in f"{table.section} {table.caption}".lower():
            continue
        for row in table.rows:
            if row and row[0].startswith(first_cell):
                value_col = _col(table.header, "Pooled", "Value")
                if value_col is None or value_col >= len(row):
                    continue
                count_col = _col(table.header, "Count")
                interval_col = _col(table.header, "Cluster 95%")
                count = row[count_col] if count_col is not None and count_col < len(row) else ""
                interval = (
                    row[interval_col]
                    if interval_col is not None and interval_col < len(row)
                    else ""
                )
                return Headline("", row[value_col], count, interval, first_cell)
    return None


@dataclass
class Proof:
    available: bool = False
    title: str = ""
    kind: str = "unknown"  # "development", "held-out" or "unknown": as PROOF.md says
    banners: list[str] = field(default_factory=list)
    status: list[tuple[str, str]] = field(default_factory=list)
    headlines: list[Headline] = field(default_factory=list)
    parts: list[tuple[str, str]] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)
    file_name: str = "PROOF.md"


def read_proof(path: Path) -> Proof:
    """The headline numbers of PROOF.md, whatever set it holds. Anything not found is left
    out rather than guessed; the label of the set comes from the file's own words."""
    if not path.is_file():
        return Proof()
    doc = parse_markdown(path.read_text(encoding="utf-8"))
    words = f"{doc.title} {' '.join(doc.banners)}".lower()
    if "not held out" in words or "development" in words:
        kind = "development"
    elif "held-out" in words or "held out" in words:
        kind = "held-out"
    else:
        kind = "unknown"
    proof = Proof(available=True, title=doc.title, kind=kind, banners=doc.banners)
    for table in doc.tables:
        if table.section == "Status" and table.header[:1] == [""]:
            proof.status = [(r[0], r[1]) for r in table.rows if len(r) >= 2]
        if table.caption == "Parts of this proof":
            proof.parts = [(r[0], r[1]) for r in table.rows if len(r) >= 2]
    for label, hint, first_cell, note in HEADLINE_SPECS:
        found = find_headline(doc, hint, first_cell)
        if found is not None:
            found.label = label
            found.note = note
            proof.headlines.append(found)
    for line in doc.sections.get("What these numbers leave out", []):
        if line.startswith("- "):
            proof.limits.append(plain(line[2:]))
    return proof


# =============================================================================== html


class _Blocks(HTMLParser):
    """The source text of each direct child of <main>."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=False)
        self.text = text
        self._starts = [0] + [m.end() for m in re.finditer("\n", text)]
        self.blocks: list[str] = []
        self._in_main = False
        self._depth = 0
        self._from = 0
        self.balanced = True

    def _offset(self) -> int:
        line, col = self.getpos()
        return self._starts[line - 1] + col

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._in_main:
            if tag == "main":
                self._in_main = True
                self._depth = 0
            return
        offset = self._offset()
        if self._depth == 0:
            self._from = offset
        if tag in VOID:
            if self._depth == 0:
                self.blocks.append(self.text[offset : offset + len(self.get_starttag_text() or "")])
        else:
            self._depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._in_main and self._depth == 0:
            offset = self._offset()
            self.blocks.append(self.text[offset : offset + len(self.get_starttag_text() or "")])

    def handle_endtag(self, tag: str) -> None:
        if not self._in_main or tag in VOID:
            return
        if tag == "main" and self._depth == 0:
            self._in_main = False
            return
        self._depth -= 1
        if self._depth == 0:
            end = self.text.index(">", self._offset()) + 1
            self.blocks.append(self.text[self._from : end])
        elif self._depth < 0:
            self.balanced = False
            self._depth = 0


def split_main(document: str) -> list[str]:
    parser = _Blocks(document)
    parser.feed(document)
    parser.close()
    if not parser.balanced:
        raise ValueError("converted HTML is not balanced inside <main>")
    return parser.blocks


def block_page(block: str) -> int | None:
    found = re.search(r'\bid="p(\d+)-e\d+"', block)
    return int(found.group(1)) if found else None


def blocks_by_page(document: str) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    current = 1
    for block in split_main(document):
        page = block_page(block)
        if page is not None:
            current = page
        out.setdefault(current, []).append(block)
    return out


def shift_headings(fragment: str) -> str:
    """Heading levels re-set to nest under the page's own h3: the first heading becomes
    an h4 and none goes more than one level below the one before it."""
    levels = [int(m.group(1)) for m in re.finditer(r"<h([1-6])\b", fragment)]
    if not levels:
        return fragment
    low = min(levels)
    mapped: list[int] = []
    previous = HEADING_BASE - 1
    for level in levels:
        new = min(HEADING_BASE + (level - low), previous + 1, 6)
        mapped.append(new)
        previous = new
    starts = iter(zip(levels, mapped, strict=True))
    stack: list[int] = []

    def swap(match: re.Match[str]) -> str:
        closing, level = match.group(1), match.group(2)
        if closing:
            return f"</h{stack.pop() if stack else level}"
        original, new = next(starts)
        stack.append(new)
        return f'<h{new} class="lv{original}"'

    # Opening tags are rewritten as "<hN class=..." and the rest of the tag follows; a tag
    # that already has a class gets a second attribute, which browsers ignore, so the
    # converted headings (which carry only an id) are the case this handles.
    return re.sub(r"<(/?)h([1-6])\b", swap, fragment)


def extract_images(fragment: str, out: Out, base: str) -> str:
    """data: images become files in `base`/img/, named by the hash of their bytes."""

    def swap(match: re.Match[str]) -> str:
        kind, payload = match.group(1).lower(), match.group(2)
        raw = base64.b64decode(payload)
        ext = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "gif": "gif", "webp": "webp"}.get(
            kind, "bin"
        )
        name = f"img/{hashlib.sha256(raw).hexdigest()[:16]}.{ext}"
        out.put(f"{base}/{name}", raw)
        return f'src="{name}"'

    return re.sub(r'src="data:image/([a-zA-Z+.-]+);base64,([^"]+)"', swap, fragment)


MARKER = ".a11y-stem-site-build"


class Out:
    """The files of the site, kept in memory until `write`."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def put(self, rel: str, data: bytes | str) -> None:
        self.files[rel] = data.encode("utf-8") if isinstance(data, str) else data

    def write(self, root: Path) -> None:
        """Replace `root` with the files. A folder that was not made by this script (it has
        no marker file) and is not empty is refused, so a wrong --out cannot delete work."""
        if root.exists():
            if any(root.iterdir()) and not (root / MARKER).exists():
                raise SystemExit(f"refusing to replace {root}: not a site build folder")
            shutil.rmtree(root)
        root.mkdir(parents=True)
        (root / MARKER).write_text("generated by site/build.py\n", encoding="utf-8")
        for rel in sorted(self.files):
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.files[rel])


def retarget_links(fragment: str, here: set[str], full_page: str) -> str:
    """#id links that point outside this excerpt go to the same id in the full page."""

    def swap(match: re.Match[str]) -> str:
        target = match.group(1)
        return match.group(0) if target in here else f'href="{full_page}#{target}"'

    return re.sub(r'href="#([^"]*)"', swap, fragment)


def scope_css(css: str, scope: str = ".converted") -> list[str]:
    """The tool's stylesheet with every rule limited to `scope`. Returns whole rules."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return _scope_rules(css, scope)


def _matching_brace(text: str, start: int) -> int:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced braces in CSS")


def _scope_selector(selector: str, scope: str) -> str:
    selector = selector.strip()
    head = re.match(r"(:root|html|body|main)(?=$|[\s.:#\[>+~])", selector)
    if head:
        return (scope + selector[head.end() :]).strip()
    if selector.startswith(":"):
        return f"{scope} {selector}"
    return f"{scope} {selector}"


def _scope_rules(css: str, scope: str) -> list[str]:
    out: list[str] = []
    i = 0
    while True:
        j = css.find("{", i)
        if j < 0:
            break
        end = _matching_brace(css, j)
        prelude, body = css[i:j].strip(), css[j + 1 : end]
        if prelude.startswith(("@media", "@supports")):
            inner = _scope_rules(body, scope)
            out.append(f"{prelude} {{\n" + "\n".join(inner) + "\n}")
        elif prelude.startswith("@"):
            out.append(f"{prelude} {{{body}}}")
        else:
            selectors = ", ".join(_scope_selector(s, scope) for s in prelude.split(","))
            out.append(f"{selectors} {{{body.strip()}}}")
        i = end + 1
    return out


def style_of(document: str) -> str:
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", document, flags=re.S))


# =============================================================================== pdf


class Pdf:
    """A PDF opened once: whole pages and clips as PNG bytes."""

    def __init__(self, path: Path) -> None:
        self.doc = pymupdf.open(path)

    def page_size(self, index: int) -> tuple[float, float]:
        rect = self.doc[index].rect
        return rect.width, rect.height

    def page_png(self, index: int) -> bytes:
        pix = self.doc[index].get_pixmap(matrix=pymupdf.Matrix(PAGE_ZOOM, PAGE_ZOOM), alpha=False)
        return pix.tobytes("png")

    def clip_png(
        self, index: int, box: tuple[float, float, float, float]
    ) -> tuple[bytes, int, int]:
        x0, y0, x1, y1 = box
        width, height = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
        zoom = min(CROP_MAX_ZOOM, CROP_MAX_SIDE_PX / max(width, height))
        zoom = max(zoom, min(CROP_MIN_ZOOM, CROP_MAX_SIDE_PX / max(width, height)))
        pix = self.doc[index].get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom), clip=pymupdf.Rect(x0, y0, x1, y1), alpha=False
        )
        # Shown at 2 CSS px per point; the file has more pixels, for a sharp image.
        return (
            pix.tobytes("png"),
            round(width * CROP_CSS_PX_PER_PT),
            round(height * CROP_CSS_PX_PER_PT),
        )


# =============================================================================== examples


@dataclass
class Card:
    """One decision as the review screen asks it, in static form."""

    anchor: str
    kind: str
    title: str
    meta: str
    tier: str  # "required" or "optional"
    crop: str | None = None
    crop_size: tuple[int, int] = (0, 0)
    crop_alt: str = ""
    mathml: str | None = None
    proposal_text: str | None = None
    proposal_label: str = "Proposal"
    said: list[speech_diff.Segment] | None = None
    said_plain: str | None = None
    pdf_said: list[speech_diff.Segment] | None = None
    pdf_said_plain: str = ""
    pdf_text: str | None = None
    diff_rows: list[Any] = field(default_factory=list)
    same: list[str] = field(default_factory=list)
    why: list[str] = field(default_factory=list)
    choices: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class SpeechRow:
    example: str
    element_id: str
    page: int
    pdf_text: str
    pdf_said: str
    mathml: str
    html_said: str
    crop: str
    crop_size: tuple[int, int]
    notes: list[str] = field(default_factory=list)


@dataclass
class Example:
    id: str
    meta: dict[str, Any]
    demo: dict[str, Any]
    result: DocumentResult
    html_path: Path
    pdf_path: Path
    report_path: Path
    document: str
    models: list[str]
    cards: list[Card] = field(default_factory=list)
    optional_lines: list[str] = field(default_factory=list)
    speech: list[SpeechRow] = field(default_factory=list)
    pairs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def title(self) -> str:
        return str(self.meta["title"])

    @property
    def base(self) -> str:
        return f"examples/{self.id}"


def load_example(folder: Path) -> Example:
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    base = (folder / meta.get("artefacts", ".")).resolve()
    report_path = base / meta.get("report", "report.json")
    html_path = base / meta.get("html", "converted.html")
    pdf_path = base / meta.get("pdf", "source.pdf")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    result = DocumentResult.model_validate(report["result"])
    models = sorted({c.model for f in result.fixes for c in f.calls})
    return Example(
        id=str(meta["id"]),
        meta=meta,
        demo=report["demo"],
        result=result,
        html_path=html_path,
        pdf_path=pdf_path,
        report_path=report_path,
        document=html_path.read_text(encoding="utf-8").replace(APP_HOME_LINK, SITE_BACK_LINK),
        models=models,
    )


def load_examples(root: Path) -> list[Example]:
    examples = [load_example(p.parent) for p in sorted(root.glob("*/meta.json"))]
    return sorted(examples, key=lambda e: (e.meta.get("order", 99), e.id))


def provider_words(example: Example) -> str:
    names = {"anthropic": "Claude", "openai": "OpenAI"}
    client = str(example.demo.get("client", ""))
    name = names.get(client, client)
    return f"{name} ({', '.join(example.models)})" if example.models else name


def why_lines(fix: Fix) -> list[str]:
    lines: list[str] = []
    for check in fix.failed_checks:
        text = messages.explain_check(check).text
        if text not in lines:
            lines.append(text)
    if fix.reason:
        text = messages.explain_reason(fix.reason).text
        if text not in lines:
            lines.append(text)
    if not lines:
        lines.append(
            "The model was not sure enough for the tool to accept this without a person "
            "looking at it."
        )
    return lines


def _pad(
    box: tuple[float, float, float, float], pad: float, size: tuple[float, float] | None
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    width, height = size if size else (float("inf"), float("inf"))
    return max(0.0, x0 - pad), max(0.0, y0 - pad), min(width, x1 + pad), min(height, y1 + pad)


def _crop(pdf: Pdf, out: Out, example: Example, element: Element, box: Any) -> tuple[str, int, int]:
    data, width, height = pdf.clip_png(element.page, box)
    name = f"crops/{element.id}.png"
    out.put(f"{example.base}/{name}", data)
    return name, width, height


def build_cards(example: Example, pdf: Pdf, out: Out) -> None:
    """The decisions a person would see, the optional notes, and the crops that go with them."""
    result = example.result
    elements: dict[str, Element] = {e.id: e for e in result.elements}
    labels = item_labels(result)
    sizes = dict(result.page_sizes)
    for group in group_fixes(result):
        for unit in group.units:
            element = elements.get(unit[0].element_id)
            if element is not None:
                example.cards.append(
                    _card(example, pdf, out, group.kind, unit, element, elements, labels, sizes)
                )
        for fix in group.optional:
            element = elements.get(fix.element_id)
            if element is not None:
                label = labels.get(fix.element_id, KIND_LABELS.get(fix.kind, "Item"))
                note = " ".join(why_lines(fix))
                example.optional_lines.append(f"{label}, page {element.page + 1}: {note}")


def _card(
    example: Example,
    pdf: Pdf,
    out: Out,
    kind: Kind,
    unit: list[Fix],
    element: Element,
    elements: dict[str, Element],
    labels: dict[str, str],
    sizes: dict[int, tuple[float, float]],
) -> Card:
    fix = unit[0]
    page_size = sizes.get(element.page)
    label = labels.get(fix.element_id, KIND_LABELS.get(kind, "Item"))
    pages = sorted({elements[f.element_id].page + 1 for f in unit if f.element_id in elements})
    where = f"page {pages[0]}" if len(pages) == 1 else "pages " + ", ".join(map(str, pages))
    confidence = (
        f" Confidence: {messages.confidence_label(fix.confidence)}."
        if fix.confidence is not None
        else ""
    )
    words = reject_words(kind, element, fix)
    card = Card(
        anchor=f"decision-{element.id}",
        kind=KIND_LABELS.get(kind, "Item"),
        title=label,
        meta=f"On {where}.{confidence}",
        tier="optional" if fix.review == "optional" else "required",
        why=why_lines(fix),
    )
    if kind is Kind.EQUATION and fix.equation is not None:
        card.crop, *size = _crop(pdf, out, example, element, context_clip(element, page_size))
        card.crop_size = (size[0], size[1])
        card.crop_alt = f"{label} as printed in the PDF, with the words around it."
        card.mathml = fix.equation.mathml
        card.said_plain = fix.equation.speech
        diff = speech_diff.spoken_diff(fix.equation.speech, element.text)
        if diff is not None:
            card.said, card.pdf_said = diff.said, diff.pdf_said
        card.pdf_text = element.text or None
        card.pdf_said_plain = speech_diff.read_text_layer(element.text) if element.text else ""
        compare = compare_equation(element, fix)
        card.diff_rows, card.same = list(compare.rows), list(compare.same)
        card.choices = [
            ("Accept", "The proposed equation goes into the document."),
            ("Edit the LaTeX", "Type a corrected equation. It is checked again."),
            (words.button, words.consequence),
        ]
    elif kind is Kind.FIGURE and fix.figure is not None:
        box = _pad(element.bbox, 6.0, page_size)
        card.crop, *size = _crop(pdf, out, example, element, box)
        card.crop_size = (size[0], size[1])
        card.crop_alt = f"{label} as it is in the PDF."
        card.proposal_label = "Proposed alt text"
        card.proposal_text = fix.figure.alt
        card.choices = [
            ("Accept", "The alt text goes into the document."),
            ("Edit the alt text", "Write your own. It is checked again."),
            (words.button, words.consequence),
        ]
    elif kind is Kind.HEADING and fix.heading is not None:
        card.crop, *size = _crop(pdf, out, example, element, band_clip(element, page_size))
        card.crop_size = (size[0], size[1])
        card.crop_alt = f"The line “{element.text or ''}” in the PDF, with the lines around it."
        text = elements[unit[0].element_id].text or fix.heading.text
        card.title = (
            f"Heading: “{text}”, {len(unit)} times" if len(unit) > 1 else f"{label}: “{text}”"
        )
        card.proposal_text = f"A heading at level {fix.heading.level}: “{fix.heading.text}”."
        if len(unit) > 1:
            card.proposal_text += f" One decision covers all {len(unit)}."
        card.choices = [
            (
                f"Keep as headings at level {fix.heading.level}",
                "They stay in the outline that a screen reader lists.",
            ),
            ("Choose another level", "Pick the level for all of them."),
            (words.button, words.consequence),
        ]
    return card


def observations(pdf_said: str, html_said: str, asked: bool = False) -> list[str]:
    """Plain statements about how the two readings differ, each one checked against the
    words of the readings themselves (no guess about what a screen reader does)."""
    notes: list[str] = []
    if asked:
        notes.append(
            "The two readings differ in a symbol or its style, and the proposal may be the "
            "wrong one. The tool asks a person about this equation (see the decisions on its "
            "example page)."
        )
    spelled = re.search(r"\b(?:[a-z] ){3,}[a-z]\b", pdf_said)
    if spelled:
        notes.append(
            f"The PDF's text is read letter by letter in places (“{spelled.group(0)}”). "
            "In the HTML a word stays a word."
        )
    if any(w in html_said for w in ("over", "the fraction")) and not any(
        w in pdf_said for w in ("over", "the fraction")
    ):
        notes.append(
            "The HTML marks a fraction, so a listener hears where it starts and ends. The PDF's "
            "text runs the top and the bottom together."
        )
    if "sub " in f"{html_said} " and "sub " not in f"{pdf_said} ":
        notes.append("Subscripts are marked as subscripts in the HTML.")
    if "square root" in html_said and "square root" not in pdf_said:
        notes.append("The HTML marks where a square root begins and ends.")
    if html_said.startswith("row 1:") or " row 2:" in html_said:
        notes.append("A multi-line working is read line by line in the HTML.")
    return notes


def build_speech(example: Example, pdf: Pdf, out: Out) -> None:
    """The equations named in the example's meta, each with the PDF's reading and the HTML's."""
    elements = {e.id: e for e in example.result.elements}
    fixes = {f.element_id: f for f in example.result.fixes}
    sizes = dict(example.result.page_sizes)
    for eid in example.meta.get("speech", []):
        fix, element = fixes.get(eid), elements.get(eid)
        if fix is None or element is None or fix.equation is None or not element.text:
            continue
        crop, width, height = _crop(
            pdf, out, example, element, context_clip(element, sizes.get(element.page))
        )
        example.speech.append(
            SpeechRow(
                example=example.id,
                element_id=eid,
                page=element.page + 1,
                pdf_text=element.text,
                pdf_said=speech_diff.read_text_layer(element.text),
                mathml=fix.equation.mathml,
                html_said=fix.equation.speech,
                crop=f"{example.base}/{crop}",
                crop_size=(width, height),
                notes=observations(
                    speech_diff.read_text_layer(element.text),
                    fix.equation.speech,
                    asked=fix.review == "required"
                    and speech_diff.spoken_diff(fix.equation.speech, element.text) is not None,
                ),
            )
        )


def build_pairs(example: Example, pdf: Pdf, out: Out) -> None:
    """For each page named in the meta: the page as printed, and the converted blocks of it."""
    by_page = blocks_by_page(example.document)
    for number in example.meta.get("pages", []):
        fragment = "\n".join(by_page.get(number, []))
        here = set(re.findall(r'\bid="([^"]+)"', fragment))
        fragment = retarget_links(fragment, here, "converted.html")
        fragment = extract_images(fragment, out, example.base)
        fragment = shift_headings(fragment)
        name = f"pages/page-{number:02d}.png"
        out.put(f"{example.base}/{name}", pdf.page_png(number - 1))
        width, height = pdf.page_size(number - 1)
        example.pairs.append(
            {
                "number": number,
                "image": name,
                "html": fragment,
                "width": round(width * PAGE_ZOOM),
                "height": round(height * PAGE_ZOOM),
                "empty": not by_page.get(number),
            }
        )


def summarise(example: Example) -> dict[str, Any]:
    result = example.result
    stats = result.stats
    by_kind = stats.by_kind
    facts = result.source_facts
    asked = stats.required_decisions
    return {
        "pages": stats.pages,
        "headings": by_kind.get("heading", 0),
        "equations": by_kind.get("equation", 0),
        "figures": by_kind.get("figure", 0),
        "accepted": stats.by_status.get("accepted", 0),
        "asked": asked,
        "optional": stats.by_review.get("optional", 0),
        "cost": f"{stats.cost_usd:.3f}",
        "calls": stats.model_calls,
        "source": facts,
    }


def check_findings(example: Example) -> list[dict[str, Any]]:
    """Failed checks across all items: how often, and what happened to each item."""
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for fix in example.result.fixes:
        for check in fix.failed_checks:
            key = (check.name, check.code or "")
            slot = counts.setdefault(key, {"required": 0, "optional": 0, "none": 0})
            slot[fix.review if fix.review in slot else "none"] += 1
    rows = []
    for (name, code), slot in sorted(counts.items(), key=lambda kv: (-sum(kv[1].values()), kv[0])):
        text = messages.explain_check(
            next(
                c
                for f in example.result.fixes
                for c in f.failed_checks
                if c.name == name and (c.code or "") == code
            )
        ).text
        rows.append(
            {
                "text": text,
                "code": f"{name}: {code}" if code else name,
                "count": sum(slot.values()),
                "asked": slot["required"],
                "note": slot["optional"],
                "accepted": slot["none"],
            }
        )
    return rows


def page_checks(example: Example) -> list[dict[str, Any]]:
    return [
        {
            "text": HTML_CHECK_WORDS.get(c.name, c.name),
            "passed": c.passed,
            "severity": c.severity,
            "name": c.name,
        }
        for c in example.result.html_checks
    ]


def notices_of(example: Example) -> list[str]:
    return [messages.explain_notice(n).text for n in example.result.notices]


def prepare_example(example: Example, out: Out) -> None:
    pdf = Pdf(example.pdf_path)
    build_cards(example, pdf, out)
    build_speech(example, pdf, out)
    build_pairs(example, pdf, out)
    out.put(f"{example.base}/converted.html", example.document)
    out.put(f"{example.base}/source.pdf", example.pdf_path.read_bytes())
    out.put(f"{example.base}/report.json", example.report_path.read_bytes())


# =============================================================================== pages


def root_of(path: str) -> str:
    depth = path.count("/")
    return "../" * depth


NAV = [
    ("home", "Home", "index.html"),
    ("examples", "Examples", "examples/index.html"),
    ("before-after", "Before and after", "before-after/index.html"),
    ("stemcheck", "stemcheck", "stemcheck/index.html"),
    ("method", "Method and limits", "method/index.html"),
    ("attribution", "Attribution", "attribution/index.html"),
]


def make_env() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(SITE_DIR / "templates"),
        autoescape=jinja2.select_autoescape(["html"]),
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    return env


def converted_css(examples: list[Example]) -> str:
    seen: list[str] = []
    for example in examples:
        for rule in scope_css(style_of(example.document)):
            if rule not in seen:
                seen.append(rule)
    return (
        "/* The tool's own stylesheet for its output, copied from the converted pages and\n"
        "   limited to .converted by site/build.py. */\n" + "\n".join(seen) + "\n"
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_site(out_dir: Path, *, proof_path: Path | None = None) -> list[str]:
    """Build every page into `out_dir`. Returns the paths written, relative to it."""
    out = Out()
    examples = load_examples(SITE_DIR / "examples")
    for example in examples:
        prepare_example(example, out)
    proof = read_proof(proof_path or REPO_ROOT / "PROOF.md")
    stemcheck = read_json(SITE_DIR / "stemcheck" / "meta.json")
    sets = read_set_limits(REPO_ROOT / "proof" / "sets")

    env = make_env()
    common: dict[str, Any] = {
        "nav": NAV,
        "repo_url": REPO_URL,
        "stemcheck_url": STEMCHECK_URL,
        "author": AUTHOR,
        "cc_by": CC_BY,
    }

    def page(path: str, template: str, current: str, title: str, **context: Any) -> None:
        text = env.get_template(template).render(
            **common, root=root_of(path), current=current, title=title, **context
        )
        out.put(path, text)

    page(
        "index.html", "home.html", "home", "a11y-stem reviewer demo",
        proof=proof, examples=examples, stemcheck=stemcheck,
    )  # fmt: skip
    page(
        "examples/index.html", "examples_index.html", "examples", "Worked examples",
        examples=examples, summaries={e.id: summarise(e) for e in examples},
        providers={e.id: provider_words(e) for e in examples},
    )  # fmt: skip
    for example in examples:
        page(
            f"{example.base}/index.html", "example.html", "examples", example.title,
            example=example, summary=summarise(example), provider=provider_words(example),
            findings=check_findings(example), page_checks=page_checks(example),
            notices=notices_of(example), speech_rows=example.speech[:3],
        )  # fmt: skip
    page(
        "before-after/index.html",
        "before_after.html",
        "before-after",
        "Before and after",
        examples=[e for e in examples if e.speech],
    )
    page(
        "stemcheck/index.html",
        "stemcheck.html",
        "stemcheck",
        "A second look with stemcheck",
        stemcheck=stemcheck,
    )
    out.put(
        "stemcheck/sample-report.html", (SITE_DIR / "stemcheck" / "sample-report.html").read_bytes()
    )
    page(
        "method/index.html",
        "method.html",
        "method",
        "Method and limits",
        proof=proof,
        sets=sets,
    )
    page(
        "attribution/index.html",
        "attribution.html",
        "attribution",
        "Attribution",
        examples=examples,
        stemcheck=stemcheck,
        proof=proof,
    )
    page("404.html", "not_found.html", "", "Page not found")
    out.put("assets/site.css", (SITE_DIR / "assets" / "site.css").read_text(encoding="utf-8"))
    out.put("assets/converted.css", converted_css(examples))
    out.put(".nojekyll", "")
    out.write(out_dir)
    return sorted(out.files)


def read_set_limits(folder: Path) -> list[dict[str, Any]]:
    """The limits that each proof set states about itself (proof/sets/*.json)."""
    rows = []
    for path in sorted(folder.glob("*.json")):
        data = read_json(path)
        if "limits" in data:
            rows.append(
                {
                    "id": data.get("id", path.stem),
                    "title": data.get("title", ""),
                    "held_out": bool(data.get("held_out", False)),
                    "limits": list(data["limits"]),
                }
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the static reviewer demo site.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder")
    args = parser.parse_args(argv)
    paths = build_site(args.out)
    total = sum((args.out / p).stat().st_size for p in paths)
    print(f"wrote {len(paths)} files ({total / 1e6:.1f} MB) to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
