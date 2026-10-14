"""Pipeline evals against the OpenStax gold set: equations, headings, figures, document.

Each task rebuilds the input the pipeline would see from the gold answer key
(`eval.render`), runs the real pipeline code on it, and scores the output:

- equations: each gold equation rendered to a PNG -> `equation_step.transcribe`, given
  what `run.convert_pdf` would give it (the printed equation's text layer and the text
  around it on the page) -> scored against the gold MathML (`eval.equations.score_mathml`).
- headings: each gold page printed to PDF -> `layout.analyze` +
  `heading_step.heading_fixes` -> matched to gold h1-h6 (text, relative level).
- figures: each gold figure image -> `figure_step.describe` -> graded against the
  OpenStax alt text by the LLM judge (`eval.judge`), plus the step's own checks.
- document: each gold page printed to PDF -> `run.convert_pdf` -> equations aligned to
  gold in reading order, and the output HTML's static/axe check results. Predicted
  equations with no gold match are broken down (prose maths / fragment / other), and
  gold equations the layout cut into several elements are counted.

Inputs first: every input of the run is built (rendered, printed, downloaded) before
the first model call. Building them is free, so a failure there (`RenderError`, e.g. an
openstax.org download that times out) raises before anything is spent instead of
throwing away a half-paid run. The media's Chromium is then closed: it holds a sync
Playwright session in this thread, and axe-core (`html_checks.axe_checks`, document
task) cannot start its own beside it.

Spend: every model call goes through the client's `JobBudget` (an `InMemorySpendGuard`
capped by `--max-usd`). When a reservation is refused the run stops cleanly: the item
in flight is dropped, the report says `complete: false` and why, and every metric is
over the `n` items that finished.

The report carries what the CI gate needs to compare two runs fairly: pipeline and
prompt versions, model ids, the gold manifest hash and a hash of the selected item ids.
Dates come from the caller (`EvalConfig.date`); nothing here reads the wall clock.

Review load (equations, headings, document): how many decisions the pipeline asks a
person for (`Fix.review == "required"`; a group of repeated headings is one decision),
how many optional notes it leaves, and how well the required ones point at real errors:
flag precision (share of required decisions that are actually wrong against gold) and
flag recall (share of actually wrong items that are required, i.e. real errors a person
is asked about). Wrong items that are not required are listed in the per-item rows
(`wrong_not_required`) so they can be read one by one.

Privacy: the gold set is public OpenStax content, so per-item rows may hold text
(predicted LaTeX, alt text, heading text). Nothing here logs; the CLI prints only
metrics.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import re
import struct
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from a11y_stem.config import Settings
from a11y_stem.core import triage
from a11y_stem.core.budget import BudgetExceeded, JobBudget
from a11y_stem.core.types import Check, ConversionOptions, Element, Fix, Kind, ModelCall, Status
from a11y_stem.eval import judge as judge_mod
from a11y_stem.eval import verifier_metrics as vm
from a11y_stem.eval.equations import (
    EquationScore,
    MathMLError,
    normalize,
    score_latex,
    score_mathml,
)
from a11y_stem.eval.gold_scoring import iter_gold_records
from a11y_stem.models.base import VisionClient
from a11y_stem.pipeline import (
    equation_step,
    figure_details,
    figure_step,
    heading_step,
    html_checks,
    layout,
    run,
)
from a11y_stem.pipeline import options as run_options

SCHEMA_VERSION = 1
TASKS = ("equations", "headings", "figures", "document")
# Selective prediction: confidence thresholds 0.50, 0.55, ..., 0.95.
THRESHOLDS = tuple(round(0.5 + 0.05 * i, 2) for i in range(10))
HEADING_MATCH_RATIO = 0.85
EQUATION_ALIGN_MIN = 0.5
# A predicted equation is a piece of a gold one when this share of its signature appears,
# in order, in the gold signature (and it is at least two symbols long).
PIECE_CONTAINMENT = 0.8
UNMATCHED_REASONS = ("prose_math", "fragment", "other")
_EDGE_OPERATORS = frozenset("=+-*/^_<>,.;:\u00d7\u00f7\u22c5\u2264\u2265\u2260\u2248")
ROUTES = ("cheap", "main", "escalated", "fallback", "none", "other")
MINUS_SIGN = chr(0x2212)


# ============================================================================ stats


def mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return sum(items) / len(items) if items else None


def rate(flags: Iterable[bool]) -> float | None:
    items = list(flags)
    return sum(items) / len(items) if items else None


def percentile(values: Sequence[float], q: float) -> float | None:
    """Percentile with linear interpolation between closest ranks (numpy's default).

    For sorted x[0..n-1] the position is (n - 1) * q / 100; e.g. p50 of [1, 2, 3, 4] is
    2.5 and p95 of [10, 20] is 19.5. None for an empty list.
    """
    if not values:
        return None
    if not 0 <= q <= 100:
        raise ValueError("q must be in [0, 100]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[low])
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def wilson_interval(successes: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    """95% Wilson score interval for a proportion; honest at small n and near 0 or 1."""
    if n == 0:
        return None
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


def latency_summary(values: Sequence[float]) -> dict:
    return {"p50": percentile(values, 50), "p95": percentile(values, 95), "n": len(values)}


# ========================================================================= matching


def text_signature(text: str | None) -> str:
    """Comparable form of maths text: NFKC, case-folded, minus sign as '-', no spaces."""
    folded = unicodedata.normalize("NFKC", text or "").casefold().replace(MINUS_SIGN, "-")
    return "".join(ch for ch in folded if not ch.isspace())


def math_signature(mathml: str | None) -> str:
    """Token text of a MathML tree after lenient normalisation (annotations dropped)."""
    if not mathml:
        return ""
    try:
        root = normalize(mathml, lenient=True)
    except MathMLError:
        return ""
    return text_signature("".join(root.itertext()))


def heading_signature(text: str | None) -> str:
    """Heading text for matching: NFKC, case-folded, letters/digits/spaces only."""
    folded = unicodedata.normalize("NFKC", text or "").casefold()
    kept = "".join(ch if ch.isalnum() else " " for ch in folded)
    return " ".join(kept.split())


def containment(part: str, whole: str) -> float:
    """Share of `part`'s characters found in order in `whole`; 0 when either is empty."""
    if not part or not whole:
        return 0.0
    matcher = difflib.SequenceMatcher(None, part, whole, autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks()) / len(part)


def similarity(a: str, b: str) -> float:
    """difflib ratio (2 * matches / total length); 0 when either side is empty."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def align(
    gold: Sequence[Any],
    pred: Sequence[Any],
    score: Callable[[Any, Any], float],
    *,
    min_similarity: float,
) -> list[tuple[int, int, float]]:
    """Order-preserving alignment maximising total similarity (weighted LCS).

    Pairs (gold index, pred index, similarity) with similarity >= `min_similarity`; each
    item is used at most once and pairs never cross. Ties prefer matching over skipping,
    so repeated texts (OpenStax repeats "Teacher Support") pair up in order.
    """
    n, m = len(gold), len(pred)
    sims = [[score(g, p) for p in pred] for g in gold]
    table = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best = max(table[i - 1][j], table[i][j - 1])
            s = sims[i - 1][j - 1]
            if s >= min_similarity:
                best = max(best, table[i - 1][j - 1] + s)
            table[i][j] = best
    pairs: list[tuple[int, int, float]] = []
    i, j = n, m
    while i > 0 and j > 0:
        s = sims[i - 1][j - 1]
        if s >= min_similarity and math.isclose(table[i][j], table[i - 1][j - 1] + s):
            pairs.append((i - 1, j - 1, s))
            i, j = i - 1, j - 1
        elif math.isclose(table[i][j], table[i - 1][j]):
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


# ============================================================================ gold


def load_gold(gold_dir: Path) -> list[dict]:
    """Every gold record, in file order (files sorted, then line order)."""
    return list(iter_gold_records(gold_dir))


def group_pages(records: Iterable[dict]) -> list[list[dict]]:
    """Records grouped by (book, page), pages in first-seen order."""
    pages: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        pages.setdefault((record["book_slug"], record["page_slug"]), []).append(record)
    return list(pages.values())


def _of_kind(records: Iterable[dict], kind: str) -> list[dict]:
    return sorted(
        (r for r in records if r.get("kind") == kind), key=lambda r: r.get("index_on_page", 0)
    )


def manifest_sha256(gold_dir: Path) -> str:
    """SHA-256 of manifest.json, which itself lists every gold file's SHA-256.

    Without a manifest (ad-hoc gold dirs in tests), hash the JSONL files directly.
    """
    manifest = gold_dir / "manifest.json"
    if manifest.is_file():
        return hashlib.sha256(manifest.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for path in sorted(gold_dir.rglob("*.jsonl")):
        digest.update(path.relative_to(gold_dir).as_posix().encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def select_units(
    task: str, records: list[dict], limit: int | None, every: int | None = None
) -> list[Any]:
    """What one task iterates over: equation or figure records, or pages of records.

    `limit` counts those units: equations, figures, or pages (headings, document). `every`
    keeps every Nth unit from the first, before `limit` is applied.
    """
    units: list[Any]
    if task == "equations":
        units = [r for r in records if r.get("kind") == "equation"]
    elif task == "figures":
        units = [
            r
            for r in records
            if r.get("kind") == "figure" and any(img.get("url") for img in r.get("images", []))
        ]
    elif task == "headings":
        units = [p for p in group_pages(records) if _of_kind(p, "heading")]
    elif task == "document":
        units = group_pages(records)
    else:
        raise ValueError(f"unknown task {task!r}; expected one of {', '.join(TASKS)}")
    if every:
        units = units[::every]
    return units[:limit] if limit is not None else units


def unit_ids(task: str, units: Sequence[Any]) -> list[str]:
    if task in {"equations", "figures"}:
        return [u["id"] for u in units]
    return [f"{p[0]['book_slug']}/{p[0]['page_slug']}" for p in units]


# =========================================================================== inputs


class EvalMedia(Protocol):
    """Inputs rebuilt from gold records. `CachedMedia` is the real one; tests fake it."""

    def equation_png(self, record: dict) -> bytes: ...

    def equation_text(self, record: dict) -> str | None:
        """The equation's PDF text layer (what `layout` would put on `Element.text`)."""
        ...

    def equation_context(self, record: dict) -> str | None:
        """The text around the equation, as `run.convert_pdf` passes `context_text`."""
        ...

    def figure_png(self, url: str) -> bytes: ...

    def page_pdf(self, page_records: list[dict]) -> bytes: ...

    def close(self) -> None:
        """Release the browser. Called once every input is built, before any step runs."""
        ...


class CachedMedia:
    """Chromium rendering and image downloads, cached under `cache_dir` (`.cache/eval`).

    One browser serves the whole run and starts only on the first cache miss. Page
    XHTML comes from the gold fetcher's cache (`.cache/openstax`) via `fetcher`.
    """

    def __init__(
        self, gold_dir: Path, cache_dir: Path, fetcher: Any, *, paper: str = "Letter"
    ) -> None:
        from a11y_stem.eval import render

        self._render = render
        self.gold_dir = gold_dir
        self.cache_dir = cache_dir
        self.fetcher = fetcher
        self.paper = paper
        self.images = render.ImageCache(cache_dir / "images")
        self.browser = render.ChromiumRenderer()
        self._contexts: dict[str, list[tuple[str, str | None]]] = {}

    def __enter__(self) -> CachedMedia:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self.browser.close()

    def equation_png(self, record: dict) -> bytes:
        pngs = self._render.render_equations(
            [record], self.cache_dir / "equations", renderer=self.browser
        )
        return pngs[record["id"]]

    def equation_text(self, record: dict) -> str | None:
        return self._render.render_equation_text(
            record["mathml"], self.cache_dir / "equations", renderer=self.browser
        )

    def equation_context(self, record: dict) -> str | None:
        """Context from the page's archive XHTML, found by the record's index on the page."""
        render = self._render
        url = record["content_url"]
        if url not in self._contexts:
            page = render.page_source_from_records([record], self.fetcher)
            self._contexts[url] = render.equation_contexts(page.xhtml)
        contexts = self._contexts[url]
        index = record.get("index_on_page")
        found = contexts[index] if isinstance(index, int) and 0 <= index < len(contexts) else None
        if found is None or found[0] != render.math_tokens(record["mathml"]):
            raise render.RenderError(
                f"equation {record['id']} is not at its index in the page source"
            )
        return found[1]

    def figure_png(self, url: str) -> bytes:
        return self.images.png(url)

    def page_pdf(self, page_records: list[dict]) -> bytes:
        render = self._render
        book = page_records[0]["book_slug"]
        page = render.page_source_from_records(
            page_records, self.fetcher, authors=render.book_authors(self.gold_dir, book)
        )
        return render.render_page_pdf(
            page,
            images=self.images,
            renderer=self.browser,
            paper="A4" if self.paper == "A4" else "Letter",
            cache_dir=self.cache_dir / "pages",
        )


@dataclass
class EvalConfig:
    task: str
    settings: Settings
    client: VisionClient
    media: EvalMedia
    date: str  # YYYY-MM-DD, passed in by the caller
    gold_dir: Path
    limit: int | None = None
    every: int | None = None  # every Nth unit (`select_units`)
    client_label: str = "openai"  # "fake" for FakeVisionClient runs
    gold_label: str | None = None  # how to show gold_dir in the report (no home paths)
    concurrency: int = 4  # document task: convert_pdf's thread pool
    # The client's budget, when there is one: its record of spend is the source of truth
    # (it includes calls made for an item that was then dropped at a budget stop).
    budget: JobBudget | None = None
    # Gold equation ids the scorer marks wrong even for known-correct LaTeX (normalisation
    # gaps, `scorer_gap_ids`): review-load metrics are also given without them.
    scorer_gaps: frozenset[str] = frozenset()
    # Who judges the figures (`judge_mod.make_judge`); None: the run's own client and main
    # model, as before the judge could be chosen.
    judge: judge_mod.JudgeSetup | None = None

    @property
    def judge_setup(self) -> judge_mod.JudgeSetup:
        return self.judge or judge_mod.JudgeSetup(self.client, self.settings)


@dataclass
class EvalRun:
    report: dict
    items: list[dict] = field(default_factory=list)


# ========================================================================= helpers


def route_of(calls: Sequence[ModelCall], *, main_model: str, cheap_model: str) -> str:
    """How a fix was produced: cheap only, main only, cheap then main, or via fallback."""
    if not calls:
        return "none"
    if any(call.is_fallback for call in calls):
        return "fallback"
    models = [call.model for call in calls]
    if models[0] == cheap_model:
        return "escalated" if main_model in models[1:] else "cheap"
    if models[0] == main_model:
        return "main"
    return "other"


def budget_reason(fix: Fix) -> str | None:
    """Category code when a fix failed because spend ran out (it was never really tried)."""
    if fix.status == Status.FAILED and (fix.reason or "").startswith("budget"):
        return fix.reason
    if any(call.outcome == "budget_exceeded" for call in fix.calls):
        return "budget_exceeded"
    return None


def _failed_codes(checks: Sequence[Check]) -> list[str]:
    return [c.code or c.name for c in checks if not c.passed and c.severity == "error"]


def _calls_summary(calls: Sequence[ModelCall]) -> dict:
    return {
        "cost_usd": sum(c.cost_usd for c in calls),
        "latency_ms": sum(c.latency_ms for c in calls),
        "model_calls": len(calls),
        "models": [c.model for c in calls],
        "call_outcomes": [c.outcome for c in calls],
        "prompt_versions": {c.step: c.prompt_version for c in calls},
    }


def _png_size(png: bytes) -> tuple[int, int] | None:
    """Width and height from a PNG header (no decode); None for anything else."""
    if len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR":
        width, height = struct.unpack(">II", png[16:24])
        return width, height
    return None


def _element(kind: Kind, index: int, png: bytes, **extra: Any) -> Element:
    """A stand-alone element for a step called outside a document (bbox = pixel size)."""
    width, height = _png_size(png) or (0, 0)
    return Element(
        id=f"p1-e{index}",
        kind=kind,
        page=0,
        bbox=(0.0, 0.0, float(width), float(height)),
        order=index,
        **extra,
    )


def _missing_score(gold_mathml: str) -> EquationScore:
    """Score for "no proposal": counts as wrong, like a missing prediction in gold_scoring."""
    size = score_mathml(gold_mathml, gold_mathml).gold_nodes
    return EquationScore(False, False, size, 0.0, size, 0, "no proposal")


# ===================================================================== review load


def scorer_gap_ids(records: Iterable[dict], predictions: Path) -> frozenset[str]:
    """Gold equations whose known-correct LaTeX (one JSON line per equation: `id`,
    `latex`, as in tests/fixtures/sample_latex_predictions.jsonl) still fails
    exact_lenient: any transcription of them scores wrong, so a "wrong" one there says
    nothing about the model or the flags. Empty when the file is missing."""
    if not predictions.is_file():
        return frozenset()
    gold = {r["id"]: r["mathml"] for r in records if r.get("kind") == "equation"}
    gaps = set()
    for line in predictions.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("id") in gold and not score_latex(row["latex"], gold[row["id"]]).exact_lenient:
            gaps.add(row["id"])
    return frozenset(gaps)


def flag_metrics(units: Iterable[Sequence[Any]]) -> dict:
    """Flag precision and recall over decision units.

    Each unit is (required, wrong members[, ...]): one equation is (required, 0 or 1);
    a group of repeated headings is one unit with the number of its headings that are
    wrong. Precision = required units with at least one wrong member / required units.
    Recall = wrong members inside required units / all wrong members (a real error a
    person is asked about counts as caught).
    """
    units = [(bool(u[0]), int(u[1])) for u in units]
    required = [wrong for flagged, wrong in units if flagged]
    wrong = sum(w for _, w in units)
    caught = sum(required)
    useful = sum(1 for w in required if w)
    return {
        "units": len(units),
        "required_units": len(required),
        "required_units_wrong": useful,
        "wrong": wrong,
        "wrong_required": caught,
        "flag_precision": _safe_div(useful, len(required)),
        "flag_recall": _safe_div(caught, wrong),
    }


def load_counts(fixes: Sequence[Fix]) -> dict:
    """The pipeline's review tiers for one unit of work (`core.triage.review_load`)."""
    return triage.review_load(fixes).as_dict()


def _sum_loads(loads: Iterable[dict]) -> dict:
    total = triage.ReviewLoad()
    for load in loads:
        total.add(
            triage.ReviewLoad(
                required_decisions=load["required_decisions"],
                required_fixes=load["required_fixes"],
                optional=load["optional"],
                none=load["none"],
                required_by_kind=Counter(load["required_by_kind"]),
                optional_by_kind=Counter(load["optional_by_kind"]),
                required_by_reason=Counter(load["required_by_reason"]),
                optional_by_reason=Counter(load["optional_by_reason"]),
            )
        )
    return total.as_dict()


def _per(n: float, d: float) -> float | None:
    return round(n / d, 4) if d else None


# ======================================================================= equations


def equation_item(record: dict, fix: Fix, settings: Settings) -> dict:
    proposal = fix.equation
    score = (
        score_mathml(proposal.mathml, record["mathml"])
        if proposal is not None
        else _missing_score(record["mathml"])
    )
    return {
        "id": record["id"],
        "display": record.get("display"),
        "status": fix.status.value,
        "confidence": fix.confidence,
        "reason": fix.reason,
        "route": route_of(
            fix.calls, main_model=settings.main_model, cheap_model=settings.cheap_model
        ),
        "has_proposal": proposal is not None,
        "exact_strict": score.exact_strict,
        "exact_lenient": score.exact_lenient,
        "teds": score.teds,
        "score_error": score.error if proposal is not None else None,
        "failed_checks": _failed_codes(fix.checks),
        "pred_latex": proposal.latex if proposal is not None else None,
        "pred_mathml": proposal.mathml if proposal is not None else None,
        # Review load: the tier the pipeline gave it, why, and whether it is wrong.
        "review": fix.review,
        "tier_reasons": triage.tier_reasons(fix),
        "wrong": not score.exact_lenient,
        "wrong_not_required": not score.exact_lenient and fix.review != "required",
        **_calls_summary(fix.calls),
        # Second automated check, shadow mode (ADR 0005; eval.verifier_metrics).
        **vm.equation_fields(fix),
    }


def selective_table(items: Sequence[dict], thresholds: Sequence[float] = THRESHOLDS) -> list:
    """Accuracy (exact_lenient) and coverage if only proposals with confidence >= t were kept.

    coverage = kept / all items; accuracy = exact_lenient among kept (None when none
    kept). A useful confidence makes accuracy rise as the threshold rises.
    """
    n = len(items)
    rows = []
    for threshold in thresholds:
        kept = [
            item
            for item in items
            if item["has_proposal"]
            and item.get("confidence") is not None
            and item["confidence"] >= threshold - 1e-9
        ]
        rows.append(
            {
                "threshold": threshold,
                "kept": len(kept),
                "coverage": len(kept) / n if n else None,
                "accuracy": rate(item["exact_lenient"] for item in kept),
                "mean_teds": mean(item["teds"] for item in kept),
            }
        )
    return rows


def _group_rates(items: Sequence[dict], key: str, order: Sequence[str] | None = None) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        groups[str(item.get(key))].append(item)
    names = [k for k in (order or []) if k in groups] + sorted(
        k for k in groups if k not in (order or [])
    )
    n = len(items)
    return {
        name: {
            "n": len(groups[name]),
            "share": len(groups[name]) / n if n else None,
            "exact_lenient": rate(i["exact_lenient"] for i in groups[name]),
            "mean_teds": mean(i["teds"] for i in groups[name]),
        }
        for name in names
    }


STATUS_ORDER = [s.value for s in Status]


def equation_metrics(items: Sequence[dict]) -> dict:
    """Rates over every item: an item with no proposal counts as wrong (TEDS 0)."""
    n = len(items)
    lenient = sum(i["exact_lenient"] for i in items)
    return {
        "exact_strict": rate(i["exact_strict"] for i in items),
        "exact_lenient": rate(i["exact_lenient"] for i in items),
        "exact_lenient_ci95": wilson_interval(lenient, n),
        "mean_teds": mean(i["teds"] for i in items),
        "proposal_rate": rate(i["has_proposal"] for i in items),
        "by_status": _group_rates(items, "status", STATUS_ORDER),
        "by_route": _group_rates(items, "route", ROUTES),
        "by_display": _group_rates(items, "display", ["block", "inline"]),
        "selective": selective_table(items),
        "review_load": equation_review_load(items),
        "second_check": vm.equation_metrics(items),
    }


def equation_review_load(items: Sequence[dict]) -> dict:
    """Required decisions and optional notes per equation and per section (a gold page;
    the equations task prints no PDF pages, see the document task for per-PDF-page
    numbers), and flag precision/recall with "wrong" = not exact_lenient, also without
    the scorer's known gaps (`scorer_gap` on the item)."""
    n = len(items)
    sections = len({_section_of(i.get("id", "")) for i in items})
    required = sum(i.get("review") == "required" for i in items)
    optional = sum(i.get("review") == "optional" for i in items)
    units = [(i.get("review") == "required", int(not i["exact_lenient"])) for i in items]
    clean = [
        (i.get("review") == "required", int(not i["exact_lenient"]))
        for i in items
        if not i.get("scorer_gap")
    ]
    reasons: dict[str, Counter[str]] = {"required": Counter(), "optional": Counter()}
    for item in items:
        if item.get("review") in reasons:
            reasons[item["review"]].update(item.get("tier_reasons", []))
    return {
        "required": required,
        "optional": optional,
        "required_per_item": _per(required, n),
        "optional_per_item": _per(optional, n),
        "sections": sections,
        "required_per_section": _per(required, sections),
        "optional_per_section": _per(optional, sections),
        "required_by_reason": dict(sorted(reasons["required"].items())),
        "optional_by_reason": dict(sorted(reasons["optional"].items())),
        "flags": flag_metrics(units),
        "scorer_gaps": sorted(i.get("id", "") for i in items if i.get("scorer_gap")),
        "flags_excluding_scorer_gaps": flag_metrics(clean),
        "wrong_not_required": [
            {
                "id": i.get("id"),
                "review": i.get("review"),
                "reasons": i.get("tier_reasons", []),
                "scorer_gap": bool(i.get("scorer_gap")),
            }
            for i in items
            if not i["exact_lenient"] and i.get("review") != "required"
        ],
    }


def _section_of(item_id: str) -> str:
    """'openstax/physics/2-1-x/eq-003' -> 'openstax/physics/2-1-x'."""
    return item_id.rsplit("/", 1)[0]


@dataclass(frozen=True)
class EquationInput:
    """What `equation_step.transcribe` gets for one equation inside `run.convert_pdf`.

    The text layer matters beyond the image: the step routes short inline equations to
    the cheap model by its length, and cross-checks the transcription against it (a
    mismatch adds a failed check and lowers the confidence, so ACCEPTED becomes
    NEEDS_REVIEW). Without it the eval would measure a confidence the app never sees.
    """

    png: bytes
    text_layer: str | None
    context: str | None


def _equation_inputs(media: EvalMedia, units: list[dict]) -> list[EquationInput]:
    return [
        EquationInput(media.equation_png(r), media.equation_text(r), media.equation_context(r))
        for r in units
    ]


def _eval_equations(
    cfg: EvalConfig, units: list[dict], inputs: list[EquationInput]
) -> tuple[list[dict], str | None]:
    items: list[dict] = []
    for index, (record, given) in enumerate(zip(units, inputs, strict=True)):
        element = _element(
            Kind.EQUATION,
            index,
            given.png,
            display=record.get("display") or "block",
            text=given.text_layer,
        )
        try:
            fix = equation_step.transcribe(
                element,
                given.png,
                client=cfg.client,
                settings=cfg.settings,
                context_text=given.context,
            )
        except BudgetExceeded as exc:
            return items, f"budget_{exc.scope}"
        stopped = budget_reason(fix)
        if stopped:
            return items, stopped
        item = equation_item(record, fix, cfg.settings)
        item["scorer_gap"] = record["id"] in cfg.scorer_gaps
        items.append(item)
    return items, None


# ======================================================================== headings

KEPT_HEADING_STATUSES = {Status.ACCEPTED, Status.NEEDS_REVIEW, Status.EDITED}


def predicted_headings(fixes: Sequence[Fix]) -> list[dict]:
    """Headings the pipeline would emit: heading fixes it did not abstain on or fail."""
    return [
        {
            "level": f.heading.level,
            "text": f.heading.text,
            "status": f.status.value,
            "review": f.review,
            "group_key": f.group_key,
            "tier_reasons": triage.tier_reasons(f),
        }
        for f in fixes
        if f.heading is not None and f.status in KEPT_HEADING_STATUSES
    ]


def heading_flags(item: dict, predicted: Sequence[dict]) -> dict:
    """Review flags of one page's predicted headings against its gold match (`item` from
    `heading_page_item`). Wrong = not a gold heading, or matched at the wrong relative
    level. A group of repeated headings is one unit (one decision)."""
    matched = {i: pair for i, pair in zip(item["matched_pred"], item["pairs"], strict=True)}
    units: dict[str, list[bool]] = {}
    required: dict[str, bool] = {}
    wrong_not_required = []
    for index, heading in enumerate(predicted):
        pair = matched.get(index)
        wrong = pair is None or not pair["level_correct"]
        key = heading.get("group_key") or f"single-{index}"
        units.setdefault(key, []).append(wrong)
        flagged = heading.get("review") == "required"
        required[key] = required.get(key, False) or flagged
        if wrong and not flagged:
            wrong_not_required.append(
                {
                    "text": heading["text"],
                    "level": heading["level"],
                    "status": heading.get("status"),
                    "review": heading.get("review"),
                    "reasons": heading.get("tier_reasons", []),
                    "why_wrong": "not_a_gold_heading" if pair is None else "wrong_level",
                    "gold_level": pair["gold_level"] if pair is not None else None,
                }
            )
    flags = [(required[key], sum(members)) for key, members in units.items()]
    return {"units": [list(f) for f in flags], "wrong_not_required": wrong_not_required}


def heading_page_item(page_id: str, gold: Sequence[dict], predicted: Sequence[dict]) -> dict:
    """Match one page's predicted headings to gold (in order, text ratio >= 0.85).

    Levels are compared relative to each side's top level on the page: OpenStax pages
    start at h2, a PDF has no levels at all, so "second level below the top" is the
    meaningful comparison.
    """
    pairs = align(
        [heading_signature(g["text"]) for g in gold],
        [heading_signature(p["text"]) for p in predicted],
        similarity,
        min_similarity=HEADING_MATCH_RATIO,
    )
    gold_top = min((g["level"] for g in gold), default=1)
    pred_top = min((p["level"] for p in predicted), default=1)
    matched = []
    for gi, pi, sim in pairs:
        g, p = gold[gi], predicted[pi]
        matched.append(
            {
                "gold_text": g["text"],
                "pred_text": p["text"],
                "similarity": round(sim, 4),
                "gold_level": g["level"],
                "pred_level": p["level"],
                "level_correct": g["level"] - gold_top == p["level"] - pred_top,
                "pred_status": p["status"],
                "gold_context": g.get("context"),
            }
        )
    matched_gold = {gi for gi, _, _ in pairs}
    matched_pred = {pi for _, pi, _ in pairs}
    return {
        "id": page_id,
        "n_gold": len(gold),
        "n_pred": len(predicted),
        "matched": len(pairs),
        "level_correct": sum(1 for m in matched if m["level_correct"]),
        "pairs": matched,
        "matched_pred": [pi for _, pi, _ in pairs],
        "missed": [
            {"text": g["text"], "level": g["level"], "context": g.get("context")}
            for i, g in enumerate(gold)
            if i not in matched_gold
        ],
        "extra": [
            {"text": p["text"], "level": p["level"], "status": p["status"]}
            for i, p in enumerate(predicted)
            if i not in matched_pred
        ],
    }


def _safe_div(a: float, b: float) -> float | None:
    return a / b if b else None


def heading_metrics(items: Sequence[dict]) -> dict:
    """Micro-averaged over pages: every heading counts once, whatever its page.

    recall_by_context splits recall by the enclosing OpenStax box ("body" when none).
    It matters on this gold set: 50 of its 121 headings are the repeated "Teacher
    Support" titles of os-teacher boxes, which are easy to find and can flatter the total.
    """
    gold = sum(i["n_gold"] for i in items)
    pred = sum(i["n_pred"] for i in items)
    matched = sum(i["matched"] for i in items)
    precision = _safe_div(matched, pred)
    recall = _safe_div(matched, gold)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    pred_by_status: Counter[str] = Counter()
    matched_by_status: Counter[str] = Counter()
    gold_by_context: Counter[str] = Counter()
    found_by_context: Counter[str] = Counter()
    for item in items:
        for pair in item["pairs"]:
            pred_by_status[pair["pred_status"]] += 1
            matched_by_status[pair["pred_status"]] += 1
            context = pair["gold_context"] or "body"
            gold_by_context[context] += 1
            found_by_context[context] += 1
        for extra in item["extra"]:
            pred_by_status[extra["status"]] += 1
        for missed in item["missed"]:
            gold_by_context[missed["context"] or "body"] += 1
    return {
        "gold_headings": gold,
        "predicted_headings": pred,
        "matched": matched,
        "precision": precision,
        "recall": recall,
        "recall_ci95": wilson_interval(matched, gold),
        "f1": f1,
        "level_accuracy": _safe_div(sum(i["level_correct"] for i in items), matched),
        "precision_by_status": {
            s: {"n": pred_by_status[s], "precision": _safe_div(matched_by_status[s], c)}
            for s, c in sorted(pred_by_status.items())
        },
        "recall_by_context": {
            c: {"n": gold_by_context[c], "recall": _safe_div(found_by_context[c], n)}
            for c, n in sorted(gold_by_context.items())
        },
        "per_page": [
            {
                "id": i["id"],
                "precision": _safe_div(i["matched"], i["n_pred"]),
                "recall": _safe_div(i["matched"], i["n_gold"]),
                "n_gold": i["n_gold"],
                "n_pred": i["n_pred"],
                "error": i.get("error"),
            }
            for i in items
        ],
        "page_errors": _page_errors(items),
        "review_load": heading_review_load(items),
    }


def heading_review_load(items: Sequence[dict]) -> dict:
    """Required decisions and optional notes per section (gold page) and per PDF page,
    and flag precision/recall (`heading_flags`) over every page."""
    loads = [i["review"] for i in items if "review" in i]
    load = _sum_loads(loads)
    sections = len(items)
    pdf_pages = sum(i.get("pdf_pages", 0) for i in items)
    units = [u for i in items for u in i.get("flags", {}).get("units", [])]
    return {
        **load,
        "sections": sections,
        "pdf_pages": pdf_pages,
        "required_per_section": _per(load["required_decisions"], sections),
        "required_per_pdf_page": _per(load["required_decisions"], pdf_pages),
        "optional_per_pdf_page": _per(load["optional"], pdf_pages),
        "flags": flag_metrics(units),
        "wrong_not_required": [
            {"page": i["id"], **w}
            for i in items
            for w in i.get("flags", {}).get("wrong_not_required", [])
        ],
    }


def _page_errors(items: Sequence[dict]) -> dict[str, int]:
    """LayoutError codes per page that could not be analysed (scored as no predictions)."""
    return dict(sorted(Counter(i["error"] for i in items if i.get("error")).items()))


def _page_id(page: Sequence[dict]) -> str:
    return f"{page[0]['book_slug']}/{page[0]['page_slug']}"


def _page_inputs(media: EvalMedia, units: list[list[dict]]) -> list[bytes]:
    return [media.page_pdf(page) for page in units]


def _eval_headings(
    cfg: EvalConfig, units: list[list[dict]], pdfs: list[bytes]
) -> tuple[list[dict], str | None]:
    items = []
    for page, pdf in zip(units, pdfs, strict=True):
        gold = _of_kind(page, "heading")
        error = None
        fixes: list[Fix] = []
        pdf_pages = 0
        try:
            analysed = layout.analyze(pdf)
            fixes = heading_step.heading_fixes(analysed.elements)
            pdf_pages = analysed.page_count
            predicted = predicted_headings(fixes)
        except layout.LayoutError as exc:  # the pipeline failed this page: nothing found
            predicted, error = [], exc.code
        item = heading_page_item(_page_id(page), gold, predicted)
        item["error"] = error
        item["pdf_pages"] = pdf_pages
        item["review"] = load_counts(fixes)
        item["flags"] = heading_flags(item, predicted)
        items.append(item)
    return items, None


# ========================================================================= figures


def figure_item(record: dict, fix: Fix, judgement: judge_mod.Judgement | None) -> dict:
    image = _figure_image(record)
    reference = (image.get("alt") or "").strip()
    proposal = fix.figure
    verdict = judgement.verdict if judgement is not None else None
    if not reference:
        judge_outcome = "no_reference"
    elif proposal is None:
        judge_outcome = "no_proposal"
    elif judgement is None or verdict is None:
        judge_outcome = f"judge_{judgement.call.outcome}" if judgement else "judge_skipped"
    else:
        judge_outcome = "judged"
    return {
        "id": record["id"],
        "page_slug": record.get("page_slug"),
        "figure_number": record.get("figure_number"),
        "image_url": image["url"],
        "caption": record.get("caption"),
        "reference_alt": reference,
        "status": fix.status.value,
        "confidence": fix.confidence,
        "reason": fix.reason,
        "has_proposal": proposal is not None,
        "candidate_alt": proposal.alt if proposal is not None else None,
        "figure_type": proposal.figure_type if proposal is not None else None,
        "decorative": proposal.decorative if proposal is not None else None,
        "checks_passed": proposal is not None and not _failed_codes(fix.checks),
        "failed_checks": _failed_codes(fix.checks),
        "judge_outcome": judge_outcome,
        "judge_pass": verdict.passed if verdict is not None else None,
        "judge_conveys": verdict.conveys_key_information if verdict is not None else None,
        "judge_factual_error": verdict.factual_error if verdict is not None else None,
        "judge_score": verdict.score if verdict is not None else None,
        "judge_confidence": verdict.confidence if verdict is not None else None,
        "judge_cost_usd": judgement.call.cost_usd if judgement is not None else 0.0,
        "judge_latency_ms": judgement.call.latency_ms if judgement is not None else 0,
        "judge_prompt_version": judgement.call.prompt_version if judgement is not None else None,
        **_calls_summary(fix.calls),
        # Second automated check (ADR 0005; eval.verifier_metrics).
        **vm.figure_fields(fix),
    }


def figure_metrics(items: Sequence[dict]) -> dict:
    """Check pass rate over all figures; judge pass rate over figures with a reference.

    judge_pass_rate counts a figure the step abstained on (or failed) as not passing,
    since a reader gets no description; figures whose judge call failed are left out
    (the instrument did not measure them) and counted in judge_errors.
    """
    with_reference = [i for i in items if i["judge_outcome"] != "no_reference"]
    measured = [i for i in with_reference if i["judge_outcome"] in {"judged", "no_proposal"}]
    judged = [i for i in measured if i["judge_outcome"] == "judged"]
    passes = sum(bool(i["judge_pass"]) for i in measured)
    by_type: Counter[str] = Counter(str(i["figure_type"]) for i in items if i["has_proposal"])
    failed: Counter[str] = Counter(code for i in items for code in i["failed_checks"])
    metrics = {
        "proposal_rate": rate(i["has_proposal"] for i in items),
        "check_pass_rate": rate(i["checks_passed"] for i in items),
        "failed_check_codes": dict(sorted(failed.items())),
        "by_status": {
            s: sum(i["status"] == s for i in items)
            for s in STATUS_ORDER
            if any(i["status"] == s for i in items)
        },
        "figure_types": dict(sorted(by_type.items())),
        "with_reference": len(with_reference),
        "judge_measured": len(measured),
        "judge_errors": len(with_reference) - len(measured),
        "judge_pass_rate": _safe_div(passes, len(measured)),
        "judge_pass_rate_ci95": wilson_interval(passes, len(measured)),
        "judge_pass_rate_answered": rate(bool(i["judge_pass"]) for i in judged),
        "judge_mean_score": mean(i["judge_score"] for i in judged),
        "judge_factual_error_rate": rate(bool(i["judge_factual_error"]) for i in judged),
        "decorative_rate": rate(bool(i["decorative"]) for i in items if i["has_proposal"]),
        "second_check": vm.figure_metrics(items),
    }
    if any(i.get("long_descriptions") for i in items):
        metrics["long_descriptions"] = long_description_metrics(items)
    return metrics


def _figure_image(record: dict) -> dict:
    return next(img for img in record["images"] if img.get("url"))


def _figure_inputs(media: EvalMedia, units: list[dict]) -> list[bytes]:
    return [media.figure_png(_figure_image(record)["url"]) for record in units]


def _eval_figures(
    cfg: EvalConfig, units: list[dict], pngs: list[bytes]
) -> tuple[list[dict], str | None]:
    # The options the app would use (FEATURE_LONG_DESCRIPTIONS): with long descriptions
    # on, the step sends its long prompt and the long-description judge grades the
    # extra output too.
    options = ConversionOptions.from_settings(cfg.settings)
    items = []
    for index, (record, png) in enumerate(zip(units, pngs, strict=True)):
        image = _figure_image(record)
        caption = record.get("caption")
        element = _element(Kind.FIGURE, index, png, caption=caption)
        try:
            with run_options.using(options):
                fix = figure_step.describe(
                    element, png, client=cfg.client, settings=cfg.settings, context_text=caption
                )
            stopped = budget_reason(fix)
            if stopped:
                return items, stopped
            judgement = None
            reference = (image.get("alt") or "").strip()
            if fix.figure is not None and reference:
                judgement = judge_mod.judge_alt(
                    fix.figure.alt,
                    reference,
                    caption,
                    client=cfg.judge_setup.client,
                    settings=cfg.judge_setup.settings,
                    image=png,
                )
            long_judgement = None
            proposal = fix.figure
            if options.long_descriptions and proposal and figure_details.has_details(proposal):
                long_judgement = judge_mod.judge_long(
                    proposal.long_description,
                    proposal.data_table,
                    reference,
                    caption,
                    client=cfg.judge_setup.client,
                    settings=cfg.judge_setup.settings,
                    image=png,
                )
        except BudgetExceeded as exc:
            return items, f"budget_{exc.scope}"
        item = figure_item(record, fix, judgement)
        if options.long_descriptions:
            item |= long_description_fields(fix, long_judgement)
        items.append(item)
    return items, None


# Long descriptions (FEATURE_LONG_DESCRIPTIONS; docs/eval.md "Long descriptions").


def long_description_fields(fix: Fix, judgement: judge_mod.LongJudgement | None) -> dict:
    """Per-item fields of a figure described with long descriptions on."""
    figure = fix.figure
    verdict = judgement.verdict if judgement is not None else None
    failed = {c.code for c in fix.failed_checks}
    if judgement is None:
        outcome = "not_judged"  # nothing to grade: no long description and no table
    else:
        outcome = "judged" if verdict is not None else f"judge_{judgement.call.outcome}"
    table = figure.data_table if figure is not None else None
    return {
        "long_descriptions": True,
        "long_description": figure.long_description if figure is not None else None,
        "long_unsure": "long_description_unsure" in failed,
        "data_table": judge_mod.table_text(table) if table is not None else None,
        "data_table_rows": table.rows - table.header_rows if table is not None else 0,
        "data_values_from_text": figure.data_values_from_text if figure is not None else None,
        "values_from_image": "values_from_image" in failed,
        "colour_only": bool(figure is not None and figure.colour_only),
        "review": fix.review,
        "long_judge_outcome": outcome,
        "long_pass": verdict.passed if verdict is not None else None,
        "long_accurate": verdict.accurate if verdict is not None else None,
        "long_complete": verdict.complete if verdict is not None else None,
        "long_invented_values": verdict.invented_values if verdict is not None else None,
        "long_score": verdict.score if verdict is not None else None,
        "table_values_ok": verdict.table_values_ok if verdict is not None else None,
        "table_wrong_values": verdict.table_wrong_values if verdict is not None else None,
        "long_judge_cost_usd": judgement.call.cost_usd if judgement is not None else 0.0,
        "long_judge_prompt_version": judgement.call.prompt_version if judgement else None,
    }


def long_description_metrics(items: Sequence[dict]) -> dict:
    """What the long prompt adds, over the figures described with it.

    Pass rates are over judged long descriptions (a figure with only a table has no
    description to grade); table accuracy over judged tables. `required_decisions` is
    the figures left needing a decision; `tables_offered` the data tables read from the
    image, which are optional extras (not in the student file until a reviewer adds
    them, never a decision).
    """
    on = [i for i in items if i.get("long_descriptions")]
    proposals = [i for i in on if i["has_proposal"]]
    complex_figures = [i for i in proposals if i["figure_type"] in figure_step.COMPLEX_FIGURE_TYPES]
    described = [i for i in proposals if i["long_description"]]
    graded = [i for i in described if i["long_judge_outcome"] == "judged"]
    tables = [i for i in proposals if i["data_table"]]
    tables_graded = [i for i in tables if i["table_values_ok"] is not None]
    passes = sum(bool(i["long_pass"]) for i in graded)
    return {
        "figures": len(on),
        "described": len(described),
        "described_rate": rate(bool(i["long_description"]) for i in proposals),
        "complex_figures": len(complex_figures),
        "complex_described_rate": rate(bool(i["long_description"]) for i in complex_figures),
        "mean_chars": mean(len(i["long_description"]) for i in described),
        "judged": len(graded),
        "judge_errors": sum(i["long_judge_outcome"].startswith("judge_") for i in on),
        "pass_rate": _safe_div(passes, len(graded)),
        "pass_rate_ci95": wilson_interval(passes, len(graded)),
        "accurate_rate": rate(bool(i["long_accurate"]) for i in graded),
        "complete_rate": rate(bool(i["long_complete"]) for i in graded),
        "invented_values_rate": rate(bool(i["long_invented_values"]) for i in graded),
        "mean_score": mean(i["long_score"] for i in graded),
        "unsure": sum(bool(i["long_unsure"]) for i in on),
        "data_tables": len(tables),
        "data_tables_judged": len(tables_graded),
        "table_values_ok_rate": rate(bool(i["table_values_ok"]) for i in tables_graded),
        "table_wrong_values": sum(i["table_wrong_values"] or 0 for i in tables_graded),
        "values_from_text": sum(i["data_values_from_text"] is True for i in tables),
        "colour_only": sum(bool(i["colour_only"]) for i in on),
        "required_decisions": sum(i["review"] == "required" for i in on),
        "tables_offered": sum(bool(i["values_from_image"]) for i in on),
        "judge_usd": sum(i["long_judge_cost_usd"] for i in on),
    }


# ======================================================================== document


def document_page_item(
    page_id: str,
    gold: Sequence[dict],
    elements: Sequence[Element],
    fixes: dict[str, Fix],
    static: Sequence[Check],
    axe: Sequence[Check],
) -> dict:
    """Align predicted equations to gold equations in reading order and score matches.

    A predicted equation's signature is its proposed MathML's token text and, as a
    second chance, the PDF text of its region: detection is credited even when the
    transcription is wrong (that shows up in exact_lenient instead).

    Predictions without a gold match are not all the same kind of mistake, so each one
    gets a reason (`unmatched_reason`): a "fragment" (a piece of a gold equation the
    layout cut apart, or text that cannot be a whole formula: it starts or ends with an
    operator, or its brackets do not balance), "prose_math" (a whole inline formula with
    evidence that it is one: a relation, a variable with an index or a script, an italic
    variable with an operator; typically maths the source wrote as text, so the gold has
    no MathML for it), or "other" (a bare quantity, a lone symbol, anything else). A
    gold equation is "split" when two or more predictions are pieces of it.
    """
    predicted = sorted((e for e in elements if e.kind == Kind.EQUATION), key=lambda e: e.order)
    gold_sigs = [math_signature(g["mathml"]) for g in gold]
    pred_sigs = []
    for element in predicted:
        fix = fixes.get(element.id)
        proposal = fix.equation if fix is not None else None
        pred_sigs.append(
            (math_signature(proposal.mathml if proposal else None), text_signature(element.text))
        )

    def score(gold_sig: str, pred: tuple[str, str]) -> float:
        return max(similarity(gold_sig, pred[0]), similarity(gold_sig, pred[1]))

    pairs = align(gold_sigs, pred_sigs, score, min_similarity=EQUATION_ALIGN_MIN)
    matched = []
    for gi, pi, sim in pairs:
        element = predicted[pi]
        fix = fixes.get(element.id)
        proposal = fix.equation if fix is not None else None
        result = (
            score_mathml(proposal.mathml, gold[gi]["mathml"])
            if proposal is not None
            else _missing_score(gold[gi]["mathml"])
        )
        matched.append(
            {
                "gold_id": gold[gi]["id"],
                "gold_display": gold[gi].get("display"),
                "element_id": element.id,
                "pred_display": element.display,
                "similarity": round(sim, 4),
                "status": fix.status.value if fix is not None else None,
                "exact_lenient": result.exact_lenient,
                "teds": result.teds,
                "review": fix.review if fix is not None else None,
                "tier_reasons": triage.tier_reasons(fix) if fix is not None else [],
                "pred_latex": proposal.latex if proposal is not None else None,
                **_shadow_pair(fix),
            }
        )
    matched_gold = {gi for gi, _, _ in pairs}
    pieces = _split_pieces(gold, gold_sigs, predicted, pred_sigs, pairs)
    unmatched = []
    for pi, element in enumerate(predicted):
        if any(pi == p for _, p, _ in pairs):
            continue
        reason = _unmatched_reason(element, pred_sigs[pi], pi in pieces)
        unmatched.append(
            {
                "element_id": element.id,
                "pred_display": element.display,
                "reason": reason,
                "piece_of": gold[pieces[pi]]["id"] if pi in pieces else None,
            }
        )
    split = _split_gold(gold, pairs, pieces)
    return {
        "id": page_id,
        "n_gold": len(gold),
        "n_gold_by_display": dict(Counter(str(g.get("display")) for g in gold)),
        "n_pred": len(predicted),
        "matched": len(pairs),
        "pairs": matched,
        "missed_gold_ids": [g["id"] for i, g in enumerate(gold) if i not in matched_gold],
        "unmatched": unmatched,
        "unmatched_by_reason": {
            reason: sum(u["reason"] == reason for u in unmatched) for reason in UNMATCHED_REASONS
        },
        "split_gold_ids": split,
        "static_violations": _failed_codes(static),
        "axe_violations": _failed_codes(axe) if axe_status(axe) == "ran" else [],
        "axe_status": axe_status(axe),
    }


def _shadow_pair(fix: Fix | None) -> dict[str, Any]:
    """The final proposal's shadow answer for a matched pair (None without one)."""
    fields = vm.equation_fields(fix) if fix is not None else {}
    return {key: fields.get(key) for key in ("shadow_p_meaning", "shadow_flagged")}


def _split_pieces(
    gold: Sequence[dict],
    gold_sigs: Sequence[str],
    predicted: Sequence[Element],
    pred_sigs: Sequence[tuple[str, str]],
    pairs: Sequence[tuple[int, int, float]],
) -> dict[int, int]:
    """Unmatched prediction index -> index of the gold equation it is a piece of.

    Candidates are the gold equations near it in reading order (between the gold
    equations matched just before and just after it, both included) of the same display
    type; an inline piece must also sit in the same paragraph as the gold equation's
    matched prediction, if it has one. A piece is at least two symbols long, and
    PIECE_CONTAINMENT of it appears in order in the gold equation. ("d0" mentioned in
    the paragraph above "Delta d = d_f - d_0" is not a piece of that display equation.)
    """
    by_pred = {pi: gi for gi, pi, _ in pairs}
    by_gold = {gi: pi for gi, pi, _ in pairs}
    matched_preds = sorted(by_pred)
    pieces: dict[int, int] = {}
    for pi, element in enumerate(predicted):
        signatures = [sig for sig in pred_sigs[pi] if len(sig) >= 2]
        if pi in by_pred or not signatures:
            continue
        before = [p for p in matched_preds if p < pi]
        after = [p for p in matched_preds if p > pi]
        lo = by_pred[before[-1]] if before else 0
        hi = by_pred[after[0]] if after else len(gold) - 1
        best, best_share = None, PIECE_CONTAINMENT
        for gi in range(lo, hi + 1):
            if (gold[gi].get("display") or "block") != (element.display or "block"):
                continue
            host = by_gold.get(gi)
            elsewhere = host is not None and predicted[host].parent_id != element.parent_id
            if element.display == "inline" and elsewhere:
                continue
            share = max(containment(sig, gold_sigs[gi]) for sig in signatures)
            if share >= best_share:
                best, best_share = gi, share
        if best is not None:
            pieces[pi] = best
    return pieces


def _split_gold(
    gold: Sequence[dict], pairs: Sequence[tuple[int, int, float]], pieces: dict[int, int]
) -> list[str]:
    """Ids of gold equations that came out as two or more predicted elements."""
    counts = Counter(gi for gi, _, _ in pairs)
    counts.update(pieces.values())
    return [gold[gi]["id"] for gi, n in sorted(counts.items()) if n >= 2]


def _unmatched_reason(element: Element, sigs: tuple[str, str], piece: bool) -> str:
    """ "fragment", "prose_math" or "other" for a prediction with no gold match.

    "prose_math" needs positive evidence that the inline run is a formula
    (`_formula_evidence`), so it is not a catch-all: a bare quantity ("-49 m", "2.5",
    "125 m/s") or a lone symbol ("(Delta)") is "other", inline or not.
    """
    text = sigs[1] or sigs[0]
    broken = (
        not text
        or text[0] in _EDGE_OPERATORS - {"-"}
        or text[-1] in _EDGE_OPERATORS - {".", ","}
        or _brackets_broken(text)
    )
    if piece or broken:
        return "fragment"
    if element.display == "inline" and _formula_evidence(element, text):
        return "prose_math"
    return "other"


# Layout's reasons for an inline run that say it is a formula, not just maths-like.
_FORMULA_REASONS = frozenset(
    {"relation between operands", "letter with sub/superscript", "italic variable with operator"}
)
_RELATION_SIGNS = frozenset("=<>\u2264\u2265\u2260\u2248\u2261")
# A number with a unit and nothing else, in signature form (no spaces, minus as "-"):
# "-49m", "2.5", "125m/s", "9.8m/s2", "20%".
_QUANTITY = re.compile(r"[-+\u00b1]?\d[\d.,]*(?:[^\W\d_]+\d*(?:[/\u00b7\u22c5][^\W\d_]+\d*)*)?%?")
_INDEXED_VARIABLE = re.compile(r"[^\W\d_]\d+")  # "d0", "x2": a variable and its index


def _formula_evidence(element: Element, text: str) -> bool:
    """Is an inline prediction a formula (maths the source wrote as text), not a bare
    quantity or a lone symbol? It must have a relation with operands on both sides
    ("t=0s"), a letter carrying an index ("d0"), or layout's word for the same (a
    script, an italic variable next to an operator: `_FORMULA_REASONS`)."""
    if _QUANTITY.fullmatch(text):
        return False
    if _FORMULA_REASONS & set(element.reasons):
        return True
    for i, c in enumerate(text):
        if c in _RELATION_SIGNS and any(x.isalnum() for x in text[:i]):
            return any(x.isalnum() for x in text[i + 1 :])
    return bool(_INDEXED_VARIABLE.fullmatch(text))


def _brackets_broken(text: str) -> bool:
    depth = 0
    for c in text:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth < 0:
                return True
    return depth != 0


AXE_SKIP_CODES = {"skipped", "axe_skipped"}


def axe_status(axe: Sequence[Check]) -> str | None:
    """Whether axe-core really ran on a page: "ran", "skipped", "error", or None (no
    result, e.g. a page the layout could not read).

    `html_checks.axe_checks` reports a run that never happened as one Check named
    "axe": coded "skipped" when Playwright or Chromium is missing, and failed with code
    "axe_error" when axe or the browser broke (for one, Playwright refusing to start
    while another sync session is open in the thread). Neither is a clean page, so
    neither may count as zero violations. Rule results are named "axe.<rule>", so
    axe-core's real "skip-link" rule still counts as a violation.
    """
    if not axe:
        return None
    if len(axe) == 1 and axe[0].name == "axe":
        if axe[0].code in AXE_SKIP_CODES:
            return "skipped"
        if not axe[0].passed:
            return "error"
    return "ran"


def document_metrics(items: Sequence[dict]) -> dict:
    gold = sum(i["n_gold"] for i in items)
    pred = sum(i["n_pred"] for i in items)
    matched = sum(i["matched"] for i in items)
    pairs = [p for i in items for p in i["pairs"]]
    by_display = {}
    for display in ("block", "inline"):
        n_gold = sum(i["n_gold_by_display"].get(display, 0) for i in items)
        found = sum(p["gold_display"] == display for p in pairs)
        by_display[display] = {"n_gold": n_gold, "recall": _safe_div(found, n_gold)}
    static_codes: Counter[str] = Counter(c for i in items for c in i["static_violations"])
    axe_codes: Counter[str] = Counter(c for i in items for c in i["axe_violations"])
    unmatched = {
        reason: sum(i.get("unmatched_by_reason", {}).get(reason, 0) for i in items)
        for reason in UNMATCHED_REASONS
    }
    return {
        "gold_equations": gold,
        "predicted_equations": pred,
        "matched": matched,
        "detection_precision": _safe_div(matched, pred),
        "detection_recall": _safe_div(matched, gold),
        "detection_recall_by_display": by_display,
        "matched_exact_lenient": rate(p["exact_lenient"] for p in pairs),
        "matched_mean_teds": mean(p["teds"] for p in pairs),
        "end_to_end_exact_lenient": _safe_div(sum(p["exact_lenient"] for p in pairs), gold),
        # Why the unmatched predictions did not match, and the precision without the
        # inline formulas the gold has no MathML for (maths written as text). A
        # prediction is prose_math only with evidence that it is a formula
        # (`_formula_evidence`); the gold cannot confirm that, so this is an estimate.
        "unmatched_predicted": pred - matched,
        "unmatched_by_reason": unmatched,
        "detection_precision_excluding_prose_math": _safe_div(
            matched, pred - unmatched["prose_math"]
        ),
        "split_gold_equations": sum(len(i.get("split_gold_ids", [])) for i in items),
        "static_violations": sum(static_codes.values()),
        "static_violation_codes": dict(sorted(static_codes.items())),
        "axe_violations": sum(axe_codes.values()),
        "axe_violation_codes": dict(sorted(axe_codes.items())),
        "axe_checked_pages": sum(i["axe_status"] == "ran" for i in items),
        "axe_skipped_pages": sum(i["axe_status"] == "skipped" for i in items),
        "axe_error_pages": sum(i["axe_status"] == "error" for i in items),
        "page_errors": _page_errors(items),
        "review_load": document_review_load(items),
        "second_check": vm.document_metrics(items),
    }


def document_review_load(items: Sequence[dict]) -> dict:
    """Required decisions (every kind; a heading group counts once) and optional notes
    per PDF page and per section, plus flag precision/recall for the equations matched
    to gold (wrong = not exact_lenient; also without the scorer's known gaps) and the
    headings (wrong = not a gold heading or wrong level). Figures and equations with no
    gold partner have no answer key here, so they count in the load but not in the
    flags."""
    load = _sum_loads(i["review"] for i in items if "review" in i)
    sections = len(items)
    pdf_pages = sum(i.get("pdf_pages", 0) for i in items)
    pairs = [p for i in items for p in i.get("pairs", [])]
    equations = [(p.get("review") == "required", int(not p["exact_lenient"])) for p in pairs]
    clean = [
        (p.get("review") == "required", int(not p["exact_lenient"]))
        for p in pairs
        if not p.get("scorer_gap")
    ]
    headings = [u for i in items for u in i.get("heading_flags", {}).get("units", [])]
    wrong_equations = [
        {
            "page": i["id"],
            "kind": "equation",
            "gold_id": p["gold_id"],
            "review": p.get("review"),
            "reasons": p.get("tier_reasons", []),
            "scorer_gap": bool(p.get("scorer_gap")),
            "pred_latex": p.get("pred_latex"),
        }
        for i in items
        for p in i.get("pairs", [])
        if not p["exact_lenient"] and p.get("review") != "required"
    ]
    wrong_headings = [
        {"page": i["id"], "kind": "heading", **w}
        for i in items
        for w in i.get("heading_flags", {}).get("wrong_not_required", [])
    ]
    return {
        **load,
        "sections": sections,
        "pdf_pages": pdf_pages,
        "required_per_section": _per(load["required_decisions"], sections),
        "required_per_pdf_page": _per(load["required_decisions"], pdf_pages),
        "optional_per_pdf_page": _per(load["optional"], pdf_pages),
        "equation_flags": flag_metrics(equations),
        "equation_flags_excluding_scorer_gaps": flag_metrics(clean),
        "heading_flags": flag_metrics(headings),
        "flags": flag_metrics([*equations, *headings]),
        "flags_excluding_scorer_gaps": flag_metrics([*clean, *headings]),
        "wrong_not_required": [*wrong_equations, *wrong_headings],
    }


def _eval_document(
    cfg: EvalConfig, units: list[list[dict]], pdfs: list[bytes]
) -> tuple[list[dict], str | None]:
    items = []
    for page, pdf in zip(units, pdfs, strict=True):
        try:
            output = run.convert_pdf(
                pdf, settings=cfg.settings, client=cfg.client, concurrency=cfg.concurrency
            )
        except BudgetExceeded as exc:
            return items, f"budget_{exc.scope}"
        except layout.LayoutError as exc:  # the pipeline failed this page: nothing found
            item = document_page_item(_page_id(page), _of_kind(page, "equation"), [], {}, [], [])
            items.append(item | _calls_summary([]) | {"error": exc.code, "pdf_pages": 0})
            continue
        result = output.result
        stopped = next((r for f in result.fixes if (r := budget_reason(f))), None)
        if stopped:  # a half-converted page would score as misses; drop it and stop
            return items, stopped
        fixes = {f.element_id: f for f in result.fixes}
        item = document_page_item(
            _page_id(page),
            _of_kind(page, "equation"),
            result.elements,
            fixes,
            html_checks.static_checks(result.html),
            html_checks.axe_checks(result.html),
        )
        for pair in item["pairs"]:
            pair["scorer_gap"] = pair["gold_id"] in cfg.scorer_gaps
        item["review"] = load_counts(result.fixes)
        headings = predicted_headings(result.fixes)
        heading_item = heading_page_item(_page_id(page), _of_kind(page, "heading"), headings)
        item["heading_flags"] = heading_flags(heading_item, headings)
        calls = [c for f in result.fixes for c in f.calls]
        item.update(_calls_summary(calls))
        item["verifier"] = vm.page_summary(result.fixes)
        item["verifier_cost_usd"] = sum(f.verifier_cost_usd for f in result.fixes)
        item["verifier_requests"] = sum(vm.requests_sent(f) for f in result.fixes)
        item["latency_ms"] = result.stats.latency_ms or item["latency_ms"]
        item["prompt_versions"] = dict(result.prompt_versions) or item["prompt_versions"]
        # One item is one OpenStax section printed to a PDF of many pages: cost per PDF
        # page is the figure to compare with production (`ops report`), not per item.
        item["pdf_pages"] = result.stats.pages
        items.append(item)
    return items, None


# ========================================================================== report

INPUTS: dict[str, Callable[[EvalMedia, list[Any]], list[Any]]] = {
    "equations": _equation_inputs,
    "headings": _page_inputs,
    "figures": _figure_inputs,
    "document": _page_inputs,
}
RUNNERS: dict[str, Callable[[EvalConfig, list[Any], list[Any]], tuple[list[dict], str | None]]] = {
    "equations": _eval_equations,
    "headings": _eval_headings,
    "figures": _eval_figures,
    "document": _eval_document,
}
METRICS: dict[str, Callable[[Sequence[dict]], dict]] = {
    "equations": equation_metrics,
    "headings": heading_metrics,
    "figures": figure_metrics,
    "document": document_metrics,
}
STEP_PROMPTS = {
    "equations": {"equation": equation_step.PROMPT_VERSION},
    "headings": {},
    "figures": {"figure": figure_step.PROMPT_VERSION},
    "document": {
        "equation": equation_step.PROMPT_VERSION,
        "figure": figure_step.PROMPT_VERSION,
    },
}


def _prompt_versions(task: str, items: Sequence[dict]) -> dict[str, str]:
    """Versions seen on the calls (what really ran); module constants when none ran."""
    seen: dict[str, set[str]] = defaultdict(set)
    for item in items:
        for step, version in item.get("prompt_versions", {}).items():
            seen[step].add(version)
        if item.get("judge_prompt_version"):
            seen["judge"].add(item["judge_prompt_version"])
        if item.get("long_judge_prompt_version"):
            seen["judge_long"].add(item["long_judge_prompt_version"])
    versions = dict(STEP_PROMPTS[task])
    versions.update({step: ",".join(sorted(v)) for step, v in seen.items()})
    if task == "figures" and "judge" not in versions:
        versions["judge"] = judge_mod.judge_prompt_version()
    return dict(sorted(versions.items()))


# What one item is, for the cost line. A document item is an OpenStax section (one
# gold "page"), printed to a PDF of 13-16 pages; its cost per PDF page is reported too.
COST_UNITS = {"equations": "equation", "figures": "figure", "document": "section"}
LATENCY_BASIS = {
    "document": "wall-clock conversion of each section's PDF (layout, crops, model calls "
    "and assembly; DocumentStats.latency_ms)",
}
DEFAULT_LATENCY_BASIS = "sum of model-call latencies per item (excludes rendering)"


def _cost(task: str, items: Sequence[dict], budget: JobBudget | None) -> dict:
    """Cost of the finished items, plus what the budget recorded for the whole run.

    The two differ after a budget stop: an item dropped mid-way (a cheap call made
    before its escalation was refused, a describe call before its judge call was
    refused) was paid for but is not in `items`. `budget_spent_usd` includes it;
    `dropped_usd` is the difference.
    """
    # The second automated check is part of what the pipeline spends (ADR 0005); it is
    # also reported on its own so the added cost is visible.
    verifier_cost = sum(i.get("verifier_cost_usd", 0.0) for i in items)
    step_cost = sum(i.get("cost_usd", 0.0) for i in items) + verifier_cost
    judge_cost = sum(
        i.get("judge_cost_usd", 0.0) + i.get("long_judge_cost_usd", 0.0) for i in items
    )
    n = len(items)
    unit = COST_UNITS.get(task, "page")
    calls = sum(i.get("model_calls", 0) for i in items)
    if task == "figures":
        calls += sum(1 for i in items if i.get("judge_prompt_version"))
        calls += sum(1 for i in items if i.get("long_judge_prompt_version"))
    out = {
        "total_usd": step_cost + judge_cost,
        "pipeline_usd": step_cost,
        "per_item_usd": step_cost / n if n else None,
        "per_item_unit": unit,
        "model_calls": calls,
        "verifier_usd": verifier_cost,
        "verifier_per_item_usd": verifier_cost / n if n else None,
        "verifier_requests": sum(i.get("verifier_requests", 0) for i in items),
    }
    if task == "figures":
        out["judge_usd"] = judge_cost
    if task == "document":
        pdf_pages = sum(i.get("pdf_pages", 0) for i in items)
        out["pdf_pages"] = pdf_pages
        out["per_pdf_page_usd"] = step_cost / pdf_pages if pdf_pages else None
        out["verifier_per_pdf_page_usd"] = verifier_cost / pdf_pages if pdf_pages else None
    if budget is not None:
        out["budget_spent_usd"] = budget.spent_usd
        out["budget_calls"] = budget.calls
        out["dropped_usd"] = max(0.0, budget.spent_usd - out["total_usd"])
    return out


def build_report(
    cfg: EvalConfig, items: list[dict], *, planned_ids: list[str], stopped: str | None
) -> dict:
    task = cfg.task
    latencies = [float(i.get("latency_ms", 0)) for i in items]
    models = {"main": cfg.settings.main_model, "cheap": cfg.settings.cheap_model}
    if task == "figures":
        models["judge"] = cfg.judge_setup.model
    used = sorted({m for i in items for m in i.get("models", [])})
    report = {
        "schema_version": SCHEMA_VERSION,
        "task": task,
        "date": cfg.date,
        "pipeline_version": run.PIPELINE_VERSION,
        "prompt_versions": _prompt_versions(task, items),
        "models": models,
        "models_used": used,
        "client": cfg.client_label,
        "dataset": {
            "gold_dir": cfg.gold_label or cfg.gold_dir.name,
            "manifest_sha256": manifest_sha256(cfg.gold_dir),
            "limit": cfg.limit,
            **({"every": cfg.every} if cfg.every else {}),
            "unit": {"equations": "equation", "figures": "figure"}.get(task, "page"),
            "selection_sha256": hashlib.sha256("\n".join(planned_ids).encode()).hexdigest(),
        },
        "n_planned": len(planned_ids),
        "n": len(items),
        "complete": stopped is None and len(items) == len(planned_ids),
        "stopped_reason": stopped,
        "metrics": METRICS[task](items),
        "cost": _cost(task, items, cfg.budget),
        "latency_ms": latency_summary(latencies)
        | {"basis": LATENCY_BASIS.get(task, DEFAULT_LATENCY_BASIS)},
    }
    if task == "figures" and cfg.judge is not None:
        # Which provider judged is recorded when the run chose it (the CLI always does).
        report["judge_client"] = cfg.judge.describe()
    return report


def run_pipeline_eval(cfg: EvalConfig) -> EvalRun:
    """Run one task end to end and return the report plus per-item rows.

    Raises RenderError, before any model call, when an input cannot be built.
    """
    if cfg.task not in RUNNERS:
        raise ValueError(f"unknown task {cfg.task!r}; expected one of {', '.join(TASKS)}")
    units = select_units(cfg.task, load_gold(cfg.gold_dir), cfg.limit, cfg.every)
    planned = unit_ids(cfg.task, units)
    # All inputs before the first model call (see the module docstring): a RenderError
    # raises here, with nothing spent. Then the browser goes, before any step runs.
    inputs = INPUTS[cfg.task](cfg.media, units)
    cfg.media.close()
    items, stopped = RUNNERS[cfg.task](cfg, units, inputs)
    return EvalRun(
        report=build_report(cfg, items, planned_ids=planned, stopped=stopped), items=items
    )


# ========================================================================== output


def write_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_items(items: Iterable[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{_fmt(value[0], digits)}-{_fmt(value[1], digits)}"
    if isinstance(value, dict):  # category counts, e.g. page_errors
        return ", ".join(f"{k}: {v}" for k, v in value.items()) or "none"
    return str(value)


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_fmt(c) for c in row) + " |" for row in rows]
    return lines


HEADLINE = {
    "equations": ["exact_strict", "exact_lenient", "exact_lenient_ci95", "mean_teds",
                  "proposal_rate"],
    "headings": ["precision", "recall", "recall_ci95", "f1", "level_accuracy",
                 "gold_headings", "predicted_headings", "page_errors"],
    "figures": ["proposal_rate", "check_pass_rate", "judge_pass_rate",
                "judge_pass_rate_ci95", "judge_pass_rate_answered", "judge_mean_score",
                "judge_factual_error_rate", "judge_errors"],
    "document": ["detection_precision", "detection_recall", "matched_exact_lenient",
                 "end_to_end_exact_lenient", "unmatched_predicted",
                 "detection_precision_excluding_prose_math", "split_gold_equations",
                 "static_violations", "axe_violations", "axe_checked_pages",
                 "axe_skipped_pages", "axe_error_pages", "page_errors"],
}  # fmt: skip


LONG_HEADLINE = [
    "described", "described_rate", "complex_described_rate", "mean_chars", "judged",
    "pass_rate", "pass_rate_ci95", "accurate_rate", "complete_rate", "invented_values_rate",
    "mean_score", "unsure", "data_tables", "table_values_ok_rate", "table_wrong_values",
    "values_from_text", "colour_only", "required_decisions", "tables_offered",
    "judge_errors",
]  # fmt: skip


def _review_rows(task: str, load: dict) -> list[tuple[str, Any]]:
    """The headline review-load numbers of one report (`*_review_load`)."""
    if task == "equations":
        flags, clean = load["flags"], load["flags_excluding_scorer_gaps"]
        return [
            ("required", load["required"]),
            ("required_per_section", load["required_per_section"]),
            ("optional", load["optional"]),
            ("flag_precision", flags["flag_precision"]),
            ("flag_recall", flags["flag_recall"]),
            ("flag_precision_excluding_scorer_gaps", clean["flag_precision"]),
            ("flag_recall_excluding_scorer_gaps", clean["flag_recall"]),
            ("wrong_not_required", len(load["wrong_not_required"])),
        ]
    rows: list[tuple[str, Any]] = [
        ("required_decisions", load["required_decisions"]),
        ("required_per_pdf_page", load["required_per_pdf_page"]),
        ("optional", load["optional"]),
        ("optional_per_pdf_page", load["optional_per_pdf_page"]),
        ("flag_precision", load["flags"]["flag_precision"]),
        ("flag_recall", load["flags"]["flag_recall"]),
    ]
    if task == "document":
        clean = load["flags_excluding_scorer_gaps"]
        rows += [
            ("flag_precision_excluding_scorer_gaps", clean["flag_precision"]),
            ("flag_recall_excluding_scorer_gaps", clean["flag_recall"]),
        ]
    rows.append(("wrong_not_required", len(load["wrong_not_required"])))
    return rows


def _usd(value: float | None) -> str:
    """A small dollar amount with enough digits to read (the check costs ~$0.00005)."""
    return "n/a" if value is None else f"${value:.6f}"


def _second_check_rows(task: str, metrics: dict) -> list[tuple[str, Any]]:
    """Headline numbers of `eval.verifier_metrics` for one report."""
    check = metrics["second_check"]
    if task == "figures":
        agreement = check["agreement_with_judge"]
        return [
            ("answered", check["answered"]),
            ("outcomes", check["outcomes"]),
            ("flagged", check["flagged"]),
            ("flag_rate", check["flag_rate"]),
            ("agreement_with_judge", agreement["raw_agreement"]),
            ("kappa_with_judge", agreement["kappa"]),
            ("auroc_vs_judge_fail", agreement["auroc_score_vs_judge_fail"]),
            ("cost_per_figure_usd", _usd(check["per_figure_usd"])),
        ]
    shadow = check["final"] if task == "equations" else check["equations_matched"]
    rows: list[tuple[str, Any]] = [
        ("answered (final proposal)", shadow["answered"]),
        ("wrong (not exact_lenient)", shadow["wrong"]),
        ("auroc_vs_wrong", shadow["auroc_vs_wrong"]),
        ("auroc_vs_wrong_excluding_scorer_gaps", shadow["auroc_vs_wrong_excluding_scorer_gaps"]),
        ("would_flag", shadow["would_flag"]),
        ("would_flag_precision", shadow["would_flag_precision"]),
        ("would_flag_recall", shadow["would_flag_recall"]),
    ]
    if task == "equations":
        rows += [
            ("cheap_stage_answered", check["cheap_stage"]["answered"]),
            ("cheap_stage_would_flag", check["cheap_stage"]["would_flag"]),
            ("cost_per_equation_usd", _usd(check["per_equation_usd"])),
        ]
    else:
        rows += [
            ("figure_checked", check["figure_checked"]),
            ("figure_flagged", check["figure_flagged"]),
            ("cost_per_pdf_page_usd", _usd(check["per_pdf_page_usd"])),
        ]
    return rows


def format_markdown(report: dict) -> str:
    """Human summary of a report (metrics only; no document text)."""
    metrics = report["metrics"]
    status = "complete" if report["complete"] else f"PARTIAL ({report['stopped_reason']})"
    lines = [
        f"## Eval: {report['task']} ({report['date']}, {report['client']} client)",
        "",
        f"n = {report['n']} of {report['n_planned']} {report['dataset']['unit']}s, {status}. "
        f"Pipeline {report['pipeline_version']}; gold manifest "
        f"{report['dataset']['manifest_sha256'][:12]}.",
        "",
    ]
    lines += _table(["metric", "value"], ((k, metrics.get(k)) for k in HEADLINE[report["task"]]))
    cost, latency = report["cost"], report["latency_ms"]
    lines += [
        "",
        f"Cost: ${cost['total_usd']:.4f} total, "
        f"${_fmt(cost['per_item_usd'], 5)} per {cost['per_item_unit']} (pipeline), "
        + (
            f"${_fmt(cost['per_pdf_page_usd'], 5)} per PDF page ({cost['pdf_pages']} pages), "
            if "per_pdf_page_usd" in cost
            else ""
        )
        + f"{cost['model_calls']} model calls. "
        f"Latency p50 {_fmt(latency['p50'], 0)} ms, p95 {_fmt(latency['p95'], 0)} ms"
        + (f" ({latency['basis']})." if latency.get("basis") else "."),
    ]
    if cost.get("dropped_usd"):
        lines.append(
            f"Budget recorded ${cost['budget_spent_usd']:.4f}: ${cost['dropped_usd']:.4f} "
            "went to items dropped at the budget stop."
        )
    if "review_load" in metrics:
        lines += ["", "### Review load", ""]
        lines += _table(["metric", "value"], _review_rows(report["task"], metrics["review_load"]))
    if "second_check" in metrics:
        lines += ["", "### Second automated check (Decisions API)", ""]
        lines += _table(["metric", "value"], _second_check_rows(report["task"], metrics))
    if "long_descriptions" in metrics:
        lines += ["", "### Long descriptions (FEATURE_LONG_DESCRIPTIONS on)", ""]
        long = metrics["long_descriptions"]
        lines += _table(["metric", "value"], ((k, long[k]) for k in LONG_HEADLINE))
    if report["task"] == "document":
        lines += ["", "### Unmatched predicted equations", ""]
        lines += _table(["reason", "n"], metrics["unmatched_by_reason"].items())
    if report["task"] == "equations":
        lines += ["", "### Selective prediction (keep proposals with confidence >= t)", ""]
        lines += _table(
            ["t", "coverage", "accuracy", "kept"],
            (
                (f"{r['threshold']:.2f}", r["coverage"], r["accuracy"], r["kept"])
                for r in metrics["selective"]
            ),
        )
        for title, key in (("By status", "by_status"), ("By route", "by_route")):
            lines += ["", f"### {title}", ""]
            lines += _table(
                ["group", "n", "exact_lenient", "mean_teds"],
                ((g, v["n"], v["exact_lenient"], v["mean_teds"]) for g, v in metrics[key].items()),
            )
    return "\n".join(lines) + "\n"
