"""Page structure and accessibility basics that can be checked without a browser,
public page content, template hygiene (CSP-safe, wording) and the vendored htmx.

The axe-core checks in a real browser are in test_web_browser.py (marker: browser).
"""

from __future__ import annotations

import base64
import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from fixtures.web.support import ADMIN, ALICE, WebEnv, job_id_from, login, make_pdf, upload
from readablestem.web.security import VIEW_CSP

WEB = Path(__file__).resolve().parents[1] / "src" / "readablestem" / "web"
TEMPLATES = WEB / "templates"
DOCS = Path(__file__).resolve().parents[1] / "docs" / "web.md"


class Page(HTMLParser):
    """Collects what the checks below need from one HTML page."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.labels: set[str] = set()
        self.controls: list[tuple[str, str | None]] = []
        self.title = ""
        self._in_title = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.tags.append((tag, attributes))
        if tag == "label" and attributes.get("for"):
            self.labels.add(attributes["for"] or "")
        if tag in {"textarea", "select"} or (
            tag == "input" and attributes.get("type") not in {"hidden", "submit", "button"}
        ):
            self.controls.append((tag, attributes.get("id")))
        self._in_title = tag == "title"

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data

    def count(self, tag: str) -> int:
        return sum(1 for t, _ in self.tags if t == tag)


@pytest.fixture(scope="module")
def pages(tmp_path_factory) -> dict[str, str]:
    """Every kind of page, rendered once, as the user who can see it."""
    from readablestem.web.routes import pages as pages_module

    root = tmp_path_factory.mktemp("pages")
    env = WebEnv(root)
    out: dict[str, str] = {}
    # The committed demo is a converted document, not an app page: render /demo here as it
    # looks before a demo is built (the real file is checked in test_committed_demo_*).
    with pytest.MonkeyPatch.context() as mp, env.running() as anon:
        mp.setattr(pages_module, "DEMO_PAGE", root / "no-demo" / "index.html")
        for path in (
            "/",
            "/demo",
            "/privacy",
            "/terms",
            "/limits",
            "/status",
            "/login",
            "/dev-login",
        ):
            out[path] = anon.get(path).text
        out["404"] = anon.get("/missing").text
        out["not_allowed"] = login(env.client(), "stranger@example.org").text
        env.allow(ALICE)
        alice = env.client()
        login(alice, ALICE)
        queued = job_id_from(upload(alice, make_pdf(1)))
        out["/jobs/{id} queued"] = alice.get(f"/jobs/{queued}").text
        env.run_worker()
        out["/app"] = alice.get("/app").text
        out["/jobs/{id}"] = alice.get(f"/jobs/{queued}").text
        out["/jobs/{id}/review"] = alice.get(f"/jobs/{queued}/review").text
        admin = env.client()
        login(admin, ADMIN)
        out["/admin"] = admin.get("/admin").text
    return out


def test_every_page_has_the_shared_accessible_layout(pages):
    for name, html in pages.items():
        page = Page(html)
        assert page.count("main") == 1, name
        assert page.count("h1") == 1, name
        assert html.lstrip().startswith("<!doctype html>"), name
        assert '<html lang="en">' in html, name
        assert '<a class="skip-link" href="#main">Skip to main content</a>' in html, name
        assert '<main id="main"' in html, name
        assert page.count("header") == 1 and page.count("footer") == 1, name
        for link in ('href="/privacy"', 'href="/terms"', 'href="/limits"', 'href="/status"'):
            assert link in html, (name, link)
        # The licence sentence is on the Terms page; every footer has a plain link to it.
        assert "offered under AGPL-3.0 terms" not in html or name == "/terms", name
        assert 'href="/terms#open-source">Open source</a>' in html, name
        # The short footers (signed-in pages, sign-in) keep the trust links and "Open source";
        # the source-code and accessibility-office links are in the full footer and the header.
        if "site-footer-compact" not in html:
            assert 'href="https://github.com/harishm17/readablestem">Source code</a>' in html, name
            assert 'href="/for-accessibility-offices">For accessibility offices</a>' in html, name


def test_every_form_control_has_a_label(pages):
    for name, html in pages.items():
        page = Page(html)
        for tag, control_id in page.controls:
            assert control_id, (name, tag)
            assert control_id in page.labels, (name, tag, control_id)


def test_page_titles_are_unique(pages):
    titles = {name: Page(html).title.strip() for name, html in pages.items()}
    distinct = {t for name, t in titles.items() if name != "/jobs/{id} queued"}
    assert len(distinct) == len(titles) - 1, titles
    assert all(t.endswith("| ReadableSTEM") for t in titles.values())


def test_privacy_and_terms_describe_real_behaviour(pages):
    for path in ("/privacy", "/terms"):
        assert "Draft" not in pages[path] and "draft-note" not in pages[path]
        assert "help@example.org" in pages[path]
    privacy = " ".join(pages["/privacy"].split())
    assert "deleted automatically <strong>24 hours</strong> after you last worked on it" in privacy
    assert "<strong>7 days</strong>" in privacy and "<strong>30 days</strong>" in privacy
    assert "can be made the default for new uploads in" in privacy
    assert "does not keep it longer" in privacy  # sharing does not extend the owner clock
    assert "We do not use your uploads to train models, sell them or share them" in privacy
    assert "beta built and maintained by Harish Manoharan" in privacy
    assert "an individual developer" not in privacy and "not a company" not in privacy
    assert "student records" in privacy
    assert "never contain document text" in privacy
    terms = " ".join(pages["/terms"].split())
    assert "student records" in terms and "assists" in terms
    assert "beta built and maintained by Harish Manoharan" in terms
    assert "an individual developer" not in terms and "not a company" not in terms
    assert "governed by the law of the State of Texas, USA" in terms
    assert "does not certify" in terms


def test_launch_check_reports_no_unfinished_public_copy(capsys):
    from readablestem.cli.main import main
    from readablestem.ops.launch import launch_blockers

    assert launch_blockers() == []
    assert main(["ops", "launch-check"]) == 0
    assert "launch_blockers=0" in capsys.readouterr().out


def test_launch_check_still_catches_a_todo_or_a_draft_note(tmp_path, monkeypatch):
    from readablestem.ops import launch

    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "limits.html").write_text('{# TODO(Harish): x #}<p class="draft-note">Draft</p>')
    monkeypatch.setattr(launch, "TEMPLATES_DIR", templates)
    items = launch.launch_blockers()
    assert "web/templates/limits.html: still has a TODO(Harish) note" in items
    assert "web/templates/limits.html: still shows the draft note" in items


def test_a_placeholder_demo_is_not_counted_as_done(tmp_path, monkeypatch):
    from readablestem.ops import launch

    demo = tmp_path / "index.html"
    demo.write_text('<p data-readablestem-demo="placeholder">x</p>')
    monkeypatch.setattr(launch, "DEMO_PAGE", demo)
    assert any("placeholder output" in item for item in launch.launch_blockers())


def test_demo_file_is_served_sandboxed(tmp_path, monkeypatch):
    from readablestem.web.routes import pages as pages_module

    demo = tmp_path / "index.html"
    demo.write_text("<!doctype html><title>demo</title><p style='color:red'>maths</p>")
    monkeypatch.setattr(pages_module, "DEMO_PAGE", demo)
    env = WebEnv(tmp_path)
    with env.running() as client:
        response = client.get("/demo")
    assert response.status_code == 200 and "maths" in response.text
    assert response.headers["content-security-policy"] == VIEW_CSP


def test_job_page_works_without_javascript(pages):
    queued = pages["/jobs/{id} queued"]
    assert "http-equiv" not in queued  # reloading is opt-in (WCAG 2.2.1)
    assert re.search(r'<noscript>\s*<p><a href="/jobs/[0-9a-f]{32}\?auto=1">', queued)
    assert 'role="status"' in queued
    done = pages["/jobs/{id}"]
    assert "noscript" not in done
    # Results are plain links: no JavaScript needed to review or download (a draft,
    # while decisions are open, asks first on a page of its own).
    assert re.search(r'href="/jobs/[0-9a-f]{32}/draft"', done)
    assert re.search(r'href="/jobs/[0-9a-f]{32}/review(\?item=[^"]*)?"', done)


def test_status_is_never_shown_by_colour_alone(pages):
    for name in ("/app", "/jobs/{id}", "/jobs/{id}/review"):
        for badge in re.findall(r'<span class="status-badge[^"]*">([^<]*)</span>', pages[name]):
            assert badge.strip(), name


# --------------------------------------------------------------------------- templates


def template_files() -> list[Path]:
    return sorted(TEMPLATES.glob("*.html"))


@pytest.mark.parametrize("path", template_files(), ids=lambda p: p.name)
def test_templates_have_no_inline_script_or_style(path):
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"\sstyle\s*=", text), "inline style attribute (blocked by CSP)"
    assert "<style" not in text
    assert not re.search(r"\son[a-z]+\s*=", text), "inline event handler"
    assert "javascript:" not in text
    for match in re.finditer(r"<script\b([^>]*)>", text):
        assert "src=" in match.group(1), "inline script (blocked by CSP)"


def test_no_compliance_claims_anywhere_in_the_web_package_or_docs():
    banned = re.compile(r"\b(" + "compl" + r"iant|ensures? compl" + r"iance)\b", re.IGNORECASE)
    files = [*template_files(), *WEB.rglob("*.py"), *WEB.rglob("*.md"), DOCS]
    for path in files:
        if path.exists():
            assert not banned.search(path.read_text(encoding="utf-8")), path


def test_vendored_htmx_matches_its_pinned_hashes():
    vendor = WEB / "static" / "vendor"
    data = (vendor / "htmx-2.0.11.min.js").read_bytes()
    readme = (vendor / "README.md").read_text(encoding="utf-8")
    assert hashlib.sha256(data).hexdigest() in readme
    sri = "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert f'src="/static/vendor/htmx-2.0.11.min.js" integrity="{sri}"' in base
    assert sri in readme


def test_htmx_is_configured_for_the_strict_csp():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    for setting in (
        '"allowEval": false',
        '"includeIndicatorStyles": false',
        '"historyCacheSize": 0',
    ):
        assert setting in base


def test_pages_do_not_offer_word_when_libreoffice_is_missing(tmp_path):
    """The production image is built without LibreOffice: OFFICE_INPUT_ENABLED is true by
    default, but the form, the privacy page and the limits page must say PDF only."""
    env = WebEnv(
        tmp_path, office_input_enabled=True, soffice_path=str(tmp_path / "no-such-soffice")
    )
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        app = client.get("/app").text
        privacy = " ".join(client.get("/privacy").text.split())
        limits = " ".join(client.get("/limits").text.split())
    assert "data-office" not in app and ".docx" not in app
    assert "Word or PowerPoint" not in privacy and "LibreOffice" not in privacy
    assert "Only PDF files are taken" in limits


def test_launch_check_lists_a_half_set_microsoft_sign_in(tmp_path):
    from fixtures.web.support import make_settings
    from readablestem.ops import launch

    tenant = "11111111-1111-4111-8111-111111111111"

    def microsoft_items(**values):
        settings = make_settings(tmp_path, **values)
        return [i for i in launch.launch_blockers(settings=settings) if "MS_" in i]

    assert microsoft_items() == []
    assert (
        microsoft_items(ms_client_id="app", ms_client_secret="s", ms_allowed_tenants=[tenant]) == []
    )
    only_tenants = microsoft_items(ms_allowed_tenants=[tenant])
    assert len(only_tenants) == 1 and "Microsoft sign-in is off" in only_tenants[0]
    id_only = microsoft_items(ms_client_id="app")
    assert len(id_only) == 1 and "MS_CLIENT_ID is set but MS_CLIENT_SECRET is not" in id_only[0]
    secret_only = microsoft_items(ms_client_secret="s", ms_allowed_tenants=[tenant])
    assert len(secret_only) == 1 and "MS_CLIENT_ID is not set" not in secret_only[0]
    no_tenants = microsoft_items(ms_client_id="app", ms_client_secret="s")
    assert len(no_tenants) == 1 and "lists no tenant" in no_tenants[0]
