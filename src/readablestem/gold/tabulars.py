"""Where the tables of an arXiv paper are, from the author's LaTeX (labels for table detection).

The source says which `tabular` environments a paper has and what words each cell holds; the
PDF says where those words are. `find_tabulars` reads the environments (rows, cells, the
words of each cell with the maths left out); `locate` finds one on a page by aligning its words
with the page's words in content-stream order (pdfTeX writes a tabular row by row), and returns
the box of the aligned span. No model, no TeX engine. The box is a proposal for a person to
check by eye (`scripts/tables_label.py` draws it): it spans the words, so a table whose first or
last row is only maths comes out short.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from readablestem.gold import arxiv_tex as tex

TABULAR_ENVS = ("tabular", "tabular*", "tabularx", "tabulary", "longtable", "supertabular")
_BEGIN = re.compile(r"\\begin\{(" + "|".join(re.escape(e) for e in TABULAR_ENVS) + r")\}")
_TOKEN = re.compile(r"[a-z]{2,}|\d+(?:\.\d+)?")
_MATH = re.compile(r"\$\$.*?\$\$|\$.*?\$|\\\(.*?\\\)|\\\[.*?\\\]", re.S)
_SKIP_COMMANDS = re.compile(
    r"\\(?:hline|toprule|midrule|bottomrule|cline\{[^}]*\}|cmidrule(?:\([^)]*\))?\{[^}]*\}"
    r"|noalign\{[^}]*\}|rowcolor(?:\[[^\]]*\])?\{[^}]*\}|cellcolor(?:\[[^\]]*\])?\{[^}]*\}"
    r"|hphantom\{[^}]*\}|vspace\*?\{[^}]*\}|hspace\*?\{[^}]*\}|label\{[^}]*\}|cite[a-z]*\{[^}]*\}"
    r"|ref\{[^}]*\}|footnotemark|endhead|endfirsthead|endfoot|endlastfoot)"
)
MIN_MATCH = 0.6  # share of a table's words a page must hold, in order, to place it there


@dataclass
class Tabular:
    env: str
    spec: str
    rows: list[list[str]]  # raw cell source
    offset: int  # position in the body
    has_graphics: bool = False
    tokens: list[str] = field(default_factory=list)  # the words of the cells, in order

    @property
    def columns(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    @property
    def filled_columns(self) -> int:
        """Columns that hold a word in at least one row."""
        used: set[int] = set()
        for row in self.rows:
            for c, cell in enumerate(row):
                if _TOKEN.search(plain(cell)):
                    used.add(c)
        return len(used)


def plain(cell: str) -> str:
    """A cell's words as the PDF would hold them: commands and maths dropped, braces opened."""
    text = _MATH.sub(" ", cell)
    text = _SKIP_COMMANDS.sub(" ", text)
    text = re.sub(r"\\(?:multicolumn|multirow)\{[^}]*\}\{[^}]*\}", " ", text)
    text = re.sub(r"\\[a-zA-Z]+\*?", " ", text)
    text = text.replace("\\&", "&").replace("~", " ")
    text = re.sub(r"[{}\\]", " ", text)
    return " ".join(text.lower().split())


def tokens_of(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _split_top(text: str, separator: str) -> list[str]:
    """Split at `separator` ("&" or the row break) outside braces and nested environments."""
    parts: list[str] = []
    depth = env_depth = 0
    start = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            if text.startswith("\\begin{", i):
                env_depth += 1
            elif text.startswith("\\end{", i):
                env_depth -= 1
            elif separator == "\\\\" and text.startswith("\\\\", i) and not depth and not env_depth:
                parts.append(text[start:i])
                i += 2
                m = re.match(r"\s*\*?\s*(\[[^\]]*\])?", text[i:])
                i += m.end() if m else 0
                start = i
                continue
            elif separator == "\\\\" and text.startswith("\\tabularnewline", i) and not depth:
                parts.append(text[start:i])
                i += len("\\tabularnewline")
                start = i
                continue
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == separator and separator == "&" and not depth and not env_depth:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return parts


def find_tabulars(body: str) -> list[Tabular]:
    """Every `tabular`-family environment of the body (macros already expanded), in order."""
    found: list[Tabular] = []
    pos = 0
    while True:
        m = _BEGIN.search(body, pos)
        if m is None:
            break
        env = m.group(1)
        end = tex._env_end(body, env, m.end())
        if end is None:
            pos = m.end()
            continue
        inner = body[m.end() : end - len(f"\\end{{{env}}}")]
        i = 0
        if env in ("tabular*", "tabularx", "tabulary"):
            group = tex.read_group(inner, tex._skip_ws(inner, 0))
            i = group[1] if group else 0
        bracket = tex.read_bracket(inner, tex._skip_ws(inner, i))
        if bracket:
            i = bracket[1]
        spec_group = tex.read_group(inner, tex._skip_ws(inner, i))
        spec, i = spec_group if spec_group else ("", i)
        rows = [
            [c.strip() for c in _split_top(r, "&")] for r in _split_top(inner[i:], "\\\\")
        ]
        rows = [r for r in rows if any(plain(c) for c in r)]
        table = Tabular(env, spec, rows, m.start(), "\\includegraphics" in inner)
        table.tokens = [t for r in rows for c in r for t in tokens_of(plain(c))]
        found.append(table)
        pos = end  # a nested tabular belongs to the outer one
    return found


def is_data_table(t: Tabular) -> bool:
    """The labelling rule of data/real-tables: at least two columns of content, ruled or
    not; a grid of images is not a table."""
    return t.filled_columns >= 2 and len(t.rows) >= 2 and not t.has_graphics and len(t.tokens) >= 4


@dataclass
class Location:
    page: int
    bbox: tuple[float, float, float, float]
    matched: float  # share of the table's words found in order on the page


def locate(
    table: Tabular, pages: list[list[tuple[float, float, float, float, str]]]
) -> Location | None:
    """The page and box of a table: the span of the page's words that aligns with the
    table's. `pages[p]` is that page's words `(x0, y0, x1, y1, text)` in stream order."""
    want = table.tokens
    if len(want) < 4:
        return None
    best: tuple[int, Location] | None = None
    for number, words in enumerate(pages):
        have: list[str] = []
        owner: list[int] = []
        for index, (*_, text) in enumerate(words):
            for token in tokens_of(text):
                have.append(token)
                owner.append(index)
        if not have:
            continue
        blocks = [
            b
            for b in SequenceMatcher(None, want, have, autojunk=False).get_matching_blocks()
            if b.size >= 2
        ]
        if not blocks:
            continue
        # The densest run of blocks along the page (a table is one stretch of the stream).
        clusters: list[list] = []
        for block in sorted(blocks, key=lambda b: b.b):
            if clusters and block.b - (clusters[-1][-1].b + clusters[-1][-1].size) <= 80:
                clusters[-1].append(block)
            else:
                clusters.append([block])
        cluster = max(clusters, key=lambda c: sum(b.size for b in c))
        matched = sum(b.size for b in cluster)
        first, last = owner[cluster[0].b], owner[cluster[-1].b + cluster[-1].size - 1]
        chosen = words[first : last + 1]
        box = (
            min(w[0] for w in chosen),
            min(w[1] for w in chosen),
            max(w[2] for w in chosen),
            max(w[3] for w in chosen),
        )
        found = Location(number, box, matched / len(want))
        if best is None or matched > best[0]:
            best = (matched, found)
    if best is None or best[1].matched < MIN_MATCH:
        return None
    return best[1]
