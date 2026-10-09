"""Live check of the second automated check against the real Decisions API (opt-in).

Run with: uv run --env-file .env pytest -m live_model tests/test_decisions_live.py -s

Four requests (about $0.0002) through a JobBudget capped at $0.01. Inputs are the canary's
synthetic images (a bar chart and an equation drawn with PyMuPDF), never documents.
"""

from __future__ import annotations

import base64
import json

import pytest

from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.models.decisions_client import DecisionsClient, reservation_usd
from a11y_stem.models.verifier import (
    MEANING_CHANGING,
    DecisionsVerifier,
    equation_request,
    figure_request,
    figure_score,
)
from a11y_stem.ops.canary import REQUESTS_FILE
from test_steps_support import make_settings

pytestmark = pytest.mark.live_model


def test_the_decisions_verifier_ranks_right_above_wrong_within_its_reservation():
    settings = make_settings()  # reads OPENAI_API_KEY from the environment
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value():
        pytest.skip("OPENAI_API_KEY is not set")
    images = {
        k: base64.b64decode(v) for k, v in json.loads(REQUESTS_FILE.read_text())["images"].items()
    }
    # No model calls allowed: Decisions requests hold dollars only.
    budget = JobBudget(InMemorySpendGuard(cap_usd=0.01), max_usd=0.01, max_calls=0)
    verifier = DecisionsVerifier(DecisionsClient(settings, budget))
    caption = "Figure 1. Results of a class vote."
    right = verifier.figure_alt(
        images["bars"], alt="Bar chart of votes: A has 2, B has 5 and C has 3.", caption=caption
    )
    wrong = verifier.figure_alt(
        images["bars"], alt="Bar chart of votes: A has 2, B has 9 and C has 3.", caption=caption
    )
    match = verifier.equation_match(images["e_mc2"], latex="E = mc^2")
    mismatch = verifier.equation_match(images["e_mc2"], latex="E = mc^3")
    for verdict in (right, wrong, match, mismatch):
        assert verdict.reason_code == "ok", verdict
        assert all(0.0 <= p <= 1.0 for p in verdict.probabilities.values())
        assert verdict.model == "gpt-6-luna" and verdict.input_tokens > 0
    print(
        "figure right/wrong:",
        dict(right.probabilities),
        dict(wrong.probabilities),
        "equation match/mismatch:",
        dict(match.probabilities),
        dict(mismatch.probabilities),
    )
    assert figure_score(right.probabilities) > figure_score(wrong.probabilities)  # type: ignore[operator]
    assert mismatch.probabilities[MEANING_CHANGING] > match.probabilities[MEANING_CHANGING]
    # Every request stayed inside the reservation made for it, and the budget saw them all.
    alt = "Bar chart of votes: A has 2, B has 5 and C has 3."
    reserved = reservation_usd(
        "gpt-6-luna", figure_request(images["bars"], alt=alt, caption=caption)
    )
    assert right.cost_usd <= reserved
    reserved_eq = reservation_usd("gpt-6-luna", equation_request(images["e_mc2"], latex="E = mc^2"))
    assert match.cost_usd <= reserved_eq
    total = right.cost_usd + wrong.cost_usd + match.cost_usd + mismatch.cost_usd
    # Held in dollars only: none of the four is one of the job's model calls.
    assert budget.calls == 0 and budget.spent_usd == pytest.approx(total)
