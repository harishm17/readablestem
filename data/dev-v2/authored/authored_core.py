"""Shared machinery of the authored dev-v2 builder: gold records, prose with scripts,
toolchain wrappers (Tectonic, LibreOffice, Chromium) and PDF helpers.

Everything here is deterministic: no clock, no unseeded randomness. The document
generators live in `authored_latex.py`, `authored_word.py` and `authored_html.py`; the
command line and the self-check are in `build_authored.py`.
"""

from __future__ import annotations

import bisect
import contextlib
import itertools
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from a11y_stem.eval.equations import latex_to_mathml

SOURCE_DATE_EPOCH = "1791331200"  # 2026-10-07 00:00 UTC, as the layout fixtures
FIXED_PDF_DATE = "D:20261007000000Z"
MAX_PDF_BYTES = 300_000
RETRIEVED_AT = "2026-10-14"
LICENSE_NAME = "MIT License"
LICENSE_URL = "https://opensource.org/licenses/MIT"
AUTHOR = "a11y-stem contributors"

# Set by the command line: paths of the tools the generators call themselves.
CONFIG: dict = {"tectonic": "tectonic"}

FAMILIES = {
    "latex": ("authored-latex", "Authored documents (LaTeX)", "tectonic"),
    "word": ("authored-word", "Authored documents (Word)", "libreoffice"),
    "html": ("authored-html", "Authored documents (HTML)", "chromium"),
}

CLASS_IDS = (
    "inline_fraction",
    "system_braced",
    "multirow_equations",
    "determinant",
    "matrix",
    "piecewise_words",
    "numbered_exercise_eq",
    "display_large",
    "equation_image",
    "chem_formula",
    "chem_charge",
    "chem_equation",
    "nuclide",
    "prose_unit_power",
    "prose_chem_script",
    "prose_nuclide",
    "prose_script_other",
    "code_block",
    "glossary_entry",
    "table_data",
    "table_layout",
    "heading_sentence",
    "heading_repeated_label",
    "heading_multiline",
    "hyphen_break",
)

KIND_PREFIX = {
    "equation": "eq",
    "heading": "hd",
    "script": "sc",
    "code": "cd",
    "glossary": "gl",
    "table": "tb",
    "hyphen_break": "hy",
    "exercise_text": "ex",
}

SCRIPT_CLASSES = {
    "u": "prose_unit_power",
    "c": "prose_chem_script",
    "n": "prose_nuclide",
    "o": "prose_script_other",
}


# --------------------------------------------------------------------------- text helpers


def collapse(text: str) -> str:
    """Whitespace collapsed to single spaces."""
    return " ".join(text.split())


SOFT_HYPHEN = chr(0xAD)
HYPHENS = "-" + SOFT_HYPHEN
_QUOTES = str.maketrans({0x2019: "'", 0x2018: "'", 0x201C: '"', 0x201D: '"'})
_DROP = re.compile(
    "[\\s" + SOFT_HYPHEN + "\\-" + chr(0x2010) + "-" + chr(0x2015) + chr(0x2212) + "]+"
)


def squash(text: str) -> str:
    """A text-layer comparison key: NFKC (ligatures), no spaces, hyphens or soft hyphens."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    return _DROP.sub("", text).lower()


def strip_spacing(latex: str) -> str:
    """Drop purely typographic spacing commands from a LaTeX string."""
    out = re.sub(r"\\(?:,|;|:|!|quad|qquad)(?![A-Za-z])", " ", latex)
    return collapse(out)


def heading_classes(text: str, all_texts: Iterable[str]) -> list[str]:
    """The heading classes of the brief, from the heading text and the document's headings."""
    classes = []
    if text.rstrip()[-1:] in {".", "?", ":", "!"} or len(text.split()) >= 8:
        classes.append("heading_sentence")
    if sum(1 for t in all_texts if t == text) >= 2:
        classes.append("heading_repeated_label")
    if len(text) >= 60:
        classes.append("heading_multiline")
    return classes


# --------------------------------------------------------------------------- prose with scripts


@dataclass(frozen=True)
class Seg:
    kind: str  # t | sup | sub
    text: str
    cls: str | None = None
    italic: bool = False


Para = list[Seg]

_MARK = re.compile(r"\*(?P<it>[^*]+)\*|(?P<op>[\^_])\{(?P<val>[^}|]*)\|(?P<cls>[uocn])\}")


def parse_para(markup: str) -> Para:
    """Parse `CO_{2|c}`, `^{14|n}C`, `*x*_{1|o}` into segments.

    `^{value|class}` is a superscript, `_{value|class}` a subscript, with class u (unit
    power), c (chemical), n (nuclide prefix) or o (other); `*x*` is an italic run.
    """
    segs: Para = []
    pos = 0
    for m in _MARK.finditer(markup):
        if m.start() > pos:
            segs.append(Seg("t", markup[pos : m.start()]))
        if m.group("it") is not None:
            segs.append(Seg("t", m.group("it"), italic=True))
        else:
            kind = "sup" if m.group("op") == "^" else "sub"
            segs.append(Seg(kind, m.group("val"), SCRIPT_CLASSES[m.group("cls")]))
        pos = m.end()
    if pos < len(markup):
        segs.append(Seg("t", markup[pos:]))
    return segs


def flat(para: Para) -> str:
    """The printed text of a paragraph: scripts written inline, italics plain."""
    return "".join(s.text for s in para)


def marked(para: Para) -> str:
    """The gold `text`: the paragraph with ^{...} and _{...} marking the scripts."""
    out = []
    for s in para:
        if s.kind == "sup":
            out.append("^{" + s.text + "}")
        elif s.kind == "sub":
            out.append("_{" + s.text + "}")
        else:
            out.append(s.text)
    return "".join(out)


def script_runs(para: Para) -> list[dict]:
    runs = []
    before = ""
    for s in para:
        if s.kind in {"sup", "sub"}:
            runs.append(
                {
                    "script": s.kind,
                    "value": s.text,
                    "before": before[-24:],
                    "class": s.cls,
                }
            )
        before += s.text
    return runs


_TEX_SPECIAL = {
    "&": r"\&",
    "%": r"\%",
    "#": r"\#",
    "_": r"\_",
    "$": r"\$",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    "\\": r"\textbackslash{}",
}


def tex_escape(text: str) -> str:
    return "".join(_TEX_SPECIAL.get(ch, ch) for ch in text)


def _needs_math(value: str) -> bool:
    return bool(re.search(r"[-+=/]", value))


def para_to_tex(para: Para) -> str:
    """LaTeX of a paragraph: scripts as \\textsuperscript / \\textsubscript, or as math
    scripts when the value holds an operator or a letter variable (`-3`, `n+1`)."""
    out = []
    for s in para:
        if s.kind == "t":
            txt = tex_escape(s.text)
            out.append(r"\textit{" + txt + "}" if s.italic else txt)
        else:
            op = "^" if s.kind == "sup" else "_"
            val = s.text
            if _needs_math(val) or (val.isalpha() and len(val) == 1):
                out.append("$" + op + "{" + val + "}$")
            elif s.kind == "sup":
                out.append(r"\textsuperscript{" + val + "}")
            else:
                out.append(r"\textsubscript{" + val + "}")
    return "".join(out)


# --------------------------------------------------------------------------- table markup

Cell = str | tuple[str, dict]


def html_text(markup: str) -> str:
    """Cell markup (`10^{8|o}`, `m^{2|u}`) -> HTML text with <sup> and <sub>."""
    out = []
    for s in parse_para(markup):
        txt = s.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        out.append(f"<{s.kind}>{txt}</{s.kind}>" if s.kind in {"sup", "sub"} else txt)
    return "".join(out)


def table_html(
    rows: list[list[Cell]], *, header_rows: int = 1, header_cols: int = 0, layout: bool = False
) -> str:
    """Plain table markup: th (with scope) for header cells, td otherwise, rowspan/colspan
    only where given. Layout tables have td only. Cells that a span covers are omitted."""
    parts = ["<table>"]
    for r, row in enumerate(rows):
        parts.append("<tr>")
        for c, cell in enumerate(row):
            text, attrs = (cell, {}) if isinstance(cell, str) else cell
            is_head = not layout and (r < header_rows or c < header_cols)
            tag = "th" if is_head else "td"
            attr = ""
            if is_head:
                attr += ' scope="col"' if r < header_rows else ' scope="row"'
            for k in ("rowspan", "colspan"):
                if k in attrs:
                    attr += f' {k}="{attrs[k]}"'
            parts.append(f"<{tag}{attr}>{html_text(text)}</{tag}>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def table_anchor(rows: list[list[Cell]]) -> str | None:
    cells = [c if isinstance(c, str) else c[0] for row in rows for c in row]
    cells = [flat(parse_para(c)) for c in cells]
    for c in cells:
        if len(c.strip()) >= 4:
            return c
    return next((c for c in cells if c.strip()), None)


# --------------------------------------------------------------------------- the document


@dataclass
class Doc:
    base: str
    variant: str  # dev | check
    family: str  # latex | word | html
    title: str
    items: list[dict] = field(default_factory=list)
    extra_files: list[str] = field(default_factory=list)

    @property
    def doc_id(self) -> str:
        return self.base if self.variant == "dev" else self.base + "-chk"

    def add(self, kind: str, *, anchor: str | None = None, classes=(), **fields) -> dict:
        rec = {"kind": kind, "classes": list(classes), "_anchor": anchor, **fields}
        self.items.append(rec)
        return rec

    # ---- typed helpers

    def equation(
        self,
        latex: str,
        *,
        display: str,
        classes=(),
        context: str,
        anchor: str | None,
        exercise_number: str | None = None,
        appearance: str = "text",
        **extra,
    ) -> dict:
        gold = strip_spacing(latex)
        latex_to_mathml(gold, display)  # raises if latex2mathml cannot convert it
        return self.add(
            "equation",
            anchor=anchor,
            classes=classes,
            display=display,
            latex=gold,
            mathml=latex_to_mathml(gold, display),
            context=collapse(context),
            appearance=appearance,
            exercise_number=exercise_number,
            page=None,
            **extra,
        )

    def heading(self, level: int, text: str) -> dict:
        return self.add("heading", anchor="^" + text, level=level, text=collapse(text))

    def script_para(self, para: Para) -> dict:
        return self.add(
            "script",
            anchor=flat(para)[:40],
            classes=(),
            text=marked(para),
            runs=script_runs(para),
        )

    def code(self, text: str, language: str) -> dict:
        first = next((ln for ln in text.splitlines() if ln.strip()), text)
        return self.add(
            "code", anchor=first.strip(), classes=["code_block"], text=text, language=language
        )

    def glossary(self, term: str, definition: str) -> dict:
        return self.add(
            "glossary",
            anchor=term + " " + definition[:30],
            classes=["glossary_entry"],
            term=term,
            definition=collapse(definition),
        )

    def table(
        self,
        rows: list[list[Cell]],
        *,
        header_rows: int = 1,
        header_cols: int = 0,
        layout: bool = False,
        caption: str | None = None,
    ) -> dict:
        n_cols = max(
            sum(1 if isinstance(c, str) else c[1].get("colspan", 1) for c in r) for r in rows
        )
        return self.add(
            "table",
            anchor=table_anchor(rows),
            classes=["table_layout" if layout else "table_data"],
            is_layout=layout,
            html=table_html(rows, header_rows=header_rows, header_cols=header_cols, layout=layout),
            n_rows=len(rows),
            n_cols=n_cols,
            caption=caption,
        )

    def exercise_text(self, number: int, text: str) -> dict:
        return self.add(
            "exercise_text",
            anchor=text[:24],
            classes=["numbered_exercise_eq"],
            number=str(number),
            text=text,
        )

    # ---- finishing

    def finalize_headings(self) -> None:
        texts = [r["text"] for r in self.items if r["kind"] == "heading"]
        for r in self.items:
            if r["kind"] == "heading":
                r["classes"] = heading_classes(r["text"], texts)

    def records(self, pages: list[int | None]) -> list[dict]:
        """Final gold records: common fields, ids, per-kind index, page (see `locate`)."""
        slug, book_title, toolchain = FAMILIES[self.family]
        counters: dict[str, int] = {}
        out = []
        for rec, page in zip(self.items, pages, strict=True):
            kind = rec["kind"]
            idx = counters.get(kind, 0)
            counters[kind] = idx + 1
            body = {k: v for k, v in rec.items() if not k.startswith("_")}
            if kind != "hyphen_break":
                body["page"] = page
            body["classes"] = sorted(set(body["classes"]))
            rec_id = f"authored/{self.doc_id}/{KIND_PREFIX[kind]}-{idx:03d}"
            if kind == "table":
                body["cluster"] = rec_id
            out.append(
                {
                    "id": rec_id,
                    "source": "authored",
                    "book_slug": slug,
                    "book_title": book_title,
                    "book_uuid": None,
                    "book_version": "1",
                    "archive_version": None,
                    "book_state": "authored",
                    "page_slug": self.doc_id,
                    "page_title": self.title,
                    "page_uuid": None,
                    "source_url": None,
                    "content_url": None,
                    "license_name": LICENSE_NAME,
                    "license_url": LICENSE_URL,
                    "retrieved_at": RETRIEVED_AT,
                    "toolchain": toolchain,
                    "variant": self.variant,
                    "pdf": f"authored/{self.doc_id}/{self.doc_id}.pdf",
                    "index_on_page": idx,
                    **body,
                }
            )
        return out


@dataclass
class Built:
    """A generated document: gold records, the source text/bytes and any assets."""

    doc: Doc
    source: str | bytes
    post: Callable[[Doc, bytes], None] | None = None
    assets: dict[str, bytes | str] = field(default_factory=dict)


def write_gold(path: Path, records: list[dict]) -> None:
    lines = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- PDF helpers


def pdf_pages_text(pdf: bytes, strip_numbers: bool = False) -> list[str]:
    """Text of each page, without a trailing page-number line (LaTeX's plain page style)."""
    import pymupdf

    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        pages = []
        for page in doc:
            lines = page.get_text().rstrip().split("\n")
            if strip_numbers and len(lines) > 1 and re.fullmatch(r"\d{1,3}", lines[-1].strip()):
                lines = lines[:-1]
            pages.append("\n".join(lines) + "\n")
        return pages


def locate(pages: list[str], anchors: list[str | None]) -> list[int | None]:
    """Page of each anchor, searching forward through the document in order.

    An anchor is prose that sits at (or just before) the item. `=` repeats the previous
    page. `^text` is a heading: a line that is the heading (or its first line, or starts
    with it as a run-in heading). An anchor not found after the cursor is searched from the
    start; otherwise None.
    """
    full = ""
    offsets = []
    lines: list[tuple[int, int, str]] = []  # (start offset in `full`, page, squashed line)
    for pno, text in enumerate(pages):
        offsets.append(len(full))
        for ln in text.split("\n"):
            sq = squash(ln)
            if sq:
                lines.append((len(full), pno, sq))
            full += sq
    cursor = 0
    result: list[int | None] = []
    last: int | None = None
    for anchor in anchors:
        if anchor == "=":
            result.append(last)
            continue
        if not anchor:
            result.append(None)
            continue
        pos = -1
        if anchor.startswith("^"):
            key = squash(anchor[1:])
            for start, _, sq in lines:  # the heading alone on its line, or a run-in heading
                if start >= cursor and sq.startswith(key):
                    pos = start
                    break
            if pos < 0:  # a heading that wraps: its first line is a prefix of it
                for start, _, sq in lines:
                    if start >= cursor and len(sq) >= 0.4 * len(key) and key.startswith(sq):
                        pos = start
                        break
        else:
            key = squash(anchor)
        if pos < 0:
            pos = full.find(key, cursor)
        if pos >= 0:
            cursor = pos + len(key)
        else:
            pos = full.find(key)
        if pos < 0:
            result.append(None)
            continue
        last = bisect.bisect_right(offsets, pos) - 1
        result.append(last)
    return result


def has_struct_tree(pdf: bytes) -> bool:
    return b"StructTreeRoot" in pdf


def fix_pdf(pdf: bytes) -> bytes:
    """Fixed dates and ids so a rebuild gives the same bytes, and no structure tree."""
    import pymupdf

    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        meta = dict(doc.metadata or {})
        meta.update(
            creationDate=FIXED_PDF_DATE,
            modDate=FIXED_PDF_DATE,
            producer="",
            creator="",
            author=AUTHOR,
        )
        doc.set_metadata(meta)
        doc.del_xml_metadata()  # LibreOffice writes the build time into the XMP packet
        zero_id = "[<00000000000000000000000000000000><00000000000000000000000000000000>]"
        with contextlib.suppress(Exception):  # the id is cosmetic
            doc.xref_set_key(-1, "ID", zero_id)
        return doc.tobytes(garbage=3, deflate=True, no_new_id=True)


# --------------------------------------------------------------------------- hyphen breaks

LEFT_RE = re.compile("([A-Za-z]+(?:-[A-Za-z]+)*)[" + HYPHENS + "]$")
_WORD = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")


def word_table(texts: Iterable[str]) -> dict[str, str]:
    """lower-case word -> word as written, over every word of the source prose."""
    table: dict[str, str] = {}
    for t in texts:
        for m in _WORD.finditer(t):
            table.setdefault(m.group(0).lower(), m.group(0))
    return table


def detect_hyphen_breaks(pdf: bytes, words: dict[str, str]) -> list[dict]:
    """Line-end hyphens in the text layer, verified against the source word list.

    A line of a text block ends in "-" (or a soft hyphen) and the next line of the same
    block starts with a lower-case letter. `word` is the joined word as printed (checked
    against the source word list); `compound` is true when the hyphen belongs to it.
    """
    import pymupdf

    found = []
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for page_no, page in enumerate(doc):
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                lines = [
                    "".join(span["text"] for span in ln["spans"]).rstrip() for ln in block["lines"]
                ]
                for cur, nxt in itertools.pairwise(lines):
                    if not cur or cur[-1] not in HYPHENS or not nxt[:1].islower():
                        continue
                    lm = LEFT_RE.search(cur)
                    rm = re.match(r"[a-z]+(?:-[a-z]+)*", nxt)
                    if not lm or not rm:
                        continue
                    left, right = lm.group(1), rm.group(0)
                    joined = (left + right).lower()
                    hyphenated = (left + "-" + right).lower()
                    # The word is written as it is printed here (a sentence-initial word
                    # is capitalised in one place and not in another).
                    if joined in words:
                        word, compound = left + right, False
                    elif hyphenated in words:
                        word, compound = left + "-" + right, True
                    else:
                        continue
                    found.append(
                        {
                            "left": left,
                            "right": right,
                            "word": word,
                            "compound": compound,
                            "page": page_no,
                        }
                    )
    return found


# --------------------------------------------------------------------------- toolchains


def run_tectonic(tex: Path, tectonic: str) -> tuple[bytes, str]:
    """Compile `tex` in a scratch folder (deterministic mode); return (PDF bytes, aux text)."""
    env = {**os.environ, "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH}
    with tempfile.TemporaryDirectory(prefix="a11y-dsv2-tex-") as scratch:
        work = Path(scratch)
        shutil.copy2(tex, work / tex.name)
        proc = subprocess.run(
            [
                tectonic,
                "-Z",
                "deterministic-mode",
                "--keep-intermediates",
                "--chatter",
                "minimal",
                tex.name,
            ],
            cwd=work,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"tectonic failed on {tex.name}:\n{proc.stderr[-2000:]}")
        pdf = (work / tex.with_suffix(".pdf").name).read_bytes()
        aux_path = work / tex.with_suffix(".aux").name
        aux = aux_path.read_text(encoding="utf-8", errors="replace") if aux_path.exists() else ""
        if os.environ.get("A11Y_DSV2_VERBOSE"):
            bad = [ln for ln in proc.stderr.splitlines() if "verfull" in ln or "rror" in ln]
            if bad:
                print(f"  {tex.name}: " + "; ".join(bad[:6]))
        return pdf, aux


def label_pages(aux: str) -> dict[str, int]:
    """\\newlabel entries of an .aux file -> 0-based PDF page index."""
    out = {}
    for m in re.finditer(r"\\newlabel\{(eq\d+)\}\{\{[^{}]*\}\{(\d+)\}", aux):
        out[m.group(1)] = int(m.group(2)) - 1
    return out


def run_soffice(docx: Path, hyph_dir: Path | None) -> bytes:
    """LibreOffice's PDF of `docx`, untagged (LibreOffice 26.2 tags by default)."""
    profile = Path(tempfile.gettempdir()) / "dsv2-lo-profile"
    env = {**os.environ}
    if hyph_dir is not None:
        env["DICPATH"] = str(hyph_dir)
    with tempfile.TemporaryDirectory(prefix="a11y-dsv2-lo-") as out:
        subprocess.run(
            [
                "soffice",
                f"-env:UserInstallation=file://{profile}",
                "--headless",
                "--convert-to",
                'pdf:writer_pdf_Export:{"UseTaggedPDF":{"type":"boolean","value":"false"}}',
                "--outdir",
                out,
                str(docx),
            ],
            check=True,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return (Path(out) / docx.with_suffix(".pdf").name).read_bytes()


def print_html(source: str) -> bytes:
    """Chromium's PDF of `source`, at the page size of its CSS @page rule."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(java_script_enabled=False, offline=True)
            page = context.new_page()
            page.set_content(source, wait_until="load")
            page.emulate_media(media="print")
            return page.pdf(prefer_css_page_size=True, print_background=True, tagged=False)
        finally:
            browser.close()


def find_hyph_dir() -> Path | None:
    """A folder holding hyph_en_US.dic: $A11Y_HYPH_DIR, $DICPATH, system folders, pyphen."""
    candidates = [os.environ.get("A11Y_HYPH_DIR"), os.environ.get("DICPATH")]
    candidates += ["/usr/share/hyphen", "/usr/share/myspell", "/usr/share/hunspell"]
    try:
        import pyphen  # type: ignore[import-not-found]

        candidates.append(str(Path(pyphen.__file__).parent / "dictionaries"))
    except ImportError:
        pass
    for c in candidates:
        if c and (Path(c) / "hyph_en_US.dic").exists():
            return Path(c)
    return None
