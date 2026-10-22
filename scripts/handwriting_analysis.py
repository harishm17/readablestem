"""Error analysis of a handwriting run (docs/eval-handwriting.md, "Root causes and fixes").

    uv run python scripts/handwriting_analysis.py rows --part dev --arm control
    uv run python scripts/handwriting_analysis.py classes --part heldout --arm control
    uv run python scripts/handwriting_analysis.py calibration --part heldout --arm control
    uv run python scripts/handwriting_analysis.py second --part dev --arm final

Everything is computed from a run's recordings (`handwriting_accuracy.py run`, offline) and costs
nothing. `rows` joins each gold equation with what the pipeline read (the page reader's block and
confidence, the second reading's verdict) and writes `rows.json` beside the arm's report; the
other commands read it. `data/handwriting/error-classes.json` holds the by-eye class of every wrong
equation of the first measurement (ids and classes only; it carries no label and no ink).
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "handwriting_accuracy", REPO / "scripts" / "handwriting_accuracy.py"
)
assert _spec is not None and _spec.loader is not None
hwa = importlib.util.module_from_spec(_spec)
sys.modules["handwriting_accuracy"] = hwa
_spec.loader.exec_module(hwa)
hwe = hwa.hwe
CLASSES = REPO / "data" / "handwriting" / "error-classes.json"
CLASS_NAMES = {
    "AMB": "ambiguous ink, or a label the ink does not show",
    "CONV": "a notation the label did not use (the reading is faithful)",
    "MIS": "a misread of clear ink (dropped mark, symbol, structure)",
    "LAY": "right symbols, wrong only because a display was set inline",
    "OMIT": "the equation was left out of the reading",
}


def arm_dir(arm: str, part: str) -> Path:
    return hwa.arm_root(arm) / part


def cmd_rows(args: argparse.Namespace) -> int:
    os.environ.update(
        MODEL_PROVIDER="anthropic",
        CLAUDE_SECOND_CHECK="off",
        FEATURE_IMAGE_PAGES="true",
        IMAGE_PAGES_MODEL="claude-opus-5-5",
    )
    for item in args.env or []:
        key, _, value = item.partition("=")
        os.environ[key] = value
    from readablestem.config import Settings
    from readablestem.core.types import Kind
    from readablestem.eval.answers import answer_key, load_rows
    from readablestem.pipeline import image_page, layout

    if args.prompt:  # the prompt an arm was recorded with, when it is not the packaged one
        import hashlib

        from readablestem.models import prompts

        text = Path(args.prompt).read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        fixed = prompts.Prompt(name="image_page", version=f"image_page-{digest}", text=text)
        image_page.load_prompt = lambda name: fixed
        image_page.PROMPT_VERSION = fixed.version
    settings = Settings()
    answers = {r["k"]: r for r in load_rows(hwa.shared_answers(args.part)) if r.get("t") == "model"}
    items = [
        json.loads(line)
        for line in (arm_dir(args.arm, args.part) / "items.jsonl").read_text().splitlines()
        if line.strip()
    ]
    gold: dict[str, dict] = {}
    for path in sorted((hwe.CACHE / "gold" / args.part).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                gold[record["id"]] = record
    out: list[dict] = []
    for item in items:
        unit = item["id"].split("/")[-1]
        pdf = (hwe.CACHE / "gold" / args.part / "pdfs" / f"{unit}.pdf").read_bytes()
        built = {}
        for page in range(item["pdf_pages"]):
            png = layout.render_page(
                pdf, page, dpi=settings.image_pages_dpi, max_side_px=image_page.MAX_SIDE_PX
            )
            row = answers.get(answer_key(settings, image_page.build_request(settings, png)))
            if row is None or row["payload"] is None:
                continue
            output = image_page.ImagePageOutput.model_validate(row["payload"])
            verdicts = {}
            if args.second:
                from readablestem.eval.answers import CachedClient

                client = CachedClient(None, hwa.shared_answers(args.part), settings)
                result = image_page.PassResult(
                    reads={page: image_page.PageRead(page, output, [], "ok", png)}
                )
                image_page.second_readings(client, settings, result, concurrency=1)
                verdicts = result.reads[page].verdicts
            extra = {"verdicts": verdicts} if args.second else {}
            built[page] = image_page.build(
                page, output, box=(0.0, 0.0, 612.0, 792.0), png=png, **extra
            )
        pairs = {p["gold_id"]: p for p in item["pairs"]}
        for record in gold.values():
            if record["page_slug"] != unit:
                continue
            pair = pairs.get(record["id"])
            flag = next(
                (f for f in item["image_pages"]["flags"] if f["gold_id"] == record["id"]), {}
            )
            r = {
                "unit": unit,
                "id": record["id"],
                "page": record["pdf_page"] - 1,
                "gold_display": record["display"],
                "gold": record["latex"],
                "found": pair is not None,
                "asked": bool(flag.get("asked")),
            }
            if pair:
                r.update(
                    pred=pair["pred_latex"],
                    pred_display=pair["pred_display"],
                    ok=bool(pair["exact_lenient"]),
                    ok_symbols=bool(pair["exact_symbols"]),
                    element=pair["element_id"],
                )
                page_built = built.get(r["page"])
                if page_built:
                    elements = {e.id: e for e in page_built.elements}
                    element = elements.get(pair["element_id"])
                    if element:
                        r["confidence"] = element.image_confidence
                        parent = elements.get(element.parent_id) if element.parent_id else None
                        r["block"] = parent.id if parent else element.id
                        r["block_kind"] = (parent or element).kind.value
                        fix = page_built.fixes.get(parent.id) if parent else None
                        if fix is not None:
                            r["second_reading"] = next(
                                (
                                    c.code
                                    for c in fix.checks
                                    if c.name == "image_page.second_reading"
                                ),
                                None,
                            )
                            r["block_required"] = fix.review == "required"
            out.append(r)
    target = arm_dir(args.arm, args.part) / "rows.json"
    target.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    del Kind
    print(f"{args.part}/{args.arm}: {len(out)} rows -> {target}")
    return 0


def load_rows(arm: str, part: str) -> list[dict]:
    return json.loads((arm_dir(arm, part) / "rows.json").read_text(encoding="utf-8"))


def cmd_classes(args: argparse.Namespace) -> int:
    classes = json.loads(CLASSES.read_text(encoding="utf-8"))
    rows = load_rows(args.arm, args.part)
    wrong = [r for r in rows if not (r["found"] and r["ok"])]
    counts = collections.Counter(classes.get(r["id"], "unclassified") for r in wrong)
    share = len(wrong) / len(rows)
    print(f"{args.part}: {len(rows)} gold equations, {len(wrong)} wrong ({share:.1%})")
    for key, name in CLASS_NAMES.items():
        n = counts.get(key, 0)
        print(
            f"  {key:5} {n:3} ({n / len(wrong):.0%} of wrong, {n / len(rows):.1%} of all)  {name}"
        )
    if counts.get("unclassified"):
        print(f"  unclassified {counts['unclassified']}")
    folded = _fold_flips(args.part, wrong)
    print(
        f"  folding notation on both sides (\\bar=\\overline, \\ell=l, ...) makes right: {folded}"
    )
    return 0


def fold(latex: str) -> str:
    """A notation-only normaliser, used to count how many wrong equations are wrong in notation
    alone; it never changes what is reported as the score."""
    s = re.sub(r"\\bar\b", r"\\overline", latex)
    s = s.replace("\\varepsilon", "\\epsilon")
    s = re.sub(r"\\ell\b", "l", s)
    s = re.sub(r"\\(ldots|cdots|dots)", "...", s).replace("\\cdot\\cdot\\cdot", "...")
    s = re.sub(r"\\mathbb\{(\w)\}", r"\1", s)
    s = re.sub(r"\\(?:mathrm|text)\{(\w+)\}", r"\1", s)
    s = s.replace("\\|", "||").replace("\\left", "").replace("\\right", "")
    s = s.replace("\\,", "").replace("\\ ", "")
    s = re.sub(r"\\(geq|ge)\b", r"\\ge", s)
    s = re.sub(r"\\(leq|le)\b", r"\\le", s)
    s = s.replace("^{\\prime}", "'").replace("^\\prime", "'")
    return re.sub(r"\\overline\{(\w)\}_(\w)", r"\\overline{\1_\2}", s)


def _fold_flips(part: str, wrong: list[dict]) -> int:
    os.environ.setdefault("MODEL_PROVIDER", "anthropic")
    from readablestem.eval.equations import score_mathml
    from readablestem.pipeline import equation_step

    flips = 0
    for r in wrong:
        if not r["found"]:
            continue
        mode = "block" if r["gold_display"] == "block" else "inline"
        gold = equation_step._convert(equation_step.clean_latex(fold(r["gold"])), mode)[0]
        pred = equation_step._convert(equation_step.clean_latex(fold(r["pred"])), mode)[0]
        if gold and pred and score_mathml(pred, gold).exact_lenient:
            flips += 1
    return flips


def _auc(points: list[tuple[int, bool]]) -> float:
    pos = [s for s, ok in points if ok]
    neg = [s for s, ok in points if not ok]
    if not pos or not neg:
        return float("nan")
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


def cmd_calibration(args: argparse.Namespace) -> int:
    rows = [r for r in load_rows(args.arm, args.part) if r.get("confidence")]
    scale = {"high": 2, "medium": 1, "low": 0}
    print(f"{args.part}/{args.arm}: share of formulas right, by the page reader's confidence")
    for kind in ("all", "equation", "text"):
        sel = [
            r
            for r in rows
            if kind == "all" or (r["block_kind"] == "equation") == (kind == "equation")
        ]
        by = collections.defaultdict(lambda: [0, 0])
        for r in sel:
            by[r["confidence"]][0] += 1
            by[r["confidence"]][1] += r["ok"]
        cells = ", ".join(
            f"{c} {v[1]}/{v[0]} ({v[1] / v[0]:.0%})"
            for c in ("high", "medium", "low")
            if (v := by[c])[0]
        )
        auc = _auc([(scale[r["confidence"]], r["ok"]) for r in sel])
        print(f"  {kind:8} {cells}; AUC {auc:.2f}")
    blocks: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        blocks[f"{r['unit']}/{r['page']}/{r['block']}"].append(r)
    by_block = collections.defaultdict(lambda: [0, 0])
    for group in blocks.values():
        c = group[0]["confidence"]
        by_block[c][0] += 1
        by_block[c][1] += all(x["ok"] for x in group)
    cells = ", ".join(
        f"{c} {v[1]}/{v[0]} ({v[1] / v[0]:.0%})"
        for c in ("high", "medium", "low")
        if (v := by_block[c])[0]
    )
    total = sum(v[0] for v in by_block.values())
    right = sum(v[1] for v in by_block.values())
    print(f"  blocks wholly right: {cells}; all {right}/{total} ({right / total:.0%})")
    return 0


def cmd_second(args: argparse.Namespace) -> int:
    rows = [r for r in load_rows(args.arm, args.part) if r.get("block")]
    blocks: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        blocks[f"{r['unit']}/{r['page']}/{r['block']}"].append(r)
    table = collections.Counter()
    for group in blocks.values():
        if group[0].get("block_kind") == "equation":
            continue
        table[(group[0].get("second_reading"), all(x["ok"] for x in group))] += 1
    print(f"{args.part}/{args.arm}: text blocks with formulas, by the second reading")
    for (code, right), n in sorted(table.items(), key=str):
        print(f"  {code or 'not asked':32} wholly right={right!s:5} {n}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("rows", "classes", "calibration", "second"):
        p = sub.add_parser(name)
        p.add_argument("--part", choices=hwa.PARTS, required=True)
        p.add_argument("--arm", required=True)
        if name == "rows":
            p.add_argument("--second", action="store_true", help="replay the second reading too")
            p.add_argument("--env", action="append")
            p.add_argument("--prompt", help="the image page prompt file the arm was recorded with")
    args = parser.parse_args()
    return {
        "rows": cmd_rows,
        "classes": cmd_classes,
        "calibration": cmd_calibration,
        "second": cmd_second,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
