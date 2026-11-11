"""One measured replay of the corpus: what each document took of the models and of the clock.

`readablestem eval cost` (`eval.cost`) and `readablestem eval speed` (`eval.speed`) are both
made from it. Each upload of the corpus (`corpus.UPLOADS`) is converted the way the web
worker converts a job: the production profile's behaviour settings (`ops.profile`), the job's
caps (`web.worker.job_caps`), the conversion in a process of its own
(`web.isolated_convert`), and the corpus's recorded answers in front of the model client, so
nothing is sent and nothing is spent. A request the recording does not hold is refused and
counted (`Converted.missed`); the conversion then goes on as it would after a refused call.

Every upload is measured in a fresh Python process (`python -m readablestem.eval.measure`),
one at a time unless asked otherwise, so that

- the highest memory use read back is that upload's own (the operating system keeps one high
  mark per process and one for all the children it waited for);
- starting the conversion is in the time, as it is for a job;
- one upload's caches cannot make the next one look faster.

What is measured is content-free: page kinds, step facts (`pipeline.step_facts`), seconds,
megabytes and dollars, and a hash of each part of the result with its timing left out
(`parts`).

The same replay answers a second question: does a slower machine give the same document?
`run(..., share=0.25)` holds every conversion back to a quarter of the time, and
`unexplained` names what then came out different without a step saying it stopped early
(tests/test_determinism.py).

A page has one kind, the first of these that fits, so that the dollars and the seconds of
the kinds add up to the document's:

- `scan`: a scanned or handwritten page (read from its picture when the upload asked for
  that, kept as its picture when it did not);
- `source`: a unit of a document read from its LaTeX source;
- `slide`: a page of slide shape;
- `dense_math`: `DENSE_MATH` equations or more;
- `table`, then `figure`: a page with one;
- `typical`: any other page.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from readablestem.core.types import DocumentResult, Kind, StepFacts
from readablestem.eval import corpus
from readablestem.pipeline import step_facts

if TYPE_CHECKING:
    from readablestem.config import Settings
    from readablestem.core.types import ConversionOptions

KINDS = ("scan", "source", "slide", "dense_math", "table", "figure", "typical")
# A page with this many equations is a page of a derivation or of a worked solution. On the
# corpus 34 of 338 pages have that many (2026-11-11; `readablestem eval cost` prints the
# count of pages of each kind).
DENSE_MATH = 9
_SCAN_PAGE = ("scan_no_text", "scan_ocr", "handwritten", "handwritten_ocr")
# The largest corpus file is 39 pages and under 8 MB, and a replay spends nothing: the limits
# on size and on a day's spending are not what is measured.
ROOM = {
    "max_pages_per_job": 60,
    "max_source_pages": 60,
    "max_upload_mb": 25,
    "max_large_upload_mb": 25,
}
# How long one upload's process may run before it is stopped and counted as failed.
LIMIT_S = 1800.0
# The fields of a result that say how long something took. Two conversions of one file
# agree on everything else (`parts`).
_TIMING = frozenset({"latency_ms", "seconds"})


@dataclass(frozen=True)
class Upload:
    """One way a corpus file is uploaded: the file and the boxes ticked."""

    entry: str
    image_pages: bool = False
    exam: bool = False

    @property
    def name(self) -> str:
        ticked = [box for box, on in (("image_pages", self.image_pages), ("exam", self.exam)) if on]
        return "+".join([self.entry, *ticked])


@dataclass(frozen=True)
class Converted:
    """What one upload took. `error` names why there is nothing else (a code, never text)."""

    upload: str
    pages: int = 0
    kinds: list[str] = field(default_factory=list)  # one per page, in page order
    facts: StepFacts = field(default_factory=StepFacts)  # with `start_up` first
    usd: float = 0.0  # the result's dollars
    calls: int = 0  # the result's call count
    missed: int = 0  # requests the recording did not hold
    wall_s: float = 0.0  # the conversion call, start-up included
    cpu_s: float = 0.0  # CPU seconds of the conversion process and of what it started
    own_cpu_s: float = 0.0  # CPU seconds of the process that called it (the worker's part)
    peak_mb: float = 0.0  # the most memory the conversion process held
    own_peak_mb: float = 0.0  # the most the calling process held
    error: str = ""
    # A hash of each part of the result with its timing left out (`parts`).
    parts: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"facts": self.facts.model_dump(mode="json")}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Converted:
        return cls(**(raw | {"facts": StepFacts.model_validate(raw.get("facts") or {})}))


def every_upload() -> list[Upload]:
    """Each upload of the corpus, in the manifest's order."""
    return [
        Upload(entry_id, **ticked)
        for entry_id in corpus.load_manifest()
        for ticked in corpus.uploads(entry_id)
    ]


def page_kinds(result: DocumentResult, facts: StepFacts) -> list[str]:
    """The kind of each page of `result` (module docstring), in page order."""
    from readablestem.pipeline.slides import slide_shaped

    pages = result.stats.pages
    from_source = any(fact.stage == "source" for fact in facts.stages)
    scans = {t.page for t in result.page_types if t.kind in _SCAN_PAGE} | set(
        result.image_only_pages
    )
    found: dict[int, Counter[str]] = {page: Counter() for page in range(pages)}
    for element in result.elements:
        if element.page in found:
            found[element.page][element.kind] += 1
    kinds = []
    for page in range(pages):
        size = result.page_sizes.get(page)
        if page in scans:
            kinds.append("scan")
        elif from_source:
            kinds.append("source")
        elif size is not None and slide_shaped(*size):
            kinds.append("slide")
        elif found[page][Kind.EQUATION] >= DENSE_MATH:
            kinds.append("dense_math")
        elif found[page][Kind.TABLE]:
            kinds.append("table")
        elif found[page][Kind.FIGURE]:
            kinds.append("figure")
        else:
            kinds.append("typical")
    return kinds


def _timeless(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _timeless(item) for key, item in value.items() if key not in _TIMING}
    if isinstance(value, list):
        return [_timeless(item) for item in value]
    return value


def parts(result: DocumentResult) -> dict[str, str]:
    """A hash of each part of a result, in the order a conversion makes them, with the
    fields that hold seconds left out: what two conversions of one file must agree on,
    whatever machine each ran on."""
    raw = _timeless(result.model_dump(mode="json"))
    fixes = raw.pop("fixes")
    split = {
        "page types": [raw.pop(name, None) for name in ("page_types", "image_only_pages")],
        "elements": raw.pop("elements"),
        **{
            f"{kind} fixes": [fix for fix in fixes if fix["kind"] == kind]
            for kind in sorted({fix["kind"] for fix in fixes})
        },
        "html": raw.pop("html"),
        "the rest": raw,
    }
    return {
        name: hashlib.sha256(json.dumps(part, sort_keys=True).encode()).hexdigest()[:16]
        for name, part in split.items()
    }


def differing(one: Converted, other: Converted) -> list[str]:
    """The parts of the result two conversions of one upload disagree on, the earliest
    made first; "error" when one of them ended differently."""
    if one.error != other.error:
        return ["error"]
    names = [*one.parts, *(name for name in other.parts if name not in one.parts)]
    return [name for name in names if one.parts.get(name) != other.parts.get(name)]


def unexplained(quick: Converted, slow: Converted) -> list[str]:
    """What the slower of two conversions of one upload changed without saying so: the
    parts that differ (`differing`), unless a step of the slower one says it stopped early
    with a "not finished" fact the quicker one does not have."""
    said = [stop for stop in slow.facts.not_finished if stop not in quick.facts.not_finished]
    return [] if said else differing(quick, slow)


def replay_settings(answers: Path) -> Settings:
    """Production's behaviour settings, with the recorded answers in place of the provider."""
    from readablestem.config import Settings
    from readablestem.ops import profile

    base = Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        model_answers_file=answers,
        model_answers_offline=True,
        # A replay sends nothing, so any key will do.
        anthropic_api_key="sk-ant-replay-sends-nothing",
        **ROOM,
    )
    return profile.load().apply(base)


def answers_file(folder: Path) -> Path:
    """The recorded answers as the one plain file the client reads: the committed ones and,
    where this machine has them, those kept out of the repository (`corpus.KEPT_OUT`)."""
    from readablestem.proof.bundle import dump_line

    path = folder / "answers.jsonl"
    rows = corpus.read_answers() + corpus.read_answers(corpus.KEPT_OUT)
    path.write_text("".join(dump_line(row) + "\n" for row in rows), encoding="utf-8")
    return path


def cannot_replay(entry_id: str) -> str:
    """Why this machine cannot replay the file's conversion, or "" when it can."""
    from readablestem.pipeline import table_model

    entry = corpus.load_manifest()[entry_id]
    if not (entry.may_commit or corpus.KEPT_OUT.is_file()):
        return "answers_kept_out"  # its license keeps its answers out of the repository
    if not table_model.model_path().is_file():
        return "no_table_model"  # the answers were recorded with it: make table-model
    return ""


def _not_fetched(url: str, size: int) -> bytes:
    raise corpus.CorpusError("not fetched")


@dataclass(frozen=True)
class _Job:
    """One upload as a job gets it: the file, its page count, the job's client (under the
    job's caps) and its options."""

    pdf: bytes
    pages: int
    client: Any
    options: ConversionOptions


def _job(upload: Upload, settings: Settings) -> _Job | str:
    """The job of one upload, or the code of why this machine cannot replay it."""
    import pymupdf

    from readablestem.core.budget import InMemorySpendGuard, JobBudget
    from readablestem.core.types import ConversionOptions
    from readablestem.web.worker import default_client_factory, job_caps

    if reason := cannot_replay(upload.entry):
        return reason
    try:
        pdf = corpus.document(corpus.load_manifest()[upload.entry], get=_not_fetched)
    except corpus.CorpusError:
        return "not_fetched"
    with pymupdf.open(stream=pdf, filetype="pdf") as opened:
        page_count = opened.page_count
    image_pages = upload.image_pages and settings.feature_image_pages
    max_usd, max_calls = job_caps(settings, page_count, image_pages)
    budget = JobBudget(InMemorySpendGuard(cap_usd=1000.0), max_usd=max_usd, max_calls=max_calls)
    options = ConversionOptions.from_settings(settings).model_copy(
        update={"exam_mode": upload.exam, "image_pages": image_pages}
    )
    return _Job(pdf, page_count, default_client_factory(settings, budget), options)


def result_of(upload: Upload, settings: Settings) -> tuple[bytes, DocumentResult] | str:
    """One upload's file and the document a job makes of it (converted as `convert`
    converts it, without the measuring), or the code of why there is none."""
    from readablestem.web.isolated_convert import ConversionFailed, convert_isolated

    job = _job(upload, settings)
    if isinstance(job, str):
        return job
    try:
        output = convert_isolated(
            job.pdf,
            settings=settings,
            client=job.client,
            options=job.options,
            concurrency=settings.model_concurrency,
        )
    except ConversionFailed as exc:
        return str(exc.code)
    return job.pdf, output.result


def convert(upload: Upload, settings: Settings) -> Converted:
    """Convert one upload in a conversion process, as a job is converted, and measure it."""
    from readablestem.web.isolated_convert import ConversionFailed, convert_isolated

    job = _job(upload, settings)
    if isinstance(job, str):
        return Converted(upload.name, error=job)
    pdf, page_count, client, options = job.pdf, job.pages, job.client, job.options
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    own_before = time.process_time()
    started = time.monotonic()
    try:
        output = convert_isolated(
            pdf,
            settings=settings,
            client=client,
            options=options,
            concurrency=settings.model_concurrency,
        )
    except ConversionFailed as exc:
        return Converted(upload.name, pages=page_count, error=str(exc.code))
    wall_s = time.monotonic() - started
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    readers = [client, getattr(client, "second_check", None)]
    facts = output.facts or StepFacts()
    facts = facts.model_copy(update={"stages": [step_facts.start_up(facts, wall_s), *facts.stages]})
    result = output.result
    return Converted(
        upload.name,
        pages=result.stats.pages,
        kinds=page_kinds(result, facts),
        facts=facts,
        usd=result.stats.cost_usd,
        calls=result.stats.model_calls,
        missed=sum(sum(r.missed_because.values()) for r in readers if r is not None),
        wall_s=round(wall_s, 4),
        cpu_s=round(
            after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
            4,
        ),
        own_cpu_s=round(time.process_time() - own_before, 4),
        # Linux counts both in KB.
        peak_mb=round(after.ru_maxrss / 1024, 1),
        own_peak_mb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        parts=parts(result),
    )


def _children(pid: int) -> list[int] | None:
    """The processes `pid` started, from the kernel's own list (one file for each of its
    threads). None where the kernel keeps no such list."""
    found: list[int] = []
    try:
        threads = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return found  # it has ended
    for thread in threads:
        try:
            listed = Path(f"/proc/{pid}/task/{thread}/children").read_text(encoding="ascii")
        except FileNotFoundError:
            if Path(f"/proc/{pid}/task/{thread}").is_dir():
                return None  # the thread is there and the list is not: no such list here
            continue
        except OSError:
            continue
        found.extend(int(one) for one in listed.split())
    return found


def _started_under(pid: int) -> list[int]:
    """`pid` and every process started under it, read from /proc."""
    found = [pid]
    for one in found:
        below = _children(one)
        if below is None:
            return _started_under_by_parent(pid)
        found.extend(below)
    return found


def _started_under_by_parent(pid: int) -> list[int]:
    """The same from every process's parent: slow (it reads all of /proc), for a kernel
    without the lists of children."""
    children: dict[int, list[int]] = {}
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            # "pid (name) state parent ...": the name may hold spaces and brackets.
            text = stat.read_text(encoding="utf-8", errors="replace")
            parent = int(text.rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue  # it ended while it was being read
        children.setdefault(parent, []).append(int(stat.parent.name))
    found = [pid]
    for one in found:
        found.extend(children.get(one, []))
    return found


def _tell(pids: Iterable[int], what: signal.Signals) -> None:
    for pid in pids:
        with contextlib.suppress(OSError):  # it has ended
            os.kill(pid, what)


def _held_back(process: subprocess.Popen[bytes], share: float) -> int | None:
    """Wait for `process` while it and everything it started run for only `share` of the
    time: they are stopped and continued ten times a second. To every clock the conversion
    reads, that is a machine with that share of this one to give. Returns the exit status,
    or None when `LIMIT_S` passed (everything is then ended)."""
    period = 0.1
    deadline = time.monotonic() + LIMIT_S
    stopped: list[int] = []
    try:
        while process.poll() is None:
            if time.monotonic() > deadline:
                _tell(_started_under(process.pid), signal.SIGKILL)
                return None
            began = time.monotonic()
            time.sleep(period * share)
            stopped = _started_under(process.pid)
            _tell(stopped, signal.SIGSTOP)
            # Finding and stopping them took time in which they ran: the pause is sized
            # from the time they really had, so the share holds whatever that cost.
            ran = time.monotonic() - began
            time.sleep(ran * (1 - share) / share)
            _tell(stopped, signal.SIGCONT)
            stopped = []
    finally:
        _tell(stopped, signal.SIGCONT)  # never leave anything stopped behind
    return process.returncode


def _in_a_process(
    upload: Upload, answers: Path, folder: Path, cores: str | None, share: float | None
) -> Converted:
    out = folder / f"{upload.name}.json"
    command = [sys.executable, "-m", "readablestem.eval.measure", upload.entry]
    command += ["--answers", str(answers), "--out", str(out)]
    command += ["--image-pages"] if upload.image_pages else []
    command += ["--exam"] if upload.exam else []
    if cores:
        command = ["taskset", "--cpu-list", cores, *command]
    quiet: Any = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    ended: int | None
    if share is None:
        try:
            ended = subprocess.run(command, timeout=LIMIT_S, **quiet).returncode
        except subprocess.TimeoutExpired:
            ended = None
    else:
        with subprocess.Popen(command, **quiet) as process:
            ended = _held_back(process, share)
    if ended is None:
        return Converted(upload.name, error="timeout")
    if ended != 0 or not out.is_file():
        return Converted(upload.name, error="process_failed")
    return Converted.from_dict(json.loads(out.read_text(encoding="utf-8")))


def run(
    uploads: Sequence[Upload],
    *,
    at_once: int = 1,
    cores: str | None = None,
    share: float | None = None,
    said: Callable[[Converted], None] | None = None,
) -> list[Converted]:
    """Measure each upload in a process of its own, `at_once` of them at a time (1 for
    seconds that mean anything), pinned to the CPU list `cores` when given. `said` is
    called as each one ends.

    `share` (0.25 for a quarter) holds each conversion back to that share of the time, as
    a much slower machine would: its seconds then mean nothing, and its result must be the
    one a quick machine gives (`unexplained`)."""
    with tempfile.TemporaryDirectory(prefix="readablestem-measure-") as name:
        folder = Path(name)
        answers = answers_file(folder)

        def one(upload: Upload) -> Converted:
            done = _in_a_process(upload, answers, folder, cores, share)
            if said is not None:
                said(done)
            return done

        with ThreadPoolExecutor(max_workers=max(1, at_once)) as pool:
            return list(pool.map(one, uploads))


def machine() -> dict[str, Any]:
    """What the seconds were measured on: the CPU's model name and how many cores this
    process may use. Two records compare only when both are the same."""
    model = ""
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {"cpu": model, "cores": len(os.sched_getaffinity(0))}


def record(
    measured: Iterable[Converted], settings: Settings, metrics: dict[str, Any]
) -> dict[str, Any]:
    """A report of a measured replay: what it says about itself, then its figures under
    `metrics`, the shape `readablestem eval bar` reads."""
    return stamps(measured, settings) | {"metrics": metrics}


def header(record: dict[str, Any]) -> list[str]:
    """The lines a printed report says about itself."""
    from readablestem.eval import split
    from readablestem.ops import profile

    chosen = record.get("split") or {}
    lines = [
        f"Set: the corpus, a {chosen.get('name')} set of {chosen.get('documents')} documents "
        f"({str(chosen.get('set_sha256'))[:12]}). Pipeline {record.get('pipeline_version')}, "
        f"commit {record.get('commit')}.",
        profile.stamp_line(record.get("profile")),
    ]
    if chosen.get("name") != split.HELD_OUT:
        lines.append(
            "Proof owed: a replay of held-out whole documents. No such recording exists, so "
            "these figures meet no bar and set no public claim."
        )
    return lines


def stamps(measured: Iterable[Converted], settings: Settings) -> dict[str, Any]:
    """What a report of a measured replay says about itself (`eval.split`): the documents and
    their side, the settings, and the code."""
    from readablestem.eval import run_record, split
    from readablestem.ops import profile
    from readablestem.pipeline.run import PIPELINE_VERSION

    manifest = corpus.load_manifest()
    names = sorted({done.upload.split("+")[0] for done in measured})
    hashes = {name: manifest[name].sha256 or manifest[name].builder for name in names}
    # Every corpus file has been read while the readers were built.
    chosen = split.Split(seed="corpus", development=tuple(names), held_out=(), hashes=hashes)
    sha, dirty = run_record.source_state(corpus.REPO)
    return {
        "split": chosen.stamp(split.DEVELOPMENT),
        "profile": profile.stamp(settings),
        "pipeline_version": PIPELINE_VERSION,
        "commit": sha + ("+changes" if dirty else ""),
        "machine": machine(),
    }


def main(argv: list[str] | None = None) -> int:
    """Measure one upload and write it as JSON (the process `run` starts)."""
    parser = argparse.ArgumentParser(prog="python -m readablestem.eval.measure")
    parser.add_argument("entry")
    parser.add_argument("--image-pages", action="store_true")
    parser.add_argument("--exam", action="store_true")
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    upload = Upload(args.entry, image_pages=args.image_pages, exam=args.exam)
    done = convert(upload, replay_settings(args.answers))
    args.out.write_text(json.dumps(done.as_dict()), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
