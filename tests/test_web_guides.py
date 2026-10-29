"""The guides under /guides/ (web/routes/pages.py, templates/guide_*.html): each one a
server-rendered page with a visible date, an Article in its structured data, a place in the
sitemap and llms.txt, a link from the footer, a Sources list, and no claim that
ReadableSTEM makes anything conform."""

from __future__ import annotations

import itertools
import json
import re
import xml.etree.ElementTree as ET

import pytest

from fixtures.web.support import WebEnv
from readablestem.web import seo

ADA = seo.ADA_GUIDE
COMPARE = seo.COMPARE_GUIDE
GUIDES = (ADA, COMPARE)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(seo, "SHOWCASE_DIR", tmp_path / "no-showcase")
    return WebEnv(tmp_path)


def flat(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def graph(html: str) -> list[dict]:
    (block,) = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    return json.loads(block)["@graph"]


@pytest.mark.parametrize("path", GUIDES)
def test_a_guide_is_a_public_server_rendered_page_with_its_own_metadata(env, path):
    site = env.settings.base_url.rstrip("/")
    entry = next(page for page in seo.PUBLIC_PAGES if page.path == path)
    assert entry.published and entry.updated >= entry.published
    with env.running() as client:
        response = client.get(path)
        html = response.text
        assert client.head(path).status_code == 200
        assert client.get(path + "/").headers["location"] == path
    assert response.status_code == 200
    assert 'name="robots"' not in html
    assert f'<link rel="canonical" href="{site}{path}">' in html
    assert f'<meta property="og:url" content="{site}{path}">' in html
    assert '<meta property="og:type" content="article">' in html
    assert f'<meta property="article:published_time" content="{entry.published}">' in html
    assert f'<meta property="article:modified_time" content="{entry.updated}">' in html
    # One h1 first, then h2s only (no skipped level), and a visible date.
    levels = [int(n) for n in re.findall(r"<h([1-6])[ >]", html)]
    assert levels[0] == 1 and levels.count(1) == 1
    assert all(b - a <= 1 for a, b in itertools.pairwise(levels))
    assert f'Last updated <time datetime="{entry.updated}">' in html
    assert "<h2>Sources</h2>" in html and "accessed on october 28, 2026" in html.lower()
    assert "https://harishmanoharan.com" in html


@pytest.mark.parametrize("path", GUIDES)
def test_a_guide_has_an_article_by_the_author_in_its_structured_data(env, path):
    entry = next(page for page in seo.PUBLIC_PAGES if page.path == path)
    with env.running() as client:
        nodes = graph(client.get(path).text)
    site = env.settings.base_url.rstrip("/")
    article = next(n for n in nodes if n["@type"] == "Article")
    assert article["datePublished"] == entry.published
    assert article["dateModified"] == entry.updated
    assert article["author"]["name"] == "Harish Manoharan"
    assert article["author"]["url"] == "https://harishmanoharan.com"
    assert article["mainEntityOfPage"] == {"@id": f"{site}{path}#webpage"}
    page = next(n for n in nodes if n["@type"] == "WebPage")
    assert page["@id"] == f"{site}{path}#webpage" and page["dateModified"] == entry.updated
    assert {"WebSite", "Person", "SoftwareApplication"} <= {n["@type"] for n in nodes}


def test_only_the_guides_get_an_article(env):
    with env.running() as client:
        landing = graph(client.get("/").text)
        posting = graph(client.get("/help/posting").text)
    assert not [n for n in landing + posting if n["@type"] == "Article"]


def test_the_guides_are_in_the_sitemap_llms_txt_and_the_footer(env):
    site = env.settings.base_url.rstrip("/")
    with env.running() as client:
        sitemap = ET.fromstring(client.get("/sitemap.xml").content)
        llms = client.get("/llms.txt").text
        footer = client.get("/limits").text
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    dates = {
        url.find("s:loc", ns).text: url.find("s:lastmod", ns).text
        for url in sitemap.findall("s:url", ns)
    }
    for path in GUIDES:
        entry = next(page for page in seo.PUBLIC_PAGES if page.path == path)
        assert dates[f"{site}{path}"] == entry.updated
        assert f"]({site}{path})" in llms
        assert f'href="{path}"' in footer
    assert "## Guides" in llms


def test_the_ada_guide_states_the_dates_in_the_rule_and_how_they_were_checked(env):
    with env.running() as client:
        text = flat(client.get(ADA).text)
    # 28 CFR 35.200(b) as amended by the interim final rule of April 20, 2026 (91 FR 20902).
    assert "April 26, 2027" in text and "April 26, 2028" in text
    assert "WCAG) version 2.1, Level A and Level AA" in text
    assert "91 FR 20902" in text and "June 22, 2026" in text
    assert "As of October 28, 2026" in text and "the dates may still change" in text
    assert "guidance, not legal advice" in text
    for source in (
        "ecfr.gov/current/title-28/chapter-I/part-35/subpart-H/section-35.200",
        "federalregister.gov/documents/2026/04/20/2026-07663",
        "ada.gov/resources/2024-03-08-web-rule/",
    ):
        assert source in client_html(env, ADA)


def client_html(env, path: str) -> str:
    with env.running() as client:
        return client.get(path).text


@pytest.mark.parametrize("path", GUIDES)
def test_no_guide_claims_conformance_or_ranks_the_tools(env, path):
    text = flat(client_html(env, path)).lower()
    assert "it assists; it does not certify" in text
    for phrase in (
        "readablestem is compliant",
        "readablestem is accessible",
        "makes your",
        "guarantee",
        "ensures compliance",
        "ada compliant",
        "wcag compliant",
        "beats",
        "outperform",
        "best ",
    ):
        assert phrase not in text, phrase


def test_the_comparison_says_where_the_other_tools_fit_better(env):
    text = flat(client_html(env, COMPARE))
    assert "You have the LaTeX source. LaTeXML is the better fit." in text
    assert "Mathpix is the better fit" in text
    assert "no accuracy comparison" in text
    for source in (
        "docs.mathpix.com/reference/shared-types",
        "mathpix.com/pricing/snip",
        "dlmf.nist.gov/LaTeXML/manual/commands/latexmlpost.html",
        "github.com/brucemiller/LaTeXML/blob/master/LICENSE",
    ):
        assert source in client_html(env, COMPARE)
