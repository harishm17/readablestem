"""T3-2: decisions carry over when a revised PDF is uploaded.

The rules of `web/carry.py` on small constructed results (an item is the same item only
when its picture is, a changed item never inherits a decision, an accepted proposal is
carried only if the tool proposes the same again), then the whole path: decide, upload a
revised version, convert, and see the card and the carried decisions.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from a11y_stem.core.types import (
    ConversionOutput,
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    Status,
)
from a11y_stem.web import carry
from a11y_stem.web.db import Job, ReviewEdit, session_scope
from fixtures.web.support import (
    ALICE,
    BOB,
    PNG,
    WebEnv,
    csrf_from,
    fake_output,
    job_id_from,
    login,
    make_pdf,
    token,
    upload,
)

# --------------------------------------------------------------------------- the rules


def png(n: int) -> bytes:
    """A different picture for each n (a PNG with something after its end: decoders skip it)."""
    return PNG + bytes([n])


def equation(eid: str, page: int = 0, y: float = 0.0, latex: str = "x") -> tuple[Element, Fix]:
    element = Element(
        id=eid, kind=Kind.EQUATION, page=page, bbox=(0, y, 100, y + 20), order=int(y) + page * 1000,
        display="block",
    )  # fmt: skip
    fix = Fix(
        element_id=eid,
        kind=Kind.EQUATION,
        status=Status.NEEDS_REVIEW,
        confidence=0.5,
        equation=EquationFix(
            latex=latex, mathml="<math><mi>x</mi></math>", speech="x", display="block"
        ),
    )
    return element, fix


def figure(eid: str, page: int = 0, y: float = 0.0, alt: str = "a line") -> tuple[Element, Fix]:
    element = Element(
        id=eid, kind=Kind.FIGURE, page=page, bbox=(0, y, 100, y + 50), order=int(y) + page * 1000,
        caption="Figure 1",
    )  # fmt: skip
    fix = Fix(
        element_id=eid,
        kind=Kind.FIGURE,
        status=Status.NEEDS_REVIEW,
        confidence=0.5,
        figure=FigureFix(figure_type="chart", alt=alt),
    )
    return element, fix


def result_of(*pairs: tuple[Element, Fix]) -> DocumentResult:
    return DocumentResult(
        pipeline_version="test",
        elements=[e for e, _ in pairs],
        fixes=[f for _, f in pairs],
        stats=DocumentStats(pages=2, cost_usd=0, latency_ms=0, model_calls=0),
    )


def decide(fix: Fix, status: Status, **changes) -> Fix:
    """The fix as a reviewer left it."""
    fix.status = status
    fix.reviewed = True
    for name, value in changes.items():
        setattr(fix, name, value)
    return fix


def test_a_same_picture_same_proposal_accept_is_carried():
    before_e, before_f = equation("p1-e0")
    decide(before_f, Status.ACCEPTED)
    new_e, new_f = equation("p1-e0")
    old, new = result_of((before_e, before_f)), result_of((new_e, new_f))
    report, changes = carry.carry_decisions(old, {"p1-e0": png(1)}, new, {"p1-e0": png(1)})
    assert report.carried == ["p1-e0"] and [c.element_id for c in changes] == ["p1-e0"]
    assert new_f.status is Status.ACCEPTED and new_f.reviewed and not new_f.pending
    assert changes[0].before["status"] == "needs_review" and changes[0].after["reviewed"] is True


def test_a_changed_picture_never_inherits_a_decision():
    before_e, before_f = equation("p1-e0")
    decide(before_f, Status.ACCEPTED)
    new_e, new_f = equation("p1-e0")
    old, new = result_of((before_e, before_f)), result_of((new_e, new_f))
    report, changes = carry.carry_decisions(old, {"p1-e0": png(1)}, new, {"p1-e0": png(2)})
    assert changes == [] and report.carried == []
    assert new_f.status is Status.NEEDS_REVIEW and not new_f.reviewed
    assert report.changed == ["p1-e0"]  # the earlier decided item stood in that place
    assert report.removed == 0


def test_an_accept_is_not_carried_when_the_tool_now_proposes_something_else():
    before_e, before_f = equation("p1-e0", latex="x")
    decide(before_f, Status.ACCEPTED)
    new_e, new_f = equation("p1-e0", latex="y")
    old, new = result_of((before_e, before_f)), result_of((new_e, new_f))
    report, changes = carry.carry_decisions(old, {"p1-e0": png(1)}, new, {"p1-e0": png(1)})
    assert changes == [] and report.suggestion_changed == ["p1-e0"]
    assert new_f.pending


def test_an_edit_belongs_to_the_picture_and_is_carried_over_a_different_proposal():
    before_e, before_f = equation("p1-e0", latex="x")
    decide(
        before_f,
        Status.EDITED,
        equation=EquationFix(latex="z", mathml="<math><mi>z</mi></math>", speech="z"),
    )
    new_e, new_f = equation("p1-e0", latex="y")
    old, new = result_of((before_e, before_f)), result_of((new_e, new_f))
    report, _ = carry.carry_decisions(old, {"p1-e0": png(1)}, new, {"p1-e0": png(1)})
    assert report.carried == ["p1-e0"]
    assert new_f.status is Status.EDITED and new_f.equation is not None
    assert new_f.equation.latex == "z"


def test_a_rejection_keeps_the_original_in_the_new_version_too():
    before_e, before_f = figure("p1-e0")
    decide(before_f, Status.REJECTED)
    new_e, new_f = figure("p1-e0", alt="something else")
    old, new = result_of((before_e, before_f)), result_of((new_e, new_f))
    report, _ = carry.carry_decisions(old, {"p1-e0": png(1)}, new, {"p1-e0": png(1)})
    assert report.carried == ["p1-e0"] and new_f.status is Status.REJECTED


def test_a_figure_whose_caption_changed_is_a_changed_item():
    before_e, before_f = figure("p1-e0")
    decide(before_f, Status.ACCEPTED)
    new_e, new_f = figure("p1-e0")
    new_e.caption = "Figure 2"
    old, new = result_of((before_e, before_f)), result_of((new_e, new_f))
    report, changes = carry.carry_decisions(old, {"p1-e0": png(1)}, new, {"p1-e0": png(1)})
    assert changes == [] and report.changed == ["p1-e0"]


def test_an_item_nobody_decided_is_not_decided_by_the_carry():
    before_e, before_f = equation("p1-e0")  # still waiting in the earlier version
    new_e, new_f = equation("p1-e0")
    old, new = result_of((before_e, before_f)), result_of((new_e, new_f))
    report, changes = carry.carry_decisions(old, {"p1-e0": png(1)}, new, {"p1-e0": png(1)})
    assert changes == [] and report.carried == [] and new_f.pending
    assert report.new == [] and report.changed == []  # waiting before, waiting now


def test_the_pipelines_own_acceptance_is_not_a_decision():
    before_e, before_f = equation("p1-e0")
    before_f.status = Status.ACCEPTED  # accepted automatically, reviewed stays False
    new_e, new_f = equation("p1-e0")
    old, new = result_of((before_e, before_f)), result_of((new_e, new_f))
    _, changes = carry.carry_decisions(old, {"p1-e0": png(1)}, new, {"p1-e0": png(1)})
    assert changes == [] and new_f.pending


def test_identical_pictures_are_matched_by_position_one_to_one():
    """The same small figure on three pages: each keeps its own decision."""
    pairs_old = [figure(f"p{n}-e0", page=n - 1, alt=f"alt {n}") for n in (1, 2, 3)]
    decide(pairs_old[0][1], Status.REJECTED)
    decide(pairs_old[2][1], Status.EDITED, figure=FigureFix(figure_type="chart", alt="mine"))
    pairs_new = [figure(f"p{n}-e0", page=n - 1, alt=f"alt {n}") for n in (1, 2, 3)]
    # The new version has them on the same pages, listed in another order.
    old = result_of(*pairs_old)
    new = result_of(*reversed(pairs_new))
    same = {f"p{n}-e0": png(7) for n in (1, 2, 3)}
    report, _ = carry.carry_decisions(old, same, new, same)
    by_id = {f.element_id: f for f in new.fixes}
    assert by_id["p1-e0"].status is Status.REJECTED
    assert by_id["p2-e0"].pending  # page 2 was never decided
    assert by_id["p3-e0"].status is Status.EDITED
    assert by_id["p3-e0"].figure is not None and by_id["p3-e0"].figure.alt == "mine"
    assert sorted(report.carried) == ["p1-e0", "p3-e0"]


def test_an_earlier_decision_with_no_counterpart_is_counted_as_removed():
    before_e, before_f = equation("p1-e0")
    decide(before_f, Status.ACCEPTED)
    new_e, new_f = equation("p2-e0", page=1, y=400.0)  # elsewhere, and a different picture
    old, new = result_of((before_e, before_f)), result_of((new_e, new_f))
    report, _ = carry.carry_decisions(old, {"p1-e0": png(1)}, new, {"p2-e0": png(2)})
    assert report.removed == 1 and report.new == ["p2-e0"] and report.changed == []


def heading(eid: str, text: str, order: int, level: int = 2) -> tuple[Element, Fix]:
    element = Element(
        id=eid, kind=Kind.HEADING, page=0, bbox=(0, order * 30, 100, order * 30 + 20),
        order=order, text=text, font_size=14, bold=True,
    )  # fmt: skip
    fix = Fix(
        element_id=eid,
        kind=Kind.HEADING,
        status=Status.NEEDS_REVIEW,
        heading=HeadingFix(level=level, text=text),
    )
    return element, fix


def test_headings_match_by_their_words():
    old_pairs = [heading("p1-e0", "Intro", 0, 1), heading("p1-e1", "Worked  Example", 1, 2)]
    decide(old_pairs[1][1], Status.EDITED, heading=HeadingFix(level=2, text="Worked  Example"))
    new_pairs = [heading("p1-e0", "Intro", 0, 1), heading("p1-e1", "worked example", 1, 2)]
    old, new = result_of(*old_pairs), result_of(*new_pairs)
    report, _ = carry.carry_decisions(old, {}, new, {})
    assert report.carried == ["p1-e1"]  # spacing and case are not a change


def test_a_heading_level_the_outline_would_now_change_is_not_carried():
    """A level two steps below the heading before it is written at another level."""
    deep_old = [heading("p1-e0", "Intro", 0, 1), heading("p1-e1", "Deep", 1, 4)]
    decide(deep_old[1][1], Status.EDITED, heading=HeadingFix(level=4, text="Deep"))
    deep_new = [heading("p1-e0", "Intro", 0, 1), heading("p1-e1", "Deep", 1, 4)]
    report, changes = carry.carry_decisions(result_of(*deep_old), {}, result_of(*deep_new), {})
    assert changes == [] and report.suggestion_changed == ["p1-e1"]


def test_the_report_round_trips_and_holds_no_text():
    report = carry.CarryReport(carried=["p1-e0"], changed=["p1-e1"], new=["p2-e0"], removed=2)
    again = carry.CarryReport.from_json(report.to_json())
    assert again == report
    assert carry.CarryReport.from_json("junk") == carry.CarryReport()
    odd = carry.CarryReport.from_json({"carried": [1, "p1-e0"], "removed": -3})
    assert odd == carry.CarryReport(carried=["p1-e0"])


# --------------------------------------------------------------------------- the whole path


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path, feature_notation_memory=True)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.alice = client
        env.bob = env.client()
        login(env.bob, BOB)
        yield env
        env.bob.close()


def first_output() -> ConversionOutput:
    """The small fake document with a different picture for each item, so each is its own
    item: p1-e3 equation and p1-e4 figure wait for decisions, p1-e5 was not converted."""
    output = fake_output()
    output.crops = {"p1-e3": png(3), "p1-e4": png(4), "p1-e5": png(5)}
    return output


def revised_output() -> ConversionOutput:
    """The same document revised: the equation p1-e3 is unchanged, the figure p1-e4's
    picture changed, and the third item p1-e5 is unchanged and still not converted."""
    output = first_output()
    output.crops = {"p1-e3": png(3), "p1-e4": png(40), "p1-e5": png(5)}
    return output


def decide_in_browser(client, job_id: str, element_id: str, action: str) -> None:
    page = f"/jobs/{job_id}/review"
    response = client.post(
        f"{page}/{element_id}",
        data={"csrf_token": token(client, page), "view": "list", "action": action},
    )
    assert response.status_code in {200, 303}, response.text[:300]


def stored(env, job_id: str) -> Job:
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        session.expunge(job)
        return job


def revise(env, client, first: str, name: str = "notes-v2.pdf") -> str:
    response = client.post(
        "/jobs",
        data={"csrf_token": token(client), "revises": first},
        files={"pdf": (name, make_pdf(1), "application/pdf")},
    )
    return job_id_from(response)


def test_a_revised_upload_carries_the_decisions_and_says_what_changed(web):
    env = web
    env.converter.output = first_output()
    first = job_id_from(upload(env.alice, make_pdf(1)))
    env.run_worker()
    decide_in_browser(env.alice, first, "p1-e3", "accept")
    decide_in_browser(env.alice, first, "p1-e4", "accept")
    decide_in_browser(env.alice, first, "p1-e5", "reject")

    env.converter.output = revised_output()
    second = revise(env, env.alice, first)
    assert stored(env, second).revises_job_id == first
    env.run_worker()

    job = stored(env, second)
    report = carry.CarryReport.from_json(job.carried_json)
    assert report.carried == ["p1-e3", "p1-e5"]  # the same pictures
    assert report.changed == ["p1-e4"]  # the figure's picture changed: decide again
    assert (job.carried_json or {})["from_name"] == "notes.pdf"
    result = DocumentResult.model_validate(job.result_json)
    states = {f.element_id: (f.status, f.reviewed) for f in result.fixes}
    assert states["p1-e3"] == (Status.ACCEPTED, True)
    assert states["p1-e5"] == (Status.REJECTED, True)
    assert states["p1-e4"][0] is Status.NEEDS_REVIEW  # never inherited
    with session_scope(env.ctx.factory) as session:
        edits = list(session.scalars(select(ReviewEdit).where(ReviewEdit.job_id == second)))
    assert sorted(e.element_id for e in edits) == ["p1-e3", "p1-e5"]
    assert {e.action for e in edits} == {carry.CARRIED_ACTION}

    page = env.alice.get(f"/jobs/{second}")
    assert page.status_code == 200
    text = re.sub(r"\s+", " ", page.text)
    assert "Compared with the earlier version" in text
    assert "notes.pdf" in text
    assert "2 decisions were carried over" in text
    assert "1 item changed since the earlier version" in text
    assert f"/jobs/{second}/review?item=p1-e4" in text
    # The file students get was made again with the carried decisions.
    assert stored(env, second).html


def test_the_review_screen_says_which_decisions_were_carried(web):
    env = web
    env.converter.output = first_output()
    first = job_id_from(upload(env.alice, make_pdf(1)))
    env.run_worker()
    decide_in_browser(env.alice, first, "p1-e3", "accept")
    env.converter.output = revised_output()
    second = revise(env, env.alice, first, "v2.pdf")
    env.run_worker()
    page = env.alice.get(f"/jobs/{second}/review?item=p1-e3")
    assert page.status_code == 200
    assert "carried over from the earlier version" in page.text


def test_the_earlier_version_being_gone_is_said_and_nothing_is_carried(web):
    env = web
    env.converter.output = first_output()
    first = job_id_from(upload(env.alice, make_pdf(1)))
    env.run_worker()
    second = revise(env, env.alice, first, "v2.pdf")
    # Deleted before the revised one is converted.
    env.alice.post(
        f"/jobs/{first}/delete", data={"csrf_token": token(env.alice, "/app"), "confirm": "yes"}
    )
    env.run_worker()
    job = stored(env, second)
    assert job.status == "done" and job.carried_json is None
    text = re.sub(r"\s+", " ", env.alice.get(f"/jobs/{second}").text)
    assert "that document was gone before this one finished" in text


def test_someone_elses_document_cannot_be_named_as_the_earlier_version(web):
    env = web
    env.converter.output = first_output()
    first = job_id_from(upload(env.alice, make_pdf(1)))
    env.run_worker()
    response = env.bob.post(
        "/jobs",
        data={"csrf_token": token(env.bob), "revises": first},
        files={"pdf": ("v2.pdf", make_pdf(1), "application/pdf")},
    )
    assert response.status_code == 400
    assert "was not found" in response.text
    # A made-up id is refused the same way, and nothing is queued.
    response = env.alice.post(
        "/jobs",
        data={"csrf_token": token(env.alice), "revises": "0" * 32},
        files={"pdf": ("v2.pdf", make_pdf(1), "application/pdf")},
    )
    assert response.status_code == 400
    with session_scope(env.ctx.factory) as session:
        assert [j.id for j in session.scalars(select(Job))] == [first]


def test_the_upload_form_for_a_revision_names_the_document_and_keeps_its_choices(web):
    env = web
    made = env.alice.post(
        "/courses", data={"csrf_token": csrf_from(env.alice.get("/courses").text), "name": "P1"}
    )
    course_id = made.headers["location"].rsplit("/", 1)[-1]
    env.converter.output = first_output()
    first = job_id_from(
        env.alice.post(
            "/jobs",
            data={"csrf_token": token(env.alice), "course": course_id, "exam": "on"},
            files={"pdf": ("notes.pdf", make_pdf(1), "application/pdf")},
        )
    )
    env.run_worker()
    page = env.alice.get(f"/app?revises={first}")
    assert 'name="revises"' in page.text and "Upload a revised version" in page.text
    assert re.search(rf'<option value="{course_id}" selected', page.text)
    assert re.search(r'id="exam"[^>]*checked', page.text)
    # Another person's id, or nonsense, gives the ordinary form.
    for who, ident in ((env.bob, first), (env.alice, "nonsense")):
        other = who.get(f"/app?revises={ident}")
        assert 'name="revises"' not in other.text and "Convert a PDF" in other.text
    # The job page offers it.
    assert f"/app?revises={first}" in env.alice.get(f"/jobs/{first}").text
