"""The "Combine parts" download (GET /jobs/{id}/combine, web/combine.py): one HTML file from the
finished parts of one source that their owner still keeps, in page order. Only the owner's
parts, never a deleted or unfinished one, and nothing is changed by asking."""

from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import select

from fixtures.web.support import fake_output
from readablestem.core.types import ConversionOutput, Status
from readablestem.ops.retention import delete_jobs
from readablestem.pipeline.assemble import FIGURE_NOT_DESCRIBED_ALT
from readablestem.web.database import as_utc
from readablestem.web.db import Blob, Job, JobStatus, session_scope, utcnow
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
    with session_scope(web.ctx.factory) as session:  # a part stored before decorations were settled
        row = session.get(Job, jobs[2].id)
        result = dict(row.result_json)
        figure = next(e for e in result["elements"] if e["kind"] == "figure")
        fix = next(f for f in result["fixes"] if f["element_id"] == figure["id"])
        fix["figure"] = dict(fix["figure"], alt="", decorative=True)  # no evidence stored
        again = dict(figure, id="p1-e99", page=figure["page"] + 1)  # the same picture, a page on
        result["elements"] = [*result["elements"], again]
        result["fixes"] = [*result["fixes"], dict(fix, element_id="p1-e99")]
        row.result_json = result
        crop = session.scalars(
            select(Blob).where(Blob.job_id == row.id, Blob.element_id == figure["id"])
        ).one()
        session.add(Blob(job_id=row.id, kind="crop", element_id="p1-e99",
                         media_type=crop.media_type, content=crop.content))  # fmt: skip
    html = combine(web, jobs[1]).text  # settled as its own page is: a repeat is decoration
    assert FIGURE_NOT_DESCRIBED_ALT not in html and html.count('class="decorative"') == 2


def combined_files(web) -> int:  # noqa: F811
    with session_scope(web.ctx.factory) as session:
        return len(session.scalars(select(Blob.id).where(Blob.kind == "export")).all())


def test_the_combined_file_is_kept_once_and_goes_with_any_part(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    first = combine(web, jobs[1]).content
    assert combined_files(web) == 1
    assert combine(web, jobs[2]).content == first  # asked again, from another part
    assert combined_files(web) == 1
    with session_scope(web.ctx.factory) as session:  # the last part: not the one it is kept on
        delete_jobs(session, [jobs[2].id])
    assert combined_files(web) == 0  # no copy of a deleted part is left
    assert "p9-e0" not in ids_of(combine(web, jobs[0]).text)  # and the next file leaves it out


# ----------------------------------------------------------------------------- ownership


def test_another_person_cannot_combine_my_parts(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    assert combine(web, jobs[0], web.bob).status_code == 404
    assert web.alice.get("/jobs/not-a-job/combine").status_code == 404
    anonymous = web.client()
    response = combine(web, jobs[0], anonymous)
    assert response.status_code in {303, 401, 403} and "p1-e0" not in response.text
    anonymous.close()


# ----------------------------------------------------------------------------- retention


def test_a_part_with_a_decision_open_is_held_back_like_in_download_all(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    with session_scope(web.ctx.factory) as session:
        row = session.get(Job, jobs[1].id)
        result = dict(row.result_json)
        result["fixes"] = [
            dict(f, status="needs_review", review="required") for f in result["fixes"]
        ]
        row.result_json = result
    html = combine(web, jobs[0]).text
    assert "p5-e0" not in ids_of(html) and {"p1-e0", "p9-e0"} <= set(ids_of(html))
    assert "Pages 5, 6, 7 and 8 of the original PDF were left out of this file." in html
    page = text_of(web.alice.get(f"/jobs/{jobs[0].id}").text)
    assert "the 2 finished parts" in page  # the link counts what the file would hold


def test_two_parts_with_a_page_in_common_keep_the_newest_not_a_409(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    change(web, jobs[1], part_label="3-6", created_at=utcnow() + timedelta(minutes=1))
    html = combine(web, jobs[0]).text  # the part "5-8" and the newer "3-6" share pages 5 and 6
    ids = set(ids_of(html))
    assert {"p3-e0", "p9-e0"} <= ids and not ({"p1-e0", "p5-e0"} & ids)  # 1-4 and 5-8 are older
    assert "Pages 7 and 8 of the original PDF were left out of this file." in html


# ----------------------------------------------------------------------------- the link


# ------------------------------------------------------------------- Word and EPUB


def test_the_combined_file_can_be_an_epub_recorded_in_that_file_type(web):  # noqa: F811
    import io
    import zipfile

    from readablestem.web import events

    _, jobs = three_finished_parts(web)
    response = web.alice.get(f"/jobs/{jobs[0].id}/combine?format=epub")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/epub+zip"
    assert ".epub" in response.headers["content-disposition"]
    assert zipfile.ZipFile(io.BytesIO(response.content)).namelist()[0] == "mimetype"
    with session_scope(web.ctx.factory) as session:
        assert all(events.downloaded_formats(session, j.id) == ["epub"] for j in jobs)


def test_the_combined_file_in_an_unknown_type_is_a_400_and_changes_nothing(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    soon = utcnow() + timedelta(hours=1)
    for job in jobs:
        change(web, job, retain_until=soon)
    assert web.alice.get(f"/jobs/{jobs[0].id}/combine?format=pdf").status_code == 400
    with session_scope(web.ctx.factory) as session:
        assert all(as_utc(session.get(Job, j.id).retain_until) == as_utc(soon) for j in jobs)


# ----------------------------------------------------------------------------- the page


def test_the_combine_page_answers_with_decisions_open_and_links_to_the_first_of_each(web):  # noqa: F811
    web.converter.output = fake_output()  # every part keeps its decisions open
    upload_id = keep_big(web, 12)
    assert choose(web, upload_id, "1-4\n5-8\n9-12").status_code == 303
    for _ in range(3):
        web.run_worker()
    jobs = jobs_of(web)
    # Before: nothing is ready, and the download refused with a 409 and no way on.
    response = web.alice.get(f"/jobs/{jobs[0].id}/combine/parts")
    assert response.status_code == 200
    page = text_of(response.text)
    assert "Combining needs at least two ready parts; 0 are ready now." in page
    assert "Make the decisions in 3 parts" in page
    for job in jobs:
        links = re.findall(rf'href="(/jobs/{job.id}/review\?item=[^"]+)"', response.text)
        assert len(links) == 1  # the first open decision of this part, by its key
        assert web.alice.get(links[0]).status_code == 200
    assert web.alice.get(f"/jobs/{jobs[0].id}/combine").status_code == 200  # no longer a dead end
    assert web.bob.get(f"/jobs/{jobs[0].id}/combine/parts").status_code == 404


def test_the_combine_page_offers_the_ready_parts_and_says_what_it_leaves_out(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    with session_scope(web.ctx.factory) as session:
        row = session.get(Job, jobs[1].id)
        result = dict(row.result_json)
        result["fixes"] = [
            dict(f, status="needs_review", review="required") for f in result["fixes"]
        ]
        row.result_json = result
        row.open_decisions = len(result["fixes"])
    page = text_of(web.alice.get(f"/jobs/{jobs[0].id}/combine/parts").text)
    assert "Combine the 2 ready parts (leaves out 1)" in page


def test_the_language_is_asked_once_for_the_whole_file_and_each_part_keeps_its_own(web):  # noqa: F811
    _, jobs = three_finished_parts(web)
    page_url = f"/jobs/{jobs[0].id}/combine/parts"
    assert "combine-language" not in web.alice.get(page_url).text  # all three read the same

    def set_result(job: Job, lang: str, notices: tuple[str, ...] = ()) -> None:
        with session_scope(web.ctx.factory) as session:
            row = session.get(Job, job.id)
            result = dict(row.result_json)
            kept = [n for n in result.get("notices", []) if not n["code"].startswith("lang_")]
            result["lang"] = lang
            result["notices"] = [*kept, *({"code": c, "review": "required"} for c in notices)]
            row.result_json = result

    # two parts assumed to be English: one sentence, not one per part, and the file stays English
    set_result(jobs[0], "en", ("lang_assumed",))
    set_result(jobs[1], "en", ("lang_assumed",))
    set_result(jobs[2], "en")
    page = text_of(web.alice.get(page_url).text)
    assert page.count("confirm the language") == 1
    assert "The language of 2 parts was assumed" in page
    assert "Open the parts for pages 1-4, pages 5-8, then confirm" in page  # named, not counted
    assert '<html lang="en">' in combine(web, jobs[0]).text
    # no part waits, and no language has most of the parts: said once, each part marked
    set_result(jobs[0], "de")
    set_result(jobs[1], "fr")
    set_result(jobs[2], "es")
    page = text_of(web.alice.get(page_url).text)
    assert page.count("no language has most of them") == 1
    html = combine(web, jobs[0]).text
    assert '<html lang="de">' in html and 'lang="fr"' in html and 'lang="es"' in html
    # two of three agree: nothing to ask, the third is marked
    set_result(jobs[1], "de")
    assert "combine-language" not in web.alice.get(page_url).text
    assert '<html lang="de">' in combine(web, jobs[0]).text
