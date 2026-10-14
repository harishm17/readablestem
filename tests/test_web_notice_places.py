"""T3-7: notices name the link or figure they are about, link to its place in the Split View
and are listed with the optional checks.

Uses fixtures.web.review_doc, with a link whose text says nothing ("here") in a paragraph,
a figure that tells things apart by colour alone, and a notice for each. The notice stays
content-free (a code, a page, an element id); the names are worked out again from the
result when a page is shown, for the owner only.
"""

from __future__ import annotations

import re
from html import unescape

import pytest

from a11y_stem.core.types import ConversionOutput, DocumentResult, Link, Notice
from a11y_stem.web import notice_places
from a11y_stem.web.review import item_labels
from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload

PARAGRAPH = "p1-e4"
TEXT = "Open the simulation here to practise vectors and scalars."


def noted_output() -> ConversionOutput:
    output = review_output()
    result = output.result
    paragraph = next(e for e in result.elements if e.id == PARAGRAPH)
    paragraph.text = TEXT
    start = TEXT.index("here")
    paragraph.links = [
        Link(kind="uri", start=start, end=start + 4, href="https://example.org/sim"),
        Link(kind="uri", start=0, end=4, href="https://example.org/open"),
    ]
    figure = result.fix_for("p1-e6")
    assert figure is not None and figure.figure is not None
    figure.figure.colour_only = True
    result.notices.append(Notice(code="link_text_unclear", page=0, element_id=PARAGRAPH))
    return output


@pytest.fixture
def noted(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = noted_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


# --------------------------------------------------------------------------- the rules


def test_a_notice_names_the_link_and_the_figure():
    result = noted_output().result
    labels = item_labels(result)
    found = {p.notice.code: p for p in notice_places.places(result, labels)}
    link = found["link_text_unclear"]
    assert link.names == ("here",)  # "Open" is not unclear; only the link that says nothing
    assert link.snippet.startswith("Open the simulation here")
    assert link.key == f"link_text_unclear:{PARAGRAPH}"
    assert link.query == f"page=1&note=link_text_unclear%3A{PARAGRAPH}"
    figure = found["colour_only"]
    assert figure.names == ("Figure 1",)
    assert figure.query == "item=p1-e6&page=1"
    assert figure.key == ""  # a figure has its own panel; no note panel


def test_a_notice_about_the_whole_document_has_no_place():
    result = noted_output().result
    result.notices.append(Notice(code="lang_assumed"))
    places = {p.notice.code: p for p in notice_places.places(result, item_labels(result))}
    assert places["lang_assumed"].query == "" and places["lang_assumed"].address == ""
    fixes = {f.element_id: f for f in result.fixes}
    assert not notice_places.shown_in_review(places["lang_assumed"], fixes)
    assert notice_places.shown_in_review(places["link_text_unclear"], fixes)


def test_a_decision_notice_is_not_listed_again_with_the_optional_checks():
    result = noted_output().result
    fixes = {f.element_id: f for f in result.fixes}
    result.notices.append(Notice(code="table_not_rebuilt", page=0, element_id="p1-e6"))
    for place in notice_places.places(result, item_labels(result)):
        if place.notice.code == "table_not_rebuilt":
            assert not notice_places.shown_in_review(place, fixes)


# --------------------------------------------------------------------------- the job page


def test_the_job_page_names_the_link_and_links_to_its_place(noted):
    page = noted.browser.get(f"/jobs/{noted.job_id}").text
    worth = page[page.index("Worth checking") :]
    text = text_of(worth)
    assert "Page 1:" in text
    assert "A link's text does not say where it goes" in text
    assert "The link text: “here”." in text
    assert "The figure: “Figure 1”." in text
    where = f"/jobs/{noted.job_id}/review?page=1&amp;note=link_text_unclear%3A{PARAGRAPH}"
    assert f"{where}#decision-title" in page
    assert f"/jobs/{noted.job_id}/review?item=p1-e6&amp;page=1#decision-title" in page


# --------------------------------------------------------------------------- the Split View


def screen(env, query: str = "") -> str:
    response = env.browser.get(f"/jobs/{env.job_id}/review{query}")
    assert response.status_code == 200
    return response.text


def optional_box(page: str) -> str:
    start = page.index('class="optional-box"')
    return page[start : page.index("</details>", start)]


def test_the_notices_are_listed_with_the_optional_checks(noted):
    page = screen(noted)
    box = optional_box(page)
    text = text_of(box)
    assert "Optional checks (3)" in text  # the equation note, the link, the figure
    assert "A link's text does not say where it goes" in text
    assert "“here”" in text
    assert f"/jobs/{noted.job_id}/review?page=1&amp;note=link_text_unclear%3A{PARAGRAPH}" in box


def test_a_notice_about_text_has_a_panel_and_a_mark_on_the_page(noted):
    page = screen(noted, f"?page=1&note=link_text_unclear:{PARAGRAPH}")
    panel = page[page.index('id="decision"') : page.index('class="optional-area"')]
    text = text_of(panel)
    assert "Worth checking" in text and "Optional check" in text
    assert "A link's text does not say where it goes" in text
    assert "The link text: “here”." in text
    assert "The words around it" in text and TEXT in text
    # The way to change the words (T3-5), and nothing that holds the document up.
    assert f"change={PARAGRAPH}" in panel and "Change the words" in text
    # The mark: a link on the paragraph, current, in the page.
    marks = re.findall(rf'<a id="box-{PARAGRAPH}" class="box ([^"]*)"([^>]*)>', page)
    assert marks and "is-current" in marks[0][0] and 'aria-current="true"' in marks[0][1]
    assert "Page 1, optional check: A link&#39;s text does not say where it goes" in page


def test_the_mark_of_a_notice_sits_under_the_marks_of_the_fixes(noted):
    page = screen(noted)
    order = [m.group(1) for m in re.finditer(r'<a id="box-([\w-]+)" class="box', page)]
    assert PARAGRAPH in order
    assert order.index(PARAGRAPH) < order.index("p1-e2")  # before the equation inside it


def test_marks_css_places_the_paragraph(noted):
    css = noted.browser.get(f"/jobs/{noted.job_id}/review/marks.css").text
    assert f"#box-{PARAGRAPH}{{" in css


def test_a_figure_notice_opens_the_figure_itself(noted):
    page = screen(noted, "?item=p1-e6&page=1")
    assert "Figure 1" in text_of(page)


@pytest.mark.parametrize(
    "note",
    ["", "x", "link_text_unclear:", "link_text_unclear:p9-e9", "../../etc:passwd", "A:b" * 40],
)
def test_a_note_that_is_not_a_notice_falls_back_to_the_first_decision(noted, note):
    page = screen(noted, f"?note={note}")
    assert "Decision 1 of 3" in page


def test_the_list_view_lists_them_too(noted):
    page = noted.browser.get(f"/jobs/{noted.job_id}/review/list").text
    assert "Notes on links, text and figures (2)" in page
    section = page[page.index('id="notes-title"') :]
    assert "“here”" in section and "Figure 1" in section
    assert f"/jobs/{noted.job_id}/review?page=1&amp;note=link_text_unclear%3A{PARAGRAPH}" in section
    assert "Optional checks (3)" in page


def test_nothing_is_left_when_there_are_no_notices(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = review_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        page = client.get(f"/jobs/{job_id}/review/list").text
        assert "Notes on links, text and figures" not in page
        assert "Optional checks (1)" in page
        _ = token  # (imported for symmetry with the other web tests)


def test_notice_names_are_escaped_where_they_are_shown(tmp_path):
    output = noted_output()
    paragraph = next(e for e in output.result.elements if e.id == PARAGRAPH)
    paragraph.text = 'Click <script>alert(1)</script> here "now" & see'
    start = paragraph.text.index("here")
    paragraph.links = [Link(kind="uri", start=start, end=start + 4, href="https://example.org/")]
    env = WebEnv(tmp_path)
    env.converter.output = output
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        for path in (
            f"/jobs/{job_id}",
            f"/jobs/{job_id}/review",
            f"/jobs/{job_id}/review?note=link_text_unclear:{PARAGRAPH}",
            f"/jobs/{job_id}/review/list",
        ):
            page = client.get(path).text
            assert "<script>alert(1)</script>" not in page, path
        panel = client.get(f"/jobs/{job_id}/review?note=link_text_unclear:{PARAGRAPH}").text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in panel


def test_result_notices_hold_no_document_text():
    result = DocumentResult.model_validate(noted_output().result.model_dump(mode="json"))
    dumped = str([n.model_dump() for n in result.notices])
    assert "here" not in dumped and "simulation" not in dumped
