"""The sandbox probe at start-up, its place on /healthz/ops, and the switch it is for LaTeX source
(`web/sandbox_status.py`): FEATURE_LATEX_SOURCE, FEATURE_BEAMER_SOURCE and a FEATURE_BATCH_ZIP zip
with a LaTeX project take effect only while the last probe PASSED."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

import pytest
from sqlalchemy import select

from fixtures.web.support import ALICE, WebEnv, csrf_from, login, make_pdf
from readablestem.core import sandbox
from readablestem.observability.logging import FIELDS_ATTR
from readablestem.source import latexml
from readablestem.web import sandbox_status
from readablestem.web.db import Job, session_scope
from readablestem.web.sandbox_status import SandboxStatus, run_probe
from source_support import make_zip

ARTICLE = "\\documentclass{article}\n\\begin{document}\nHello $x^2$.\n\\end{document}\n"
DECK = "\\documentclass{beamer}\n\\begin{document}\\begin{frame}x\\end{frame}\\end{document}"


@dataclass
class FakeCheck:
    name: str
    value: str


@dataclass
class FakeReport:
    checks: list[FakeCheck] = field(default_factory=list)
    passed: bool = True


def report(passed: bool = True) -> FakeReport:
    return FakeReport(
        [
            FakeCheck("bwrap", "ok" if passed else "fail"),
            FakeCheck("sandbox_start_error", "/secret/path: boom"),
            FakeCheck("sandbox_seccomp", "ok"),
            FakeCheck("sandbox_cgroup", "unavailable"),
            FakeCheck("sandbox_total_bound_mb", "2048"),
        ],
        passed,
    )


def finish(thread: threading.Thread | None) -> None:
    assert thread is not None
    thread.join(10)
    assert not thread.is_alive()


# ------------------------------------------------------------------------------ the probe run


def test_a_passing_probe_is_logged_once_and_shown(caplog):
    status = SandboxStatus()
    with caplog.at_level(logging.INFO):
        finish(run_probe(status, timeout_s=5, probe=lambda: report(True), installed=lambda: True))
    lines = [r for r in caplog.records if "sandbox_probe" in r.getMessage()]
    assert len(lines) == 1
    fields = getattr(lines[0], FIELDS_ATTR)
    assert fields.get("result") == "PASS" and fields.get("bwrap") == "ok"
    assert "sandbox_start_error" not in fields and "/secret" not in str(fields)
    shown = status.public()
    assert shown["state"] == "pass" and shown["result"] == "pass"
    assert shown["bound_mb"] == 2048 and shown["cgroup"] == "unavailable"
    assert shown["seccomp"] == "ok" and shown["checked_at"]
    assert shown["checks"]["bwrap"] == "ok" and "sandbox_start_error" not in shown["checks"]
    assert status.allows_source()


def test_a_failing_probe_does_not_allow_source(caplog):
    status = SandboxStatus()
    with caplog.at_level(logging.INFO):
        finish(run_probe(status, timeout_s=5, probe=lambda: report(False), installed=lambda: True))
    assert status.public()["result"] == "fail" and not status.allows_source()
    assert any(getattr(r, FIELDS_ATTR, {}).get("result") == "FAIL" for r in caplog.records)


def test_a_probe_that_raises_is_a_failure_not_a_crash():
    status = SandboxStatus()

    def boom():
        raise RuntimeError("the message may hold a path: /x")

    finish(run_probe(status, timeout_s=5, probe=boom, installed=lambda: True))
    assert status.state == "fail" and not status.allows_source()


def test_a_probe_that_takes_too_long_is_a_timeout_and_a_failure():
    status = SandboxStatus()
    release = threading.Event()
    thread = run_probe(
        status, timeout_s=0.2, probe=lambda: release.wait(10) or report(), installed=lambda: True
    )
    finish(thread)
    release.set()
    assert status.state == "timeout" and status.public()["result"] == "fail"
    time.sleep(0.2)
    assert status.state == "timeout" and not status.allows_source()  # a late answer changes nothing


def test_without_latexml_nothing_is_run_and_the_state_says_so():
    status = SandboxStatus()
    ran = []
    assert (
        run_probe(status, timeout_s=5, probe=lambda: ran.append(1), installed=lambda: False) is None
    )
    assert ran == [] and status.state == "not_installed" and not status.allows_source()
    assert status.public()["checks"] == {}


def test_the_default_probe_is_the_one_with_require_latexml(monkeypatch):
    from readablestem.ops import sandbox_probe

    seen = {}
    monkeypatch.setattr(sandbox_probe, "probe", lambda **kw: seen.update(kw) or report())
    finish(run_probe(SandboxStatus(), timeout_s=5, installed=lambda: True))
    assert seen == {"require_latexml": True}


# --------------------------------------------------------------------- start-up and /healthz/ops


def test_the_web_process_runs_the_probe_in_the_background_and_healthz_ops_shows_it(
    tmp_path, monkeypatch
):
    from readablestem.ops import sandbox_probe

    release = threading.Event()
    monkeypatch.setattr(latexml, "installed", lambda: True)
    monkeypatch.setattr(sandbox_probe, "probe", lambda **kw: release.wait(10) and report())
    env = WebEnv(tmp_path, sandbox_probe_at_startup=True)
    with env.running() as client:
        # The probe is still running: start-up and the health routes did not wait for it.
        assert client.get("/healthz").status_code == 200
        assert client.get("/healthz/ops").json()["sandbox"]["state"] == "running"
        release.set()
        for _ in range(100):
            shown = client.get("/healthz/ops").json()["sandbox"]
            if shown["state"] != "running":
                break
            time.sleep(0.05)
        assert shown["state"] == "pass" and shown["result"] == "pass"
        assert set(shown) >= {"state", "checks", "bound_mb", "cgroup", "seccomp", "checked_at"}


def test_healthz_ops_says_not_installed_when_the_image_has_no_latexml(tmp_path, monkeypatch):
    monkeypatch.setattr(latexml, "installed", lambda: False)
    env = WebEnv(tmp_path, sandbox_probe_at_startup=True)
    with env.running() as client:
        assert client.get("/healthz/ops").json()["sandbox"]["state"] == "not_installed"


def test_the_probe_is_not_run_when_it_is_switched_off(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(sandbox_status, "run_probe", lambda *a, **k: called.append(1))
    env = WebEnv(tmp_path)  # tests turn the start-up probe off
    with env.running() as client:
        assert client.get("/healthz/ops").json()["sandbox"]["state"] == "not_run"
    assert called == []


# ------------------------------------------------------------- the switch for LaTeX source


@pytest.fixture
def source_env(tmp_path, monkeypatch):
    monkeypatch.setattr(latexml, "installed", lambda: True)
    monkeypatch.setattr(sandbox, "available", lambda: True)
    env = WebEnv(
        tmp_path,
        feature_latex_source=True,
        feature_beamer_source=True,
        feature_batch_zip=True,
        per_user_daily_page_quota=500,
    )
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


def send(env, data: bytes, filename: str):
    token = csrf_from(env.browser.get("/app").text)
    return env.browser.post(
        "/jobs",
        data={"csrf_token": token},
        files={"pdf": (filename, data, "application/octet-stream")},
    )


def job_names(env) -> list[str]:
    with session_scope(env.ctx.factory) as session:
        return sorted(j.filename for j in session.scalars(select(Job)))


@pytest.mark.parametrize("state", ["not_run", "running", "fail", "timeout", "not_installed"])
def test_a_tex_file_is_unavailable_unless_the_probe_passed(source_env, state):
    source_env.ctx.sandbox.set(state)
    response = send(source_env, ARTICLE.encode(), "a.tex")
    assert response.status_code == 503 and response.headers["retry-after"] == "3600"
    assert job_names(source_env) == []


def test_a_beamer_deck_is_unavailable_unless_the_probe_passed(source_env):
    source_env.ctx.sandbox.set("fail")
    assert send(source_env, DECK.encode(), "slides.tex").status_code == 503
    assert job_names(source_env) == []
    source_env.ctx.sandbox.set("pass")
    assert send(source_env, DECK.encode(), "slides.tex").status_code == 303
    assert job_names(source_env) == ["slides.tex"]


def test_a_project_zip_is_unavailable_unless_the_probe_passed(source_env):
    source_env.ctx.sandbox.set("fail")
    zipped = make_zip({"main.tex": ARTICLE})
    assert send(source_env, zipped, "paper.zip").status_code == 503
    assert job_names(source_env) == []


def test_a_batch_zip_places_its_pdfs_but_not_its_projects_unless_the_probe_passed(source_env):
    data = make_zip({"p1/main.tex": ARTICLE, "slides.pdf": make_pdf(1)})
    source_env.ctx.sandbox.set("fail")
    response = send(source_env, data, "term.zip")
    assert response.status_code in (303, 503)
    assert job_names(source_env) == ["slides.pdf"]  # the project was not accepted
    source_env.ctx.sandbox.set("pass")
    send(source_env, data, "term2.zip")
    assert job_names(source_env) == ["p1.zip", "slides.pdf", "slides.pdf"]


def test_the_pages_offer_latex_source_only_after_a_pass(source_env):
    source_env.ctx.sandbox.set("fail")
    assert 'data-latex="1"' not in source_env.browser.get("/app").text
    assert "LaTeX source files (.tex)" not in source_env.browser.get("/").text
    source_env.ctx.sandbox.set("pass")
    assert 'data-latex="1"' in source_env.browser.get("/app").text
    assert "LaTeX source files (.tex)" in source_env.browser.get("/").text
