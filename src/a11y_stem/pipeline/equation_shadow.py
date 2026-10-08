"""Shadow mode of the second automated check on equations (ADR 0005, docs/steps.md).

For each transcribed equation, the job's verifier (`models.verifier`, the Decisions API)
is shown the original crop and the proposed LaTeX as text (no renderer) and asked one
fixed-order choice: "match", "cosmetic" or "meaning_changing". P(meaning_changing) is
recorded on the fix (`Fix.verifications`) for the final proposal and, when the equation
was escalated, for the cheap model's answer before escalation, and the aggregates count
it. That is all it does: it never changes a status, a confidence, a review tier, a check
or the escalation decision, because it runs after the fix is complete.

Why only a shadow: on research data (2026-10-08) the choice question caught almost every
synthetic symbol error (signs, digits, exponents, Greek letters) that the text-layer
checks miss, but its threshold moved out of sample and across fonts (12-23% false flags
on correct equations), and no real non-bold error has been seen yet. It becomes a gate
only once real errors are found and false flags on real crops are at most 5%.
"""

from __future__ import annotations

import dataclasses

from a11y_stem.config import Settings
from a11y_stem.core.types import Fix, Verification
from a11y_stem.models.verifier import MEANING_CHANGING, VerifierVerdict, verifier_of


def record(
    fix: Fix, png: bytes, *, client: object, settings: Settings, cheap_latex: str | None
) -> Fix:
    """`fix` with the shadow answers appended (cheap answer first, then the final one).

    `cheap_latex` is the cheap model's valid LaTeX when the equation was escalated away
    from it, else None. Answers are deterministic, so when it equals the final proposal
    the final answer is reused for it instead of asking (and paying) twice.
    """
    verifier = verifier_of(client)
    if verifier is None or not settings.decisions_equation_shadow:
        return fix
    final_latex = fix.equation.latex if fix.equation is not None else None
    final = verifier.equation_match(png, latex=final_latex) if final_latex else None
    records: list[Verification] = []
    if cheap_latex:
        if final is not None and cheap_latex == final_latex:
            cheap = dataclasses.replace(final, input_tokens=0, cost_usd=0.0, latency_ms=0)
        else:
            cheap = verifier.equation_match(png, latex=cheap_latex)
        records.append(_record(cheap, "cheap", settings))
    if final is not None:
        records.append(_record(final, "final", settings))
    if not records:
        return fix
    return fix.model_copy(update={"verifications": [*fix.verifications, *records]})


def _record(verdict: VerifierVerdict, stage: str, settings: Settings) -> Verification:
    p = verdict.probabilities.get(MEANING_CHANGING) if verdict.answered else None
    flagged = None if p is None else p >= settings.decisions_equation_flag_at
    return verdict.record(stage=stage, mode="shadow", flagged=flagged)
