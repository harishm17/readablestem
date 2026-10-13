"""Helpers for the proof tests: a tiny project on disk with a proof set, a gold set, real
(authored) PDFs standing in for the printed sections, and a deterministic fake model.

Nothing here touches the network, a browser or a key. The PDFs are the repository's own
layout fixtures; the "OpenStax" records are the shared synthetic ones of
`test_eval_support`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from a11y_stem.config import Settings
from a11y_stem.core.budget import JobBudget
from a11y_stem.models import OFFLINE_DEFAULTS
from a11y_stem.models.fake import FakeVisionClient, image_key
from a11y_stem.models.verifier import FakeVerifier, any_check_enabled
from test_eval_support import (
    EQ_A,
    EQ_F,
    EQ_V,
    EQ_X,
    FakeMedia,
    equation,
    fake_png,
    figure,
    heading,
    write_gold,
)

LAYOUT = Path(__file__).parent / "fixtures" / "layout"
PDF_OF_PAGE = {"p-1": LAYOUT / "worked_example.pdf", "p-2": LAYOUT / "lecture_notes.pdf"}
SET_ID = "synthetic"


class PdfMedia(FakeMedia):
    """FakeMedia whose section PDFs are real PDFs (the layout fixtures)."""

    def page_pdf(self, page_records: list[dict]) -> bytes:
        slug = page_records[0]["page_slug"]
        self.calls.append(("page", slug))
        return PDF_OF_PAGE[slug].read_bytes()


def gold_records() -> list[dict]:
    return [
        equation("p-1", 0, EQ_V, "block"),
        equation("p-1", 1, EQ_X, "inline"),
        heading("p-1", 0, 2, "Worked example"),
        figure("p-1", 0, "A block on a ramp.", "Figure 1 A cart."),
        equation("p-2", 0, EQ_A, "block"),
        equation("p-2", 1, EQ_F, "block"),
        heading("p-2", 0, 2, "Lecture notes"),
    ]


def fake_client(settings: Settings, budget: JobBudget) -> FakeVisionClient:
    """Answers that differ by picture (so a replay that mixed them up would show), plus a
    judge and the second check, all charging `budget`."""
    answers = {
        EQ_V: {"is_equation": True, "latex": r"v = \frac{d}{t}", "confidence": 0.95},
        EQ_X: {"is_equation": True, "latex": "x = 3", "confidence": 0.6},
        EQ_F: {"is_equation": True, "latex": "F = m a", "confidence": 0.7},
    }
    by_image = {("equation", image_key(fake_png(m))): a for m, a in answers.items()}
    defaults = {
        **OFFLINE_DEFAULTS,
        "judge": {
            "conveys_key_information": True,
            "factual_error": False,
            "score": 4,
            "confidence": 0.8,
        },
    }
    verifier = (
        FakeVerifier(budget=budget, model=settings.decisions_model)
        if any_check_enabled(settings)
        else None
    )
    return FakeVisionClient(by_image=by_image, defaults=defaults, budget=budget, verifier=verifier)


def make_project(
    root: Path,
    *,
    held_out: bool = False,
    repeats: dict[str, int] | None = None,
    prereg: bytes | None = None,
) -> Path:
    """A project root with `proof/sets/synthetic.json` and its gold set under `data/`."""
    gold = write_gold(root / "data", gold_records())
    manifest_sha = hashlib.sha256((gold / "manifest.json").read_bytes()).hexdigest()
    data = {
        "schema_version": 1,
        "id": SET_ID,
        "title": "Synthetic set for the tests",
        "held_out": held_out,
        "description": "Two made-up sections.",
        "gold": {
            "dir": "data/gold",
            "manifest_sha256": manifest_sha,
            "fetch": {
                "date": "2026-10-06",
                "allow_retired": False,
                "sections": [
                    {"book": "physics", "page": "p-1"},
                    {"book": "physics", "page": "p-2"},
                ],
            },
        },
        "repeats": repeats or {"document": 2, "equations": 1, "headings": 1, "figures": 1},
        "tasks": ["document", "equations", "headings", "figures"],
        "concurrency": 2,
        "bundle": "proof/bundles/synthetic",
        "limits": ["Made-up inputs: the numbers mean nothing."],
        "signoff": ["the decisions are made"] if held_out else [],
    }
    if held_out:
        data["preregistration"] = {
            "file": "proof/PREREGISTRATION.md",
            "sha256": hashlib.sha256(prereg or b"").hexdigest(),
        }
        if prereg is not None:
            (root / "proof").mkdir(parents=True, exist_ok=True)
            (root / "proof" / "PREREGISTRATION.md").write_bytes(prereg)
    sets = root / "proof" / "sets"
    sets.mkdir(parents=True, exist_ok=True)
    (sets / f"{SET_ID}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (sets / "index.json").write_text(json.dumps({"default": SET_ID}), encoding="utf-8")
    return root
