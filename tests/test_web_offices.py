"""The page for accessibility offices and its one-page summary.

The facts on both pages are read from the settings through the list the privacy page uses
(`web.recipients`), so these tests change the settings and look at what the pages say. They
also pin what the pages must not say (a conformance claim) and the wording rules of the
trust work: no "individual developer" framing anywhere a visitor reads.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from fixtures.web.support import WebEnv
from readablestem.web.recipients import recipients

ROOT = Path(__file__).resolve().parent.parent
PATH = "/for-accessibility-offices"
SUMMARY = "/for-accessibility-offices/summary"


def text_of(html: str) -> str:
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

    parser = Plain()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


def recipients_table(html: str) -> str:
    """The by-company table (the first table on the page is the by-data one)."""
    return html.split('class="recipients-table"', 1)[1].split("</table>", 1)[0]


def get(tmp_path, path=PATH, **settings):
    env = WebEnv(tmp_path, **settings)
    with env.running() as client:
        response = client.get(path)
    assert response.status_code == 200
    return response


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    return get(tmp_path_factory.mktemp("offices")).text


def test_the_table_has_one_row_per_recipient_in_the_privacy_pages_order(tmp_path):
    env = WebEnv(tmp_path, model_provider="anthropic", math_reader_openai_enabled=True)
    with env.running() as client:
        html = client.get(PATH).text
        privacy = client.get("/privacy").text
    found = recipients(env.settings)
    table = recipients_table(html)
    assert re.findall(r'<th scope="row">([^<]+)</th>', table) == [r.name for r in found]
    assert "Anthropic and OpenAI" in text_of(html)
    for r in found:
        assert r.name in privacy


def test_the_page_makes_no_conformance_claim_and_says_what_it_does_not_do(page):
    text = text_of(page).lower()
    for claim in ("is compliant", "are compliant", "fully accessible", "wcag-conformant"):
        assert claim not in text
    assert "does not say that a document meets wcag" in text
    assert "no accessibility conformance report (vpat)" in text
    assert "no data agreement is offered" in text
    assert "student records" in text and "never sign in" in text
    assert "not legal advice" in text


# --------------------------------------------------------------------------- wording rules
