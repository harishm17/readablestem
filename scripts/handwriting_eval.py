"""The handwriting evaluation (docs/eval-handwriting.md): fetch the ink, build the set, run, replay.

    uv run python scripts/handwriting_eval.py fetch      # download + verify + unpack MathWriting
    uv run python scripts/handwriting_eval.py build      # compose pages, write gold and manifest
    uv run python scripts/handwriting_eval.py verify     # rebuild and compare with the manifest
    uv run --env-file .env python scripts/handwriting_eval.py run --part dev --max-usd 2
    uv run python scripts/handwriting_eval.py run --part heldout --offline   # replay, no spend
    uv run python scripts/handwriting_eval.py report --part heldout          # the tables

The data is Google's MathWriting dataset, CC BY-NC-SA 4.0: it is downloaded to
`~/.cache/readablestem/handwriting`, never committed. What is committed is
`data/handwriting/manifest.json` (identifiers, hashes, placement), the registration
(`data/handwriting/PREREGISTRATION.md`, hashed in `REGISTRATION.sha256`) and the results.

`run` refuses to call a model unless the registered files still hash as registered, and a part is
recorded live once: a marker file stops a second live run; `--offline` replays the answers file.
Each live run appends to `.local/handwriting/spend-log.jsonl`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

from readablestem.eval import handwriting_set as hw

REPO = Path(__file__).resolve().parent.parent
CACHE = Path.home() / ".cache" / "readablestem" / "handwriting"
ARCHIVE_URL = "https://storage.googleapis.com/mathwriting_data/mathwriting-2024.tgz"
ARCHIVE_SHA256 = "f1977f120b0efa8abf715ac544b0596b213b099133da99b5e64f946730929269"
RAW = CACHE / "mathwriting-raw"
MANIFEST = REPO / "data" / "handwriting" / "manifest.json"
REGISTRATION = REPO / "data" / "handwriting" / "REGISTRATION.sha256"
RUNS = Path(os.environ.get("HANDWRITING_RUNS", REPO / ".local" / "handwriting"))
SEED = "handwriting-2026-10-21"
SCAN_SEED = 20261021
# part: (MathWriting split the ink comes from, units). Dev is the valid split, held-out the test
# split: each is the dataset's own, and the two share no expression (the dataset's rule).
PARTS = {"dev": ("valid", 5), "heldout": ("test", 12)}
POOL_SIZE = 500


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ink_dir(split: str) -> Path:
    return RAW / "mathwriting-2024" / split


def fetch() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / "mathwriting-2024.tgz"
    if not archive.is_file():
        print(f"downloading {ARCHIVE_URL}")
        urllib.request.urlretrieve(ARCHIVE_URL, archive)
    digest = hashlib.sha256()
    with archive.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != ARCHIVE_SHA256:
        raise SystemExit(f"archive hash {digest.hexdigest()} is not {ARCHIVE_SHA256}")
    if ink_dir("test").is_dir() and ink_dir("valid").is_dir():
        print("already unpacked")
        return
    RAW.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        members = [
            m
            for m in tar
            if m.isfile()
            and m.name.split("/")[1:2] in (["valid"], ["test"])
            and ".." not in m.name
            and not m.name.startswith("/")
        ]
        tar.extractall(RAW, members=members, filter="data")
    print(f"unpacked {len(members)} files to {RAW}")


def candidates_for(split: str) -> dict[str, hw.Candidate]:
    ids = hw.ranked(SEED, split, [p.stem for p in ink_dir(split).glob("*.inkml")])
    found: dict[str, hw.Candidate] = {}
    for sample_id in ids:
        try:
            ink = hw.read_ink(ink_dir(split) / f"{sample_id}.inkml")
        except (hw.InkError, ValueError):
            continue
        item = hw.candidate(ink)
        if item is not None and ink.label not in {c.ink.label for c in found.values()}:
            found[sample_id] = item
        if len(found) >= POOL_SIZE:
            break
    return found


def font_hashes() -> dict[str, str]:
    return {p.name: sha256(p) for p in (hw.FONT_PATH, hw.FONT_BOLD_PATH)}


def build(out_root: Path) -> dict:
    """Compose both parts into `out_root/<part>/` and return the manifest."""
    manifest: dict = {
        "seed": SEED,
        "scan_seed": SCAN_SEED,
        "source": {
            "name": "MathWriting (Google LLC)",
            "url": hw.SOURCE_URL,
            "archive_url": ARCHIVE_URL,
            "archive_sha256": ARCHIVE_SHA256,
            "license": hw.LICENSE_NAME,
            "license_url": hw.LICENSE_URL,
        },
        "fonts": font_hashes(),
        "recipe": {
            "page_px": [hw.PAGE_W, hw.PAGE_H],
            "blocks_per_page": hw.BLOCKS_PER_PAGE,
            "pages_per_unit": hw.PAGES_PER_UNIT,
            "pool_size": POOL_SIZE,
        },
        "parts": {},
    }
    for part, (split, units) in PARTS.items():
        found = candidates_for(split)
        pool = hw.Pool(list(found.values()))
        directory = out_root / part
        (directory / "pdfs").mkdir(parents=True, exist_ok=True)
        entries = []
        gold_lines: list[str] = []
        for number in range(1, units + 1):
            unit_id = f"hw-{part}-{number:02d}"
            images, plan = hw.compose_unit(unit_id, pool, SEED)
            pdf, made = hw.unit_pdf(images, SCAN_SEED + number)
            name = f"pdfs/{unit_id}.pdf"
            (directory / name).write_bytes(pdf)
            records = hw.gold_records(
                plan, found, part=part, pdf_name=name, pdf_sha256=hw.pdf_content_sha256(pdf)
            )
            lines = [json.dumps(r, sort_keys=True, ensure_ascii=False) for r in records]
            (directory / f"{unit_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
            gold_lines.extend(lines)
            entries.append(
                {
                    "unit": unit_id,
                    "pages": len(images),
                    "pen": plan.pen,
                    "ruled": plan.ruled,
                    "pdf_sha256": hw.pdf_content_sha256(pdf),
                    "gold_sha256": sha256(directory / f"{unit_id}.jsonl"),
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
        part_manifest = {
            "part": part,
            "source_split": split,
            "units": entries,
            "n_units": len(entries),
            "n_pages": sum(e["pages"] for e in entries),
            "n_expressions": sum(len(e["expressions"]) for e in entries),
            "n_display": sum(x["display"] == "block" for e in entries for x in e["expressions"]),
        }
        manifest["parts"][part] = part_manifest
        (directory / "manifest.json").write_text(
            json.dumps(part_manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
    return manifest


def dump(manifest: dict) -> str:
    return json.dumps(manifest, indent=1, sort_keys=True) + "\n"


def cmd_build() -> int:
    manifest = build(CACHE / "gold")
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(dump(manifest), encoding="utf-8")
    for part, data in manifest["parts"].items():
        print(
            f"{part}: {data['n_units']} units, {data['n_pages']} pages, "
            f"{data['n_expressions']} expressions ({data['n_display']} display)"
        )
    print(f"manifest sha256 {sha256(MANIFEST)}")
    return 0


def cmd_verify() -> int:
    rebuilt = build(CACHE / "verify")
    if dump(rebuilt) == MANIFEST.read_text(encoding="utf-8"):
        print("verified: the rebuilt set is the manifest's, byte for byte")
        return 0
    old = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for part, data in rebuilt["parts"].items():
        for new, was in zip(data["units"], old["parts"][part]["units"], strict=False):
            if new != was:
                print(f"differs: {new['unit']} (pdf {new['pdf_sha256'] == was['pdf_sha256']})")
    print("the rebuilt set differs from the manifest (a different Pillow, pymupdf or font?)")
    return 1


# ------------------------------------------------------------------------------- the run


def require_registration(part: str, offline: bool) -> None:
    if not REGISTRATION.is_file():
        raise SystemExit("not registered: data/handwriting/REGISTRATION.sha256 is missing")
    for line in REGISTRATION.read_text().splitlines():
        digest, _, name = line.partition("  ")
        path = REPO / name
        if not path.is_file() or sha256(path) != digest:
            raise SystemExit(f"registered file {name} has changed since registration")
    marker = RUNS / f"{part}.recorded"
    if marker.is_file() and not offline:
        raise SystemExit(f"{part} was already recorded: replay with --offline")


def answers_cost(path: Path) -> float:
    if not path.is_file():
        return 0.0
    total = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            total += float((json.loads(line).get("call") or {}).get("cost_usd") or 0.0)
    return total


def cmd_run(args: argparse.Namespace) -> int:
    # The registered settings: Opus reads the pages, no second automated check, defaults else.
    os.environ["CLAUDE_SECOND_CHECK"] = "off"
    os.environ["FEATURE_IMAGE_PAGES"] = "true"
    os.environ["IMAGE_PAGES_MODEL"] = "claude-opus-5-5"
    os.environ.pop("IMAGE_PAGES_DPI", None)
    os.environ.pop("ANTHROPIC_EFFORT_IMAGE_PAGES", None)
    from readablestem.cli import eval_cli
    from readablestem.cli.main import build_parser
    from readablestem.eval.realworld_media import RealWorldMedia

    require_registration(args.part, args.offline)
    gold = CACHE / "gold" / args.part
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))["parts"][args.part]
    for entry in manifest["units"]:  # the cached set must be the registered one
        pdf = gold / "pdfs" / f"{entry['unit']}.pdf"
        if not pdf.is_file() or hw.pdf_content_sha256(pdf.read_bytes()) != entry["pdf_sha256"]:
            raise SystemExit(f"{entry['unit']}: the cached PDF is not the manifest's (run build)")
    out_dir = RUNS / args.part
    out_dir.mkdir(parents=True, exist_ok=True)
    answers, report, items = (
        out_dir / "answers.jsonl",
        out_dir / "report.json",
        out_dir / "items.jsonl",
    )
    argv = [
        "eval", "pipeline", "--task", "document", "--provider", "anthropic",
        "--answers", str(answers), "--items", str(items), "--out", str(report),
        "--section-workers", "1", "--cache", str(out_dir / "cache"),
        "--max-usd", str(args.max_usd),
    ]  # fmt: skip
    if args.offline:
        argv.append("--answers-offline")
    ns = build_parser().parse_args(argv)
    before = answers_cost(answers)
    code = eval_cli._run_pipeline(
        ns,
        gold,
        report,
        gold_label=f"handwriting ({args.part})",
        media_cls=RealWorldMedia,
        cache_dir=out_dir / "cache",
        openstax_cache=out_dir / "cache",
    )
    spent = answers_cost(answers) - before
    if code == 0 and not args.offline:
        entry = {
            "when": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "part": args.part,
            "cap_usd": args.max_usd,
            "actual_usd": round(spent, 4),
            "commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO, check=False
            ).stdout.strip(),
        }
        with (RUNS / "spend-log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        (RUNS / f"{args.part}.recorded").write_text(dt.date.today().isoformat())
    print(f"spent this start: ${spent:.4f}")
    return code


# ----------------------------------------------------------------------------- the report

BOOTSTRAP = 10_000
BOOTSTRAP_SEED = 20261021
BUCKETS = (("3-7 tokens", 3, 7), ("8-15 tokens", 8, 15), ("16-40 tokens", 16, 40))


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def _fmt(value: float | None) -> str:
    return "n.a." if value is None else f"{value:.3f}"


def _stratum(rows: list[dict]) -> dict:
    """Counts over gold equations: `rows` have `ok` (exact_lenient), `ok_sym`, `found`, `asked`."""
    n = len(rows)
    return {
        "n": n,
        "found": sum(r["found"] for r in rows),
        "exact_lenient": _rate(sum(r["ok"] for r in rows), n),
        "symbols": _rate(sum(r["ok_sym"] for r in rows), n),
        "silent_wrong_symbols": sum(
            r["found"] and not r["ok_sym"] and not r["asked"] for r in rows
        ),
        "silent_wrong_strict": sum(r["found"] and not r["ok"] and not r["asked"] for r in rows),
    }


def gold_rows(part: str) -> list[dict]:
    """One row per gold equation: the part's items joined with the gold's own fields."""
    gold = {}
    for path in sorted((CACHE / "gold" / part).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                gold[record["id"]] = record
    items = [
        json.loads(line)
        for line in (RUNS / part / "items.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    scored = {item["id"].split("/")[-1] for item in items}
    rows = []
    for item in items:
        flags = {f["gold_id"]: f for f in (item.get("image_pages") or {}).get("flags", [])}
        pairs = {p["gold_id"]: p for p in item["pairs"]}
        for record in gold.values():
            if record["page_slug"] != item["id"].split("/")[-1]:
                continue
            pair = pairs.get(record["id"])
            flag = flags.get(record["id"], {})
            rows.append(
                {
                    "unit": record["page_slug"],
                    "id": record["id"],
                    "display": record["display"],
                    "pen": record["pen"],
                    "ruled": "ruled" if record["ruled"] else "plain",
                    "tokens": record["tokens"],
                    "latex": record["latex"],
                    "found": pair is not None,
                    "ok": bool(pair and pair["exact_lenient"]),
                    "ok_sym": bool(pair and pair["exact_symbols"]),
                    "similarity": pair["symbols_similarity"] if pair else 0.0,
                    "asked": bool(flag.get("asked")),
                    "pred": pair["pred_latex"] if pair else None,
                    "pred_display": pair["pred_display"] if pair else None,
                }
            )
    del scored
    return rows


def cluster_interval(rows: list[dict], key: str) -> tuple[float, float]:
    import random

    units: dict[str, list[dict]] = {}
    for row in rows:
        units.setdefault(row["unit"], []).append(row)
    names = sorted(units)
    rng = random.Random(BOOTSTRAP_SEED)
    stats = []
    for _ in range(BOOTSTRAP):
        sample = [units[rng.choice(names)] for _ in names]
        n = sum(len(u) for u in sample)
        stats.append(sum(r[key] for u in sample for r in u) / n)
    stats.sort()
    return stats[int(0.025 * BOOTSTRAP)], stats[int(0.975 * BOOTSTRAP) - 1]


def cmd_report(args: argparse.Namespace) -> int:
    report = json.loads((RUNS / args.part / "report.json").read_text(encoding="utf-8"))
    metrics = report["metrics"]
    rows = gold_rows(args.part)
    flags = metrics["image_pages"]["equation_flags"]
    lo, hi = cluster_interval(rows, "ok")
    slo, shi = cluster_interval(rows, "ok_sym")
    out = {
        "part": args.part,
        "complete": report["complete"],
        "stopped_reason": report["stopped_reason"],
        "units": f"{report['n']} of {report['n_planned']}",
        "gold_equations": metrics["gold_equations"],
        "primary_exact_lenient": metrics["end_to_end_exact_lenient"],
        "primary_interval": [lo, hi],
        "symbols_exact_credit": metrics["secondary_symbols"]["end_to_end_exact_with_credit"],
        "symbols_exact": metrics["secondary_symbols"]["end_to_end_exact"],
        "symbols_interval_uncredited": [slo, shi],
        "detection_recall": metrics["detection_recall"],
        "detection_precision": metrics["detection_precision"],
        "silent_wrong_symbols": flags["all_symbols"]["wrong_not_asked"],
        "silent_wrong_strict": flags["all"]["wrong_not_asked"],
        "unmatched_predicted": metrics["unmatched_predicted"],
        "equation_flags": flags,
        "second_reading": metrics["image_pages"]["second_reading"],
        "image_pages": {
            k: metrics["image_pages"][k]
            for k in (
                "pages", "converted", "fell_back", "kept_as_image", "illegible",
                "cost_usd", "cost_per_page_usd", "seconds_per_page", "blocks_per_page",
                "required_per_converted_page", "optional_per_converted_page",
                "low_confidence_share", "by_kind",
            )
        },
        "cost_per_pdf_page_usd": report["cost"]["per_pdf_page_usd"],
        "budget_spent_usd": report["cost"]["budget_spent_usd"],
        "dropped_usd": report["cost"].get("dropped_usd"),
        "strata": {},
    }  # fmt: skip
    groups = {
        "all": lambda r: "all",
        "role": lambda r: r["display"],
        "pen": lambda r: r["pen"],
        "paper": lambda r: r["ruled"],
        "length": lambda r: next(n for n, a, b in BUCKETS if a <= r["tokens"] <= b),
        "unit": lambda r: r["unit"],
    }
    for name, key in groups.items():
        buckets: dict[str, list[dict]] = {}
        for row in rows:
            buckets.setdefault(key(row), []).append(row)
        out["strata"][name] = {k: _stratum(v) for k, v in sorted(buckets.items())}
    wrong = sorted((r for r in rows if r["found"] and not r["ok"]), key=lambda r: r["similarity"])
    out["worst_wrong"] = [
        {k: r[k] for k in ("id", "display", "latex", "pred", "similarity", "asked")}
        for r in wrong[:12]
    ]
    out["silent_wrong_examples"] = [
        {k: r[k] for k in ("id", "display", "latex", "pred", "similarity")}
        for r in rows
        if r["found"] and not r["ok_sym"] and not r["asked"]
    ]
    out["missed"] = [
        {k: r[k] for k in ("id", "display", "latex", "asked")} for r in rows if not r["found"]
    ]
    target = RUNS / args.part / "summary.json"
    target.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"primary exact_lenient {_fmt(out['primary_exact_lenient'])} "
        f"[{_fmt(lo)}, {_fmt(hi)}] of {out['gold_equations']} gold equations"
    )
    print(
        f"symbols credited {_fmt(out['symbols_exact_credit'])}; "
        f"recall {_fmt(out['detection_recall'])}, precision {_fmt(out['detection_precision'])}"
    )
    print(
        f"silent wrong: symbols {out['silent_wrong_symbols']}, strict {out['silent_wrong_strict']}"
    )
    for name in ("role", "pen", "paper", "length", "unit"):
        print(f"-- by {name}")
        for key, st in out["strata"][name].items():
            print(
                f"{key:14} n={st['n']:3} found={st['found']:3} strict={_fmt(st['exact_lenient'])} "
                f"symbols={_fmt(st['symbols'])} silent(sym)={st['silent_wrong_symbols']}"
            )
    print(f"wrote {target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch")
    sub.add_parser("build")
    sub.add_parser("verify")
    rep = sub.add_parser("report")
    rep.add_argument("--part", choices=sorted(PARTS), required=True)
    run = sub.add_parser("run")
    run.add_argument("--part", choices=sorted(PARTS), required=True)
    run.add_argument("--max-usd", type=float, required=True)
    run.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.command == "fetch":
        fetch()
        return 0
    if args.command == "build":
        return cmd_build()
    if args.command == "verify":
        return cmd_verify()
    if args.command == "report":
        return cmd_report(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
