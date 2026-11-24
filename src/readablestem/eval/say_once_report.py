"""`readablestem eval say-once`: the say-once measure (`pipeline.say_once`) over the corpus.

Per page, the share of words said by no element and the share said twice, from what layout
reads and what it makes of it, with no model: each file is read by `layout.analyze` as a job
reads it (the page maths a model would add is not asked for, so the elements are layout's
own) and counted by the function the conversion runs at its end in shadow (SW60). Spends
nothing and gates nothing. The pages over the line (`say_once.LINE`) are the ones the
shadow notice names; the lines above and below it are counted too, so the line can be
moved on figures and not on a guess.

Counts only: no word, no page text and no reading is kept in a record. The corpus is a
development set, so no figure here meets a bar; `docs/eval-say-once.md` says what the
figures were when the line was chosen.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from readablestem.core.types import StepFacts
from readablestem.eval import corpus, measure
from readablestem.eval.measure import Converted, Upload
from readablestem.pipeline import say_once

# A share of the page's words, as the edges of the counts by line: the first line is the
# first at which a page is counted ("at least 5%"); the page's words are at least MIN_WORDS.
LINES = (0.01, 0.02, 0.05, 0.10, 0.15, 0.25, 0.50)
# The codes that are a word said again (`say_once.CODES` but the missing one).
PERCENTILES = {"median": 0.5, "p90": 0.9, "p99": 0.99, "max": 1.0}
TWICE = tuple(code for code in say_once.CODES if code != "no_owner")


def _convert(entry: str, image_pages: bool, exam: bool) -> dict[str, Any]:
    """One upload, read and counted (a process of `run`)."""
    from readablestem.pipeline import layout

    upload = Upload(entry, image_pages=image_pages, exam=exam)
    settings = measure.replay_settings(corpus.ANSWERS)
    try:
        pdf = corpus.document(corpus.load_manifest()[entry], get=measure._not_fetched)
    except corpus.CorpusError:
        return Converted(upload.name, error="not_fetched").as_dict()
    started = time.perf_counter()
    try:
        grouping: dict[str, Any] = (
            {"display_grouping": True} if settings.layout_display_grouping else {}
        )
        if settings.layout_inline_joins:
            grouping["inline_joins"] = True
        if settings.region_tree == "on":
            grouping["region_tree"] = True
        if settings.layout_join_panels:
            grouping["join_panels"] = True
        if settings.layout_figure_art:
            grouping["figure_art"] = True
        grouping["table_search_limited"] = False
        doc = layout.analyze(pdf, max_pages=settings.max_pages_per_job, **grouping)
    except Exception as exc:
        from readablestem.web.worker import error_code_for

        return Converted(upload.name, error=error_code_for(exc)).as_dict()
    read_s = time.perf_counter() - started
    counted = time.perf_counter()
    made = say_once.measure(doc.sources, doc.elements, skip=doc.kept_pages)
    counted_s = time.perf_counter() - counted
    # what the conversion keeps of it, in shadow
    from readablestem.pipeline.shadow import Shadow

    shadow = Shadow()
    shadow.over_line(say_once.NOTICE_CODE, lambda: say_once.over_line(made))
    return Converted(
        upload.name,
        pages=len(doc.pages),
        facts=StepFacts(shadow=shadow.facts()),
        wall_s=round(read_s + counted_s, 4),
        say_once=[
            page.record() | {"measure_s": round(counted_s / max(1, len(made)), 5)}
            for page in made.values()
        ],
    ).as_dict()


def run(
    uploads: Sequence[Upload],
    *,
    at_once: int = 1,
    said: Callable[[Converted], None] | None = None,
) -> list[Converted]:
    """Each upload read and counted in a process of its own, `at_once` at a time. `said` is
    called as each one ends."""
    done: list[Converted] = []
    with ProcessPoolExecutor(max_workers=max(1, at_once), max_tasks_per_child=1) as pool:
        for raw in pool.map(
            _convert,
            [upload.entry for upload in uploads],
            [upload.image_pages for upload in uploads],
            [upload.exam for upload in uploads],
        ):
            done.append(Converted.from_dict(raw))
            if said is not None:
                said(done[-1])
    return done


def _share(count: int, whole: int) -> float:
    return count / whole if whole else 0.0


def _pages(run: Sequence[Converted]) -> list[tuple[str, dict[str, Any]]]:
    return [(done.upload, page) for done in run if not done.error for page in done.say_once or []]


def _count(page: dict[str, Any], codes: Sequence[str]) -> int:
    return sum(page["codes"].get(code, [0, 0])[0] for code in codes)


def report(run: Sequence[Converted]) -> dict[str, Any]:
    """The figures of a run, as plain data (the `metrics` of the record `measure.record`
    writes). Pages of fewer than `say_once.MIN_WORDS` words are in the totals and in no
    count by line: a page with a handful of words has no share to speak of."""
    pages = _pages(run)
    body = [(name, page) for name, page in pages if page["words"] >= say_once.MIN_WORDS]
    words = sum(page["words"] for _, page in pages)
    by_code = {
        code: sum(_count(page, [code]) for _, page in pages)
        for code in (*say_once.CODES, *say_once.INFO_CODES)
    }
    left_out: dict[str, int] = {}
    for _, page in pages:
        for reason, glyphs in page["left_out"].items():
            left_out[reason] = left_out.get(reason, 0) + glyphs
    glyphs = sum(page["glyphs"] for _, page in pages)

    def at_lines(codes: Sequence[str]) -> dict[str, int]:
        return {
            f"{line:.0%}": sum(1 for _, page in body if _count(page, codes) / page["words"] >= line)
            for line in LINES
        }

    def share_of(codes: Sequence[str]) -> list[float]:
        return sorted(_count(page, codes) / page["words"] for _, page in body)

    def at(sorted_shares: list[float], fraction: float) -> float:
        return (
            round(sorted_shares[int(fraction * (len(sorted_shares) - 1))], 4)
            if sorted_shares
            else 0.0
        )

    missing, twice = share_of(["no_owner"]), share_of(TWICE)
    flagged = [
        {
            "upload": name,
            "page": page["page"],
            "words": page["words"],
            "share": round(_share(_count(page, ["no_owner"]), page["words"]), 4),
        }
        for name, page in body
        if _share(_count(page, ["no_owner"]), page["words"]) >= say_once.LINE
    ]
    read = [done.wall_s for done in run if not done.error and done.pages]
    return {
        "line": {"share": say_once.LINE, "min_words": say_once.MIN_WORDS},
        "uploads": {
            "measured": sum(1 for done in run if not done.error),
            "not_measured": sorted(done.upload for done in run if done.error),
        },
        "pages": {"all": len(pages), "with_words": len(body), "words": words, "glyphs": glyphs},
        "words_by_code": by_code,
        "left_out_glyphs": dict(sorted(left_out.items())),
        "missing": {
            "share_of_words": round(_share(by_code["no_owner"], words), 5),
            "pages_at_least": at_lines(["no_owner"]),
            "page_share_at": {name: at(missing, f) for name, f in PERCENTILES.items()},
        },
        "twice": {
            "share_of_words": round(_share(sum(by_code[c] for c in TWICE), words), 5),
            "pages_at_least": at_lines(TWICE),
            "by_code_pages_at_least_15": {
                code: sum(1 for _, page in body if _count(page, [code]) / page["words"] >= 0.15)
                for code in TWICE
            },
            "page_share_at": {name: at(twice, f) for name, f in PERCENTILES.items()},
        },
        "flagged": flagged,
        "seconds": {
            "read_and_count_per_file_mean": round(sum(read) / len(read), 3) if read else 0.0,
            "count_per_page_mean": round(
                sum(page.get("measure_s", 0.0) for _, page in pages) / len(pages), 5
            )
            if pages
            else 0.0,
        },
    }


def render(record: dict[str, Any]) -> str:
    """A say-once record as the lines `readablestem eval say-once` prints."""
    made = record["metrics"]
    pages, line = made["pages"], made["line"]
    lines = [
        f"say once: {made['uploads']['measured']} files, {pages['all']} pages "
        f"({pages['with_words']} of at least {line['min_words']} words), {pages['words']} words",
        *measure.header(record),
        "",
        f"words said by no element: {made['missing']['share_of_words']:.2%} of all words; "
        f"words said twice: {made['twice']['share_of_words']:.2%}",
        "left out by rule, in glyphs: "
        + ", ".join(f"{name} {n}" for name, n in made["left_out_glyphs"].items()),
        "",
        "pages with that share of their words or more:",
    ]
    rows = [
        (name, made["missing"]["pages_at_least"][name], made["twice"]["pages_at_least"][name])
        for name in made["missing"]["pages_at_least"]
    ]
    lines += measure.table(("share", "in no element", "said twice"), rows)
    lines += [
        "",
        f"over the line ({line['share']:.0%}, {line['min_words']} words): "
        f"{len(made['flagged'])} pages",
    ]
    for page in made["flagged"]:
        lines.append(
            f"  {page['upload']} page {page['page'] + 1}: "
            f"{page['share']:.1%} of {page['words']} words"
        )
    if made["uploads"]["not_measured"]:
        lines += ["", "not measured: " + ", ".join(made["uploads"]["not_measured"])]
    lines += [
        "",
        "This does not see a block read in the wrong order, or a word said in the wrong place.",
    ]
    return "\n".join(lines)
