"""Real-world set, step 3: run the arms on a part (dev or held-out) and keep their answers.

    R="uv run --env-file .env python scripts/realworld_run.py"
    $R estimate   --part dev
    $R rs         --part dev --max-usd 4 --note "..."
    $R chat       --part dev --arm chatbot-haiku-purdue --max-usd 1
    $R chat-items --part dev --arm chatbot-haiku-purdue

Arms: `rs` is ReadableSTEM at its current settings (provider anthropic, the settings' main and
cheap models, page math pass off, no second check) through the document task (`eval pipeline
--task document`, reached through the CLI's own code with this set's media); `chat` is the chatbot
recipe of the proof (`proof.chatbot.record_arms`: the PDF, a 150 dpi PNG of every page and the
verbatim Purdue sentence, no system prompt). Every model answer is recorded under ROOT/runs and a
rerun replays it for free (`--answers`); nothing is asked twice.

The held-out part: runs only when `data/realworld/REGISTRATION.sha256` is present and every file it
lists still has the recorded hash (the registration, the manifests, the order), when the gold on
disk matches the manifest, and when that arm has not been recorded on the held-out part before (a
marker file is written when the run finishes). `--offline` replays an existing recording and
spends nothing.

Each live run appends to `data/realworld/spend-log.jsonl` (the estimate it was given, its cap, what
it cost, from the recorded answers). Keys come from the environment (ANTHROPIC_API_KEY).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

from readablestem.eval import pipeline_eval as pe
from readablestem.eval.realworld_media import RealWorldMedia
from readablestem.proof import chatbot
from readablestem.proof import systems as S

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "realworld"
SPEND_LOG = DATA / "spend-log.jsonl"
# Per PDF page, list price, used only to size a run before it is paid for (from the OpenStax
# held-out runs, 2026-10-14 and 2026-10-21); the real figure is measured and logged.
ESTIMATE_PER_PAGE = {"rs": 0.035, "chatbot-haiku-purdue": 0.003, "chatbot-opus-purdue": 0.060}


def gold_dir(part: str) -> Path:
    return ROOT / "gold" / part


def runs_dir(part: str) -> Path:
    return ROOT / "runs" / part


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(part: str) -> dict:
    return json.loads((gold_dir(part) / "manifest.json").read_text(encoding="utf-8"))


def verify_gold(part: str) -> None:
    """The gold and the cut PDFs on disk are the ones the manifest names."""
    m = manifest(part)
    for paper in m["papers"]:
        slug = paper["id"].replace("/", "_")
        if sha256(gold_dir(part) / f"{slug}.jsonl") != paper["gold_sha256"]:
            raise SystemExit(f"gold of {paper['id']} differs from the manifest")
        if sha256(gold_dir(part) / "pdfs" / f"{slug}.pdf") != paper["extract_pdf_sha256"]:
            raise SystemExit(f"PDF of {paper['id']} differs from the manifest")


REGISTRATIONS = {
    "heldout": "REGISTRATION.sha256",
    "fresh": "REGISTRATION-fresh.sha256",
    "fresh2": "REGISTRATION-fresh2.sha256",
    "fresh3": "REGISTRATION-fresh3.sha256",
}


def require_registration(part: str, arm: str, offline: bool) -> None:
    if part not in REGISTRATIONS:
        return
    reg = DATA / REGISTRATIONS[part]
    if not reg.is_file():
        raise SystemExit(f"the {part} part is not registered (data/realworld/{reg.name})")
    for line in reg.read_text().splitlines():
        digest, _, name = line.partition("  ")
        if sha256(DATA / name) != digest:
            raise SystemExit(f"registered file {name} has changed since registration")
    marker = runs_dir(part) / f"{arm}.recorded"
    if marker.is_file() and not offline:
        raise SystemExit(f"{arm} was already recorded on the {part} part: replay with --offline")


def registered_arm(args: argparse.Namespace) -> str:
    """The name a registered run is recorded under: `rs` on the held-out part, the tag on the
    fresh part (two arms of one registration)."""
    return args.tag if args.part in ("fresh", "fresh2", "fresh3") else "rs"


def papers(part: str) -> list[dict]:
    return manifest(part)["papers"]


def code_identity() -> dict:
    """The commit the run was made at and the hash of its `src` tree (a run of a pre-registered
    arm names the code it ran: `git_head`, `src_tree`)."""
    import subprocess

    def git(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    return {"git_head": git("rev-parse", "HEAD"), "src_tree": git("rev-parse", "HEAD:src")}


def log_spend(entry: dict) -> None:
    entry = {
        "when": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **code_identity(),
        **entry,
    }
    with SPEND_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def cmd_estimate(args: argparse.Namespace) -> int:
    m = manifest(args.part)
    pages = m["n_pages"]
    print(f"{args.part}: {m['n_papers']} papers, {pages} pages, {m['n_equations']} equations")
    for arm, per_page in ESTIMATE_PER_PAGE.items():
        print(f"  {arm:24s} ${pages * per_page:6.2f}  (at ${per_page}/page)")
    return 0


# ------------------------------------------------------------------------------ ReadableSTEM


def cmd_rs(args: argparse.Namespace) -> int:
    # No second automated check: it is shadow-only (it changes no output) and costs model calls,
    # and the registered OpenStax run had none either.
    os.environ["CLAUDE_SECOND_CHECK"] = "off"
    if args.page_math:
        os.environ["FEATURE_PAGE_MATH"] = "true"
    if args.tables:
        os.environ["FEATURE_TABLES"] = "true"
    if args.notation_memory:
        os.environ["FEATURE_NOTATION_MEMORY"] = "true"
    if args.line_context:
        os.environ["EQUATION_LINE_CONTEXT"] = "true"
    if args.no_agreement_lift:
        os.environ["PAGE_MATH_AGREEMENT_LIFT"] = "false"
    if args.page_math_tokens:
        os.environ["PAGE_MATH_MAX_OUTPUT_TOKENS"] = str(args.page_math_tokens)
    from readablestem.cli import eval_cli
    from readablestem.cli.main import build_parser

    verify_gold(args.part)
    require_registration(args.part, registered_arm(args), args.offline)
    out_dir = runs_dir(args.part) / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    answers = out_dir / "answers.jsonl"
    if args.seed_answers_from and not answers.exists():
        # an experiment that asks mostly the same questions starts from the baseline's answers:
        # a request already answered replays for free and is not paid for twice
        shutil.copyfile(runs_dir(args.part) / args.seed_answers_from / "answers.jsonl", answers)
    report = out_dir / "report.json"
    items = out_dir / "items.jsonl"
    argv = [
        "eval", "pipeline", "--task", "document", "--provider", "anthropic",
        "--answers", str(answers), "--items", str(items), "--out", str(report),
        "--section-workers", "3", "--cache", str(out_dir / "cache"),
        "--max-usd", str(args.max_usd),
    ]  # fmt: skip
    if args.offline:
        argv.append("--answers-offline")
    if args.papers:
        argv += ["--sections", *(f"arxiv/{i.replace('/', '_')}" for i in args.papers.split(","))]
    ns = build_parser().parse_args(argv)
    before = answers_cost(answers)
    code = eval_cli._run_pipeline(
        ns,
        gold_dir(args.part),
        report,
        gold_label=f"realworld ({args.part})",
        media_cls=RealWorldMedia,
        cache_dir=out_dir / "cache",
        openstax_cache=out_dir / "cache",
    )
    spent = answers_cost(answers) - before
    if code == 0 and not args.offline:
        log_spend(
            {
                "part": args.part, "arm": "rs", "tag": args.tag,
                "estimate_usd": round(manifest(args.part)["n_pages"] * ESTIMATE_PER_PAGE["rs"], 2),
                "cap_usd": args.max_usd, "actual_usd": round(spent, 4), "note": args.note,
            }
        )  # fmt: skip
        if args.part in REGISTRATIONS:
            marker = runs_dir(args.part) / f"{registered_arm(args)}.recorded"
            marker.write_text(dt.date.today().isoformat())
    print(f"spent this start: ${spent:.4f}")
    return code


def answers_cost(path: Path) -> float:
    """What the answers in the file cost at list price (each answer paid once)."""
    if not path.is_file():
        return 0.0
    total = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            total += float((json.loads(line).get("call") or {}).get("cost_usd") or 0.0)
    return total


# ---------------------------------------------------------------------------------- chatbots


def chat_order(part: str) -> list[str]:
    """Paper ids in the registered order for arms run on a part of the papers only: by the
    SHA-256 of seed, "chat-order" and the id."""
    seed = manifest(part)["seed"]
    ids = [p["id"] for p in papers(part)]
    return sorted(ids, key=lambda i: hashlib.sha256(f"{seed}|chat-order|{i}".encode()).hexdigest())


def pdfs_of(part: str, only: list[str] | None = None) -> dict[str, Path]:
    found = {}
    for paper in papers(part):
        if only and paper["id"] not in only:
            continue
        slug = paper["id"].replace("/", "_")
        found[f"arxiv/{slug}"] = gold_dir(part) / "pdfs" / f"{slug}.pdf"
    return found


def cmd_chat(args: argparse.Namespace) -> int:
    verify_gold(args.part)
    arm = chatbot.ARM_BY_ID[args.arm]
    require_registration(args.part, arm.id, False)
    only = args.papers.split(",") if args.papers else None
    if args.first:
        only = chat_order(args.part)[: args.first]
    pdfs = pdfs_of(args.part, only)
    out = runs_dir(args.part) / "chat" / "systems"
    pages = sum(chatbot.pdf_page_count(p) for p in pdfs.values())
    estimate = pages * ESTIMATE_PER_PAGE[arm.id]
    print(
        f"{arm.id}: {len(pdfs)} papers, {pages} pages, estimate ${estimate:.2f}, "
        f"cap ${args.max_usd}"
    )
    before = chatbot.recorded_cost(out, [arm.id])
    client = chatbot.make_client(arm.provider)
    summary: dict = {}
    for attempt in range(4):  # a connection error while counting tokens ends one start, not the run
        try:
            summary = chatbot.record_arms(
                pdfs, out, arms=[arm], max_usd=args.max_usd, client=client,
                workers=args.workers, runs=1,
            )  # fmt: skip
            break
        except Exception as exc:  # recorded jobs are skipped on the next start
            print(f"start {attempt + 1} stopped: {type(exc).__name__}", flush=True)
            time.sleep(30)
            client = chatbot.make_client(arm.provider)
    else:
        raise SystemExit("the arm could not be recorded after 4 starts")
    spent = chatbot.recorded_cost(out, [arm.id]) - before
    print(json.dumps(summary))
    log_spend(
        {
            "part": args.part, "arm": arm.id, "papers": len(pdfs), "pages": pages,
            "estimate_usd": round(estimate, 2), "cap_usd": args.max_usd,
            "actual_usd": round(spent, 4), "refused_by_cap": summary["refused_by_cap"],
            "note": args.note,
        }
    )  # fmt: skip
    if args.part in REGISTRATIONS and not summary["refused_by_cap"]:
        (runs_dir(args.part) / f"{arm.id}.recorded").write_text(dt.date.today().isoformat())
    return 0


def chat_items(part: str, arm_id: str) -> list[dict]:
    """The document task's item of every paper the arm answered (a paper it was not run on has
    no item; a paper whose run failed is scored as found nothing)."""
    arms = S.load_arms_dir(runs_dir(part) / "chat" / "systems")
    runs = arms.get(arm_id, {})
    records = pe.load_gold(gold_dir(part))
    items = []
    for unit in pe.group_pages(records):
        section = pe._page_id(unit)
        if section not in runs:
            continue
        run = runs[section][0]
        output = run.read()
        item = S.equation_item(section, pe._of_kind(unit, "equation"), output)
        item["pdf_pages"] = run.pdf_pages
        item["cost_usd"] = run.cost_usd
        item["status"] = run.status
        item["seconds"] = float(run.meta.get("seconds") or 0.0)
        item["review"] = pe.load_counts([])  # a chatbot asks nobody about anything
        items.append(item)
    return items


def cmd_chat_items(args: argparse.Namespace) -> int:
    items = chat_items(args.part, args.arm)
    out = runs_dir(args.part) / "chat" / f"{args.arm}.items.jsonl"
    out.write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")
    for item in items:
        print(
            item["id"],
            item["status"],
            f"${item['cost_usd']:.4f}",
            item["matched"],
            "/",
            item["n_gold"],
        )
    print(f"wrote {out}")
    return 0


def cmd_tally(args: argparse.Namespace) -> int:
    """Spend of everything recorded, from the recorded answers. A run that started from another
    run's answers (`--seed-answers-from`) holds copies of them: each answer is counted once,
    under the first run that holds it (`rs` first, then by tag)."""
    rows = []
    for part in ("dev", "dev2", "heldout", "fresh", "fresh2", "fresh3"):
        counted: set[str] = set()
        for tag_dir in sorted(
            runs_dir(part).glob("*/answers.jsonl"),
            key=lambda p: (p.parent.name != "rs", p.parent.name),
        ):
            cost = 0.0
            for line in tag_dir.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("k") in counted:
                    continue
                counted.add(row.get("k"))
                cost += float((row.get("call") or {}).get("cost_usd") or 0.0)
            rows.append((part, f"rs:{tag_dir.parent.name}", cost, 0))
        systems = runs_dir(part) / "chat" / "systems"
        for arm_dir in sorted(systems.glob("*")) if systems.is_dir() else []:
            jobs = list(arm_dir.glob("*.json"))
            cost = sum(float(json.loads(j.read_text()).get("cost_usd") or 0) for j in jobs)
            pages = sum(int(json.loads(j.read_text()).get("pdf_pages") or 0) for j in jobs)
            rows.append((part, arm_dir.name, cost, pages))
    total = 0.0
    for part, arm, cost, pages in rows:
        total += cost
        per_page = f"  ${cost / pages:.4f}/page ({pages} pages)" if pages else ""
        print(f"{part:8s} {arm:28s} ${cost:8.4f}{per_page}")
    print(f"{'total':37s} ${total:8.4f}  (hard cap $20)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (
        ("estimate", cmd_estimate),
        ("rs", cmd_rs),
        ("chat", cmd_chat),
        ("chat-items", cmd_chat_items),
        ("tally", cmd_tally),
    ):
        p = sub.add_parser(name)
        if name != "tally":
            p.add_argument(
                "--part",
                choices=["dev", "dev2", "heldout", "fresh", "fresh2", "fresh3"],
                required=True,
            )
        if name == "rs":
            p.add_argument("--max-usd", type=float, required=True)
            p.add_argument("--tag", default="rs")
            p.add_argument("--offline", action="store_true")
            p.add_argument("--page-math", action="store_true", help="FEATURE_PAGE_MATH on")
            p.add_argument("--tables", action="store_true", help="FEATURE_TABLES on")
            p.add_argument("--line-context", action="store_true", help="EQUATION_LINE_CONTEXT on")
            p.add_argument(
                "--notation-memory", action="store_true", help="FEATURE_NOTATION_MEMORY on"
            )
            p.add_argument("--page-math-tokens", type=int, help="PAGE_MATH_MAX_OUTPUT_TOKENS")
            p.add_argument("--no-agreement-lift", action="store_true")
            p.add_argument(
                "--seed-answers-from", help="tag whose answers file this run starts from"
            )
            p.add_argument("--papers", help="comma-separated arXiv ids (default: all of the part)")
            p.add_argument("--note", default="")
        if name in ("chat", "chat-items"):
            p.add_argument("--arm", choices=sorted(chatbot.ARM_BY_ID), required=True)
        if name == "chat":
            p.add_argument("--max-usd", type=float, required=True)
            p.add_argument("--workers", type=int, default=2)
            p.add_argument("--papers", help="comma-separated arXiv ids (default: all of the part)")
            p.add_argument("--first", type=int, help="the first N papers of the registered order")
            p.add_argument("--note", default="")
        p.set_defaults(func=func)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
