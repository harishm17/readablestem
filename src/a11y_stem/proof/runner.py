"""Runs the a11y-stem arm of a proof: live (record) or replay.

Both modes run the same eval code (`eval.pipeline_eval`, the tasks `document`,
`equations`, `headings`, `figures`) on the same gold set; they differ only in what stands
in for the outside world:

- model client: live uses the real client of the chosen provider (`OpenAIVisionClient`, or
  `AnthropicVisionClient` with `--provider anthropic`), wrapped to record every answer;
  replay uses `ReplayClient`, which answers from the recording;
- second automated check: the real Decisions API, recorded; or the recorded verdicts;
- section PDFs: printed by headless Chromium; or the PDFs held in the bundle;
- equation and figure pictures: rendered or downloaded; or stand-ins that declare the
  recorded image identity;
- axe-core: run in Chromium and recorded; or the recorded result;
- threads: the production setting (4); or 1, so repeated requests are answered in order.

Settings are `Settings(_env_file=None)`: the defaults plus whatever the environment sets,
for the OpenAI provider unless a run asks for another (`proof_settings`); a replay takes
the provider and the model ids from the recording (`recorded_settings`).
A replay refuses to run when the settings, the prompt versions or the pipeline version
differ from the recorded run (`check_identity`), because the recorded answers belong to
that code. After a replay, `compare_with_recorded` checks that every number still equals
the live run's: that is the check that the published numbers come from this checkout.

Nothing in here logs content. Dates come from the caller.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
import platform
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

from a11y_stem.config import Provider, Settings, with_provider
from a11y_stem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from a11y_stem.eval import pipeline_eval as pe
from a11y_stem.eval.render import RenderError
from a11y_stem.models import make_live_client, make_vision_client
from a11y_stem.models.base import VisionClient
from a11y_stem.models.verifier import (
    EQUATION_VERSION,
    FIGURE_VERSION,
    any_check_enabled,
)
from a11y_stem.observability.logging import FIELDS_ATTR
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


ANTHROPIC_IDENTITY_SETTINGS = (
    "model_provider",
    "anthropic_effort_main",
    "anthropic_effort_cheap",
    "anthropic_refusal_fallback",
)


def proof_settings(
    provider: Provider = "openai",
    *,
    main_model: str | None = None,
    cheap_model: str | None = None,
) -> Settings:
    """Defaults plus the environment; never a `.env` file lying in the working directory.

    The provider is the OpenAI one unless the run names another: the MODEL_PROVIDER
    environment variable does not change what a proof is recorded or replayed with.
    `main_model` and `cheap_model` name models of `provider`.
    """
    base = Settings(_env_file=None)  # type: ignore[call-arg]
    if provider == "openai" and main_model is None and cheap_model is None:
        # The environment may set another provider; a proof does not follow it.
        return base if base.model_provider == "openai" else with_provider(base, "openai")
    return with_provider(base, provider, main_model=main_model, cheap_model=cheap_model)


def recorded_settings(recorded: dict[str, Any]) -> Settings:
    """The settings a recording was made with, as far as they pick the models: the OpenAI
    defaults for a recording that names no provider (every recording made before the
    Anthropic provider existed), else the recorded provider and model ids."""
    provider = recorded.get("model_provider", "openai")
    if provider == "openai":
        return proof_settings()
    base = proof_settings(
        provider,
        main_model=recorded.get("main_model"),
        cheap_model=recorded.get("cheap_model"),
    )
    values = {k: recorded[k] for k in ANTHROPIC_IDENTITY_SETTINGS[1:] if k in recorded}
    return base.model_copy(update=values)


def gold_label(pset: ProofSet) -> str:
    return f"proof set {pset.id}"


# ------------------------------------------------------------------------ identity


def code_identity(settings: Settings) -> dict[str, Any]:
    """What the recorded answers depend on: versions, models, the settings that matter."""
    from a11y_stem.eval.gate import code_versions

    names = IDENTITY_SETTINGS
    if settings.model_provider != "openai":
        # Only a run on another provider records these, so that a recording made on the
        # OpenAI provider keeps exactly the identity it was made with.
        names = (*names, *ANTHROPIC_IDENTITY_SETTINGS)
    values = {name: getattr(settings, name) for name in names}
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
    # The HTML a11y-stem wrote for each section, rebuilt by a replay: (repeat, section) ->
    # HTML. The invented-content rate and the comparison read it; it is not stored in the
    # bundle because the replay makes it again from the recorded answers.
    html: dict[tuple[int, str], str] = field(default_factory=dict)
    # Why a live run ended before every (task, repeat) was made (the spend cap), when the
    # finished runs were kept (`record_arm(keep_finished=True)`); None for a full run.
    stopped: str | None = None
    resumed: list[str] = field(default_factory=list)  # runs read back after an interruption

    def runs(self, task: str) -> list[RunOutcome]:
        return [o for o in self.outcomes if o.task == task]

    def items(self, task: str) -> list[dict[str, Any]]:
        return [item for o in self.runs(task) for item in o.items]

    @property
    def complete(self) -> bool:
        return (
            bool(self.outcomes) and self.stopped is None and all(o.complete for o in self.outcomes)
        )


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
    section_workers: int = 1,
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
        section_workers=section_workers,
        budget=budget,
        scorer_gaps=gaps if task in {"equations", "document"} else frozenset(),
    )


# ------------------------------------------------------------------------- replay


@contextmanager
def capturing_html(
    bundle: Bundle, repeat: int, sink: dict[tuple[int, str], str]
) -> Generator[None, None, None]:
    """Keep the HTML `convert_pdf` writes for each section PDF of the bundle (found by the
    SHA-256 of the PDF it was given). Nothing about the conversion changes."""
    section_of = {e["sha256"]: e["section"] for e in bundle.pdfs}
    original = pe.run.convert_pdf

    def wrapper(pdf: bytes, *args: Any, **kwargs: Any) -> Any:
        output = original(pdf, *args, **kwargs)
        section = section_of.get(hashlib.sha256(pdf).hexdigest())
        if section is not None:
            sink[(repeat, section)] = output.result.html
        return output

    pe.run.convert_pdf = wrapper  # ty: ignore[invalid-assignment]
    try:
        yield
    finally:
        pe.run.convert_pdf = original


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
    recorded = [(str(r["task"]), int(r["repeat"])) for r in bundle.runs]
    for task, repeat in recorded or pset.runs():
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
                with patched_axe(AxeReplay(answers)), capturing_html(bundle, repeat, results.html):
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
    """The real client of the settings' provider (or the offline one). With the OpenAI
    provider it carries the second check on the same budget; with Anthropic there is none."""
    if fake:
        return make_vision_client(settings, budget, fake=True)
    return make_live_client(settings, budget)  # checks the key before any work


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


# A model call that ended in one of these is not the system's answer: the provider was not
# reached or did not answer. (A refusal, or an answer that fails the schema, is the system's.)
FAILED_CALL_OUTCOMES = frozenset({"api_error", "timeout"})
# What a stop by a spend cap is called in the model client's reports, in plain words.
STOP_WORDS = {
    "budget_daily": "the --max-usd cap for the whole proof",
    "budget_job_usd": "the --max-usd cap for one run",
    "budget_job_calls": "the --max-calls cap for one run",
}
PROGRESS_EVERY = 50


class CallTally(logging.Handler):
    """Counts failed model calls by (HTTP status, error code) from the client's own log lines.

    The lines carry numbers and category codes only. Attached to the models logger during a
    live run, this also replaces the bare `model_call` lines Python would otherwise print for
    every failure, and gives the failure message the status and code the provider sent.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self._lock = threading.Lock()
        self.failed: Counter[tuple[object, object]] = Counter()

    def emit(self, record: logging.LogRecord) -> None:
        fields = getattr(record, FIELDS_ATTR, None)
        if record.getMessage() != "model_call" or not isinstance(fields, dict):
            return
        if fields.get("outcome") in FAILED_CALL_OUTCOMES:
            key = (fields.get("status"), fields.get("code") or fields.get("error"))
            with self._lock:
                self.failed[key] += 1

    def reset(self) -> None:
        with self._lock:
            self.failed.clear()

    def describe(self) -> str:
        with self._lock:
            parts = [
                f"HTTP {st or 'none'} {code or 'no code'} x {n}"
                for (st, code), n in sorted(self.failed.items(), key=lambda kv: -kv[1])
            ]
        return "; ".join(parts)


def failed_calls(rows: list[dict[str, Any]]) -> tuple[int, int, Counter[str]]:
    """(model calls that failed, model calls in all, failures by outcome) in a run's answers."""
    model = [r for r in rows if r["t"] == "model"]
    verify = [r for r in rows if r["t"] == "verify"]
    by_outcome: Counter[str] = Counter(
        r["call"]["outcome"] for r in model if r["call"]["outcome"] in FAILED_CALL_OUTCOMES
    )
    by_outcome.update(
        f"second check {r['verdict']['reason_code']}"
        for r in verify
        if r["verdict"].get("reason_code") in FAILED_CALL_OUTCOMES
    )
    return sum(by_outcome.values()), len(model) + len(verify), by_outcome


def require_answers(stem: str, rows: list[dict[str, Any]], tally: CallTally | None) -> None:
    """A run in which a model call failed is not recorded: a failed call is not the system's
    answer, and it would be scored as one. Raises `Incomplete`; the runs that finished are
    kept for `--resume`."""
    failed, total, by_outcome = failed_calls(rows)
    if not failed:
        return
    detail = ", ".join(f"{k} x {n}" for k, n in sorted(by_outcome.items()))
    codes = tally.describe() if tally is not None else ""
    raise Incomplete(
        f"{stem}: {failed} of {total} model calls failed ({detail}"
        f"{'; ' + codes if codes else ''}). A failed call is not the system's answer, so this "
        "run was not recorded. Check the account's credit and rate limit and the key, then "
        "run the same command again with --resume"
    )


def probe_api(settings: Settings, *, send: Callable[[], None] | None = None) -> None:
    """One small request before any work, so a missing key, an empty balance or a rate limit
    stops a live run at once and names itself. Costs a fraction of a cent.

    `send` replaces the request (tests). Raises `ProofError` with the HTTP status and the
    provider's error code, never the provider's message (it can echo the request).
    """
    if settings.model_provider == "anthropic":
        return _probe_anthropic(settings, send)
    import openai

    def real() -> None:
        sdk = openai.OpenAI(
            api_key=settings.require_openai_key(),
            timeout=settings.model_timeout_s,
            max_retries=0,
        )
        sdk.responses.create(
            model=settings.cheap_model,
            input="Reply with the word ok.",
            max_output_tokens=32,
            store=False,
        )

    try:
        (send or real)()
    except openai.APIStatusError as exc:
        code = exc.code if isinstance(getattr(exc, "code", None), str) else "no code"
        hint = {
            401: "the key was refused",
            403: "the key may not use this model",
            429: "the account has no credit or is rate limited",
        }.get(exc.status_code, "the API did not answer a test request")
        raise ProofError(
            f"the model API refused a one-word test request: HTTP {exc.status_code}, "
            f"{code} ({hint}). Nothing was recorded"
        ) from None
    except openai.OpenAIError as exc:
        raise ProofError(
            f"the model API could not be reached for a test request ({type(exc).__name__}). "
            "Nothing was recorded"
        ) from None


def _probe_anthropic(settings: Settings, send: Callable[[], None] | None) -> None:
    """`probe_api` for the Anthropic provider: one tiny Messages request on the cheap model."""
    import anthropic

    from a11y_stem.models.anthropic_client import build_sdk

    def real() -> None:
        sdk = build_sdk(settings).with_options(timeout=settings.model_timeout_s, max_retries=0)
        sdk.messages.create(
            model=settings.cheap_model,
            max_tokens=512,  # thinking counts toward it
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": "Reply with the word ok."}],
        )

    try:
        (send or real)()
    except anthropic.APIStatusError as exc:
        kind = exc.type if isinstance(getattr(exc, "type", None), str) else "no type"
        hint = {
            400: "the request was refused; a key that is not scoped to a workspace needs "
            "ANTHROPIC_WORKSPACE_ID",
            401: "the key was refused",
            403: "the key may not use this model",
            404: "the model is unknown or not available to this organisation",
            429: "the account has no credit or is rate limited",
        }.get(exc.status_code, "the API did not answer a test request")
        raise ProofError(
            f"the model API refused a one-word test request: HTTP {exc.status_code}, "
            f"{kind} ({hint}). Nothing was recorded"
        ) from None
    except anthropic.AnthropicError as exc:
        raise ProofError(
            f"the model API could not be reached for a test request ({type(exc).__name__}). "
            "Nothing was recorded"
        ) from None


def prebuild_inputs(pset: ProofSet, media_factory: Callable[[], Any], records: list[dict]) -> None:
    """Build every input of every task before the first model call, as the eval does for
    one task: a failed print or download stops the proof with nothing spent."""
    for task in dict.fromkeys(pset.tasks):
        media = media_factory()
        try:
            pe.INPUTS[task](media, pe.select_units(task, records, None))
        finally:
            media.close()


# (folder, extension) of the files a finished run leaves in the writer's partial folder.
RUN_FILES = (("answers", "jsonl"), ("reports", "json"), ("runs", "json"))


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
    keep_finished: bool = False,
    resume: bool = False,
    section_workers: int = 1,
) -> tuple[ArmResults, MediaRecorder, float]:
    """Run every (task, repeat) with the real models and record the answers into `writer`.

    `section_workers` converts that many sections of the document task at once, which
    shortens a live run (it waits on a hosted model). The default 1 is how the registered
    runs are made. With more, the sections' wall-clock times are not recorded (they would
    include each one's wait for the others, and a replay cannot measure them), and a
    replay is unchanged: it always converts the sections one after the other.

    One spend guard covers the whole proof; each run gets its own `JobBudget`, so a run's
    own spend is known. A run the budget stops is not scored half-converted: the proof
    stops with `Incomplete` and the partial bundle is left under `<dir>.partial`. With
    `keep_finished`, a stop by the cap ends the run instead: the runs that finished are kept
    (the one the cap stopped is dropped whole), `ArmResults.stopped` says why, and the
    report is labelled incomplete. A cap stop with no finished run still raises.

    With `resume`, a (task, repeat) whose answers, report and run record are already in the
    writer's partial folder (a run that finished before an interruption) is not made again:
    its record is read back and its spend counted against the cap. A run that was running
    when the process stopped left no answers and is made again whole.
    """
    say = progress or (lambda _msg: None)
    tally = CallTally()
    models_log = logging.getLogger("a11y_stem.models")
    models_log.addHandler(tally)
    try:
        return _record_arm(
            pset,
            gold_dir,
            settings,
            writer,
            media_factory=media_factory,
            max_usd=max_usd,
            max_calls=max_calls,
            date=date,
            fake=fake,
            say=say,
            client_factory=client_factory,
            keep_finished=keep_finished,
            resume=resume,
            tally=tally,
            section_workers=section_workers,
        )
    finally:
        models_log.removeHandler(tally)


def _record_arm(
    pset: ProofSet,
    gold_dir: Path,
    settings: Settings,
    writer: BundleWriter,
    *,
    media_factory: Callable[[], Any],
    max_usd: float,
    max_calls: int,
    date: str,
    fake: bool,
    say: Callable[[str], None],
    client_factory: Callable[[Settings, JobBudget], VisionClient] | None,
    keep_finished: bool,
    resume: bool,
    tally: CallTally,
    section_workers: int = 1,
) -> tuple[ArmResults, MediaRecorder, float]:
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
        stem = run_stem(task, repeat)
        if resume and all(writer.has(f"{d}/{stem}.{e}") for d, e in RUN_FILES):
            record = writer.read_json(f"runs/{stem}.json")
            report = writer.read_json(f"reports/{stem}.json")
            guard.settle(guard.reserve(record["spend_usd"]), record["spend_usd"])
            results.outcomes.append(
                RunOutcome(
                    task,
                    repeat,
                    report,
                    [],
                    timing=record["timing"],
                    wall_seconds=record["wall_seconds"],
                )
            )
            results.resumed.append(stem)
            say(f"resume {stem}: already recorded")
            continue
        budget = JobBudget(guard, max_usd=max_usd, max_calls=max_calls)
        store = AnswerStore()
        inner = (
            client_factory(settings, budget)
            if client_factory is not None
            else live_client(settings, budget, fake=fake)
        )
        tally.reset()
        counter = itertools.count(1)

        def progress_line(_call: Any, stem: str = stem, counter: Any = counter) -> None:
            n = next(counter)
            if n % PROGRESS_EVERY == 0:
                say(f"  {stem}: {n} model calls made")

        client = RecordingClient(inner, store, on_call=progress_line)
        cfg = _config(
            task,
            pset=pset,
            settings=settings,
            client=client,
            media=recording_media(),
            gold_dir=gold_dir,
            date=date,
            label="fake" if fake else settings.model_provider,
            concurrency=pset.concurrency,
            budget=budget,
            gaps=gaps,
            section_workers=section_workers if task == "document" else 1,
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
            message = f"{run_stem(task, repeat)}: stopped by the spend cap ({exc})"
            if keep_finished and results.outcomes:
                results.stopped = message
                break
            raise Incomplete(message) from exc
        wall = time.monotonic() - started
        if not done.report.get("complete"):
            reason = str(done.report.get("stopped_reason"))
            message = f"{stem} did not finish ({STOP_WORDS.get(reason, reason)})"
            if keep_finished and results.outcomes:
                results.stopped = message
                break
            raise Incomplete(
                f"{message}; raise --max-usd. A budget stop is never scored half-converted"
            )
        require_answers(stem, store.rows(), tally)
        # Sections that ran together waited for each other: their times say nothing.
        timing = [] if cfg.section_workers > 1 else section_timing(task, done.items)
        spent_here = float(done.report.get("cost", {}).get("budget_spent_usd") or 0.0)
        writer.write_jsonl(f"answers/{stem}.jsonl", store.rows())
        writer.write_json(f"reports/{stem}.json", done.report)
        writer.write_json(
            f"runs/{stem}.json",
            {"timing": timing, "wall_seconds": round(wall, 1), "spend_usd": spent_here},
        )
        results.outcomes.append(
            RunOutcome(
                task, repeat, done.report, done.items, timing=timing, wall_seconds=round(wall, 1)
            )
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
