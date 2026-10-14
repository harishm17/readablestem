"""Shared helpers for the web tests (tests/test_web_*.py).

Imported as `fixtures.web.support` (pytest puts tests/ on sys.path). Everything is
offline: SQLite in tmp_path, a fake converter that returns a fixed `ConversionOutput`,
`FakeVisionClient`, PDFs generated with PyMuPDF, and dev login.

The fake document deliberately contains SECRET-* markers in every kind of content
(text, LaTeX, alt text, filename) so tests can assert none of it reaches the logs.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pymupdf
from fastapi.testclient import TestClient

from a11y_stem.config import Settings
from a11y_stem.core.budget import JobBudget
from a11y_stem.core.types import (
    Check,
    ConversionOutput,
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    ModelCall,
    Status,
)
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.web.app import create_app
from a11y_stem.web.auth import set_allowed
from a11y_stem.web.db import Job, session_scope
from a11y_stem.web.email_senders import LoginEmail, register_sender
from a11y_stem.web.worker import Worker

SECRETS = (
    "SECRET-DOC-TEXT",
    "SECRET-HEADING",
    "SECRETLATEX",
    "SECRET-ALT",
    "SECRET-FILENAME",
    "SECRET-CAPTION",
)
ADMIN = "admin@example.org"
ALICE = "alice@example.org"
BOB = "bob@example.org"

# 1x1 transparent PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
_CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


class OutboxSender:
    """A sign-in email sender that keeps what it was asked to send (never a real provider)."""

    sent: list[LoginEmail] = []  # noqa: RUF012  (shared on purpose: the app builds its own)
    fails = False

    def send(self, message: LoginEmail) -> None:
        if OutboxSender.fails:
            raise RuntimeError("SECRET-SMTP-DETAIL")
        OutboxSender.sent.append(message)

    @classmethod
    def reset(cls) -> None:
        cls.sent = []
        cls.fails = False


register_sender("outbox", lambda settings: OutboxSender(), production_ok=False)


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": "test",
        "database_url": f"sqlite:///{tmp_path}/web.db",
        "dev_login_enabled": True,
        "admin_emails": ADMIN,
        "run_worker": False,
        "contact_email": "help@example.org",
        "cron_token": "c" * 40,
        "per_user_daily_page_quota": 20,
        "daily_spend_cap_usd": 5.0,
        "max_pages_per_job": 5,
        "max_upload_mb": 1,
        "google_client_id": None,
        "google_client_secret": None,
        "session_secret": None,
        # Word and PowerPoint uploads depend on LibreOffice being installed; the tests that
        # are about them (tests/test_web_office.py) turn this on.
        "office_input_enabled": False,
    }
    values.update(overrides)
    if values["env"] != "prod":
        # Browser tests share one server and one address for many sign-ins and uploads; the
        # rate limit tests (tests/test_audit_ratelimit.py) switch it back on. Production
        # settings keep the default, which production requires.
        values.setdefault("rate_limits_enabled", False)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def make_pdf(pages: int = 1, text: str = "Kinematics") -> bytes:
    doc = pymupdf.open()
    for number in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"{text} page {number + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def encrypted_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "locked")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="own")
    doc.close()
    return data


def fake_result() -> DocumentResult:
    """A small finished document: a heading, a paragraph, two equations, a figure."""
    call = ModelCall(step="equation", model="gpt-6.1-sol", prompt_version="eq-test", cost_usd=0.004)
    elements = [
        Element(id="p1-e0", kind=Kind.HEADING, page=0, bbox=(0, 0, 1, 1), order=0,
                text="SECRET-HEADING Kinematics", font_size=18, bold=True),
        Element(id="p1-e1", kind=Kind.PARAGRAPH, page=0, bbox=(0, 1, 1, 2), order=1,
                text="SECRET-DOC-TEXT The speed is ⟦p1-e2⟧."),
        Element(id="p1-e2", kind=Kind.EQUATION, page=0, bbox=(0, 1, 1, 2), order=2,
                display="inline", parent_id="p1-e1"),
        Element(id="p1-e3", kind=Kind.EQUATION, page=0, bbox=(0, 2, 1, 3), order=3,
                display="block"),
        Element(id="p1-e4", kind=Kind.FIGURE, page=0, bbox=(0, 3, 1, 4), order=4,
                caption="Figure 1 SECRET-CAPTION"),
        Element(id="p1-e5", kind=Kind.EQUATION, page=0, bbox=(0, 4, 1, 5), order=5,
                display="block"),
    ]  # fmt: skip
    fixes = [
        Fix(element_id="p1-e0", kind=Kind.HEADING, status=Status.ACCEPTED, confidence=1.0,
            heading=HeadingFix(level=1, text="SECRET-HEADING Kinematics")),
        Fix(element_id="p1-e2", kind=Kind.EQUATION, status=Status.ACCEPTED, confidence=0.97,
            equation=EquationFix(latex="v = d / t", mathml="<math><mi>v</mi><mo>=</mo>"
                                 "<mi>d</mi><mo>/</mo><mi>t</mi></math>",
                                 speech="v equals d over t", display="inline"),
            checks=[Check(name="latex.allowed_commands", passed=True)], calls=[call]),
        Fix(element_id="p1-e3", kind=Kind.EQUATION, status=Status.NEEDS_REVIEW, confidence=0.55,
            equation=EquationFix(latex=r"\SECRETLATEX{x}", mathml="<math display=\"block\">"
                                 "<mi>x</mi><mo>+</mo><mn>1</mn></math>",
                                 speech="x plus 1", display="block"),
            checks=[Check(name="latex.allowed_commands", passed=False, severity="warning",
                          code="unknown_command")], calls=[call]),
        Fix(element_id="p1-e4", kind=Kind.FIGURE, status=Status.NEEDS_REVIEW, confidence=0.6,
            figure=FigureFix(figure_type="chart", alt="SECRET-ALT a rising line"),
            calls=[call]),
        Fix(element_id="p1-e5", kind=Kind.EQUATION, status=Status.ABSTAINED, confidence=0.2,
            reason="low_confidence"),
    ]  # fmt: skip
    return DocumentResult(
        pipeline_version="test",
        prompt_versions={"equation": "eq-test"},
        title="SECRET-HEADING Kinematics",
        elements=elements,
        fixes=fixes,
        stats=DocumentStats(
            pages=1,
            cost_usd=0.012,
            latency_ms=5,
            model_calls=3,
            by_kind={"equation": 3, "figure": 1, "heading": 1},
            by_status={"accepted": 2, "needs_review": 2, "abstained": 1},
        ),
        html="<!doctype html><html lang='en'><head><title>SECRET-HEADING</title></head>"
        "<body><main><h1>SECRET-HEADING Kinematics</h1><p>SECRET-DOC-TEXT</p></main></body></html>",
    )


def fake_output() -> ConversionOutput:
    return ConversionOutput(result=fake_result(), crops={"p1-e3": PNG, "p1-e4": PNG, "p1-e5": PNG})


def output_with_nul() -> ConversionOutput:
    """`fake_output()` with NUL characters (they occur in text extracted from PDFs) in the
    HTML, an element's text and an alt text. Postgres refuses NUL in text and JSON."""
    output = fake_output()
    result = output.result
    result.html = result.html.replace("SECRET-DOC-TEXT", "SECRET-DOC-TEXT\x00tail")
    result.elements[1].text = "para\x00graph"
    assert result.fixes[3].figure is not None
    result.fixes[3].figure.alt = "alt\x00text"
    return output


class FakeConverter:
    """Stands in for `pipeline.run.convert_pdf`; records calls, can raise instead."""

    def __init__(self, output: ConversionOutput | None = None) -> None:
        self.output = output or fake_output()
        self.calls: list[dict[str, Any]] = []
        self.raises: BaseException | None = None
        self.before_return: Callable[[], None] | None = None

    def __call__(
        self,
        pdf_bytes: bytes,
        /,
        *,
        settings,
        client,
        progress=None,
        options=None,
        stage_progress=None,
        concurrency=None,
    ) -> ConversionOutput:
        self.calls.append(
            {
                "pdf": pdf_bytes,
                "settings": settings,
                "client": client,
                "options": options,
                "concurrency": concurrency,
            }
        )
        if progress is not None:
            progress(1, 1)
        if self.raises is not None:
            raise self.raises
        if self.before_return is not None:
            self.before_return()
        return self.output.model_copy(deep=True)


def fake_client_factory(settings: Settings, budget: JobBudget) -> FakeVisionClient:
    return FakeVisionClient(budget=budget)


def with_file(env: Any, job_id: str | None = None) -> Job:
    """The job after its HTML file is up to date: a review write only marks the file out
    of date, and asking for the file makes it (`routes.jobs.current_html`)."""
    job_id = job_id or env.job_id
    response = env.browser.get(f"/jobs/{job_id}/view")
    assert response.status_code == 200, response.status_code
    return env.job(job_id)


def csrf_from(html: str) -> str:
    match = _CSRF.search(html)
    assert match, "no CSRF token in page"
    return match.group(1)


class WebEnv:
    """One app + database for a test, plus helpers to drive it like a browser."""

    def __init__(self, tmp_path: Path, **overrides: Any) -> None:
        self.settings = make_settings(tmp_path, **overrides)
        self.converter = FakeConverter()
        self.app = create_app(
            self.settings, converter=self.converter, client_factory=fake_client_factory
        )
        self.ctx = self.app.state.ctx

    @contextmanager
    def running(self) -> Iterator[TestClient]:
        with TestClient(self.app, follow_redirects=False) as client:
            yield client

    def client(self) -> TestClient:
        """Another browser (own cookies) on the already-started app."""
        return TestClient(self.app, follow_redirects=False)

    def allow(self, email: str, *, admin: bool = False) -> None:
        with session_scope(self.ctx.factory) as session:
            set_allowed(session, email, allowed=True, admin=admin)

    def worker(self, **kwargs: Any) -> Worker:
        return Worker(
            self.settings,
            self.ctx.factory,
            converter=self.converter,
            client_factory=fake_client_factory,
            **kwargs,
        )

    def run_worker(self) -> str | None:
        return self.worker().run_once()

    def job(self, job_id: str) -> Job:
        with session_scope(self.ctx.factory) as session:
            job = session.get(Job, job_id)
            assert job is not None
            return job


def login(client: TestClient, email: str):
    token = csrf_from(client.get("/dev-login").text)
    return client.post("/dev-login", data={"csrf_token": token, "email": email})


def token(client: TestClient, path: str = "/app") -> str:
    return csrf_from(client.get(path).text)


def upload(
    client: TestClient,
    data: bytes,
    *,
    filename: str = "notes.pdf",
    keep: bool = False,
    share: bool = False,
    exam: bool = False,
    csrf: str | None = None,
):
    fields = {"csrf_token": csrf if csrf is not None else token(client)}
    if keep:
        fields["keep"] = "on"
    if share:
        fields["share"] = "on"
    if exam:
        fields["exam"] = "on"
    return client.post("/jobs", data=fields, files={"pdf": (filename, data, "application/pdf")})


def job_id_from(response) -> str:
    location = response.headers["location"]
    assert location.startswith("/jobs/"), location
    return location.rsplit("/", 1)[-1]
