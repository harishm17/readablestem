"""Command line: `parsers-bench list | setup | run | adapt | score | plumbing`.

`plumbing` is the whole chain on two development pages (the committed demo PDF, OpenStax
Physics 2.3, CC BY 4.0): convert with every arm, adapt, score. It proves the pieces fit; it is
not a result, and it must never be pointed at the held-out sections.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from parsers_bench import arms as arms_mod
from parsers_bench import intermediate, runners
from parsers_bench.arms import ARMS, NOT_RUN, Arm
from parsers_bench.equations import VARIANTS

REPO_ROOT = arms_mod.HARNESS_DIR.parents[1]
DEMO_PDF = REPO_ROOT / "src" / "readablestem" / "web" / "static" / "demo" / "source.pdf"
GOLD_DIR = REPO_ROOT / "data" / "gold"
DEV_BOOK = "physics"
DEV_PAGE = "2-3-position-vs-time-graphs"
DEFAULT_OUT = Path.home() / ".cache" / "a11y-proof" / "parsers-plumbing"


def _arm_list(names: list[str]) -> list[Arm]:
    return [arms_mod.get(n) for n in names] if names else list(ARMS.values())


def cmd_list(_: argparse.Namespace) -> int:
    print("arms that run:")
    for arm in ARMS.values():
        ready = "ready" if (arm.env_dir / "bin").is_dir() else "not set up"
        print(f"  {arm.plan_id:4} {arm.id:16} {arm.name} [{ready}]")
    print("considered, not run (no runner on purpose):")
    for name, info in NOT_RUN.items():
        print(f"  {info['plan_id']:4} {name:16} {info['version']}: {info['reason']}")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    """Create each arm's environment from its lock file; for MinerU fetch the model weights."""
    seen: set[str] = set()
    for arm in _arm_list(args.arms):
        if arm.project in seen:
            continue
        seen.add(arm.project)
        env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(arm.env_dir)}
        env.pop("VIRTUAL_ENV", None)
        print(f"{arm.project}: uv sync --frozen -> {arm.env_dir}")
        subprocess.run(
            ["uv", "sync", "--frozen", "--project", str(arm.project_dir)], env=env, check=True
        )
        if arm.project == "mineru":
            home = arms_mod.mineru_home()
            print(f"mineru: models (basic tier) -> {home}")
            subprocess.run(
                [
                    str(arm.env_dir / "bin" / "mineru-kit"),
                    "models",
                    "download",
                    "--tier",
                    "basic",
                    "--source",
                    "huggingface",
                ],
                env={**env, "MINERU_HOME": str(home)},
                check=True,
            )
        if arm.project == "docling":
            print(
                "docling: weights download on the first run (Hugging Face cache); run the "
                "plumbing once before timing anything"
            )
    return 0


def _section(pdf: Path, explicit: str | None) -> str:
    return explicit or pdf.stem


def cmd_run(args: argparse.Namespace) -> int:
    arm = arms_mod.get(args.arm)
    pdf = Path(args.pdf).resolve()
    out = Path(args.out)
    code = 0
    for n in range(1, args.repeat + 1):
        run_dir = out / arm.id / f"run{n}"
        result = runners.run_arm(
            arm,
            pdf,
            run_dir,
            section=_section(pdf, args.section),
            timeout_seconds=args.timeout,
            offline=args.offline,
        )
        rec = result.record
        print(
            f"{arm.id} run{n}: {rec['status']} wall {rec['wall_seconds']} s "
            f"({rec['wall_seconds_per_page']} s/page) -> {run_dir}"
        )
        code = code or int(rec["status"] != "ok")
    return code


def cmd_adapt(args: argparse.Namespace) -> int:
    from parsers_bench.adapters import adapt_run

    arm = arms_mod.get(args.arm)
    run_dir = Path(args.run_dir)
    doc = adapt_run(arm, run_dir, section=args.section or run_dir.parent.name)
    intermediate.write(doc, run_dir / "intermediate.json")
    print(json.dumps({"arm": doc.arm, "status": doc.status, **doc.counts()}))
    return 0


def _cut_pdf(src: Path, pages_one_based: list[int], dst: Path) -> None:
    import pymupdf

    dst.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(src) as source, pymupdf.open() as cut:
        for page in pages_one_based:
            cut.insert_pdf(source, from_page=page - 1, to_page=page - 1)
        cut.save(dst, deflate=True, garbage=3)


def _parse_pages(text: str) -> list[int]:
    pages: list[int] = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        pages += list(range(int(lo), int(hi or lo) + 1))
    return pages


def cmd_plumbing(args: argparse.Namespace) -> int:
    """Convert, adapt and score two development pages with every arm that is set up."""
    from parsers_bench import devgold, invented, score
    from parsers_bench.adapters import adapt_run

    if not DEMO_PDF.is_file() or not GOLD_DIR.is_dir():
        print(
            "run this from a checkout of ReadableSTEM (the demo PDF and data/gold are needed)",
            file=sys.stderr,
        )
        return 2
    out = Path(args.out)
    pages = _parse_pages(args.pages)
    dev_pdf = out / "in" / "dev-pages.pdf"
    _cut_pdf(DEMO_PDF, pages, dev_pdf)
    gold = devgold.restrict(
        devgold.section_records(GOLD_DIR, DEV_BOOK, DEV_PAGE),
        DEMO_PDF.read_bytes(),
        [p - 1 for p in pages],
        section=f"{DEV_BOOK}/{DEV_PAGE}",
    )
    reference = invented.reference_from_pdf(dev_pdf)
    print(f"input: {DEV_BOOK} {DEV_PAGE}, pages {pages} of the demo PDF; gold {gold.counts()}")
    summary: dict[str, Any] = {
        "kind": "plumbing on two development pages; not a result",
        "input": {
            "book": DEV_BOOK,
            "page": DEV_PAGE,
            "pdf_pages": pages,
            "license": "CC BY 4.0 (OpenStax Physics)",
        },
        "gold": gold.counts(),
        "arms": {},
    }
    failures = 0
    for arm in _arm_list(args.arms):
        if not (arm.env_dir / "bin").is_dir():
            print(f"{arm.id}: environment missing; run `parsers-bench setup {arm.id}`")
            summary["arms"][arm.id] = {"status": "not set up"}
            failures += 1
            continue
        docs = []
        for n in range(1, args.repeat + 1):
            run_dir = out / arm.id / f"run{n}"
            if not (args.reuse and (run_dir / "run.json").is_file()):
                runners.run_arm(
                    arm,
                    dev_pdf,
                    run_dir,
                    section="dev-pages",
                    timeout_seconds=args.timeout,
                    offline=args.offline,
                )
            doc = adapt_run(arm, run_dir, section="dev-pages")
            intermediate.write(doc, run_dir / "intermediate.json")
            docs.append((run_dir, doc))
        last_dir, last = docs[-1]
        deterministic = all(_same_content(last, other) for _, other in docs[:-1])
        run = json.loads((last_dir / "run.json").read_text(encoding="utf-8"))
        scored = score.score_arm(last, run, gold, reference, page_id="dev-pages")
        (out / arm.id / "score.json").write_text(
            json.dumps(scored["summary"], indent=1, ensure_ascii=False), encoding="utf-8"
        )
        (out / arm.id / "score-items.json").write_text(
            json.dumps(scored["items"], indent=1, ensure_ascii=False), encoding="utf-8"
        )
        entry = _entry(scored["summary"], run, docs, deterministic)
        summary["arms"][arm.id] = entry
        failures += entry["status"] != "ok"
        print(_line(arm, entry))
    (out / "plumbing-summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(f"summary: {out / 'plumbing-summary.json'}")
    return int(bool(failures))


def cmd_proof(args: argparse.Namespace) -> int:
    """The proof run: every section PDF through every arm; see `parsers_bench.heldout`."""
    from parsers_bench import heldout

    return heldout.run_set(
        Path(args.pdf_dir),
        Path(args.out_dir),
        Path(args.work_dir),
        arm_ids=args.arms or None,
        timeout_seconds=args.timeout,
        offline=args.offline,
        wait_quiet=not args.no_wait,
    )


def _same_content(a: intermediate.Intermediate, b: intermediate.Intermediate) -> bool:
    """Same content, ignoring what legitimately differs between runs (timings, notes)."""

    def strip(doc: intermediate.Intermediate) -> dict:
        data = doc.to_dict()
        for key in ("seconds", "notes", "html_path", "markdown_path"):
            data.pop(key, None)
        return data

    return strip(a) == strip(b)


def _entry(summary: dict, run: dict, docs: list, deterministic: bool) -> dict[str, Any]:
    eq = summary["equations"]
    primary = eq["primary"]
    return {
        "status": summary["status"],
        "plan_id": run["plan_id"],
        "versions": run["versions"],
        "runs": len(docs),
        "deterministic_across_runs": deterministic,
        "wall_seconds_per_page_by_run": [
            json.loads((d / "run.json").read_text(encoding="utf-8"))["wall_seconds_per_page"]
            for d, _ in docs
        ],
        "output": summary["output"],
        "equations": {
            "does_not_transcribe_by_design": eq["does_not_transcribe_by_design"],
            "transcribed": primary["transcribed"],
            "converted_primary": primary["converted"],
            "refused_or_failed_primary": primary["refused_or_failed"],
            "end_to_end_exact_lenient": {v: eq[v]["end_to_end_exact_lenient"] for v in VARIANTS},
            "detection_recall_primary": primary["detection_recall"],
            "unflagged_wrong_per_100_primary": primary["unflagged_wrong_per_100"],
            "retained_in_any_form": {
                k: eq["retained_in_any_form"][k]
                for k in ("checked", "retained_in_any_form", "rate")
            },
        },
        "headings": {
            k: summary["headings"][k]
            for k in (
                "gold_headings",
                "predicted_headings",
                "matched",
                "precision",
                "recall",
                "f1",
                "level_accuracy",
                "distinct_levels_in_output",
            )
        },
        "invented_content": {
            k: summary["invented_content"][k]
            for k in (
                "prose_sentence",
                "number",
                "heading",
                "equation_aligned",
                "equation_unaligned",
            )
        },
        "figures": summary["figures"],
        "cost_and_time": summary["cost_and_time"],
    }


def _line(arm: Arm, entry: dict) -> str:
    eq = entry["equations"]
    head = entry["headings"]
    ct = entry["cost_and_time"]
    return (
        f"{arm.id:16} {entry['status']:7} "
        f"eq {eq['transcribed']} transcribed, "
        f"e2e exact {eq['end_to_end_exact_lenient']['primary']}, "
        f"retained {eq['retained_in_any_form']['retained_in_any_form']}/"
        f"{eq['retained_in_any_form']['checked']}; "
        f"headings {head['matched']}/{head['gold_headings']} (F1 {head['f1']}); "
        f"{ct['wall_seconds_per_page']} s/page, {ct['peak_rss_mb']} MB, "
        f"deterministic {entry['deterministic_across_runs']}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="parsers-bench", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="arms that run and arms that do not").set_defaults(func=cmd_list)

    p = sub.add_parser("setup", help="create the arms' environments from their lock files")
    p.add_argument("arms", nargs="*")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("run", help="convert one PDF with one arm")
    p.add_argument("arm")
    p.add_argument("pdf")
    p.add_argument("--out", default=str(DEFAULT_OUT / "adhoc"))
    p.add_argument("--section")
    p.add_argument("--timeout", type=float, default=runners.DEFAULT_TIMEOUT_SECONDS)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--offline", action="store_true", help="HF_HUB_OFFLINE=1 (weights cached)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("adapt", help="turn a run directory into intermediate.json")
    p.add_argument("arm")
    p.add_argument("run_dir")
    p.add_argument("--section")
    p.set_defaults(func=cmd_adapt)

    p = sub.add_parser("proof", help="the proof run: a folder of section PDFs through the arms")
    p.add_argument("pdf_dir")
    p.add_argument("out_dir")
    p.add_argument("--work-dir", default=str(DEFAULT_OUT / "proof-work"))
    p.add_argument("--arms", nargs="*", default=[])
    p.add_argument("--timeout", type=float, default=runners.DEFAULT_TIMEOUT_SECONDS)
    p.add_argument("--offline", action="store_true")
    p.add_argument("--no-wait", action="store_true", help="do not wait for a quiet box")
    p.set_defaults(func=cmd_proof)

    p = sub.add_parser("plumbing", help="two development pages through every arm and the scorers")
    p.add_argument("arms", nargs="*")
    p.add_argument("--out", default=str(DEFAULT_OUT / "latest"))
    p.add_argument("--pages", default="10-11", help="1-based pages of the demo PDF (default 10-11)")
    p.add_argument("--repeat", type=int, default=2, help="runs per arm; the last is scored")
    p.add_argument("--timeout", type=float, default=runners.DEFAULT_TIMEOUT_SECONDS)
    p.add_argument("--offline", action="store_true")
    p.add_argument(
        "--reuse",
        action="store_true",
        help="adapt and score runs already in --out instead of converting again",
    )
    p.set_defaults(func=cmd_plumbing)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
