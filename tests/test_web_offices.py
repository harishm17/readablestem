"""The page for accessibility offices and its one-page summary.

The facts on both pages are read from the settings through the list the privacy page uses
(`web.recipients`), so these tests change the settings and look at what the pages say. They
also pin what the pages must not say (a conformance claim) and the wording rules of the
trust work: no "individual developer" framing anywhere a visitor reads.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import pytest

from fixtures.web.support import WebEnv
from readablestem.web import offices, seo
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


def get(tmp_path, path=PATH, **settings):
    env = WebEnv(tmp_path, **settings)
    with env.running() as client:
        response = client.get(path)
    assert response.status_code == 200
    return response


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    return get(tmp_path_factory.mktemp("offices")).text


def test_the_page_has_one_h1_a_text_equivalent_for_the_diagram_and_the_asked_for_sections(page):
    assert page.count("<h1>") == 1 and page.count("<main") == 1
    for heading in (
        "Where a document goes",
        "Who receives what",
        "How long it is kept",
        "No training, and what is stored by the model company",
        "Student records (FERPA)",
        "What the tool does not certify",
        "Pilot checklist",
    ):
        assert f">{heading}</h2>" in page, heading
    # The diagram is an image with a title and a description, and the steps are also a list.
    svg = page.split("<svg", 1)[1].split("</svg>", 1)[0]
    assert 'role="img"' in svg and "<title" in svg and "<desc" in svg
    assert "The same steps are listed below." in page
    checklist = page.split('id="pilot"', 1)[1]
    assert checklist.count("<li>") >= 8
    assert 'class="data-flow"' in page and "style=" not in svg  # the CSP allows no style attribute


def test_the_page_names_the_companies_and_the_numbers_the_settings_give(tmp_path):
    settings_page = get(
        tmp_path, model_provider="anthropic", retention_hours=36, opt_in_retention_days=45
    )
    text = text_of(settings_page.text)
    assert "<strong>36 hours</strong>" in " ".join(settings_page.text.split())
    assert "45 days" in text
    table = settings_page.text.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert "Anthropic" in table and "OpenAI" not in table
    assert "sends its model requests to Anthropic" in text
    assert "Railway" in text and "Langfuse" not in text
    assert "PostHog" not in text
    assert "The store setting belongs to the OpenAI path only" in text


def test_the_openai_path_says_store_is_off(tmp_path):
    text = text_of(get(tmp_path, model_provider="openai").text)
    assert "OpenAI" in text and "Anthropic" not in text
    assert "sent with the store setting off" in text


def test_the_table_has_one_row_per_recipient_in_the_privacy_pages_order(tmp_path):
    env = WebEnv(tmp_path, model_provider="anthropic", math_reader_openai_enabled=True)
    with env.running() as client:
        html = client.get(PATH).text
        privacy = client.get("/privacy").text
    found = recipients(env.settings)
    table = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert re.findall(r'<th scope="row">([^<]+)</th>', table) == [r.name for r in found]
    assert "Anthropic and OpenAI" in text_of(html)
    for r in found:
        assert r.name in privacy


def test_tracing_and_analytics_appear_only_when_they_are_on(tmp_path):
    on = offices.offices_context(
        WebEnv(tmp_path / "a", model_provider="anthropic").settings.model_copy(
            update={"tracing_enabled": True}
        ),
        tracer_on=True,
    )
    assert [r.name for r in on["recipients"]][-1] == "Langfuse"
    off = offices.offices_context(WebEnv(tmp_path / "b", model_provider="anthropic").settings)
    assert "Langfuse" not in [r.name for r in off["recipients"]]
    assert on["model_names"] == off["model_names"] == "Anthropic"
    shown = text_of(get(tmp_path / "c", analytics_enabled=True).text)
    assert "PostHog" in shown and "nobody is identified" in shown


def test_the_page_describes_features_only_when_they_are_on(tmp_path):
    plain = text_of(get(tmp_path / "a").text)
    assert "Convert scanned or handwritten pages" not in plain
    assert "tables, with" not in plain
    full = text_of(get(tmp_path / "b", feature_image_pages=True, feature_tables=True).text)
    assert "Convert scanned or handwritten pages" in full
    assert "equations and figures and tables" in full


def test_the_page_makes_no_conformance_claim_and_says_what_it_does_not_do(page):
    text = text_of(page).lower()
    for claim in ("is compliant", "are compliant", "fully accessible", "wcag-conformant"):
        assert claim not in text
    assert "does not say that a document meets wcag" in text
    assert "no accessibility conformance report (vpat)" in text
    assert "no data agreement is offered" in text
    assert "student records" in text and "never sign in" in text
    assert "not legal advice" in text


def test_the_footer_links_the_page_and_the_sitemap_and_llms_txt_list_it(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        home = client.get("/").text
        sitemap = ET.fromstring(client.get("/sitemap.xml").content)
        llms = client.get("/llms.txt").text
        robots_meta = client.get(PATH).text
    assert f'href="{PATH}">For accessibility offices</a>' in home
    locs = [e.text for e in sitemap.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    assert any(loc.endswith(PATH) for loc in locs)
    assert f"{PATH})" in llms
    assert PATH in {p.path for p in seo.PUBLIC_PAGES}
    assert 'content="noindex"' not in robots_meta  # a public page is offered to search engines


def test_the_summary_is_a_self_contained_download_with_the_same_facts(tmp_path):
    response = get(tmp_path, SUMMARY, model_provider="anthropic", retention_hours=36)
    assert "attachment" in response.headers["content-disposition"]
    assert offices.SUMMARY_FILENAME in response.headers["content-disposition"]
    assert "sandbox" in response.headers["content-security-policy"]
    html = response.text
    assert "<script" not in html and "<link" not in html and " src=" not in html
    assert '<html lang="en">' in html and html.count("<h1>") == 1
    assert 'content="noindex"' in html
    text = text_of(html)
    assert "Deleted 36 hours after" in text
    assert "Anthropic" in text and "Railway" in text
    assert "does not say that a document meets WCAG" in text
    assert "No school-official or data processing agreement" in text
    assert "Built and maintained by Harish Manoharan" in text
    assert "help@example.org" in text


def test_the_summary_is_not_a_page_search_engines_index(tmp_path):
    assert SUMMARY not in {p.path for p in seo.PUBLIC_PAGES}


# --------------------------------------------------------------------------- wording rules

FRAMING = ("an individual developer", "not a company or a university", "independent project")


def read_text_files() -> dict[str, str]:
    """Everything a visitor or a reviewer reads: templates, the site's pages, the site
    copy in seo.py, the README, CHANGELOG-free docs and ADRs."""
    roots = [
        ROOT / "src/readablestem/web/templates",
        ROOT / "site/templates",
        ROOT / "docs",
    ]
    files = [ROOT / "README.md", ROOT / "src/readablestem/web/seo.py"]
    for root in roots:
        files += [p for p in root.rglob("*") if p.suffix in {".html", ".md"}]
    return {str(p.relative_to(ROOT)): p.read_text(encoding="utf-8") for p in files if p.is_file()}


def test_no_page_or_doc_uses_the_old_operator_framing():
    for name, body in read_text_files().items():
        for phrase in FRAMING:
            assert phrase not in body.lower(), (name, phrase)


def test_the_about_line_and_the_licence_wording_are_where_the_decision_put_them(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        landing = text_of(client.get("/").text)
        terms = text_of(client.get("/terms").text)
        privacy = text_of(client.get("/privacy").text)
    assert "Built and maintained by Harish Manoharan. The code is open source" in landing
    assert "built and maintained by Harish Manoharan" in terms
    assert "built and maintained by Harish Manoharan" in privacy
    for name, body in {"landing": landing, "privacy": privacy}.items():
        assert "AGPL" not in body and "PyMuPDF" not in body, name
    assert "AGPL-3.0" in terms
