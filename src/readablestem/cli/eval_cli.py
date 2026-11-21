"""CLI: readablestem eval pipeline|gate|calibrate|render|invented, demo build (WP7).

Commands that can call a model take `--max-usd` (a hard cap enforced by an
`InMemorySpendGuard` before each request) or `--fake` (an offline `FakeVisionClient`
with no recorded answers: every call is refused at zero cost, so a fake run checks the
plumbing, not quality). `--fake` and `--max-usd` together cap the fake's simulated cost,
which is how the budget stop is exercised offline.

Dates are taken here (`--date`, default today) and passed into library code, which
never reads the clock.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from readablestem.cli import heldout as heldout_cli

GOLD_DIR = Path("data/gold")
EVAL_DIR = Path("data/eval")
EVAL_CACHE = Path(".cache/eval")
OPENSTAX_CACHE = Path(".cache/openstax")
DEMO_DIR = Path("src/readablestem/web/demo")
WEEKLY_DIR = Path(".local") / "eval" / "weekly"  # git-ignored; commit a report on purpose
# Known-correct LaTeX for every sample equation (`make eval-latex-check`): an equation it
# still fails is a scorer gap, left out of the "excluding scorer gaps" review flags.
SCORER_CHECK = Path("tests/fixtures/sample_latex_predictions.jsonl")
DEFAULT_MAX_CALLS = 5000
# Same as eval.pipeline_eval.TASKS (a test checks); kept here so building the parser
# does not import the pipeline.
TASKS = ("equations", "headings", "figures", "document")
# The tables task (eval.tables) runs beside them: its own gold, modes and floors.
TABLES_TASK = "tables"
PIPELINE_TASKS = (*TASKS, TABLES_TASK)
TABLE_GOLD_DIR = Path("data/gold/tables")
AUTHORED_TABLES = Path("data/authored/tables")
# Private real documents for the tables gate's second part (git-ignored; never committed).
PRIVATE_TABLES = Path("private-inputs/tables")


def _default(path: Path) -> Path:
    from readablestem.cli.main import project_root

    return project_root() / path


def _shown(path: Path) -> str:
    """Path relative to the project root when inside it (reports never hold home paths)."""
    from readablestem.cli.main import project_root

    try:
        return path.resolve().relative_to(project_root().resolve()).as_posix()
    except ValueError:
        return path.name


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, got {number}")
    return number


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from None
    if not number > 0 or math.isinf(number):
        raise argparse.ArgumentTypeError(f"must be a positive amount, got {value}")
    return number


def _iso_date(value: str) -> str:
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None


def _add_spend_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-usd",
        type=_positive_float,
        help="hard spend cap for this run in USD (required unless --fake)",
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="offline FakeVisionClient: no API calls, plumbing check only",
    )
    parser.add_argument(
        "--max-calls",
        type=_positive_int,
        default=DEFAULT_MAX_CALLS,
        help=f"cap on model calls (default {DEFAULT_MAX_CALLS})",
    )
    parser.add_argument(
        "--provider",
        choices=["openai", "anthropic"],
        help="model provider for this run (default: MODEL_PROVIDER, openai unless set); "
        "the report records it as `client` and the model ids as `models`",
    )
    parser.add_argument(
        "--main-model",
        metavar="ID",
        help="main model of the chosen provider (default: the setting, e.g. claude-opus-5-5)",
    )
    parser.add_argument(
        "--cheap-model",
        metavar="ID",
        help="cheap model of the chosen provider (default: the setting, e.g. claude-haiku-5-5)",
    )
    parser.add_argument(
        "--profile",
        choices=["production"],
        help="run under the production profile (ops/production.profile.toml) instead of this "
        "environment's settings; the report then names the profile and its hash, and without "
        "it says 'development settings' unless the settings are the profile's",
    )


def _add_judge_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--judge-provider",
        choices=["openai", "anthropic"],
        help="provider of the figure judge (default: the run's provider); the report records "
        "it as `judge_client`",
    )
    parser.add_argument(
        "--judge-model",
        metavar="ID",
        help="model of the figure judge (default: the run's main model on openai, "
        "claude-opus-5-5 on anthropic, whatever the run's main model is)",
    )


def _make_judge(
    args: argparse.Namespace, settings: Any, client: Any, budget: Any, *, label: str
) -> Any:
    """The figure judge of this run (`eval.judge.make_judge`), or an error message. It
    charges the run's budget, so one `--max-usd` covers the run and the judge."""
    from pydantic import ValidationError

    from readablestem.config import ConfigError
    from readablestem.eval import judge as judge_mod

    try:
        made = judge_mod.make_judge(
            settings,
            budget,
            run_client=client,
            provider=getattr(args, "judge_provider", None),
            model=getattr(args, "judge_model", None),
            fake=label == "fake",
        )
        if made.client is not client:  # the judge's own client: behind the file as well
            made = judge_mod.JudgeSetup(_cached(args, made.settings, made.client), made.settings)
        return made
    except ValidationError as exc:
        reasons = "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())
        return f"invalid judge settings: {reasons}"
    except ConfigError as exc:
        return str(exc)


def _cached(args: argparse.Namespace, settings: Any, client: Any) -> Any:
    """`client` behind the answers file when the run has one (`--answers`)."""
    path = getattr(args, "answers", None)
    if path is None:
        return client
    from readablestem.eval.answers import CachedClient, keep_second_check_answers

    rekey = bool(getattr(args, "answers_rekey", False))
    if getattr(args, "batch", False):
        # A staged run: what the file cannot answer is collected, not sent (eval/staged.py).
        from readablestem.models.anthropic_batch import StagingClient

        staging = StagingClient(client, args.staged_pool)
        cached = CachedClient(staging, path, settings, rekey=rekey)
        staging.owner = cached
        return cached
    offline = bool(getattr(args, "answers_offline", False))
    cached = CachedClient(None if offline else client, path, settings, rekey=rekey)
    keep_second_check_answers(client, cached, offline=offline)
    return cached


def _make_client(args: argparse.Namespace, settings: Any) -> tuple[Any, Any, str] | str:
    """(client, budget, label), or an error message. Every request reserves spend first."""
    from readablestem.config import ConfigError
    from readablestem.core.budget import InMemorySpendGuard, JobBudget
    from readablestem.models.fake import FakeVisionClient

    if not args.fake and args.max_usd is None:
        return "--max-usd is required for real model calls (or pass --fake)"
    cap = args.max_usd if args.max_usd is not None else math.inf
    if not args.fake:
        from readablestem.ops.limits import run_cap_problem

        if problem := run_cap_problem(settings, cap, settings.model_concurrency):
            return f"--max-usd: {problem}"
    budget = JobBudget(InMemorySpendGuard(cap_usd=cap), max_usd=cap, max_calls=args.max_calls)
    if getattr(args, "batch", False):
        from readablestem.models.anthropic_batch import BatchRunner, PendingPool

        args.staged_pool = PendingPool()
        args.staged_runner = BatchRunner(budget)
    if args.fake:
        return FakeVisionClient(budget=budget), budget, "fake"
    from readablestem.models import make_live_client

    try:
        # Checks the key before any work, not on the first call. The OpenAI client carries
        # the second automated check on the same budget, as in the app
        # (DECISIONS_FIGURE_VERIFY, DECISIONS_EQUATION_SHADOW turn it off); the Claude
        # client carries the Claude check (CLAUDE_SECOND_CHECK, off by default).
        client = make_live_client(settings, budget)
    except ConfigError as exc:
        return str(exc)
    client = _cached(args, settings, client)
    return client, budget, settings.model_provider


def _settings() -> Any:
    from readablestem.config import get_settings

    return get_settings().model_copy(update={"table_search_unlimited": True})


def _settings_for(args: argparse.Namespace) -> Any:
    """The settings with this run's `--provider`, `--main-model` and `--cheap-model`
    applied; an error message (str) when they do not make valid settings."""
    from pydantic import ValidationError

    from readablestem.config import with_provider

    base = _settings()
    if getattr(args, "profile", None) == "production":
        base = base.production_profile()
    try:
        return with_provider(
            base,
            getattr(args, "provider", None),
            main_model=getattr(args, "main_model", None),
            cheap_model=getattr(args, "cheap_model", None),
        )
    except ValidationError as exc:
        reasons = "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())
        return f"invalid model settings: {reasons}"


# ================================================================== kept recordings


@contextmanager
def _recording(
    args: argparse.Namespace, keep: tuple[Path, str] | None, *, model_run: bool = True
) -> Generator[Any]:
    """Make the run's folder (eval/run_record.py) when the run calls a real model.

    `keep` is (the set's root folder, the set's name), or None for a run that keeps nothing
    (the sealed check part). Unless the run was given its own files, `--answers` and `--items`
    point into the folder, so a live run always leaves its answers and rows behind. A fake
    run, a replay and a run with no model keep nothing. A folder that stayed empty (the run
    stopped on an error before it asked anything) is taken away again.
    """
    if keep is None or args.fake or getattr(args, "answers_offline", False) or not model_run:
        yield None
        return
    from readablestem.cli.main import project_root
    from readablestem.eval import run_record

    root, name = keep
    rec = run_record.start(root, name, repo=project_root(), task=getattr(args, "task", None))
    if getattr(args, "answers", None) is None:
        args.answers = rec.answers
    if getattr(args, "items", None) is None:
        args.items = rec.items
    try:
        yield rec
    finally:
        if run_record.holds_nothing(rec.directory):
            run_record.discard(rec.directory)


def _finish_recording(rec: Any, args: argparse.Namespace, report: dict) -> None:
    """Name the folder and the commit in the report (before it is written)."""
    if rec is not None:
        report["recording"] = rec.describe(
            answers=getattr(args, "answers", None) == rec.answers,
            items=getattr(args, "items", None) == rec.items,
        )


def _copy_report(rec: Any, report: dict) -> None:
    """The report beside its answers and rows; says where the folder is."""
    if rec is None:
        return
    from readablestem.eval import pipeline_eval as pe

    pe.write_report(report, rec.report)
    print(f"kept: {rec.directory}")


def _gold_keep() -> tuple[Path, str]:
    """Where a run on the repository's own gold (`data/gold`) keeps its recording."""
    from readablestem.eval import run_record

    return run_record.set_root("gold"), "gold"


# ======================================================================== pipeline


def _cmd_pipeline(args: argparse.Namespace) -> int:
    if (args.answers or args.answers_offline) and (
        args.gold_set == "heldout" or (args.gold_set == "dev-v2" and args.part == "check")
    ):
        return _error("--answers is for development sets: not for the held-out or check parts")
    if args.answers_offline and not args.answers:
        return _error("--answers-offline needs --answers FILE")
    if args.answers_rekey and not args.answers:
        return _error("--answers-rekey needs --answers FILE")
    if args.scanned and args.task != "document":
        return _error("--scanned applies to the document task")
    if args.scanned and (
        args.gold_set == "heldout" or (args.gold_set == "dev-v2" and args.part == "check")
    ):
        return _error("--scanned is for development sets: not for the held-out or check parts")
    if args.batch and not (args.answers and not args.answers_offline and not args.fake):
        return _error("--batch needs --answers FILE, real model calls, and no --answers-offline")
    if args.batch and (args.provider or _settings().model_provider) != "anthropic":
        return _error("--batch is for --provider anthropic (the Message Batches API)")
    if args.gold_set == "dev-v2":
        return _run_devset(args)
    if args.gold_set:
        return _run_heldout(args)
    if args.task == TABLES_TASK:
        return _run_tables(args, keep=_gold_keep())
    gold_dir = args.gold or _default(GOLD_DIR)
    if not gold_dir.is_dir():
        return _error(f"gold dir not found: {gold_dir}")
    if message := heldout_cli.unselected(gold_dir):
        return _error(message)
    out = args.out or _default(EVAL_DIR / f"latest-{args.task}.json")
    if getattr(args, "notation", None):
        return _run_with_notation(args, gold_dir, out)
    return _run_pipeline(args, gold_dir, out, gold_label=_shown(gold_dir), keep=_gold_keep())


def _run_heldout(args: argparse.Namespace) -> int:
    """`eval pipeline --set heldout`: the pre-registered held-out sections, with the
    registered configuration and nothing else changed. The gold must be the registered
    one; its caches, the OpenStax archive responses and the reports stay outside the
    repository. Refused: `--fake` (a fake run is a run on these sections and is not
    allowed before the registered one, pre-registration section 10.1), `--notation` (no
    course notation is registered) and the tables task (not a task of this set)."""
    from readablestem.gold.heldout import REGISTERED

    if args.fake:
        return _error(
            "--fake is not allowed on the held-out set (a fake run still runs the layout and "
            "heading code on these sections; pre-registration, section 10.1). Check the "
            "plumbing on data/gold instead"
        )
    if args.task == TABLES_TASK:
        return _error("the tables task is not part of the held-out set")
    if args.notation:
        return _error("--notation is not part of the registered configuration")
    resolved = heldout_cli.resolve(
        args.heldout_dir,
        args.gold,
        args.cache,
        writes=(("--out", args.out), ("--items", args.items)),
    )
    if isinstance(resolved, str):
        return _error(resolved)
    loc, gold_dir, cache_dir = resolved
    out = args.out or loc.reports / f"{args.task}.json"
    return _run_pipeline(
        args,
        gold_dir,
        out,
        gold_label=f"heldout (pre-registered {REGISTERED.date})",
        extra={
            "set": "heldout",
            "preregistration_sha256": REGISTERED.preregistration_sha256,
        },
        cache_dir=cache_dir,
        openstax_cache=loc.openstax,
        keep=(loc.root, loc.root.name),
    )


def _run_devset(args: argparse.Namespace) -> int:
    """`eval pipeline --set dev-v2`: the extended development set (data/dev-v2). `--part
    dev` is the part to build against; `--part check` is the small sealed part, run for a
    pre-merge sanity check: its files and caches stay outside the repository, the report
    holds totals only (no `--items`) and goes outside the repository too. `--fake` is
    allowed on both. The tables task reads this set's tables (OpenStax and authored)."""
    from readablestem.cli import devset as devset_cli
    from readablestem.eval.devset_media import DevSetMedia

    if args.notation:
        return _error("--notation is not part of --set dev-v2")
    if args.part == "check" and args.items:
        return _error("--items would list the sealed check part item by item; not allowed")
    resolved = devset_cli.resolve_eval(
        args.part,
        args.devset_dir,
        args.gold,
        args.cache,
        writes=(("--out", args.out),),
    )
    if isinstance(resolved, str):
        return _error(resolved)
    gold_dir, cache_dir, openstax_cache, extra = resolved
    out = devset_cli.report_path(args.part, args.task, args.out)
    if args.task == TABLES_TASK:
        # The authored documents' tables sit beside the OpenStax pages' (authored/index.json).
        authored = gold_dir / "authored"
        keep = None if args.part == "check" else (devset_cli.cache_home(), "dev-v2")
        return _run_tables(
            args, devset=(gold_dir, authored, cache_dir, openstax_cache, out, extra), keep=keep
        )
    return _run_pipeline(
        args,
        gold_dir,
        out,
        gold_label=f"dev-v2 ({args.part})",
        extra=extra,
        cache_dir=cache_dir,
        openstax_cache=openstax_cache,
        media_cls=DevSetMedia,
        keep=None if args.part == "check" else (devset_cli.cache_home(), "dev-v2"),
    )


def _run_with_notation(args: argparse.Namespace, gold_dir: Path, out: Path) -> int:
    """The equations task with a course notation in force (FEATURE_NOTATION_MEMORY), as a
    conversion for that course would run it: `--notation "bold d" "v=other"` (a symbol,
    then `=meaning`, vector by default). The report records the entries."""
    from readablestem.core.notation import canonical_symbol
    from readablestem.core.types import ConversionOptions, NotationEntry
    from readablestem.pipeline import options as run_options

    if args.task != "equations":
        return _error("--notation applies to the equations task only")
    meanings = {"vector", "matrix", "set", "operator", "constant", "unit", "other"}
    entries = []
    for spec in args.notation:
        symbol, _, meaning = spec.rpartition("=") if "=" in spec else (spec, "", "vector")
        key = canonical_symbol(symbol)
        if key is None or meaning not in meanings:
            return _error(f'not a notation entry: {spec!r} (e.g. "bold d" or "v=other")')
        entry = {"symbol": symbol, "canonical": key, "meaning": meaning}
        entries.append(NotationEntry.model_validate(entry))
    options = ConversionOptions.from_settings(_settings()).model_copy(
        update={"notation": entries, "notation_memory": True}
    )
    extra = {"notation": [{"canonical": e.canonical, "meaning": e.meaning} for e in entries]}
    with run_options.using(options):
        return _run_pipeline(
            args, gold_dir, out, gold_label=_shown(gold_dir), extra=extra, keep=_gold_keep()
        )


def _cmd_sample(args: argparse.Namespace) -> int:
    """The weekly sample (eval.sample): a seeded share of the gold set, scored as usual."""
    import tempfile

    from readablestem.eval.sample import iso_week, sample_gold

    gold_dir = args.gold or _default(GOLD_DIR)
    if not gold_dir.is_dir():
        return _error(f"gold dir not found: {gold_dir}")
    if message := heldout_cli.unselected(gold_dir):
        return _error(message)
    week = args.week or iso_week(args.date_value)
    out = args.out or _default(WEEKLY_DIR / f"{week}-{args.task}.json")
    with tempfile.TemporaryDirectory(prefix="readablestem-sample-") as tmp:
        try:
            sample = sample_gold(
                gold_dir, Path(tmp), task=args.task, fraction=args.fraction, seed=week
            )
        except ValueError as exc:
            return _error(str(exc))
        if not sample["units_sampled"]:
            return _error(f"the gold set has nothing for task {args.task}")
        print(
            f"weekly sample {week}: {sample['units_sampled']} of {sample['units_total']} "
            f"{args.task} units ({args.fraction:.0%})"
        )
        label = f"{_shown(gold_dir)} (sample {week}, {args.fraction:.0%})"
        return _run_pipeline(
            args, Path(tmp), out, gold_label=label, extra={"sample": sample}, keep=_gold_keep()
        )


def _run_pipeline(
    args: argparse.Namespace,
    gold_dir: Path,
    out: Path,
    *,
    gold_label: str,
    extra: dict | None = None,
    cache_dir: Path | None = None,
    openstax_cache: Path | None = None,
    media_cls: type | None = None,
    keep: tuple[Path, str] | None = None,
) -> int:
    """One pipeline task. `keep` is where a live run leaves its recording (`_recording`)."""
    with _recording(args, keep) as rec:
        return _run_pipeline_in(
            args,
            gold_dir,
            out,
            gold_label=gold_label,
            extra=extra,
            cache_dir=cache_dir,
            openstax_cache=openstax_cache,
            media_cls=media_cls,
            rec=rec,
        )


def _run_pipeline_in(
    args: argparse.Namespace,
    gold_dir: Path,
    out: Path,
    *,
    gold_label: str,
    extra: dict | None,
    cache_dir: Path | None,
    openstax_cache: Path | None,
    media_cls: type | None,
    rec: Any,
) -> int:
    from readablestem.eval import pipeline_eval as pe
    from readablestem.eval.render import RenderError
    from readablestem.gold.openstax import HttpFetcher

    settings = _settings_for(args)
    if isinstance(settings, str):
        return _error(settings)
    made = _make_client(args, settings)
    if isinstance(made, str):
        return _error(made)
    client, budget, label = made
    judge = None
    if args.task == "figures":  # the only task with a judge
        judge = _make_judge(args, settings, client, budget, label=label)
        if isinstance(judge, str):
            return _error(judge)
    elif args.judge_provider or args.judge_model:
        return _error(
            f"--judge-provider and --judge-model apply to the figures task, not {args.task}"
        )
    fetcher = HttpFetcher(cache_dir=openstax_cache or _default(OPENSTAX_CACHE))
    with (media_cls or pe.CachedMedia)(
        gold_dir, cache_dir or args.cache or _default(EVAL_CACHE), fetcher, paper=args.paper
    ) as inner_media:
        media = inner_media
        if getattr(args, "scanned", False):  # the pages as scans (ADR 0007, eval/scan.py)
            from readablestem.eval.scan_media import ScannedMedia

            media = ScannedMedia(inner_media, args.scan_seed)
        config = pe.EvalConfig(
            task=args.task,
            settings=settings,
            client=client,
            media=media,
            date=args.date,
            gold_dir=gold_dir,
            limit=getattr(args, "limit", None),
            every=getattr(args, "every", None),
            sections=tuple(getattr(args, "sections", None) or ()),
            client_label=label,
            gold_label=gold_label,
            concurrency=args.concurrency,
            section_workers=getattr(args, "section_workers", 1),
            budget=budget,
            judge=judge,
            scorer_gaps=pe.scorer_gap_ids(pe.load_gold(gold_dir), _default(SCORER_CHECK))
            if args.task in {"equations", "document"}
            else frozenset(),
        )
        try:
            if getattr(args, "batch", False):
                from readablestem.eval import staged

                def one_pass() -> Any:
                    client.reset()
                    return pe.run_pipeline_eval(config)

                run = staged.drive(one_pass, args.staged_pool, args.staged_runner)
            else:
                run = pe.run_pipeline_eval(config)
        except RenderError as exc:  # raised while building inputs, before any model call
            return _error(f"could not build the eval input (no model calls made): {exc}")
        except staged_error() as exc:
            return _error(str(exc))
        except NotImplementedError as exc:
            return _error(f"a pipeline module is still a stub ({exc}); integrate it first")
    run.report.update(extra or {})
    if getattr(args, "speech", False):
        speech = _speech_block(args.task, run.items, gold_dir)
        if speech is not None:
            run.report["speech"] = speech
    if hasattr(client, "summary"):
        run.report["answers_file"] = client.summary()
    _finish_recording(rec, args, run.report)
    pe.write_report(run.report, out)
    if args.items:
        pe.write_items(run.items, args.items)
    print(pe.format_markdown(run.report), end="")
    if "speech" in run.report:
        print(_speech_lines(run.report["speech"]), end="")
    print(f"\nwrote {_shown(out)}" + (f" and {_shown(args.items)}" if args.items else ""))
    _copy_report(rec, run.report)
    if hasattr(client, "summary"):
        print(f"answers file: {dict(client.summary())}")
    return 0


def _speech_block(task: str, items: Any, gold_dir: Path) -> dict | None:
    """`--speech`: the share of equations whose speech equals the gold's under MathCAT and
    Speech Rule Engine (eval.speech_oracle). Equations and document tasks only; a note, and a
    report block that says so, when the engines are not built."""
    from readablestem.eval import speech_oracle

    if task not in {"equations", "document"}:
        print(f"--speech applies to the equations and document tasks, not {task}; skipped")
        return None
    block = speech_oracle.report_for_run(task, items, gold_dir)
    if block.get("skipped"):
        print(block["skipped"])
    return block


def _speech_lines(block: dict) -> str:
    if "engines" not in block:
        return ""
    lines = ["\nSpeech equal to the gold's (no proposal counts as different):"]
    for engine, v in block["engines"].items():
        lines.append(f"- {engine}: {v['equal']} / {v['n']}")
    return "\n".join(lines) + "\n"


def staged_error() -> type[Exception]:
    from readablestem.eval.staged import StageError

    return StageError


def _run_tables(
    args: argparse.Namespace,
    *,
    devset: tuple[Path, Path, Path | None, Path | None, Path, dict] | None = None,
    keep: tuple[Path, str] | None = None,
) -> int:
    """`eval pipeline --task tables`: local mode needs no model and no spend cap; model
    mode needs `--max-usd` (or `--fake`) like every task that calls a model. `devset` is
    (gold dir, authored dir, render cache, archive cache, report, report extras) of
    `--set dev-v2`. `keep`: where a model-mode run leaves its recording (`_recording`)."""
    with _recording(args, keep, model_run=args.mode == "model") as rec:
        return _run_tables_in(args, devset, rec)


def _run_tables_in(
    args: argparse.Namespace,
    devset: tuple[Path, Path, Path | None, Path | None, Path, dict] | None,
    rec: Any,
) -> int:
    from readablestem.eval import tables
    from readablestem.eval.render import RenderError
    from readablestem.gold.openstax import HttpFetcher

    gold_dir = devset[0] if devset else args.gold or _default(TABLE_GOLD_DIR)
    if not gold_dir.is_dir():
        return _error(f"gold dir not found: {gold_dir} (run: readablestem gold fetch --set tables)")
    styles = tuple(s.strip() for s in args.styles.split(",") if s.strip())
    unknown = [s for s in styles if s not in tables.TABLE_STYLES]
    if unknown or not styles:
        return _error(f"--styles takes {', '.join(tables.TABLE_STYLES)}; got {args.styles!r}")
    settings = _settings_for(args)
    if isinstance(settings, str):
        return _error(settings)
    client = budget = None
    label = "none"
    if args.mode == "model":
        made = _make_client(args, settings)
        if isinstance(made, str):
            return _error(made)
        client, budget, label = made
    authored = None if args.no_authored else _default(AUTHORED_TABLES)
    real_dir = args.real_dir or _default(PRIVATE_TABLES)
    if args.real_dir is not None and not (args.real_dir / tables.LABELS_FILE).is_file():
        return _error(f"no {tables.LABELS_FILE} in {args.real_dir}")
    out = args.out or _default(EVAL_DIR / f"latest-tables-{args.mode}.json")
    cache_dir = args.cache or _default(EVAL_CACHE)
    openstax_cache = _default(OPENSTAX_CACHE)
    extra: dict = {}
    if devset:
        gold_dir, authored, render_cache, archive_cache, out, extra = devset
        cache_dir = render_cache or cache_dir
        openstax_cache = archive_cache or openstax_cache
        real_dir = Path(
            "/nonexistent-private-inputs"
        )  # the private documents are not part of this set
    fetcher = HttpFetcher(cache_dir=openstax_cache)
    with tables.CachedTableMedia(gold_dir, cache_dir, fetcher, paper=args.paper) as media:
        config = tables.TablesConfig(
            settings=settings,
            media=media,
            date=args.date,
            gold_dir=gold_dir,
            authored_dir=authored,
            mode=args.mode,
            client=client,
            styles=styles,
            limit=args.limit,
            client_label=label,
            gold_label=f"dev-v2 ({args.part})" if devset else _shown(gold_dir),
            concurrency=args.concurrency,
            budget=budget,
            real_dir=real_dir if (real_dir / tables.LABELS_FILE).is_file() else None,
        )
        try:
            run = tables.run_tables_eval(config)
        except RenderError as exc:  # raised while printing inputs, before any model call
            return _error(f"could not build the eval input (no model calls made): {exc}")
    from readablestem.eval import pipeline_eval as pe

    run.report.update(extra)
    _finish_recording(rec, args, run.report)
    pe.write_report(run.report, out)
    if args.items:
        pe.write_items(run.items, args.items)
    print(tables.format_markdown(run.report), end="")
    print(f"\nwrote {_shown(out)}" + (f" and {_shown(args.items)}" if args.items else ""))
    _copy_report(rec, run.report)
    return 0


# ======================================================================== classes


def _cmd_classes(args: argparse.Namespace) -> int:
    """`eval classes --set dev-v2`: the scorers for the dev-v2 classes that have gold but
    no equation, heading or table scorer (prose scripts, code blocks, glossary entries,
    hyphenated line ends; `eval.prose_classes`). `--self-check` scores HTML rendered from
    the gold itself (every metric must be 1.0); `--html-dir` scores converted pages saved
    as `<book>__<page>.html`. The document task reports the same under `prose_classes`."""
    from readablestem.cli import devset as devset_cli
    from readablestem.eval import prose_classes
    from readablestem.eval.pipeline_eval import group_pages, load_gold

    if args.gold_set != "dev-v2":
        return _error("eval classes works on the extended development set: add --set dev-v2")
    if args.part != "dev":
        return _error("eval classes --part check is not available (item by item output)")
    found = devset_cli.resolve_eval("dev", args.devset_dir, None, None)
    if isinstance(found, str):
        return _error(found)
    records = [r for r in load_gold(found[0]) if r.get("kind") in prose_classes.KINDS]
    if args.self_check:
        summary = prose_classes.self_check(records)
    else:
        if not args.html_dir.is_dir():
            return _error(f"html dir not found: {args.html_dir}")
        items = []
        for page in group_pages(records):
            name = f"{page[0]['book_slug']}__{page[0]['page_slug']}.html"
            path = args.html_dir / name
            html = path.read_text(encoding="utf-8") if path.is_file() else ""
            items.append(prose_classes.score_page(html, page))
        summary = prose_classes.summarize(items)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


# =========================================================================== prune


def _cmd_prune(args: argparse.Namespace) -> int:
    """`eval prune --older-than DAYS`: delete kept runs (eval/run_record.py) older than the age,
    except a run a committed report names under `recording.run`."""
    from readablestem.cli.main import project_root
    from readablestem.core.paths import cache_dir
    from readablestem.eval import run_record

    base = cache_dir()
    names = (
        args.set or sorted(p.name for p in base.iterdir() if (p / run_record.RUNS).is_dir())
        if base.is_dir()
        else []
    )
    referenced = run_record.referenced_runs(project_root())
    total = 0
    for name in names:
        deleted, kept = run_record.prune(
            run_record.set_root(name),
            name,
            older_than_days=args.older_than,
            keep=referenced,
            dry_run=args.dry_run,
        )
        total += len(deleted)
        verb = "would delete" if args.dry_run else "deleted"
        for path in deleted:
            print(f"{verb} {path}")
        for path in kept:
            print(f"kept (a committed report names it) {path}")
    print(f"{'would delete' if args.dry_run else 'deleted'} {total} run(s)")
    return 0


# ============================================================================ gate


def _cmd_gate(args: argparse.Namespace) -> int:
    from readablestem.eval.gate import run_gate

    for path in (args.current, args.baseline):
        if path is not None and not path.is_file():
            return _error(f"report not found: {path}")
    tolerances = args.tolerance_file
    if tolerances is None and _default(EVAL_DIR / "tolerances.json").is_file():
        tolerances = _default(EVAL_DIR / "tolerances.json")
    try:
        return run_gate(
            args.current, args.baseline, tolerances, require_current=args.require_current
        )
    except (ValueError, KeyError) as exc:  # malformed JSON or tolerance file
        return _error(f"cannot read reports or tolerances: {exc}")


def _cmd_bar(args: argparse.Namespace) -> int:
    """`eval bar REPORT --measure NAME --at-least X`: hold one number of a report against a
    bar. Only a held-out score under the production profile can meet one (eval/split.py)."""
    from readablestem.eval import split

    if not args.report.is_file():
        return _error(f"report not found: {args.report}")
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except ValueError as exc:
        return _error(f"cannot read {args.report}: {exc}")
    verdict = split.judge(
        report, args.measure, at_least=args.at_least, at_most=args.at_most, kind=args.kind
    )
    print(verdict.line)
    return verdict.exit_code


def _cmd_ledger(args: argparse.Namespace) -> int:
    """`eval ledger`: every row of the quality ledger, from the records the evals keep, each
    figure with its counts, a bound and a label, held against its two bars (`eval.ledger`).
    Measures nothing and spends nothing. Exit code 0: no held-out figure is worse than its
    release bar; 1: one is, or `--for-release` and a row with a release bar has no held-out
    figure of this release; 2: refused."""
    from readablestem.config import Settings
    from readablestem.eval import corpus, ledger, run_record
    from readablestem.ops import profile

    folder: Path = args.records or _default(Path("data/eval"))
    bars_path: Path = args.bars or folder / ledger.BARS_FILE
    try:
        production = profile.load()
    except (OSError, ValueError) as exc:
        return _error(f"refused: no production profile to name the release by ({exc})")
    # The profile's own settings, so the stamp also names the switches this image counts off.
    settings = production.apply(Settings(_env_file=None, env="test"))  # type: ignore[call-arg]
    sha, dirty = run_record.source_state(corpus.REPO)
    stamps = ledger.stamps(profile.stamp(settings, production), sha + ("+changes" if dirty else ""))
    release = f"{stamps['pipeline_version']}+{production.sha256[:12]}"
    try:
        bars = ledger.read_bars(bars_path)
        made = ledger.build(ledger.read_records(folder), bars, release=release, stamps=stamps)
        if args.register_bars or args.replace_bar:
            bars, written = ledger.register(made, bars, replace=args.replace_bar)
            if written:
                _write_record(bars_path, {"bars": bars})
                made = ledger.build(
                    ledger.read_records(folder), bars, release=release, stamps=stamps
                )
            print(
                "release bars written: " + (", ".join(written) or "none"),
                file=sys.stderr,
            )
    except ledger.Refused as exc:
        return _error(f"refused: {exc}")
    failed = ledger.failures(made, for_release=args.for_release)
    _write_record(args.out, made)
    print(ledger.render(made, failed))
    return 1 if failed else 0


def _measured(
    args: argparse.Namespace, runs: int, *, outputs: bool = False
) -> tuple[list[list[Any]], Any] | int:
    """The measured replays `eval cost` and `eval speed` report on: read from `--from`, or
    made now (`eval.measure`) and written to `--records`. `outputs`: each conversion also
    builds and audits the files a student is given."""
    from readablestem.eval import corpus, measure

    settings = measure.replay_settings(corpus.ANSWERS)
    if args.records_from is not None:
        if not args.records_from.is_file():
            return _error(f"records not found: {args.records_from}")
        try:
            kept = json.loads(args.records_from.read_text(encoding="utf-8"))
            made = [[measure.Converted.from_dict(row) for row in run] for run in kept["runs"]]
        except (ValueError, KeyError, TypeError) as exc:
            return _error(f"cannot read {args.records_from}: {exc}")
        if not made:
            return _error(f"{args.records_from}: no run in it")
        known = {upload.name for upload in measure.every_upload()}
        unknown = sorted({done.upload for run in made for done in run} - known)
        if unknown:
            return _error(f"{args.records_from}: not uploads of the corpus: {', '.join(unknown)}")
        return made, settings
    uploads = measure.every_upload()
    if args.only:
        unknown = sorted(set(args.only) - {upload.entry for upload in uploads})
        if unknown:
            return _error(f"not in the corpus: {', '.join(unknown)}")
        uploads = [upload for upload in uploads if upload.entry in args.only]
    cores = [part for part in (args.cores or "").split("/") if part]
    made = []
    for number in range(runs):

        def said(done: Any, number: int = number) -> None:
            ended = done.error or f"{done.pages} pages, {done.wall_s:.1f} s, ${done.usd:.4f}"
            print(f"run {number + 1}: {done.upload}: {ended}", file=sys.stderr, flush=True)

        made.append(
            measure.run(
                uploads,
                at_once=args.at_once,
                cores=cores[number % len(cores)] if cores else None,
                said=said,
                outputs=outputs,
            )
        )
    if args.records is not None:
        args.records.parent.mkdir(parents=True, exist_ok=True)
        kept = {"runs": [[done.as_dict() for done in run] for run in made]}
        args.records.write_text(json.dumps(kept), encoding="utf-8")
    return made, settings


def _add_measure_args(parser: argparse.ArgumentParser, *, at_once: int) -> None:
    parser.add_argument(
        "--only", action="append", metavar="ID", help="a corpus file to measure (repeatable)"
    )
    parser.add_argument(
        "--at-once",
        type=_positive_int,
        default=at_once,
        help=f"uploads converted at the same time (default {at_once})",
    )
    parser.add_argument(
        "--cores", help="CPU list to pin a run to; a list per run, separated by / (0-3/4-7)"
    )
    parser.add_argument("--records", type=Path, help="write what was measured, to report again")
    parser.add_argument(
        "--from",
        dest="records_from",
        type=Path,
        help="report on records written by --records and measure nothing",
    )
    parser.add_argument("--out", type=Path, help="write the report as JSON")


def _write_record(path: Path | None, record: dict[str, Any]) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")


def _cmd_cost(args: argparse.Namespace) -> int:
    """`eval cost`: dollars a page by page kind and by step, from a replay of the corpus
    under the production profile (`eval.cost`). Spends nothing."""
    from readablestem.eval import cost, measure

    measured = _measured(args, runs=1)
    if isinstance(measured, int):
        return measured
    (run,), settings = measured[0][:1], measured[1]
    metrics = cost.report(run, open_pool_usd=settings.open_tier_daily_spend_cap_usd)
    record = measure.record(run, settings, metrics)
    _write_record(args.out, record)
    print(cost.render(record))
    return 0 if metrics["uploads"]["measured"] else _error("no upload could be measured")


def _cmd_speed(args: argparse.Namespace) -> int:
    """`eval speed`: seconds a page by page kind and by stage, from replays of the corpus
    under the production profile, and the bar against an earlier record (`eval.speed`).
    Exit code 0: the bar is met or none was asked; 1: a page kind is slower than allowed;
    2: the two records cannot be compared."""
    from readablestem.eval import measure, speed

    before = None
    if args.against is not None:
        try:
            before = json.loads(args.against.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return _error(f"cannot read {args.against}: {exc}")
    measured = _measured(args, runs=args.runs)
    if isinstance(measured, int):
        return measured
    runs, settings = measured
    metrics = speed.report(runs, allowance=args.allowance)
    record = measure.record(speed.fastest(runs), settings, metrics)
    _write_record(args.out, record)
    verdict = None if before is None else speed.judge(record, before, allowance=args.allowance)
    print(speed.render(record, verdict))
    if not metrics["uploads"]["measured"]:
        return _error("no upload could be measured")
    if verdict is None or verdict.met:
        return 0
    return 1 if verdict.judged else 2


def _cmd_burden(args: argparse.Namespace) -> int:
    """`eval burden`: decisions a page asks of a reviewer, by page kind, by what is decided
    and by why it was asked, from a replay of the corpus under the production profile, and
    the bar against an earlier record (`eval.burden`). Spends nothing. Exit code 0: the bar
    is met or none was asked; 1: a page kind asks for more than before (with `--same`: an
    upload asks for other decisions than the record has); 2: the two records cannot be
    compared."""
    from readablestem.eval import burden, measure

    before = None
    if args.against is not None:
        try:
            before = json.loads(args.against.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return _error(f"cannot read {args.against}: {exc}")
    measured = _measured(args, runs=1)
    if isinstance(measured, int):
        return measured
    (run,), settings = measured[0][:1], measured[1]
    metrics = burden.report(run)
    record = measure.record(run, settings, metrics)
    _write_record(args.out, record)
    verdict = (
        None
        if before is None
        else burden.judge(record, before, allowance=args.allowance, same=args.same)
    )
    print(burden.render(record, verdict))
    if not metrics["uploads"]["measured"]:
        return _error("no upload could be measured")
    if verdict is None or verdict.met:
        return 0
    return 1 if verdict.judged else 2


def _scored_set(args: argparse.Namespace) -> tuple[list[Any], dict[str, Any]] | int:
    """`eval order --set`: the documents of a layout gold set scored (or read from
    `--from`), and the set's registry; an exit code when it cannot be done. The folder must
    be the registered one, and a held-out set is scored only when the caller says the
    score is for a release."""
    from readablestem.cli import layout_gold as sets
    from readablestem.eval import measure, order_report
    from readablestem.gold import layout_gold

    listed = sets.registry_path(args.set, args.registry)
    root = sets.set_root(args.set, args.root)
    if not listed.is_file():
        return _error(f"no registry at {listed}")
    registry = json.loads(listed.read_text(encoding="utf-8"))
    if registry["side"] == "held-out":
        if not args.for_release:
            return _error(
                f"{args.set} is a held-out set: it is scored for a release, once, and a score "
                "read while the reader is being changed spends it (--for-release)"
            )
        if args.against is not None:
            return _error("a held-out set is held to its release bar, not compared page by page")
        if args.records is not None or args.records_from is not None:
            return _error(
                "a held-out set's pages are not kept: --records and --from are not for it"
            )
    known = {row["id"] for row in registry["documents"]}
    if args.records_from is not None:
        try:
            kept = json.loads(args.records_from.read_text(encoding="utf-8"))
            run = [measure.Converted.from_dict(row) for row in kept["runs"][0]]
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            return _error(f"cannot read {args.records_from}: {exc}")
        unknown = sorted({done.upload for done in run} - known)
        if unknown:
            return _error(f"{args.records_from}: not documents of {args.set}: {', '.join(unknown)}")
        return run, registry
    if not (root / "gold").is_dir():
        return _error(f"no set at {root} (a folder with files/ and gold/)")
    problems = layout_gold.verify_set(root, registry)
    if problems:
        return _error(f"{root} is not the registered set: " + "; ".join(problems[:5]))
    unknown = sorted(set(args.only or ()) - known)
    if unknown:
        return _error(f"not in {args.set}: {', '.join(unknown)}")

    def said(done: Any) -> None:
        print(f"{done.upload}: {done.error or f'{done.pages} pages'}", file=sys.stderr, flush=True)

    run = order_report.set_run(root, only=args.only or (), at_once=args.at_once, said=said)
    if args.records is not None:
        args.records.parent.mkdir(parents=True, exist_ok=True)
        kept = {"runs": [[done.as_dict() for done in run]]}
        args.records.write_text(json.dumps(kept), encoding="utf-8")
    return run, registry


def _cmd_parsing_bench(args: argparse.Namespace) -> int:
    """`eval parsing-bench`: the reader on the categories of olmOCR-Bench that need no model,
    scored by the benchmark's own checker (`eval.olmocr_bench`). Spends nothing. The
    benchmark is fetched outside the repository (`--fetch`) and a run keeps one file per page
    there; `--run-dir` resumes or repeats a run from them."""
    from readablestem.eval import olmocr_bench

    folder = args.folder or olmocr_bench.default_folder()
    if args.fetch:
        olmocr_bench.fetch(folder, said=lambda line: print(line, file=sys.stderr, flush=True))
    if not (folder / "bench_data").is_dir():
        return _error(f"no benchmark at {folder}: run with --fetch")
    run_dir = args.run_dir or olmocr_bench.new_run_dir(folder)

    def said(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    try:
        record = olmocr_bench.run(
            folder,
            run_dir,
            at_once=args.at_once,
            only=args.only,
            limit=args.limit,
            said=said,
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        return _error(f"parsing-bench: {exc}")
    _write_record(args.out, record)
    print(olmocr_bench.render(record))
    return 0


def _cmd_order(args: argparse.Namespace) -> int:
    """`eval order`: whether each gold page of the corpus is read in order, and what else
    is wrong on a page that is, in the page a student is given and in what the pipeline
    holds, from a replay under the production profile, and the bar against an earlier
    record (`eval.order_report`). With `--set`, the pages of a layout gold set, each file
    converted with no model. Spends nothing. Exit code 0: the bar is met or none was
    asked; 1: a page that was right is not right now; 2: the two records cannot be
    compared, or nothing could be measured."""
    from readablestem.eval import corpus, measure, order_report

    before = None
    if args.against is not None:
        try:
            before = json.loads(args.against.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return _error(f"cannot read {args.against}: {exc}")
    if args.set is not None:
        scored = _scored_set(args)
        if isinstance(scored, int):
            return scored
        run, registry = scored
        settings = order_report.set_settings()
        held_out = registry["side"] == "held-out"
        metrics = order_report.report(run, order_report.set_groups(registry), listed=not held_out)
        of_set = order_report.set_stamp(registry, [done.upload for done in run])
        record = measure.record(run, settings, metrics, of_set=of_set | {"client": "none"})
    else:
        if not (args.only or args.records_from):
            with_gold = (entry.id for entry in corpus.load_manifest().values() if entry.gold)
            args.only = sorted(with_gold)
        measured = _measured(args, runs=1)
        if isinstance(measured, int):
            return measured
        (run,), settings = measured[0][:1], measured[1]
        metrics = order_report.report(run)
        record = measure.record(run, settings, metrics)
    _write_record(args.out, record)
    verdict = None if before is None else order_report.judge(record, before)
    print(order_report.render(record, verdict))
    if not metrics["uploads"]["measured"] and not metrics["uploads"]["answers_missing"]:
        return _error("no upload could be measured")
    if verdict is None or verdict.met:
        return 0
    return 1 if verdict.judged else 2


def _cmd_outputs(args: argparse.Namespace) -> int:
    """`eval outputs`: every file a student is given (the page, Word, EPUB, the two Canvas
    files, the Cartridge and the Listen transcript), built from a replay of the corpus under
    the production profile, read and counted (`eval.outputs`). Spends nothing and gates
    nothing. Exit code 0: measured; 2: nothing could be measured."""
    from readablestem.eval import measure, outputs

    measured = _measured(args, runs=1, outputs=True)
    if isinstance(measured, int):
        return measured
    (run,), settings = measured[0][:1], measured[1]
    metrics = outputs.report(run)
    record = measure.record(run, settings, metrics)
    _write_record(args.out, record)
    print(outputs.render(record))
    if not metrics["whole"]["uploads"] and not metrics["answers_missing"]["uploads"]:
        return _error("no upload could be measured")
    return 0


def _cmd_pictures(args: argparse.Namespace) -> int:
    """`eval pictures`: A18's count over a replay of the corpus (the same replay as `eval
    outputs`): the pictures not converted by reason, the content pictures with empty alt, the
    regions called handwriting against the truth list (`eval.pictures`). Spends nothing and
    gates nothing. Exit code 0: measured; 2: nothing could be counted."""
    from readablestem.eval import measure, pictures

    path = args.truth or pictures.TRUTH
    try:
        truth = pictures.read_truth(path) if path.is_file() else []
    except (ValueError, KeyError, TypeError) as exc:
        return _error(f"cannot read {path}: {exc}")
    measured = _measured(args, runs=1, outputs=True)
    if isinstance(measured, int):
        return measured
    (run,), settings = measured[0][:1], measured[1]
    metrics = pictures.report(run, truth)
    record = measure.record(run, settings, metrics)
    _write_record(args.out, record)
    print(pictures.render(record))
    if not metrics["uploads"]["counted"]:
        return _error("no upload could be counted (a record kept before `eval pictures` has none)")
    return 0


def _cmd_say_once(args: argparse.Namespace) -> int:
    """`eval say-once`: the share of each page's words that no element says, and that are
    said twice, over the corpus, read by layout with no model (`eval.say_once_report`).
    Spends nothing and gates nothing. Exit code 0: measured; 2: nothing could be measured."""
    from readablestem.eval import corpus, measure, say_once_report

    settings = measure.replay_settings(corpus.ANSWERS)
    uploads = measure.every_upload()
    if args.only:
        unknown = sorted(set(args.only) - {upload.entry for upload in uploads})
        if unknown:
            return _error(f"not in the corpus: {', '.join(unknown)}")
        uploads = [upload for upload in uploads if upload.entry in args.only]

    def said(done: Any) -> None:
        print(f"{done.upload}: {done.error or f'{done.pages} pages'}", file=sys.stderr, flush=True)

    run = say_once_report.run(uploads, at_once=args.at_once, said=said)
    metrics = say_once_report.report(run)
    record = measure.record(run, settings, metrics)
    _write_record(args.out, record)
    print(say_once_report.render(record))
    if not metrics["uploads"]["measured"]:
        return _error("no upload could be measured")
    return 0


def _cmd_page_audit(args: argparse.Namespace) -> int:
    """`eval page-audit`: the page audit in shadow on the corpus or on a layout gold set's
    folder (`eval.page_audit_eval`). It writes verdicts and changes no output. Exit code 0:
    every page has a verdict; 1: the spend cap ended the run early (the report says where);
    2: the run could not start."""
    import tempfile

    from readablestem.eval import page_audit_eval as audit_eval
    from readablestem.eval import pipeline_eval as pe
    from readablestem.eval import run_record, staged
    from readablestem.eval.render import ChromiumRenderer, RenderError

    live = not (args.fake or args.answers_offline)
    if args.answers_offline and not args.answers:
        return _error("--answers-offline needs --answers FILE")
    if args.batch and not live:
        return _error("--batch needs real model calls: no --fake and no --answers-offline")
    settings = _settings_for(args)
    if isinstance(settings, str):
        return _error(settings)
    if live and settings.model_provider != "anthropic":
        return _error("the page audit asks an Anthropic model: pass --provider anthropic")
    root: Path | None = args.gold_dir
    if root is not None and not (root / "files").is_dir():
        return _error(f"{root} is not a layout gold set's folder (no files/ in it)")
    name = "corpus" if root is None else root.name
    keep = (run_record.set_root("corpus"), "corpus") if root is None else (root, name)
    default_out = (
        _default(Path("data/eval/latest-page-audit.json"))
        if root is None
        else root / "page-audit.json"  # a held-out set's rows stay with the set
    )
    out: Path = args.out or default_out
    with _recording(args, keep) as rec:
        if args.answers_offline:
            from readablestem.eval.answers import CachedClient

            client: Any = CachedClient(None, args.answers, settings)
            label = "replay"
        else:
            made = _make_client(args, settings)
            if isinstance(made, str):
                return _error(made)
            client, _budget, label = made
        with tempfile.TemporaryDirectory() as scratch:
            documents = list(
                audit_eval.corpus_documents(Path(scratch), args.only)
                if root is None
                else audit_eval.gold_documents(root, settings, args.only)
            )[: args.limit]
        if not documents:
            return _error("no document to audit: the set is empty, or --only names none of it")
        renderer = ChromiumRenderer() if args.rendered else None

        def one_pass() -> Any:
            if hasattr(client, "reset"):
                client.reset()
            quiet = (lambda _line: None) if args.batch else print
            return audit_eval.audit_documents(
                documents, client, settings, renderer=renderer, say=quiet
            )

        try:
            if args.batch:
                run = staged.drive(one_pass, args.staged_pool, args.staged_runner)
            else:
                run = one_pass()
        except RenderError as exc:
            return _error(f"could not draw a page's output: {exc}")
        except staged_error() as exc:
            return _error(str(exc))
        finally:
            if renderer is not None:
                renderer.close()
        found = audit_eval.report(
            run,
            set_name=name,
            settings=settings,
            date=args.date,
            client_label=label,
            with_output_picture=args.rendered,
        )
        if hasattr(client, "summary"):
            found["answers_file"] = dict(client.summary())
        _finish_recording(rec, args, found)
        pe.write_report(found, out)
        print(audit_eval.format_markdown(found), end="")
        print(f"wrote {_shown(out)}")
        _copy_report(rec, found)
    return 1 if run.stopped else 0


def _cmd_promote(args: argparse.Namespace) -> int:
    """Copy a reviewed report to data/eval/<baseline|latest>-<task>.json for the CI gate.

    Refuses a partial run, and (for `latest`) a report produced with other prompts or
    models than this code, which the gate would refuse anyway.
    """
    from readablestem.config import (
        ANTHROPIC_CHEAP_MODEL,
        ANTHROPIC_MAIN_MODEL,
        CHEAP_MODEL,
        MAIN_MODEL,
    )
    from readablestem.eval.gate import GateRefused, check_current, code_versions

    if not args.report.is_file():
        return _error(f"report not found: {args.report}")
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except ValueError as exc:
        return _error(f"cannot read {args.report}: {exc}")
    task = report.get("task")
    if task not in PIPELINE_TASKS:
        return _error(f"not an eval report (task {task!r})")
    if not report.get("complete", False):
        return _error("the report is partial; re-run with a higher --max-usd")
    # A tables run in local mode calls no model ("none"): it is a real measurement too.
    no_model = task == TABLES_TASK and report.get("mode") == "local"
    client = report.get("client")
    if client not in ("openai", "anthropic") and not (no_model and client == "none"):
        return _error("only real-model reports can be baselines (this one is a fake run)")
    if client == "anthropic" and args.out_dir is None:
        # The committed baselines in data/eval are the Claude runs the CI gate compares with.
        return _error(
            "a report made with the anthropic provider is not copied into data/eval unless you "
            "say where: give --out-dir (data/eval replaces the committed Claude baseline of the "
            "same name, on purpose)"
        )
    if args.kind == "latest":
        if client == "anthropic":
            models = {
                "main": ANTHROPIC_MAIN_MODEL,
                "cheap": ANTHROPIC_CHEAP_MODEL,
                "judge": ANTHROPIC_MAIN_MODEL,
            }
        else:
            models = {"main": MAIN_MODEL, "cheap": CHEAP_MODEL, "judge": MAIN_MODEL}
        try:
            check_current(report, code_versions(), models)
        except GateRefused as exc:
            return _error(str(exc))
    target = args.out_dir or _default(EVAL_DIR)
    name = f"{task}-{report.get('mode')}" if task == TABLES_TASK else task
    path = target / f"{args.kind}-{name}.json"
    _write_json(report, path)
    print(f"wrote {_shown(path)}; review and commit it")
    return 0


# ======================================================================= calibrate


def _cmd_calibrate(args: argparse.Namespace) -> int:
    from readablestem.eval import calibration as cal

    if args.probes:
        return _calibrate_probes(args)
    if args.judge_provider or args.judge_model:
        return _error("--judge-provider and --judge-model go with --probes (the calls it makes)")
    if args.write_template and not args.judge:
        return _error("--write-template needs --judge judged.jsonl (from eval pipeline)")
    if not args.write_template and not (args.judge and args.labels):
        return _error("give --judge and --labels, or --write-template, or --probes")
    try:
        judged = cal.load_judged(args.judge)
        if args.write_template:
            count = cal.write_label_template(judged.values(), args.write_template)
            print(f"wrote {count} rows to {args.write_template} (fill human_pass and notes)")
            return 0
        report = cal.agreement_report(
            judged, cal.load_labels(args.labels), n_boot=args.n_boot, seed=args.seed
        )
    except FileNotFoundError as exc:
        return _error(f"file not found: {exc.filename}")
    except ValueError as exc:
        return _error(str(exc))
    if args.out:
        _write_json(report, args.out)
    print(cal.format_agreement(report), end="")
    return 0


def _calibrate_probes(args: argparse.Namespace) -> int:
    with _recording(args, _gold_keep()) as rec:
        return _calibrate_probes_in(args, rec)


def _calibrate_probes_in(args: argparse.Namespace, rec: Any) -> int:
    from readablestem.eval import calibration as cal
    from readablestem.eval import run_record
    from readablestem.eval.pipeline_eval import load_gold, write_items

    gold_dir = args.gold or _default(GOLD_DIR)
    if not gold_dir.is_dir():
        return _error(f"gold dir not found: {gold_dir}")
    if message := heldout_cli.unselected(gold_dir):
        return _error(message)
    settings = _settings_for(args)
    if isinstance(settings, str):
        return _error(settings)
    made = _make_client(args, settings)
    if isinstance(made, str):
        return _error(made)
    client, budget, label = made
    judge = _make_judge(args, settings, client, budget, label=label)
    if isinstance(judge, str):
        return _error(judge)
    paraphrases = cal.load_paraphrases(args.paraphrases) if args.paraphrases else None
    figures = cal.probe_figures(load_gold(gold_dir), args.limit)
    images = None
    # The figures eval judges with the image attached; probe the same instrument. A fake
    # client never looks at images, so a plumbing run does not download them.
    if not args.text_only and not args.fake:
        from readablestem.eval import render

        cache = render.ImageCache(_default(EVAL_CACHE) / "images")
        try:
            images = {f["id"]: cache.png(cal.probe_image_url(f)) for f in figures}
        except render.RenderError as exc:
            return _error(f"could not get a figure image (no model calls made): {exc}")
    report, items = cal.run_probes(
        figures,
        client=judge.client,
        settings=judge.settings,
        paraphrases=paraphrases,
        images=images,
    )
    report["judge"] = judge.describe()
    report["provenance"] = run_record.provenance()
    _finish_recording(rec, args, report)
    if args.out:
        _write_json(report, args.out)
    if args.items:
        write_items(items, args.items)
    print(cal.format_probes(report), end="")
    _copy_report(rec, report)
    return 0


def _write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _cmd_figures_spotcheck(args: argparse.Namespace) -> int:
    """Offline: draw the sheet a person reads (every long-judge fail, some passes), or score
    their labels against the key. Calls no model."""
    from readablestem.eval import figures_spotcheck as spot

    try:
        if args.items:
            if not args.sheet:
                return _error("--items needs --sheet (where to write the labelling sheet)")
            key_path = args.key or args.sheet.with_suffix(".key.json")
            counts = spot.write_sheet(
                spot.load_items(args.items),
                args.sheet,
                key_path,
                passes=args.passes,
                seed=args.seed,
            )
            print(
                f"wrote {counts['judge_fails'] + counts['passes_sampled']} rows to {args.sheet} "
                f"({counts['judge_fails']} judge fails, {counts['passes_sampled']} of "
                f"{counts['judge_passes']} passes; {counts['not_judged']} figures not graded) "
                f"and the key to {key_path}"
            )
            print("Fill human_accurate, human_complete, human_invented_values (yes/no).")
            return 0
        if args.key and args.labels:
            rep = spot.report(spot.load_key(args.key), spot.load_labels(args.labels))
            if args.out:
                _write_json(rep, args.out)
            print(spot.format_report(rep), end="")
            return 0
    except FileNotFoundError as exc:
        return _error(f"file not found: {exc.filename}")
    except (ValueError, json.JSONDecodeError) as exc:
        return _error(str(exc))
    return _error("give --items and --sheet, or --key and --labels")


# ========================================================================== render


def _page_source(book: str, slug: str, gold_dir: Path, fetcher: Any) -> Any:
    """Gold pages come from the cache; any other page is looked up (license-gated)."""
    from readablestem.eval import render
    from readablestem.eval.pipeline_eval import load_gold

    records = (
        [r for r in load_gold(gold_dir) if r["book_slug"] == book and r["page_slug"] == slug]
        if gold_dir.is_dir()
        else []
    )
    if records:
        authors = render.book_authors(gold_dir, book)
        return render.page_source_from_records(records, fetcher, authors=authors)
    return render.page_source_from_openstax(book, slug, fetcher)


def _cmd_render(args: argparse.Namespace) -> int:
    from readablestem.eval import render
    from readablestem.eval.pipeline_eval import load_gold
    from readablestem.gold.openstax import GoldError, HttpFetcher

    book = args.book or "physics"
    gold_dir = args.gold or _default(GOLD_DIR)
    cache = args.cache or _default(EVAL_CACHE)
    openstax_cache = _default(OPENSTAX_CACHE)
    if args.gold_set == "dev-v2":
        from readablestem.cli import devset as devset_cli

        if args.part != "dev" or args.all or args.out_dir:
            return _error("eval render --set dev-v2 prints pages of the dev part only")
        found = devset_cli.resolve_eval("dev", args.devset_dir, args.gold, args.cache)
        if isinstance(found, str):
            return _error(found)
        gold_dir = found[0]
    elif args.gold_set:
        from readablestem.gold.heldout import REGISTERED

        resolved = heldout_cli.resolve(
            args.heldout_dir,
            args.gold,
            args.cache,
            writes=(("--out", args.out), ("--out-dir", args.out_dir)),
        )
        if isinstance(resolved, str):
            return _error(resolved)
        loc, gold_dir, cache = resolved
        openstax_cache = loc.openstax
        if args.all:
            return _render_heldout_all(args, loc, gold_dir, cache)
        section = REGISTERED.section(args.page, args.book) if args.page else None
        if args.page and section is None:
            return _error(f"{args.page} is not one of the held-out sections (--set heldout)")
        if section is not None:
            book = section.book
    elif args.all or args.out_dir:
        return _error("--all and --out-dir belong to --set heldout")
    elif message := heldout_cli.unselected(gold_dir):
        return _error(message)
    if args.equations:
        if not gold_dir.is_dir():
            return _error(f"gold dir not found: {gold_dir}")
        records = [r for r in load_gold(gold_dir) if r.get("kind") == "equation"]
        records = records[: args.limit] if args.limit else records
        try:
            pngs = render.render_equations(records, cache / "equations")
        except render.RenderError as exc:
            return _error(str(exc))
        print(f"{len(pngs)} equation PNGs in {_shown(cache / 'equations')}")
        return 0
    if not args.page:
        return _error("give --page SLUG or --equations")
    fetcher = HttpFetcher(cache_dir=openstax_cache)
    out = args.out or cache / "pages" / f"{book}-{args.page}.pdf"
    try:
        page = _page_source(book, args.page, gold_dir, fetcher)
        render.render_page_pdf(
            page, out, images=render.ImageCache(cache / "images"), paper=args.paper
        )
    except (GoldError, render.RenderError) as exc:
        return _error(str(exc))
    print(f"wrote {_shown(out)}")
    return 0


def _render_heldout_all(
    args: argparse.Namespace, loc: heldout_cli.Locations, gold_dir: Path, cache: Path
) -> int:
    """`eval render --set heldout --all`: print the 15 sections, count their pages.

    The PDFs go into the render cache under the key the document task looks them up by
    (so a later run converts these very files) and, named `<book>-<page>.pdf`, into
    `--out-dir` (default: the held-out folder's pdfs/) with a `pages.json` of their page
    counts and SHA-256 values: content-free, and what a run report records. Exit 1 when a
    Letter print has another page count than the registered one (242 in all). The prints
    only count pages: nothing converts them.
    """
    import hashlib

    from readablestem.eval import render
    from readablestem.gold.heldout import REGISTERED
    from readablestem.gold.openstax import GoldError, HttpFetcher

    out_dir = args.out_dir or loc.pdfs
    fetcher = HttpFetcher(cache_dir=loc.openstax)
    images = render.ImageCache(cache / "images")
    rows: list[dict[str, Any]] = []
    try:
        with render.ChromiumRenderer() as browser:
            for section in REGISTERED.sections:
                page = _page_source(section.book, section.slug, gold_dir, fetcher)
                pdf = render.render_page_pdf(
                    page,
                    out_dir / f"{section.book}-{section.slug}.pdf",
                    images=images,
                    renderer=browser,
                    paper="A4" if args.paper == "A4" else "Letter",
                    cache_dir=cache / "pages",
                )
                rows.append(
                    {
                        "book": section.book,
                        "page": section.slug,
                        "pdf_pages": heldout_cli.pdf_page_count(pdf),
                        "registered_pdf_pages": section.pdf_pages,
                        "sha256": hashlib.sha256(pdf).hexdigest(),
                        "bytes": len(pdf),
                    }
                )
    except (GoldError, render.RenderError) as exc:
        return _error(str(exc))
    letter = args.paper == "Letter"
    differs = [r for r in rows if letter and r["pdf_pages"] != r["registered_pdf_pages"]]
    total = sum(r["pdf_pages"] for r in rows)
    summary = {
        "paper": args.paper,
        "total_pdf_pages": total,
        "registered_total_pdf_pages": REGISTERED.total_pdf_pages,
        "pages_match_registration": None if not letter else not differs,
        "sections": rows,
    }
    _write_json(summary, out_dir / "pages.json")
    for r in rows:
        mark = (
            "" if not letter or r not in differs else f"  (registered {r['registered_pdf_pages']})"
        )
        print(
            f"{r['book']:<24} {r['page']:<62} {r['pdf_pages']:>3} pages  {r['sha256'][:12]}{mark}"
        )
    print(f"{total} PDF pages in {len(rows)} sections (registered: {REGISTERED.total_pdf_pages})")
    print(f"wrote {out_dir}/<book>-<page>.pdf and {out_dir / 'pages.json'}")
    if differs:
        print(f"error: {len(differs)} section(s) print to another page count than registered")
        return 1
    return 0


# ============================================================================ demo


def build_demo(
    page: Any,
    *,
    settings: Any,
    client: Any,
    out_dir: Path,
    date: str,
    images: Any,
    client_label: str,
    paper: str = "Letter",
    renderer: Any = None,
) -> dict:
    """Print the page, convert it, write index.html (with a demo note) and report.json.

    Also writes source.pdf, the exact input, so the demo can show before and after.
    Returns report.json's content.
    """
    from readablestem.core.types import Status
    from readablestem.eval import render
    from readablestem.pipeline import run

    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = render.render_page_pdf(
        page,
        out_dir / "source.pdf",
        images=images,
        renderer=renderer,
        paper="A4" if paper == "A4" else "Letter",
    )
    output = run.convert_pdf(pdf, settings=settings, client=client)
    result = output.result
    html = render.wrap_demo_html(
        result.html,
        page,
        date=date,
        source_url=getattr(settings, "source_url", None),
        placeholder=client_label == "fake",
    )
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    not_attempted = sum(
        1
        for f in result.fixes
        if f.status == Status.FAILED and (f.reason or "").startswith("budget")
    )
    report = {
        "demo": {
            "date": date,
            "client": client_label,
            "book_slug": page.book_slug,
            "book_title": page.book_title,
            "page_slug": page.page_slug,
            "page_title": page.page_title,
            "page_url": page.page_url,
            "license_name": page.license_name,
            "license_url": page.license_url,
            "authors": page.authors,
            "pipeline_version": result.pipeline_version,
            "budget_not_attempted": not_attempted,
        },
        "result": result.model_dump(mode="json", exclude={"html"}),
    }
    _write_json(report, out_dir / "report.json")
    return report


def rerender_demo(report_path: Path, pdf_path: Path, html_path: Path, *, note: bool = True) -> dict:
    """Write today's output for a stored result: no page is fetched, no model is asked, and
    nothing is paid. The pictures are cut from the PDF again as a conversion cuts them
    (`pipeline.run.recrop`), the file is written by today's assembler from the stored result
    (`web.review.rerender_html`), the demo note goes in again (`note`), and the report keeps
    its result with the checks of the new file. The headings are decided again with today's
    heading step (no model: `pipeline.run.refresh_heading_fixes`). What the models answered
    stays the stored answers; so does the pipeline version the result says it was converted by."""
    from readablestem.config import Settings
    from readablestem.core.types import DocumentResult
    from readablestem.eval import render
    from readablestem.pipeline.run import recrop, refresh_heading_fixes
    from readablestem.web.review import rerender_html

    raw = report_path.read_text(encoding="utf-8")
    report = json.loads(raw)
    meta = report["demo"]
    result = DocumentResult.model_validate({**report["result"], "html": ""})
    pdf = pdf_path.read_bytes()
    refreshed = refresh_heading_fixes(result)  # the heading step needs no model
    html = rerender_html(result, recrop(pdf, result.elements))
    if note:
        page = render.PageSource(
            book_slug=meta.get("book_slug") or "",
            book_title=meta.get("book_title") or "",
            page_slug=meta.get("page_slug") or "",
            page_title=meta.get("page_title") or "",
            page_url=meta.get("page_url") or "",
            content_url="",
            license_name=meta.get("license_name") or "",
            license_url=meta.get("license_url") or "",
            xhtml="",
            authors=list(meta.get("authors") or []),
        )
        # The note links to the source code (the licence wording is on the Terms page).
        source_url = Settings(_env_file=None).source_url  # type: ignore[call-arg]
        html = render.wrap_demo_html(
            html, page, date=meta["date"], source_url=source_url, placeholder=False
        )
    html_path.write_text(html, encoding="utf-8")
    # The report keeps the stored result as it was written, down to its formatting; only the
    # checks of the file (`html_checks`) are those of the file just written.
    checks = [c.model_dump(mode="json") for c in result.html_checks]
    if refreshed:
        # Only the heading fixes and the review counts follow; every other entry is as stored.
        stored = result.model_dump(mode="json", exclude={"html"})
        report["result"]["fixes"] = [
            new if old["kind"] == "heading" else old
            for old, new in zip(report["result"]["fixes"], stored["fixes"], strict=True)
        ]
        report["result"]["stats"] = stored["stats"]
    if refreshed or report["result"].get("html_checks") != checks:
        report["result"]["html_checks"] = checks
        indented = raw.startswith("{\n")
        text = json.dumps(
            report,
            ensure_ascii=False,
            indent=2 if indented else None,
            separators=None if indented else (",", ":"),
        )
        report_path.write_text(text + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
    return report


def _cmd_demo_rerender(args: argparse.Namespace) -> int:
    report = rerender_demo(args.report, args.pdf, args.out, note=not args.no_note)
    stats = report["result"]["stats"]
    print(f"{args.report.name}: {stats['pages']} PDF pages, 0 model calls, $0.0000")
    print(f"wrote {_shown(args.out)} and {_shown(args.report)}")
    return 0


def _cmd_demo_build(args: argparse.Namespace) -> int:
    from readablestem.eval import render
    from readablestem.gold.openstax import GoldError, HttpFetcher

    if args.gold and (message := heldout_cli.unselected(args.gold)):
        return _error(message)
    settings = _settings_for(args)
    if isinstance(settings, str):
        return _error(settings)
    made = _make_client(args, settings)
    if isinstance(made, str):
        return _error(made)
    client, _budget, label = made
    fetcher = HttpFetcher(cache_dir=_default(OPENSTAX_CACHE))
    out_dir = args.out or _default(DEMO_DIR)
    try:
        page = _page_source(args.book, args.page, args.gold or _default(GOLD_DIR), fetcher)
        report = build_demo(
            page,
            settings=settings,
            client=client,
            out_dir=out_dir,
            date=args.date,
            images=render.ImageCache(_default(EVAL_CACHE) / "images"),
            client_label=label,
            paper=args.paper,
        )
    except (GoldError, render.RenderError) as exc:
        return _error(str(exc))
    except NotImplementedError as exc:
        return _error(f"a pipeline module is still a stub ({exc}); integrate it first")
    stats = report["result"]["stats"]
    print(
        f"demo for {args.book}/{args.page}: {stats['pages']} PDF pages, "
        f"{stats['model_calls']} model calls, ${stats['cost_usd']:.4f}"
    )
    if report["demo"]["budget_not_attempted"]:
        print(
            f"warning: spend cap reached; {report['demo']['budget_not_attempted']} fixes were "
            "not attempted (raise --max-usd)"
        )
    for name in ("index.html", "report.json", "source.pdf"):
        print(f"wrote {_shown(out_dir / name)}")
    return 0


# ======================================================================== register


def register(
    group: argparse._SubParsersAction, top: argparse._SubParsersAction | None = None
) -> None:
    """Add subcommands. `group` is the `eval` subparsers (add pipeline, gate, ...);
    `top` is the top-level subparsers (add `demo`). Called by cli.main.build_parser."""
    today = dt.date.today().isoformat()

    pipeline = group.add_parser(
        "pipeline", help="run a pipeline step on rendered gold inputs and score it"
    )
    pipeline.add_argument("--task", required=True, choices=PIPELINE_TASKS)
    _add_spend_args(pipeline)
    _add_judge_args(pipeline)
    heldout_cli.add_set_arguments(pipeline)
    pipeline.add_argument(
        "--limit",
        type=_positive_int,
        help="first N items (equations, figures), pages, or documents (tables)",
    )
    pipeline.add_argument(
        "--gold", type=Path, help="gold dir (default: data/gold; tables: data/gold/tables)"
    )
    pipeline.add_argument(
        "--out",
        type=Path,
        help="report JSON (default: data/eval/latest-<task>.json; "
        "tables: latest-tables-<mode>.json)",
    )
    tables_group = pipeline.add_argument_group("tables task")
    tables_group.add_argument(
        "--mode",
        choices=["local", "model"],
        default="local",
        help="local: detection and the local grid, no model (no --max-usd needed); "
        "model: also the table step (needs --max-usd or --fake)",
    )
    tables_group.add_argument(
        "--styles",
        default="grid,booktabs,norules",
        help="table print styles of the OpenStax pages, comma-separated",
    )
    tables_group.add_argument(
        "--no-authored", action="store_true", help="leave out data/authored/tables"
    )
    tables_group.add_argument(
        "--real-dir",
        type=Path,
        help="private real documents with labels.json (default: private-inputs/tables "
        "when it has one; counts only are reported)",
    )
    pipeline.add_argument(
        "--every",
        type=_positive_int,
        help="equations and figures: every Nth item, from the first (a spread over the set "
        "where --limit takes only its start); with --limit, the first N of those",
    )
    pipeline.add_argument(
        "--sections",
        nargs="+",
        metavar="BOOK/PAGE",
        help="only the equations, figures or pages whose book/page id contains one of these "
        "strings (a slice of a set; applied before --every and --limit)",
    )
    pipeline.add_argument(
        "--scanned",
        action="store_true",
        help="document task: every page is printed, then turned into the image-only PDF of a "
        "degraded scan of it (eval/scan.py) before it is converted; the gold and the scorers "
        "are the same, plus a prose word error rate. With FEATURE_IMAGE_PAGES off this "
        "measures what the pipeline did with scans before (kept as images). Development sets "
        "only",
    )
    pipeline.add_argument(
        "--scan-seed",
        type=int,
        default=0,
        help="--scanned: the seed of the degradation (the same seed gives the same scans)",
    )
    pipeline.add_argument("--items", type=Path, help="also write per-item rows as JSONL")
    pipeline.add_argument("--date", type=_iso_date, default=today, help="run date to record")
    pipeline.add_argument("--cache", type=Path, help="render cache (default: .cache/eval)")
    pipeline.add_argument("--paper", choices=["Letter", "A4"], default="Letter")
    pipeline.add_argument(
        "--concurrency", type=_positive_int, default=4, help="document task: worker threads"
    )
    pipeline.add_argument(
        "--section-workers",
        type=_positive_int,
        default=1,
        help="document task: sections converted at once (default 1, one after the other). "
        "Shortens a live run, which waits on a hosted model; a section's latency in the "
        "report then includes its wait for the others, so keep 1 for a run that is timed",
    )
    pipeline.add_argument(
        "--answers",
        type=Path,
        help="a file of recorded model answers (eval/answers.py): a request already in it is "
        "answered from it at no cost, any other goes to the model and is appended. One "
        "baseline recording serves every experiment that asks the same thing. Development "
        "sets only",
    )
    pipeline.add_argument(
        "--batch",
        action="store_true",
        help="with --answers and --provider anthropic: send what the file cannot answer as "
        "Message Batches (half price, minutes to wait) in stages, the same requests as a "
        "live run less the refusal fallback (models/anthropic_batch.py)",
    )
    pipeline.add_argument(
        "--answers-offline",
        action="store_true",
        help="with --answers: a request not in the file is refused, not sent (spends nothing)",
    )
    pipeline.add_argument(
        "--answers-rekey",
        action="store_true",
        help="with --answers: a request the file holds only under the key used before the "
        "answer format was part of it is answered from that row, and the row is written "
        "again under the new key. One such run moves a recorded file to the new keys",
    )
    pipeline.add_argument(
        "--speech",
        action="store_true",
        help="equations and document tasks: add the share of equations whose speech equals the "
        "gold's under MathCAT (NVDA, JAWS) and Speech Rule Engine (MathJax); skips with a "
        "message if they are not built (scripts/speech_oracles/build.sh)",
    )
    pipeline.add_argument(
        "--notation",
        nargs="+",
        metavar="SYMBOL[=MEANING]",
        help='equations task: a course notation in force, e.g. "bold d" "v=other"',
    )
    pipeline.set_defaults(func=_cmd_pipeline)

    classes = group.add_parser(
        "classes",
        help="score the dev-v2 classes without an equation scorer: prose scripts, code "
        "blocks, glossary entries, hyphenated line ends",
    )
    heldout_cli.add_set_arguments(classes)
    what = classes.add_mutually_exclusive_group(required=True)
    what.add_argument(
        "--self-check",
        action="store_true",
        help="score HTML rendered from the gold itself: every metric must be 1.0",
    )
    what.add_argument(
        "--html-dir",
        type=Path,
        help="converted pages saved as <book>__<page>.html (a missing page scores as all lost)",
    )
    classes.add_argument("--report", type=Path, help="also write the summary as JSON")
    classes.set_defaults(func=_cmd_classes)

    prune = group.add_parser(
        "prune",
        help="delete kept run recordings older than an age (not those a committed report names)",
    )
    prune.add_argument("--older-than", type=_positive_float, required=True, metavar="DAYS")
    prune.add_argument(
        "--set",
        action="append",
        metavar="NAME",
        help="a set's cache folder under ~/.cache/readablestem (repeatable; default: every one "
        "that has runs)",
    )
    prune.add_argument("--dry-run", action="store_true", help="list, delete nothing")
    prune.set_defaults(func=_cmd_prune)

    gate = group.add_parser(
        "gate", help="fail when a tracked metric regressed vs a baseline, or is below a floor"
    )
    gate.add_argument("--current", type=Path, required=True)
    gate.add_argument(
        "--baseline",
        type=Path,
        help="baseline report (optional for tasks with floors: tables checks its floors alone)",
    )
    gate.add_argument(
        "--tolerance-file",
        type=Path,
        help="tolerances JSON (default: data/eval/tolerances.json if present)",
    )
    gate.add_argument(
        "--require-current",
        action="store_true",
        help="refuse (exit 2) a current report made with other prompts or models than this code",
    )
    gate.set_defaults(func=_cmd_gate)

    bar = group.add_parser(
        "bar",
        help="hold one number of a report against a bar: only a held-out score under the "
        "production profile can meet one",
    )
    bar.add_argument("report", type=Path, help="a report that records its split and profile")
    bar.add_argument("--measure", required=True, help="a number under metrics (dots go deeper)")
    limit = bar.add_mutually_exclusive_group(required=True)
    limit.add_argument("--at-least", type=float, metavar="X")
    limit.add_argument("--at-most", type=float, metavar="X")
    bar.add_argument("--kind", choices=["release", "goal"], default="release")
    bar.set_defaults(func=_cmd_bar)

    cost = group.add_parser(
        "cost",
        help="dollars a page by page kind and by step, from a replay of the corpus under "
        "the production profile (spends nothing)",
    )
    _add_measure_args(cost, at_once=4)
    cost.set_defaults(func=_cmd_cost)

    speed = group.add_parser(
        "speed",
        help="seconds a page by page kind and by stage, from replays of the corpus under "
        "the production profile, and the bar against an earlier record",
    )
    _add_measure_args(speed, at_once=1)
    speed.add_argument(
        "--runs", type=_positive_int, default=2, help="replays; an upload's fastest counts (2)"
    )
    speed.add_argument("--against", type=Path, help="an earlier record to hold the bar against")
    speed.add_argument(
        "--allowance",
        type=float,
        default=0.10,
        help="how much slower a page kind may be (0.10; 1.0 on a shared runner)",
    )
    speed.set_defaults(func=_cmd_speed)

    burden = group.add_parser(
        "burden",
        help="decisions a page asks of a reviewer, by page kind and by why, from a replay of "
        "the corpus under the production profile, and the bar against an earlier record "
        "(spends nothing)",
    )
    _add_measure_args(burden, at_once=4)
    burden.add_argument("--against", type=Path, help="an earlier record to hold the bar against")
    burden.add_argument(
        "--allowance",
        type=float,
        default=0.0,
        help="how many more decisions a page a page kind may ask for than before (0)",
    )
    burden.add_argument(
        "--same",
        action="store_true",
        help="with --against: every upload measured now must ask for exactly the decisions "
        "the record has, fewer or more (CI's eval gate); an upload this machine cannot "
        "replay is not compared",
    )
    burden.set_defaults(func=_cmd_burden)

    ledger = group.add_parser(
        "ledger",
        help="the quality ledger: every row from the records the evals keep, with counts, a "
        "bound and a label, held against its release bar and its goal bar (measures nothing)",
    )
    ledger.add_argument(
        "--records", type=Path, metavar="DIR", help="the kept records (default: data/eval)"
    )
    ledger.add_argument(
        "--bars", type=Path, metavar="FILE", help="default: ledger-bars.json beside the records"
    )
    ledger.add_argument("--out", type=Path, help="write the ledger here as JSON")
    ledger.add_argument(
        "--for-release",
        action="store_true",
        help="also fail when a row that has a release bar has no held-out figure of this release",
    )
    ledger.add_argument(
        "--register-bars",
        action="store_true",
        help="write this run's held-out figures as the release bars of rows that have none",
    )
    ledger.add_argument(
        "--replace-bar",
        action="append",
        default=[],
        metavar="ROW",
        help="replace this row's release bar with this run's held-out figure (repeat)",
    )
    ledger.set_defaults(func=_cmd_ledger)

    order = group.add_parser(
        "order",
        help="whether each gold page is read in order and what else is wrong on it, in the "
        "page a student is given, from a replay of the corpus, and the bar against an "
        "earlier record (spends nothing)",
    )
    _add_measure_args(order, at_once=4)
    order.add_argument("--against", type=Path, help="an earlier record to hold the bar against")
    order.add_argument(
        "--set",
        metavar="NAME",
        help="score a layout gold set (data/gold/layout/NAME.json) and not the corpus: each "
        "file converted with no model",
    )
    order.add_argument("--root", type=Path, help="the set's folder (default: the cache's)")
    order.add_argument("--registry", type=Path, help="default: data/gold/layout/<set>.json")
    order.add_argument(
        "--for-release",
        action="store_true",
        help="score a held-out set: once, for a release, never while the reader is changed",
    )
    order.set_defaults(func=_cmd_order)

    parsing = group.add_parser(
        "parsing-bench",
        help="the reader on the olmOCR-Bench categories that need no model, scored by the "
        "benchmark's own checker (spends nothing)",
    )
    parsing.add_argument("--fetch", action="store_true", help="fetch the benchmark first")
    parsing.add_argument("--folder", type=Path, help="the fetched benchmark (outside the repo)")
    parsing.add_argument(
        "--run-dir", type=Path, help="a run's folder: pages already converted there are kept"
    )
    parsing.add_argument(
        "--at-once", type=_positive_int, default=2, help="conversions at once (3 at most)"
    )
    parsing.add_argument("--only", action="append", help="one category (repeat); default: all")
    parsing.add_argument("--limit", type=_positive_int, help="the first N PDFs of each category")
    parsing.add_argument("--out", type=Path, help="write the run record as JSON")
    parsing.set_defaults(func=_cmd_parsing_bench)

    outputs = group.add_parser(
        "outputs",
        help="the files a student is given, built from a replay of the corpus, read and "
        "counted: what is wrong in each, by kind (spends nothing, gates nothing)",
    )
    _add_measure_args(outputs, at_once=4)
    outputs.set_defaults(func=_cmd_outputs)

    pictures = group.add_parser(
        "pictures",
        help="A18's count over a replay of the corpus: pictures not converted by reason, "
        "content pictures with empty alt, regions called handwriting against the truth "
        "list (spends nothing, gates nothing)",
    )
    _add_measure_args(pictures, at_once=4)
    pictures.add_argument(
        "--truth",
        type=Path,
        help="the list of regions a person saw as pen strokes or not "
        "(default: data/eval/pen-truth.json)",
    )
    pictures.set_defaults(func=_cmd_pictures)

    say_once = group.add_parser(
        "say-once",
        help="the share of each page's words that no element says and that are said twice, "
        "over the corpus, read with no model (spends nothing, gates nothing)",
    )
    say_once.add_argument(
        "--only", action="append", metavar="ID", help="a corpus file to measure (repeatable)"
    )
    say_once.add_argument(
        "--at-once", type=_positive_int, default=2, help="files read at the same time (default 2)"
    )
    say_once.add_argument("--out", type=Path, help="write the report as JSON")
    say_once.set_defaults(func=_cmd_say_once)

    page_audit = group.add_parser(
        "page-audit",
        help="the page audit in shadow: one request a page that compares the page with its "
        "output; writes verdicts, changes no output",
    )
    which = page_audit.add_mutually_exclusive_group(required=True)
    which.add_argument(
        "--corpus",
        action="store_true",
        help="the corpus, each upload converted from its recorded answers (as `eval cost`)",
    )
    which.add_argument(
        "--gold-dir",
        type=Path,
        metavar="DIR",
        help="a layout gold set's folder (files/<id>.pdf), each file converted with no model",
    )
    page_audit.add_argument(
        "--only", action="append", default=[], metavar="ID", help="only this document (repeat)"
    )
    page_audit.add_argument("--limit", type=_positive_int, help="the first N documents")
    page_audit.add_argument(
        "--rendered",
        action="store_true",
        help="also send a picture of the page's output as a browser draws it (needs the "
        "browser group: uv sync --group browser)",
    )
    _add_spend_args(page_audit)
    page_audit.add_argument(
        "--answers",
        type=Path,
        help="a file of recorded answers (eval/answers.py): a request in it costs nothing, "
        "any other goes to the model and is appended (default: the run's own folder)",
    )
    page_audit.add_argument(
        "--answers-offline",
        action="store_true",
        help="with --answers: replay only; a request not in the file is not audited",
    )
    page_audit.add_argument(
        "--batch",
        action="store_true",
        help="send what the answers file cannot answer as Message Batches (half price); a "
        "request that keeps failing is stored as not audited",
    )
    page_audit.add_argument("--date", type=_iso_date, default=today, help="run date to record")
    page_audit.add_argument(
        "--out",
        type=Path,
        help="report JSON (default: data/eval/latest-page-audit.json; with --gold-dir, "
        "page-audit.json in that folder)",
    )
    page_audit.set_defaults(func=_cmd_page_audit)

    promote = group.add_parser(
        "promote", help="copy a reviewed report to data/eval as the baseline or latest"
    )
    promote.add_argument("report", type=Path, help="a complete report from eval pipeline")
    promote.add_argument("--as", dest="kind", choices=["baseline", "latest"], required=True)
    promote.add_argument("--out-dir", type=Path, help="default: data/eval")
    promote.set_defaults(func=_cmd_promote)

    sample = group.add_parser(
        "sample", help="weekly drift check: score a seeded random share of the gold set"
    )
    sample.add_argument("--task", required=True, choices=TASKS)
    _add_spend_args(sample)
    _add_judge_args(sample)
    sample.add_argument("--fraction", type=float, default=0.05, help="share of units (0.05)")
    sample.add_argument("--week", help="ISO week used as the seed, e.g. 2026-W41 (default: now)")
    sample.add_argument("--gold", type=Path, help="gold dir (default: data/gold)")
    sample.add_argument(
        "--out", type=Path, help="report JSON (default: .local/eval/weekly/<week>-<task>.json)"
    )
    sample.add_argument("--items", type=Path, help="also write per-item rows as JSONL")
    sample.add_argument("--date", type=_iso_date, default=today, help="run date to record")
    sample.add_argument("--cache", type=Path, help="render cache (default: .cache/eval)")
    sample.add_argument("--paper", choices=["Letter", "A4"], default="Letter")
    sample.add_argument("--concurrency", type=_positive_int, default=4)
    sample.set_defaults(func=_cmd_sample, date_value=dt.date.today())

    calibrate = group.add_parser(
        "calibrate", help="judge vs human labels (kappa), label template, bias probes"
    )
    calibrate.add_argument("--judge", type=Path, help="judged.jsonl from eval pipeline --items")
    calibrate.add_argument("--labels", type=Path, help="labels JSONL or CSV (id, human_pass)")
    calibrate.add_argument(
        "--write-template", type=Path, help="write a labelling sheet (.csv or .jsonl)"
    )
    calibrate.add_argument(
        "--probes", action="store_true", help="run the judge bias probes on gold figures"
    )
    _add_spend_args(calibrate)
    _add_judge_args(calibrate)
    calibrate.add_argument("--gold", type=Path, help="gold dir for --probes")
    calibrate.add_argument(
        "--text-only",
        action="store_true",
        help="--probes: judge without the figure image (the older text-only instrument)",
    )
    calibrate.add_argument("--limit", type=_positive_int, help="--probes: first N figures")
    calibrate.add_argument(
        "--paraphrases", type=Path, help="--probes: hand-written verbose texts (id, text)"
    )
    calibrate.add_argument("--n-boot", type=_positive_int, default=2000)
    calibrate.add_argument("--seed", type=int, default=0)
    calibrate.add_argument("--out", type=Path, help="write the report as JSON")
    calibrate.add_argument("--items", type=Path, help="--probes: per-judgement rows (JSONL)")
    calibrate.set_defaults(func=_cmd_calibrate)

    spotcheck = group.add_parser(
        "figures-spotcheck",
        help="offline: sheet of every long-judge fail plus sampled passes for a person to check",
    )
    spotcheck.add_argument("--items", type=Path, help="judged.jsonl from a long-description run")
    spotcheck.add_argument("--sheet", type=Path, help="labelling sheet to write (.csv or .jsonl)")
    spotcheck.add_argument("--key", type=Path, help="key file (default: <sheet>.key.json)")
    spotcheck.add_argument("--labels", type=Path, help="the filled sheet, to score against --key")
    spotcheck.add_argument("--passes", type=int, default=10, help="judge passes to sample (10)")
    spotcheck.add_argument("--seed", default="figures-spotcheck", help="seed for the draw")
    spotcheck.add_argument("--out", type=Path, help="write the report as JSON")
    spotcheck.set_defaults(func=_cmd_figures_spotcheck)

    render = group.add_parser("render", help="print a gold page to PDF or pre-render equations")
    render.add_argument("--page", help="OpenStax page slug, e.g. 2-2-speed-and-velocity")
    render.add_argument(
        "--book", help="OpenStax book slug (default: physics; with --set heldout: the set's)"
    )
    heldout_cli.add_set_arguments(render)
    render.add_argument(
        "--all",
        action="store_true",
        help="--set heldout: print all 15 sections, count their pages against the registered "
        "ones and write pages.json (page counts and SHA-256 values)",
    )
    render.add_argument(
        "--out-dir", type=Path, help="--all: where the PDFs go (default: <heldout dir>/pdfs)"
    )
    render.add_argument("--equations", action="store_true", help="render gold equations")
    render.add_argument("--gold", type=Path, help="gold dir (default: data/gold)")
    render.add_argument("--limit", type=_positive_int)
    render.add_argument("--out", type=Path, help="PDF path (default: .cache/eval/pages/...)")
    render.add_argument("--cache", type=Path, help="render cache (default: .cache/eval)")
    render.add_argument("--paper", choices=["Letter", "A4"], default="Letter")
    render.set_defaults(func=_cmd_render)

    from readablestem.eval.invented.cli import register as register_invented

    register_invented(group)

    if top is None:
        return
    demo = top.add_parser("demo", help="precomputed public demo").add_subparsers(
        dest="demo_command", required=True
    )
    build = demo.add_parser("build", help="convert one OpenStax page into the static demo")
    build.add_argument("--page", required=True, help="OpenStax page slug")
    build.add_argument("--book", default="physics")
    _add_spend_args(build)
    build.add_argument("--out", type=Path, help="output dir (default: src/readablestem/web/demo/)")
    build.add_argument("--gold", type=Path, help="gold dir (gold pages render from cache)")
    build.add_argument("--date", type=_iso_date, default=today)
    build.add_argument("--paper", choices=["Letter", "A4"], default="Letter")
    build.set_defaults(func=_cmd_demo_build)
    again = demo.add_parser(
        "rerender",
        help="write today's output for a stored demo or example result (no model, no cost)",
    )
    again.add_argument("report", type=Path, help="the stored result (report.json)")
    again.add_argument("pdf", type=Path, help="the PDF it was converted from")
    again.add_argument("out", type=Path, help="the HTML file to write")
    again.add_argument("--no-note", action="store_true", help="leave out the demo note")
    again.set_defaults(func=_cmd_demo_rerender)
