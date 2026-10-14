"""The proof command: checks the pins, runs the arm in replay or live mode, writes PROOF.md.

Replay (the default) needs no key and no network:

    pins -> bundle intact -> code identity -> replay every (task, repeat) -> compare with
    the recorded reports -> stages -> PROOF.md

Live re-calls the models and records a new bundle, then replays that bundle to write
PROOF.md, so the file is always what `make proof` will produce from the bundle:

    pins -> sign-off and cost cap -> fetch gold -> print PDFs and build every input ->
    record (task, repeat) runs -> stage hooks -> write bundle -> replay -> PROOF.md

A held-out set has extra rules (preregistration sections 10 and 12): no fake client, the
pinned decisions must be confirmed with `--confirm-signoff`, and the checkout must be clean.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from a11y_stem.config import Provider, Settings
from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.eval import pipeline_eval as pe
from a11y_stem.gold.openstax import HttpFetcher
from a11y_stem.models.base import VisionClient
from a11y_stem.proof import goldinfo, registry, runner, sources, stages, stats
from a11y_stem.proof.bundle import Bundle, BundleWriter
from a11y_stem.proof.document import DocumentInfo, render
from a11y_stem.proof.errors import BundleError, Drift, Incomplete, PinMismatch, ProofError
from a11y_stem.proof.registry import PinCheck, ProofSet

ENV_CACHE = "A11Y_PROOF_CACHE"


@dataclass
class ProofOptions:
    root: Path
    set_id: str | None = None
    bundle_dir: Path | None = None
    out: Path | None = None
    work_dir: Path | None = None
    live: bool = False
    record_dir: Path | None = None
    max_usd: float | None = None
    max_calls: int = runner.DEFAULT_MAX_CALLS
    # Live runs only: sections of the document task converted at once (`runner.record_arm`).
    section_workers: int = 1
    fake: bool = False
    confirm_signoff: bool = False
    allow_dirty: bool = False
    resume: bool = False  # continue an interrupted recording (runs that finished are kept)
    keep_finished: bool = False  # a cap stop keeps the runs that finished (labelled incomplete)
    allow_drift: bool = False
    allow_misses: bool = False
    verify_sources: bool = False
    require_complete: bool = False
    n_boot: int = stats.N_BOOT
    date: str = ""  # the recording date of a live run; the CLI passes today
    # Test seams: other inputs and another model client for a live run.
    media_factory: Callable[[], Any] | None = None
    client_factory: Callable[[Settings, JobBudget], VisionClient] | None = None
    fetcher: Any = None
    probe: Callable[[], None] | None = None  # replaces the one-request API check
    # A live run on another provider (`--provider anthropic`), and its model ids (None: the
    # settings' own). A replay ignores these: it uses what the recording says.
    provider: Provider = "openai"
    main_model: str | None = None
    cheap_model: str | None = None
    # With `--add-systems chatbot`: make this many runs of each arm instead of the arm's
    # own number (None: the arm's own). The bundle records the number that was made.
    chat_runs: int | None = None
    # With `--add-systems chatbot --provider anthropic`: send the chatbot arms and the figure
    # judge as Message Batches (half the price, hours instead of minutes): the same requests
    # (`proof.chatbot_batch`, `models.anthropic_batch`).
    chat_batch: bool = False


@dataclass
class ProofResult:
    exit_code: int
    out: Path | None = None
    text: str = ""
    drift: list[str] = field(default_factory=list)
    not_wired: list[str] = field(default_factory=list)


def default_work_dir(pset: ProofSet) -> Path:
    """Where prints, downloads and caches go: outside the repository, as the
    preregistration requires (a held-out print left in `.cache/eval/pages` would flow into
    development sweeps)."""
    base = os.environ.get(ENV_CACHE)
    if base:
        return Path(base) / pset.id
    cache_home = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache_home) / "a11y-stem-proof" / pset.id


def _default_out(opts: ProofOptions, pset: ProofSet) -> Path:
    if opts.out is not None:
        return opts.out
    is_default = registry.default_set_id(opts.root) == pset.id
    return opts.root / ("PROOF.md" if is_default else f"PROOF-{pset.id}.md")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=path.name, suffix=".tmp", delete=False
    ) as fh:
        fh.write(text)
        tmp = Path(fh.name)
    os.replace(tmp, path)


# ------------------------------------------------------------------------- gold/pins


def resolve_gold(
    pset: ProofSet,
    opts: ProofOptions,
    bundle_gold: Path | None,
    work: Path,
    say: Callable[[str], None],
) -> Path:
    """The gold directory to score against: the checkout's own, the bundle's copy, or a
    fresh fetch from OpenStax when neither exists."""
    candidates = []
    if pset.gold_dir:
        candidates.append(opts.root / pset.gold_dir)
    if bundle_gold is not None:
        candidates.append(bundle_gold)
    for path in candidates:
        if (path / "manifest.json").is_file():
            return path
    say("no committed copy of the answer key; fetching it from OpenStax")
    return sources.fetch_gold_set(
        pset,
        work / f"{sources.FETCHED_PREFIX}-{pset.id}",
        work / "openstax",
        fetcher=opts.fetcher,
        say=say,
    )


def books_of(gold_dir: Path) -> list[dict[str, Any]]:
    manifest = json.loads((gold_dir / "manifest.json").read_text(encoding="utf-8"))
    return [manifest["books"][k] for k in sorted(manifest["books"])]


# ------------------------------------------------------------------------------ run


def run_proof(opts: ProofOptions, say: Callable[[str], None] = print) -> ProofResult:
    pset = registry.resolve_set(opts.root, opts.set_id)
    stages.load_plugins()
    if opts.live:
        return _live(pset, opts, say)
    bundle_dir = opts.bundle_dir or opts.root / pset.bundle
    return _replay(pset, opts, Bundle.open(bundle_dir), say, mode="replay")


def check_proof(opts: ProofOptions) -> list[PinCheck]:
    """Pins and bundle integrity only: no run. Raises on a mismatch."""
    pset = registry.resolve_set(opts.root, opts.set_id)
    bundle_dir = opts.bundle_dir or opts.root / pset.bundle
    checks: list[PinCheck] = []
    prereg = registry.check_prereg(pset, opts.root)
    if prereg is not None:
        checks.append(prereg)
    try:
        bundle = Bundle.open(bundle_dir)
    except BundleError as exc:
        checks.append(PinCheck("recorded run", "absent", str(exc)))
        bundle = None
    if bundle is not None:
        problems = bundle.verify()
        checks.append(
            PinCheck("recorded run", "mismatch", "; ".join(problems[:3]))
            if problems
            else PinCheck("recorded run", "ok", f"{len(bundle.files)} files match the manifest")
        )
        if bundle.set_id != pset.id:
            checks.append(PinCheck("recorded run", "mismatch", f"made for {bundle.set_id!r}"))
    gold = (opts.root / pset.gold_dir) if pset.gold_dir else (bundle.gold_dir if bundle else None)
    if gold is not None and (gold / "manifest.json").is_file():
        checks.extend(registry.check_gold(gold, pset))
    else:
        checks.append(PinCheck("gold manifest", "absent", "no committed copy of the answer key"))
    registry.require_ok(checks)
    return checks


def _replay(
    pset: ProofSet, opts: ProofOptions, bundle: Bundle, say: Callable[[str], None], *, mode: str
) -> ProofResult:
    work = opts.work_dir or default_work_dir(pset)
    bundle.require_intact()
    if bundle.set_id != pset.id:
        raise BundleError(f"the bundle was recorded for {bundle.set_id!r}, not {pset.id!r}")
    gold_dir = resolve_gold(pset, opts, bundle.gold_dir, work, say)
    pins = registry.check_gold(gold_dir, pset)
    prereg = registry.check_prereg(pset, opts.root)
    if prereg is not None:
        pins.append(prereg)
    pins.append(PinCheck("recorded run", "ok", f"{len(bundle.files)} files match the manifest"))
    registry.require_ok(pins)

    fetched_note = None
    if opts.verify_sources:
        fetched = sources.fetch_gold_set(
            pset,
            work / f"{sources.FETCHED_PREFIX}-verify",
            work / "openstax",
            fetcher=opts.fetcher,
            say=say,
        )
        fetched_checks = registry.check_gold(fetched, pset)
        if any(c.status != "ok" for c in fetched_checks):
            raise PinMismatch(
                "the answer key fetched from OpenStax does not match the pin: "
                + "; ".join(f"{c.name}: {c.detail}" for c in fetched_checks if c.status != "ok")
            )
        fetched_note = "**matches the pinned manifest**"

    # The provider and models the recording was made with (the OpenAI defaults for a
    # recording that names none); the identity check below then compares the rest.
    settings = runner.recorded_settings(bundle.code.get("settings", {}))
    identity = runner.code_identity(settings)
    identity_diffs = runner.check_identity(bundle.code, identity)
    if identity_diffs and not opts.allow_drift:
        raise Drift(
            "this checkout is not the code that made the recording: "
            + "; ".join(identity_diffs[:4])
            + ". Check out the recorded commit, record again, or pass --allow-drift"
        )
    arm = runner.replay_arm(
        pset, bundle, gold_dir, settings, allow_misses=opts.allow_misses, progress=say
    )
    drift = runner.compare_with_recorded(bundle, arm)
    if drift and not opts.allow_drift:
        raise Drift(
            f"replaying the recording gives other numbers in {len(drift)} place(s), first: "
            f"{drift[0]}. The published numbers do not come from this checkout; pass "
            "--allow-drift to write the report anyway, labelled"
        )

    source_checks = []
    if opts.verify_sources:
        say("printing the PDFs again from OpenStax (needs Chromium)")
        source_checks = sources.reprint_check(pset, gold_dir, bundle, work, fetcher=opts.fetcher)

    records = goldinfo.load_records(gold_dir)
    facts = goldinfo.section_facts(records, [s.id for s in pset.sections])
    ctx = stages.StageContext(
        pset=pset,
        mode=mode,
        root=opts.root,
        bundle=bundle,
        gold_dir=gold_dir,
        records=records,
        facts=facts,
        arm=arm,
        g1=goldinfo.g1_ids(records),
        settings=settings,
        work_dir=work,
        n_boot=opts.n_boot,
        pdf_paths={e["section"]: bundle.path(e["file"]) for e in bundle.pdfs},
        say=say,
    )
    results = [(s.title, s.run(ctx)) for s in stages.active_stages()]
    info = DocumentInfo(
        pset=pset,
        mode=mode,
        bundle=bundle,
        pins=pins,
        facts=facts,
        books=books_of(gold_dir),
        identity_diffs=identity_diffs,
        drift=drift,
        compared_runs=len(arm.outcomes),
        replay_stats=[o.replay for o in arm.outcomes if o.replay],
        source_checks=source_checks,
        fetched_gold=fetched_note,
        stages=results,
        fake=bundle.manifest.get("client") == "fake",
    )
    text = render(info)
    out = _default_out(opts, pset)
    _write_atomic(out, text)
    not_wired = [t for t, r in results if r.status == "not_wired"]
    code = 1 if (opts.require_complete and (not_wired or not arm.complete)) else 0
    return ProofResult(code, out, text, drift, not_wired)


# ----------------------------------------------------------------------------- live


def _live(pset: ProofSet, opts: ProofOptions, say: Callable[[str], None]) -> ProofResult:
    if not opts.date:
        raise ProofError("a live run needs the recording date (the CLI passes today's)")
    if not opts.fake and opts.max_usd is None:
        raise ProofError("--max-usd is required for real model calls (or pass --fake)")
    if opts.provider != "openai" and opts.out is None:
        # PROOF.md is the report of the committed OpenAI recording; a run on another
        # provider writes its report next to it, never over it.
        raise ProofError(
            f"a live run on the {opts.provider} provider needs --out, so that PROOF.md "
            "(the committed OpenAI recording's report) is not overwritten"
        )
    work = opts.work_dir or default_work_dir(pset)
    git = runner.git_state(opts.root)
    runner.refuse_unsafe_live(
        pset, fake=opts.fake, signoff=opts.confirm_signoff, allow_dirty=opts.allow_dirty, git=git
    )
    prereg = registry.check_prereg(pset, opts.root)
    if prereg is not None:
        registry.require_ok([prereg])
        if pset.held_out and prereg.status == "absent":
            # A replay may be shown without the registration file; a run that makes
            # held-out numbers may not (preregistration section 12, item 9).
            raise ProofError(f"a held-out live run needs the registration file: {prereg.detail}")
    settings = runner.proof_settings(
        opts.provider, main_model=opts.main_model, cheap_model=opts.cheap_model
    )
    if not opts.fake and opts.client_factory is None:
        say("checking the model API with one small request")
        runner.probe_api(settings, send=opts.probe)
    gold_dir = resolve_gold(pset, opts, None, work, say)
    registry.require_ok(registry.check_gold(gold_dir, pset))
    record_dir = opts.record_dir or work / "bundles" / f"{pset.id}-{opts.date}"
    writer = BundleWriter(record_dir, resume=opts.resume)
    http = opts.fetcher or HttpFetcher(cache_dir=work / "openstax")

    def media() -> Any:
        if opts.media_factory is not None:
            return opts.media_factory()
        return pe.CachedMedia(gold_dir, work / "eval-cache", http, paper="Letter")

    cap = opts.max_usd if opts.max_usd is not None else float("inf")
    arm, recorder, spent = runner.record_arm(
        pset,
        gold_dir,
        settings,
        writer,
        media_factory=media,
        max_usd=cap,
        max_calls=opts.max_calls,
        date=opts.date,
        fake=opts.fake,
        progress=say,
        client_factory=opts.client_factory,
        keep_finished=opts.keep_finished,
        resume=opts.resume,
        section_workers=opts.section_workers,
    )
    pdfs = runner.pdf_entries(pset, recorder, writer)
    writer.write_jsonl("media/equations.jsonl", recorder.equation_rows())
    writer.write_jsonl("media/figures.jsonl", recorder.figure_rows())
    if not pset.gold_dir:
        writer.copy_tree(gold_dir, "gold")

    records = goldinfo.load_records(gold_dir)
    pdf_dir = work / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_paths = {}
    for entry, section in zip(pdfs, pset.sections, strict=True):
        path = pdf_dir / Path(entry["file"]).name
        path.write_bytes(recorder.pdfs[section.id])
        pdf_paths[section.id] = path
    record_ctx = stages.RecordContext(
        pset=pset,
        root=opts.root,
        gold_dir=gold_dir,
        records=records,
        arm=arm,
        settings=settings,
        work_dir=work,
        pdf_paths=pdf_paths,
        say=say,
    )
    for stage in stages.active_stages():
        stage.record(record_ctx, writer)

    manifest = {
        "set_id": pset.id,
        "recorded_on": opts.date,
        "mode": "live",
        "client": "fake" if opts.fake else settings.model_provider,
        "code": {**runner.code_identity(settings), "git": git},
        "environment": runner.environment(),
        "runs": runner.run_entries(arm),
        "pdfs": pdfs,
        "spend": {
            "total_usd": round(spent, 6),
            "cap_usd": None if opts.max_usd is None else opts.max_usd,
        },
        "signoff_confirmed": opts.confirm_signoff,
    }
    if arm.resumed:
        manifest["resumed_runs"] = arm.resumed
    if arm.stopped:
        manifest["incomplete"] = {
            "reason": arm.stopped,
            "runs_made": len(arm.outcomes),
            "runs_planned": len(pset.runs()),
        }
    if any(
        not o.complete for o in arm.outcomes
    ):  # record_arm raises first; kept as a guard for a changed contract
        raise Incomplete("a run did not finish; the bundle was not written")
    bundle = writer.finish(manifest)
    say(f"wrote the recorded run to {bundle.root}")
    return _replay(pset, opts, bundle, say, mode="live")


# ------------------------------------------------------------------------- add systems

SYSTEM_KINDS = ("chatbot", "parsers")


def add_systems(
    opts: ProofOptions,
    kind: str,
    say: Callable[[str], None] = print,
    *,
    chat_client: Any = None,
    judge_client: Any = None,
    arms: list[str] | None = None,
) -> dict[str, Any]:
    """Record another system's outputs into a finished bundle, then rewrite its manifest.

    `chatbot`: runs the chatbot arms (`proof.chatbot`) within `opts.max_usd`, then judges the
    figure descriptions found in their output with what is left of the cap (so the cap covers
    the judge too). `parsers`: runs `proof/parsers` (`make proof`). Both are resumable: a
    section already recorded in the work folder is not run again. Nothing is added to the
    bundle unless the whole step finished.
    """
    from a11y_stem.proof import chatbot, judging, systems

    if kind not in SYSTEM_KINDS:
        raise ProofError(f"unknown kind of system {kind!r}; known: {', '.join(SYSTEM_KINDS)}")
    pset = registry.resolve_set(opts.root, opts.set_id)
    bundle = Bundle.open(opts.bundle_dir or opts.root / pset.bundle)
    bundle.require_intact()
    if bundle.set_id != pset.id:
        raise BundleError(f"the bundle was recorded for {bundle.set_id!r}, not {pset.id!r}")
    work = opts.work_dir or default_work_dir(pset)
    out = work / "systems-out" / kind
    out.mkdir(parents=True, exist_ok=True)
    pdfs = {e["section"]: bundle.path(e["file"]) for e in bundle.pdfs}
    extra: dict[str, Any] = {}
    if kind == "chatbot":
        if opts.max_usd is None:
            raise ProofError("--max-usd is required to record the chatbot arms")
        selected = (
            [chatbot.ARM_BY_ID[a] for a in arms] if arms else list(chatbot.arms_for(opts.provider))
        )
        held = (
            {p.name for p in bundle.path("systems").iterdir()}
            if bundle.path("systems").is_dir()
            else set()
        )
        if any(a.id in held for a in selected):
            raise ProofError("this recorded run already holds the chatbot arms")
        chat_provider = {a.provider for a in selected}
        if chat_provider != {opts.provider}:
            raise ProofError(
                f"these arms run on {', '.join(sorted(chat_provider))}, not on {opts.provider}: "
                "pass --provider"
            )
        if chat_client is None:
            say("checking the model API with one small request")
            runner.probe_api(runner.proof_settings(opts.provider), send=opts.probe)
        gold_dir = resolve_gold(pset, opts, bundle.gold_dir, work, say)
        if opts.chat_batch and opts.provider != "anthropic":
            raise ProofError("--batch needs --provider anthropic (the Message Batches API)")
        record = chatbot.record_arms
        if opts.chat_batch:
            from a11y_stem.proof import chatbot_batch

            record = chatbot_batch.record_arms_batch
        summary = record(
            pdfs,
            out,
            arms=selected,
            max_usd=opts.max_usd,
            client=chat_client or chatbot.make_client(opts.provider),
            runs=opts.chat_runs,
            say=say,
        )
        refused = summary["refused_by_cap"]
        not_run = sorted({r.split("/")[0] for r in refused})
        primary = next((a.id for a in selected if a.role == "primary"), None)
        if primary in not_run:
            raise ProofError(
                f"the cap ${opts.max_usd:.2f} did not hold the primary arm "
                f"({len(refused)} jobs refused, first: {refused[0]}); nothing was added to the "
                "bundle. Runs already made are kept in the work folder, so a rerun does not "
                "pay for them"
            )
        if not_run:
            say(f"not run, the cap did not hold them: {', '.join(not_run)}")
        # Only arms that finished whole go into the bundle; a half-run arm is never scored.
        ready = work / "systems-out" / "chatbot-ready"
        shutil.rmtree(ready, ignore_errors=True)  # an earlier step's arms are in the bundle
        for arm_id in sorted(a.id for a in selected if a.id not in not_run):
            if (out / arm_id).is_dir():
                shutil.copytree(out / arm_id, ready / arm_id, dirs_exist_ok=True)
        out = ready
        spent = chatbot.recorded_cost(out, [a.id for a in selected if a.id not in not_run])
        remaining = max(0.0, opts.max_usd - spent)
        records = goldinfo.load_records(gold_dir)
        golds = systems.section_golds(records, [s.id for s in pset.sections])
        from a11y_stem.eval import render

        images = render.ImageCache(work / "eval-cache" / "images")
        budget = JobBudget(InMemorySpendGuard(cap_usd=remaining), max_usd=remaining, max_calls=2000)
        if opts.provider == "openai":
            from a11y_stem.models.openai_client import OpenAIVisionClient

            settings = runner.proof_settings()
            client = judge_client or OpenAIVisionClient(settings, budget)
            judge_info = {"provider": "openai", "model": settings.main_model}
        else:
            # The figure judge is claude-opus-5-5 with no refusal fallback, whichever model
            # the arms ran on (`eval.judge.make_judge`).
            from a11y_stem.eval import judge as judge_mod

            base = runner.proof_settings(opts.provider)
            if judge_client is None:
                made = judge_mod.make_judge(base, budget, run_client=None, provider="anthropic")
                client, settings, judge_info = made.client, made.settings, made.describe()
            else:  # a test's stand-in
                settings = judge_mod.judge_settings(base, "anthropic")
                client = judge_client
                judge_info = {"provider": "anthropic", "model": settings.main_model}
        loaded = systems.load_arms_dir(out)
        if opts.chat_batch and judge_client is None:
            judge_cost = judging.judge_arms_batched(
                loaded,
                golds,
                client=client,
                settings=settings,
                budget=budget,
                answers=work / "systems-out" / "judge-answers.jsonl",
                image_for=images.png,
                say=say,
            )
        else:
            judge_cost = judging.judge_arms(
                loaded, golds, client=client, settings=settings, image_for=images.png, say=say
            )
        extra = {
            "systems_spend": {
                "chatbot_usd": spent,
                "judge_usd": round(judge_cost, 6),
                "cap_usd": opts.max_usd,
            }
        }
        if opts.provider != "openai":
            extra["systems_judge"] = judge_info
        if opts.chat_runs:
            extra["systems_runs"] = {
                a.id: min(opts.chat_runs, a.runs) for a in selected if a.id not in not_run
            }
        if not_run:
            extra["systems_not_run"] = {arm: "the spend cap did not hold it" for arm in not_run}
    else:
        folder = opts.root / stages.PARSERS_DIR
        if not (folder / "Makefile").is_file():
            raise ProofError("proof/parsers is not in this checkout")
        import subprocess

        pdf_dir = bundle.path("media/pdfs")
        say("parsers: running proof/parsers (this takes hours; it can be interrupted and resumed)")
        command = ["make", "-C", str(folder), "proof", f"PDF_DIR={pdf_dir}", f"OUT_DIR={out}"]
        if arms:
            command.append("ARMS=" + " ".join(arms))
        done = subprocess.run(command, check=False)
        if done.returncode != 0:
            raise ProofError(f"proof/parsers failed (make exit code {done.returncode})")
        extra = {"systems_parsers": "recorded"}
    new = bundle.extend(out, "systems", extra=_merged_extra(bundle.manifest, extra))
    say(f"added {kind} to {new.root.name}/ ({len(new.files)} files)")
    return extra


def _merged_extra(manifest: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """`extra` for the manifest when systems are added in more than one step (the Claude
    arms are recorded one after the other): spends add up, per-arm notes are joined."""
    merged = dict(extra)
    spend, before = extra.get("systems_spend"), manifest.get("systems_spend")
    if spend and before:
        merged["systems_spend"] = {
            key: round(float(spend.get(key, 0.0)) + float(before.get(key, 0.0)), 6)
            for key in sorted({*spend, *before})
        }
    for key in ("systems_runs", "systems_not_run"):
        if extra.get(key) and manifest.get(key):
            merged[key] = {**manifest[key], **extra[key]}
    return merged
