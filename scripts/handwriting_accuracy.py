"""Handwriting accuracy work (docs/eval-handwriting.md, "Root causes and fixes").

    uv run python scripts/handwriting_accuracy.py build-fresh    # compose the fresh held-out set
    uv run python scripts/handwriting_accuracy.py verify-fresh   # rebuild, compare with manifest
    uv run --env-file .env python scripts/handwriting_accuracy.py run --part dev --arm NAME \
        --max-usd 1 [--prompt FILE] [--env KEY=VALUE ...] [--offline]
    uv run python scripts/handwriting_accuracy.py report --part dev --arm NAME

It reuses the builder and the scorers of `handwriting_eval.py` (the registered, unchanged
original: MathWriting ink composed on pages, scored by the document task) and adds

* the fresh held-out part: MathWriting `test` ids and labels that neither the development part nor
  the first held-out part used, composed by the same builder with its own seed;
* arms: a named run with its own settings (`--env`) or its own image page prompt (`--prompt`),
  recorded into one answers file per part, so a request two arms share (the equation step's
  crops, mostly) is paid for once and every arm replays at no cost.

The data is Google's MathWriting dataset (CC BY-NC-SA 4.0): it stays in
`~/.cache/readablestem/handwriting`, never committed. Committed: the fresh manifest
(`data/handwriting/manifest-fresh.json`: identifiers and hashes) and the registration.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "handwriting_eval", REPO / "scripts" / "handwriting_eval.py"
)
assert _spec is not None and _spec.loader is not None
hwe = importlib.util.module_from_spec(_spec)
sys.modules["handwriting_eval"] = hwe
_spec.loader.exec_module(hwe)  # the registered script, unchanged
hw = hwe.hw

FRESH_MANIFEST = REPO / "data" / "handwriting" / "manifest-fresh.json"
REGISTRATION = REPO / "data" / "handwriting" / "REGISTRATION-ACCURACY.sha256"
FRESH_SEED = "handwriting-fresh-2026-10-22"
FRESH_SCAN_SEED = 20261022
FRESH_UNITS = 12
ARMS = REPO / ".local" / "hw-accuracy"
PARTS = ("dev", "heldout", "fresh")


# ------------------------------------------------------------------------ the fresh set


def used_by_registered_parts() -> tuple[set[str], set[str]]:
    """Sample ids and labels of the development and the first held-out part."""
    manifest = json.loads(hwe.MANIFEST.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for part in manifest["parts"].values():
        for unit in part["units"]:
            ids.update(x["sample_id"] for x in unit["expressions"])
    labels: set[str] = set()
    for part in ("dev", "heldout"):
        for path in sorted((hwe.CACHE / "gold" / part).glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    labels.add(json.loads(line)["latex"])
    return ids, labels


def fresh_candidates() -> dict[str, hw.Candidate]:
    """The pool of the fresh part: `test` ids in a new seeded order, minus everything the two
    registered parts used (by id and by label), first POOL_SIZE that pass the builder's filters."""
    ids, labels = used_by_registered_parts()
    split = "test"
    order = hw.ranked(FRESH_SEED, split, [p.stem for p in hwe.ink_dir(split).glob("*.inkml")])
    found: dict[str, hw.Candidate] = {}
    seen = set(labels)
    for sample_id in order:
        if sample_id in ids:
            continue
        try:
            ink = hw.read_ink(hwe.ink_dir(split) / f"{sample_id}.inkml")
        except (hw.InkError, ValueError):
            continue
        item = hw.candidate(ink)
        if item is not None and ink.label not in seen:
            found[sample_id] = item
            seen.add(ink.label)
        if len(found) >= hwe.POOL_SIZE:
            break
    return found


def build_fresh(out_root: Path) -> dict:
    found = fresh_candidates()
    pool = hw.Pool(list(found.values()))
    directory = out_root / "fresh"
    (directory / "pdfs").mkdir(parents=True, exist_ok=True)
    entries = []
    for number in range(1, FRESH_UNITS + 1):
        unit_id = f"hw-fresh-{number:02d}"
        images, plan = hw.compose_unit(unit_id, pool, FRESH_SEED)
        pdf, made = hw.unit_pdf(images, FRESH_SCAN_SEED + number)
        name = f"pdfs/{unit_id}.pdf"
        (directory / name).write_bytes(pdf)
        records = hw.gold_records(
            plan, found, part="fresh", pdf_name=name, pdf_sha256=hw.pdf_content_sha256(pdf)
        )
        lines = [json.dumps(r, sort_keys=True, ensure_ascii=False) for r in records]
        (directory / f"{unit_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        entries.append(
            {
                "unit": unit_id,
                "pages": len(images),
                "pen": plan.pen,
                "ruled": plan.ruled,
                "pdf_sha256": hw.pdf_content_sha256(pdf),
                "gold_sha256": hwe.sha256(directory / f"{unit_id}.jsonl"),
                "scan_dpi": [d.dpi for d in made],
                "expressions": [
                    {
                        "id": r["id"],
                        "sample_id": r["sample_id"],
                        "inkml_sha256": found[r["sample_id"]].ink.sha256,
                        "page": r["pdf_page"],
                        "display": r["display"],
                        "tokens": r["tokens"],
                    }
                    for r in records
                ],
            }
        )
    part = {
        "part": "fresh",
        "source_split": "test",
        "units": entries,
        "n_units": len(entries),
        "n_pages": sum(e["pages"] for e in entries),
        "n_expressions": sum(len(e["expressions"]) for e in entries),
        "n_display": sum(x["display"] == "block" for e in entries for x in e["expressions"]),
    }
    (directory / "manifest.json").write_text(
        json.dumps(part, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "seed": FRESH_SEED,
        "scan_seed": FRESH_SCAN_SEED,
        "builder": "handwriting_eval.py and eval/handwriting_set.py, unchanged",
        "excluded": "every sample id and every label of the development and first held-out parts",
        "source": {
            "name": "MathWriting (Google LLC)",
            "url": hw.SOURCE_URL,
            "archive_sha256": hwe.ARCHIVE_SHA256,
            "license": hw.LICENSE_NAME,
        },
        "fonts": hwe.font_hashes(),
        "recipe": {
            "page_px": [hw.PAGE_W, hw.PAGE_H],
            "blocks_per_page": hw.BLOCKS_PER_PAGE,
            "pages_per_unit": hw.PAGES_PER_UNIT,
            "pool_size": hwe.POOL_SIZE,
        },
        "parts": {"fresh": part},
    }


def cmd_build_fresh() -> int:
    manifest = build_fresh(hwe.CACHE / "gold")
    FRESH_MANIFEST.write_text(hwe.dump(manifest), encoding="utf-8")
    data = manifest["parts"]["fresh"]
    print(
        f"fresh: {data['n_units']} units, {data['n_pages']} pages, {data['n_expressions']} "
        f"expressions ({data['n_display']} display)"
    )
    print(f"manifest sha256 {hwe.sha256(FRESH_MANIFEST)}")
    return 0


def cmd_verify_fresh() -> int:
    rebuilt = build_fresh(hwe.CACHE / "verify-fresh")
    if hwe.dump(rebuilt) == FRESH_MANIFEST.read_text(encoding="utf-8"):
        print("verified: the rebuilt fresh set is the manifest's, byte for byte")
        return 0
    print("the rebuilt fresh set differs from the manifest")
    return 1


# --------------------------------------------------------------------------------- arms


def arm_root(arm: str) -> Path:
    return ARMS / "arms" / arm


def shared_answers(part: str) -> Path:
    return ARMS / "answers" / f"{part}.jsonl"


def manifest_part(part: str) -> dict:
    path = FRESH_MANIFEST if part == "fresh" else hwe.MANIFEST
    return json.loads(path.read_text(encoding="utf-8"))["parts"][part]


def require_registration() -> None:
    """A live run on the fresh part needs the registered files to hash as registered."""
    if not REGISTRATION.is_file():
        raise SystemExit("not registered: data/handwriting/REGISTRATION-ACCURACY.sha256 is missing")
    for line in REGISTRATION.read_text().splitlines():
        digest, _, name = line.partition("  ")
        path = REPO / name
        if not path.is_file() or hwe.sha256(path) != digest:
            raise SystemExit(f"registered file {name} has changed since registration")


def cmd_run(args: argparse.Namespace) -> int:
    # The registered settings of the handwriting evaluation unless an arm changes them.
    os.environ["CLAUDE_SECOND_CHECK"] = "off"
    os.environ["FEATURE_IMAGE_PAGES"] = "true"
    os.environ["IMAGE_PAGES_MODEL"] = "claude-opus-5-5"
    os.environ.pop("IMAGE_PAGES_DPI", None)
    os.environ.pop("ANTHROPIC_EFFORT_IMAGE_PAGES", None)
    for item in args.env or []:
        key, _, value = item.partition("=")
        os.environ[key] = value
    if args.prompt:
        from readablestem.models import prompts
        from readablestem.pipeline import image_page

        text = Path(args.prompt).read_text(encoding="utf-8")
        import hashlib

        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        fixed = prompts.Prompt(name="image_page", version=f"image_page-{digest}", text=text)
        original = image_page.load_prompt
        image_page.load_prompt = lambda name: fixed if name == "image_page" else original(name)
        image_page.PROMPT_VERSION = fixed.version
    from readablestem.cli import eval_cli
    from readablestem.cli.main import build_parser
    from readablestem.eval.realworld_media import RealWorldMedia

    if args.part == "fresh" and not args.offline:
        require_registration()
    gold = hwe.CACHE / "gold" / args.part
    for entry in manifest_part(args.part)["units"]:
        pdf = gold / "pdfs" / f"{entry['unit']}.pdf"
        if not pdf.is_file() or hw.pdf_content_sha256(pdf.read_bytes()) != entry["pdf_sha256"]:
            raise SystemExit(f"{entry['unit']}: the cached PDF is not the manifest's (run build)")
    out_dir = arm_root(args.arm) / args.part
    out_dir.mkdir(parents=True, exist_ok=True)
    answers = shared_answers(args.part)
    answers.parent.mkdir(parents=True, exist_ok=True)
    marker = out_dir / "live.recorded"
    if args.part == "fresh" and marker.is_file() and not args.offline:
        raise SystemExit(f"fresh/{args.arm} was already recorded live: replay with --offline")
    report, items = out_dir / "report.json", out_dir / "items.jsonl"
    argv = [
        "eval", "pipeline", "--task", "document", "--provider", "anthropic",
        "--answers", str(answers), "--items", str(items), "--out", str(report),
        "--section-workers", "1", "--cache", str(out_dir / "cache"),
        "--max-usd", str(args.max_usd),
    ]  # fmt: skip
    if args.offline:
        argv.append("--answers-offline")
    ns = build_parser().parse_args(argv)
    before = hwe.answers_cost(answers)
    code = eval_cli._run_pipeline(
        ns,
        gold,
        report,
        gold_label=f"handwriting accuracy ({args.part}, arm {args.arm})",
        media_cls=RealWorldMedia,
        cache_dir=out_dir / "cache",
        openstax_cache=out_dir / "cache",
    )
    spent = hwe.answers_cost(answers) - before
    if code == 0 and not args.offline:
        entry = {
            "when": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "part": args.part,
            "arm": args.arm,
            "cap_usd": args.max_usd,
            "actual_usd": round(spent, 4),
            "env": args.env or [],
            "prompt": args.prompt,
            "code_root": str(Path(sys.modules["readablestem"].__file__ or "").parent.parent),
            "commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO, check=False
            ).stdout.strip(),
        }
        with (ARMS / "spend-log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        marker.write_text(dt.date.today().isoformat())
    print(f"spent this start: ${spent:.4f}")
    return code


def cmd_report(args: argparse.Namespace) -> int:
    hwe.RUNS = arm_root(args.arm)
    return hwe.cmd_report(SimpleNamespace(part=args.part))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build-fresh")
    sub.add_parser("verify-fresh")
    for name in ("run", "report"):
        p = sub.add_parser(name)
        p.add_argument("--part", choices=PARTS, required=True)
        p.add_argument("--arm", required=True)
        if name == "run":
            p.add_argument("--max-usd", type=float, required=True)
            p.add_argument("--offline", action="store_true")
            p.add_argument(
                "--prompt", help="an image page prompt file to use instead of the packaged one"
            )
            p.add_argument("--env", action="append", help="KEY=VALUE for the run's settings")
    args = parser.parse_args()
    if args.command == "build-fresh":
        return cmd_build_fresh()
    if args.command == "verify-fresh":
        return cmd_verify_fresh()
    if args.command == "report":
        return cmd_report(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
