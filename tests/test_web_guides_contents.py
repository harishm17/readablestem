"""The long public pages share one "On this page" contents (templates/_contents.html) whose
links reach stable heading ids, and the comparison guide has a stacked view of its table for
phones that carries every fact, source link and date the table does."""

from __future__ import annotations

import re

import pytest

from fixtures.web.support import WebEnv
from readablestem.web import offices, seo

LONG_PAGES = (
    seo.ADA_GUIDE,
    seo.COMPARE_GUIDE,
    "/help/posting",
    offices.PAGE_PATH,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(seo, "SHOWCASE_DIR", tmp_path / "no-showcase")
    return WebEnv(tmp_path)


def fetch(env, path: str) -> str:
    with env.running() as client:
        response = client.get(path)
    assert response.status_code == 200
    return response.text


@pytest.mark.parametrize("path", LONG_PAGES)
def test_a_long_page_has_a_contents_whose_links_all_land(env, path):
    html = fetch(env, path)
    html = html[html.index("<main") : html.index("</main>")]
    nav = re.search(r'<nav class="toc" aria-label="On this page">(.*?)</nav>', html, re.S)
    assert nav, path
    targets = re.findall(r'<a href="#([^"]+)">', nav.group(1))
    assert len(targets) >= 6
    ids = re.findall(r'\sid="([^"]+)"', html)
    assert len(ids) == len(set(ids)), "an id is used twice"
    assert set(targets) <= set(ids)
    # The contents lists every section: each h2 is in it, in the page's order.
    h2_ids = re.findall(r'<h2 id="([^"]+)"', html)
    assert targets == [t for t in h2_ids if t in targets]
    assert all('<h2 id="' in h for h in re.findall(r"<h2[^>]*>", html)), "an h2 has no id"
    # The introduction still comes first: the contents is after the first paragraph.
    assert html.index("<p", html.index("<h1")) < html.index('aria-label="On this page"')


def view(html: str, start: str, end: str) -> str:
    return html[html.index(start) : html.index(end)]


def test_the_comparison_stack_carries_every_fact_source_and_date_of_the_table(env):
    html = fetch(env, seo.COMPARE_GUIDE)
    table = view(html, '<div class="table-scroll compare-wide-view"', '<div class="compare-stack">')
    stack = view(html, '<div class="compare-stack">', '<h2 id="readablestem">')
    assert "<table" in table and "<table" not in stack
    cells = re.findall(r"<td>(.*?)</td>", table, re.S)
    entries = re.findall(r"<dd>(.*?)</dd>", stack, re.S)
    assert len(cells) == len(entries) == 45  # nine topics, five tools
    norm = lambda parts: [" ".join(p.split()) for p in parts]  # noqa: E731
    assert norm(entries) == norm(cells)  # the very same cells, so every link and date is kept
    for part in (table, stack):
        assert part.count("<time ") == len(re.findall(r"<time ", table))
    assert len(re.findall(r"<time ", stack)) >= 30
    # Each topic is a heading, and each line names its tool.
    assert len(re.findall(r"<h3 ", stack)) == 9
    assert re.findall(r"<dt>(.*?)</dt>", stack)[:5] == [
        "ReadableSTEM",
        "Mathpix",
        "Aelira",
        "Chatbots",
        "LaTeXML",
    ]


def test_the_stack_is_hidden_on_wide_screens_and_the_table_on_phones():
    from pathlib import Path

    import readablestem

    css = (Path(readablestem.__file__).parent / "web" / "static" / "app.css").read_text()
    assert ".compare-stack { display: none; }" in css
    phone = css[css.index("ux-lane-c: comparison guide") :]
    phone = phone[phone.index("@media (max-width: 51.99rem)") :]
    assert ".compare-wide-view { display: none; }" in phone
    assert ".compare-stack { display: block; }" in phone
