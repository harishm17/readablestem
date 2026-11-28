"""`readablestem eval order`: whether each gold page is read in the order it was written, and
what else is wrong on a page that is.

Made from one measured replay of the corpus (`eval.measure`): every file with gold converted
as the worker converts a job, under the production profile, with the recorded answers in
place of the provider. The replay spends nothing. The counts are those of `eval.order`,
which says what each one is; this module adds them up and holds a bar.

Two things are scored against the same gold, and they answer different questions:

- `html`: the page a student is given, read as a listener hears it. This is the product,
  and every headline figure is of it.
- `elements`: what the pipeline holds before any file is written. Where the two differ,
  the writer changed what is heard (it joined, dropped or moved something).

What is printed, and why not one figure. `right` is a page read in order with nothing
broken, joined across columns or read out of a stamp. A page can be `right` and still read
a running head aloud, cut a paragraph in two, run two paragraphs together, or put a caption
on the wrong picture: `faultless` is `right` with none of those (`eval.order`). So pages
right is never printed alone: beside it stand the faultless pages, the pages with furniture
left in, and each further count. A formula missing from a sentence is not counted by any of
them (the gold does not mark where a formula stands); the equations eval and the page audit
count that. How often the page check flags a page that is wrong here is not measured yet.

The groups are the manifest's (`labels.group`): `real` pages of published documents,
labeled by hand; `self-made` pages built from a source that carries each block's role;
`edge` pages generated to hold one hard case each. Bars written as "of 78" are of the real
and the self-made pages together.

What is left out of the headline, and printed apart: a file whose recording does not hold
every answer its conversion asks for (an element whose answer is missing fails, which is
the recording's doing). A file this machine cannot replay, or whose conversion failed, is
named as not measured.

The bar (`judge`): against an earlier record of the same documents, scored by the same
version of the scorer, no page that was right is not right now. A page that became right
is named too. The corpus is a development set: these figures meet no release bar.

A layout gold set (`gold/layout_gold.py`: a folder of files and gold built from their
sources, registered under `data/gold/layout/`) is scored by `set_run`: each file converted
as a job converts it under production's behavior settings, with no model. An equation, a
picture and a table then keep what the file gives, which is what the reading order is made
of. Its groups are the registry's kinds of document. A held-out set is scored for a
release and not while the reader is being changed: its record holds totals and no page,
so no failing page can be looked up.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from readablestem.eval import corpus, measure, order, split
from readablestem.eval.measure import Converted

SOURCES = ("html", "elements")
SAID = {
    "html": "the page a student is given",
    "elements": "what the pipeline holds before the page is written",
}
# The counts printed beside pages right: on how many pages, and how many in all.
BESIDE = ("furniture", "stray", "cut", "merged", "heading_merged", "caption_wrong")
OTHER = "other"


def corpus_groups() -> dict[str, str]:
    """The manifest's group of each corpus file with gold."""
    return {
        entry.id: (entry.labels or {}).get("group") or OTHER
        for entry in corpus.load_manifest().values()
        if entry.gold
    }


def set_groups(registry: Mapping[str, Any]) -> dict[str, str]:
    """The kind of each document of a layout gold set, from its registry."""
    return {row["id"]: row.get("kind") or OTHER for row in registry["documents"]}


def set_stamp(registry: Mapping[str, Any], names: Iterable[str]) -> dict[str, Any]:
    """What a record of a layout gold set says of its documents: the set's name and the
    side, count and hash of the documents scored (`measure.record`'s `of_set`)."""
    rows = {row["id"]: row for row in registry["documents"]}
    names = tuple(sorted(set(names) & set(rows)))
    hashes = {name: rows[name]["pdf_sha256"] + rows[name]["gold_sha256"] for name in names}
    held_out = registry["side"] == split.HELD_OUT
    chosen = split.Split(
        seed=registry["set"],
        development=() if held_out else names,
        held_out=names if held_out else (),
        hashes=hashes,
    )
    return {"set": registry["set"], "split": chosen.stamp(registry["side"])}


def set_settings(*, model_order: bool = False, live: bool = False) -> Any:
    """Production's behavior settings for a conversion that asks no model, with a job's
    own limits: a document longer than a job may be is not measured, and is named.

    `model_order` turns the model's page order on (`pipeline.page_order`, off in production);
    `live` starts from the environment's settings, which hold the key a live run asks with."""
    from readablestem.config import Settings, get_settings
    from readablestem.ops import profile

    base = get_settings() if live else Settings(_env_file=None, env="test")  # type: ignore[call-arg]
    settings = profile.load().apply(base)
    if model_order:
        settings = settings.model_copy(update={"feature_layout_model_order": True})
    return settings


@dataclass(frozen=True)
class OrderAnswers:
    """Where the page order step's answers come from when a set is converted with the step on
    (`eval order --model-order`): the recording `path`, replayed offline. `max_usd` set: what
    the recording lacks is asked of the model, up to that many dollars for the whole run
    (the caps of `docs/eval.md`: it bounds reservations, not spend). Only page order requests
    are answered; any other request is refused, as with no model."""

    path: str
    max_usd: float | None = None

    @property
    def live(self) -> bool:
        return self.max_usd is not None


# One call a page, and a set holds a few hundred pages: the dollar cap is what stops a run.
MAX_CALLS = 5000


@cache
def _live_budget(max_usd: float) -> Any:
    """One budget for every conversion of this process: the cap is the run's."""
    from readablestem.core.budget import InMemorySpendGuard, JobBudget

    return JobBudget(InMemorySpendGuard(cap_usd=max_usd), max_usd=max_usd, max_calls=MAX_CALLS)


class PageOrderOnly:
    """A `VisionClient` that answers page order requests from the recording (or, with `live`,
    from the model for what it lacks) and refuses every other request, counting them."""

    verifier = None

    def __init__(self, settings: Any, answers: OrderAnswers) -> None:
        from readablestem.eval.answers import CachedClient

        inner = None
        if answers.max_usd is not None:
            from readablestem.models import make_live_client

            inner = make_live_client(settings, _live_budget(answers.max_usd), second_check=False)
        self.cached = CachedClient(
            inner, Path(answers.path), settings, live_limit_usd=answers.max_usd
        )
        self.refused: Counter[str] = Counter()

    def run(self, request: Any, schema: Any, *, is_fallback: bool = False) -> Any:
        if request.step == "page_order":
            return self.cached.run(request, schema, is_fallback=is_fallback)
        from readablestem.core.types import ModelCall

        self.refused[request.step] += 1
        call = ModelCall(
            step=request.step,
            model=request.model,
            prompt_version=request.prompt_version,
            is_fallback=is_fallback,
            outcome="refused",
        )
        return None, call


def _convert(pdf: bytes, settings: Any, client: Any) -> Any:
    """`pdf` converted as a job converts it under `settings` and asking `client`: its output,
    or the code of the error it ended with."""
    from readablestem.core.types import ConversionOptions
    from readablestem.web.isolated_convert import convert_isolated

    try:
        return convert_isolated(
            pdf,
            settings=settings,
            client=client,
            options=ConversionOptions.from_settings(settings),
            concurrency=settings.model_concurrency,
        )
    except Exception as exc:
        from readablestem.web.worker import error_code_for

        return error_code_for(exc)


def convert_document(pdf: bytes) -> Any:
    """One document of a set converted with no model, as a job converts it under
    `set_settings()`: its output, or the code of the error it ended with (a file the reader
    refuses or cannot finish ends as a job's does)."""
    from readablestem.models.fake import FakeVisionClient

    return _convert(pdf, set_settings(), FakeVisionClient())


def _set_document(
    pdf_path: str, gold_path: str, answers: OrderAnswers | None = None
) -> dict[str, Any]:
    """One document of a set, converted with no model (or with the page order's recorded
    answers) and scored (a process of `set_run`)."""
    from readablestem.core.types import StepFacts

    gold = json.loads(Path(gold_path).read_text(encoding="utf-8"))
    pdf = Path(pdf_path).read_bytes()
    client: PageOrderOnly | None = None
    if answers is None:
        output = convert_document(pdf)
    else:
        settings = set_settings(model_order=True, live=answers.live)
        client = PageOrderOnly(settings, answers)
        output = _convert(pdf, settings, client)
    if isinstance(output, str):
        return Converted(gold["id"], error=output).as_dict()
    extra: dict[str, Any] = {}
    if client is not None:
        summary = output.result.page_order
        extra = {
            "page_order": (summary.model_dump() if summary is not None else {})
            | {"other_steps_refused": dict(client.refused)},
            "missed": client.cached.unanswered,
            "usd": output.result.stats.cost_usd,
            "calls": output.result.stats.model_calls,
        }
    done = Converted(
        gold["id"],
        pages=output.result.stats.pages,
        facts=output.facts or StepFacts(),
        order=measure.order_of(gold, pdf, output.result),
        **extra,
    )
    return done.as_dict()


def set_run(
    root: Path,
    *,
    only: Sequence[str] = (),
    at_once: int = 1,
    said: Callable[[Converted], None] | None = None,
    answers: OrderAnswers | None = None,
) -> list[Converted]:
    """Each document of a layout gold set's folder (`files/<id>.pdf`, `gold/<id>.json`)
    converted as a job converts it, with no model, and scored: `at_once` at a time, each in
    a process of its own. `said` is called as each one ends. `answers`: the model's page order
    is on and answers from that recording. A live run (`answers.live`) converts one document
    at a time in this process, so that its cap is the run's."""
    from readablestem.gold import layout_gold

    wanted = [
        (str(pdf), str(root / "gold" / f"{doc_id}.json"))
        for doc_id, pdf, _gold in layout_gold.documents(root)
        if not only or doc_id in only
    ]
    measured = []
    if answers is not None and answers.live:
        for pdf_path, gold_path in wanted:
            measured.append(Converted.from_dict(_set_document(pdf_path, gold_path, answers)))
            if said is not None:
                said(measured[-1])
        return measured
    with ProcessPoolExecutor(max_workers=max(1, at_once), max_tasks_per_child=1) as pool:
        for raw in (
            pool.map(_set_document, *zip(*wanted, strict=True), [answers] * len(wanted))
            if wanted
            else ()
        ):
            measured.append(Converted.from_dict(raw))
            if said is not None:
                said(measured[-1])
    return measured


def _sums(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Every count added up over `rows`, on how many pages each of `BESIDE` is found, and how
    many documents have every one of their gold pages right."""
    sums: dict[str, Any] = dict(order.total(list(rows)))
    sums["pages_with"] = {count: sum(1 for row in rows if row.get(count)) for count in BESIDE}
    by_document: dict[str, list[bool]] = {}
    for row in rows:
        by_document.setdefault(row["upload"], []).append(bool(row["right"]))
    sums["documents"] = len(by_document)
    sums["documents_right"] = sum(all(pages) for pages in by_document.values())
    return sums


def _model_order(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """What the page order step did over the documents, added up (`PageOrderSummary`, and the
    requests of other steps that were refused); empty when it was off."""
    total: Counter[str] = Counter()
    refused: Counter[str] = Counter()
    for run in runs:
        for name, value in run.items():
            if name == "other_steps_refused":
                refused.update(value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                total[name] += value
    if not runs:
        return {}
    return {**total, "documents": len(runs), "other_steps_refused": dict(refused)}


def _why(row: dict[str, Any]) -> list[str]:
    """What keeps a page from being right, as the names of its counts."""
    found: list[str] = []
    out_of_order = row.get("opairs", 0) - row.get("opairs_ok", 0)
    for count, said in (
        (row.get("lost"), "blocks not found"),
        (row.get("broken"), "blocks broken"),
        (out_of_order, "neighbours out of order"),
        (row.get("xjoin"), "joins across columns"),
        (row.get("ejoin"), "entries joined"),
        (row.get("stamp_in_text"), "stamps read in the text"),
        (row.get("numrun_bad"), "numbered runs out of order"),
    ):
        if count:
            found.append(f"{said}: {count}")
    return found or ["not clean"]


def report(
    measured: Sequence[Converted],
    groups: Mapping[str, str] | None = None,
    *,
    listed: bool = True,
) -> dict[str, Any]:
    """The figures of a measured replay, as plain data: the `metrics` of an order record
    (`measure.record`), which `render` prints and `judge` holds the bar on. `groups`: the
    group of each document (the corpus's when not given). `listed` False keeps the totals
    and no page: a held-out set's record names no page."""
    group_of: Mapping[str, str] = corpus_groups() if groups is None else groups
    scored = [done for done in measured if not done.error and done.order is not None]
    failed = sorted(done.upload for done in measured if done.error)
    whole = [done for done in scored if not done.missed]
    short = {done.upload: done.missed for done in scored if done.missed}

    def rows_of(uploads: Iterable[Converted], source: str) -> list[dict[str, Any]]:
        return [
            {**row, "upload": done.upload, "group": group_of.get(done.upload.split("+")[0], OTHER)}
            for done in uploads
            for row in (done.order or {}).get(source, [])
        ]

    sources = {}
    for source in SOURCES:
        rows = rows_of(whole, source)
        found = sorted({row["group"] for row in rows})
        sources[source] = {
            "all": _sums(rows),
            "groups": {name: _sums([r for r in rows if r["group"] == name]) for name in found},
            "main": _sums([r for r in rows if r["group"] in ("real", "self-made")]),
            "apart": _sums(rows_of([d for d in scored if d.missed], source)),
        }
    pages = {}
    for source in SOURCES:
        for row in rows_of(scored, source):
            page = pages.setdefault(
                (row["upload"], row["page"]),
                {
                    "upload": row["upload"],
                    "page": row["page"],
                    "group": row["group"],
                    "apart": row["upload"] in short,
                },
            )
            kept = {key: row[key] for key in ("right", "faultless", *BESIDE)}
            page[source] = kept | ({} if row["right"] else {"why": _why(row)})
    model_order = _model_order([done.page_order for done in scored if done.page_order is not None])
    return {
        "scorer": order.VERSION,
        "listed": listed,
        **({"model_order": model_order} if model_order else {}),
        "uploads": {
            "measured": len(whole),
            "answers_missing": dict(sorted(short.items())),
            "not_measured": failed,
            "without_gold": sum(1 for done in measured if not done.error and done.order is None),
        },
        "sources": sources,
        "pages": [pages[key] for key in sorted(pages)] if listed else [],
    }


@dataclass(frozen=True)
class Verdict:
    """What the bar says of a record against an earlier one."""

    judged: bool  # False: the two cannot be compared, and `why` says so
    why: str = ""
    worse: list[str] = field(default_factory=list)  # pages that were right and are not
    better: list[str] = field(default_factory=list)  # pages that were not right and are

    @property
    def met(self) -> bool:
        return self.judged and not self.worse


def judge(now: dict[str, Any], before: dict[str, Any], *, source: str = "html") -> Verdict:
    """The bar, on two order records: no page that was right in `before` is not right now.
    Only two records of the same documents, scored by the same version of the scorer, are
    compared, and every page scored then must be scored now."""
    here = (now.get("split") or {}).get("set_sha256")
    there = (before.get("split") or {}).get("set_sha256")
    if not here or here != there:
        return Verdict(False, "the two records are not of the same documents")
    one, other = now["metrics"], before["metrics"]
    if one["scorer"] != other["scorer"]:
        return Verdict(
            False,
            f"that record was scored by version {other['scorer']} of the scorer, "
            f"this one by version {one['scorer']}",
        )

    def right(metrics: dict[str, Any]) -> dict[tuple[str, int], int]:
        return {
            (page["upload"], page["page"]): page[source]["right"]
            for page in metrics["pages"]
            if source in page
        }

    if not (one.get("listed", True) and other.get("listed", True)):
        return Verdict(False, "a held-out set's record names no page to compare")
    was, is_now = right(other), right(one)
    gone = sorted(set(was) - set(is_now))
    if gone:
        return Verdict(False, f"{len(gone)} pages scored then are not scored now")
    name = "{} page {}".format
    return Verdict(
        True,
        worse=[name(*key) for key in sorted(was) if was[key] and not is_now[key]],
        better=[name(*key) for key in sorted(was) if not was[key] and is_now[key]],
    )


_HEAD = (
    "pages",
    "right",
    "faultless",
    "furniture",
    "stray",
    "cut",
    "merged",
    "heading merged",
    "caption wrong",
    "broken",
    "joined across",
)


def _line(name: str, sums: dict[str, Any]) -> tuple[Any, ...]:
    """One row of the table: a count that can be on several places of a page is printed as
    its pages and, in brackets, its places."""
    with_ = sums["pages_with"]

    def both(count: str) -> str:
        return f"{with_[count]} ({sums.get(count, 0)})"

    return (
        name,
        sums["pages"],
        sums.get("right", 0),
        sums.get("faultless", 0),
        *(both(count) for count in ("furniture", "stray", "cut", "merged", "heading_merged")),
        f"{sums.get('caption_wrong', 0)} of {sums.get('caption_judged', 0)}",
        sums.get("broken", 0),
        sums.get("xjoin", 0),
    )


def _source_table(made: dict[str, Any]) -> list[str]:
    rows = [_line(name, sums) for name, sums in made["groups"].items()]
    if made["main"]["pages"] and made["main"]["pages"] != made["all"]["pages"]:
        rows.append(_line("real and self-made", made["main"]))
    rows.append(_line("all", made["all"]))
    if made["apart"]["pages"]:
        rows.append(_line("apart: answers missing", made["apart"]))
    return measure.table(("", *_HEAD), rows)


def _model_order_lines(done: dict[str, Any] | None) -> list[str]:
    """The lines that say the model's page order was on, and what it did."""
    if not done:
        return []
    refused = sum(done.get("other_steps_refused", {}).values())
    return [
        f"the model's page order was on: {done['pages']} pages asked, {done['failed']} of them "
        f"with no usable answer ({done['unknown_number']} named a number that is no item, "
        f"{done['repeated_number']} named one twice, {done['no_numbers']} named none, "
        f"{done.get('too_much_furniture', 0)} left out too much as furniture), "
        f"the verified answer laid over {done['applied_pages']} pages "
        f"({done['stale_pages']} pages changed in between and kept the rules); "
        f"{done['furniture']} lines left out as furniture, {done['left_out']} lines it did not "
        f"name, {done['bad_join']} blocks that held an equation, figure or table with other "
        f"items; ${done['cost_usd']:.4f} in {done['calls']} calls "
        f"(recorded prices; {done['unoffered_items']} items were beyond the list's length). "
        f"{refused} requests of other steps were refused, as with no model."
    ]


def render(record: dict[str, Any], verdict: Verdict | None = None) -> str:
    """An order record as the lines `readablestem eval order` prints."""
    made = record["metrics"]
    uploads, sources = made["uploads"], made["sources"]
    student = sources["html"]["all"]
    lines = [
        f"reading order: {uploads['measured']} files with gold, {student['pages']} gold pages, "
        f"scorer version {made['scorer']}",
        *measure.header(record),
    ]
    if uploads["answers_missing"]:
        owed = sum(uploads["answers_missing"].values())
        lines.append(
            f"apart, answers missing from the recording ({owed} in all): "
            + ", ".join(f"{name} ({n})" for name, n in uploads["answers_missing"].items())
        )
    if uploads["not_measured"]:
        lines.append("not measured: " + ", ".join(uploads["not_measured"]))
    lines += _model_order_lines(made.get("model_order"))
    with_ = student["pages_with"]
    lines += [
        "",
        f"{SAID['html']}: {student.get('right', 0)} of {student['pages']} pages right, "
        f"{student.get('faultless', 0)} faultless; {with_['furniture']} pages read furniture "
        f"aloud, {with_['cut']} cut a block, {with_['merged']} run blocks together. "
        f"{student.get('documents_right', 0)} of {student.get('documents', 0)} documents have "
        "every gold page right.",
        "Pages right is one count of several, and none of them sees a formula missing from "
        "a sentence. The page check's flag rate on these pages is not measured yet.",
    ]
    for source in SOURCES:
        lines += ["", f"{SAID[source]} (a count as pages, and places in brackets):"]
        lines += _source_table(sources[source])
    lines += ["", "the same pages by the counts of scorer version 4:", order.HEADER]
    for source in SOURCES:
        lines.append(order.table_row(source, Counter(sources[source]["all"])))
    wrong = [page for page in made["pages"] if "html" in page and not page["html"]["right"]]
    if not made.get("listed", True):
        lines += [
            "",
            "A held-out set: no page is named here or in the record, and a failing page is "
            "not looked at.",
        ]
    if wrong:
        lines += ["", f"the {len(wrong)} pages not right in the page a student is given:"]
        lines += measure.table(
            ("upload", "page", "group", "why", "in the elements"),
            [
                (
                    page["upload"] + (" (apart)" if page["apart"] else ""),
                    page["page"],
                    page["group"],
                    ", ".join(page["html"]["why"]),
                    "right" if page.get("elements", {}).get("right") else "not right",
                )
                for page in wrong
            ],
        )
    if verdict is not None:
        lines.append("")
        if not verdict.judged:
            lines.append(f"The bar was not judged: {verdict.why}.")
        elif verdict.met:
            lines.append("The bar is met: no page that was right is not right now.")
        else:
            lines.append(f"The bar is NOT met: {len(verdict.worse)} pages were right and are not:")
            lines += [f"  {name}" for name in verdict.worse]
        if verdict.judged and verdict.better:
            lines.append(f"{len(verdict.better)} pages became right: " + ", ".join(verdict.better))
    return "\n".join(lines)
