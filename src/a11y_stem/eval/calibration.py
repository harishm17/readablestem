"""Calibrate the figure judge: agreement with Harish's labels, and bias probes.

An LLM judge is only worth publishing if it agrees with a careful human. This module
measures that in two ways.

1. Agreement (`eval calibrate --judge judged.jsonl --labels labels.jsonl`). Harish labels
   a sample of judged figures pass/fail by hand (template: `--write-template`). We report
   raw agreement, Cohen's kappa (agreement corrected for chance: two raters who both say
   "pass" 90% of the time agree 82% of the time by luck alone), a seeded bootstrap 95%
   confidence interval for kappa, and the confusion matrix.
2. Bias probes (`eval calibrate --probes`). The judge grades texts whose right answer we
   know without a human:
   - `reference`: the OpenStax alt text itself. Should pass.
   - `verbose`: the same content, padded and repeated (about twice as long). Should pass
     with the same score; a higher score is length bias.
   - `empty`: "". Should fail.
   - `irrelevant`: another figure's alt text. Should fail.

Label files are JSONL ({"id", "human_pass", "notes"}) or CSV with the same columns, so a
spreadsheet export works. human_pass accepts true/false, yes/no, pass/fail, 1/0, y/n;
blank means not labelled yet.
"""

from __future__ import annotations

import csv
import json
import random
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from a11y_stem.config import Settings
from a11y_stem.core.budget import BudgetExceeded
from a11y_stem.eval import judge as judge_mod
from a11y_stem.eval.pipeline_eval import mean, percentile, rate
from a11y_stem.models.base import VisionClient

Pair = tuple[bool, bool]  # (judge_pass, human_pass)
PROBES: dict[str, bool] = {"reference": True, "verbose": True, "empty": False, "irrelevant": False}
IRRELEVANT_FALLBACK = "A bowl of fruit on a wooden kitchen table."
TEMPLATE_FIELDS = [
    "id",
    "page_slug",
    "figure_number",
    "image_url",
    "caption",
    "reference_alt",
    "candidate_alt",
    "human_pass",
    "notes",
]


# ======================================================================= agreement


def confusion(pairs: Sequence[Pair]) -> dict[str, int]:
    return {
        "judge_pass_human_pass": sum(j and h for j, h in pairs),
        "judge_pass_human_fail": sum(j and not h for j, h in pairs),
        "judge_fail_human_pass": sum(not j and h for j, h in pairs),
        "judge_fail_human_fail": sum(not j and not h for j, h in pairs),
    }


def raw_agreement(pairs: Sequence[Pair]) -> float | None:
    return rate(j == h for j, h in pairs)


def cohen_kappa(pairs: Sequence[Pair]) -> float | None:
    """kappa = (p_o - p_e) / (1 - p_e) for two raters and two classes.

    p_o is observed agreement; p_e is the agreement expected if each rater kept their own
    pass rate but answered independently. None when p_e = 1 (both raters gave one and
    the same answer to everything), where kappa is undefined.
    """
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(j == h for j, h in pairs) / n
    judge_pass = sum(j for j, _ in pairs) / n
    human_pass = sum(h for _, h in pairs) / n
    expected = judge_pass * human_pass + (1 - judge_pass) * (1 - human_pass)
    if expected >= 1 - 1e-12:
        return None
    return (observed - expected) / (1 - expected)


def bootstrap_ci(
    pairs: Sequence[Pair],
    statistic: Callable[[Sequence[Pair]], float | None] = cohen_kappa,
    *,
    n_boot: int = 2000,
    seed: int = 0,
    level: float = 0.95,
) -> dict:
    """Percentile bootstrap: resample items with replacement, recompute, take quantiles.

    Seeded, so the same labels always give the same interval. Resamples where the
    statistic is undefined (e.g. every resampled item is "pass" on both sides) are
    skipped and counted in `skipped`.
    """
    if not pairs:
        return {"low": None, "high": None, "level": level, "n_boot": n_boot, "skipped": 0}
    rng = random.Random(seed)
    n = len(pairs)
    values = []
    skipped = 0
    for _ in range(n_boot):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        value = statistic(sample)
        if value is None:
            skipped += 1
        else:
            values.append(value)
    tail = (1 - level) / 2 * 100
    return {
        "low": percentile(values, tail),
        "high": percentile(values, 100 - tail),
        "level": level,
        "n_boot": n_boot,
        "skipped": skipped,
        "seed": seed,
    }


def parse_bool(value: Any) -> bool | None:
    """Spreadsheet-friendly booleans; None for blank (not labelled)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"true", "yes", "y", "pass", "1", "t"}:
        return True
    if text in {"false", "no", "n", "fail", "0", "f"}:
        return False
    raise ValueError(f"cannot read {value!r} as pass/fail")


def _read_rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    rows = []
    with path.open(encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: not valid JSON ({exc.msg})") from exc
    return rows


def load_labels(path: Path) -> dict[str, bool | None]:
    """id -> human_pass (None when the row is not labelled yet)."""
    labels: dict[str, bool | None] = {}
    for number, row in enumerate(_read_rows(path), 1):
        if not row.get("id"):
            raise ValueError(f"{path}: row {number} has no id")
        try:
            labels[row["id"]] = parse_bool(row.get("human_pass"))
        except ValueError as exc:
            raise ValueError(f"{path}: row {number}: {exc}") from None
    return labels


def load_judged(path: Path) -> dict[str, dict]:
    """Per-item rows from `eval pipeline --task figures --items judged.jsonl`, by id."""
    return {row["id"]: row for row in _read_rows(path) if row.get("id")}


def agreement_report(
    judged: dict[str, dict],
    labels: dict[str, bool | None],
    *,
    n_boot: int = 2000,
    seed: int = 0,
) -> dict:
    """Join judge verdicts with human labels by id and measure agreement.

    Only items the judge actually graded count (a figure the step abstained on has no
    judge verdict to calibrate). Unlabelled and unmatched ids are reported, not used.
    """
    pairs: list[Pair] = []
    ids = []
    for item_id, human in sorted(labels.items()):
        row = judged.get(item_id)
        if human is None or row is None or row.get("judge_pass") is None:
            continue
        pairs.append((bool(row["judge_pass"]), human))
        ids.append(item_id)
    return {
        "n": len(pairs),
        "labelled": sum(v is not None for v in labels.values()),
        "unlabelled": sum(v is None for v in labels.values()),
        "labels_without_judgement": sum(
            1
            for i, v in labels.items()
            if v is not None and (i not in judged or judged[i].get("judge_pass") is None)
        ),
        "raw_agreement": raw_agreement(pairs),
        "kappa": cohen_kappa(pairs),
        "kappa_ci95": bootstrap_ci(pairs, n_boot=n_boot, seed=seed),
        "confusion": confusion(pairs),
        "judge_pass_rate": rate(j for j, _ in pairs),
        "human_pass_rate": rate(h for _, h in pairs),
        "disagreements": [i for i, (j, h) in zip(ids, pairs, strict=True) if j != h],
    }


def write_label_template(judged: Iterable[dict], path: Path) -> int:
    """Rows for hand labelling: the texts to compare, blank human_pass and notes.

    The judge's verdict is left out on purpose so it cannot anchor the human label.
    CSV (UTF-8 with BOM, so Excel shows symbols right) when `path` ends in .csv,
    JSONL otherwise. Returns the number of rows written.
    """
    rows = [
        {
            "id": item["id"],
            "page_slug": item.get("page_slug"),
            "figure_number": item.get("figure_number"),
            "image_url": item.get("image_url"),
            "caption": item.get("caption"),
            "reference_alt": item.get("reference_alt"),
            "candidate_alt": item.get("candidate_alt"),
            "human_pass": "",
            "notes": "",
        }
        for item in judged
        if item.get("judge_outcome") == "judged"
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=TEMPLATE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    else:
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


# ========================================================================== probes


def verbose_paraphrase(reference: str) -> str:
    """Same content, about twice as long: padding plus a restatement, no new facts."""
    body = reference.strip()
    return (
        f"This figure is described as follows. {body} In summary, and to say it once "
        f"more for clarity, the figure shows this: {body}"
    )


def probe_candidates(
    figures: Sequence[dict], paraphrases: dict[str, str] | None = None
) -> list[dict]:
    """One row per (figure, probe): the candidate text and whether it should pass.

    `figures` are gold figure records with a non-empty reference alt. The irrelevant
    candidate is the reference of the figure half the list away (so rarely a neighbour
    on the same page), or a fixed unrelated sentence when there is only one figure.
    """
    references = [_reference(f) for f in figures]
    rows = []
    n = len(figures)
    for index, figure in enumerate(figures):
        reference = references[index]
        other = references[(index + max(1, n // 2)) % n] if n > 1 else IRRELEVANT_FALLBACK
        if other == reference:
            other = IRRELEVANT_FALLBACK
        texts = {
            "reference": reference,
            "verbose": (paraphrases or {}).get(figure["id"]) or verbose_paraphrase(reference),
            "empty": "",
            "irrelevant": other,
        }
        for probe, expected in PROBES.items():
            rows.append(
                {
                    "id": figure["id"],
                    "probe": probe,
                    "expected_pass": expected,
                    "candidate": texts[probe],
                    "reference": reference,
                    "caption": figure.get("caption"),
                }
            )
    return rows


def _reference(figure: dict) -> str:
    image = next((img for img in figure.get("images", []) if img.get("url")), {})
    return (image.get("alt") or "").strip()


def probe_figures(records: Iterable[dict], limit: int | None = None) -> list[dict]:
    """Gold figure records that have a reference alt (the probes need one)."""
    figures = [r for r in records if r.get("kind") == "figure" and _reference(r)]
    return figures[:limit] if limit is not None else figures


Judge = Callable[..., judge_mod.Judgement]


def run_probes(
    figures: Sequence[dict],
    *,
    client: VisionClient,
    settings: Settings,
    paraphrases: dict[str, str] | None = None,
    judge: Judge | None = None,
) -> tuple[dict, list[dict]]:
    """Judge every probe candidate. Stops cleanly when the budget is exhausted."""
    judge_fn = judge or judge_mod.judge_alt
    candidates = probe_candidates(figures, paraphrases)
    items: list[dict] = []
    stopped = None
    for row in candidates:
        try:
            judgement = judge_fn(
                row["candidate"], row["reference"], row["caption"], client=client, settings=settings
            )
        except BudgetExceeded as exc:
            stopped = f"budget_{exc.scope}"
            break
        verdict = judgement.verdict
        items.append(
            {
                "id": row["id"],
                "probe": row["probe"],
                "expected_pass": row["expected_pass"],
                "candidate_chars": len(row["candidate"]),
                "reference_chars": len(row["reference"]),
                "judge_outcome": judgement.call.outcome,
                "judge_pass": verdict.passed if verdict is not None else None,
                "judge_score": verdict.score if verdict is not None else None,
                "judge_cost_usd": judgement.call.cost_usd,
            }
        )
    return probe_report(items, n_planned=len(candidates), stopped=stopped), items


def probe_report(items: Sequence[dict], *, n_planned: int, stopped: str | None) -> dict:
    probes = {}
    for name, expected in PROBES.items():
        rows = [i for i in items if i["probe"] == name]
        judged = [i for i in rows if i["judge_pass"] is not None]
        pass_rate = rate(bool(i["judge_pass"]) for i in judged)
        probes[name] = {
            "expected_pass": expected,
            "n": len(rows),
            "judged": len(judged),
            "pass_rate": pass_rate,
            "as_expected_rate": (
                None if pass_rate is None else (pass_rate if expected else 1 - pass_rate)
            ),
            "mean_score": mean(i["judge_score"] for i in judged),
            "mean_chars": mean(i["candidate_chars"] for i in rows),
        }
    ref, verbose = probes["reference"], probes["verbose"]
    return {
        "n_planned": n_planned,
        "n": len(items),
        "complete": stopped is None and len(items) == n_planned,
        "stopped_reason": stopped,
        "probes": probes,
        "length_bias": {
            "mean_score_delta": _delta(verbose["mean_score"], ref["mean_score"]),
            "pass_rate_delta": _delta(verbose["pass_rate"], ref["pass_rate"]),
            "length_ratio": _ratio(verbose["mean_chars"], ref["mean_chars"]),
        },
        "cost_usd": sum(i["judge_cost_usd"] for i in items),
    }


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def _ratio(a: float | None, b: float | None) -> float | None:
    return None if a is None or not b else a / b


def load_paraphrases(path: Path) -> dict[str, str]:
    """Optional hand-written verbose paraphrases: rows {"id", "text"} (JSONL or CSV)."""
    return {row["id"]: row["text"] for row in _read_rows(path) if row.get("id") and row.get("text")}


# ========================================================================== output


def _f(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def format_agreement(report: dict) -> str:
    ci = report["kappa_ci95"]
    c = report["confusion"]
    return (
        "\n".join(
            [
                f"## Judge vs human labels (n = {report['n']})",
                "",
                "| metric | value |",
                "|---|---|",
                f"| raw agreement | {_f(report['raw_agreement'])} |",
                f"| Cohen's kappa | {_f(report['kappa'])} |",
                f"| kappa 95% CI (bootstrap, {ci['n_boot']} resamples, seed {ci.get('seed')}) "
                f"| {_f(ci['low'])} to {_f(ci['high'])} |",
                f"| judge pass rate | {_f(report['judge_pass_rate'])} |",
                f"| human pass rate | {_f(report['human_pass_rate'])} |",
                "",
                "| | human pass | human fail |",
                "|---|---|---|",
                f"| judge pass | {c['judge_pass_human_pass']} | {c['judge_pass_human_fail']} |",
                f"| judge fail | {c['judge_fail_human_pass']} | {c['judge_fail_human_fail']} |",
                "",
                f"Unlabelled rows: {report['unlabelled']}; labels with no judge verdict: "
                f"{report['labels_without_judgement']}.",
            ]
        )
        + "\n"
    )


def format_probes(report: dict) -> str:
    status = "complete" if report["complete"] else f"PARTIAL ({report['stopped_reason']})"
    lines = [
        f"## Judge bias probes ({report['n']} of {report['n_planned']} judgements, {status})",
        "",
        "| probe | should | pass rate | as expected | mean score | mean chars |",
        "|---|---|---|---|---|---|",
    ]
    for name, p in report["probes"].items():
        lines.append(
            f"| {name} | {'pass' if p['expected_pass'] else 'fail'} | {_f(p['pass_rate'])} "
            f"| {_f(p['as_expected_rate'])} | {_f(p['mean_score'], 2)} "
            f"| {_f(p['mean_chars'], 0)} |"
        )
    bias = report["length_bias"]
    lines += [
        "",
        f"Length bias: verbose minus reference mean score {_f(bias['mean_score_delta'], 2)}, "
        f"pass rate {_f(bias['pass_rate_delta'])} (verbose is {_f(bias['length_ratio'], 1)}x "
        f"longer). Cost ${report['cost_usd']:.4f}.",
    ]
    return "\n".join(lines) + "\n"
