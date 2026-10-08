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

from a11y_stem.web.database import utc_today
from a11y_stem.web.db import SpendDay, session_scope
from a11y_stem.web.security import VIEW_CSP
from fixtures.web.support import ADMIN, ALICE, WebEnv, job_id_from, login, make_pdf, upload

WEB = Path(__file__).resolve().parents[1] / "src" / "a11y_stem" / "web"
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
    from a11y_stem.web.routes import pages as pages_module

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
        assert 'href="https://github.com/harishm17/a11y-stem">Source code</a>' in html, name
        assert "GNU Affero General Public License" in html, name


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
    assert all(t.endswith("| a11y-stem") for t in titles.values())


def test_landing_page_says_assists_and_links_the_demo(pages):
    html = pages["/"]
    assert "assists" in html
    assert 'href="/demo"' in html
    assert 'href="/dev-login"' in html  # the sign-in option on this test server


def test_privacy_and_terms_are_drafts_describing_real_behaviour(pages):
    for path in ("/privacy", "/terms"):
        assert "Draft, to be reviewed by Harish." in pages[path]
        assert "help@example.org" in pages[path]
    privacy = " ".join(pages["/privacy"].split())
    assert "deleted automatically <strong>24 hours</strong> after upload" in privacy
    assert "<strong>30 days</strong>" in privacy and "unticked unless you tick it" in privacy
    assert "never used to train AI models" in privacy
    assert "student records" in privacy
    assert "never contain document text" in privacy
    terms = " ".join(pages["/terms"].split())
    assert "student records" in terms and "assists" in terms


def test_privacy_page_states_the_purge_window_and_backups(pages):
    privacy = " ".join(pages["/privacy"].split())
    assert "an hourly clean-up then removes it from the database" in privacy
    assert "one backup is kept" in privacy and "up to about one more day" in privacy
    assert "TODO(Harish)" not in pages["/privacy"]  # the decision note is a template comment
    assert "Langfuse" not in privacy  # only named when traces are actually sent there


class StubTracer:
    def flush(self) -> None:
        pass


def test_privacy_page_names_langfuse_when_tracing_is_on(tmp_path, monkeypatch):
    from a11y_stem.web import app as app_module

    monkeypatch.setattr(app_module, "make_tracer", lambda settings: StubTracer())
    env = WebEnv(tmp_path, tracing_enabled=True)
    with env.running() as client:
        privacy = " ".join(client.get("/privacy").text.split())
    assert "<strong>Langfuse</strong>" in privacy and "never contain document text" in privacy


def test_status_page_says_whether_conversions_are_open(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        page = " ".join(client.get("/status").text.split())
        assert "Up" in page and "Open" in page and "Documents waiting</dt><dd>0" in page
        with session_scope(env.ctx.factory) as session:
            session.add(SpendDay(day=utc_today(), spent_usd=5.0, held_usd=0.0))
        page = " ".join(client.get("/status").text.split())
        assert "Paused: today" in page


def test_limits_page_is_left_for_harish(pages):
    assert "TODO(Harish)" in pages["/limits"]


def test_launch_check_lists_what_is_unfinished(capsys):
    from a11y_stem.cli.main import main
    from a11y_stem.ops.launch import launch_blockers

    items = launch_blockers()
    assert "web/templates/limits.html: still has a TODO(Harish) note" in items
    assert main(["ops", "launch-check"]) == 1
    out = capsys.readouterr().out
    assert "limits.html" in out and f"launch_blockers={len(items) + 1}" in out  # + baseline


def test_a_placeholder_demo_is_not_counted_as_done(tmp_path, monkeypatch):
    from a11y_stem.ops import launch

    demo = tmp_path / "index.html"
    demo.write_text('<p data-a11y-stem-demo="placeholder">x</p>')
    monkeypatch.setattr(launch, "DEMO_PAGE", demo)
    assert any("placeholder output" in item for item in launch.launch_blockers())


def test_demo_page_without_a_demo_file(pages):
    assert "The demo is being prepared" in pages["/demo"]


def test_demo_file_is_served_sandboxed(tmp_path, monkeypatch):
    from a11y_stem.web.routes import pages as pages_module

    demo = tmp_path / "index.html"
    demo.write_text("<!doctype html><title>demo</title><p style='color:red'>maths</p>")
    monkeypatch.setattr(pages_module, "DEMO_PAGE", demo)
    env = WebEnv(tmp_path)
    with env.running() as client:
        response = client.get("/demo")
    assert response.status_code == 200 and "maths" in response.text
    assert response.headers["content-security-policy"] == VIEW_CSP


def test_committed_demo_is_real_output_and_served_sandboxed(tmp_path):
    from a11y_stem.web.routes import pages as pages_module

    demo = pages_module.DEMO_PAGE
    if not demo.is_file():
        pytest.skip("no demo built (a11y-stem demo build)")
    text = demo.read_text(encoding="utf-8")
    assert 'data-a11y-stem-demo="placeholder"' not in text  # built with the real models
    assert "openstax.org" in text and "CC BY" in text  # attribution travels with it
    env = WebEnv(tmp_path)
    with env.running() as client:
        response = client.get("/demo")
    assert response.status_code == 200 and "<math" in response.text
    assert response.headers["content-security-policy"] == VIEW_CSP


def test_job_page_works_without_javascript(pages):
    queued = pages["/jobs/{id} queued"]
    assert "http-equiv" not in queued  # reloading is opt-in (WCAG 2.2.1)
    assert re.search(r'<noscript>\s*<p><a href="/jobs/[0-9a-f]{32}\?auto=1">', queued)
    assert 'role="status"' in queued
    done = pages["/jobs/{id}"]
    assert "noscript" not in done
    # Results are plain links: no JavaScript needed to download or review.
    assert re.search(r'href="/jobs/[0-9a-f]{32}/download"', done)
    assert re.search(r'href="/jobs/[0-9a-f]{32}/review"', done)


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
