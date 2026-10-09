"""The review screen (routes/split.py): one decision at a time beside the page.

Uses the real-shaped document of fixtures.web.review_doc (a PDF made with PyMuPDF and the
result the pipeline would make of it) and the real validators, assembler and page
renderer. Decisions: 1 the bold omega read plain (equation p1-e2), 2 the figure p1-e6,
3 the "Teacher Support" heading group; one optional note (equation p1-e5).

The page image routes are in test_web_page_images.py; the list view (`/review/list`) in
test_web_review*.py; axe, reflow and keyboard in test_web_review_split_browser.py.
"""

from __future__ import annotations

import re
from html import unescape

import pytest

from a11y_stem.core.types import DocumentResult
from a11y_stem.web import page_images
from a11y_stem.web.db import Job, session_scope
from a11y_stem.web.routes.split import headline
from fixtures.web.review_doc import GROUP, review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload


@pytest.fixture
def split(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = review_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def screen(env, query: str = "", status: int = 200) -> str:
    response = env.browser.get(f"/jobs/{env.job_id}/review{query}")
    assert response.status_code == status, response.status_code
    return response.text


def post(env, target: str, **fields):
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review")} | fields
    return env.browser.post(f"/jobs/{env.job_id}/review/{target}", data=data)


def text_of(html: str) -> str:
    """Visible-ish text: tags removed, whitespace collapsed."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def section(html: str, start: str, end: str) -> str:
    begin = html.index(start)
    return html[begin : html.index(end, begin)]


def panel(html: str) -> str:
    return section(html, 'id="decision"', 'class="optional-area"')


def boxes(html: str) -> dict[str, str]:
    """element id -> the whole mark link."""
    return {
        m.group(1): m.group(0)
        for m in re.finditer(r'<a id="box-([\w-]+)" class="box[^"]*"[^>]*>.*?</a>', html, re.S)
    }


def stored(env) -> DocumentResult:
    return DocumentResult.model_validate(env.job(env.job_id).result_json)


# --------------------------------------------------------------------------- copy


def test_one_short_sentence_counts_decisions_not_fixes(split):
    page = screen(split)
    lede = section(page, '<p class="review-lede">', "</p>")
    # Three decisions: the heading group of three headings counts once.
    assert text_of(lede) == (
        "3 things need your decision. Everything else was accepted; optional checks are below."
    )
    # No instruction paragraph: the buttons and the one sentence say it.
    for wordy in ("For each fix", "keeps the proposal as it is", "Only what could change"):
        assert wordy not in page
    assert "<title>Decision 1 of 3: Review review.pdf" not in page  # the upload's own name
    assert re.search(r"<title>Decision 1 of 3: Review [^<]+ \| a11y-stem</title>", page)


REST = "Everything else was accepted; optional checks are below."


@pytest.mark.parametrize(
    ("left", "total", "optional", "sentence"),
    [
        (3, 3, 2, "3 things need your decision. " + REST),
        (1, 1, 0, "1 thing needs your decision. Everything else was accepted."),
        (2, 3, 1, "2 of 3 decisions left. " + REST),
        (0, 3, 1, "All decisions made. " + REST),
        (0, 0, 0, "Nothing needs your decision. Everything was accepted."),
    ],
)
def test_headline(left, total, optional, sentence):
    assert headline(left, total, optional) == sentence


# --------------------------------------------------------------------------- decision 1


def test_the_first_decision_is_the_equation_with_a_same_size_compare(split):
    page = screen(split)
    body = panel(page)
    assert "Decision 1 of 3" in body
    assert "Equation 1, page 1, in a sentence" in text_of(body)
    assert "Needs your decision" in body
    # What to look at, in one sentence, from the step's own text-layer rules.
    assert "The ω is bold in the PDF (a vector) but plain in the proposal." in body
    # The crop with the words around it, and the proposal under it at the same scale.
    assert f'src="/jobs/{split.job_id}/regions/p1-e2.png"' in body
    assert "with the words around it, the symbol to check outlined." in body
    assert 'id="cmp-p1-e2"' in body and 'class="cmp-math"' in body
    assert '<mi class="flagged">ω</mi>' in body  # outlined in the proposal too
    table = section(body, '<table class="diff-table">', "</table>")
    assert "<caption>What differs</caption>" in table
    row = text_of(section(table, "<tbody>", "</tbody>"))
    assert row == "𝛚 style bold plain Yes"
    # Accept first, Reject beside it; editing is folded until wanted.
    assert re.search(r'name="action" value="accept" class="button" data-role="accept">Accept', body)
    assert '<details class="edit-box">' in body and "Edit the LaTeX" in body
    assert 'name="view" value="split"' in body


def test_marks_on_the_page_are_links_with_number_shape_and_text(split):
    page = screen(split)
    marks = boxes(page)
    # Decisions: numbered, solid; the selected one is current. A group's headings carry
    # the group's number. The optional note is quieter; accepted items are hidden.
    assert set(marks) == {"p1-e2", "p1-e3", "p1-e5", "p1-e6", "p1-e7"}
    assert 'class="box box-required is-current"' in marks["p1-e2"]
    assert 'aria-current="true"' in marks["p1-e2"]
    assert "1: Equation 1, needs your decision (1 of 3)" in marks["p1-e2"]
    assert "2: Figure 1, needs your decision (2 of 3)" in marks["p1-e6"]
    assert (
        "3: Heading 2, “Teacher Support”, repeated heading, needs your decision (3 of 3)"
        in (marks["p1-e3"])
    )
    assert 'class="box box-optional"' in marks["p1-e5"]
    assert "Note: Equation 2, optional check" in marks["p1-e5"]
    for eid, mark in marks.items():
        assert re.search(r'href="/jobs/[0-9a-f]{32}/review\?item=', mark), eid
    assert 'aria-label="Page 1 image with 5 marks"' in page
    assert "Show accepted (1)" in page
    shown = boxes(screen(split, "?accepted=1"))
    assert "p1-e0" in shown and "Heading 1, accepted automatically" in shown["p1-e0"]
    assert "Hide accepted" in screen(split, "?accepted=1")


def test_the_page_list_counts_each_decision_once(split):
    page = screen(split)
    pages = section(page, '<nav class="page-list"', "</nav>")
    chips = re.findall(r'<a href="([^"]+)" class="page-chip[^"]*"([^>]*)>(.*?)</a>', pages, re.S)
    assert len(chips) == 2
    first, second = (text_of(label) for _, _, label in chips)
    # The group's first heading waiting is on page 1, so page 2 has nothing of its own.
    assert first == "Page 1 , 3 to decide"
    assert second == "Page 2"
    assert 'aria-current="page"' in chips[0][1]


def test_marks_stylesheet_places_marks_and_sizes_the_proposal(split):
    response = split.browser.get(f"/jobs/{split.job_id}/review/marks.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.headers["cache-control"] == "private, no-store"
    css = response.text
    assert re.search(r"#box-p1-e2\{left:[\d.]+%;top:[\d.]+%;width:[\d.]+%;height:[\d.]+%\}", css)
    assert re.search(r"#cmp-p1-e2 \.cmp-math\{font-size:[\d.]+cqw;margin-left:[\d.]+cqw\}", css)
    # Ids and numbers only.
    assert not re.search(r"Teacher|Vectors|ω|car", css)


# --------------------------------------------------------------------------- other decisions


def test_the_figure_decision_shows_the_image_and_the_alt_field(split):
    body = panel(screen(split, "?item=p1-e6"))
    assert "Decision 2 of 3" in body
    assert f'src="/jobs/{split.job_id}/crops/p1-e6.png"' in body
    assert "Caption in the PDF:</span> Figure 1 A car moves from home to school." in body
    assert re.search(r'<textarea id="alt-p1-e6"[^>]*>A car on a road between', body)
    assert 'id="decorative-p1-e6"' in body
    assert "Reject keeps the figure without a description" in body


@pytest.mark.parametrize("item", [GROUP, "p1-e7", "p2-e0"])
def test_a_heading_group_is_one_card_whoever_names_it(split, item):
    body = panel(screen(split, f"?item={item}"))
    assert "Decision 3 of 3" in body
    assert "“Teacher Support” appears 3 times as a bold line in the body text size." in body
    assert "Keep as headings at level 2" in body and "Make bold text" in body
    assert "(all 3 “Teacher Support”)" in body  # in the buttons' names
    assert f'action="/jobs/{split.job_id}/review/{GROUP}"' in body
    assert text_of(section(body, '<p class="group-pages">', "</p>")) == "On pages page 1 , page 2 ."
    # The heading in its band, outlined.
    assert f'src="/jobs/{split.job_id}/regions/p1-e3.png"' in body


def test_a_heading_group_on_another_page(split):
    page = screen(split, f"?item={GROUP}&page=2")
    assert "Page 2 <span" in page
    marks = boxes(page)
    assert "is-current" in marks["p2-e0"]


def test_optional_checks_are_folded_and_open_on_their_own(split):
    page = screen(split)
    optional = section(page, '<section class="optional-area"', "</section>")
    assert '<details class="optional-box">' in optional  # folded
    assert '<summary id="optional-summary">Optional checks (1)</summary>' in optional
    line = text_of(section(optional, '<ul class="optional-list">', "</ul>"))
    assert line == "Equation 2, page 1 A letter's style differs slightly; the meaning is the same."
    opened = screen(split, "?item=p1-e5")
    assert '<details class="optional-box" open>' in opened
    body = panel(opened)
    assert "Optional check" in body and "Accepted, with a note" in body
    assert "Back to decision 1" in body
    assert "which does not change the meaning" in body
    assert 'value="accept"' not in body  # already accepted; edit or reject only


# --------------------------------------------------------------------------- deciding


def test_a_decision_moves_on_and_focus_lands_on_the_next_decision(split):
    response = post(split, "p1-e2", action="accept")
    assert response.status_code == 303
    assert response.headers["location"] == f"/jobs/{split.job_id}/review?item=p1-e6"
    page = split.browser.get(response.headers["location"]).text
    heading = re.search(r'<h2 id="decision-title"[^>]*>', page)
    assert heading is not None
    assert "autofocus" in heading.group(0)
    assert 'aria-describedby="decision-notice"' in heading.group(0)
    notice = section(page, 'id="decision-notice"', "</p>")
    assert 'role="status"' in notice
    assert "Equation 1: Accepted. 2 decisions left." in notice
    assert "2 of 3 decisions left." in page
    assert "1 of 3 decided" in page
    # A plain visit does not move focus.
    again = screen(split, "?item=p1-e6")
    assert "autofocus" not in re.search(r'<h2 id="decision-title"[^>]*>', again).group(0)


def test_every_decision_then_done(split):
    assert post(split, "p1-e2", action="edit", latex=r"\boldsymbol{\omega}").status_code == 303
    response = post(split, "p1-e6", action="accept")
    assert response.headers["location"] == f"/jobs/{split.job_id}/review?item={GROUP}"
    response = post(split, GROUP, action="reject")
    assert response.headers["location"] == f"/jobs/{split.job_id}/review"
    page = split.browser.get(response.headers["location"]).text
    body = panel(page)
    assert "All decisions made" in body
    assert f'href="/jobs/{split.job_id}/download">Download the accessible HTML</a>' in body
    assert "Repeated heading “Teacher Support”: Made 3 headings bold text instead of headings." in (
        text_of(body)
    )
    assert "All decisions made. Everything else was accepted" in page
    assert page.count("step step-done") == 3
    result = stored(split)
    assert {f.element_id: f.status.value for f in result.fixes if f.reviewed} == {
        "p1-e2": "edited", "p1-e6": "accepted", "p1-e3": "rejected", "p1-e7": "rejected",
        "p2-e0": "rejected",
    }  # fmt: skip
    # A decided decision can still be opened, and says so.
    decided = panel(screen(split, "?item=p1-e2"))
    assert "Decided: Edited by you" in decided
    group = panel(screen(split, f"?item={GROUP}"))
    assert "Decided: Rejected: original kept" in group
    assert "Every heading of this group has been decided." in group
    assert "Make bold text" not in group


def test_a_refused_edit_comes_back_on_the_review_screen(split):
    response = post(split, "p1-e2", action="edit", latex=r"\notacommand{x}")
    assert response.status_code == 422
    page = response.text
    assert "<title>Error: Decision 1 of 3" in page
    assert '<a href="#latex-p1-e2">' in page  # the error summary
    body = panel(page)
    assert '<details class="edit-box" open>' in body  # so the link reaches the field
    assert re.search(r'<textarea id="latex-p1-e2"[^>]*aria-invalid="true"[^>]*>', body)
    assert r"\notacommand{x}</textarea>" in body
    assert 'id="latex-error-p1-e2"' in body
    assert stored(split).fix_for("p1-e2").status.value == "needs_review"  # nothing saved


def test_a_refused_group_level_comes_back_on_the_card(split):
    response = post(split, GROUP, action="edit", level="6")
    assert response.status_code == 422
    body = panel(response.text)
    assert "Decision 3 of 3" in body
    assert f'id="group-error-{GROUP}"' in body
    assert "Choose another level" in body and '<details class="edit-box" open>' in body


def test_the_list_view_is_still_one_link_away(split):
    page = screen(split)
    assert f'<a href="/jobs/{split.job_id}/review/list">All items as a list</a>' in page
    listing = split.browser.get(f"/jobs/{split.job_id}/review/list").text
    assert "<h1>All items as a list</h1>" in listing
    assert len(re.findall(r'<article id="fix-', listing)) == 8
    assert listing.count('name="view" value="list"') >= 5
    assert f'<a href="/jobs/{split.job_id}/review">Back to the review screen</a>' in listing


def test_unknown_items_fall_back_to_the_first_decision(split):
    for query in ("?item=nope", "?item=p1-e1", "?item=%3Cscript%3E", "?page=99", "?page=x"):
        assert "Decision 1 of 3" in panel(screen(split, query)), query


# --------------------------------------------------------------------------- contrast


def _contrast(a: str, b: str) -> float:
    def luminance(hex_colour: str) -> float:
        channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_the_review_colours_have_enough_contrast():
    from pathlib import Path

    css = (Path(__file__).parents[1] / "src/a11y_stem/web/static/app.css").read_text()
    colours = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", css))
    white = "#ffffff"
    # Marks are graphics on the white page image: 3:1 (WCAG 1.4.11).
    for mark in ("--mark-required", "--mark-current", "--mark-optional", "--mark-accepted"):
        assert _contrast(colours[mark], white) >= 3, mark
    # The numbers on the discs and the page counts are white text: 4.5:1.
    for disc in ("--mark-required", "--mark-current", "--ok"):
        assert _contrast(white, colours[disc]) >= 4.5, disc
    # Text on the review screen's tinted backgrounds.
    pairs = [
        ("--mark-required", "--tint-required"),
        ("--text", "--desk"),
        ("--muted", "--desk"),
        ("--link", "--desk"),
        ("--mark-optional", "--panel"),
    ]
    for text, background in pairs:
        assert _contrast(colours[text], colours[background]) >= 4.5, (text, background)


# --------------------------------------------------------------------------- older results


def _make_old(env) -> None:
    """Store the result as it was before tiers and page sizes existed."""
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, env.job_id)
        assert job is not None and job.result_json is not None
        data = dict(job.result_json)
        data.pop("page_sizes", None)
        data["fixes"] = [
            {k: v for k, v in fix.items() if k not in {"review", "group_key", "group_size"}}
            for fix in data["fixes"]
        ]
        job.result_json = data


def test_results_stored_before_page_sizes_and_tiers_still_load(split):
    _make_old(split)
    page = screen(split)
    # Without group keys each waiting heading is its own decision again.
    assert "5 things need your decision." in page
    # Page sizes come from the PDF itself, so the marks are still placed.
    assert "The marks could not be placed" not in page
    css = split.browser.get(f"/jobs/{split.job_id}/review/marks.css").text
    assert "#box-p1-e2{left:" in css


def test_without_page_sizes_a_failed_probe_leaves_the_decisions_usable(split, monkeypatch):
    _make_old(split)

    def broken(*args, **kwargs):
        raise page_images.RenderFailed()

    monkeypatch.setattr(page_images, "_child", broken)
    page = screen(split)
    assert "Decision 1 of 3" not in page  # five decisions now (no tiers)
    assert "Decision 1 of 5" in panel(page)
    assert "The marks could not be placed on this page." in page
    assert boxes(page) == {}
    css = split.browser.get(f"/jobs/{split.job_id}/review/marks.css").text
    assert "#box-" not in css
