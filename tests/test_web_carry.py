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
from readablestem.core.types import (
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
from readablestem.web import carry
from readablestem.web.db import Job, session_scope

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


def test_an_accept_is_not_carried_when_the_tool_now_proposes_something_else():
    before_e, before_f = equation("p1-e0", latex="x")
    decide(before_f, Status.ACCEPTED)
    new_e, new_f = equation("p1-e0", latex="y")
    old, new = result_of((before_e, before_f)), result_of((new_e, new_f))
    report, changes = carry.carry_decisions(old, {"p1-e0": png(1)}, new, {"p1-e0": png(1)})
    assert changes == [] and report.suggestion_changed == ["p1-e0"]
    assert new_f.pending


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
