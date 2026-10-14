"""CLI: a11y-stem eval pipeline|gate|calibrate|render|invented, demo build (WP7).

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
import sys
from pathlib import Path
from typing import Any

from a11y_stem.cli import heldout as heldout_cli

GOLD_DIR = Path("data/gold")
EVAL_DIR = Path("data/eval")
EVAL_CACHE = Path(".cache/eval")
OPENSTAX_CACHE = Path(".cache/openstax")
DEMO_DIR = Path("src/a11y_stem/web/static/demo")
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
    from a11y_stem.cli.main import project_root

    return project_root() / path


def _shown(path: Path) -> str:
    """Path relative to the project root when inside it (reports never hold home paths)."""
    from a11y_stem.cli.main import project_root

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

    from a11y_stem.config import ConfigError
    from a11y_stem.eval import judge as judge_mod

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
    from a11y_stem.eval.answers import CachedClient

    if getattr(args, "batch", False):
        # A staged run: what the file cannot answer is collected, not sent (eval/staged.py).
        from a11y_stem.models.anthropic_batch import StagingClient

        staging = StagingClient(client, args.staged_pool)
        cached = CachedClient(staging, path, settings)
        staging.owner = cached
        return cached
    return CachedClient(None if args.answers_offline else client, path, settings)


def _make_client(args: argparse.Namespace, settings: Any) -> tuple[Any, Any, str] | str:
    """(client, budget, label), or an error message. Every request reserves spend first."""
    from a11y_stem.config import ConfigError
    from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
    from a11y_stem.models.fake import FakeVisionClient

    if not args.fake and args.max_usd is None:
        return "--max-usd is required for real model calls (or pass --fake)"
    cap = args.max_usd if args.max_usd is not None else math.inf
    budget = JobBudget(InMemorySpendGuard(cap_usd=cap), max_usd=cap, max_calls=args.max_calls)
    if getattr(args, "batch", False):
        from a11y_stem.models.anthropic_batch import BatchRunner, PendingPool

        args.staged_pool = PendingPool()
        args.staged_runner = BatchRunner(budget)
    if args.fake:
        return FakeVisionClient(budget=budget), budget, "fake"
    from a11y_stem.models import make_live_client

    try:
        # Checks the key before any work, not on the first call. The OpenAI client carries
        # the second automated check on the same budget, as in the app
        # (DECISIONS_FIGURE_VERIFY, DECISIONS_EQUATION_SHADOW turn it off); the Claude
        # client has none.
        client = make_live_client(settings, budget)
    except ConfigError as exc:
        return str(exc)
    client = _cached(args, settings, client)
    return client, budget, settings.model_provider


def _settings() -> Any:
    from a11y_stem.config import get_settings

    return get_settings()


def _settings_for(args: argparse.Namespace) -> Any:
    """The settings with this run's `--provider`, `--main-model` and `--cheap-model`
    applied; an error message (str) when they do not make valid settings."""
    from pydantic import ValidationError

    from a11y_stem.config import with_provider

    try:
        return with_provider(
            _settings(),
            getattr(args, "provider", None),
            main_model=getattr(args, "main_model", None),
            cheap_model=getattr(args, "cheap_model", None),
        )
    except ValidationError as exc:
        reasons = "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())
        return f"invalid model settings: {reasons}"


# ======================================================================== pipeline


def _cmd_pipeline(args: argparse.Namespace) -> int:
    if (args.answers or args.answers_offline) and (
        args.gold_set == "heldout" or (args.gold_set == "dev-v2" and args.part == "check")
    ):
        return _error("--answers is for development sets: not for the held-out or check parts")
    if args.answers_offline and not args.answers:
        return _error("--answers-offline needs --answers FILE")
    if args.batch and not (args.answers and not args.answers_offline and not args.fake):
        return _error("--batch needs --answers FILE, real model calls, and no --answers-offline")
    if args.batch and (args.provider or _settings().model_provider) != "anthropic":
        return _error("--batch is for --provider anthropic (the Message Batches API)")
    if args.gold_set == "dev-v2":
        return _run_devset(args)
    if args.gold_set:
        return _run_heldout(args)
    if args.task == TABLES_TASK:
        return _run_tables(args)
    gold_dir = args.gold or _default(GOLD_DIR)
    if not gold_dir.is_dir():
        return _error(f"gold dir not found: {gold_dir}")
    if message := heldout_cli.unselected(gold_dir):
        return _error(message)
    out = args.out or _default(EVAL_DIR / f"latest-{args.task}.json")
    if getattr(args, "notation", None):
        return _run_with_notation(args, gold_dir, out)
    return _run_pipeline(args, gold_dir, out, gold_label=_shown(gold_dir))


def _run_heldout(args: argparse.Namespace) -> int:
    """`eval pipeline --set heldout`: the pre-registered held-out sections, with the
    registered configuration and nothing else changed. The gold must be the registered
    one; its caches, the OpenStax archive responses and the reports stay outside the
    repository. Refused: `--fake` (a fake run is a run on these sections and is not
    allowed before the registered one, pre-registration section 10.1), `--notation` (no
    course notation is registered) and the tables task (not a task of this set)."""
    from a11y_stem.gold.heldout import REGISTERED

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
    )


def _run_devset(args: argparse.Namespace) -> int:
    """`eval pipeline --set dev-v2`: the extended development set (data/dev-v2). `--part
    dev` is the part to build against; `--part check` is the small sealed part, run for a
    pre-merge sanity check: its files and caches stay outside the repository, the report
    holds totals only (no `--items`) and goes outside the repository too. `--fake` is
    allowed on both. The tables task reads this set's tables (OpenStax and authored)."""
    from a11y_stem.cli import devset as devset_cli
    from a11y_stem.eval.devset_media import DevSetMedia

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
        return _run_tables(args, devset=(gold_dir, authored, cache_dir, openstax_cache, out, extra))
    return _run_pipeline(
        args,
        gold_dir,
        out,
        gold_label=f"dev-v2 ({args.part})",
        extra=extra,
        cache_dir=cache_dir,
        openstax_cache=openstax_cache,
        media_cls=DevSetMedia,
    )


def _run_with_notation(args: argparse.Namespace, gold_dir: Path, out: Path) -> int:
    """The equations task with a course notation in force (FEATURE_NOTATION_MEMORY), as a
    conversion for that course would run it: `--notation "bold d" "v=other"` (a symbol,
    then `=meaning`, vector by default). The report records the entries."""
    from a11y_stem.core.notation import canonical_symbol
    from a11y_stem.core.types import ConversionOptions, NotationEntry
    from a11y_stem.pipeline import options as run_options

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
        return _run_pipeline(args, gold_dir, out, gold_label=_shown(gold_dir), extra=extra)


def _cmd_sample(args: argparse.Namespace) -> int:
    """The weekly sample (eval.sample): a seeded share of the gold set, scored as usual."""
    import tempfile

    from a11y_stem.eval.sample import iso_week, sample_gold

    gold_dir = args.gold or _default(GOLD_DIR)
    if not gold_dir.is_dir():
        return _error(f"gold dir not found: {gold_dir}")
    if message := heldout_cli.unselected(gold_dir):
        return _error(message)
    week = args.week or iso_week(args.date_value)
    out = args.out or _default(WEEKLY_DIR / f"{week}-{args.task}.json")
    with tempfile.TemporaryDirectory(prefix="a11y-stem-sample-") as tmp:
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
        return _run_pipeline(args, Path(tmp), out, gold_label=label, extra={"sample": sample})


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
) -> int:
    from a11y_stem.eval import pipeline_eval as pe
    from a11y_stem.eval.render import RenderError
    from a11y_stem.gold.openstax import HttpFetcher

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
    ) as media:
        config = pe.EvalConfig(
            task=args.task,
            settings=settings,
            client=client,
            media=media,
            date=args.date,
            gold_dir=gold_dir,
            limit=getattr(args, "limit", None),
            every=getattr(args, "every", None),
            client_label=label,
            gold_label=gold_label,
            concurrency=args.concurrency,
            budget=budget,
            judge=judge,
            scorer_gaps=pe.scorer_gap_ids(pe.load_gold(gold_dir), _default(SCORER_CHECK))
            if args.task in {"equations", "document"}
            else frozenset(),
        )
        try:
            if getattr(args, "batch", False):
                from a11y_stem.eval import staged

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
    if hasattr(client, "summary"):
        run.report["answers_file"] = client.summary()
    pe.write_report(run.report, out)
    if args.items:
        pe.write_items(run.items, args.items)
    print(pe.format_markdown(run.report), end="")
    print(f"\nwrote {_shown(out)}" + (f" and {_shown(args.items)}" if args.items else ""))
    if hasattr(client, "summary"):
        print(f"answers file: {dict(client.summary())}")
    return 0


def staged_error() -> type[Exception]:
    from a11y_stem.eval.staged import StageError

    return StageError


def _run_tables(
    args: argparse.Namespace,
    *,
    devset: tuple[Path, Path, Path | None, Path | None, Path, dict] | None = None,
) -> int:
    """`eval pipeline --task tables`: local mode needs no model and no spend cap; model
    mode needs `--max-usd` (or `--fake`) like every task that calls a model. `devset` is
    (gold dir, authored dir, render cache, archive cache, report, report extras) of
    `--set dev-v2`."""
    from a11y_stem.eval import tables
    from a11y_stem.eval.render import RenderError
    from a11y_stem.gold.openstax import HttpFetcher

    gold_dir = devset[0] if devset else args.gold or _default(TABLE_GOLD_DIR)
    if not gold_dir.is_dir():
        return _error(f"gold dir not found: {gold_dir} (run: a11y-stem gold fetch --set tables)")
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
    from a11y_stem.eval import pipeline_eval as pe

    run.report.update(extra)
    pe.write_report(run.report, out)
    if args.items:
        pe.write_items(run.items, args.items)
    print(tables.format_markdown(run.report), end="")
    print(f"\nwrote {_shown(out)}" + (f" and {_shown(args.items)}" if args.items else ""))
    return 0


# ============================================================================ gate


def _cmd_gate(args: argparse.Namespace) -> int:
    from a11y_stem.eval.gate import run_gate

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


def _cmd_promote(args: argparse.Namespace) -> int:
    """Copy a reviewed report to data/eval/<baseline|latest>-<task>.json for the CI gate.

    Refuses a partial run, and (for `latest`) a report produced with other prompts or
    models than this code, which the gate would refuse anyway.
    """
    from a11y_stem.config import (
        ANTHROPIC_CHEAP_MODEL,
        ANTHROPIC_MAIN_MODEL,
        CHEAP_MODEL,
        MAIN_MODEL,
    )
    from a11y_stem.eval.gate import GateRefused, check_current, code_versions

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
        # The committed baselines in data/eval are the OpenAI runs the CI gate compares with.
        return _error(
            "a report made with the anthropic provider would replace the OpenAI baseline "
            "of the same name; give --out-dir to keep it apart"
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
    from a11y_stem.eval import calibration as cal

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
    from a11y_stem.eval import calibration as cal
    from a11y_stem.eval.pipeline_eval import load_gold, write_items

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
        from a11y_stem.eval import render

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
    if args.out:
        _write_json(report, args.out)
    if args.items:
        write_items(items, args.items)
    print(cal.format_probes(report), end="")
    return 0


def _write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ========================================================================== render


def _page_source(book: str, slug: str, gold_dir: Path, fetcher: Any) -> Any:
    """Gold pages come from the cache; any other page is looked up (license-gated)."""
    from a11y_stem.eval import render
    from a11y_stem.eval.pipeline_eval import load_gold

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
    from a11y_stem.eval import render
    from a11y_stem.eval.pipeline_eval import load_gold
    from a11y_stem.gold.openstax import GoldError, HttpFetcher

    book = args.book or "physics"
    gold_dir = args.gold or _default(GOLD_DIR)
    cache = args.cache or _default(EVAL_CACHE)
    openstax_cache = _default(OPENSTAX_CACHE)
    if args.gold_set == "dev-v2":
        from a11y_stem.cli import devset as devset_cli

        if args.part != "dev" or args.all or args.out_dir:
            return _error("eval render --set dev-v2 prints pages of the dev part only")
        found = devset_cli.resolve_eval("dev", args.devset_dir, args.gold, args.cache)
        if isinstance(found, str):
            return _error(found)
        gold_dir = found[0]
    elif args.gold_set:
        from a11y_stem.gold.heldout import REGISTERED

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

    from a11y_stem.eval import render
    from a11y_stem.gold.heldout import REGISTERED
    from a11y_stem.gold.openstax import GoldError, HttpFetcher

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
    from a11y_stem.core.types import Status
    from a11y_stem.eval import render
    from a11y_stem.pipeline import run

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


def _cmd_demo_build(args: argparse.Namespace) -> int:
    from a11y_stem.eval import render
    from a11y_stem.gold.openstax import GoldError, HttpFetcher

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
    pipeline.add_argument("--items", type=Path, help="also write per-item rows as JSONL")
    pipeline.add_argument("--date", type=_iso_date, default=today, help="run date to record")
    pipeline.add_argument("--cache", type=Path, help="render cache (default: .cache/eval)")
    pipeline.add_argument("--paper", choices=["Letter", "A4"], default="Letter")
    pipeline.add_argument(
        "--concurrency", type=_positive_int, default=4, help="document task: worker threads"
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
        "--notation",
        nargs="+",
        metavar="SYMBOL[=MEANING]",
        help='equations task: a course notation in force, e.g. "bold d" "v=other"',
    )
    pipeline.set_defaults(func=_cmd_pipeline)

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

    from a11y_stem.eval.invented.cli import register as register_invented

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
    build.add_argument(
        "--out", type=Path, help="output dir (default: src/a11y_stem/web/static/demo/)"
    )
    build.add_argument("--gold", type=Path, help="gold dir (gold pages render from cache)")
    build.add_argument("--date", type=_iso_date, default=today)
    build.add_argument("--paper", choices=["Letter", "A4"], default="Letter")
    build.set_defaults(func=_cmd_demo_build)
