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
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from a11y_stem.config import Settings
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

    settings = runner.proof_settings()
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
    settings = runner.proof_settings()
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
        "client": "fake" if opts.fake else "openai",
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
        if bundle.path("systems").is_dir() and any(
            a.startswith("chatbot-") for a in (p.name for p in bundle.path("systems").iterdir())
        ):
            raise ProofError("this recorded run already holds the chatbot arms")
        gold_dir = resolve_gold(pset, opts, bundle.gold_dir, work, say)
        selected = [chatbot.ARM_BY_ID[a] for a in arms] if arms else list(chatbot.ARMS)
        summary = chatbot.record_arms(
            pdfs,
            out,
            arms=selected,
            max_usd=opts.max_usd,
            client=chat_client or chatbot.make_client(),
            say=say,
        )
        if summary["refused_by_cap"]:
            raise ProofError(
                f"the cap ${opts.max_usd:.2f} did not hold every run "
                f"({len(summary['refused_by_cap'])} refused, first: "
                f"{summary['refused_by_cap'][0]}); nothing was added to the bundle. "
                "Runs already made are kept in the work folder, so a rerun does not pay for them"
            )
        remaining = max(0.0, opts.max_usd - summary["spent_usd"])
        settings = runner.proof_settings()
        records = goldinfo.load_records(gold_dir)
        golds = systems.section_golds(records, [s.id for s in pset.sections])
        from a11y_stem.eval import render
        from a11y_stem.models.openai_client import OpenAIVisionClient

        images = render.ImageCache(work / "eval-cache" / "images")
        budget = JobBudget(InMemorySpendGuard(cap_usd=remaining), max_usd=remaining, max_calls=2000)
        client = judge_client or OpenAIVisionClient(settings, budget)
        loaded = systems.load_arms_dir(out)
        judge_cost = judging.judge_arms(
            loaded, golds, client=client, settings=settings, image_for=images.png, say=say
        )
        extra = {
            "systems_spend": {
                "chatbot_usd": summary["spent_usd"],
                "judge_usd": round(judge_cost, 6),
                "cap_usd": opts.max_usd,
            }
        }
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
    new = bundle.extend(out, "systems", extra={**extra})
    say(f"added {kind} to {new.root.name}/ ({len(new.files)} files)")
    return extra
