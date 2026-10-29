"""What search engines and crawlers see (web.seo, routes/discovery.py): HEAD, canonical
addresses, robots.txt, sitemap.xml, llms.txt, the IndexNow key, noindex on private pages,
structured data on public ones, and the showcase."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

import pytest

from fixtures.web.support import WebEnv
from readablestem.web import seo
from readablestem.web.security import APP_CSP, VIEW_CSP


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(seo, "SHOWCASE_DIR", tmp_path / "no-showcase")
    return WebEnv(tmp_path)


def json_ld(html: str) -> list[dict]:
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    return [json.loads(block) for block in blocks]


def description(html: str) -> str:
    match = re.search(r'<meta name="description" content="([^"]*)"', html)
    assert match
    return match.group(1)


def test_head_is_answered_like_get_without_a_body(env):
    with env.running() as client:
        for path in ("/", "/privacy", "/robots.txt", "/sitemap.xml", "/llms.txt"):
            got, head = client.get(path), client.head(path)
            assert head.status_code == got.status_code == 200, path
            assert head.content == b"", path
            assert head.headers["content-type"] == got.headers["content-type"], path
        assert client.head("/no-such-page").status_code == 404


def test_www_and_trailing_slashes_redirect_to_one_address(env):
    base = env.settings.base_url.rstrip("/")
    host = base.split("://", 1)[1]
    with env.running() as client:
        www = client.get("/limits?x=1", headers={"host": f"www.{host}"})
        assert www.status_code == 301 and www.headers["location"] == f"{base}/limits?x=1"
        slash = client.get("/privacy/")
        assert slash.status_code == 301 and slash.headers["location"] == "/privacy"
        assert client.get("/").status_code == 200
        assert client.post("/privacy/").status_code != 301  # only GET and HEAD move


def test_a_trailing_slash_never_becomes_a_protocol_relative_address():
    middleware = seo.CrawlerRequestsMiddleware(None, base_url="https://readablestem.com")  # ty: ignore[invalid-argument-type]
    scope = {"type": "http", "method": "GET", "path": "//evil.example/", "headers": []}
    assert middleware._redirect_target(scope) == "/evil.example"


def test_robots_allows_the_public_pages_and_names_the_sitemap(env):
    with env.running() as client:
        text = client.get("/robots.txt").text
    site = env.settings.base_url.rstrip("/")
    assert f"Sitemap: {site}/sitemap.xml" in text
    groups = text.split("\n\n")
    agents = [g.splitlines()[0] for g in groups if g.startswith("User-agent:")]
    assert "User-agent: *" in agents and "User-agent: GPTBot" in agents
    assert "User-agent: ClaudeBot" in agents and "User-agent: PerplexityBot" in agents
    for group in groups[:-1]:
        assert "Allow: /" in group and "Disallow: /app" in group and "Disallow: /jobs/" in group
        disallowed = [line.split(": ", 1)[1] for line in group.splitlines() if "Disallow" in line]
        for page in seo.PUBLIC_PAGES:
            assert not any(page.path.startswith(rule) for rule in disallowed), page.path


def test_sitemap_lists_every_public_page_and_nothing_private(env):
    with env.running() as client:
        response = client.get("/sitemap.xml")
        assert response.headers["content-type"].startswith("application/xml")
        root = ET.fromstring(response.content)
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [loc.text for loc in root.findall("s:url/s:loc", ns)]
        site = env.settings.base_url.rstrip("/")
        assert locs == [f"{site}{page.path}" for page in seo.PUBLIC_PAGES]
        assert all(
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", m.text or "")
            for m in root.iter(f"{{{ns['s']}}}lastmod")
        )
        for page in seo.PUBLIC_PAGES:
            assert client.get(page.path).status_code == 200, page.path


def test_llms_txt_and_indexnow_key(env):
    with env.running() as client:
        llms = client.get("/llms.txt").text
        key = client.get(f"/{seo.INDEXNOW_KEY}.txt")
        icon = client.get("/favicon.ico")
    assert llms.startswith("# ReadableSTEM\n\n> ")
    assert "/demo" in llms and seo.PROOF_URL in llms and "does not certify" in llms
    assert key.status_code == 200 and key.text == seo.INDEXNOW_KEY
    assert icon.status_code == 200 and icon.content


def test_public_pages_carry_structured_data_and_private_ones_noindex(env):
    with env.running() as client:
        public = {page.path: client.get(page.path).text for page in seo.PUBLIC_PAGES}
        private = {path: client.get(path).text for path in ("/login", "/status", "/missing")}
    for path, html in public.items():
        assert 'name="robots"' not in html, path
        assert f'rel="canonical" href="{env.settings.base_url.rstrip("/")}{path}"' in html, path
        (data,) = json_ld(html)
        types = {node["@type"] for node in data["@graph"]}
        assert {"WebSite", "SoftwareApplication", "Person", "WebPage"} <= types, path
        app = next(n for n in data["@graph"] if n["@type"] == "SoftwareApplication")
        assert app["offers"]["price"] == "0" and app["isAccessibleForFree"] is True
        assert app["applicationCategory"] == "EducationalApplication"
        person = next(n for n in data["@graph"] if n["@type"] == "Person")
        assert person["name"] == "Harish Manoharan" and person["url"] == seo.AUTHOR_URL
    for path, html in private.items():
        assert '<meta name="robots" content="noindex">' in html, path
        assert not json_ld(html), path
    app_pages = [html for path, html in public.items() if path != "/demo"]
    descriptions = [description(html) for html in app_pages]
    assert len(set(descriptions)) == len(descriptions)
    assert all(len(d) <= 170 for d in descriptions), descriptions


def test_structured_data_cannot_be_closed_by_page_text():
    tag = str(seo.json_ld_tag({"name": "</script><script>alert(1)</script> & more"}))
    assert tag.count("</script>") == 1 and "<script>alert" not in tag
    body = tag.removeprefix('<script type="application/ld+json">').removesuffix("</script>")
    assert json.loads(body)["name"].startswith("</script>")


def test_demo_has_one_address_and_a_title_of_its_own(tmp_path, monkeypatch):
    from readablestem.web.routes import pages as pages_module

    demo = tmp_path / "index.html"
    demo.write_text("<!doctype html><html><head><title>Speed</title></head><body><h1>x</h1>")
    monkeypatch.setattr(pages_module, "DEMO_PAGE", demo)
    env = WebEnv(tmp_path)
    with env.running() as client:
        html = client.get("/demo").text
    site = env.settings.base_url.rstrip("/")
    assert f'<link rel="canonical" href="{site}/demo">' in html
    assert f"<title>{pages_module.DEMO_TITLE}</title>" in html
    assert json_ld(html)


# --------------------------------------------------------------------------- showcase


@pytest.fixture
def showcase(tmp_path, monkeypatch):
    root = tmp_path / "showcase"
    page = (
        '<!doctype html><html lang="en"><head><title>{} | ReadableSTEM demo</title>'
        '<meta name="description" content="About {}."></head><body><h1>{}</h1></body></html>'
    )
    for rel in ("index.html", "method/index.html", "examples/physics-2-3/converted.html"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(page.format(rel, rel, rel))
    (root / "assets").mkdir()
    (root / "assets" / "site.css").write_text("body{}")
    (root / ".nojekyll").write_text("")
    (root / "404.html").write_text("missing")
    monkeypatch.setattr(seo, "SHOWCASE_DIR", root)
    return WebEnv(tmp_path)


def test_showcase_pages_are_served_with_their_own_address(showcase):
    site = showcase.settings.base_url.rstrip("/")
    with showcase.running() as client:
        assert client.get("/showcase").headers["location"] == "/showcase/"
        home = client.get("/showcase/")
        method = client.get("/showcase/method/")
        same = client.get("/showcase/method/index.html")
        css = client.get("/showcase/assets/site.css")
        converted = client.get("/showcase/examples/physics-2-3/converted.html")
        for hidden in ("/showcase/.nojekyll", "/showcase/404.html", "/showcase/../seo.py"):
            assert client.get(hidden).status_code == 404, hidden
        assert client.get("/showcase/%2e%2e/seo.py").status_code == 404
        sitemap = client.get("/sitemap.xml").text
        landing = client.get("/").text
    assert home.status_code == method.status_code == css.status_code == 200
    assert method.text == same.text
    assert f'<link rel="canonical" href="{site}/showcase/method/index.html">' in method.text
    assert method.headers["content-security-policy"] == APP_CSP
    assert json_ld(method.text) and 'name="robots"' not in method.text
    # The stored conversion is the demo's twin: sandboxed, not indexed, pointing at /demo.
    assert converted.headers["content-security-policy"] == VIEW_CSP
    assert '<meta name="robots" content="noindex, follow">' in converted.text
    assert f'<link rel="canonical" href="{site}/demo">' in converted.text
    assert f"{site}/showcase/method/index.html" in sitemap
    assert "converted.html" not in sitemap and "404.html" not in sitemap
    assert 'href="/showcase/examples/index.html"' in landing


def test_without_a_showcase_build_nothing_links_to_it(env):
    with env.running() as client:
        assert client.get("/showcase/").status_code == 404
        assert "/showcase/" not in client.get("/").text
        assert "/showcase/" not in client.get("/sitemap.xml").text
