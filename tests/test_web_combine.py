"""The "Combine parts" download (GET /jobs/{id}/combine, web/combine.py): one HTML file from the
finished parts of one source that their owner still keeps, in page order. Only the owner's
parts, never a deleted or unfinished one, and nothing is changed by asking."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from fixtures.web.support import BOB, fake_output
from readablestem.core.types import ConversionOutput, Status
from readablestem.web import ranges
from readablestem.web.combine import number_part_pages
from readablestem.web.db import Job, JobStatus, User, session_scope, utcnow
from test_web_parts import choose, jobs_of, keep_big, text_of, web  # noqa: F401


def settled_output() -> ConversionOutput:
    """The fake conversion with every decision made, so the results page is the finish line."""
    output = fake_output()
    for fix in output.result.fixes:
        fix.status, fix.review = Status.ACCEPTED, "none"
    return output


def three_finished_parts(web) -> tuple[str, list[Job]]:  # noqa: F811
    web.converter.output = settled_output()
    upload_id = keep_big(web, 12)
    assert choose(web, upload_id, "1-4\n5-8\n9-12").status_code == 303
    for _ in range(3):
        web.run_worker()
    jobs = jobs_of(web)
    assert [j.status for j in jobs] == [JobStatus.DONE] * 3
    return upload_id, jobs


def change(web, job: Job, **fields) -> None:  # noqa: F811
    with session_scope(web.ctx.factory) as session:
        row = session.get(Job, job.id)
        for name, value in fields.items():
            setattr(row, name, value)


def combine(web, job: Job, client=None):  # noqa: F811
    return (client or web.alice).get(f"/jobs/{job.id}/combine")


def ids_of(html: str) -> list[str]:
    return re.findall(r'\bid="([^"]+)"', html)


# ----------------------------------------------------------------------------- the file


def test_the_parts_are_joined_in_page_order_with_unique_ids_and_markers_that_continue(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    response = combine(web, jobs[1])  # asked from the middle part, whichever it is
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["content-disposition"].startswith("attachment; filename=")
    assert "chapter2-combined-" in response.headers["content-disposition"]
    assert "content-security-policy" in response.headers
    html = response.text
    ids = ids_of(html)
    assert len(ids) == len(set(ids))
    assert {"p1-e0", "p5-e0", "p9-e0"} <= set(ids)  # each part's first element, by source page
    assert re.findall(r'aria-label="(Page \d+)"', html) == ["Page 1", "Page 5", "Page 9"]
    assert html.count("<main>") == 1 and html.count('aria-label="Contents"') == 1
    assert "<title>chapter2</title>" in html
    assert html.count("<math") == html.count("<math ") + html.count("<math>")  # all there
    assert html.count("<figure") == 3 or html.count("<img") >= 3  # each part's figure
    assert "left out of this file" not in html


def test_each_part_stores_where_its_pages_are_in_the_source(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    with session_scope(web.ctx.factory) as session:
        labels = [session.get(Job, j.id).result_json["page_labels"] for j in jobs]
    assert labels == [{"0": "1"}, {"0": "5"}, {"0": "9"}]


def test_only_the_label_pages_with_elements_are_numbered_and_printed_labels_win():
    class Result:
        def __init__(self):
            from readablestem.core.types import DocumentResult, DocumentStats, Element, Kind

            self.value = DocumentResult(
                pipeline_version="t",
                stats=DocumentStats(pages=3, cost_usd=0, latency_ms=0, model_calls=0),
                elements=[
                    Element(id=f"p{n + 1}-e0", kind=Kind.PARAGRAPH, page=n, bbox=(0, 0, 1, 1),
                            order=n, text="x")
                    for n in (0, 2)
                ],
                page_labels={0: "iv"},
            )  # fmt: skip

    job = Job(part_label="31-33")
    result = Result().value
    assert number_part_pages(job, result) is True
    assert result.page_labels == {0: "iv", 2: "33"}  # page 1 has no elements: no label
    assert number_part_pages(job, result) is False  # nothing more to add
    assert number_part_pages(Job(part_label=None), result) is False


# ----------------------------------------------------------------------------- ownership


def test_another_person_cannot_combine_my_parts(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    assert combine(web, jobs[0], web.bob).status_code == 404
    assert web.alice.get("/jobs/not-a-job/combine").status_code == 404
    anonymous = web.client()
    response = combine(web, jobs[0], anonymous)
    assert response.status_code in {303, 401, 403} and "p1-e0" not in response.text
    anonymous.close()


def test_a_part_of_someone_else_in_the_same_group_is_never_included(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    with session_scope(web.ctx.factory) as session:
        bob_id = session.scalar(select(User.id).where(User.email == BOB))
    change(web, jobs[1], user_id=bob_id)
    html = combine(web, jobs[0]).text
    ids = set(ids_of(html))
    assert {"p1-e0", "p9-e0"} <= ids and "p5-e0" not in ids
    assert "Pages 5, 6, 7 and 8 of the original PDF were left out of this file." in html


def test_the_group_is_the_one_of_the_job_asked_about_not_one_the_person_chooses(web):  # noqa: F811
    first, jobs_a = three_finished_parts(web)
    second = keep_big(web, 12, "other.pdf")
    choose(web, second, "1-4\n5-8")
    web.run_worker()
    web.run_worker()
    other = [j for j in jobs_of(web) if j.part_group == second]
    html = combine(web, other[0]).text
    assert "<title>other</title>" in html and "chapter2" not in html
    assert len([i for i in ids_of(html) if i.endswith("-e0")]) == 2
    assert first != second and len(jobs_a) == 3


# ----------------------------------------------------------------------------- retention


def test_a_deleted_or_expired_part_is_left_out_and_the_file_names_the_missing_pages(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    change(web, jobs[1], retain_until=utcnow() - timedelta(minutes=1))  # expired, not yet purged
    html = combine(web, jobs[0]).text
    assert "p5-e0" not in ids_of(html) and {"p1-e0", "p9-e0"} <= set(ids_of(html))
    assert "Pages 5, 6, 7 and 8 of the original PDF were left out of this file." in html
    with session_scope(web.ctx.factory) as session:
        session.delete(session.get(Job, jobs[1].id))  # really deleted
    assert "p5-e0" not in combine(web, jobs[2]).text


def test_with_fewer_than_two_parts_left_there_is_nothing_to_combine(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    for job in jobs[1:]:
        change(web, job, retain_until=utcnow() - timedelta(minutes=1))
    response = combine(web, jobs[0])
    assert response.status_code == 409
    assert "Nothing to combine yet" in text_of(response.text)
    assert "at least two finished parts" in text_of(response.text)


def test_a_part_that_is_not_finished_is_not_joined(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    change(web, jobs[2], status=JobStatus.RUNNING)
    html = combine(web, jobs[0]).text
    assert "p9-e0" not in ids_of(html) and {"p1-e0", "p5-e0"} <= set(ids_of(html))


def test_a_document_that_is_not_a_part_has_nothing_to_combine(web):  # noqa: F811
    from fixtures.web.support import make_pdf, upload

    response = upload(web.alice, make_pdf(2))
    job_id = response.headers["location"].rsplit("/", 1)[-1]
    web.run_worker()
    assert web.alice.get(f"/jobs/{job_id}/combine").status_code == 409


def test_combining_changes_nothing_and_needs_no_token(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    with session_scope(web.ctx.factory) as session:
        before = [
            (j.retain_until, j.result_version, j.html_stale)
            for j in (session.get(Job, x.id) for x in jobs)
        ]
    assert combine(web, jobs[0]).status_code == 200
    with session_scope(web.ctx.factory) as session:
        after = [
            (j.retain_until, j.result_version, j.html_stale)
            for j in (session.get(Job, x.id) for x in jobs)
        ]
    assert after == before
    # a POST (which would carry a token if state changed) is not a way to ask
    assert web.alice.post(f"/jobs/{jobs[0].id}/combine").status_code in {403, 405}


# ----------------------------------------------------------------------------- the link


def test_the_results_page_of_a_part_links_to_combine_when_two_parts_are_finished(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    page = web.alice.get(f"/jobs/{jobs[1].id}").text
    assert f'<a href="/jobs/{jobs[1].id}/combine">Combine parts</a>' in page
    assert page.count("/combine") == 1  # one link
    text = text_of(page)
    assert "the 3 finished parts of this file that you still have, in page order" in text
    assert "A part you deleted, or one that is not finished, is left out" in text
    change(web, jobs[0], retain_until=utcnow() - timedelta(minutes=1))
    assert "the 2 finished parts" in text_of(web.alice.get(f"/jobs/{jobs[1].id}").text)
    change(web, jobs[2], status=JobStatus.RUNNING)
    assert "/combine" not in web.alice.get(f"/jobs/{jobs[1].id}").text


def test_a_document_that_is_not_a_part_has_no_combine_link(web):  # noqa: F811
    from fixtures.web.support import make_pdf, upload

    job_id = upload(web.alice, make_pdf(2)).headers["location"].rsplit("/", 1)[-1]
    web.run_worker()
    assert "/combine" not in web.alice.get(f"/jobs/{job_id}").text


def test_the_limits_page_no_longer_says_parts_cannot_be_joined(web):  # noqa: F811
    limits = text_of(web.alice.get("/limits").text)
    assert "not joined into one file" not in limits
    assert "a “Combine parts” link on a part's page joins the ones you still have" in limits


@pytest.mark.parametrize(
    ("label", "pages"),
    [
        ("1-4", (0, 1, 2, 3)),
        ("5", (4,)),
        ("1-3, 9", (0, 1, 2, 8)),
        ("31-59", tuple(range(30, 59))),
    ],
)
def test_a_label_names_its_pages_and_label_reads_back(label, pages):
    assert ranges.pages_of_label(label) == pages
    assert ranges.label(pages) == label


@pytest.mark.parametrize("label", ["", None, "0", "3-1", "a", "1-", "1,,2", "1 - 2 - 3"])
def test_what_is_not_a_label_names_no_pages(label):
    assert ranges.pages_of_label(label) is None
