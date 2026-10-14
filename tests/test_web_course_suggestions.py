"""Notation suggested from a course's documents (T3-12): the course page and the job page
list the symbols the documents use, the instructor ticks what to keep and what each means,
and nothing is added without that."""

from __future__ import annotations

import logging
import re
from html import unescape

import pytest
from sqlalchemy import select

from a11y_stem.core.notation import canonical_symbol
from a11y_stem.web.db import CourseNotation, session_scope
from fixtures.web.support import (
    ALICE,
    BOB,
    WebEnv,
    fake_output,
    job_id_from,
    login,
    make_pdf,
    token,
)

BOLD_D = canonical_symbol(r"\mathbf{d}")
REALS = canonical_symbol(r"\mathbb{R}")
VEC_V = canonical_symbol(r"\vec{v}")


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path, feature_notation_memory=True)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        yield env


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def post(client, path: str, page: str, **fields):
    data = {"csrf_token": token(client, page)} | fields
    return client.post(path, data=data)


def new_course(client, name: str = "Physics 1") -> str:
    response = post(client, "/courses", "/courses", name=name)
    return response.headers["location"].rsplit("/", 1)[-1]


def notation_output():
    """The fake document with notation in its equations: a bold d twice, a double-struck R
    and a vector arrow on v (the third equation needs review, so it counts too)."""
    output = fake_output()
    latex = {
        "p1-e2": r"\mathbf{d} = \mathbf{d}_0 + \vec{v} t",
        "p1-e3": r"x \in \mathbb{R}, \quad \mathbf{d}",
    }
    for fix in output.result.fixes:
        if fix.element_id in latex and fix.equation is not None:
            fix.equation.latex = latex[fix.element_id]
    return output


def converted(web: WebEnv, course_id: str | None) -> str:
    web.converter.output = notation_output()
    fields = {"csrf_token": token(web.browser)}
    if course_id is not None:
        fields["course"] = course_id
    files = {"pdf": ("notes.pdf", make_pdf(1), "application/pdf")}
    response = web.browser.post("/jobs", data=fields, files=files)
    job_id = job_id_from(response)
    assert web.run_worker() == job_id
    return job_id


def entries(env) -> list[CourseNotation]:
    with session_scope(env.ctx.factory) as session:
        return list(session.scalars(select(CourseNotation)))


def form_of(page: str) -> tuple[str, dict[str, list[str]]]:
    """The suggestion form's action and its fields: keys and their default meanings."""
    form = re.search(
        r'<form method="post" action="(/courses/[^"]+/suggestions)".*?</form>', page, re.S
    )
    assert form is not None, "no suggestion form"
    html = form.group(0)
    keys = [unescape(k) for k in re.findall(r'name="key" value="([^"]*)"', html)]
    meanings = []
    for select_html in re.findall(r"<select.*?</select>", html, re.S):
        chosen = re.search(r'<option value="(\w+)" selected', select_html)
        assert chosen is not None
        meanings.append(chosen.group(1))
    return form.group(1), {"key": keys, "meaning": meanings}


def submit(web: WebEnv, page_path: str, ticked: list[str], *, meaning=None, job: str | None = None):
    action, fields = form_of(web.browser.get(page_path).text)
    data: dict[str, object] = {
        "csrf_token": token(web.browser, page_path),
        "key": fields["key"],
        "meaning": [
            (meaning or {}).get(k, m) for k, m in zip(fields["key"], fields["meaning"], strict=True)
        ],
        "pick": [str(fields["key"].index(k)) for k in ticked],
    }
    if job:
        data["job"] = job
    return web.browser.post(action, data=data)


# --------------------------------------------------------------------------- the pages


def test_the_course_page_lists_the_symbols_its_documents_use(web):
    course_id = new_course(web.browser)
    before = web.browser.get(f"/courses/{course_id}").text
    assert "Symbols found in" not in before  # no document yet
    converted(web, course_id)
    page = web.browser.get(f"/courses/{course_id}").text
    text = text_of(page)
    assert "Symbols found in your documents" in text
    assert "bold d (2 equations, first on page 1)" in text
    assert "double-struck R (1 equation, first on page 1)" in text and "v with an arrow" in text
    _, fields = form_of(page)
    assert set(fields["key"]) == {BOLD_D, REALS, VEC_V}
    guesses = dict(zip(fields["key"], fields["meaning"], strict=True))
    assert guesses[BOLD_D] == "vector" and guesses[REALS] == "set" and guesses[VEC_V] == "vector"
    assert entries(web) == []  # looking adds nothing


def test_the_job_page_lists_the_symbols_of_that_document(web):
    course_id = new_course(web.browser)
    job_id = converted(web, course_id)
    page = web.browser.get(f"/jobs/{job_id}").text
    assert "Symbols found in this document" in text_of(page)
    action, fields = form_of(page)
    assert action == f"/courses/{course_id}/suggestions" and BOLD_D in fields["key"]
    assert 'name="job" value="' + job_id + '"' in page


def test_a_job_without_a_course_has_no_suggestions(web):
    job_id = converted(web, None)
    assert "Symbols found in" not in web.browser.get(f"/jobs/{job_id}").text


def test_with_the_flag_off_the_job_page_has_none(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.converter.output = notation_output()
        response = client.post(
            "/jobs",
            data={"csrf_token": token(client)},
            files={"pdf": ("notes.pdf", make_pdf(1), "application/pdf")},
        )
        job_id = job_id_from(response)
        env.run_worker()
        assert "Symbols found in" not in client.get(f"/jobs/{job_id}").text


# --------------------------------------------------------------------------- confirming


def test_ticked_symbols_are_added_with_the_meaning_chosen_and_the_rest_are_not(web):
    course_id = new_course(web.browser)
    converted(web, course_id)
    path = f"/courses/{course_id}"
    response = submit(web, path, [BOLD_D, REALS], meaning={REALS: "other"})
    assert response.status_code == 303 and response.headers["location"] == path
    added = {e.canonical: e for e in entries(web)}
    assert set(added) == {BOLD_D, REALS}
    assert added[BOLD_D].meaning == "vector" and added[REALS].meaning == "other"
    assert {e.source for e in added.values()} == {"document"}
    after = web.browser.get(path).text
    assert "Added to Physics 1:" in text_of(after) and "From a document" in text_of(after)
    _, fields = form_of(after)  # what is left to decide is still offered
    assert BOLD_D not in fields["key"] and VEC_V in fields["key"]


def test_nothing_ticked_adds_nothing(web):
    course_id = new_course(web.browser)
    converted(web, course_id)
    response = submit(web, f"/courses/{course_id}", [])
    assert response.status_code == 303 and entries(web) == []
    after = web.browser.get(response.headers["location"]).text
    assert "Tick the symbols to add first" in text_of(after)


def test_from_the_job_page_it_comes_back_to_the_job_page(web):
    course_id = new_course(web.browser)
    job_id = converted(web, course_id)
    response = submit(web, f"/jobs/{job_id}", [VEC_V], job=job_id)
    assert response.headers["location"] == f"/jobs/{job_id}#suggestions"
    assert [e.canonical for e in entries(web)] == [VEC_V]


def test_a_symbol_the_documents_do_not_use_is_not_taken(web):
    course_id = new_course(web.browser)
    converted(web, course_id)
    path = f"/courses/{course_id}"
    data = {
        "csrf_token": token(web.browser, path),
        "key": [canonical_symbol(r"\mathbf{z}"), "not a key"],
        "meaning": ["vector", "vector"],
        "pick": ["0", "1", "7", "x"],
    }
    response = web.browser.post(f"{path}/suggestions", data=data)
    assert response.status_code == 303 and entries(web) == []


def test_a_meaning_that_is_not_one_of_the_list_is_not_taken(web):
    course_id = new_course(web.browser)
    converted(web, course_id)
    response = submit(web, f"/courses/{course_id}", [BOLD_D], meaning={BOLD_D: "everything"})
    assert response.status_code == 303 and entries(web) == []


def test_only_the_owner_can_add_to_a_course_or_use_a_job(web):
    course_id = new_course(web.browser)
    job_id = converted(web, course_id)
    bob = web.client()
    login(bob, BOB)
    path = f"/courses/{course_id}/suggestions"
    fields = {"key": [BOLD_D], "meaning": ["vector"], "pick": ["0"]}
    assert post(bob, path, "/courses", **fields).status_code == 404
    other = new_course(web.browser, "Other")
    mismatch = post(web.browser, f"/courses/{other}/suggestions", "/courses", job=job_id, **fields)
    assert mismatch.status_code == 404  # the job belongs to another course
    assert entries(web) == []


def test_it_needs_the_csrf_token(web):
    course_id = new_course(web.browser)
    converted(web, course_id)
    response = web.browser.post(
        f"/courses/{course_id}/suggestions",
        data={"key": [BOLD_D], "meaning": ["vector"], "pick": ["0"]},
    )
    assert response.status_code in (400, 403) and entries(web) == []


def test_a_symbol_added_meanwhile_is_skipped_not_doubled(web):
    course_id = new_course(web.browser)
    converted(web, course_id)
    path = f"/courses/{course_id}"
    _, fields = form_of(web.browser.get(path).text)
    post(web.browser, f"{path}/entries", path, symbol="bold d", meaning="vector", note="")
    data = {
        "csrf_token": token(web.browser, path),
        "key": fields["key"],
        "meaning": fields["meaning"],
        "pick": [str(fields["key"].index(REALS))],
    }
    # The stale form also ticked bold d (now in the course): only the real one is new.
    data["pick"].append(str(fields["key"].index(BOLD_D)) if BOLD_D in fields["key"] else "99")
    web.browser.post(f"{path}/suggestions", data=data)
    canonicals = sorted(e.canonical for e in entries(web))
    assert canonicals == sorted([BOLD_D, REALS])


def test_a_new_entry_is_used_in_the_next_conversion(web):
    course_id = new_course(web.browser)
    converted(web, course_id)
    submit(web, f"/courses/{course_id}", [BOLD_D])
    converted(web, course_id)
    options = web.converter.calls[-1]["options"]
    assert options is not None
    assert [(n.canonical, n.source) for n in options.notation] == [(BOLD_D, "document")]


def test_symbols_and_course_names_never_reach_the_logs(web, caplog):
    caplog.set_level(logging.INFO)
    course_id = new_course(web.browser, "SECRET-COURSE")
    converted(web, course_id)
    submit(web, f"/courses/{course_id}", [BOLD_D])
    logged = "\n".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    assert "notation_added" in logged
    for secret in ("SECRET-COURSE", BOLD_D, "bold d"):
        assert secret not in logged
