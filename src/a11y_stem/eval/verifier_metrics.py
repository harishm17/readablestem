"""Eval metrics for the second automated check (ADR 0005, docs/eval.md).

- figures: the check's flag rate on the pipeline's accepted alt texts and its agreement
  with the gpt-6.1-sol judge (raw agreement, Cohen's kappa, the confusion table, and the
  AUROC of its score against the judge's verdict). The judge is a model too: agreement
  between two models is not validity, only Harish's labels are (`eval calibrate`).
- equations and document: shadow mode, so only how well P(meaning_changing) on the
  final proposal ranks wrong transcriptions above right ones (AUROC against "not
  exact_lenient", also without the scorer's known gaps), what the shadow threshold
  would have flagged, and the cheap-stage answers.

Per-item fields come from `Fix.verifications` (`*_fields`); the metric functions read
only those fields, so they work on the JSONL rows too.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

from a11y_stem.core.types import Fix, Kind, Verification
from a11y_stem.models.verifier import (
    EQUATION_CHECK,
    FIGURE_CHECK,
    P_FACTUAL_ERROR,
    P_OK,
    verification_score,
)


def auroc(scored: Iterable[tuple[float, bool]]) -> float | None:
    """P(a positive scores above a negative), ties counting half. `scored` holds
    (score, positive) pairs; None without both classes."""
    pairs = list(scored)
    pos = [s for s, p in pairs if p]
    neg = [s for s, p in pairs if not p]
    if not pos or not neg:
        return None
    wins = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in pos for b in neg)
    return round(wins / (len(pos) * len(neg)), 4)


def kappa(pairs: Sequence[tuple[bool, bool]]) -> float | None:
    """Cohen's kappa for two raters and two classes; None when it is undefined."""
    n = len(pairs)
    if not n:
        return None
    observed = sum(a == b for a, b in pairs) / n
    first = sum(a for a, _ in pairs) / n
    second = sum(b for _, b in pairs) / n
    expected = first * second + (1 - first) * (1 - second)
    if expected >= 1 - 1e-12:
        return None
    return round((observed - expected) / (1 - expected), 4)


def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 4) if b else None


def _usd_per(cost: float, n: int) -> float | None:
    """Dollars per unit, unrounded past 1e-8 (these are a few hundred-thousandths)."""
    return round(cost / n, 8) if n else None


def find(fix: Fix, check: str, stage: str = "final") -> Verification | None:
    return next((v for v in fix.verifications if v.check == check and v.stage == stage), None)


def requests_sent(fix: Fix) -> int:
    """Requests actually sent (a reused answer and a refused reservation cost nothing)."""
    return sum(1 for v in fix.verifications if v.cost_usd > 0 or v.latency_ms > 0)


def cost_fields(fix: Fix) -> dict[str, Any]:
    return {
        "verifier_cost_usd": fix.verifier_cost_usd,
        "verifier_latency_ms": sum(v.latency_ms for v in fix.verifications),
        "verifier_requests": requests_sent(fix),
        # The question wording's version ("<check>-<sha8>"), like a prompt version: two
        # runs with different versions asked different questions.
        "verifier_versions": sorted({v.version for v in fix.verifications if v.version}),
    }


def _versions(items: Sequence[dict]) -> list[str]:
    return sorted({v for i in items for v in i.get("verifier_versions", [])})


def figure_fields(fix: Fix) -> dict[str, Any]:
    """Per-item fields of the figure check (None when it did not run)."""
    record = find(fix, FIGURE_CHECK)
    return {
        "verifier_outcome": record.outcome if record else None,
        "verifier_p_ok": record.probabilities.get(P_OK) if record else None,
        "verifier_p_factual_error": record.probabilities.get(P_FACTUAL_ERROR) if record else None,
        "verifier_score": verification_score(record) if record else None,
        "verifier_flagged": record.flagged if record else None,
        **cost_fields(fix),
    }


def equation_fields(fix: Fix) -> dict[str, Any]:
    """Per-item fields of the equation shadow check (final and cheap stage)."""
    final, cheap = find(fix, EQUATION_CHECK), find(fix, EQUATION_CHECK, "cheap")
    return {
        "shadow_outcome": final.outcome if final else None,
        "shadow_p_meaning": verification_score(final) if final else None,
        "shadow_flagged": final.flagged if final else None,
        "shadow_cheap_outcome": cheap.outcome if cheap else None,
        "shadow_cheap_p_meaning": verification_score(cheap) if cheap else None,
        "shadow_cheap_flagged": cheap.flagged if cheap else None,
        **cost_fields(fix),
    }


# ------------------------------------------------------------------------- figures


def figure_metrics(items: Sequence[dict]) -> dict[str, Any]:
    """The figure check over the figures task's items.

    Agreement is over figures with both a check answer and a judge verdict: flag =
    "possible mismatch", judge fail = not (conveys key information and no factual
    error). The AUROC ranks the check's score (low = mismatch) against judge fails.
    """
    ran = [i for i in items if i.get("verifier_outcome") is not None]
    answered = [i for i in ran if i.get("verifier_score") is not None]
    flagged = [i for i in answered if i.get("verifier_flagged")]
    both = [i for i in answered if i.get("judge_pass") is not None]
    pairs = [(bool(i["verifier_flagged"]), not i["judge_pass"]) for i in both]
    confusion = {
        "flag_and_judge_fail": sum(f and j for f, j in pairs),
        "flag_and_judge_pass": sum(f and not j for f, j in pairs),
        "no_flag_and_judge_fail": sum(not f and j for f, j in pairs),
        "no_flag_and_judge_pass": sum(not f and not j for f, j in pairs),
    }
    cost = sum(i.get("verifier_cost_usd", 0.0) for i in items)
    return {
        "mode": "note",
        "versions": _versions(items),
        "ran": len(ran),
        "answered": len(answered),
        "outcomes": dict(sorted(Counter(str(i["verifier_outcome"]) for i in ran).items())),
        "flagged": len(flagged),
        "flag_rate": _ratio(len(flagged), len(answered)),
        "flagged_ids": [i.get("id") for i in flagged],
        "scores": {str(i.get("id")): i.get("verifier_score") for i in answered},
        "agreement_with_judge": {
            "n": len(pairs),
            "raw_agreement": _ratio(sum(f == j for f, j in pairs), len(pairs)),
            "kappa": kappa(pairs),
            "confusion": confusion,
            "auroc_score_vs_judge_fail": auroc(
                (1.0 - float(i["verifier_score"]), not i["judge_pass"]) for i in both
            ),
        },
        "cost_usd": round(cost, 8),
        "per_figure_usd": _usd_per(cost, len(items)),
        "requests": sum(i.get("verifier_requests", 0) for i in items),
    }


# ------------------------------------------------------------------------ equations


def _shadow(rows: Sequence[dict], *, score_key: str, flag_key: str) -> dict[str, Any]:
    """AUROC of P(meaning_changing) against wrong (not exact_lenient), and what the
    shadow threshold would flag, over rows with an answer."""
    answered = [r for r in rows if r.get(score_key) is not None]
    wrong = [r for r in answered if not r["exact_lenient"]]
    flagged = [r for r in answered if r.get(flag_key)]
    hits = [r for r in flagged if not r["exact_lenient"]]
    clean = [r for r in answered if not r.get("scorer_gap")]
    return {
        "answered": len(answered),
        "wrong": len(wrong),
        "auroc_vs_wrong": auroc((float(r[score_key]), not r["exact_lenient"]) for r in answered),
        "auroc_vs_wrong_excluding_scorer_gaps": auroc(
            (float(r[score_key]), not r["exact_lenient"]) for r in clean
        ),
        "would_flag": len(flagged),
        "would_flag_precision": _ratio(len(hits), len(flagged)),
        "would_flag_recall": _ratio(len(hits), len(wrong)),
        "would_flag_wrong_excluding_scorer_gaps": sum(1 for r in hits if not r.get("scorer_gap")),
    }


def equation_metrics(items: Sequence[dict]) -> dict[str, Any]:
    """Shadow answers over the equations task's items (rows of `equation_item`)."""
    ran = [i for i in items if i.get("shadow_outcome") is not None]
    cheap = [i for i in items if i.get("shadow_cheap_outcome") is not None]
    cheap_answered = [
        float(i["shadow_cheap_p_meaning"])
        for i in cheap
        if i.get("shadow_cheap_p_meaning") is not None
    ]
    cost = sum(i.get("verifier_cost_usd", 0.0) for i in items)
    return {
        "mode": "shadow",
        "versions": _versions(items),
        "ran": len(ran),
        "outcomes": dict(sorted(Counter(str(i["shadow_outcome"]) for i in ran).items())),
        "final": _shadow(items, score_key="shadow_p_meaning", flag_key="shadow_flagged"),
        "cheap_stage": {
            "ran": len(cheap),
            "answered": sum(1 for i in cheap if i.get("shadow_cheap_p_meaning") is not None),
            "would_flag": sum(1 for i in cheap if i.get("shadow_cheap_flagged")),
            "mean_p_meaning": _ratio(sum(cheap_answered), len(cheap_answered)),
        },
        "p_meaning": {str(i.get("id")): i.get("shadow_p_meaning") for i in ran},
        "cost_usd": round(cost, 8),
        "per_equation_usd": _usd_per(cost, len(items)),
        "requests": sum(i.get("verifier_requests", 0) for i in items),
    }


# ------------------------------------------------------------------------- document


def page_summary(fixes: Iterable[Fix]) -> dict[str, Any]:
    """Per-page counts of the second check in the document task (all fixes, matched
    to gold or not)."""
    fixes = list(fixes)
    figures = [find(f, FIGURE_CHECK) for f in fixes if f.kind is Kind.FIGURE]
    equations = [find(f, EQUATION_CHECK) for f in fixes if f.kind is Kind.EQUATION]
    return {
        "figure_checked": sum(1 for v in figures if v is not None and v.outcome == "ok"),
        "figure_flagged": sum(1 for v in figures if v is not None and v.flagged),
        "equation_checked": sum(1 for v in equations if v is not None and v.outcome == "ok"),
        "equation_would_flag": sum(1 for v in equations if v is not None and v.flagged),
        "outcomes": dict(
            Counter(v.outcome for f in fixes for v in f.verifications if v.stage == "final")
        ),
    }


def document_metrics(items: Sequence[dict]) -> dict[str, Any]:
    """Shadow AUROC over the equations matched to gold, plus the page counts."""
    pairs = [p for i in items for p in i.get("pairs", [])]
    pages = [i.get("verifier", {}) for i in items]
    outcomes: Counter[str] = Counter()
    for page in pages:
        outcomes.update(page.get("outcomes", {}))
    cost = sum(i.get("verifier_cost_usd", 0.0) for i in items)
    pdf_pages = sum(i.get("pdf_pages", 0) for i in items)
    return {
        "equations_matched": _shadow(
            pairs, score_key="shadow_p_meaning", flag_key="shadow_flagged"
        ),
        "figure_checked": sum(p.get("figure_checked", 0) for p in pages),
        "figure_flagged": sum(p.get("figure_flagged", 0) for p in pages),
        "equation_checked": sum(p.get("equation_checked", 0) for p in pages),
        "equation_would_flag": sum(p.get("equation_would_flag", 0) for p in pages),
        "outcomes": dict(sorted(outcomes.items())),
        "cost_usd": round(cost, 8),
        "per_pdf_page_usd": _usd_per(cost, pdf_pages),
    }
