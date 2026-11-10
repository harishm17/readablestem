"""The guides under /guides/ (web/routes/pages.py, templates/guide_*.html): each one a
server-rendered page with a visible date, an Article in its structured data, a place in the
sitemap and llms.txt, a link from the footer, a Sources list, and no claim that
ReadableSTEM makes anything conform."""

from __future__ import annotations

import itertools
import json
import re

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
    assert (
        '<h2 id="sources">Sources</h2>' in html and "accessed on october 28, 2026" in html.lower()
    )
    assert "https://harishmanoharan.com" in html


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
