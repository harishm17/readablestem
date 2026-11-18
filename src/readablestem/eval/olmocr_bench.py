"""A public benchmark for the reader: olmOCR-Bench, scored by its own checker (A27).

olmOCR-Bench (Allen Institute for AI, ODC-BY-1.0, `huggingface.co/datasets/allenai/olmOCR-bench`)
is 1,403 one-page PDFs with 7,010 pass-or-fail checks on the markdown an OCR tool writes for
each page: text present, text absent (running heads and feet), reading order, tables and math.
Its README asks for research and educational use, which a measure of our own reader is.

Nothing of it is in this repository: not a PDF, not a check, not a page's text. `fetch` puts it
in a folder outside the repository, at one pinned revision, and `run` reads that folder.

What runs here, at $0
---------------------
Each page goes through the product's own conversion path (`web.isolated_convert`, as a job
converts a file, with production's behavior settings and a client that refuses every model
request), so the PDF is read as hostile input and no model is called. The student page it
makes (`DocumentResult.html`) is turned into markdown by `html_to_markdown` and scored by the
benchmark's own checker (`proof/parsers/runners/olmocr_check.py`, the pinned `olmocr` release
in a separate environment, with no model in it).

Categories, and what is left out and why (`CATEGORIES`):

- `multi_column` (reading order), `headers_footers` (text that must be absent), `long_tiny_text`
  (text present), `table_tests` (tables) and the baseline check of each of those pages are
  code in the product and are run.
- `arxiv_math` and `old_scans_math` are equations: the product reads them with a model, so no
  check of them is run here. `old_scans` are scans with no text layer: also read with a model.
- A page the product itself types as a scan (`measure.page_kinds`) is dropped from a run
  category: its text is not in the file, a model reads its picture.
- A page the conversion refuses or cannot finish is dropped and named with its code.

A page that asks the model for something at $0 (an equation or a figure) is still scored: its
text checks do not depend on the answer unless the check holds the equation. Its requests are
counted per step so that a failure can be put down to them.

Every page's result is one file outside the repository (`pages/<category>/<name>.json`, which
holds the markdown), so a run that stops can be resumed and a failure read afterwards. The
run record holds scores and counts only.
"""

from __future__ import annotations

import contextlib
import hashlib
import html.parser
import json
import re
import subprocess
import time
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from readablestem.core.paths import cache_dir

DATASET = "allenai/olmOCR-bench"
# The revision of the dataset every number here was measured on (2026-02-19).
REVISION = "54a96a6fb6a2bd3b297e59869491db4d3625b711"
LICENSE = "ODC-BY-1.0"
# The checker is the benchmark's own, from its release on PyPI, with the packages it imports
# and nothing that runs a model. Math checks are not loaded, so no browser is installed.
CHECKER = "olmocr==0.4.27"
CHECKER_PACKAGES = (
    CHECKER,
    "fuzzysearch==0.8.1",
    "rapidfuzz==3.14.6",
    "tqdm==4.70.1",
    "beautifulsoup4==4.15.0",
    "numpy==2.5.5",
    "playwright==1.65.2",  # imported by the math checks' module; no browser is started
)
USER_AGENT = "dataset-fetcher/1.0"
MAX_AT_ONCE = 3
# Pages whose first `--limit` conversions are taken make a quick check of the plumbing.
RUNNER = Path(__file__).resolve().parents[3] / "proof" / "parsers" / "runners" / "olmocr_check.py"


@dataclass(frozen=True)
class Category:
    """One file of the benchmark: its published name, the column of its README table, the
    folder of its PDFs and whether it can be run at $0 (and if not, why)."""

    name: str
    column: str
    folder: str
    kind: str  # the check type it is made of
    skipped: str = ""


CATEGORIES = (
    Category("multi_column", "MultCol", "multi_column", "order"),
    Category("headers_footers", "Hdr/Ftr", "headers_footers", "absent"),
    Category("long_tiny_text", "TinyTxt", "long_tiny_text", "present"),
    Category("table_tests", "Tables", "tables", "table"),
    Category(
        "arxiv_math", "ArXiv", "arxiv_math", "math", "equations are read by a model in the product"
    ),
    Category(
        "old_scans_math",
        "OldMath",
        "old_scans_math",
        "math",
        "equations are read by a model in the product",
    ),
    Category(
        "old_scans",
        "OldScan",
        "old_scans",
        "present, absent, order",
        "scans have no text layer; a model reads their picture",
    ),
)
BASELINE = "baseline"

# Published scores of other tools (README of the revision above; percent). Quoted next to ours
# for the columns we ran, never as a total: ours covers a subset.
_PUBLISHED_COLUMNS = ("MultCol", "Hdr/Ftr", "TinyTxt", "Tables", "Base")
_PUBLISHED_ROWS = (
    ("GOT OCR", (42.0, 93.6, 29.9, 0.2, 94.0)),
    ("Marker v1.6.2", (71.0, 87.1, 76.9, 69.8, 99.5)),
    ("MinerU v1.3.10", (59.0, 96.6, 39.1, 60.9, 96.6)),
    ("Mistral OCR API", (71.3, 93.6, 77.1, 60.6, 99.4)),
    ("GPT-4o (Anchored)", (69.3, 93.8, 60.6, 70.0, 96.8)),
    ("Gemini Flash 2 (Anchored)", (61.5, 64.7, 71.5, 72.1, 95.6)),
    ("Qwen 2.5 VL (No Anchor)", (68.3, 73.6, 49.1, 67.3, 98.3)),
    ("olmOCR (Anchored)", (79.4, 93.4, 81.7, 70.2, 99.0)),
)
PUBLISHED: dict[str, dict[str, float]] = {
    name: dict(zip(_PUBLISHED_COLUMNS, scores, strict=True)) for name, scores in _PUBLISHED_ROWS
}


# --- Fetching ------------------------------------------------------------------------------


def default_folder() -> Path:
    return cache_dir() / "bench" / "olmocr-bench"


def checker_python(folder: Path) -> Path:
    return folder.parent / "checker-env" / "bin" / "python"


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def fetch(folder: Path, *, said: Callable[[str], None] = print) -> dict[str, Any]:
    """The benchmark at `REVISION` in `folder` (files already there with the right size are
    kept), and the checker's environment beside it. Sends nothing but a generic user agent.
    Returns what `FETCHED.json` says."""
    listing = json.loads(
        _get(f"https://huggingface.co/api/datasets/{DATASET}?blobs=true&revision={REVISION}")
    )
    wanted = [
        (row["rfilename"], row["size"])
        for row in listing["siblings"]
        if row["rfilename"].startswith("bench_data/") and not row["rfilename"].endswith(".DS_Store")
    ] + [
        ("README.md", next(r["size"] for r in listing["siblings"] if r["rfilename"] == "README.md"))
    ]
    fresh = 0
    for name, size in wanted:
        target = folder / name
        if target.is_file() and target.stat().st_size == size:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        data = _get(f"https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}/{name}")
        if len(data) != size:
            raise OSError(f"{name}: {len(data)} bytes, expected {size}")
        part = target.with_name(target.name + ".part")
        part.write_bytes(data)
        part.replace(target)
        fresh += 1
        if fresh % 100 == 0:
            said(f"fetched {fresh} files")
    fetched = {
        "dataset": DATASET,
        "revision": REVISION,
        "license": LICENSE,
        "files": len(wanted),
        "fetched_now": fresh,
        "checker": list(CHECKER_PACKAGES),
    }
    (folder / "FETCHED.json").write_text(json.dumps(fetched, indent=1) + "\n", encoding="utf-8")
    python = checker_python(folder)
    if not python.is_file():
        env = python.parents[1]
        subprocess.run(["uv", "venv", "-q", "--python", "3.12", str(env)], check=True)
        subprocess.run(
            ["uv", "pip", "install", "-q", "--python", str(python), "--no-deps", CHECKER],
            check=True,
        )
        subprocess.run(
            ["uv", "pip", "install", "-q", "--python", str(python), *CHECKER_PACKAGES[1:]],
            check=True,
        )
    return fetched


def read_license(folder: Path) -> str:
    """The license the fetched README states, which a run record repeats."""
    text = (folder / "README.md").read_text(encoding="utf-8")
    found = re.search(r"^license:\s*(\S+)", text, re.MULTILINE)
    return found.group(1).upper() if found else ""


# --- The adapter: the student page as the markdown the checker reads ------------------------

_SKIPPED = frozenset(
    {"script", "style", "nav", "annotation", "annotation-xml", "button", "details"}
)
_VOID = frozenset({"br", "img", "hr", "input", "meta", "link", "col"})
_BLOCKS = frozenset(
    {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "figcaption", "blockquote", "dt", "dd"}
)
_LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
}
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿⁢⁣⁤"))
_MATH_WORDS = frozenset({"mtext", "ms"})


def clean_text(text: str) -> str:
    """Text as the checker compares it: ligature letters spelled out, invisible characters
    gone, every kind of space one space. The checker folds quotes and dashes itself."""
    text = unicodedata.normalize("NFC", text)
    for ligature, letters in _LIGATURES.items():
        text = text.replace(ligature, letters)
    return re.sub(r"\s+", " ", text.translate(_INVISIBLE)).strip()


class _Markdown(html.parser.HTMLParser):
    """The text of the student page's `<main>`, block by block, in the page's order, with
    what the writer adds left out (the notice, the about section, a title it adds, labels,
    hidden text) and a table kept as an HTML table, which the checker reads."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        from readablestem.pipeline import assemble

        self.authored = assemble.AUTHORED_CLASS
        self.blocks: list[str] = []
        self.in_main = False
        self.skipping = 0
        self.open: list[list] = []  # [tag, texts] of the block being read
        self.loose: list[str] = []  # text outside a block
        self.table: list[list[tuple[str, str, int, int]]] | None = None
        self.depth = 0  # tables inside the first
        self.cell: list[Any] | None = None  # [tag, texts, rowspan, colspan]
        self.math = 0
        self.tag = ""
        self.lists: list[str] = []

    def _text(self, data: str) -> None:
        if self.cell is not None:
            self.cell[1].append(data)
        elif self.open:
            self.open[-1][1].append(data)
        else:
            self.loose.append(data)

    def _flush_loose(self) -> None:
        text = clean_text(" ".join(self.loose))
        self.loose = []
        if text:
            self.blocks.append(text)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        given = dict(attrs)
        if tag == "main":
            self.in_main = True
            return
        if not self.in_main:
            return
        if tag == "hr" and given.get("role") == "doc-pagebreak":
            return
        classes = (given.get("class") or "").split()
        if (
            self.skipping
            or given.get("aria-hidden") == "true"
            or tag in _SKIPPED
            or self.authored in classes
            or (tag == "h1" and not given.get("id"))  # the title the writer adds
        ):
            self.skipping += 0 if tag in _VOID else 1
            return
        self.tag = tag
        if tag == "br":
            self._text(" ")
        elif tag == "img":
            alt = self._unconverted_equation(given.get("alt") or "")
            if alt:
                self._text(f" {alt} ")
        elif tag == "math":
            self.math += 1
        elif tag == "table":
            self.depth += 1
            if self.depth == 1:
                self._flush_loose()
                self.table = []
        elif self.table is not None:
            if tag == "tr" and self.depth == 1:
                self.table.append([])
            elif tag in ("td", "th") and self.depth == 1:
                self.cell = [tag, [], _span(given, "rowspan"), _span(given, "colspan")]
        elif tag in ("ul", "ol"):
            self.lists.append(tag)
        elif tag in _BLOCKS:
            self._flush_loose()
            self.open.append([tag, []])

    @staticmethod
    def _unconverted_equation(alt: str) -> str:
        """The PDF's own text of an equation kept as a picture (its alt text before the
        writer's mark that it was not checked); "" for any other picture."""
        from readablestem.pipeline import assemble

        for mark in assemble.EQUATION_TEXT_ALT_SUFFIXES:
            if alt.endswith(mark):
                return alt[: -len(mark)]
        return ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "main":
            self.in_main = False
            self._flush_loose()
            return
        if not self.in_main:
            return
        if self.skipping:
            if tag not in _VOID:
                self.skipping -= 1
            return
        self.tag = ""
        if tag == "math" and self.math:
            self.math -= 1
        elif tag == "table" and self.depth:
            self.depth -= 1
            if self.depth == 0 and self.table is not None:
                self.blocks.append(_table_html(self.table))
                self.table = None
        elif self.table is not None:
            if tag in ("td", "th") and self.cell is not None and self.depth == 1:
                kind, texts, rows, columns = self.cell
                self.table[-1].append((kind, clean_text(" ".join(texts)), rows, columns))
                self.cell = None
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
        elif tag in _BLOCKS:
            for at in range(len(self.open) - 1, -1, -1):
                if self.open[at][0] == tag:
                    _, texts = self.open.pop(at)
                    text = clean_text(" ".join(texts) if tag == "pre" else "".join(texts))
                    if text:
                        self.blocks.append(_prefix(tag, self.lists) + text)
                    break

    def handle_data(self, data: str) -> None:
        if not self.in_main or self.skipping:
            return
        if self.math and self.tag not in _MATH_WORDS:
            data = data.strip()
        self._text(data)


def _span(given: dict, name: str) -> int:
    try:
        return max(1, int(given.get(name) or 1))
    except ValueError:
        return 1


def _prefix(tag: str, lists: list[str]) -> str:
    if tag.startswith("h") and len(tag) == 2:
        return "#" * int(tag[1]) + " "
    if tag == "li":
        return "  " * max(0, len(lists) - 1) + "- "
    return ""


def _table_html(rows: list[list[tuple[str, str, int, int]]]) -> str:
    """A table as plain HTML: rows of `th` and `td` with their spans. The checker reads a
    table's cells, spans and header cells from it."""
    lines = ["<table>"]
    for row in rows:
        cells = []
        for kind, text, rowspan, colspan in row:
            spans = (f' rowspan="{rowspan}"' if rowspan > 1 else "") + (
                f' colspan="{colspan}"' if colspan > 1 else ""
            )
            cells.append(f"<{kind}{spans}>{_escape(text)}</{kind}>")
        lines.append("<tr>" + "".join(cells) + "</tr>")
    lines.append("</table>")
    return "\n".join(lines)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def html_to_markdown(document: str) -> str:
    """The student page's text as markdown: headings with `#`, list items with `- `, a table
    as an HTML table, blocks one blank line apart, in the order the page is read."""
    parser = _Markdown()
    parser.feed(document)
    parser.close()
    return "\n\n".join(parser.blocks) + "\n"


# --- Converting a page ---------------------------------------------------------------------


def _page_facts(result: Any, facts: Any) -> dict[str, Any]:
    """What the product made of the page, content-free: its kind for the cost model, its
    page type, where the product reads the page's content from (a page read from its picture
    needs a model, and is dropped from a run), and how many visible characters its text
    layer holds."""
    from readablestem.eval import measure

    kinds = measure.page_kinds(result, facts)
    types = result.page_types
    route = types[0].route if types else ""
    return {
        "kind": kinds[0] if kinds else "none",
        "page_type": types[0].kind if types else "",
        "route": route,
        "visible_chars": types[0].visible_chars if types else 0,
        "scan": bool(kinds and kinds[0] == "scan") or route == "vision_page",
    }


def convert_page(pdf: bytes) -> dict[str, Any]:
    """One one-page PDF through the product's conversion with no model: what it came to, as
    plain data (`markdown` is the page's text and stays out of any record)."""
    from readablestem.core.types import ConversionOptions, StepFacts
    from readablestem.eval import order_report
    from readablestem.models.fake import FakeVisionClient
    from readablestem.web.isolated_convert import convert_isolated

    settings = order_report.set_settings()
    client = FakeVisionClient()  # refuses every request: no model is called
    started = time.monotonic()
    try:
        output = convert_isolated(
            pdf,
            settings=settings,
            client=client,
            options=ConversionOptions.from_settings(settings),
            concurrency=settings.model_concurrency,
        )
    except Exception as exc:
        from readablestem.web.worker import error_code_for

        return {"error": error_code_for(exc), "seconds": round(time.monotonic() - started, 2)}
    result = output.result
    return {
        "error": "",
        "pages": result.stats.pages,
        **_page_facts(result, output.facts or StepFacts()),
        "asked": dict(Counter(request.step for request in client.requests)),
        "stopped": [stop.step for stop in (output.facts or StepFacts()).not_finished],
        "seconds": round(time.monotonic() - started, 2),
        "markdown": html_to_markdown(result.html),
    }


def _convert_file(pdf_path: str, page_path: str) -> str:
    """A process of `convert_all`: one PDF converted and its page file written."""
    pdf = Path(pdf_path).read_bytes()
    done = convert_page(pdf) | {"pdf_sha256": hashlib.sha256(pdf).hexdigest()}
    target = Path(page_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    part.write_text(json.dumps(done), encoding="utf-8")
    part.replace(target)
    return done["error"]


def pdfs_of(folder: Path, category: Category) -> list[str]:
    """The PDFs of a category as the benchmark's files name them (`<folder>/<name>.pdf`)."""
    root = folder / "bench_data" / "pdfs" / category.folder
    return sorted(f"{category.folder}/{p.name}" for p in root.glob("*.pdf"))


def convert_all(
    folder: Path,
    run_dir: Path,
    categories: list[Category],
    *,
    at_once: int,
    limit: int | None = None,
    said: Callable[[str], None] | None = None,
) -> int:
    """Convert every PDF of `categories` whose page file is not in `run_dir/pages` yet, at
    most `at_once` at a time, each in a process of its own. Returns how many were converted
    now."""
    todo: list[tuple[str, str]] = []
    for category in categories:
        names = pdfs_of(folder, category)[:limit]
        for name in names:
            page = run_dir / "pages" / name.removesuffix(".pdf")
            if not page.with_suffix(".json").is_file():
                todo.append(
                    (str(folder / "bench_data" / "pdfs" / name), str(page.with_suffix(".json")))
                )
    if not todo:
        return 0
    with ProcessPoolExecutor(
        max_workers=min(max(1, at_once), MAX_AT_ONCE), max_tasks_per_child=1
    ) as pool:
        for number, error in enumerate(pool.map(_convert_file, *zip(*todo, strict=True)), 1):
            if said is not None and (error or number % 25 == 0):
                said(f"{number}/{len(todo)}" + (f" ({error})" if error else ""))
    return len(todo)


# --- Scoring -------------------------------------------------------------------------------


def write_markdown(run_dir: Path) -> int:
    """The markdown files the checker reads, from the page files; returns how many."""
    count = 0
    for page in sorted((run_dir / "pages").rglob("*.json")):
        done = json.loads(page.read_text(encoding="utf-8"))
        target = run_dir / "markdown" / page.relative_to(run_dir / "pages").with_suffix(".md")
        if done["error"] or done.get("scan"):
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(done["markdown"], encoding="utf-8")
        count += 1
    return count


def run_checker(folder: Path, run_dir: Path, categories: list[Category]) -> list[dict[str, Any]]:
    """The benchmark's checker over the run's markdown: one row per check."""
    python = checker_python(folder)
    if not python.is_file():
        raise FileNotFoundError("no checker environment: run with --fetch")
    out = run_dir / "checks.json"
    files = [f"{category.name}.jsonl" for category in categories]
    subprocess.run(
        [str(python), "-I", str(RUNNER), str(folder), str(run_dir / "markdown"), str(out), *files],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out.read_text(encoding="utf-8"))


def score(
    rows: list[dict[str, Any]], pages: dict[str, dict[str, Any]], categories: list[Category]
) -> dict[str, Any]:
    """Scores and counts by category (percent of the checks run that passed, with n) and
    what was left out of each and why. `pages`: the page file of each PDF."""
    by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_file["baseline" if row["type"] == "baseline" else row["file"]].append(row)

    def reason(row: dict[str, Any]) -> str:
        page = pages.get(row["pdf"])
        if page is None:
            return "not converted"
        if page["error"]:
            return f"refused: {page['error']}"
        if not page.get("scan"):
            return "no output"
        if page.get("page_type") == "scan_no_text":
            return "scan with no text layer"
        return "scan read from its picture (an OCR layer, if any, is not used)"

    def summary(group: list[dict[str, Any]]) -> dict[str, Any]:
        kept = [row for row in group if row["passed"] is not None]
        passed = sum(bool(row["passed"]) for row in kept)
        dropped = Counter(reason(row) for row in group if row["passed"] is None)
        plain = [row for row in kept if not (pages.get(row["pdf"]) or {}).get("asked")]
        return {
            "checks": len(group),
            "run": len(kept),
            "passed": passed,
            "score": round(100 * passed / len(kept), 1) if kept else None,
            "score_if_dropped_failed": round(100 * passed / len(group), 1) if group else None,
            "dropped": dict(sorted(dropped.items())),
            # The same score on the pages where the product asked no model for anything.
            "run_no_request": len(plain),
            "score_no_request": (
                round(100 * sum(bool(row["passed"]) for row in plain) / len(plain), 1)
                if plain
                else None
            ),
            "pdfs": len({row["pdf"] for row in group}),
            "pdfs_run": len({row["pdf"] for row in kept}),
        }

    metrics: dict[str, Any] = {}
    for category in categories:
        metrics[category.name] = summary(by_file[category.name]) | {"column": category.column}
    metrics[BASELINE] = summary(by_file["baseline"]) | {"column": "Base"}
    return metrics


def not_run(folder: Path, categories: list[Category]) -> dict[str, Any]:
    """The categories this run leaves out, with their PDFs and checks, counted from the
    benchmark's files (`reason` is why)."""
    counts: dict[str, Any] = {}
    for category in CATEGORIES:
        if category in categories or not category.skipped:
            continue
        checks = sum(
            1
            for line in (folder / "bench_data" / f"{category.name}.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        )
        counts[category.name] = {
            "pdfs": len(pdfs_of(folder, category)),
            "checks": checks,
            "reason": category.skipped,
            "column": category.column,
        }
    return counts


def page_files(run_dir: Path) -> dict[str, dict[str, Any]]:
    """The page files of a run, by the PDF's name in the benchmark's files."""
    out = {}
    for page in (run_dir / "pages").rglob("*.json"):
        relative = page.relative_to(run_dir / "pages").with_suffix(".pdf")
        out[str(relative)] = json.loads(page.read_text(encoding="utf-8"))
    return out


def run_summary(pages: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """What the conversions came to, without any page's text: how many were scans or were
    refused, which steps asked a model, and how long they took."""
    asked: Counter[str] = Counter()
    with_requests = 0
    for page in pages.values():
        asked.update(page.get("asked") or {})
        with_requests += bool(page.get("asked"))
    seconds = sorted(page["seconds"] for page in pages.values())
    return {
        "pages": len(pages),
        "scans": sum(bool(page.get("scan")) for page in pages.values()),
        "refused": dict(Counter(page["error"] for page in pages.values() if page["error"])),
        "page_types": dict(Counter(page.get("page_type", "") for page in pages.values())),
        "kinds": dict(Counter(page.get("kind", "") for page in pages.values())),
        "pages_that_asked_a_model": with_requests,
        "requests_refused_by_step": dict(sorted(asked.items())),
        "stopped_early": sum(bool(page.get("stopped")) for page in pages.values()),
        "seconds_median": seconds[len(seconds) // 2] if seconds else 0,
    }


def make_record(
    folder: Path, run_dir: Path, categories: list[Category], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """The run record: what ran, on which revision and code, the scores with n, what was
    left out. Counts only; no PDF name, no text of any page."""
    from readablestem.eval import measure, split
    from readablestem.eval.order_report import set_settings

    pages = page_files(run_dir)
    names = sorted(pages)
    hashes = {name: pages[name]["pdf_sha256"] for name in names}
    chosen = split.Split(seed="olmocr-bench", development=tuple(names), held_out=(), hashes=hashes)
    of_set = {
        "set": "olmocr-bench",
        "split": chosen.stamp(split.DEVELOPMENT),
        "client": "none",
    }
    metrics = score(rows, pages, categories)
    return measure.stamps([], set_settings(), of_set=of_set) | {
        "benchmark": {
            "dataset": DATASET,
            "revision": REVISION,
            "license": read_license(folder),
            "checker": CHECKER,
            "page": "olmOCR-Bench, Allen Institute for AI; fetched outside the repository",
        },
        "model_calls": 0,
        "usd": 0.0,
        "run": run_summary(pages),
        "metrics": metrics,
        "not_run": not_run(folder, categories),
        "published": {
            "source": "README of the fetched revision, percent; a tool's row is its own full run",
            "rows": {
                name: {
                    m["column"]: row[m["column"]] for m in metrics.values() if m["column"] in row
                }
                for name, row in PUBLISHED.items()
            },
        },
    }


def render(record: dict[str, Any]) -> str:
    """The record as the table a person reads, with the published rows beside it."""
    lines = [
        f"olmOCR-Bench {record['benchmark']['revision'][:12]} ({record['benchmark']['license']}), "
        f"checker {record['benchmark']['checker']}; pipeline {record['pipeline_version']}, "
        f"commit {record['commit']}; profile {record['profile']['sha256'][:12]}. "
        "$0, no model call.",
        f"{record['run']['pages']} pages converted, {record['run']['scans']} typed as scans "
        f"(dropped), {sum(record['run']['refused'].values())} refused; "
        f"{record['run']['pages_that_asked_a_model']} asked a model for something "
        f"(refused: {record['run']['requests_refused_by_step']}).",
        "",
        f"{'category':<17}{'score':>7}{'n run':>8}{'of':>7}   dropped",
    ]
    for name, row in record["metrics"].items():
        dropped = ", ".join(f"{count} {why}" for why, count in row["dropped"].items()) or "none"
        score_text = "-" if row["score"] is None else f"{row['score']:.1f}"
        lines.append(f"{name:<17}{score_text:>7}{row['run']:>8}{row['checks']:>7}   {dropped}")
    for name, row in record["not_run"].items():
        lines.append(f"{name:<17}{'-':>7}{0:>8}{row['checks']:>7}   not run at $0: {row['reason']}")
    columns = [row["column"] for row in record["metrics"].values()]
    lines += ["", "Published (their full runs; ours is a subset): " + "  ".join(columns)]
    for name, row in record["published"]["rows"].items():
        lines.append(f"  {name:<27}" + "  ".join(f"{row.get(c, ''):>7}" for c in columns))
    ours = "  ".join(
        f"{m['score']:>7}" if m["score"] is not None else f"{'-':>7}"
        for m in record["metrics"].values()
    )
    lines.append(f"  {'ReadableSTEM (no model)':<27}{ours}")
    return "\n".join(lines)


def run(
    folder: Path,
    run_dir: Path,
    *,
    at_once: int = 2,
    only: list[str] | None = None,
    limit: int | None = None,
    said: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Convert (resuming), score with the benchmark's checker, and return the run record."""
    categories = [c for c in CATEGORIES if not c.skipped and (not only or c.name in only)]
    if not categories:
        raise ValueError("no category to run")
    convert_all(folder, run_dir, categories, at_once=at_once, limit=limit, said=said)
    write_markdown(run_dir)
    rows = run_checker(folder, run_dir, categories)
    if limit is not None:
        # A quick run holds only the PDFs it converted: the checks of the others are not
        # counted as dropped.
        converted = set(page_files(run_dir))
        rows = [row for row in rows if row["pdf"] in converted]
    return make_record(folder, run_dir, categories, rows)


def new_run_dir(folder: Path) -> Path:
    import datetime as dt

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = folder.parent / "olmocr-bench-runs" / stamp
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.chmod(0o700)
    return path
