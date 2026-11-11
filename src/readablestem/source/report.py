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
from pathlib import Path

from readablestem.core.types import SourceMark, SourceProblem, SourceReport

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
_PACKAGES = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\]\s*)?\{([^}]*)\}")


def clean(text: str) -> str:
    """A name from the source as it may be shown: printable characters, at most 120."""
    printable = "".join(c for c in text if c.isprintable())
    return printable.strip()[:MAX_NAME]


def loaded_packages(texts: dict[str, str]) -> set[str]:
    """The packages the project's own files load by a literal `\\usepackage` (or
    `\\RequirePackage`)."""
    found: set[str] = set()
    for text in texts.values():
        for match in _PACKAGES.finditer(text):
            found.update(p.strip() for p in match.group(1).split(",") if p.strip())
    return found


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
            report.counts[category] = report.counts.get(category, 0) + 1
            if kept.get(category, 0) >= MAX_PER_CATEGORY:
                report.omitted[category] = report.omitted.get(category, 0) + 1
                continue
            kept[category] = kept.get(category, 0) + 1
            report.problems.append(
                SourceProblem(
                    level="fatal" if level == "Fatal" else "error",
                    category=category,
                    name=clean(match["object"]),
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
    }
