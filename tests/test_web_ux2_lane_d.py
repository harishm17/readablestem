"""UX round 2, lane D: the public pages and the shared chrome.

What these pin: the offices page, its one-page summary and the privacy notice are written from
one list of data (so they cannot drift); every scrollable table region can be reached by
keyboard; the short summaries that lead the long pages; the comparison guide's decision list and
its LaTeX-source wording; sign-in consent; the short footers; and the style page's labels.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from fixtures.web.support import WebEnv
from readablestem.web import offices, seo
from readablestem.web.recipients import recipients

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "src/readablestem/web/templates"
STATIC = ROOT / "src/readablestem/web/static"
ALL_ON = {
    "feature_tables": True,
    "feature_image_pages": True,
    "feature_notation_memory": True,
    "math_reader_openai_enabled": True,
    "model_provider": "anthropic",
    "analytics_enabled": True,
}


class Plain(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in {"style", "script"}

    def handle_endtag(self, tag):
        self.skip -= tag in {"style", "script"}

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def text_of(html: str) -> str:
    parser = Plain()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


def get(tmp_path, path: str, **settings) -> str:
    env = WebEnv(tmp_path, **settings)
    with env.running() as client:
        response = client.get(path)
    assert response.status_code == 200, path
    return response.text


# ------------------------------------------------------------- one list of data


def test_the_matrix_the_company_table_and_the_summary_are_written_from_one_list(tmp_path):
    env = WebEnv(tmp_path, **ALL_ON)
    context = offices.offices_context(env.settings)
    items = context["data_items"]
    labels = [item.label for item in items]
    # The kinds of data the main page describes in prose are all rows, and so are the opted-in
    # full-page images and the course notation the summary used to leave out.
    joined = " ".join(labels)
    assert "tables" in joined and "scanned or handwritten page" in joined
    assert "course's notation" in joined and "close-up of a handwritten expression" in joined
    with env.running() as client:
        page = client.get(offices.PAGE_PATH).text
        summary = client.get(offices.SUMMARY_PATH).text
    page_text, summary_text = text_of(page), text_of(summary)
    for label in labels:
        assert label in page_text, label
        assert label in summary_text, label
    # Every company's cell on the page lists what the same list says it receives.
    table = page.split('class="recipients-table"', 1)[1].split("</table>", 1)[0]
    for r in recipients(env.settings):
        if "hosting" in r.purposes:
            continue
        for label in offices.received_by(items, r.name):
            assert label in text_of(table), (r.name, label)


def test_the_model_companies_keeping_times_match_the_privacy_notices_wording():
    macro = (TEMPLATES / "_recipients.html").read_text(encoding="utf-8")
    assert "within\n    30 days" in macro or "within 30 days" in " ".join(macro.split())
    assert "30 days" in offices.COMPANY_KEEPS["Anthropic"]
    assert "API data policies" in offices.COMPANY_KEEPS["OpenAI"]


# ----------------------------------------------------- scrollable regions by keyboard


# Tables with a fixed layout that stack into blocks below 600px (app.css, `.doc-table`): they
# never overflow, so their box is not a scrollable region and takes no tab stop.
STACKED = {"_documents.html", "_batch_rows.html"}


def test_every_scrolling_table_box_in_the_templates_is_focusable_and_named():
    """axe `scrollable-region-focusable`: a `.table-scroll` needs tabindex="0" and a group or
    region role with a name, wherever it is written (templates, the showcase site)."""
    bad = []
    for root in (TEMPLATES, ROOT / "site/templates"):
        for path in sorted(root.glob("*.html")):
            if path.name in STACKED:
                continue
            for tag in re.findall(
                r'<div[^>]*class="[^"]*\btable-scroll\b[^"]*"[^>]*>',
                path.read_text(encoding="utf-8"),
            ):
                named = "aria-label=" in tag or "aria-labelledby=" in tag
                if not (
                    'tabindex="0"' in tag and re.search(r'role="(group|region)"', tag) and named
                ):
                    bad.append((path.name, tag))
    macro = (TEMPLATES / "_table_scroll.html").read_text(encoding="utf-8")
    assert 'tabindex="0"' in macro and 'role="group"' in macro and "aria-label" in macro
    assert not bad, bad


# -------------------------------------------------------- long pages open with a summary


# ------------------------------------------------------- the comparison guide


def test_every_page_that_lists_inputs_says_the_same_about_latex_source(tmp_path):
    """The landing page, limits, the guides, offices and privacy all name LaTeX source when the
    feature is on and not when it is off (one flag: routes/pages.py `_features`)."""
    on = WebEnv(tmp_path / "on", feature_latex_source=True)
    off = WebEnv(tmp_path / "off")
    paths = {
        "/": "LaTeX source files (.tex)",
        "/limits": "A LaTeX file (.tex) or a zip of a LaTeX project is also taken",
        seo.COMPARE_GUIDE: "a zip of a LaTeX project",
        seo.ADA_GUIDE: "It also reads LaTeX source files",
        offices.PAGE_PATH: "a zip of a LaTeX project",
        "/privacy": "for LaTeX source, the .tex file",
    }
    with on.running() as client_on, off.running() as client_off:
        for path, phrase in paths.items():
            assert phrase in text_of(client_on.get(path).text), path
            assert phrase not in text_of(client_off.get(path).text), path


# ------------------------------------------------------------------- landing


# ----------------------------------------------------------- sign-in and chrome


# ------------------------------------------------------------------ style page


# ----------------------------------------------------------------- demo note


def test_the_demo_note_is_short_and_keeps_the_licence_and_the_teacher_support_note():
    demo = (STATIC / "demo/index.html").read_text(encoding="utf-8")
    note = demo.split('<aside aria-label="About this demo"', 1)[1].split("</aside>", 1)[0]
    visible = note.split("<details>", 1)[0]
    assert len(text_of(visible).split()) < 75
    assert "CC BY 4.0" in visible  # the attribution stays visible
    assert "<summary>Source, licence and notes</summary>" in note
    assert "Teacher Support" in note.split("<details>", 1)[1]
    assert note.count("Teacher Support") == 1  # explained once
    assert "AGPL" not in note


# ------------------------------------------------------------- the comparison chart


@pytest.mark.parametrize("path", [seo.COMPARE_GUIDE, offices.PAGE_PATH])
def test_the_chart_is_one_shared_component_with_the_numbers_in_a_real_table(tmp_path, path):
    page = get(tmp_path, path)
    assert page.count('class="proof-chart"') == 1  # the patterns are defined once per page
    chart = page.split('<figure class="proof-chart"', 1)[1].split("</figure>", 1)[0]
    # The bars are for the eye; the same numbers are a table a screen reader always reads.
    assert '<div class="chart" aria-hidden="true">' in chart
    hidden = chart.split('<div class="visually-hidden">', 1)[1].split("</table>", 1)[0]
    assert "<caption>" in hidden and hidden.count('<th scope="row">') == 6
    assert "0.840" in hidden and "$0.068" in hidden and "never asks" in hidden
    # Every bar has its system's name and number beside it, and no style attribute (the CSP).
    assert chart.count('class="chart-name"') == chart.count('class="chart-value"') == 18
    assert " style=" not in chart
    assert "ReadableSTEM and the Opus chatbot are" in " ".join(text_of(chart).split())
    assert "level" in text_of(chart) and "beat" not in text_of(chart).lower()
    assert "26 documents the tool had never seen" in text_of(chart)
