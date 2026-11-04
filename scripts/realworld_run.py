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

The fifth fresh split (`PREREGISTRATION-FRESH5.md`) has four strata in five gold folders:
`fresh5a` (arXiv), `fresh5b` (OpenStax, printed PDFs), `fresh5c` (course LaTeX), `fresh5d` and
`fresh5d-private` (OpenStax chemistry; the private folder never enters the repository). `rs` runs
one folder with `--settings fresh5-control` or `fresh5-candidate` (the registered settings, in a
clean environment), and `--cap-group` holds the two ReadableSTEM arms to one cap together. The
chatbots run with `--part fresh5`: every unit of the split in the registered order (strata A to
C, then D) within one cap, answers under `runs/fresh5/chat/systems`; `chat-items --part fresh5a`
(and so on) scores one folder from them. The strong-prompt arm is `chatbot-opus-expert`.

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
from readablestem.eval.render import RenderError
from readablestem.proof import chatbot
from readablestem.proof import systems as S

ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "realworld"
SPEND_LOG = DATA / "spend-log.jsonl"
# Per PDF page, list price, used only to size a run before it is paid for (from the OpenStax
# held-out runs, 2026-10-14 and 2026-10-21); the real figure is measured and logged.
ESTIMATE_PER_PAGE = {
    "rs": 0.035,
    "chatbot-haiku-purdue": 0.003,
    "chatbot-opus-purdue": 0.060,
    "chatbot-opus-expert": 0.090,
}
# The strong-prompt Opus arm of fresh5 (registration section 3): the `expert` prompt of
# `proof/chat_prompts.json` on the Opus model, one reply, no system prompt.
STRONG_ARM = chatbot.ChatArm(
    "chatbot-opus-expert", "claude-opus-5-5", "expert", 1, "secondary", "anthropic"
)
ARM_BY_ID = {**chatbot.ARM_BY_ID, STRONG_ARM.id: STRONG_ARM}
FRESH5_PARTS = ("fresh5a", "fresh5b", "fresh5c", "fresh5d", "fresh5d-private")
FRESH5_PRIMARY = ("fresh5a", "fresh5b", "fresh5c")
FRESH5_SEED = "realworld-fresh5-2026-11-02"
PARTS = ("dev", "dev2", "heldout", "fresh", "fresh2", "fresh3", "fresh4", *FRESH5_PARTS)
# The registered settings of the two ReadableSTEM arms of fresh5 (registration section 3); a
# run gets these and nothing else from its environment but the key.
_FRESH5_COMMON = {
    "FEATURE_TABLES": "true",
    "FEATURE_NOTATION_MEMORY": "true",
    "EQUATION_LINE_CONTEXT": "true",
    "FEATURE_PAGE_WITNESS": "false",
    "LAYOUT_DISPLAY_GROUPING": "false",
    "FEATURE_PAGE_MATH": "false",
    "CLAUDE_SECOND_CHECK": "off",
    "ANTHROPIC_MAIN_MODEL": "claude-opus-5-5",
    "ANTHROPIC_CHEAP_MODEL": "claude-haiku-5-5",
    "ANTHROPIC_EFFORT_MAIN": "medium",
    "ANTHROPIC_EFFORT_CHEAP": "low",
}
FRESH5_SETTINGS = {
    "fresh5-control": _FRESH5_COMMON
    | {
        "EQUATION_GLYPH_CHECKS": "false",
        "FLAG_POLICY": "false",
        "EQUATION_GLYPH_RECONCILE": "false",
    },
    "fresh5-candidate": _FRESH5_COMMON
    | {"EQUATION_GLYPH_CHECKS": "true", "FLAG_POLICY": "true", "EQUATION_GLYPH_RECONCILE": "true"},
}
# What a registered fresh5 run may carry from its environment besides its settings.
ENV_KEEP = ("PATH", "HOME", "ANTHROPIC_API_KEY", "LANG", "TMPDIR", "UV_CACHE_DIR", "VIRTUAL_ENV")


def gold_dir(part: str) -> Path:
    return ROOT / "gold" / part


def runs_dir(part: str) -> Path:
    return ROOT / "runs" / part


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(part: str) -> dict:
    return json.loads((gold_dir(part) / "manifest.json").read_text(encoding="utf-8"))


def is_openstax_part(part: str) -> bool:
    """A folder of OpenStax gold whose pages were printed beforehand (fresh5 strata B and D)."""
    return part in ("fresh5b", "fresh5d", "fresh5d-private")


def openstax_sections(part: str) -> list[dict]:
    """The registered sections of an OpenStax folder (`selection-fresh5-B.json`, `-D.json`):
    book, section, gold and PDF hashes. D's public section is in `fresh5d`, its private ones in
    `fresh5d-private`."""
    name = "selection-fresh5-B.json" if part == "fresh5b" else "selection-fresh5-D.json"
    sections = json.loads((DATA / name).read_text(encoding="utf-8"))["sections"]
    if part == "fresh5d":
        sections = [s for s in sections if s.get("licence_tier") == "public"]
    elif part == "fresh5d-private":
        sections = [s for s in sections if s.get("licence_tier") == "private"]
    return sections


def openstax_pdf(part: str, book: str, section: str) -> Path:
    return gold_dir(part) / "pdfs" / f"{book}-{section}.pdf"


def n_pages(part: str) -> int:
    if is_openstax_part(part):
        return sum(
            chatbot.pdf_page_count(openstax_pdf(part, s["book"], s["section"]))
            for s in openstax_sections(part)
        )
    return int(manifest(part)["n_pages"])


def verify_gold(part: str) -> None:
    """The gold and the cut PDFs on disk are the ones the manifest names (an OpenStax folder:
    the ones its registered selection names)."""
    if is_openstax_part(part):
        sections = openstax_sections(part)
        if not sections:
            raise SystemExit(f"{part}: no registered sections")
        for s in sections:
            gold = gold_dir(part) / "openstax" / s["book"] / f"{s['section']}.jsonl"
            if sha256(gold) != s["gold_sha256"]:
                raise SystemExit(f"gold of {s['book']}/{s['section']} differs from the selection")
            if sha256(openstax_pdf(part, s["book"], s["section"])) != s["pdf_sha256"]:
                raise SystemExit(f"PDF of {s['book']}/{s['section']} differs from the selection")
        return
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
    "fresh4": "REGISTRATION-fresh4.sha256",
    **{part: "REGISTRATION-fresh5.sha256" for part in (*FRESH5_PARTS, "fresh5")},
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
    fresh = ("fresh", "fresh2", "fresh3", "fresh4", *FRESH5_PARTS)
    return args.tag if args.part in fresh else "rs"


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
    pages = n_pages(args.part)
    print(f"{args.part}: {len(unit_pdfs(args.part))} units, {pages} pages")
    for arm, per_page in ESTIMATE_PER_PAGE.items():
        print(f"  {arm:24s} ${pages * per_page:6.2f}  (at ${per_page}/page)")
    return 0


# ------------------------------------------------------------------------------ ReadableSTEM


class PrintedOpenStaxMedia(RealWorldMedia):
    """An OpenStax folder of fresh5: each section's page is the Letter PDF printed when the
    split was built (`pdfs/<book>-<section>.pdf`, hashed in its selection file), not a new
    print of the archive."""

    def page_pdf(self, page_records: list[dict]) -> bytes:
        first = page_records[0]
        path = self.gold_dir / "pdfs" / f"{first['book_slug']}-{first['page_slug']}.pdf"
        if not path.is_file():
            raise RenderError(f"PDF {path.name} is missing under {self.gold_dir}")
        return path.read_bytes()


def apply_registered_settings(name: str) -> None:
    """Put the registered settings of a fresh5 arm in the environment, after checking that
    nothing else could reach the settings: no `.env` file here and no other setting variable."""
    from readablestem.config import Settings

    preset = FRESH5_SETTINGS[name]
    fields = {f.upper() for f in Settings.model_fields}
    stray = sorted(k for k in os.environ if k.upper() in fields and k not in preset)
    stray = [k for k in stray if k not in ENV_KEEP]
    if stray:
        raise SystemExit(f"--settings {name}: other settings in the environment: {stray}")
    if Path(".env").exists():
        raise SystemExit(f"--settings {name}: a .env file here would add settings")
    os.environ.update(preset)


def answers_spent(paths: list[Path]) -> float:
    """What the answers in these files cost, each answer (by its key) counted once: a run that
    started from another's answers holds copies of them."""
    seen: set[str] = set()
    total = 0.0
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = row.get("k") or line
            if key in seen:
                continue
            seen.add(key)
            total += float((row.get("call") or {}).get("cost_usd") or 0.0)
    return total


def merge_answers(source: Path, target: Path) -> int:
    """Append to `target` the answers of `source` whose key it does not hold; returns how many."""
    if not source.is_file():
        return 0
    held = {
        json.loads(line).get("k")
        for line in target.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    new = [
        line
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("k") not in held
    ]
    if new:
        with target.open("a", encoding="utf-8") as handle:
            handle.write("".join(line + "\n" for line in new))
    return len(new)


def group_answers(tags: list[str]) -> list[Path]:
    return [runs_dir(part) / tag / "answers.jsonl" for part in FRESH5_PARTS for tag in tags]


def cmd_rs(args: argparse.Namespace) -> int:
    if args.settings:
        if args.part not in FRESH5_PARTS:
            raise SystemExit("--settings is for the fresh5 folders")
        apply_registered_settings(args.settings)
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
    if args.glyph_checks:
        os.environ["EQUATION_GLYPH_CHECKS"] = "true"
    if args.glyph_reconcile:
        os.environ["EQUATION_GLYPH_RECONCILE"] = "true"
    if args.flag_policy:
        os.environ["FLAG_POLICY"] = "true"
    if args.no_agreement_lift:
        os.environ["PAGE_MATH_AGREEMENT_LIFT"] = "false"
    if args.page_math_tokens:
        os.environ["PAGE_MATH_MAX_OUTPUT_TOKENS"] = str(args.page_math_tokens)
    from readablestem.cli import eval_cli
    from readablestem.cli.main import build_parser

    verify_gold(args.part)
    require_registration(args.part, registered_arm(args), args.offline)
    max_usd = args.max_usd
    if args.group_cap is not None:
        # the arms of one cap together (fresh5: control and candidate $7.5): this start may
        # spend what the group has left
        spent = answers_spent(group_answers(args.cap_group))
        max_usd = round(min(max_usd, args.group_cap - spent), 4)
        print(
            f"group {args.cap_group}: spent ${spent:.4f} of ${args.group_cap}; "
            f"this start ${max_usd}"
        )
        if max_usd <= 0 and not args.offline:
            raise SystemExit("the group's cap is spent")
    out_dir = runs_dir(args.part) / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    answers = out_dir / "answers.jsonl"
    if args.seed_answers_from and not answers.exists():
        # an experiment that asks mostly the same questions starts from the baseline's answers:
        # a request already answered replays for free and is not paid for twice
        shutil.copyfile(runs_dir(args.part) / args.seed_answers_from / "answers.jsonl", answers)
    elif args.seed_answers_from:
        # started before (a trial run): the baseline's answers recorded since are added
        merge_answers(runs_dir(args.part) / args.seed_answers_from / "answers.jsonl", answers)
    report = out_dir / "report.json"
    items = out_dir / "items.jsonl"
    argv = [
        "eval", "pipeline", "--task", "document", "--provider", "anthropic",
        "--answers", str(answers), "--items", str(items), "--out", str(report),
        "--section-workers", str(args.section_workers),
        "--concurrency", str(args.concurrency), "--cache", str(out_dir / "cache"),
        "--max-usd", str(max(max_usd, 0.01)),
    ]  # fmt: skip
    if args.offline:
        argv.append("--answers-offline")
    if args.papers:
        argv += ["--sections", *(f"arxiv/{i.replace('/', '_')}" for i in args.papers.split(","))]
    if args.units:
        argv += ["--sections", *args.units.split(",")]
    ns = build_parser().parse_args(argv)
    before = answers_cost(answers)
    code = eval_cli._run_pipeline(
        ns,
        gold_dir(args.part),
        report,
        gold_label=f"realworld ({args.part})",
        media_cls=PrintedOpenStaxMedia if is_openstax_part(args.part) else RealWorldMedia,
        cache_dir=out_dir / "cache",
        openstax_cache=out_dir / "cache",
    )
    spent = answers_cost(answers) - before
    if code == 0 and not args.offline:
        log_spend(
            {
                "part": args.part, "arm": "rs", "tag": args.tag,
                "estimate_usd": round(n_pages(args.part) * ESTIMATE_PER_PAGE["rs"], 2),
                "cap_usd": max_usd, "actual_usd": round(spent, 4), "note": args.note,
                **({"settings": args.settings} if args.settings else {}),
                **({"cap_group": args.cap_group, "group_cap_usd": args.group_cap}
                   if args.group_cap is not None else {}),
                **({"units": args.units} if args.units else {}),
            }
        )  # fmt: skip
        if args.part in REGISTRATIONS and not args.units:
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


def unit_pdfs(part: str) -> dict[str, Path]:
    """Unit id (`<book>/<page>` of its gold, the document task's id) -> its PDF, for one folder."""
    found: dict[str, Path] = {}
    for unit in pe.group_pages(pe.load_gold(gold_dir(part))):
        uid = pe._page_id(unit)
        first = unit[0]
        if is_openstax_part(part):
            found[uid] = openstax_pdf(part, first["book_slug"], first["page_slug"])
        else:
            found[uid] = gold_dir(part) / first["pdf"]
    return found


def fresh5_order() -> list[tuple[str, str]]:
    """(folder, unit id) of every unit of fresh5 in the registered order (section 6): strata
    A to C by SHA-256 of seed, "chat-order" and the unit id, then stratum D the same way."""

    def key(uid: str) -> str:
        return hashlib.sha256(f"{FRESH5_SEED}|chat-order|{uid}".encode()).hexdigest()

    def ordered(parts: tuple[str, ...]) -> list[tuple[str, str]]:
        units = [(part, uid) for part in parts for uid in unit_pdfs(part)]
        return sorted(units, key=lambda pu: key(pu[1]))

    return ordered(FRESH5_PRIMARY) + ordered(("fresh5d", "fresh5d-private"))


def part_of_unit(uid: str) -> str:
    return next(part for part, u in fresh5_order() if u == uid)


def pdfs_of(part: str, only: list[str] | None = None) -> dict[str, Path]:
    found = {}
    for paper in papers(part):
        if only and paper["id"] not in only:
            continue
        slug = paper["id"].replace("/", "_")
        found[f"arxiv/{slug}"] = gold_dir(part) / "pdfs" / f"{slug}.pdf"
    return found


def cmd_chat_fresh5(args: argparse.Namespace) -> int:
    """A chatbot arm on every unit of fresh5, in the registered order, within the arm's cap
    (what the arm has already spent on the split counts against it)."""
    arm = ARM_BY_ID[args.arm]
    for part in FRESH5_PARTS:
        verify_gold(part)
    require_registration("fresh5", arm.id, False)
    order = fresh5_order()
    paths = {part: unit_pdfs(part) for part in FRESH5_PARTS}
    pdfs = {uid: paths[part][uid] for part, uid in order}
    if args.units:
        wanted = args.units.split(",")
        unknown = [u for u in wanted if u not in pdfs]
        if unknown:
            raise SystemExit(f"not units of fresh5: {unknown}")
        pdfs = {uid: path for uid, path in pdfs.items() if uid in wanted}
    if args.dry:
        return chat_dry(arm, pdfs)
    out = runs_dir("fresh5") / "chat" / "systems"
    before = chatbot.recorded_cost(out, [arm.id])
    remaining = round(args.max_usd - before, 6)
    pages = sum(chatbot.pdf_page_count(p) for p in pdfs.values())
    estimate = pages * ESTIMATE_PER_PAGE[arm.id]
    print(
        f"{arm.id}: {len(pdfs)} units, {pages} pages, estimate ${estimate:.2f}; "
        f"arm cap ${args.max_usd}, spent ${before:.4f}, left ${remaining:.4f}",
        flush=True,
    )
    if remaining <= 0:
        raise SystemExit("the arm's cap is spent")
    client = chatbot.make_client(arm.provider)
    summary: dict = {}
    for attempt in range(4):  # a connection error while counting tokens ends one start
        try:
            summary = chatbot.record_arms(
                pdfs, out, arms=[arm], max_usd=remaining, client=client,
                workers=args.workers, runs=1,
            )  # fmt: skip
            break
        except Exception as exc:  # recorded jobs are skipped on the next start
            print(f"start {attempt + 1} stopped: {type(exc).__name__}", flush=True)
            time.sleep(30)
            client = chatbot.make_client(arm.provider)
            remaining = round(args.max_usd - chatbot.recorded_cost(out, [arm.id]), 6)
    else:
        raise SystemExit("the arm could not be recorded after 4 starts")
    spent = chatbot.recorded_cost(out, [arm.id]) - before
    print(json.dumps(summary))
    log_spend(
        {
            "part": "fresh5", "arm": arm.id, "papers": len(pdfs), "pages": pages,
            "estimate_usd": round(estimate, 2), "cap_usd": args.max_usd,
            "spent_before_usd": round(before, 4), "actual_usd": round(spent, 4),
            "refused_by_cap": summary["refused_by_cap"], "note": args.note,
            **({"units": args.units} if args.units else {}),
        }
    )  # fmt: skip
    recorded = {run for run in S.load_arms_dir(out).get(arm.id, {})}
    if not args.units and not summary["refused_by_cap"] and set(pdfs) <= recorded:
        (runs_dir("fresh5") / f"{arm.id}.recorded").write_text(dt.date.today().isoformat())
    return 0


def chat_dry(arm: chatbot.ChatArm, pdfs: dict[str, Path]) -> int:
    """A trial that spends nothing: each unit's message is built as the arm sends it (the PDF,
    a PNG of every page, the prompt) and its input tokens are counted by the provider's free
    counting endpoint; no model is asked and nothing is recorded."""
    client = chatbot.make_client(arm.provider)
    price = None
    total_in = 0
    worst = 0.0
    for uid, pdf in pdfs.items():
        pngs = chatbot.page_pngs(pdf)
        job = chatbot.Job(arm, uid, 1, pdf)
        _, counted, reserve = chatbot._prepare(job, pngs, client)
        price = chatbot.pricing.price_for(arm.model).for_prompt(counted)
        total_in += counted
        worst += reserve
        print(f"{uid}: {len(pngs)} pages, {counted} input tokens, worst case ${reserve:.3f}")
    if price is not None:
        print(
            f"{arm.id}: {len(pdfs)} units, {total_in} input tokens "
            f"(${total_in * price.input / 1e6:.3f} of input), worst case ${worst:.2f}"
        )
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    if args.part == "fresh5":
        return cmd_chat_fresh5(args)
    verify_gold(args.part)
    arm = ARM_BY_ID[args.arm]
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
    where = "fresh5" if part in FRESH5_PARTS else part
    arms = S.load_arms_dir(runs_dir(where) / "chat" / "systems")
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
    for part in (*PARTS, "fresh5"):
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
            choices = [*PARTS, "fresh5"] if name == "chat" else list(PARTS)
            p.add_argument("--part", choices=choices, required=True)
        if name == "rs":
            p.add_argument("--max-usd", type=float, required=True)
            p.add_argument("--tag", default="rs")
            p.add_argument("--offline", action="store_true")
            p.add_argument("--page-math", action="store_true", help="FEATURE_PAGE_MATH on")
            p.add_argument("--tables", action="store_true", help="FEATURE_TABLES on")
            p.add_argument("--line-context", action="store_true", help="EQUATION_LINE_CONTEXT on")
            p.add_argument("--glyph-checks", action="store_true", help="EQUATION_GLYPH_CHECKS on")
            p.add_argument(
                "--glyph-reconcile", action="store_true", help="EQUATION_GLYPH_RECONCILE on"
            )
            p.add_argument("--flag-policy", action="store_true", help="FLAG_POLICY on")
            p.add_argument(
                "--notation-memory", action="store_true", help="FEATURE_NOTATION_MEMORY on"
            )
            p.add_argument("--page-math-tokens", type=int, help="PAGE_MATH_MAX_OUTPUT_TOKENS")
            p.add_argument("--no-agreement-lift", action="store_true")
            p.add_argument(
                "--seed-answers-from", help="tag whose answers file this run starts from"
            )
            p.add_argument("--papers", help="comma-separated arXiv ids (default: all of the part)")
            p.add_argument(
                "--units", help="comma-separated unit ids (`<book>/<page>`): a trial run"
            )
            p.add_argument(
                "--settings", choices=sorted(FRESH5_SETTINGS), help="a registered fresh5 arm"
            )
            p.add_argument(
                "--cap-group", nargs="+", default=["control", "candidate"],
                help="tags that share --group-cap (their answers over all fresh5 folders)",
            )  # fmt: skip
            p.add_argument("--group-cap", type=float, help="the cap of the --cap-group tags")
            p.add_argument(
                "--section-workers", type=int, default=3, help="papers converted at once"
            )
            p.add_argument(
                "--concurrency", type=int, default=4, help="worker threads inside one paper"
            )
            p.add_argument("--note", default="")
        if name in ("chat", "chat-items"):
            p.add_argument("--arm", choices=sorted(ARM_BY_ID), required=True)
        if name == "chat":
            p.add_argument("--max-usd", type=float, required=True)
            p.add_argument("--workers", type=int, default=2)
            p.add_argument("--papers", help="comma-separated arXiv ids (default: all of the part)")
            p.add_argument("--first", type=int, help="the first N papers of the registered order")
            p.add_argument("--units", help="fresh5: comma-separated unit ids (a trial run)")
            p.add_argument(
                "--dry", action="store_true",
                help="fresh5: build each message and count its tokens (free); ask nothing",
            )  # fmt: skip
            p.add_argument("--note", default="")
        p.set_defaults(func=func)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
