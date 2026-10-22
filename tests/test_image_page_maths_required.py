"""Image pages: a block that holds maths is always a required decision (ADR 0007, point 12).

The page reader's `high` is not trustworthy on handwriting (docs/eval-handwriting.md: the two silent
wrong equations of the fresh held-out set were `high` blocks), and the option's promise is "you
check every item", so on a page read from an image every block with maths in it is required
whatever the model's confidence. Prose-only blocks keep their tiers; typed pages are never read
by this pass.
"""

from __future__ import annotations

from typing import Any

from image_page_support import block, math, scan_pdf, text
from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import Kind, Status
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import image_page, run
from readablestem.pipeline import image_page_check as second
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


def test_a_sure_paragraph_with_a_formula_is_a_required_decision_that_says_why():
    built = build(block("paragraph", runs=[text("Since "), math("x^2 = 4"), text(" it follows.")]))
    fix = fix_of(built, Kind.PARAGRAPH)
    assert (fix.status, fix.review) == (Status.NEEDS_REVIEW, "required")
    assert reasons(fix) == {"maths_checked"}
    assert fix.pending and not fix.optional_note


def test_a_sure_list_item_with_a_formula_and_words_is_required():
    built = build(block("list_item", marker="1.", runs=[text("Solve "), math("x^2 = 4")]))
    fix = fix_of(built, Kind.LIST_ITEM)
    assert fix.review == "required" and reasons(fix) == {"maths_checked"}


def test_a_sure_paragraph_with_no_maths_stays_an_optional_note():
    built = build(block("paragraph", runs=[text("Speed is distance over time.")]))
    fix = fix_of(built, Kind.PARAGRAPH)
    assert (fix.status, fix.review) == (Status.ACCEPTED, "optional")
    assert reasons(fix) == set()


def test_a_formula_that_does_not_validate_is_required_as_before_with_no_second_reason():
    built = build(block("paragraph", runs=[text("See "), math("\\frac{a}{")]))
    fix = fix_of(built, Kind.PARAGRAPH)
    assert fix.review == "required" and reasons(fix) == set()  # it already had its reason


def test_an_unsure_block_with_maths_has_its_own_reason_only():
    built = build(block("paragraph", confidence="medium", runs=[text("a "), math("x")]))
    fix = fix_of(built, Kind.PARAGRAPH)
    assert fix.review == "required" and reasons(fix) == set()


def test_the_setting_off_gives_the_old_tiers():
    built = build(block("paragraph", runs=[text("Since "), math("x^2 = 4")]), maths_required=False)
    assert fix_of(built, Kind.PARAGRAPH).review == "optional"


def test_an_agreed_second_reading_cannot_lift_a_block_with_maths_out_of_the_decisions():
    agreed = second.Verdict(agree=True, confidence="high")
    built = build(
        block("paragraph", confidence="medium", runs=[text("Since "), math("x^2 = 4")]),
        verdicts={0: agreed},
        accept_agreed=True,
    )
    fix = fix_of(built, Kind.PARAGRAPH)
    assert fix.review == "required" and reasons(fix) == {"maths_checked"}


def test_a_display_equation_is_decided_by_the_equation_step_as_before():
    built = build(block("equation", latex="v = \\frac{d}{t}"))
    [equation] = [e for e in built.elements if e.kind is Kind.EQUATION]
    assert equation.id not in built.fixes  # no fix of this module's: the crop is read, required


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


def test_a_sure_heading_with_maths_is_required_and_a_plain_one_is_not():
    with_maths = heading_fixes(
        block("heading", level=1, runs=[text("The "), math("\\sigma"), text(" model")])
    )
    plain = heading_fixes(block("heading", level=1, runs=[text("The model")]))
    assert (with_maths.review, with_maths.status) == ("required", Status.NEEDS_REVIEW)
    assert "maths_checked" in {c.code for c in with_maths.failed_checks}
    assert plain.review == "optional"


def test_a_heading_with_maths_stays_optional_when_the_setting_is_off():
    fix = heading_fixes(
        block("heading", level=1, runs=[text("The "), math("\\sigma")]), maths_required=False
    )
    assert fix.review == "optional"


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


def test_through_the_run_only_the_block_with_maths_becomes_a_decision():
    on = convert(scan_pdf(), PAGE)
    off = convert(scan_pdf(), PAGE, image_pages_maths_always_required=False)
    paragraphs = [e for e in on.elements if e.kind is Kind.PARAGRAPH]
    with_maths, plain = (on.fix_for(p.id).review for p in paragraphs)
    assert (with_maths, plain) == ("required", "optional")
    old = [off.fix_for(p.id).review for p in off.elements if p.kind is Kind.PARAGRAPH]
    assert old == ["optional", "optional"]
    assert on.stats.required_decisions == off.stats.required_decisions + 1


def test_a_typed_page_beside_a_scan_is_untouched_by_the_setting():
    pdf = scan_pdf(typed_first=True)
    on = convert(pdf, PAGE)
    off = convert(pdf, PAGE, image_pages_maths_always_required=False)
    typed = lambda r: [  # noqa: E731
        (e.kind, f.review, f.status)
        for e in r.elements
        if e.page == 0 and (f := r.fix_for(e.id)) is not None
    ]
    assert typed(on) == typed(off)
    assert not [
        c
        for f in on.fixes
        for c in f.checks
        if c.code == "maths_checked"
        and next(e for e in on.elements if e.id == f.element_id).page == 0
    ]
