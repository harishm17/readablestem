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
from collections import Counter
from html import unescape

import pytest
from sqlalchemy import select

from fixtures.web.review_doc import GROUP, review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload
from readablestem.core.types import DocumentResult
from readablestem.web import page_images
from readablestem.web.db import Job, ReviewEdit, session_scope


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


# --------------------------------------------------------------------------- decision 1


# --------------------------------------------------------------------------- other decisions


def test_a_style_the_pdf_text_cannot_show_is_not_called_a_difference(tmp_path):
    """The LaTeX handout's R^n: a double-struck R from an MSBM font is a plain "R" in the
    text layer. Neither the optional list nor the card may say the PDF has a plain R."""
    from readablestem.core.types import EquationFix
    from readablestem.pipeline.validate import safe_latex_to_mathml

    output = review_output()
    reals = r"\mathbb{R}^{n}"
    for element in output.result.elements:
        if element.id == "p1-e5":
            element.text, element.regular_letters = "Rn", "n"
    fix = output.result.fix_for("p1-e5")
    assert fix is not None
    fix.equation = EquationFix(latex=reals, mathml=safe_latex_to_mathml(reals, "block"), speech="")
    env = WebEnv(tmp_path)
    env.converter.output = output
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        page = screen(env)
        optional = text_of(section(page, '<ul class="optional-list">', "</ul>"))
        assert optional == (
            "Equation 2, page 1 \u211d n The proposal has a double-struck \u211d, a style the "
            "PDF's text cannot show."
        )
        body = panel(screen(env, "?item=p1-e5"))
        assert (
            "The proposal has a double-struck \u211d. The PDF's text cannot show that style, so "
            "check it in the image." in text_of(body)
        )
        assert "What differs" not in body and "plain" not in text_of(body)


# --------------------------------------------------------------------------- deciding


def test_a_decision_sent_twice_is_saved_once(split):
    """The one submit rule (SW43): the same request token again gets the first answer and
    saves nothing; a new token for the decision the item has now gets "Already saved"; a
    form without a token is checked as before."""
    from readablestem.web.db import RequestToken

    page = panel(screen(split, "?item=p1-e2"))
    sent = re.search(r'name="request_token" value="([^"]+)"', page)
    assert sent, "the decision form carries a request token"
    first = post(split, "p1-e2", action="accept", version="0", request_token=sent.group(1))
    again = post(split, "p1-e2", action="accept", version="0", request_token=sent.group(1))
    assert first.status_code == again.status_code == 303
    assert again.headers["location"] == first.headers["location"]
    job = split.job(split.job_id)
    assert job.result_version == 1
    with session_scope(split.ctx.factory) as session:
        assert len(session.scalars(select(ReviewEdit)).all()) == 1
        (row,) = session.scalars(select(RequestToken)).all()
        assert row.route == "review.decide"
        assert set(row.outcome) == {"status", "location", "notices"}  # no content
    # No token, drawn at the old version: the item has that decision, by this reviewer.
    fresh = post(split, "p1-e2", action="accept", version="0")
    assert fresh.status_code == 303
    assert "Already saved" in text_of(split.browser.get(fresh.headers["location"]).text)
    # No token, another decision from the old page: refused as before.
    assert post(split, "p1-e2", action="reject", version="0").status_code == 409
    assert split.job(split.job_id).result_version == 1


def test_fix_something_on_a_page_opens_that_pages_tools_first_with_no_decision(split):
    """WS19.6: the job page offers the way in; page mode puts the page's tools first under
    "Page N", shows no decision, lists that page's optional checks, and keeps the mode when
    the reviewer moves to another page."""
    job_page = split.browser.get(f"/jobs/{split.job_id}").text
    assert "/review?mode=page&amp;page=1" in job_page and "Fix something on a page" in job_page
    html = screen(split, "?mode=page&page=2")
    shown = panel(html)
    before_tools = shown.split('id="page-tools"')[0]
    heading = re.search(r'id="decision-title"[^>]*>([^<]*)<', shown)
    assert heading and heading.group(1) == "Page 2"
    assert "Decision" not in text_of(before_tools)  # nothing of a decision above the tools
    assert "Decision 1 of" not in text_of(html) and 'class="steps"' not in html
    assert "No optional checks." in html  # the one note is on page 1
    assert "?page=1&amp;mode=page#page-view" in html  # the page chips keep the mode
    # An address that names an item is the ordinary screen.
    assert "Decision 1 of" in text_of(screen(split, "?mode=page&page=2&item=p1-e2"))


def test_the_live_check_answers_a_signed_out_session_quietly(split):
    """F-088: signed out in another tab, the session's CSRF token is gone with it; the
    check answers 204 and an empty body, not a 403 dropped into the page."""
    path = f"/jobs/{split.job_id}/review/p1-e2/check"
    csrf = token(split.browser, f"/jobs/{split.job_id}/review")
    assert split.browser.post(path, data={"csrf_token": csrf, "latex": "x"}).status_code == 200
    forged = split.browser.post(path, data={"csrf_token": "forged", "latex": "x"})
    assert forged.status_code == 403
    split.browser.post("/logout", data={"csrf_token": csrf})
    gone = split.browser.post(path, data={"csrf_token": csrf, "latex": "x"})
    assert (gone.status_code, gone.text) == (204, "")


def test_a_decided_group_can_be_decided_again_for_all_its_headings(split):
    assert post(split, GROUP, action="reject").status_code == 303
    assert {f.status.value for f in stored(split).fixes if f.group_key == GROUP} == {"rejected"}
    # Without scope=all a group decision only acts on headings still waiting: none.
    post(split, GROUP, action="accept")
    assert {f.status.value for f in stored(split).fixes if f.group_key == GROUP} == {"rejected"}
    response = post(split, GROUP, action="accept", scope="all")
    assert response.status_code == 303
    statuses = {f.element_id: f.status.value for f in stored(split).fixes if f.group_key == GROUP}
    assert statuses == {"p1-e3": "accepted", "p1-e7": "accepted", "p2-e0": "accepted"}
    page = split.browser.get(response.headers["location"]).text
    assert "Repeated heading “Teacher Support”: Changed 3 back to headings." in (text_of(page))
    group = panel(screen(split, f"?item={GROUP}"))
    assert "All 3 are kept as headings." in group
    assert ">Make bold text<" in group and "Keep as headings" not in group
    # And back again: every heading changes, one ReviewEdit each.
    assert post(split, GROUP, action="reject", scope="all").status_code == 303
    statuses = {f.element_id: f.status.value for f in stored(split).fixes if f.group_key == GROUP}
    assert set(statuses.values()) == {"rejected"}
    with session_scope(split.ctx.factory) as session:
        actions = Counter(session.scalars(select(ReviewEdit.action)).all())
    assert actions == {"reject": 6, "accept": 3}


@pytest.mark.parametrize(
    ("target", "fields", "message", "field"),
    [
        ("p1-e6", {"alt": "A red ball."}, "You changed the alt text.", "alt-p1-e6"),
        (
            "p1-e6",
            {"alt": "A car on a road between a house and a school.", "decorative": "on"},
            "You changed “Decorative”.",
            "decorative-p1-e6",
        ),
        ("p1-e3", {"level": "3"}, "You changed the level.", "level-p1-e3"),
    ],
)
def test_accept_refuses_a_changed_alt_text_decorative_box_or_level(
    split, target, fields, message, field
):
    response = post(split, target, action="accept", **fields)
    assert response.status_code == 422
    assert message in text_of(response.text)
    assert f'<a href="#{field}">' in response.text
    assert stored(split).fix_for(target).status.value == "needs_review"


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

    static = Path(__file__).parents[1] / "src/readablestem/web/static"
    css = (static / "tokens.css").read_text() + (static / "app.css").read_text()
    colours: dict[str, str] = {}
    for name, value in re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", css):
        colours.setdefault(name, value)  # the light theme is declared first
    white = "#ffffff"
    # Marks are graphics on the white page image: 3:1 (WCAG 1.4.11).
    for mark in ("--pmark-required", "--pmark-current", "--pmark-optional", "--pmark-accepted"):
        assert _contrast(colours[mark], white) >= 3, mark
    # The numbers on the discs and the page counts are white text: 4.5:1.
    for disc in ("--pmark-required", "--pmark-current", "--ok"):
        assert _contrast(white, colours[disc]) >= 4.5, disc
    # Text on the review screen's tinted backgrounds.
    pairs = [
        ("--pmark-required", "--tint-required"),
        ("--ink", "--desk"),
        ("--ink-muted", "--desk"),
        ("--accent", "--desk"),
        ("--mark-optional", "--surface"),
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
    assert "5 decisions to make." in page
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
    assert "No marks on this page." in page
    assert boxes(page) == {}
    css = split.browser.get(f"/jobs/{split.job_id}/review/marks.css").text
    assert "#box-" not in css


def test_a_long_difference_list_is_folded_and_the_latex_box_opens_tall(tmp_path):
    """An array the PDF's text reads column by column differs in many numbers (F-077): the
    first rows show, the rest are one fold away, and the box for the LaTeX has room."""
    from readablestem.core.types import EquationFix
    from readablestem.pipeline.validate import safe_latex_to_mathml

    output = review_output()
    latex = r"v = 63\ 25\ 17\ 81\ 49\ 34\ 56"
    for element in output.result.elements:
        if element.id == "p1-e5":
            element.text = "v = 36 52 71 18 94 43 65"
    fix = output.result.fix_for("p1-e5")
    assert fix is not None
    fix.equation = EquationFix(latex=latex, mathml=safe_latex_to_mathml(latex, "block"), speech="")
    env = WebEnv(tmp_path)
    env.converter.output = output
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        body = panel(screen(env, "?item=p1-e5"))
        rows = re.findall(r'<tr class="diff-', body)
        folded = re.findall(
            r'<tr class="diff-', section(body, '<details class="diff-more"', "</details>")
        )
        assert len(rows) > 5 and len(rows) - len(folded) <= 5
        box = re.search(r'<textarea id="latex-p1-e5"[^>]*rows="(\d+)"', body)
        assert box is not None and int(box.group(1)) >= 6
