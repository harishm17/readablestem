"""Tier 1 web items of the improvements list: the title students hear (T1-7), keeping and the
clock (T1-8), the instructor screens (T1-11), the link row (T1-15), the contact line (T1-17),
the repeated-heading card (T1-18) and the table card's headers button (T1-19). T1-3 (a
heading made bold text is a decision) is in test_web_bold_headings.py, T1-16 (admin tables)
in the browser suite.

Uses the real-shaped documents of fixtures.web.review_doc and fixtures.web.table_doc.
"""

from __future__ import annotations

import re
from datetime import timedelta
from html import unescape

import pytest

from fixtures.web.review_doc import GROUP, review_output, review_pdf
from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, token, upload
from fixtures.web.table_doc import table_output, table_pdf
from readablestem.core.types import (
    Check,
    DocumentResult,
    DocumentStats,
    Element,
    Fix,
    Kind,
    Link,
    Notice,
    SourceFacts,
    Status,
)
from readablestem.ops.retention import purge_expired
from readablestem.web import heading_groups, job_page, retention
from readablestem.web.database import as_utc
from readablestem.web.db import Job, session_scope, utcnow


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def make_env(tmp_path, output, pdf, **settings):
    env = WebEnv(tmp_path, **settings)
    env.converter.output = output
    return env


@pytest.fixture
def ux(tmp_path):
    env = make_env(tmp_path, review_output(), review_pdf())
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def page(env, path: str = "") -> str:
    response = env.browser.get(f"/jobs/{env.job_id}{path}")
    assert response.status_code == 200, response.status_code
    return response.text


def post(env, path: str, status: int = 303, **fields):
    csrf = token(env.browser, f"/jobs/{env.job_id}")
    response = env.browser.post(f"/jobs/{env.job_id}{path}", data={"csrf_token": csrf} | fields)
    assert response.status_code == status, (response.status_code, response.text[:300])
    return response


def decide(env, target: str, **fields):
    version = env.job(env.job_id).result_version
    return post(env, f"/review/{target}", version=str(version), **fields)


def stored_job(env) -> Job:
    return env.job(env.job_id)


def set_retain_until(env, moment) -> None:
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, env.job_id)
        assert job is not None
        job.retain_until = moment


def retain_in(env) -> timedelta:
    return as_utc(stored_job(env).retain_until) - utcnow()


# --------------------------------------------------------------------------- T1-8: keeping


def test_the_upload_form_has_two_separate_choices_both_off(ux):
    html = ux.browser.get("/app").text
    for box, label in (
        ("keep", "Keep for 7 days, only I can see it"),
        ("share", "Also let the developer look (30 days)"),
    ):
        assert f'id="{box}"' in html and f'name="{box}"' in html
        assert label in html
        tag = re.search(rf'<input[^>]*id="{box}"[^>]*>', html)
        assert tag and "checked" not in tag.group(0)
    assert "<legend>How long to keep my document</legend>" in html


@pytest.mark.parametrize(
    ("fields", "mode", "opt_in", "days"),
    [
        ({}, "day", False, 1),
        ({"keep": "on"}, "week", False, 7),
        ({"share": "on"}, "dev", True, 30),
        ({"keep": "on", "share": "on"}, "dev", True, 30),
    ],
)
def test_each_choice_sets_the_mode_and_the_time_kept(ux, fields, mode, opt_in, days):
    csrf = token(ux.browser)
    response = ux.browser.post(
        "/jobs",
        data={"csrf_token": csrf, **fields},
        files={"pdf": ("notes.pdf", review_pdf(), "application/pdf")},
    )
    job = ux.job(job_id_from(response))
    assert (job.retention_mode, job.opt_in_retention) == (mode, opt_in)
    assert as_utc(job.retain_until) - as_utc(job.created_at) == timedelta(days=days)


def test_a_review_decision_restarts_the_24_hour_clock(ux):
    set_retain_until(ux, utcnow() + timedelta(hours=2))
    decide(ux, "p1-e2", action="accept")
    assert timedelta(hours=23, minutes=55) < retain_in(ux) <= timedelta(hours=24)


def test_a_download_restarts_the_clock_of_a_day_and_a_week_job(ux):
    set_retain_until(ux, utcnow() + timedelta(hours=2))
    assert ux.browser.get(f"/jobs/{ux.job_id}/download").status_code == 200
    assert timedelta(hours=23, minutes=55) < retain_in(ux) <= timedelta(hours=24)

    post(ux, "/keep")
    set_retain_until(ux, utcnow() + timedelta(days=1))
    assert ux.browser.get(f"/jobs/{ux.job_id}/download").status_code == 200
    assert timedelta(days=6, hours=23) < retain_in(ux) <= timedelta(days=7)


def test_activity_never_shortens_the_time_kept(ux):
    post(ux, "/keep")
    far = utcnow() + timedelta(days=20)
    set_retain_until(ux, far)
    decide(ux, "p1-e2", action="accept")
    assert as_utc(stored_job(ux).retain_until) == as_utc(far)


def test_the_developer_look_choice_keeps_its_30_days_from_upload(ux):
    with session_scope(ux.ctx.factory) as session:
        job = session.get(Job, ux.job_id)
        assert job is not None
        job.retention_mode, job.opt_in_retention = "dev", True
        job.retain_until = utcnow() + timedelta(days=3)
    before = stored_job(ux).retain_until
    decide(ux, "p1-e2", action="accept")
    ux.browser.get(f"/jobs/{ux.job_id}/download")
    assert stored_job(ux).retain_until == before


def test_keep_7_more_days_on_the_job_page(ux):
    html = page(ux)
    assert "Keep 7 more days" in html
    assert f'action="/jobs/{ux.job_id}/keep"' in html
    set_retain_until(ux, utcnow() + timedelta(hours=1))
    post(ux, "/keep")
    job = stored_job(ux)
    assert (job.retention_mode, job.opt_in_retention) == ("week", False)
    assert timedelta(days=6, hours=23) < retain_in(ux) <= timedelta(days=7)
    assert "kept for 7 days, and only you can see it" in text_of(page(ux))


def test_a_developer_look_job_is_not_offered_more_days(ux):
    with session_scope(ux.ctx.factory) as session:
        job = session.get(Job, ux.job_id)
        assert job is not None
        job.retention_mode, job.opt_in_retention = "dev", True
    assert "Keep 7 more days" not in page(ux)
    before = stored_job(ux).retain_until
    post(ux, "/keep")
    assert stored_job(ux).retain_until == before and stored_job(ux).retention_mode == "dev"


def test_keeping_needs_the_csrf_token_and_the_owner(ux):
    refused = ux.browser.post(f"/jobs/{ux.job_id}/keep", data={})
    assert refused.status_code == 403
    assert stored_job(ux).retention_mode == "day"
    login(ux.browser, BOB)
    csrf = token(ux.browser)
    other = ux.browser.post(f"/jobs/{ux.job_id}/keep", data={"csrf_token": csrf})
    assert other.status_code == 404
    assert stored_job(ux).retention_mode == "day"


def test_the_purge_leaves_a_document_worked_on_since(ux):
    """The purge knows only `retain_until`: a decision moved it, so the document is not
    deleted at the hour it would have been."""
    set_retain_until(ux, utcnow() + timedelta(hours=1))
    decide(ux, "p1-e2", action="accept")
    later = utcnow() + timedelta(hours=3)
    assert purge_expired(ux.ctx.factory, later).jobs_deleted == 0
    assert purge_expired(ux.ctx.factory, utcnow() + timedelta(hours=25)).jobs_deleted == 1


def test_the_clock_functions_on_their_own():
    class Job_:  # a job row's fields, nothing else
        retention_mode = "day"
        retain_until = utcnow() + timedelta(hours=1)

    job = Job_()
    now = utcnow()

    class S:
        retention_hours = 24
        opt_in_retention_days = 30

    assert retention.restart_clock(job, S(), now)  # type: ignore[arg-type]
    assert job.retain_until == now + timedelta(hours=24)
    assert not retention.restart_clock(job, S(), now)  # type: ignore[arg-type]
    assert retention.mode_for_upload(keep=False, share=False) == "day"
    assert retention.mode_for_upload(keep=True, share=False) == "week"
    assert retention.mode_for_upload(keep=False, share=True) == "dev"


# --------------------------------------------------------------------------- T1-7: the title


def titled_env(tmp_path, title: str):
    output = review_output()
    output.result.title = title
    env = make_env(tmp_path, output, review_pdf())
    return env


@pytest.fixture
def titled(tmp_path):
    env = titled_env(tmp_path, "1 Derivatives")
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf(), filename="week3.pdf"))
        env.run_worker()
        yield env


def title_field(html: str) -> str:
    return unescape(re.search(r'<input type="text" id="title"[^>]*value="([^"]*)"', html).group(1))


def html_title(html: str) -> str:
    return unescape(re.search(r"<title>(.*?)</title>", html, re.S).group(1))


def test_the_title_field_starts_with_the_pdf_title_without_its_section_number(titled):
    html = page(titled)
    assert title_field(html) == "Derivatives"
    assert "<h1>Derivatives</h1>" in html
    assert '<p class="doc-file">File: week3.pdf</p>' in html
    assert "Derivatives | a11y-stem" in html_title(html) or "Derivatives" in html_title(html)
    assert 'for="title">Title students hear</label>' in html


def test_the_list_download_name_and_delete_page_use_the_same_title(titled):
    listing = titled.browser.get("/app").text
    assert ">Derivatives</a>" in listing and "1 Derivatives" not in text_of(listing)
    response = titled.browser.get(f"/jobs/{titled.job_id}/download")
    assert 'filename="derivatives-' in response.headers["content-disposition"]
    confirm = titled.browser.get(f"/jobs/{titled.job_id}/delete").text
    assert "Delete Derivatives" in text_of(confirm) or "“Derivatives”" in text_of(confirm)


def test_setting_a_title_remakes_the_file_and_names_everything(titled):
    post(titled, "/title", title="  Calculus   week 3 ")
    job = stored_job(titled)
    assert job.title_override == "Calculus week 3"
    assert html_title(job.html) == "Calculus week 3"
    html = page(titled)
    assert "<h1>Calculus week 3</h1>" in html
    assert title_field(html) == "Calculus week 3"
    assert "The title students hear is now “Calculus week 3”" in text_of(html)
    assert ">Calculus week 3</a>" in titled.browser.get("/app").text
    response = titled.browser.get(f"/jobs/{titled.job_id}/download")
    assert 'filename="calculus-week-3-' in response.headers["content-disposition"]
    assert "<title>Calculus week 3</title>" in response.text


def test_the_default_title_is_what_the_file_already_says(titled):
    assert html_title(stored_job(titled).html) == "Vectors"  # the fixture's own file
    post(titled, "/title", title="Derivatives")  # the suggestion: no choice stored
    assert stored_job(titled).title_override is None


def test_an_empty_title_goes_back_to_the_pdfs(titled):
    post(titled, "/title", title="Calculus")
    post(titled, "/title", title="")
    assert stored_job(titled).title_override is None
    assert title_field(page(titled)) == "Derivatives"


def test_a_title_that_is_too_long_is_refused_with_what_was_typed(titled):
    typed = "x" * 201
    response = post(titled, "/title", status=400, title=typed)
    assert "Use a title of 200 characters or fewer." in response.text
    assert title_field(response.text) == typed
    assert 'aria-invalid="true"' in response.text and "There is a problem" in response.text
    assert stored_job(titled).title_override is None


def test_setting_a_title_needs_the_csrf_token_and_the_owner(titled):
    assert (
        titled.browser.post(f"/jobs/{titled.job_id}/title", data={"title": "X"}).status_code == 403
    )
    login(titled.browser, BOB)
    csrf = token(titled.browser)
    other = titled.browser.post(
        f"/jobs/{titled.job_id}/title", data={"csrf_token": csrf, "title": "Mine"}
    )
    assert other.status_code == 404
    assert stored_job(titled).title_override is None


def test_a_title_is_escaped_in_the_page_and_the_file(titled):
    post(titled, "/title", title='A <b>bold</b> & "odd" title')
    assert "<b>bold</b>" not in page(titled)
    assert "<b>bold</b>" not in stored_job(titled).html


# --------------------------------------------------------------------------- T1-17: contact


def footer_of(html: str) -> str:
    return html[html.index("<footer") :]


def test_the_contact_line_goes_in_the_student_footer(ux):
    post(ux, "/contact", contact="prof.lee@example.edu")
    footer = footer_of(stored_job(ux).html)
    assert (
        'tell your instructor: <a href="mailto:prof.lee@example.edu">prof.lee@example.edu</a>.'
        in footer
    )
    downloaded = ux.browser.get(f"/jobs/{ux.job_id}/download").text
    assert 'href="mailto:prof.lee@example.edu"' in downloaded
    html = page(ux)
    assert 'id="contact"' in html and 'value="prof.lee@example.edu"' in html
    assert "added to the student file" in text_of(html)


def test_a_web_address_is_accepted_and_clearing_it_removes_the_line(ux):
    post(ux, "/contact", contact="https://math.example.edu/help")
    assert '<a href="https://math.example.edu/help">' in footer_of(stored_job(ux).html)
    post(ux, "/contact", contact="")
    assert stored_job(ux).contact_line is None
    assert "<a href" not in footer_of(stored_job(ux).html)


@pytest.mark.parametrize(
    "typed", ["javascript:alert(1)", "not an address", "ftp://example.edu", "a@b", "<x@y.edu>"]
)
def test_a_contact_line_that_is_not_an_address_is_refused_with_what_was_typed(ux, typed):
    response = post(ux, "/contact", status=400, contact=typed)
    assert "Enter an email address such as name@example.edu" in response.text
    assert stored_job(ux).contact_line is None
    assert "javascript:" not in stored_job(ux).html


def test_the_contact_line_survives_a_review_decision(ux):
    post(ux, "/contact", contact="prof.lee@example.edu")
    decide(ux, "p1-e2", action="accept")
    assert "mailto:prof.lee@example.edu" in stored_job(ux).html


def test_a_title_the_instructor_set_survives_a_review_decision(titled):
    post(titled, "/title", title="Calculus")
    decide(titled, "p1-e2", action="accept")
    assert html_title(stored_job(titled).html) == "Calculus"


def test_the_contact_line_needs_the_csrf_token_and_the_owner(ux):
    assert (
        ux.browser.post(f"/jobs/{ux.job_id}/contact", data={"contact": "a@b.edu"}).status_code
        == 403
    )
    login(ux.browser, BOB)
    csrf = token(ux.browser)
    other = ux.browser.post(
        f"/jobs/{ux.job_id}/contact", data={"csrf_token": csrf, "contact": "a@b.edu"}
    )
    assert other.status_code == 404
    assert stored_job(ux).contact_line is None


# --------------------------------------------------------------------------- T1-11


def test_no_model_cost_or_internal_codes_on_the_job_page(ux):
    html = page(ux)
    text = text_of(html)
    assert "Model cost" not in text and "cost" not in text.lower().replace("decision", "")
    assert "Code:" not in text and "<code>" not in html
    assert "Confidence" not in text


def test_the_job_page_counts_decisions_and_nothing_else(ux):
    text = text_of(page(ux))
    assert "Decisions to make 3" in text and "Decisions you made 0" in text
    assert "Accepted for you" not in text and "Of the" not in text
    decide(ux, GROUP, action="reject")
    text = text_of(page(ux))
    assert "Decisions to make 2" in text and "Decisions you made 1" in text


def test_the_list_view_has_no_codes_or_confidence_and_no_model_cost(ux):
    html = ux.browser.get(f"/jobs/{ux.job_id}/review/list").text
    text = text_of(html)
    assert "Confidence" not in text and "Code:" not in text and "code-line" not in html
    assert not re.search(r"\d+%", text)


def test_language_is_one_line_with_a_change_control(ux):
    html = page(ux)
    section = html[html.index('id="language-heading"') : html.index('id="student-file-heading"')]
    assert re.search(r"<p>Language: English", section)
    assert '<summary>Change<span class="visually-hidden"> the language</span></summary>' in section
    assert section.count("<select") == 1


def test_the_checks_say_what_ran_and_what_did_not(ux):
    html = page(ux)
    assert "What was checked, and what was not" in html
    block = text_of(html[html.index('id="checks-ran"') :].split("</details>")[0])
    assert "Ran on this file:" in block and "Did not run on this file:" in block
    assert "axe accessibility test" in block and "screen reader" in block
    assert "compliant" not in html.lower() and "verified" not in html.lower()


# --------------------------------------------------------------------------- T1-15: links


def link_result(links_kept: int, total: int, dropped: int) -> DocumentResult:
    result = review_output().result
    result.source_facts = SourceFacts(links=total, tagged=False)
    host = next(e for e in result.elements if e.id == "p1-e1")
    host.links = [
        Link(kind="uri", href=f"https://example.edu/{i}", start=i, end=i + 1)
        for i in range(links_kept)
    ]
    if dropped:
        result.notices = [
            Notice(code="link_scheme_dropped", page=0, element_id="p1-e1", count=dropped)
        ]
    return result


def link_row(result: DocumentResult) -> job_page.Row:
    card = job_page.before_after(result)
    assert card is not None
    return next(r for r in card.rows if r.question == "follow the links")


def test_the_link_row_counts_the_links_kept_and_names_the_lost_ones():
    row = link_row(link_result(6, 8, 2))
    assert row.pdf == "Yes: 8 links."
    assert row.html == "6 of 8 links kept; 2 were not web addresses."
    assert row.href in {"#not-handled-heading", "#worth-checking-heading"}
    assert row.href_text == "See the notice"


def test_the_link_row_says_yes_only_when_every_link_was_kept():
    row = link_row(link_result(3, 3, 0))
    assert row.html == "Yes: 3 links." and not row.href
    assert link_row(link_result(1, 3, 0)).html == "1 of 3 links kept."
    assert link_row(link_result(0, 2, 1)).html == "0 of 2 links kept; 1 was not web addresses."


def test_the_link_row_on_the_job_page_links_to_its_notice(tmp_path):
    output = review_output()
    output.result = link_result(6, 8, 2)
    env = make_env(tmp_path, output, review_pdf())
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        html = page(env)
    assert "6 of 8 links kept; 2 were not web addresses." in text_of(html)
    href = re.search(
        r'kept; 2 were not web addresses\.\s*<a href="(#[^"]+)">See the notice</a>', html
    )
    assert href
    assert f'id="{href.group(1)[1:]}"' in html
    assert "A link in the PDF was not kept because it was not a web or email address." in html


# --------------------------------------------------------------------------- T1-18: groups


def heading(eid: str, order: int, text: str, level: int = 2) -> tuple[Element, Fix]:
    from readablestem.core.types import HeadingFix

    element = Element(id=eid, kind=Kind.HEADING, page=0, bbox=(72, 10 * order, 300, 10 * order + 9),
                      order=order, text=text, bold=True)  # fmt: skip
    fix = Fix(element_id=eid, kind=Kind.HEADING, status=Status.ACCEPTED, confidence=0.9,
              heading=HeadingFix(level=level, text=text))  # fmt: skip
    return element, fix


def group_result(texts_above: list[str | None], label: str = "Teacher Support"):
    """One box title (or a paragraph) above each copy of `label`."""
    elements: list[Element] = []
    fixes: list[Fix] = []
    members: list[Fix] = []
    order = 0
    for i, above in enumerate(texts_above):
        if above is not None:
            element, fix = heading(f"b{i}", order, above, 2)
            elements.append(element)
            fixes.append(fix)
        else:
            elements.append(Element(id=f"t{i}", kind=Kind.PARAGRAPH, page=0, order=order,
                                    bbox=(0, 0, 1, 1), text="Some text."))  # fmt: skip
        order += 1
        element, fix = heading(f"c{i}", order, label, 3)
        fix.status, fix.group_key, fix.group_size = Status.NEEDS_REVIEW, "hg-c0", len(texts_above)
        elements.append(element)
        fixes.append(fix)
        members.append(fix)
        order += 1
    result = DocumentResult(
        pipeline_version="t",
        elements=elements,
        fixes=fixes,
        stats=DocumentStats(pages=1, cost_usd=0, latency_ms=0, model_calls=0),
        html="",
    )
    return result, members


def test_the_advice_is_bold_text_when_each_copy_sits_under_the_same_words():
    result, members = group_result(["Teacher Support", "teacher  support", "Teacher Support"])
    got = heading_groups.advice(result, members)
    assert got.suggest == "bold" and got.under_same == 3 and got.total == 3
    assert "Each copy sits directly under a heading with the same words" in got.fact


def test_the_advice_counts_the_copies_when_only_some_sit_under_the_same_words():
    result, members = group_result(["Teacher Support", "Teacher Support", None, None])
    got = heading_groups.advice(result, members)
    assert got.under_same == 2 and got.suggest == "keep"  # not more than half
    result, members = group_result(["Teacher Support", "Teacher Support", "Teacher Support", None])
    got = heading_groups.advice(result, members)
    assert got.suggest == "bold"
    assert got.fact.startswith("3 of the 4 copies sit directly under a heading")


def test_a_run_in_label_is_bold_text_and_other_labels_keep_the_proposal():
    result, members = group_result([None, None], label="Solution")
    got = heading_groups.advice(result, members)
    assert got.suggest == "bold" and "usually bold text, not headings" in got.fact
    result, members = group_result([None, None], label="Key Terms")
    got = heading_groups.advice(result, members)
    assert got.suggest == "keep" and "the tool's proposal stands" in got.fact


def test_the_crops_are_the_first_middle_and_last_copy():
    result, members = group_result([None] * 7)
    elements = {e.id: e for e in result.elements}
    shown = heading_groups.crops(members, elements)
    assert [c.element_id for c in shown] == ["c0", "c3", "c6"]
    assert [c.page for c in shown] == [1, 1, 1]
    assert [c.element_id for c in heading_groups.crops(members[:2], elements)] == ["c0", "c1"]
    assert [c.element_id for c in heading_groups.crops(members[:1], elements)] == ["c0"]


def card(env, query: str = "") -> str:
    return env.browser.get(f"/jobs/{env.job_id}/review?item={GROUP}{query}").text


def test_the_group_card_names_the_fact_shows_crops_and_suggests_keeping(ux):
    """The fixture's copies sit under no heading with the same words."""
    html = card(ux)
    text = text_of(html)
    assert "Suggested: keep them as headings." in text
    assert "No copy sits directly under a heading with the same words" in text
    for eid in ("p1-e3", "p1-e7", "p2-e0"):
        assert f"/jobs/{ux.job_id}/regions/{eid}.png" in html
    assert html.count('class="region-image" loading="lazy"') == 3
    keep = re.search(
        r'<button[^>]*class="(button[^"]*)"[^>]*data-role="accept">Keep as headings', html
    )
    bold = re.search(r'<button[^>]*class="(button[^"]*)">Make bold text', html)
    assert keep and keep.group(1) == "button" and bold and bold.group(1) == "button-secondary"


def doubled_output():
    """The fixture with a box title "Teacher Support" directly above each copy."""
    output = review_output()
    result = output.result
    elements = sorted(result.elements, key=lambda e: e.order)
    rebuilt: list[Element] = []
    for element in elements:
        if element.id in {"p1-e3", "p1-e7", "p2-e0"}:
            title, fix = heading(f"box-{element.id}", 0, "Teacher Support", 2)
            title.page = element.page
            rebuilt.append(title)
            result.fixes.append(fix)
        rebuilt.append(element)
    for order, element in enumerate(rebuilt):
        element.order = order
    result.elements = rebuilt
    return output


def test_with_a_box_title_above_each_copy_bold_text_is_the_main_button(tmp_path):
    env = make_env(tmp_path, doubled_output(), review_pdf())
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        html = card(env)
        text = text_of(html)
        assert "Suggested: make them bold text." in text
        assert "Each copy sits directly under a heading with the same words" in text
        bold = re.search(r'<button[^>]*class="(button[^"]*)">Make bold text', html)
        keep = re.search(
            r'<button[^>]*class="(button[^"]*)"[^>]*data-role="accept">Keep as headings', html
        )
        assert bold and bold.group(1) == "button" and keep and keep.group(1) == "button-secondary"
        assert html.index("Make bold text") < html.index("Keep as headings")
        # The list view says the same, with its own buttons.
        listed = env.browser.get(f"/jobs/{env.job_id}/review/list").text
        assert "Suggested: make them bold text." in text_of(listed)
        assert re.search(r'class="button">Make all 3 bold text', listed)
        assert re.search(r'class="button-secondary">Apply to all 3', listed)


# --------------------------------------------------------------------------- T1-19: tables


@pytest.fixture
def tables(tmp_path):
    output = table_output()
    fix = output.result.fix_for("p1-e1")
    assert fix is not None
    fix.checks = [Check(name="table.headers", passed=False, severity="warning", code="no_headers")]
    env = make_env(tmp_path, output, table_pdf())
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, table_pdf()))
        env.run_worker()
        yield env


def test_when_only_headers_are_missing_that_is_the_main_button(tables):
    html = tables.browser.get(f"/jobs/{tables.job_id}/review?item=p1-e1").text
    main = re.search(r'<button[^>]*class="button"[^>]*>Use the first row as headers', html)
    assert main
    assert re.search(
        r'class="button-secondary"[^>]*data-role="accept">Accept without headers', html
    )
    assert html.index("Use the first row as headers") < html.index("Accept without headers")


def test_the_button_sets_the_first_row_as_headers_and_settles_the_table(tables):
    version = tables.job(tables.job_id).result_version
    post(
        tables,
        "/review/p1-e1",
        action="edit",
        change="headers",
        header_row="on",
        version=str(version),
    )
    result = DocumentResult.model_validate(tables.job(tables.job_id).result_json)
    fix = result.fix_for("p1-e1")
    assert fix is not None and fix.table is not None
    assert fix.table.grid.header_rows == 1 and not fix.pending


def test_other_findings_keep_accept_as_the_main_action(tmp_path):
    output = table_output()  # decision 1 has several findings, not only the headers
    env = make_env(tmp_path, output, table_pdf())
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, table_pdf()))
        env.run_worker()
        html = client.get(f"/jobs/{env.job_id}/review?item=p1-e1").text
    assert "Use the first row as headers" not in html


def test_the_list_view_offers_the_same_button(tables):
    html = tables.browser.get(f"/jobs/{tables.job_id}/review/list").text
    assert "Use the first row as headers" in html
    assert re.search(r'class="button-secondary">Accept without headers', html)


def test_the_checks_disclosure_counts_the_checks_that_ran(tmp_path):
    output = review_output()
    output.result.html_checks = [Check(name="html.lang", passed=True)]
    env = make_env(tmp_path, output, review_pdf())
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        html = page(env)
    block = text_of(html[html.index('id="checks-ran"') :].split("</details>")[0])
    assert "Ran on this file: 1 checks of its structure" in block
