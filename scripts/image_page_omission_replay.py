"""Measure the omission check of the image page pass by replay, at no cost (ADR 0007).

    uv run python scripts/image_page_omission_replay.py replay          # every set, both arms
    uv run python scripts/image_page_omission_replay.py replay --sets hw-dev scans-dev
    uv run python scripts/image_page_omission_replay.py simulate --sets hw-dev scans-dev real

`replay` runs each recorded set twice through the real pipeline with the model's recorded answers
(`--answers-offline`: a request the file cannot answer is refused, nothing is ever sent), once
with `IMAGE_PAGES_OMISSION_CHECK=false` ("off") and once with it on, and compares: pages
flagged, extra required decisions per page, the primary equation score and the silent wrong
equations (the image page pass's own numbers; an omission item is a figure item and touches no
equation). Every arm captures what the reader returned for each page (the picture and its
blocks) under `.local/image-pages/omission/pages/<set>`.

`simulate` takes those captures and drops blocks from the reader's answer (the last one, two or
three, and a block or two from the middle), then asks whether the check flags the page: the
check's sensitivity, as a function of how much ink the dropped blocks held. The omissions are
made up; the pages and the boxes are the reader's own.

The sets are those whose answers were recorded for docs/eval-image-pages.md and
docs/eval-handwriting.md (their answers files and their media are not in the repository; the
paths below are where they are kept). The equation step's recorded answers belong to an older
equation prompt for the typed scans, so for them most equation requests are refused: their
primary score is not comparable with the one in the docs, only between the two arms, and the
omission numbers do not depend on it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / ".local" / "image-pages" / "omission"
HW = Path.home() / ".cache" / "readablestem" / "handwriting" / "gold"
PAPERS = Path.home() / ".cache" / "readablestem" / "realworld" / "gold" / "heldout"
SETS = ("hw-dev", "hw-heldout", "scans-dev", "scans-heldout", "real")
REAL_PDFS = REPO / ".local" / "image-pages" / "real-pdfs"  # the 8 real pages, as scans
REAL_ANSWERS = REPO / ".local" / "image-pages" / "answers-real.jsonl"
ANSWERS = {
    "hw-dev": REPO / ".local" / "handwriting" / "dev" / "answers.jsonl",
    "hw-heldout": REPO / ".local" / "handwriting" / "heldout" / "answers.jsonl",
    "scans-dev": REPO / ".local" / "image-pages" / "answers-dev.jsonl",
    "scans-heldout": REPO / ".local" / "image-pages" / "heldout" / "opus" / "answers.jsonl",
    "real": REAL_ANSWERS,
}


def environment(arm: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        CLAUDE_SECOND_CHECK="off",
        FEATURE_IMAGE_PAGES="true",
        IMAGE_PAGES_MODEL="claude-opus-5-5",
        IMAGE_PAGES_OMISSION_CHECK="true" if arm == "on" else "false",
    )
    env.pop("IMAGE_PAGES_DPI", None)
    env.pop("ANTHROPIC_EFFORT_IMAGE_PAGES", None)
    return env


def capture_into(directory: Path) -> None:
    """Record every page the pass builds (its picture and the reader's blocks)."""
    from readablestem.pipeline import image_page

    directory.mkdir(parents=True, exist_ok=True)
    original = image_page.build

    def spy(page, output, *, box, png, check_omission=True):
        key = hashlib.sha256(png).hexdigest()[:12] if png else f"p{page}"
        if png:
            (directory / f"{key}.png").write_bytes(png)
        record = {"page": page, "box": list(box), "output": output.model_dump(mode="json")}
        (directory / f"{key}.json").write_text(json.dumps(record), encoding="utf-8")
        return original(page, output, box=box, png=png, check_omission=check_omission)

    image_page.build = spy


def run_eval(name: str, arm: str) -> dict:
    """One set, one arm, in this process (the environment is set by the caller)."""
    from readablestem.cli import eval_cli
    from readablestem.cli.main import build_parser

    arm_dir = OUT / f"{name}-{arm}"
    arm_dir.mkdir(parents=True, exist_ok=True)
    report, items = arm_dir / "report.json", arm_dir / "items.jsonl"
    common = [
        "--answers", str(ANSWERS[name]), "--answers-offline", "--out", str(report),
        "--items", str(items), "--max-usd", "1", "--cache", str(arm_dir / "cache"),
    ]  # fmt: skip
    kwargs: dict = {}
    if name.startswith("hw-"):
        from readablestem.eval.realworld_media import RealWorldMedia

        gold = HW / name.removeprefix("hw-")
        argv = ["eval", "pipeline", "--task", "document", "--provider", "anthropic", *common]
        kwargs = {"media_cls": RealWorldMedia}
    elif name == "scans-heldout":
        sys.path.insert(0, str(REPO / "scripts"))
        import realworld_scanned as rs  # type: ignore[import-not-found]

        gold = PAPERS
        argv = ["eval", "pipeline", "--task", "document", "--provider", "anthropic", *common]
        kwargs = {"media_cls": rs.ScannedRealWorld}
    else:  # scans-dev: handled by the CLI itself (`--set dev-v2 --part dev --scanned`)
        from readablestem.cli.main import main

        cache = str(REPO / ".cache" / "eval")
        argv = [
            "eval", "pipeline", "--set", "dev-v2", "--part", "dev", "--task", "document",
            "--scanned", "--provider", "anthropic", *common[:-2], "--cache", cache,
        ]  # fmt: skip
        code = main(argv)
        return {"exit": code, "report": str(report)}
    args = build_parser().parse_args(argv)
    code = eval_cli._run_pipeline(
        args, gold, report, gold_label=f"{name} ({arm})", cache_dir=arm_dir / "cache",
        openstax_cache=arm_dir / "cache", **kwargs,
    )  # fmt: skip
    return {"exit": code, "report": str(report)}


def run_real(arm: str) -> dict:
    """The eight real pages (no answer key): counts only."""
    from readablestem.config import Settings, with_provider
    from readablestem.core.types import ConversionOptions
    from readablestem.eval.answers import CachedClient
    from readablestem.pipeline import run

    settings = with_provider(
        Settings(feature_image_pages=True, image_pages_model="claude-opus-5-5"), "anthropic"
    )
    client = CachedClient(None, REAL_ANSWERS, settings)
    pages = required = optional = omitted = flagged = 0
    for pdf in sorted(REAL_PDFS.glob("*.pdf")):
        options = ConversionOptions.from_settings(settings)
        out = run.convert_pdf(pdf.read_bytes(), settings=settings, client=client, options=options)
        summary = out.result.image_pages
        if summary is None:
            continue
        for record in summary.per_page:
            if record.outcome != "converted":
                continue
            pages += 1
            required += record.required
            optional += record.optional
            omitted += record.omitted
            flagged += bool(record.omitted)
    return {
        "pages": pages,
        "required": required,
        "optional": optional,
        "omitted_regions": omitted,
        "omitted_pages": flagged,
    }


def numbers(name: str, arm: str, info: dict) -> dict:
    if name == "real":
        return info
    report = json.loads(Path(info["report"]).read_text(encoding="utf-8"))
    metrics = report["metrics"]
    pages = metrics["image_pages"]
    return {
        "units": f"{report['n']} of {report['n_planned']}",
        "pages": pages["converted"],
        "omitted_pages": pages.get("omitted_pages", 0),
        "omitted_regions": pages.get("omitted", 0),
        "required_per_page": pages["required_per_converted_page"],
        "optional_per_page": pages["optional_per_converted_page"],
        "primary": metrics["end_to_end_exact_lenient"],
        "silent_wrong_symbols": pages["equation_flags"]["all_symbols"]["wrong_not_asked"],
        "silent_wrong_strict": pages["equation_flags"]["all"]["wrong_not_asked"],
        "gold": metrics["gold_equations"],
    }


def one(name: str, arm: str) -> int:
    """`--one`: a single (set, arm), writes its numbers to OUT/<set>-<arm>.numbers.json."""
    OUT.mkdir(parents=True, exist_ok=True)
    if arm == "on":
        capture_into(OUT / "pages" / name)
    info = run_real(arm) if name == "real" else run_eval(name, arm)
    (OUT / f"{name}-{arm}.numbers.json").write_text(
        json.dumps(numbers(name, arm, info), indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


def replay(names: Sequence[str]) -> int:
    for name in names:
        for arm in ("off", "on"):
            print(f"-- {name} {arm}", flush=True)
            proc = subprocess.run(
                [sys.executable, str(Path(__file__)), "one", "--set", name, "--arm", arm],
                env=environment(arm), cwd=REPO, capture_output=True, text=True, check=False,
            )  # fmt: skip
            if proc.returncode:
                print(proc.stdout[-2000:], proc.stderr[-2000:])
                return proc.returncode
    header = (
        "set",
        "arm",
        "pages",
        "flagged",
        "regions",
        "req/page",
        "primary",
        "silent sym/strict",
    )
    print("{:14} {:4} {:>5} {:>7} {:>7} {:>8} {:>8} {:>17}".format(*header))
    for name in names:
        for arm in ("off", "on"):
            n = json.loads((OUT / f"{name}-{arm}.numbers.json").read_text(encoding="utf-8"))
            req = n.get("required_per_page")
            if req is None:
                req = n["required"] / max(1, n["pages"])
            silent = f"{n.get('silent_wrong_symbols', '-')}/{n.get('silent_wrong_strict', '-')}"
            primary = n.get("primary")
            primary_text = "-" if primary is None else f"{primary:.3f}"
            print(
                f"{name:14} {arm:4} {n['pages']:5} {n['omitted_pages']:7} "
                f"{n['omitted_regions']:7} {req:8.3f} {primary_text:>8} {silent:>17}"
            )
    return 0


# ------------------------------------------------------------------------- the simulation


def simulate(names: Sequence[str]) -> int:
    from readablestem.pipeline import image_page
    from readablestem.pipeline import image_page_omission as om

    bins = [(0.0, 0.03), (0.03, 0.06), (0.06, 0.12), (0.12, 1.01)]
    for name in names:
        rows: list[tuple[str, float, bool]] = []
        for meta in sorted((OUT / "pages" / name).glob("*.json")):
            png = meta.with_suffix(".png")
            if not png.is_file():
                continue
            blocks = json.loads(meta.read_text(encoding="utf-8"))["output"]["blocks"]
            mask = image_page.Ink(png.read_bytes()).mask
            boxes = [
                tuple(min(1000, max(0, b[k])) for k in ("x0", "y0", "x1", "y1")) for b in blocks
            ]
            if len(boxes) < 4:
                continue
            total = max(1, int(mask.sum()))
            height, width = mask.shape

            def held(
                i: int, mask=mask, boxes=boxes, total=total, height=height, width=width
            ) -> int:
                x0, y0, x1, y1 = boxes[i]
                return int(mask[y0 * height // 1000 : y1 * height // 1000,
                                x0 * width // 1000 : x1 * width // 1000].sum())  # fmt: skip

            cases: dict[str, list[int]] = {}
            for k in (1, 2, 3):
                cases[f"last {k}"] = list(range(len(boxes) - k, len(boxes)))
            for i in range(1, len(boxes) - 1):
                cases[f"middle {i}"] = [i]
            for i in range(1, len(boxes) - 2):
                cases[f"middle {i}+{i + 1}"] = [i, i + 1]
            for label, dropped in cases.items():
                kept = [b for j, b in enumerate(boxes) if j not in dropped]
                share = sum(held(j) for j in dropped) / total
                rows.append(
                    (label.split()[0] + str(len(dropped)), share, bool(om.find(mask, kept)))
                )
        print(f"\n{name}: {len(rows)} made-up omissions")
        print(f"{'ink in the dropped blocks (share of the page)':50} {'flagged':>10}")
        for lo, hi in bins:
            hit = [r for r in rows if lo <= r[1] < hi]
            if hit:
                got = sum(r[2] for r in hit)
                label = f"{lo:.0%} to {min(hi, 1):.0%}"
                print(f"{label:50} {got:4}/{len(hit):<4} {got / len(hit):5.2f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("replay", "simulate"):
        p = sub.add_parser(command)
        p.add_argument("--sets", nargs="+", choices=SETS, default=list(SETS))
    p = sub.add_parser("one")
    p.add_argument("--set", dest="one_set", choices=SETS, required=True)
    p.add_argument("--arm", choices=("off", "on"), required=True)
    args = parser.parse_args()
    random.seed(0)
    if args.command == "one":
        return one(args.one_set, args.arm)
    if args.command == "replay":
        return replay(args.sets)
    return simulate(args.sets)


if __name__ == "__main__":
    sys.exit(main())
