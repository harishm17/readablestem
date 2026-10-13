"""Runs the a11y-stem arm of a proof: live (record) or replay.

Both modes run the same eval code (`eval.pipeline_eval`, the tasks `document`,
`equations`, `headings`, `figures`) on the same gold set; they differ only in what stands
in for the outside world:

- model client: live uses the real `OpenAIVisionClient`, wrapped to record every answer;
  replay uses `ReplayClient`, which answers from the recording;
- second automated check: the real Decisions API, recorded; or the recorded verdicts;
- section PDFs: printed by headless Chromium; or the PDFs held in the bundle;
- equation and figure pictures: rendered or downloaded; or stand-ins that declare the
  recorded image identity;
- axe-core: run in Chromium and recorded; or the recorded result;
- threads: the production setting (4); or 1, so repeated requests are answered in order.

Settings are `Settings(_env_file=None)`: the defaults plus whatever the environment sets.
A replay refuses to run when the settings, the prompt versions or the pipeline version
differ from the recorded run (`check_identity`), because the recorded answers belong to
that code. After a replay, `compare_with_recorded` checks that every number still equals
the live run's: that is the check that the published numbers come from this checkout.

Nothing in here logs content. Dates come from the caller.
"""

from __future__ import annotations

import json
import math
import platform
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

from a11y_stem.config import Settings
from a11y_stem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from a11y_stem.eval import pipeline_eval as pe
from a11y_stem.eval.render import RenderError
from a11y_stem.models import make_vision_client
from a11y_stem.models.base import VisionClient
from a11y_stem.models.verifier import (
    EQUATION_VERSION,
    FIGURE_VERSION,
    any_check_enabled,
    make_verifier,
)
from a11y_stem.pipeline import html_checks, run
from a11y_stem.proof import goldinfo
from a11y_stem.proof.bundle import Bundle, BundleWriter, run_stem
from a11y_stem.proof.errors import BundleError, Incomplete, ProofError, ReplayMiss
from a11y_stem.proof.registry import ProofSet, sha256_file
from a11y_stem.proof.replay import (
    AnswerSet,
    AnswerStore,
    AxeRecorder,
    AxeReplay,
    MediaRecorder,
    RecordingClient,
    RecordingMedia,
    ReplayClient,
    ReplayMedia,
    patched_axe,
)

DEFAULT_MAX_CALLS = 5000
# The settings that decide what the pipeline produces; secrets and web settings are not
# part of the identity of a run.
IDENTITY_SETTINGS = (
    "main_model",
    "cheap_model",
    "auto_accept_confidence",
    "abstain_confidence",
    "decisions_model",
    "decisions_figure_verify",
    "decisions_figure_flag_below",
    "decisions_equation_shadow",
    "decisions_equation_flag_at",
    "max_pages_per_job",
    "model_max_retries",
    "feature_tables",
    "feature_long_descriptions",
    "feature_colour",
    "feature_notation_memory",
)
# Report keys that depend on the machine or on how the run was funded, not on the answers.
VOLATILE_COST_KEYS = ("budget_spent_usd", "budget_calls", "dropped_usd")
TASK_LABEL = {
    "document": "document",
    "equations": "equations",
    "headings": "headings",
    "figures": "figures",
}


def proof_settings() -> Settings:
    """Defaults plus the environment; never a `.env` file lying in the working directory."""
    return Settings(_env_file=None)  # type: ignore[call-arg]


def gold_label(pset: ProofSet) -> str:
    return f"proof set {pset.id}"


# ------------------------------------------------------------------------ identity


def code_identity(settings: Settings) -> dict[str, Any]:
    """What the recorded answers depend on: versions, models, the settings that matter."""
    from a11y_stem.eval.gate import code_versions

    values = {name: getattr(settings, name) for name in IDENTITY_SETTINGS}
    return {
        "pipeline_version": run.PIPELINE_VERSION,
        "prompts": dict(sorted(code_versions().items())),
        "second_check": {"figure": FIGURE_VERSION, "equation": EQUATION_VERSION},
        "settings": values,
    }


def check_identity(recorded: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Differences between the recorded code identity and this checkout's, as short
    sentences. Empty when the replay is for the code that made the recording."""
    out = []
    if recorded.get("pipeline_version") != current["pipeline_version"]:
        out.append(
            f"pipeline version {recorded.get('pipeline_version')} was recorded, "
            f"this checkout is {current['pipeline_version']}"
        )
    for group in ("prompts", "second_check"):
        was, now = recorded.get(group, {}), current[group]
        for key in sorted(set(was) | set(now)):
            if was.get(key) != now.get(key):
                out.append(f"{group} {key}: recorded {was.get(key)}, this checkout {now.get(key)}")
    was, now = recorded.get("settings", {}), current["settings"]
    for key in sorted(set(was) | set(now)):
        if was.get(key) != now.get(key):
            out.append(f"setting {key}: recorded {was.get(key)!r}, now {now.get(key)!r}")
    return out


def git_state(root: Path) -> dict[str, Any]:
    """Commit and cleanliness of the checkout; best effort ("unknown" outside git)."""

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", *args], cwd=root, capture_output=True, text=True, timeout=20, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain", "--untracked-files=no")
    return {"commit": commit or "unknown", "dirty": None if status is None else bool(status)}


def environment() -> dict[str, Any]:
    """Versions that can change a result; no host name, user name or path."""
    return {
        "python": ".".join(map(str, sys.version_info[:3])),
        "platform": f"{platform.system()} {platform.machine()}",
        "pymupdf": pymupdf.__version__,
        "cpus": _cpu_count(),
    }


def _cpu_count() -> int | None:
    import os

    return os.cpu_count()


# ------------------------------------------------------------------------- results


@dataclass
class RunOutcome:
    task: str
    repeat: int
    report: dict[str, Any]
    items: list[dict[str, Any]]
    replay: dict[str, Any] | None = None  # ReplayStats.as_dict() in a replay
    timing: list[dict[str, Any]] = field(default_factory=list)  # per section, live only
    wall_seconds: float | None = None

    @property
    def complete(self) -> bool:
        return bool(self.report.get("complete"))


@dataclass
class ArmResults:
    outcomes: list[RunOutcome] = field(default_factory=list)

    def runs(self, task: str) -> list[RunOutcome]:
        return [o for o in self.outcomes if o.task == task]

    def items(self, task: str) -> list[dict[str, Any]]:
        return [item for o in self.runs(task) for item in o.items]

    @property
    def complete(self) -> bool:
        return bool(self.outcomes) and all(o.complete for o in self.outcomes)


def _config(
    task: str,
    *,
    pset: ProofSet,
    settings: Settings,
    client: Any,
    media: Any,
    gold_dir: Path,
    date: str,
    label: str,
    concurrency: int,
    budget: JobBudget | None,
    gaps: frozenset[str],
) -> pe.EvalConfig:
    return pe.EvalConfig(
        task=task,
        settings=settings,
        client=client,
        media=media,
        date=date,
        gold_dir=gold_dir,
        client_label=label,
        gold_label=gold_label(pset),
        concurrency=concurrency,
        budget=budget,
        scorer_gaps=gaps if task in {"equations", "document"} else frozenset(),
    )


# ------------------------------------------------------------------------- replay


def replay_arm(
    pset: ProofSet,
    bundle: Bundle,
    gold_dir: Path,
    settings: Settings,
    *,
    allow_misses: bool = False,
    progress: Callable[[str], None] | None = None,
) -> ArmResults:
    """Run every recorded (task, repeat) again on the recorded answers."""
    say = progress or (lambda _msg: None)
    gaps = goldinfo.g1_ids(goldinfo.load_records(gold_dir))
    results = ArmResults()
    media = ReplayMedia(bundle)
    for task, repeat in pset.runs():
        answers = AnswerSet.load(bundle, task, repeat)
        client = ReplayClient(answers, second_check=any_check_enabled(settings))
        cfg = _config(
            task,
            pset=pset,
            settings=settings,
            client=client,
            media=media,
            gold_dir=gold_dir,
            date=bundle.recorded_on,
            label="replay",
            concurrency=1,
            budget=None,
            gaps=gaps,
        )
        say(f"replay {run_stem(task, repeat)}")
        try:
            if task == "document":
                with patched_axe(AxeReplay(answers)):
                    done = pe.run_pipeline_eval(cfg)
            else:
                done = pe.run_pipeline_eval(cfg)
        except RenderError as exc:
            raise BundleError(f"replay input could not be built: {exc}") from exc
        stats = answers.stats
        if stats.misses and not allow_misses:
            first = ", ".join(stats.misses[:3])
            raise ReplayMiss(
                f"{run_stem(task, repeat)}: {len(stats.misses)} request(s) have no recorded "
                f"answer (first: {first}). The code or the inputs differ from the recorded "
                "run; record a new one, or use the Docker image the run was made in"
            )
        results.outcomes.append(
            RunOutcome(task, repeat, done.report, done.items, replay=stats.as_dict())
        )
    return results


# --------------------------------------------------------------------------- live


def live_client(settings: Settings, budget: JobBudget, *, fake: bool) -> VisionClient:
    """The real client (or the offline one) with the second check on the same budget."""
    if fake:
        return make_vision_client(settings, budget, fake=True)
    from a11y_stem.models.openai_client import OpenAIVisionClient

    settings.require_openai_key()  # fail before any work, not on the first call
    verifier = make_verifier(settings, budget) if any_check_enabled(settings) else None
    return OpenAIVisionClient(settings, budget, verifier=verifier)


def refuse_unsafe_live(
    pset: ProofSet, *, fake: bool, signoff: bool, allow_dirty: bool, git: dict
) -> None:
    """Rules for a live run of a held-out set (preregistration sections 10 and 12)."""
    if not pset.held_out:
        return
    if fake:
        raise ProofError(
            "a fake run counts as a run (layout and headings are real code) and is not "
            "allowed on a held-out set; check the plumbing on a development set"
        )
    if pset.signoff and not signoff:
        items = "; ".join(pset.signoff)
        raise ProofError(
            f"this is a held-out set; pass --confirm-signoff once these are decided: {items}"
        )
    if git.get("dirty") and not allow_dirty:
        raise ProofError("the checkout has uncommitted changes; commit them, or pass --allow-dirty")


def prebuild_inputs(pset: ProofSet, media_factory: Callable[[], Any], records: list[dict]) -> None:
    """Build every input of every task before the first model call, as the eval does for
    one task: a failed print or download stops the proof with nothing spent."""
    for task in dict.fromkeys(pset.tasks):
        media = media_factory()
        try:
            pe.INPUTS[task](media, pe.select_units(task, records, None))
        finally:
            media.close()


def record_arm(
    pset: ProofSet,
    gold_dir: Path,
    settings: Settings,
    writer: BundleWriter,
    *,
    media_factory: Callable[[], Any],
    max_usd: float,
    max_calls: int = DEFAULT_MAX_CALLS,
    date: str,
    fake: bool = False,
    progress: Callable[[str], None] | None = None,
    client_factory: Callable[[Settings, JobBudget], VisionClient] | None = None,
) -> tuple[ArmResults, MediaRecorder, float]:
    """Run every (task, repeat) with the real models and record the answers into `writer`.

    One spend guard covers the whole proof; each run gets its own `JobBudget`, so a run's
    own spend is known. A run the budget stops is not scored half-converted: the proof
    stops with `Incomplete` and the partial bundle is left under `<dir>.partial`.
    """
    say = progress or (lambda _msg: None)
    records = goldinfo.load_records(gold_dir)
    gaps = goldinfo.g1_ids(records)
    recorder = MediaRecorder()
    guard = InMemorySpendGuard(cap_usd=max_usd)
    results = ArmResults()

    def recording_media() -> RecordingMedia:
        return RecordingMedia(media_factory(), recorder)

    say("building every input before the first model call")
    prebuild_inputs(pset, recording_media, records)
    for task, repeat in pset.runs():
        budget = JobBudget(guard, max_usd=max_usd, max_calls=max_calls)
        store = AnswerStore()
        inner = (
            client_factory(settings, budget)
            if client_factory is not None
            else live_client(settings, budget, fake=fake)
        )
        client = RecordingClient(inner, store)
        cfg = _config(
            task,
            pset=pset,
            settings=settings,
            client=client,
            media=recording_media(),
            gold_dir=gold_dir,
            date=date,
            label="fake" if fake else "openai",
            concurrency=pset.concurrency,
            budget=budget,
            gaps=gaps,
        )
        say(f"live {run_stem(task, repeat)}")
        started = time.monotonic()
        try:
            if task == "document":
                with patched_axe(AxeRecorder(store, html_checks.axe_checks)):
                    done = pe.run_pipeline_eval(cfg)
            else:
                done = pe.run_pipeline_eval(cfg)
        except BudgetExceeded as exc:
            raise Incomplete(f"{run_stem(task, repeat)}: stopped by the spend cap ({exc})") from exc
        wall = time.monotonic() - started
        writer.write_jsonl(f"answers/{run_stem(task, repeat)}.jsonl", store.rows())
        writer.write_json(f"reports/{run_stem(task, repeat)}.json", done.report)
        outcome = RunOutcome(
            task, repeat, done.report, done.items, timing=section_timing(task, done.items),
            wall_seconds=round(wall, 1),
        )  # fmt: skip
        results.outcomes.append(outcome)
        if not outcome.complete:
            raise Incomplete(
                f"{run_stem(task, repeat)} did not finish ({done.report.get('stopped_reason')}); "
                "raise --max-usd. A budget stop is never scored half-converted"
            )
    return results, recorder, guard.spent_usd()


def section_timing(task: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Wall-clock of each section's conversion (document task), kept in the bundle because
    a replay cannot measure it."""
    if task != "document":
        return []
    return [
        {
            "section": goldinfo.section_of_item_id(i["id"]),
            "pdf_pages": int(i.get("pdf_pages", 0)),
            "latency_ms": int(i.get("latency_ms", 0)),
        }
        for i in items
    ]


def pdf_entries(pset: ProofSet, recorder: MediaRecorder, writer: BundleWriter) -> list[dict]:
    """Write the printed PDFs into the bundle and describe them (hash, page count)."""
    entries = []
    from a11y_stem.proof.bundle import pdf_name

    for section in pset.sections:
        data = recorder.pdfs.get(section.id)
        if data is None:
            raise BundleError(f"no PDF was printed for {section.id}")
        rel = f"media/pdfs/{pdf_name(section.id)}"
        writer.write_bytes(rel, data)
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            pages = doc.page_count
        import hashlib

        entries.append(
            {
                "section": section.id,
                "file": rel,
                "sha256": hashlib.sha256(data).hexdigest(),
                "pages": pages,
            }
        )
    return entries


def run_entries(results: ArmResults) -> list[dict[str, Any]]:
    out = []
    for o in results.outcomes:
        cost = o.report.get("cost", {})
        out.append(
            {
                "task": o.task,
                "repeat": o.repeat,
                "complete": o.complete,
                "stopped_reason": o.report.get("stopped_reason"),
                "n": o.report.get("n"),
                "spend_usd": cost.get("budget_spent_usd", cost.get("total_usd")),
                "model_calls": cost.get("model_calls"),
                "wall_seconds": o.wall_seconds,
                "timing": o.timing,
                "report": f"reports/{run_stem(o.task, o.repeat)}.json",
                "answers": f"answers/{run_stem(o.task, o.repeat)}.jsonl",
            }
        )
    return out


# ------------------------------------------------------------------------ compare


def comparable(report: dict[str, Any]) -> dict[str, Any]:
    """The part of a report that a replay must reproduce: results and cost, not timing,
    not the label of the client, not the run-level funding."""
    cost = {k: v for k, v in report.get("cost", {}).items() if k not in VOLATILE_COST_KEYS}
    out = {
        "task": report.get("task"),
        "n": report.get("n"),
        "n_planned": report.get("n_planned"),
        "complete": report.get("complete"),
        "stopped_reason": report.get("stopped_reason"),
        "pipeline_version": report.get("pipeline_version"),
        "prompt_versions": report.get("prompt_versions"),
        "models": report.get("models"),
        "dataset": {
            k: v
            for k, v in report.get("dataset", {}).items()
            if k in {"manifest_sha256", "selection_sha256"}
        },
        "metrics": report.get("metrics"),
        "cost": cost,
    }
    return json.loads(json.dumps(out, sort_keys=True))  # tuples -> lists, as on disk


def _walk(a: Any, b: Any, path: str, out: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append(f"{path}.{key}: only in the {'replay' if key in b else 'recording'}")
            else:
                _walk(a[key], b[key], f"{path}.{key}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} entries recorded, {len(b)} replayed")
        for i, (x, y) in enumerate(zip(a, b, strict=False)):
            _walk(x, y, f"{path}[{i}]", out)
    elif isinstance(a, float) and isinstance(b, float):
        if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12):
            out.append(f"{path}: recorded {a!r}, replayed {b!r}")
    elif a != b:
        out.append(f"{path}: recorded {a!r}, replayed {b!r}")


def compare_with_recorded(bundle: Bundle, results: ArmResults) -> list[str]:
    """Every difference between the recorded reports and the replayed ones (empty list:
    the replay reproduces the recording exactly)."""
    problems: list[str] = []
    for outcome in results.outcomes:
        stem = run_stem(outcome.task, outcome.repeat)
        recorded = comparable(bundle.report(outcome.task, outcome.repeat))
        replayed = comparable(outcome.report)
        _walk(recorded, replayed, stem, problems)
    return problems


def pdf_hashes(bundle: Bundle) -> dict[str, str]:
    return {e["section"]: e["sha256"] for e in bundle.pdfs}


def verify_pdf_files(bundle: Bundle) -> list[str]:
    """The PDFs against the hashes in the manifest (the files' own check is `verify`)."""
    problems = []
    for entry in bundle.pdfs:
        path = bundle.path(entry["file"])
        if not path.is_file() or sha256_file(path) != entry["sha256"]:
            problems.append(f"{entry['section']}: PDF does not match its recorded hash")
    return problems
