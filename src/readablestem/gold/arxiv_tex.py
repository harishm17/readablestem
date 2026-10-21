r"""Gold equations from an author's own LaTeX source (the real-world evaluation set).

The answer key of an arXiv paper is its `.tex`: what the author typed is what the PDF shows. This
module turns a source tree into a list of equations, each with its LaTeX, its kind (display or
inline), the environment it came from and the prose around it, and then finds the PDF page each one
is printed on. No model is called and no TeX engine is needed.

What it does, in order (each step is a function, and has a test in `tests/test_arxiv_tex.py`):

1. `strip_comments`, `flatten`: drop `%` comments, follow `\input` and `\include`.
2. `MacroTable`: read `\newcommand`, `\renewcommand`, `\providecommand`, `\def`,
   `\DeclareMathOperator`, `\let` and argument-free `\newenvironment` from the whole source,
   take their definitions out of the text and expand their uses (nested, with arguments,
   optional arguments with a default). A macro
   that cannot be expanded (recursive, too large, `\def` with a delimited parameter) is recorded
   in `MacroTable.failures`; the paper is skipped when a failure touches its mathematics.
3. `extract`: walk the body and cut out `equation`, `align`, `gather`, `multline`, `eqnarray`,
   `flalign`, `alignat`, `displaymath`, `math` (and the starred forms), `$$..$$`, `\[..\]`,
   `\(..\)` and `$..$`. Verbatim, listings and TikZ pictures are skipped. Material in a float
   (`figure`, `table`) is kept apart, because a float is printed somewhere else than it is typed.
4. `split_environment`: an `align`, `eqnarray` or `flalign` with several numbered rows is one gold
   equation per numbered row (a run of unnumbered rows belongs to the next numbered row), a
   starred one is a single equation, `gather` is one equation per row, `multline` is one equation.
   This follows how the layout stage groups lines (docs/layout.md: a derivation is one equation, two
   numbered rows stay apart).
5. `page_map`: the words of the prose (maths removed) are aligned to the words of the PDF with
   unique word 3-grams and a longest increasing subsequence; an equation lies between the nearest
   anchors before and after it, and has a page when both are on the same page. A float is aligned
   by its own words. An equation whose anchors are on different pages has an interval of pages
   instead, and blocks them for the page choice (a page is used only when every equation on it
   is known).

Limits (also in docs/eval-realworld.md): an align block split by numbers is a convention, not a
fact about the PDF; macros with delimited parameters, `\newenvironment` with arguments, packages
that define maths commands (`physics`, `braket`) are not expanded unless the author defined them
in the source; an equation inside a picture or a minipage that the text anchors cannot place is
refused rather than guessed; TikZ and listing contents are not counted.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path

# ----------------------------------------------------------------------------- source files


def strip_comments(text: str) -> str:
    r"""Remove `%` comments (not `\%`) up to the end of the line, keep the newline."""
    out = []
    for line in text.split("\n"):
        i = 0
        cut = None
        while i < len(line):
            ch = line[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "%":
                cut = i
                break
            i += 1
        out.append(line if cut is None else line[:cut])
    return "\n".join(out)


def read_sources(directory: Path) -> dict[str, str]:
    """Every `.tex` and `.sty`/`.cls` text file under `directory`, by relative posix path."""
    found: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".tex", ".sty", ".cls", ".ltx", ".def"}:
            data = path.read_bytes()
            for enc in ("utf-8", "latin-1"):
                try:
                    found[path.relative_to(directory).as_posix()] = data.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
    return found


def find_main(sources: dict[str, str]) -> str | None:
    """The file with `\\documentclass` and `\\begin{document}` (the longest, if several)."""
    mains = [
        name
        for name, text in sources.items()
        if name.endswith((".tex", ".ltx"))
        and re.search(r"^[^%\n]*\\documentclass", text, re.M)
        and "\\begin{document}" in text
    ]
    return max(mains, key=lambda n: len(sources[n])) if mains else None


_INPUT = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}|\\input\s+([^\s{}\\]+)")


def _resolve(name: str, sources: dict[str, str], base: str) -> str | None:
    """The source file an `\\input{name}` in `base` means (name with or without `.tex`)."""
    name = name.strip().removeprefix("./")
    folder = base.rsplit("/", 1)[0] + "/" if "/" in base else ""
    for prefix in (folder, ""):
        for cand in (name, name + ".tex"):
            if prefix + cand in sources:
                return prefix + cand
    return None


def flatten(sources: dict[str, str], main: str, *, depth: int = 6) -> str:
    """The main file with `\\input`/`\\include` replaced by the file's text (comments removed)."""

    def expand(name: str, level: int) -> str:
        text = strip_comments(sources[name])
        if level >= depth:
            return text

        def repl(m: re.Match[str]) -> str:
            target = (m.group(1) or m.group(2) or "").strip()
            key = _resolve(target, sources, name)
            return "\n" + expand(key, level + 1) + "\n" if key else ""

        return _INPUT.sub(repl, text)

    return expand(main, 0)


# ------------------------------------------------------------------------------------ macros


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def read_group(text: str, i: int) -> tuple[str, int] | None:
    """The `{...}` group at `i` (balanced, escapes respected): (inside, index after) or None."""
    if i >= len(text) or text[i] != "{":
        return None
    depth = 0
    j = i
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1 : j], j + 1
        j += 1
    return None


def read_bracket(text: str, i: int) -> tuple[str, int] | None:
    """The `[...]` optional argument at `i` (no nesting of brackets inside braces)."""
    if i >= len(text) or text[i] != "[":
        return None
    depth = 0
    j = i + 1
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "]" and depth == 0:
            return text[i + 1 : j], j + 1
        j += 1
    return None


@dataclass
class Macro:
    name: str
    nargs: int
    default: str | None
    body: str


_CMD_NAME = re.compile(r"\\([A-Za-z@]+|.)")
_CONTROL_WORD = re.compile(r"\\([A-Za-z@]+)")
_DEFINERS = re.compile(
    r"\\(?P<kind>newcommand|renewcommand|providecommand|DeclareRobustCommand|DeclareMathOperator"
    r"|newenvironment|renewenvironment|def|gdef|edef|xdef|let|DeclareMathAlphabet)(?P<star>\*?)(?![A-Za-z@])"
)
_BUILTINS_NOT_TO_EXPAND = frozenset(
    # structure that matters more than a user's redefinition of it
    {
        "begin",
        "end",
        "left",
        "right",
        "frac",
        "sqrt",
        "text",
        "mathrm",
        "mathbf",
        "mathbb",
        "mathcal",
    }
)


@dataclass
class MacroTable:
    macros: dict[str, Macro] = field(default_factory=dict)
    environments: dict[str, tuple[str, str]] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ reading definitions

    def read(self, text: str) -> str:
        """Collect the definitions in `text` and return the text without them."""
        out: list[str] = []
        pos = 0
        while True:
            m = _DEFINERS.search(text, pos)
            if m is None:
                out.append(text[pos:])
                break
            out.append(text[pos : m.start()])
            end = self._definition(text, m)
            if end is None:  # not understood: leave it in place and go on after the keyword
                out.append(text[m.start() : m.end()])
                pos = m.end()
            else:
                pos = end
        return "".join(out)

    def _definition(self, text: str, m: re.Match[str]) -> int | None:
        kind = m.group("kind")
        i = _skip_ws(text, m.end())
        if kind in {"newenvironment", "renewenvironment"}:
            return self._environment(text, i)
        if kind in {"def", "gdef", "edef", "xdef"}:
            return self._def(text, i)
        if kind == "let":
            return self._let(text, i)
        if kind == "DeclareMathAlphabet":
            return i
        # \newcommand{\name}[n][default]{body}   or   \newcommand\name[n]{body}
        name, i = self._name(text, i)
        if name is None:
            return None
        i = _skip_ws(text, i)
        nargs, default = 0, None
        if (opt := read_bracket(text, i)) is not None:
            if kind == "DeclareMathOperator":
                return None
            nargs = int(opt[0]) if opt[0].strip().isdigit() else 0
            i = _skip_ws(text, opt[1])
            if (dflt := read_bracket(text, i)) is not None:
                default, i = dflt
                i = _skip_ws(text, i)
        body = read_group(text, i)
        if body is None:
            return None
        if kind == "DeclareMathOperator":
            star = "*" if m.group("star") else ""
            self.macros[name] = Macro(name, 0, None, f"\\operatorname{star}{{{body[0]}}}")
            return body[1]
        self.macros[name] = Macro(name, nargs, default, body[0])
        return body[1]

    @staticmethod
    def _name(text: str, i: int) -> tuple[str | None, int]:
        if i < len(text) and text[i] == "{":
            group = read_group(text, i)
            if group is None:
                return None, i
            inner = group[0].strip()
            m = re.fullmatch(r"\\([A-Za-z@]+|.)", inner)
            return (m.group(1), group[1]) if m else (None, i)
        m = _CMD_NAME.match(text, i)
        return (m.group(1), m.end()) if m else (None, i)

    def _def(self, text: str, i: int) -> int | None:
        name, i = self._name(text, i)
        if name is None:
            return None
        nargs = 0
        while i < len(text) and text[i] == "#" and i + 1 < len(text) and text[i + 1].isdigit():
            nargs += 1
            i += 2
        if i < len(text) and text[i] != "{":  # a delimited parameter: not supported
            self.failures.append(f"def with a delimited parameter: \\{name}")
            return None
        body = read_group(text, i)
        if body is None:
            return None
        self.macros[name] = Macro(name, nargs, None, body[0])
        return body[1]

    def _let(self, text: str, i: int) -> int | None:
        name, i = self._name(text, i)
        if name is None:
            return None
        i = _skip_ws(text, i)
        if i < len(text) and text[i] == "=":
            i += 1
        i = _skip_ws(text, i)
        target = _CMD_NAME.match(text, i)
        if target is None:
            return None
        self.macros[name] = Macro(name, 0, None, "\\" + target.group(1))
        return target.end()

    def _environment(self, text: str, i: int) -> int | None:
        group = read_group(text, i)
        if group is None:
            return None
        name, i = group
        i = _skip_ws(text, i)
        if read_bracket(text, i) is not None:
            self.failures.append(f"environment with arguments: {name}")
            return None
        begin = read_group(text, i)
        if begin is None:
            return None
        end = read_group(text, _skip_ws(text, begin[1]))
        if end is None:
            return None
        self.environments[name] = (begin[0], end[0])
        return end[1]

    # ------------------------------------------------------------------------- expanding

    def expand(self, text: str, *, max_passes: int = 40, max_chars: int = 6_000_000) -> str:
        """Expand every use of every known macro and environment, until nothing changes."""
        if not self.macros and not self.environments:
            return text
        starting = max(len(text), 1)
        for _ in range(max_passes):
            new = self._pass(text)
            new = self._environment_pass(new)
            if new == text:
                return text
            text = new
            if len(text) > max_chars or len(text) > 8 * starting + 50_000:
                self.failures.append("macro expansion grew without bound")
                return text
        self.failures.append("macro expansion did not settle (recursive definition?)")
        return text

    def _environment_pass(self, text: str) -> str:
        if not self.environments:
            return text

        def repl(m: re.Match[str]) -> str:
            name = m.group(2)
            pair = self.environments.get(name)
            if pair is None:
                return m.group(0)
            return pair[0] if m.group(1) == "begin" else pair[1]

        return re.sub(r"\\(begin|end)\{([^{}]+)\}", repl, text)

    def _pass(self, text: str) -> str:
        out: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            j = text.find("\\", i)
            if j < 0:
                out.append(text[i:])
                break
            out.append(text[i:j])
            m = _CONTROL_WORD.match(text, j)
            if m is None:  # \\ or \, or \{ : copy both characters
                out.append(text[j : j + 2])
                i = j + 2
                continue
            name = m.group(1)
            macro = self.macros.get(name)
            if macro is None:
                out.append(m.group(0))
                i = m.end()
                continue
            replaced = self._use(text, m.end(), macro)
            if replaced is None:
                out.append(m.group(0))
                i = m.end()
                continue
            body, i = replaced
            out.append(body)
        return "".join(out)

    def _use(self, text: str, i: int, macro: Macro) -> tuple[str, int] | None:
        args: list[str] = []
        if macro.nargs == 0:
            # a macro with no arguments swallows the space after its name
            return macro.body, i
        pos = i
        remaining = macro.nargs
        if macro.default is not None:
            probe = _skip_ws(text, pos)
            if (opt := read_bracket(text, probe)) is not None:
                args.append(opt[0])
                pos = opt[1]
            else:
                args.append(macro.default)
            remaining -= 1
        for _ in range(remaining):
            pos = _skip_ws(text, pos)
            if pos >= len(text):
                return None
            if text[pos] == "{":
                group = read_group(text, pos)
                if group is None:
                    return None
                args.append(group[0])
                pos = group[1]
            elif text[pos] == "\\":
                m = _CMD_NAME.match(text, pos)
                if m is None:
                    return None
                args.append(m.group(0))
                pos = m.end()
            else:
                args.append(text[pos])
                pos += 1
        body = macro.body
        if "#" in body:
            body = re.sub(
                r"#(\d)",
                lambda m: args[int(m.group(1)) - 1] if int(m.group(1)) <= len(args) else "",
                body,
            )
        return body, pos


# ------------------------------------------------------------------------------- extraction

DISPLAY_ENVS = (
    "equation",
    "align",
    "gather",
    "multline",
    "eqnarray",
    "flalign",
    "alignat",
    "displaymath",
)
INLINE_ENVS = ("math",)
SKIP_ENVS = frozenset(
    {
        "verbatim",
        "lstlisting",
        "minted",
        "tikzpicture",
        "pgfpicture",
        "filecontents",
        "comment",
        "thebibliography",
        "algorithm",
        "algorithmic",
        "tikzcd",
    }
)
FLOAT_ENVS = frozenset(
    {"figure", "table", "wrapfigure", "wraptable", "sidewaystable", "sidewaysfigure", "subfigure"}
)
NUMBERED_ENVS = frozenset(
    {"equation", "align", "gather", "multline", "eqnarray", "flalign", "alignat"}
)


@dataclass
class Item:
    """One piece of maths as typed in the source, before it is split into gold equations."""

    index: int
    latex: str
    display: bool
    env: str  # an environment name, "$", "$$", "\\[" or "\\("
    offset: int  # position in the body
    word_pos: int = 0  # words of main-stream prose before it
    float_id: int | None = None
    starred: bool = False


@dataclass
class Segment:
    text: str
    float_id: int | None = None


@dataclass
class Extraction:
    items: list[Item]
    segments: list[Segment]
    n_floats: int
    skipped_environments: dict[str, int] = field(default_factory=dict)
    unbalanced: int = 0


_BEGIN = re.compile(r"\\begin\{([A-Za-z]+\*?)\}")
_MATH_NAMES = {n for e in DISPLAY_ENVS for n in (e, e + "*")} | {"math", "math*"}


def _env_end(text: str, name: str, start: int) -> int | None:
    """Index just after the `\\end{name}` closing a `\\begin{name}` whose body starts at `start`."""
    depth = 1
    pattern = re.compile(r"\\(begin|end)\{" + re.escape(name) + r"\}")
    for m in pattern.finditer(text, start):
        depth += 1 if m.group(1) == "begin" else -1
        if depth == 0:
            return m.end()
    return None


def body_of(flat: str) -> str:
    """The text between `\\begin{document}` and `\\end{document}` (everything, if absent)."""
    start = flat.find("\\begin{document}")
    end = flat.rfind("\\end{document}")
    if start >= 0:
        start += len("\\begin{document}")
    else:
        start = 0
    return flat[start : end if end > start else len(flat)]


def extract(body: str) -> Extraction:
    r"""Cut the maths out of `body` (macros already expanded), in reading order."""
    items: list[Item] = []
    segments: list[Segment] = [Segment("")]
    skipped: dict[str, int] = {}
    unbalanced = 0
    float_stack: list[int] = []  # ids of the floats we are in
    float_ends: list[int] = []
    n_floats = 0
    i = 0
    n = len(body)
    text_start = 0

    def flush(upto: int) -> None:
        nonlocal text_start
        if upto > text_start:
            segments[-1].text += body[text_start:upto]
        text_start = upto

    def current_float() -> int | None:
        return float_stack[-1] if float_stack else None

    def new_segment() -> None:
        segments.append(Segment("", current_float()))

    def add_item(latex: str, display: bool, env: str, offset: int, starred: bool = False) -> None:
        # a placeholder mark in the prose keeps the order of text and maths
        segments[-1].text += f" \x00M{len(items)}\x00 "
        items.append(
            Item(
                index=len(items),
                latex=latex.strip(),
                display=display,
                env=env,
                offset=offset,
                float_id=current_float(),
                starred=starred,
            )
        )

    while i < n:
        while float_ends and i >= float_ends[-1]:
            flush(float_ends[-1])
            float_ends.pop()
            float_stack.pop()
            new_segment()
        ch = body[i]
        if ch == "\\":
            nxt = body[i + 1] if i + 1 < n else ""
            if nxt == "[":
                end = body.find("\\]", i + 2)
                if end < 0:
                    unbalanced += 1
                    i += 2
                    continue
                flush(i)
                add_item(body[i + 2 : end], True, "\\[", i)
                i = text_start = end + 2
                continue
            if nxt == "(":
                end = body.find("\\)", i + 2)
                if end < 0:
                    unbalanced += 1
                    i += 2
                    continue
                flush(i)
                add_item(body[i + 2 : end], False, "\\(", i)
                i = text_start = end + 2
                continue
            m = _BEGIN.match(body, i)
            if m:
                name = m.group(1)
                base = name.rstrip("*")
                if name in _MATH_NAMES:
                    close = _env_end(body, name, m.end())
                    if close is None:
                        unbalanced += 1
                        i = m.end()
                        continue
                    end_tag = len(f"\\end{{{name}}}")
                    inner = body[m.end() : close - end_tag]
                    flush(i)
                    display = base in DISPLAY_ENVS
                    add_item(inner, display, base, i, starred=name.endswith("*"))
                    i = text_start = close
                    continue
                if base in SKIP_ENVS:
                    close = _env_end(body, name, m.end())
                    skipped[base] = skipped.get(base, 0) + 1
                    flush(i)
                    i = text_start = close if close is not None else m.end()
                    continue
                if base in FLOAT_ENVS:
                    close = _env_end(body, name, m.end())
                    if close is not None:
                        flush(i)
                        n_floats += 1
                        float_stack.append(n_floats - 1)
                        float_ends.append(close)
                        new_segment()
                        i = m.end()
                        text_start = i
                        continue
                i = m.end()
                continue
            if nxt == "$":
                i += 2
                continue
            if body.startswith("\\verb", i):
                m2 = re.match(r"\\verb\*?(.)", body[i:])
                if m2:
                    delim = m2.group(1)
                    close = body.find(delim, i + len(m2.group(0)))
                    i = close + 1 if close >= 0 else i + 5
                    continue
            i += 2 if nxt else 1
            continue
        if ch == "$":
            if body.startswith("$$", i):
                end = body.find("$$", i + 2)
                if end < 0:
                    unbalanced += 1
                    i += 2
                    continue
                flush(i)
                add_item(body[i + 2 : end], True, "$$", i)
                i = text_start = end + 2
                continue
            end = i + 1
            while True:
                end = body.find("$", end)
                if end < 0 or body[end - 1] != "\\":
                    break
                end += 1
            if end < 0 or end - i > 4000 or "\n\n" in body[i:end]:
                unbalanced += 1
                i += 1
                continue
            flush(i)
            add_item(body[i + 1 : end], False, "$", i)
            i = text_start = end + 1
            continue
        i += 1
    flush(n)
    return Extraction(items, segments, n_floats, skipped, unbalanced)


# ------------------------------------------------------------------------- gold equations


@dataclass
class Equation:
    """One gold equation: an item, or one group of rows of an alignment environment."""

    item: int
    latex: str
    display: bool
    env: str
    rows: int = 1
    group: int = 0
    groups: int = 1
    float_id: int | None = None


_NUMBER_DROP = re.compile(
    r"\\(?:label|tag\*?|notag|nonumber|nobreak|allowdisplaybreaks|intertext|shortintertext)"
    r"(?![A-Za-z])(?:\s*\{[^{}]*\})?"
)
_SPACING_ROW_END = re.compile(r"^\s*(?:\[[^\]]*\])?")
_NESTED_BEGIN = re.compile(r"\\(begin|end)\{([A-Za-z]+\*?)\}")


def split_rows(body: str) -> list[str]:
    r"""Rows of an alignment body: split at `\\` outside braces and nested environments."""
    rows: list[str] = []
    depth = 0
    env_depth = 0
    start = 0
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            if body.startswith("\\\\", i) and depth == 0 and env_depth == 0:
                rows.append(body[start:i])
                i += 2
                if i < len(body) and body[i] == "*":
                    i += 1
                m = _SPACING_ROW_END.match(body[i:])
                i += m.end() if m else 0
                start = i
                continue
            m2 = _NESTED_BEGIN.match(body, i)
            if m2:
                env_depth += 1 if m2.group(1) == "begin" else -1
                i = m2.end()
                continue
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    rows.append(body[start:])
    return [r for r in rows if r.strip()]


def drop_alignment_marks(row: str) -> str:
    """The row without its top-level `&` (those inside `{}` or a nested environment stay)."""
    out = []
    depth = env_depth = 0
    i = 0
    while i < len(row):
        ch = row[i]
        if ch == "\\":
            m = _NESTED_BEGIN.match(row, i)
            if m:
                env_depth += 1 if m.group(1) == "begin" else -1
                out.append(m.group(0))
                i = m.end()
                continue
            out.append(row[i : i + 2])
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "&" and depth == 0 and env_depth == 0:
            out.append(" ")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _numbered(row: str) -> bool:
    return not re.search(r"\\(?:nonumber|notag)(?![A-Za-z])", row)


def _clean(latex: str) -> str:
    text = _NUMBER_DROP.sub("", latex)
    text = re.sub(r"\\(begin|end)\{split\}", r"\\\1{aligned}", text)
    return " ".join(text.split())


def split_environment(item: Item) -> list[Equation]:
    """The gold equations of one item (see the module text for the rules)."""
    base = item.env
    if base in {"align", "eqnarray", "flalign", "alignat", "gather", "multline"}:
        body = item.latex
        if base == "alignat":  # the column count `{n}` comes first
            m = re.match(r"\s*\{\d+\}", body)
            body = body[m.end() :] if m else body
        rows = split_rows(body)
        if not rows:
            return []
        if base == "multline":
            return [Equation(item.index, _clean(" ".join(rows)), True, base, rows=len(rows))]
        if base == "gather":
            return [
                Equation(item.index, _clean(r), True, base, rows=1, group=k, groups=len(rows))
                for k, r in enumerate(rows)
                if _clean(r)
            ]
        if item.starred:
            groups = [rows]
        else:  # a run of unnumbered rows belongs to the next numbered row
            groups, current = [], []
            for row in rows:
                current.append(row)
                if _numbered(row):
                    groups.append(current)
                    current = []
            if current:
                groups.append(current)
        out = []
        for k, group in enumerate(groups):
            if len(group) == 1:
                latex = _clean(drop_alignment_marks(group[0]))
            else:
                latex = (
                    "\\begin{aligned} "
                    + " \\\\ ".join(_clean(r) for r in group)
                    + " \\end{aligned}"
                )
            if latex:
                out.append(
                    Equation(
                        item.index, latex, True, base, rows=len(group), group=k, groups=len(groups)
                    )
                )
        return out
    latex = _clean(item.latex)
    if not latex:
        return []
    return [
        Equation(
            item.index, latex, item.display, base if base != "$" else "$", float_id=item.float_id
        )
    ]


def equations_of(items: list[Item]) -> list[Equation]:
    out: list[Equation] = []
    for item in items:
        for eq in split_environment(item):
            eq.float_id = item.float_id
            out.append(eq)
    return out


# ----------------------------------------------------------------------- prose and PDF words

_DROP_ARGS = re.compile(
    r"\\(?:cite[a-z]*|ref|eqref|autoref|cref|Cref|label|url|href|includegraphics|bibliography"
    r"|bibliographystyle|usepackage|input|include|footnotemark|pageref|nameref|hyperref|vspace|hspace"
    r"|setlength|thispagestyle|pagestyle|centering|noindent|bibitem)\*?(?:\[[^\]]*\])*(?:\{[^{}]*\})*"
)
_ACCENT = re.compile(r"\\['\"`^~=.uvHckrbd]\s*\{?([A-Za-z])\}?")
_COMMAND = re.compile(r"\\[A-Za-z@]+\*?")
_WORD = re.compile(r"[a-z]{4,}")
_MARK = re.compile(r"\x00M(\d+)\x00")


def fold(text: str) -> str:
    """Lower case, ligatures and accents folded, for word matching."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower().replace("\u00df", "ss").replace("\u00e6", "ae").replace("\u0153", "oe")


def prose_words(text: str) -> list[tuple[int | None, str]]:
    """Words of at least four letters from tex prose, with math marks as (index, "") entries."""
    text = _DROP_ARGS.sub(" ", text)
    text = _ACCENT.sub(r"\1", text)
    text = text.replace("\\&", " and ").replace("\\ ", " ")
    text = _COMMAND.sub(" ", text)
    out: list[tuple[int | None, str]] = []
    pos = 0
    for m in _MARK.finditer(text):
        out += [(None, w) for w in _WORD.findall(fold(text[pos : m.start()]))]
        out.append((int(m.group(1)), ""))
        pos = m.end()
    out += [(None, w) for w in _WORD.findall(fold(text[pos:]))]
    return out


def pdf_words(page_text: str) -> list[str]:
    text = re.sub(r"-\s*\n\s*", "", page_text)  # a word broken at the end of a line
    return _WORD.findall(fold(text))


@dataclass
class Placement:
    """Where an item is: one page (0-based), or an interval of pages it may be on."""

    low: int
    high: int

    @property
    def page(self) -> int | None:
        return self.low if self.low == self.high else None


def _lis(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Longest chain increasing in both coordinates (pairs sorted by the first)."""
    tails: list[int] = []
    tail_idx: list[int] = []
    prev: list[int] = [-1] * len(pairs)
    for k, (_, b) in enumerate(pairs):
        pos = bisect_left(tails, b)
        if pos == len(tails):
            tails.append(b)
            tail_idx.append(k)
        else:
            tails[pos] = b
            tail_idx[pos] = k
        prev[k] = tail_idx[pos - 1] if pos > 0 else -1
    chain = []
    k = tail_idx[-1] if tail_idx else -1
    while k >= 0:
        chain.append(pairs[k])
        k = prev[k]
    return chain[::-1]


def _unique_grams(words: list[str], n: int = 3) -> dict[tuple[str, ...], int]:
    seen: dict[tuple[str, ...], int] = {}
    dup: set[tuple[str, ...]] = set()
    for k in range(len(words) - n + 1):
        g = tuple(words[k : k + n])
        if g in seen:
            dup.add(g)
        else:
            seen[g] = k
    return {g: k for g, k in seen.items() if g not in dup}


@dataclass
class PageMap:
    placements: dict[int, Placement]
    anchors: int
    coverage: dict[int, float]  # page -> share of the page's words found in the source prose
    unplaced: list[int]


def page_map(extraction: Extraction, pages: list[str]) -> PageMap:
    """Place every item of `extraction` on the PDF pages (`pages`: the text of each page)."""
    pdf_stream: list[str] = []
    page_of_word: list[int] = []
    page_words: list[list[str]] = []
    for p, text in enumerate(pages):
        words = pdf_words(text)
        page_words.append(words)
        pdf_stream += words
        page_of_word += [p] * len(words)
    pdf_grams = _unique_grams(pdf_stream)

    main_words: list[str] = []
    main_marks: list[tuple[int, int]] = []  # (item index, words before it)
    float_words: dict[int, list[str]] = {}
    float_items: dict[int, list[int]] = {}
    for seg in extraction.segments:
        entries = prose_words(seg.text)
        if seg.float_id is None:
            for idx, w in entries:
                if idx is None:
                    main_words.append(w)
                else:
                    main_marks.append((idx, len(main_words)))
        else:
            fw = float_words.setdefault(seg.float_id, [])
            for idx, w in entries:
                if idx is None:
                    fw.append(w)
                else:
                    float_items.setdefault(seg.float_id, []).append(idx)

    # anchors: word 3-grams unique in both streams, as a monotone chain
    tex_grams = _unique_grams(main_words)
    pairs = sorted((tk, pdf_grams[g]) for g, tk in tex_grams.items() if g in pdf_grams)
    chain = _lis(pairs) if pairs else []
    anchor_tex = [a for a, _ in chain]
    anchor_pdf_page = [page_of_word[b] for _, b in chain]

    placements: dict[int, Placement] = {}
    last_page = max(len(pages) - 1, 0)
    for idx, wpos in main_marks:
        k = bisect_left(anchor_tex, wpos)
        before = anchor_pdf_page[k - 1] if k > 0 else 0
        after = anchor_pdf_page[k] if k < len(chain) else last_page
        placements[idx] = Placement(before, after)

    # floats: by their own words, wherever they are printed
    for fid, words in float_words.items():
        votes: dict[int, int] = {}
        for g in _unique_grams(words):
            if g in pdf_grams:
                p = page_of_word[pdf_grams[g]]
                votes[p] = votes.get(p, 0) + 1
        for idx in float_items.get(fid, []):
            if votes:
                top = max(votes, key=lambda p: votes[p])
                if votes[top] >= 2 or len(votes) == 1:
                    placements[idx] = Placement(top, top)
                    continue
            # no usable anchor: the float is near its place in the text, or a few pages later
            near = next(
                (placements[i] for i in range(idx - 1, -1, -1) if i in placements), Placement(0, 0)
            )
            placements[idx] = Placement(near.low, min(near.high + 2, last_page))

    vocabulary = set(main_words) | {w for ws in float_words.values() for w in ws}
    coverage = {}
    for p, words in enumerate(page_words):
        coverage[p] = sum(1 for w in words if w in vocabulary) / len(words) if words else 0.0
    unplaced = [it for it in range(len(extraction.items)) if it not in placements]
    return PageMap(placements, len(chain), coverage, unplaced)


def usable_pages(
    equations: list[Equation],
    placements: dict[int, Placement],
    coverage: dict[int, float],
    *,
    min_coverage: float = 0.9,
    min_equations: int = 5,
    skip_first: bool = True,
    unconvertible: set[int] | None = None,
) -> dict[int, list[int]]:
    """Pages (0-based) where every equation is known: page -> indexes into `equations`.

    A page is usable when each equation on it is placed on exactly that page, no equation with
    an interval of pages (or no place) may be on it, the share of its words found in the source
    prose is at least `min_coverage`, it holds at least `min_equations` equations, and none of
    its equations is in `unconvertible`.
    """
    bad = unconvertible or set()
    on_page: dict[int, list[int]] = {}
    blocked: set[int] = set()
    for k, eq in enumerate(equations):
        place = placements.get(eq.item)
        if place is None:
            blocked.update(range(len(coverage)))
        elif place.page is None:
            blocked.update(range(place.low, place.high + 1))
        else:
            on_page.setdefault(place.low, []).append(k)
    out: dict[int, list[int]] = {}
    for page, ks in sorted(on_page.items()):
        if page in blocked or (skip_first and page == 0):
            continue
        if coverage.get(page, 0.0) < min_coverage or len(ks) < min_equations:
            continue
        if any(k in bad for k in ks):
            continue
        out[page] = ks
    return out
