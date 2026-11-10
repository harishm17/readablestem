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

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, upload
from readablestem.core.types import ConversionOutput, DocumentResult, Link, Notice
from readablestem.web import notice_places
from readablestem.web.review import item_labels

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


# --------------------------------------------------------------------------- the job page


# --------------------------------------------------------------------------- the Split View


def screen(env, query: str = "") -> str:
    response = env.browser.get(f"/jobs/{env.job_id}/review{query}")
    assert response.status_code == 200
    return response.text


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
