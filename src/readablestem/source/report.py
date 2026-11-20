"""What LaTeXML said about a source: its messages, parsed into a `SourceReport`.

`latexml` exits 0 for warnings and for errors alike (only 10 or more errors make it fail), so
a missing `\\input` file, an unclosed brace or an unknown command used to end as a finished
job. The program's messages are the evidence: one line per problem,
`Error:<category>:<object> <message> at <file>; line <n> col <m>`. `parse` reads them from the
copies the supervisor wrote (`latexml.out`, `post.out`, outside the folders the document can
write to, so a document cannot forge them) before the temporary folder is removed.

What is kept: every `Error:` and `Fatal:` line, and `Warning:missing_file:` lines that are
about the project (a class or a package the document loads that LaTeXML cannot find). A
helper package that one of LaTeXML's own bindings asks for (`hyperref` asks for `ltxcmds`,
`keyval`, ...) is not a problem: it is counted, never named.

Names come from the instructor's source, so the report is kept with the document and never
logged (`summary` gives the counts that may be).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from readablestem.core.types import SourceMark, SourceProblem, SourceReport
from readablestem.source.support import SUPPORT, distributed

MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_NAME = 120
MAX_PER_CATEGORY = 20
MAX_MARKS = 500
OUTSIDE = "a file outside the project"

_LINE = re.compile(
    r"^(?P<level>Error|Fatal|Warning):(?P<category>[A-Za-z_/]+):(?P<object>\S*)"
    r"(?:\s+(?P<message>.*?))?"
    r"(?:\s+at\s+(?P<file>[^;]+);\s*line\s+(?P<line>\d+)(?:\s+col\s+\d+)?)?\s*$"
)
_COMPLETE = re.compile(r"(?:Conversion|Postprocessing) complete:?.*?(\d+) errors?\b")
_BINDING = re.compile(r"Can't find binding for (class|package) (\S+)")
_RAW = re.compile(r"Can't find package (\S+)")
_BIBLIOGRAPHY = re.compile(r"Couldn't find Bibliography")


def clean(text: str) -> str:
    """A name from the source as it may be shown: printable characters, at most 120."""
    printable = "".join(c for c in text if c.isprintable())
    return printable.strip()[:MAX_NAME]


def loaded_packages(texts: dict[str, str]) -> set[str]:
    """The packages the project's `.tex` files load by a literal `\\usepackage` (or
    `\\RequirePackage`), comments removed: the same reading the upload check makes
    (`source.intake.loaded_packages`)."""
    from readablestem.source.intake import loaded_packages as literal

    return set(literal(texts))


def _relative(name: str, prefixes: tuple[str, ...]) -> str:
    """`name` relative to the project; "" for LaTeXML's own files; `OUTSIDE` for any other
    absolute path (the Landlock backend shows host paths: the prefix goes)."""
    name = name.strip()
    if not name or name == "String" or name.endswith(".ltxml") or name.startswith("Anonymous"):
        return ""
    for prefix in prefixes:
        if prefix and name.startswith(prefix):
            return clean(name[len(prefix) :].lstrip("/"))
    if name.startswith("/") or ".." in Path(name).parts:
        return OUTSIDE
    return clean(name)


def _read(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            return handle.read(MAX_LOG_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def parse(
    out: Path,
    *,
    project: Path | None = None,
    loaded: set[str] | frozenset[str] = frozenset(),
) -> SourceReport:
    """The report of the run whose messages are in `out/latexml.out` and `out/post.out`.
    `project`: the project folder (its path is stripped from file names); `loaded`: the
    packages the document loads (`loaded_packages`), so that a binding's own missing helper is
    told apart from a package the document asked for."""
    prefixes = ("/work/", f"{project}/" if project is not None else "")
    report = SourceReport()
    kept: dict[str, int] = {}
    for step, name in (("latexml", "latexml.out"), ("post", "post.out")):
        errors_here = 0
        stated: int | None = None
        for raw in _read(out / name).splitlines():
            line = raw.strip()
            if done := _COMPLETE.search(line):
                stated = int(done.group(1))
                continue
            match = _LINE.match(line)
            if match is None:
                continue
            level, category = match["level"], match["category"]
            file = _relative(match["file"] or "", prefixes)
            number = int(match["line"]) if match["line"] else None
            message = match["message"] or ""
            if level == "Warning":
                report.warnings += 1
                if category != "missing_file":
                    continue
                problem = _missing(match["object"], message, file, number, loaded)
                if problem is not None and problem not in report.missing:
                    report.missing.append(problem)
                continue
            errors_here += 1
            obj = match["object"]
            if step == "post" and category == "missing_file":
                # `latexmlpost` names no file of the project (its "at" is a Perl location); a
                # bibliography it cannot find is the `.bib` file `\bibliography` asked for
                file, number = "", None
                if _BIBLIOGRAPHY.search(message) and not obj.endswith(".bib"):
                    obj = f"{obj}.bib"
            report.counts[category] = report.counts.get(category, 0) + 1
            if kept.get(category, 0) >= MAX_PER_CATEGORY:
                report.omitted[category] = report.omitted.get(category, 0) + 1
                continue
            kept[category] = kept.get(category, 0) + 1
            report.problems.append(
                SourceProblem(
                    level="fatal" if level == "Fatal" else "error",
                    category=category,
                    name=clean(obj),
                    file=file,
                    line=number,
                    step=step,
                )
            )
        report.log_errors += max(errors_here, stated or 0)
    return report


def _missing(
    obj: str, message: str, file: str, line: int | None, loaded: set[str] | frozenset[str]
) -> SourceProblem | None:
    """A `Warning:missing_file` line as a problem, or None when it is a helper that one of
    LaTeXML's own bindings asks for and the document never named."""
    if binding := _BINDING.search(message):
        what = binding.group(1)
        return SourceProblem(
            level="missing", category="missing_file", name=clean(binding.group(2)),
            file=file, line=line, what=what,  # ty: ignore[invalid-argument-type]
        )  # fmt: skip
    if (raw := _RAW.search(message)) and not file:
        package = raw.group(1)
        if package not in loaded:
            return None  # a binding's helper (ltxcmds for hyperref): harmless
        return SourceProblem(
            level="missing", category="missing_file", name=clean(package), line=None,
            what="package_source",
        )  # fmt: skip
    if not file:
        return None
    return SourceProblem(
        level="missing", category="missing_file", name=clean(obj), file=file, line=line,
        what="file",
    )  # fmt: skip


def with_marks(report: SourceReport, marks: list[SourceMark], error_nodes: int) -> SourceReport:
    """The report with the reader's side: the error nodes it found and where it placed them."""
    report.error_nodes = error_nodes
    report.marks = list(marks[:MAX_MARKS])
    placed = sum(1 for m in marks if m.element_id is not None)
    report.unplaced_errors = max(0, error_nodes - placed)
    return report


def summary(report: SourceReport) -> dict[str, int]:
    """Counts for a log line: per category and the three totals. Never a name."""
    counts = {f"errors_{k}": v for k, v in sorted(report.counts.items()) if re.match(r"^\w+$", k)}
    return {
        **counts,
        "log_errors": report.log_errors,
        "error_nodes": report.error_nodes,
        "unplaced_errors": report.unplaced_errors,
        "missing": len(report.missing),
        "warnings": report.warnings,
        "unread_words": report.unread_words,
        "unread_pictures": report.unread_pictures,
    }


# ---------------------------------------------------------------------------- the stop rule

# A source problem outside a formula stops the job; a problem inside a formula whose item holds
# a decision about it, or a missing picture, is a required decision on that item; a warning
# never blocks. The codes, in the order they are looked for (the first that applies names the
# cause):
STOP_CODES = (
    "source_missing_file",  # a file named by \input, \bibliography, ... is not in the upload
    "source_loop",  # LaTeXML says a command calls itself (`LOOPS`)
    "source_syntax_error",  # a brace or an environment is not closed, or a special character
    # (^ _ & #) is in the text: what follows is wrong or gone
    "source_unsupported_class",  # a class with no binding, followed by errors
    "source_unsupported_package",  # commands from a package this tool cannot read
    "source_undefined",  # commands or environments that are not defined, outside a formula
    "source_failed",  # any other error LaTeXML reported outside a formula
)
SYNTAX = frozenset({"expected", "unexpected"})
# What LaTeXML itself prints when a command calls itself: `Error:recursion:\a Token \a expands
# into itself!` (the run goes on and ends in seconds), and the two fatal stops of a nest that
# never closes (`\def\a{{\a}}`: Perl's own "Deep recursion", or LaTeXML's "Excessive
# recursion"). As (category, object): only the first names the command. Measured 2026-11-19; a
# loop that never names itself (`\def\a{\b}\def\b{\a}`) or only grows (`\def\a{x\a}`) is
# told from a long run by nothing in the log, and is left to the time and memory limits.
LOOPS = frozenset({("perl", "deep_recursion"), ("internal", "<recursion>")})
# A character TeX reads only in maths (^ _), between cells (&) or in a definition (#), written
# in running text: LaTeXML says `unexpected:^` or `misdefined:&`, and the stop names it.
CHARACTERS = {"unexpected": frozenset({"^", "_"}), "misdefined": frozenset({"&", "#"})}
MAX_DETAIL_ITEMS = 20


def _item(problem: SourceProblem) -> dict[str, object]:
    return {"name": problem.name, "file": problem.file, "line": problem.line}


def _loop(problem: SourceProblem) -> bool:
    return problem.category == "recursion" or (problem.category, problem.name) in LOOPS


def _character(problem: SourceProblem) -> bool:
    return problem.name in CHARACTERS.get(problem.category, frozenset())


def _detail(
    items: list[dict[str, object]], *, total: int | None = None, **names: str
) -> dict[str, object]:
    total = len(items) if total is None else total
    kept = items[:MAX_DETAIL_ITEMS]
    return {"items": kept, "more": max(0, total - len(kept)), **names}


# The elements LaTeXML's Beamer binding names in a `malformed` error on an ordinary deck,
# with nothing lost (the decks in tests/fixtures/source/beamer convert whole with them):
# `\titlepage` inside a frame ("<ltx:titlepage> isn't allowed in <ltx:g>", or in
# <ltx:slide>), a list with `\pause` in it (`ltx:item` in an `ltx:inline-block`, then the
# `ltx:itemize` and `ltx:inline-block` it cannot close) and a frame's slides nested in one
# another (`ltx:slidesequence` in `ltx:slidesequence`).
_BEAMER_MALFORMED = frozenset(
    {"ltx:titlepage", "ltx:item", "ltx:itemize", "ltx:inline-block", "ltx:slidesequence"}
)


def _beamer_binding(problem: SourceProblem) -> bool:
    """A complaint LaTeXML's Beamer binding makes about its own output on an ordinary deck:
    a `malformed` error about one of `_BEAMER_MALFORMED`, or its internal frame macro
    (`\\end{beamer@frame}`, a name no deck writes). A `malformed` error about any other
    element still stops a deck. `SourceProblem` keeps the element, not the one it was put
    in, so a list element that is malformed for another reason on a deck is let through as
    well."""
    if problem.category == "malformed":
        return problem.name in _BEAMER_MALFORMED
    return "beamer@" in problem.name


def _origin(names: Iterable[str]) -> str | None:
    """Where the missing packages come from, for the stop sentence: "own" when one of them is
    not a TeX distribution package (the instructor's own), "distribution" when all are, None
    when the image wrote no list of names (`source.support.distributed`)."""
    found = [distributed(name) for name in names]
    if False in found:
        return "own"
    if found and all(found):
        return "distribution"
    return None


def stop(report: SourceReport, *, beamer: bool = False) -> tuple[str, dict[str, object]] | None:
    """The stop code for a report and its details (names from the source, for the job page
    only), or None when the source converts. Errors of the `latexml` step stop a job;
    `latexmlpost` checks the result's shape and its complaints follow from those errors, except
    a file it cannot find (a bibliography). On a Beamer deck the binding's own complaints do
    not stop it (`_beamer_binding`).

    A command in a formula is left to its decision only when every place the reader tied it
    to holds one (`SourceMark.decided`). When a package with no binding is loaded and
    LaTeXML's first error is a command it does not define, that package is the cause, even
    when a syntax error follows (an mhchem charge in the text, `\\ce{SO4^2-}`)."""
    errors = [
        p for p in report.problems if p.step == "latexml" and not (beamer and _beamer_binding(p))
    ]
    undecided = {m.name for m in report.marks if not m.decided}
    decided = {m.name for m in report.marks if m.in_formula and m.decided} - undecided
    if found := [p for p in report.problems if p.category == "missing_file"]:
        return "source_missing_file", _detail([_item(p) for p in found])
    if loops := [p for p in errors if _loop(p)]:  # the first command named, when one is
        return "source_loop", _detail([_item(p) for p in loops if p.category == "recursion"][:1])
    items: dict[str, dict[str, object]] = {}  # each command once, at its first place
    for p in errors:
        if p.category == "undefined" and p.name not in decided:
            items.setdefault(p.name, _item(p))
    for m in report.marks:  # an error node outside a formula the log did not name
        if not m.in_formula and m.name:
            items.setdefault(m.name, {"name": m.name, "file": "", "line": None})
    total = len(items) + (report.omitted.get("undefined", 0) if items else 0)
    # A class read as an article (`source.support`) is not the cause of what follows it.
    classes = [
        p for p in report.missing if p.what == "class" and SUPPORT.classes.kind(p.name) != "plain"
    ]
    packages = [p for p in report.missing if p.what in ("package", "package_source")]
    first = next((p for p in errors if not (p.category == "undefined" and p.name in decided)), None)
    package_first = bool(items and packages and not classes) and (
        first is not None and first.category == "undefined"
    )
    syntax = [p for p in errors if p.level == "fatal" or p.category in SYNTAX or _character(p)]
    if syntax and not package_first:
        if all(_character(p) for p in syntax):
            spots = [_item(p) for p in syntax]
            return "source_syntax_error", _detail(spots, character=syntax[0].name)
        cause = next(p for p in syntax if not _character(p))
        return "source_syntax_error", _detail([_item(cause)], total=1)
    if classes and (errors or report.marks):
        return "source_unsupported_class", _detail([], total=0, **{"class": classes[0].name})
    if items and packages:
        detail = _detail(
            list(items.values()), total=total, package=", ".join(p.name for p in packages[:5])
        )
        if origin := _origin(p.name for p in packages):
            detail["origin"] = origin
        if origin == "own":  # name the instructor's own: those are the ones to copy from
            own = [p.name for p in packages if distributed(p.name) is False]
            detail["package"] = ", ".join(own[:5])
        return "source_unsupported_package", detail
    if items:
        return "source_undefined", _detail(list(items.values()), total=total)
    other = [p for p in errors if p.category != "undefined"]
    if other:
        return "source_failed", _detail([_item(p) for p in other])
    return None
