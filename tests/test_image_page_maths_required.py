"""Image pages: a block that holds maths is always a required decision (ADR 0007, point 12).

The page reader's `high` is not trustworthy on handwriting (docs/eval-handwriting.md: the two silent
wrong equations of the fresh held-out set were `high` blocks), and the option's promise is "you
check every item", so on a page read from an image every block with maths in it is required
whatever the model's confidence. Prose-only blocks keep their tiers; typed pages are never read
by this pass.
"""

from __future__ import annotations

from typing import Any

from image_page_support import block, cell, math, scan_pdf, text
from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import Kind, Status
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import image_page, run
from readablestem.pipeline.image_page import ImagePageOutput

BOX = (0.0, 0.0, 612.0, 792.0)
CODES = {"maths_checked"}


def build(*blocks: dict[str, Any], **kw: Any) -> image_page.Built:
    out = ImagePageOutput.model_validate({"blocks": list(blocks)})
    kw.setdefault("maths_required", True)
    built = image_page.build(0, out, box=BOX, png=None, **kw)
    assert built is not None
    return built


def fix_of(built: image_page.Built, kind: Kind):
    [element] = [e for e in built.elements if e.kind is kind and e.display != "inline"]
    return built.fixes[element.id]


def reasons(fix) -> set[str]:
    return {c.code for c in fix.failed_checks if c.code in CODES}


# ----------------------------------------------------------------------------- headings


def heading_fixes(*blocks: dict[str, Any], maths_required: bool = True):
    built = build(*blocks)
    [heading] = [e for e in built.elements if e.kind is Kind.HEADING]
    from readablestem.core.types import Fix, HeadingFix

    fix = Fix(
        element_id=heading.id,
        kind=Kind.HEADING,
        status=Status.ACCEPTED,
        confidence=0.95,
        heading=HeadingFix(level=1, text=heading.text or ""),
        review="optional",
    )
    image_page.tier_headings([fix], built.elements, maths_required=maths_required)
    return fix


# ----------------------------------------------------------------------- through the run


def convert(pdf: bytes, payload: dict[str, Any], **config: Any):
    s = Settings(  # type: ignore[call-arg]
        _env_file=None, feature_image_pages=True, image_pages_recover_omissions=False, **config
    )
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=5.0), max_usd=5.0, max_calls=100)
    fake = FakeVisionClient(
        responders={"image_page": lambda r: payload, "equation": lambda r: None}, budget=budget
    )
    return run.convert_pdf(pdf, settings=s, client=fake).result


PAGE = {
    "blocks": [
        block("heading", level=1, runs=[text("Motion")], y0=40, y1=80),
        block(
            "paragraph", runs=[text("The speed "), math("v"), text(" is small.")], y0=100, y1=160
        ),
        block("paragraph", runs=[text("Plain words.")], y0=200, y1=260),
    ]
}


# ------------------------------------------------- no maths read from an image goes unasked

EVERY_PATH = {
    "blocks": [
        block(
            "heading", level=1, runs=[text("The "), math("\\sigma"), text(" model")], y0=20, y1=60
        ),
        block(
            "paragraph", runs=[text("Since "), math("x^2 = 4"), text(" it follows.")], y0=80, y1=120
        ),
        block("list_item", marker="1.", runs=[text("Solve "), math("y = 2x")], y0=140, y1=180),
        block("equation", latex="v = \\frac{d}{t}", label="(1)", y0=200, y1=260),
        block("paragraph", runs=[math("E = mc^2")], y0=280, y1=320),  # a lone formula: display
        block(
            "table",
            rows=[
                {"cells": [cell("t", "col"), cell("f", "col")]},
                {"cells": [cell("1"), cell("x", latex="\\sqrt{2}")]},
            ],
            y0=340,
            y1=440,
        ),
        block("paragraph", confidence="low", runs=[text("Unsure "), math("k")], y0=460, y1=500),
    ]
}


def asked_about(result) -> list[str]:
    fixes = {f.element_id: f for f in result.fixes}
    return image_page.maths_unasked(result.elements, fixes)


def convert_every_path(**config: Any):
    s = Settings(  # type: ignore[call-arg]
        _env_file=None, feature_image_pages=True, image_pages_recover_omissions=False, **config
    )
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=5.0), max_usd=5.0, max_calls=100)
    fake = FakeVisionClient(
        responders={
            "image_page": lambda r: EVERY_PATH,
            # a confident crop reading that agrees with the page reader: still a decision
            "equation": lambda r: {
                "is_equation": True,
                "latex": "v = \\frac{d}{t}",
                "confidence": 1.0,
            },
        },
        budget=budget,
    )
    return run.convert_pdf(scan_pdf(), settings=s, client=fake).result


def test_no_handwritten_maths_item_can_be_accepted_without_a_person():
    """Whole-page read (headings, paragraphs, list items), display crops, lone formulas and
    table cells: every equation and table on the page is governed by a required decision."""
    result = convert_every_path()
    maths = [
        e
        for e in result.elements
        if e.kind in (Kind.EQUATION, Kind.TABLE) and image_page.IMAGE_PAGE_REASON in e.reasons
    ]
    assert len(maths) >= 6  # inline in three blocks, two displays, one table
    assert asked_about(result) == []
    # and nothing waiting is "accepted": a person's act is what settles each of them
    for e in maths:
        fix = result.fix_for(e.parent_id or e.id)
        assert fix is not None and fix.review == "required" and fix.pending


def test_the_same_with_every_second_reading_switch_on():
    result = convert_every_path(image_pages_second_reading=True, image_pages_accept_agreed=True)
    assert asked_about(result) == []


def test_a_path_that_forgot_the_rule_is_caught_after_the_steps(monkeypatch):
    """The guard in `run`: a paragraph fix that comes out accepted and optional (a new tier,
    a cached answer) is lifted to a required decision before the result is written."""
    real = image_page._prose_fix

    def lax(*args: Any, **kwargs: Any):
        fix = real(*args, **kwargs)
        return fix.model_copy(
            update={"status": Status.ACCEPTED, "review": "optional", "checks": []}
        )

    monkeypatch.setattr(image_page, "_prose_fix", lax)
    result = convert_every_path()
    assert asked_about(result) == []
    [paragraph] = [e for e in result.elements if e.text and e.text.startswith("Since")]
    fix = result.fix_for(paragraph.id)
    assert (fix.status, fix.review) == (Status.NEEDS_REVIEW, "required")
    assert "maths_checked" in {c.code for c in fix.failed_checks}
