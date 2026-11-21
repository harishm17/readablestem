"""The same file gives the same document on any machine.

A budget counts work, which is the same everywhere. A clock may only stop a step, and a step
that stops says so with a "not finished" fact (`core.types.NotFinished`). Three checks:

- every read of a clock in the conversion is one this file names, with what it is for;
- a step that leaves something out because its clock ran is named, unless it said it
  stopped;
- the whole corpus, replayed once as it is and once held back to a quarter of the time,
  gives the same documents (a `workflow` test: it converts every corpus file twice).
"""

from __future__ import annotations

import ast
import json
import os
import time
from pathlib import Path

import pymupdf
import pytest

from readablestem.cli.main import main
from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import Kind, NotFinished, StepFacts
from readablestem.eval import corpus, measure
from readablestem.eval.measure import Converted
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import page_type, run, tables
from readablestem.web.worker import default_client_factory

PACKAGE = Path(run.__file__).parents[1]
# The code a conversion runs. The model clients are apart: their clocks time a request, pace
# requests and wait for a batch, and a request that runs out of time fails its element with
# a reason on the fix.
CONVERSION = ("pipeline", "source")

# What a clock is read for.
TIMING = "timing: it is written in a field of seconds and decides nothing"
CACHE = "cache: when a kept object is forgotten; a forgotten one is made again the same"
SAYS_SO = "limit: it stops the step, which writes a 'not finished' fact"
ENDS_JOB = "limit: it ends the conversion with a failure code, so no document differs"
# The ink analysis is bounded per page by counts of work (plan WS14.9); its clock is only a
# guard against a hang, far above any honest page, and a page it stops writes a fact.
INK = "hang guard: per page, 40 times the slowest corpus page; it writes a 'not finished' fact"
# A clock all the same: a stop writes the fact, and only a slower core (not a busy machine)
# reaches it sooner. Evals and replays run without it (`TABLE_SEARCH_UNLIMITED`).
TABLE_SEARCH = "limit in the thread's CPU time: a stop writes a 'not finished' fact (WS5.14)"

# Every place the conversion reads a clock: (file, the function or class it is in) and what
# for. A new read fails the first test until it is named here, which is the
# moment to ask whether it can change what a document contains.
CLOCKS = {
    ("pipeline/run.py", "_run_step"): TIMING,
    ("pipeline/run.py", "_convert"): TIMING,
    ("pipeline/step_facts.py", "Stopwatch"): TIMING,
    ("pipeline/step_facts.py", "PageTrace"): TIMING,
    ("pipeline/layout/access.py", "_DocumentCache"): CACHE,
    ("pipeline/figure_images.py", "student_image"): CACHE,
    ("pipeline/image_page_reconcile.py", "reconcile"): SAYS_SO,
    ("pipeline/layout/figures.py", "_image_infos"): ENDS_JOB,
    ("pipeline/layout/figures.py", "_image_xrefs"): ENDS_JOB,
    ("pipeline/layout/figures.py", "_plain_image"): ENDS_JOB,
    ("pipeline/layout/figures.py", "_charge"): ENDS_JOB,
    ("pipeline/page_type.py", "InkBudget"): INK,
    ("pipeline/tables.py", "propose"): TABLE_SEARCH,
    ("pipeline/tables.py", "find_continued"): TABLE_SEARCH,
    ("source/convert.py", "_convert"): TIMING,
    ("source/convert.py", "_finish"): TIMING,
    ("source/latexml.py", "convert"): ENDS_JOB,
}
_CLOCK_NAMES = {
    "monotonic",
    "monotonic_ns",
    "perf_counter",
    "perf_counter_ns",
    "process_time",
    "process_time_ns",
    "thread_time",
    "thread_time_ns",
    "time",
    "time_ns",
}


def _clock_reads() -> set[tuple[str, str]]:
    found = set()
    for path in sorted(p for name in CONVERSION for p in (PACKAGE / name).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        named = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "time"
            for alias in node.names
            if alias.name in _CLOCK_NAMES
        }
        for top in tree.body:
            for node in ast.walk(top):
                reads = (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "time"
                    and node.attr in _CLOCK_NAMES
                ) or (isinstance(node, ast.Name) and node.id in named)
                if reads and not isinstance(top, ast.ImportFrom):
                    where = getattr(top, "name", "the module")
                    found.add((path.relative_to(PACKAGE).as_posix(), where))
    return found


def test_every_clock_the_conversion_reads_is_named_with_what_it_is_for() -> None:
    assert _clock_reads() == set(CLOCKS)


# A file made for the corpus (its answers are in the repository): two pages.
ENTRY = "s15_charts_figures"


def _converted(folder: Path, monkeypatch: pytest.MonkeyPatch, clock_s: float) -> Converted:
    """The file converted with a step planted in it that leaves the last page out once its
    clock has passed one second."""
    real = run.layout.analyze

    def analyze(*args, **kwargs):
        doc = real(*args, **kwargs)
        if clock_s > 1.0:
            doc.elements = [e for e in doc.elements if e.page != doc.pages[-1]]
        return doc

    monkeypatch.setattr(run.layout, "analyze", analyze)
    settings = measure.replay_settings(measure.answers_file(folder))
    budget = JobBudget(InMemorySpendGuard(cap_usd=10.0), max_usd=10.0, max_calls=500)
    output = run.convert_pdf(
        corpus.document(corpus.load_manifest()[ENTRY]),
        settings=settings,
        client=default_client_factory(settings, budget),
    )
    assert output.facts is not None
    return Converted(ENTRY, facts=output.facts, parts=measure.parts(output.result))


# A replay of a corpus file: the workflow job has the table model its answers were made with.
@pytest.mark.workflow
def test_a_page_a_slow_machine_leaves_out_is_named_unless_the_step_says_it_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if reason := measure.cannot_replay(ENTRY):
        if os.environ.get("CI"):
            raise RuntimeError(reason)
        pytest.skip(f"this machine cannot replay the corpus: {reason}")
    quick = _converted(tmp_path, monkeypatch, clock_s=0.3)
    slow = _converted(tmp_path, monkeypatch, clock_s=1.2)  # the same work at a quarter speed

    changed = measure.unexplained(quick, slow)

    assert changed and changed[0] == "elements"  # the earliest part that differs is named
    said = slow.facts.model_copy(
        update={"not_finished": [NotFinished(step="layout", page=1, reason="time_limit", of=1)]}
    )
    assert measure.unexplained(quick, Converted(ENTRY, facts=said, parts=slow.parts)) == []


def test_a_report_of_a_replay_names_the_step_a_clock_stopped(tmp_path, capsys) -> None:
    # A step a clock stopped gives another result on a busier machine, so a report made from
    # that run is no baseline: it says which step, in its record and in its text.
    said = StepFacts(
        not_finished=[NotFinished(step="page_type", page=3, reason="time_limit", of=1)]
    )
    run = [Converted(ENTRY, pages=4, kinds=["typical"] * 4, decisions=[], facts=said)]
    records, out = tmp_path / "records.json", tmp_path / "burden.json"
    records.write_text(json.dumps({"runs": [[done.as_dict() for done in run]]}))

    assert main(["eval", "burden", "--from", str(records), "--out", str(out)]) == 0

    assert json.loads(out.read_text())["not_finished"] == [
        {"upload": ENTRY, "step": "page_type", "page": 3, "reason": "time_limit"}
    ]
    assert f"{ENTRY} page_type page 4 (time_limit)" in capsys.readouterr().out


def test_a_page_typed_without_its_ink_because_the_guard_fired_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Before the budget was counted in work this happened to one handwritten upload of the
    # corpus at a quarter of the speed: its fourth page was no longer told to be handwriting,
    # and nothing said why. Now only the clock guard of a hang can stop a page.
    monkeypatch.setattr(page_type, "INK_GUARD_S", 0.0)
    written = Path(__file__).parent / "fixtures" / "handwriting" / "ink_no_layer.pdf"

    output = run.convert_pdf(
        written.read_bytes(),
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        client=FakeVisionClient(),
    )

    assert output.facts is not None
    assert output.facts.not_finished == [
        NotFinished(step="page_type", page=0, reason="time_limit", of=1)
    ]


# Two slides of authored tables: the layout model's search finds the first one's table, and
# nothing else on the page proposes it.
SLIDES = PACKAGE.parents[1] / "data" / "authored" / "tables" / "slides" / "slides.pdf"


def test_a_table_search_the_time_limit_stops_says_so_and_a_sleep_does_not_stop_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    def convert(**update: bool):
        output = run.convert_pdf(
            SLIDES.read_bytes(),
            settings=settings.model_copy(update=update),
            client=FakeVisionClient(),
        )
        assert output.facts is not None
        return output

    unlimited = convert(table_search_unlimited=True)
    assert any(e.kind is Kind.TABLE and e.page == 0 for e in unlimited.result.elements)
    assert unlimited.facts is not None and unlimited.facts.not_finished == []

    # The limit is CPU time: a search that sleeps 2 s spends none of it, so a limit a wall
    # clock would pass on the first page (1.5 s, none of it free) changes nothing.
    find_tables = pymupdf.Page.find_tables

    def asleep(page: pymupdf.Page, *args, **kwargs):
        time.sleep(2.0)
        return find_tables(page, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "find_tables", asleep)
    monkeypatch.setattr(tables, "DETECTOR_FREE_S", 0.0)
    monkeypatch.setattr(tables, "DETECTOR_EXCESS_S", 1.5)
    slept = convert()
    assert slept.facts is not None and slept.facts.not_finished == []
    assert measure.parts(slept.result) == measure.parts(unlimited.result)

    monkeypatch.setattr(tables, "DETECTOR_EXCESS_S", 0.0)  # none left: no page is searched
    stopped = convert()

    assert stopped.facts is not None
    assert stopped.facts.not_finished == [
        NotFinished(step="table_search", page=page, reason="time_limit", of=1) for page in (0, 1)
    ]
    said = [n.page for n in stopped.result.notices if n.code == "tables_not_searched"]
    assert said == [0, 1]
    assert not any(e.kind is Kind.TABLE and e.page == 0 for e in stopped.result.elements)


# Uploads a slower machine converts differently today, with the plan item that ends it. One
# of these may differ and need not; any other upload must give the same document.
KNOWN: dict[str, str] = {}
# A quarter of the time: the share of a core the smallest hosting plan gives a job.
SHARE = 0.25


@pytest.mark.workflow
def test_the_corpus_held_back_to_a_quarter_of_the_time_gives_the_same_documents() -> None:
    uploads = [u for u in measure.every_upload() if not measure.cannot_replay(u.entry)]
    if not uploads:
        if os.environ.get("CI"):
            raise RuntimeError("no corpus file can be replayed here")
        pytest.skip("this machine cannot replay the corpus")
    at_once = max(1, (os.cpu_count() or 2) // 2)
    quick = measure.run(uploads, at_once=at_once)
    slow = measure.run(uploads, at_once=at_once, share=SHARE)

    ended = {done.upload: done.error for done in [*quick, *slow] if done.error}
    assert set(ended.values()) <= {"not_fetched"}, ended
    assert sum(1 for done in quick if done.parts) >= len(uploads) // 2  # it compared something
    # ... and against a run that really was slower: holding back a process that does not do
    # the work compares two runs at full speed and proves nothing.
    took = {name: sum(done.wall_s for done in runs) for name, runs in (("q", quick), ("s", slow))}
    assert took["s"] >= 2 * took["q"], took
    changed = {
        a.upload: parts
        for a, b in zip(quick, slow, strict=True)
        if a.upload not in KNOWN and (parts := measure.unexplained(a, b))
    }
    assert changed == {}, "a slower machine converts these differently and no step says why"
