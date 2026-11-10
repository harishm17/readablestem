"""US spelling in everything a visitor can read (owner decision: US spelling site-wide).

Scans the visible text of every page template and the strings of the modules whose text
reaches a page, an export or the public site for British forms ("colour", "organisation",
"licence", "maths", "tick" for a checkbox, ...). Visible text means: Jinja tags and
expressions dropped (but the quoted sentences inside `{{ ... }}` kept), HTML tags and
attribute values dropped (except the ones a person reads: alt, title, aria-label,
placeholder), script and style bodies dropped. Python strings are read with `ast`, so
comments and docstrings are ignored, and a string with no space in it (an identifier, a
setting name, a CSS class, a URL) is ignored.

A hit fails with `file:line`. Add to ALLOWED only for a case that cannot change, with the
reason: a quoted proper name, or a word that is correct as spelled.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "readablestem"

# British forms to catch. Word boundaries keep `aria-labelledby` and `colour_only` out; the
# identifier-like strings are skipped for Python anyway.
BRITISH = re.compile(
    r"\b(?:"
    r"colou?r(?<!color)\w*|"  # colour, coloured, colours (not "color")
    r"organis\w*|"
    r"licen[cs]e(?<!license)\w*|licence\w*|"
    r"maths|"
    r"practis(?:e|ed|es|ing)|"
    r"judgement\w*|"
    r"labell(?:ed|ing)|"
    r"neighbour\w*|"
    r"behaviour\w*|"
    r"centre\w*|"
    r"analys(?:e|ed|ing)|"
    r"recognis\w*|"
    r"summaris\w*|"
    r"favour\w*|"
    r"programme\w*|"
    r"tick|ticks|ticked|ticking|unticked|untick"
    r")\b",
    re.IGNORECASE,
)

# (path relative to the repository, lowercase word) pairs that are correct as written.
ALLOWED: set[tuple[str, str]] = {
    # The CSS class `equation-labelled` (a name, never shown), inside the exported page's style.
    ("src/readablestem/pipeline/assemble.py", "labelled"),
    # Block reasons kept for the evaluation reports and tests; no page or export shows them.
    ("src/readablestem/pipeline/layout.py", "maths"),
}

# Folders whose text reaches a page, an export, an email or the public site.
PYTHON_ROOTS = [
    SRC / "web",
    SRC / "pipeline",
    SRC / "core",
    SRC / "source",
    ROOT / "site" / "build.py",
]
# Operator tools, evaluation code and corpus builders print for the maintainers, not for a
# visitor; the prompts are written for a model.
PYTHON_SKIP_PARTS = {"vendor", "prompts", "__pycache__"}

TEMPLATE_ROOTS = [SRC / "web" / "templates", ROOT / "site" / "templates"]
STATIC_PAGES = [SRC / "web" / "static" / "demo" / "index.html"]
STATIC_SCRIPTS = [SRC / "web" / "static" / "app.js", SRC / "web" / "static" / "parts.js"]

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_JINJA_EXPR = re.compile(r"\{\{.*?\}\}", re.S)
_JINJA_TAG = re.compile(r"\{%.*?%\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_RAW_BLOCK = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.S | re.I)
_TAG = re.compile(r"<[^>]*>", re.S)
_VISIBLE_ATTR = re.compile(
    r"""\b(?:alt|title|aria-label|placeholder)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I
)
_QUOTED_SENTENCE = re.compile(r""""([^"\n]* [^"\n]*)"|'([^'\n]* [^'\n]*)'""")


def _blank(match: re.Match[str]) -> str:
    """The same text as spaces, newlines kept, so line numbers stay true."""
    return re.sub(r"[^\n]", " ", match.group(0))


def _expression_text(match: re.Match[str]) -> str:
    """`{{ "Exam or quiz" if x else "Normal" }}` shows its quoted sentences: keep those."""
    kept = [a or b for a, b in _QUOTED_SENTENCE.findall(match.group(0))]
    text = " ".join(kept)
    pad = match.group(0).count("\n")
    return text + "\n" * pad


def _tag_text(match: re.Match[str]) -> str:
    """A tag shows nothing, except the attribute values a person reads."""
    kept = [(a or b).replace("\n", " ") for a, b in _VISIBLE_ATTR.findall(match.group(0))]
    return " ".join(kept) + "\n" * match.group(0).count("\n")


def visible_text(source: str) -> str:
    """The text of a template or page that a visitor can read, one source line per line."""
    text = _JINJA_COMMENT.sub(_blank, source)
    text = _HTML_COMMENT.sub(_blank, text)
    text = _RAW_BLOCK.sub(_blank, text)
    text = _JINJA_TAG.sub(_blank, text)  # before expressions: a tag may hold "}}" in a string
    # Tags are removed before expressions so a `{{ }}` inside an attribute is not read as
    # text; an expression in the page body keeps its quoted sentences.
    text = _TAG.sub(_tag_text, _EXPR_IN_ATTR_SAFE.sub(_blank_attr_exprs, text))
    return _JINJA_EXPR.sub(_expression_text, text)


_EXPR_IN_ATTR_SAFE = re.compile(r"<[^>]*?\{\{.*?\}\}[^>]*>", re.S)


def _blank_attr_exprs(match: re.Match[str]) -> str:
    """Inside a tag, an expression is attribute value, not text: blank it."""
    return _JINJA_EXPR.sub(_blank, match.group(0))


def hits_in_text(text: str) -> list[tuple[int, str]]:
    found = []
    for number, line in enumerate(text.split("\n"), start=1):
        for match in BRITISH.finditer(line):
            found.append((number, match.group(0)))
    return found


def python_string_hits(path: Path) -> list[tuple[int, str]]:
    source = path.read_text(encoding="utf-8")
    lines = source.split("\n")
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ) and (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        ):
            docstrings.add(id(node.body[0].value))
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstrings or not re.search(r"\s", node.value.strip()):
            continue  # a docstring, or an identifier-like string (class, key, URL)
        # The node starts at its first line; a joined string spans several source lines, so find
        # each hit's own line by counting same-word occurrences in the source lines.
        seen: dict[str, int] = {}
        span = lines[node.lineno - 1 : (node.end_lineno or node.lineno)]
        for match in BRITISH.finditer(node.value):
            word = match.group(0)
            seen[word] = seen.get(word, 0) + 1
            count = 0
            line = node.lineno
            for offset, text in enumerate(span):
                count += len(re.findall(rf"\b{re.escape(word)}\b", text))
                if count >= seen[word]:
                    line = node.lineno + offset
                    break
            found.append((line, word))
    return found


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in PYTHON_ROOTS:
        found = [root] if root.is_file() else sorted(root.rglob("*.py"))
        files += [p for p in found if not PYTHON_SKIP_PARTS & set(p.parts)]
    return files


def _page_files() -> list[Path]:
    files = [p for root in TEMPLATE_ROOTS for p in sorted(root.rglob("*.html"))]
    return files + STATIC_PAGES


def _scan() -> list[str]:
    problems: list[str] = []

    def add(path: Path, hits: list[tuple[int, str]]) -> None:
        rel = path.relative_to(ROOT).as_posix()
        for line, word in hits:
            if (rel, word.lower()) not in ALLOWED:
                problems.append(f"{rel}:{line}: {word}")

    for page in _page_files():
        add(page, hits_in_text(visible_text(page.read_text(encoding="utf-8"))))
    for script in STATIC_SCRIPTS:
        # Quoted sentences only: a variable or a CSS property is not text.
        quoted = "\n".join(
            "".join(m.group(0) for m in _QUOTED_SENTENCE.finditer(line))
            for line in script.read_text(encoding="utf-8").splitlines()
        )
        add(script, hits_in_text(quoted))
    for module in _python_files():
        add(module, python_string_hits(module))
    return problems


def test_visible_text_is_us_spelling() -> None:
    problems = _scan()
    assert not problems, "British spelling in visible text (use US spelling):\n" + "\n".join(
        problems
    )
