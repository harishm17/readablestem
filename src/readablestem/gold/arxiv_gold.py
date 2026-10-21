"""Gold records for one arXiv paper: its equations from the source, the pages they are printed on.

`analyse` reads a source directory and the PDF's page texts and says which pages can be used
as gold pages (every equation on them known, placed and convertible). `choose_pages` draws 4 to 6
of them with a seeded hash. `gold_records` writes the records in the layout of the other gold
sets (`kind`, `mathml`, `display`, `book_slug`, `page_slug`, licence fields) so that the document
task's scorers read them unchanged. See `arxiv_tex.py` for how the equations are found and
placed, and docs/eval-realworld.md for the limits.

The MathML of a gold equation is made by the pipeline's own LaTeX path (`equation_step.clean_latex`
and `validate.safe_latex_to_mathml`), so that gold and a prediction in LaTeX are converted by the
same code. An equation with a command the converter does not know (it would come out as a literal
`\\name` token), an unconverted reference (`\\ref`, `\\cite`) or any converter error is
"unconvertible": the pages it is on are not used.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from readablestem.gold import arxiv_tex as tex
from readablestem.gold.arxiv_corpus import stable_key
from readablestem.pipeline.equation_step import clean_latex
from readablestem.pipeline.validate import safe_latex_to_mathml

MIN_PAGES, MAX_PAGES_PICK = 4, 6
_TOKEN = re.compile(r"<(?:mi|mn|mo|mtext|ms)[ >]")
_LEFTOVER = re.compile(r"<m[ino]>\\[A-Za-z]")
_UNRESOLVED = re.compile(r"\\(?:ref|eqref|cite|autoref|cref|Cref|pageref|label)\b")


class GoldError(ValueError):
    pass


_UNWRAP = ("ensuremath", "mathbin", "mathrel", "mathord")
_DROP_COMMANDS = re.compile(r"\\(?:nolimits|limits|hfill|middle(?![A-Za-z])|color\{[^{}]*\})\s*")
_NAMED_OPERATORS = re.compile(r"\\(arg)(?![A-Za-z])")


def prepare(latex: str) -> str:
    r"""What the converter has to be given for a few author-level commands that it does not know:
    `\ensuremath{X}` and `\textcolor{c}{X}` are `X`, `\mathop` is `\operatorname`; `\arg` is an operator name;
    `\limits` and spacing that only moves things are dropped. Nothing else is changed."""
    text = latex
    for name in _UNWRAP:
        text = _unwrap(text, name, 1)
    text = _unwrap(text, "textcolor", 2)
    text = re.sub(r"\\mathop(?![A-Za-z])", r"\\operatorname", text)
    text = _DROP_COMMANDS.sub(" ", text)
    text = re.sub(r"\\vspace\*?\{[^{}]*\}|\\hspace\*?\{[^{}]*\}", " ", text)
    return _NAMED_OPERATORS.sub(r"\\operatorname{\1}", text)


def _unwrap(text: str, name: str, nargs: int) -> str:
    """`\name{a}{b}` with `nargs` groups becomes the last group."""
    out: list[str] = []
    i = 0
    pattern = re.compile(r"\\" + name + r"(?![A-Za-z])\s*")
    while True:
        m = pattern.search(text, i)
        if m is None:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i : m.start()])
        pos = m.end()
        last = None
        for _ in range(nargs):
            group = tex.read_group(text, pos)
            if group is None:
                last = None
                break
            last, pos = group
            pos = pos  # next group follows immediately
            while pos < len(text) and text[pos] == " ":
                pos += 1
        if last is None:
            out.append(m.group(0))
            i = m.end()
        else:
            out.append(" " + last + " ")
            i = pos


def to_mathml(latex: str, display: bool) -> str:
    """MathML as the pipeline writes it for LaTeX; raises `GoldError` when it is not sound."""
    if _UNRESOLVED.search(latex):
        raise GoldError("unresolved reference in the equation")
    try:
        mathml = safe_latex_to_mathml(clean_latex(prepare(latex)), "block" if display else "inline")
    except Exception as exc:  # LatexConversionError and whatever the converter raises
        raise GoldError(str(exc)) from None
    if _LEFTOVER.search(mathml) or "<merror" in mathml:
        raise GoldError("a command the converter does not know")
    return mathml


def token_count(mathml: str) -> int:
    return len(_TOKEN.findall(mathml))


@dataclass
class Analysis:
    equations: list[tex.Equation]
    mathml: dict[int, str]  # equation index -> MathML (convertible ones only)
    bad: dict[int, str]  # equation index -> why it is unconvertible
    placements: dict[int, tex.Placement]
    usable: dict[int, list[int]]  # page (0-based) -> equation indexes
    coverage: dict[int, float]
    macro_failures: list[str]
    n_items: int
    anchors: int
    skipped_environments: dict[str, int] = field(default_factory=dict)
    unbalanced: int = 0
    main: str | None = None

    @property
    def unconvertible_share(self) -> float:
        return len(self.bad) / len(self.equations) if self.equations else 1.0


def analyse(sources: dict[str, str], page_texts: list[str]) -> Analysis | str:
    """The analysis of a paper, or the reason it cannot be used (a short code)."""
    main = tex.find_main(sources)
    if main is None:
        return "no_main_tex"
    flat = tex.flatten(sources, main)
    table = tex.MacroTable()
    flat = table.read(flat)
    flat = table.expand(flat)
    body = tex.body_of(flat)
    extraction = tex.extract(body)
    if not extraction.items:
        return "no_equations"
    equations = tex.equations_of(extraction.items)
    mathml: dict[int, str] = {}
    bad: dict[int, str] = {}
    for k, eq in enumerate(equations):
        try:
            mathml[k] = to_mathml(eq.latex, eq.display)
        except GoldError as exc:
            bad[k] = str(exc)
    mapping = tex.page_map(extraction, page_texts)
    usable = tex.usable_pages(
        equations, mapping.placements, mapping.coverage, unconvertible=set(bad)
    )
    return Analysis(
        equations=equations,
        mathml=mathml,
        bad=bad,
        placements=mapping.placements,
        usable=usable,
        coverage=mapping.coverage,
        macro_failures=table.failures,
        n_items=len(extraction.items),
        anchors=mapping.anchors,
        skipped_environments=extraction.skipped_environments,
        unbalanced=extraction.unbalanced,
        main=main,
    )


def choose_pages(usable: dict[int, list[int]], seed: str, paper_id: str) -> list[int]:
    """4 to 6 usable pages (0-based, ascending), by the seeded hash; fewer than 4 usable: none."""
    if len(usable) < MIN_PAGES:
        return []
    k = MIN_PAGES + int(stable_key(seed, "count", paper_id)[:8], 16) % (
        MAX_PAGES_PICK - MIN_PAGES + 1
    )
    ranked = sorted(usable, key=lambda p: stable_key(seed, "page", paper_id, str(p)))
    return sorted(ranked[: min(k, len(ranked))])


def paper_slug(arxiv_id: str) -> str:
    return arxiv_id.replace("/", "_")


def gold_records(
    analysis: Analysis,
    pages: list[int],
    meta: dict,
    *,
    pdf_relpath: str,
    retrieved_at: str,
    pdf_sha256: str,
    source_sha256: str,
) -> list[dict]:
    """One record per equation on the chosen pages, in reading order (page, then source order)."""
    slug = paper_slug(meta["id"])
    chosen = sorted(pages)
    rows: list[tuple[int, int, int]] = []  # (page, equation index)
    for page in chosen:
        for k in analysis.usable[page]:
            rows.append((page, analysis.equations[k].item, k))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    records = []
    for position, (page, _, k) in enumerate(rows):
        eq = analysis.equations[k]
        mathml = analysis.mathml[k]
        records.append(
            {
                "id": f"arxiv/{slug}/eq-{position:03d}",
                "kind": "equation",
                "source": "arxiv",
                "book_slug": "arxiv",
                "book_title": "arXiv papers (author LaTeX)",
                "page_slug": slug,
                "page_title": meta.get("title", ""),
                "arxiv_id": meta["id"],
                "index_on_page": position,
                "display": "block" if eq.display else "inline",
                "env": eq.env,
                "rows": eq.rows,
                "tex_item": eq.item,
                "pdf_page": page + 1,
                "pdf_page_in_extract": chosen.index(page) + 1,
                "latex": eq.latex,
                "mathml": mathml,
                "tokens": token_count(mathml),
                "in_float": eq.float_id is not None,
                "classes": [],
                "license_name": meta["licence_name"],
                "license_url": meta["license"],
                "authors": meta.get("authors", ""),
                "source_url": f"https://arxiv.org/abs/{meta['id']}",
                "style": meta.get("style"),
                "pdf": pdf_relpath,
                "pdf_sha256": pdf_sha256,
                "source_sha256": source_sha256,
                "retrieved_at": retrieved_at,
            }
        )
    return records


def cut_pdf(source: Path, pages: list[int], target: Path) -> None:
    """The chosen pages (0-based) of `source`, in order, as a new PDF."""
    import pymupdf

    with pymupdf.open(source) as doc:
        doc.select(sorted(pages))
        doc.save(target, garbage=3, deflate=True)


def page_texts_of(pdf: Path) -> list[str]:
    import pymupdf

    with pymupdf.open(pdf) as doc:
        return [page.get_text("text") for page in doc]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
