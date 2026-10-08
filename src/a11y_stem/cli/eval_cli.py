"""CLI: a11y-stem eval pipeline|gate|calibrate|render, demo build (WP7).

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
import importlib
import json
import math
import sys
from pathlib import Path
from typing import Any

GOLD_DIR = Path("data/gold")
EVAL_DIR = Path("data/eval")
EVAL_CACHE = Path(".cache/eval")
OPENSTAX_CACHE = Path(".cache/openstax")
DEMO_DIR = Path("src/a11y_stem/web/static/demo")
DEFAULT_MAX_CALLS = 5000
# Same as eval.pipeline_eval.TASKS (a test checks); kept here so building the parser
# does not import the pipeline.
TASKS = ("equations", "headings", "figures", "document")


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


def _make_client(args: argparse.Namespace, settings: Any) -> tuple[Any, Any, str] | str:
    """(client, budget, label), or an error message. Every request reserves spend first."""
    from a11y_stem.config import ConfigError
    from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
    from a11y_stem.models.fake import FakeVisionClient

    if not args.fake and args.max_usd is None:
        return "--max-usd is required for real model calls (or pass --fake)"
    cap = args.max_usd if args.max_usd is not None else math.inf
    budget = JobBudget(InMemorySpendGuard(cap_usd=cap), max_usd=cap, max_calls=args.max_calls)
    if args.fake:
        return FakeVisionClient(budget=budget), budget, "fake"
    try:
        module = importlib.import_module("a11y_stem.models.openai_client")
        client = module.OpenAIVisionClient(settings, budget)
    except ModuleNotFoundError:
        return "the OpenAI client (models/openai_client.py, WP1) is not available yet"
    except ConfigError as exc:
        return str(exc)
    return client, budget, "openai"


def _settings() -> Any:
    from a11y_stem.config import get_settings

    return get_settings()


# ======================================================================== pipeline


def _cmd_pipeline(args: argparse.Namespace) -> int:
    from a11y_stem.eval import pipeline_eval as pe
    from a11y_stem.eval.render import RenderError
    from a11y_stem.gold.openstax import HttpFetcher

    gold_dir = args.gold or _default(GOLD_DIR)
    if not gold_dir.is_dir():
        return _error(f"gold dir not found: {gold_dir}")
    settings = _settings()
    made = _make_client(args, settings)
    if isinstance(made, str):
        return _error(made)
    client, budget, label = made
    out = args.out or _default(EVAL_DIR / f"latest-{args.task}.json")
    fetcher = HttpFetcher(cache_dir=_default(OPENSTAX_CACHE))
    with pe.CachedMedia(
        gold_dir, args.cache or _default(EVAL_CACHE), fetcher, paper=args.paper
    ) as media:
        config = pe.EvalConfig(
            task=args.task,
            settings=settings,
            client=client,
            media=media,
            date=args.date,
            gold_dir=gold_dir,
            limit=args.limit,
            client_label=label,
            gold_label=_shown(gold_dir),
            concurrency=args.concurrency,
            budget=budget,
        )
        try:
            run = pe.run_pipeline_eval(config)
        except RenderError as exc:  # raised while building inputs, before any model call
            return _error(f"could not build the eval input (no model calls made): {exc}")
        except NotImplementedError as exc:
            return _error(f"a pipeline module is still a stub ({exc}); integrate it first")
    pe.write_report(run.report, out)
    if args.items:
        pe.write_items(run.items, args.items)
    print(pe.format_markdown(run.report), end="")
    print(f"\nwrote {_shown(out)}" + (f" and {_shown(args.items)}" if args.items else ""))
    return 0


# ============================================================================ gate


def _cmd_gate(args: argparse.Namespace) -> int:
    from a11y_stem.eval.gate import run_gate

    for path in (args.current, args.baseline):
        if not path.is_file():
            return _error(f"report not found: {path}")
    tolerances = args.tolerance_file
    if tolerances is None and _default(EVAL_DIR / "tolerances.json").is_file():
        tolerances = _default(EVAL_DIR / "tolerances.json")
    try:
        return run_gate(args.current, args.baseline, tolerances)
    except (ValueError, KeyError) as exc:  # malformed JSON or tolerance file
        return _error(f"cannot read reports or tolerances: {exc}")


# ======================================================================= calibrate


def _cmd_calibrate(args: argparse.Namespace) -> int:
    from a11y_stem.eval import calibration as cal

    if args.probes:
        return _calibrate_probes(args)
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
    settings = _settings()
    made = _make_client(args, settings)
    if isinstance(made, str):
        return _error(made)
    client, _budget, _label = made
    paraphrases = cal.load_paraphrases(args.paraphrases) if args.paraphrases else None
    figures = cal.probe_figures(load_gold(gold_dir), args.limit)
    report, items = cal.run_probes(
        figures, client=client, settings=settings, paraphrases=paraphrases
    )
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

    gold_dir = args.gold or _default(GOLD_DIR)
    cache = args.cache or _default(EVAL_CACHE)
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
    fetcher = HttpFetcher(cache_dir=_default(OPENSTAX_CACHE))
    out = args.out or cache / "pages" / f"{args.book}-{args.page}.pdf"
    try:
        page = _page_source(args.book, args.page, gold_dir, fetcher)
        render.render_page_pdf(
            page, out, images=render.ImageCache(cache / "images"), paper=args.paper
        )
    except (GoldError, render.RenderError) as exc:
        return _error(str(exc))
    print(f"wrote {_shown(out)}")
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
    html = render.wrap_demo_html(result.html, page, date=date)
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

    settings = _settings()
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
    pipeline.add_argument("--task", required=True, choices=TASKS)
    _add_spend_args(pipeline)
    pipeline.add_argument(
        "--limit", type=_positive_int, help="first N items (equations, figures) or pages"
    )
    pipeline.add_argument("--gold", type=Path, help="gold dir (default: data/gold)")
    pipeline.add_argument(
        "--out", type=Path, help="report JSON (default: data/eval/latest-<task>.json)"
    )
    pipeline.add_argument("--items", type=Path, help="also write per-item rows as JSONL")
    pipeline.add_argument("--date", type=_iso_date, default=today, help="run date to record")
    pipeline.add_argument("--cache", type=Path, help="render cache (default: .cache/eval)")
    pipeline.add_argument("--paper", choices=["Letter", "A4"], default="Letter")
    pipeline.add_argument(
        "--concurrency", type=_positive_int, default=4, help="document task: worker threads"
    )
    pipeline.set_defaults(func=_cmd_pipeline)

    gate = group.add_parser("gate", help="fail when a tracked metric regressed vs a baseline")
    gate.add_argument("--current", type=Path, required=True)
    gate.add_argument("--baseline", type=Path, required=True)
    gate.add_argument(
        "--tolerance-file",
        type=Path,
        help="tolerances JSON (default: data/eval/tolerances.json if present)",
    )
    gate.set_defaults(func=_cmd_gate)

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
    calibrate.add_argument("--gold", type=Path, help="gold dir for --probes")
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
    render.add_argument("--book", default="physics")
    render.add_argument("--equations", action="store_true", help="render gold equations")
    render.add_argument("--gold", type=Path, help="gold dir (default: data/gold)")
    render.add_argument("--limit", type=_positive_int)
    render.add_argument("--out", type=Path, help="PDF path (default: .cache/eval/pages/...)")
    render.add_argument("--cache", type=Path, help="render cache (default: .cache/eval)")
    render.add_argument("--paper", choices=["Letter", "A4"], default="Letter")
    render.set_defaults(func=_cmd_render)

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
