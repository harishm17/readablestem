"""/readyz, the SIGTERM hook, and a real server shut down with SIGTERM."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from a11y_stem.web import health
from a11y_stem.web.database import as_utc
from a11y_stem.web.db import utcnow
from fixtures.web.support import WebEnv, fake_output, job_id_from, make_pdf, upload

SECRET_KEY = "sk-never-in-the-output-0123456789"


def running(tmp_path, **overrides):
    env = WebEnv(tmp_path, **overrides)
    return env, env.running()


def test_readyz_is_ready_with_a_key_and_shows_no_secret(tmp_path):
    env, client = running(tmp_path, openai_api_key=SECRET_KEY)
    with client as web:
        response = web.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "worker": "disabled", "model_provider": "ok"},
    }
    assert SECRET_KEY not in response.text and "gpt" not in response.text


def test_readyz_follows_the_selected_provider(tmp_path):
    only_openai = {"model_provider": "anthropic", "openai_api_key": SECRET_KEY}
    env, client = running(tmp_path, **only_openai)
    with client as web:
        response = web.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["model_provider"] == "no_key"
    env, client = running(tmp_path / "b", model_provider="anthropic", anthropic_api_key="sk-a")
    with client as web:
        assert web.get("/readyz").status_code == 200


def test_readyz_without_any_key_is_not_ready_unless_the_models_are_fake(tmp_path):
    env, client = running(tmp_path)
    with client as web:
        assert web.get("/readyz").status_code == 503
    env, client = running(tmp_path / "f", fake_models=True)
    with client as web:
        assert web.get("/readyz").json()["checks"]["model_provider"] == "fake"


def test_readyz_names_the_database_when_it_does_not_answer(tmp_path, monkeypatch):
    env, client = running(tmp_path, openai_api_key=SECRET_KEY)
    with client as web:
        monkeypatch.setattr(health, "ping", lambda engine: False)
        response = web.get("/readyz")
        assert web.get("/healthz").status_code == 200  # a different ping: untouched here
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unreachable"


def test_readyz_reports_the_worker_alive_stalled_and_stopping(tmp_path, monkeypatch):
    env, client = running(tmp_path, openai_api_key=SECRET_KEY, run_worker=True)
    with client as web:
        assert web.get("/readyz").json()["checks"]["worker"] == "ok"
        worker = env.ctx.worker
        monkeypatch.setattr(worker, "seconds_since_seen", lambda: health.STALLED_AFTER_S + 1)
        stalled = web.get("/readyz")
        assert stalled.status_code == 503 and stalled.json()["checks"]["worker"] == "stalled"
        monkeypatch.undo()
        worker.begin_shutdown()
        stopping = web.get("/readyz")
        assert stopping.status_code == 503 and stopping.json()["checks"]["worker"] == "stopping"


def test_a_dead_worker_thread_is_not_ready(tmp_path):
    env, client = running(tmp_path, openai_api_key=SECRET_KEY, run_worker=True)
    with client as web:
        worker = env.ctx.worker
        worker._stop.set()
        worker._thread.join(5)
        worker._stop.clear()  # the thread is gone but nobody asked for it
        assert web.get("/readyz").json()["checks"]["worker"] == "stopped"
        worker._stop.set()


def test_begin_shutdown_ends_a_running_jobs_lease_without_waiting(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow("alice@example.org")
        from fixtures.web.support import login

        login(client, "alice@example.org")
        job_id = job_id_from(upload(client, make_pdf(1)))
        started, release = threading.Event(), threading.Event()

        def converter(pdf_bytes, /, *, settings, client, progress=None):
            started.set()
            release.wait(5)
            return fake_output()

        worker = env.worker(lease_s=60)
        worker.converter = converter
        runner = threading.Thread(target=worker.run_once)
        runner.start()
        assert started.wait(5)
        began = time.monotonic()
        worker.begin_shutdown()
        worker.begin_shutdown()  # a second SIGTERM changes nothing
        assert time.monotonic() - began < 1
        assert worker._shutdown_thread is not None
        worker._shutdown_thread.join(5)
        lease = as_utc(env.job(job_id).lease_until)
        assert lease is not None and lease <= utcnow()  # ended now, not in 60 s
        assert worker.draining and not worker.alive
        release.set()
        runner.join(10)


def test_sigterm_hook_stops_the_worker_and_then_calls_the_previous_handler(tmp_path):
    env = WebEnv(tmp_path, run_worker=True)
    seen: list[int] = []
    outer = signal.signal(signal.SIGTERM, lambda signum, frame: seen.append(signum))
    try:
        with env.running():
            worker = env.ctx.worker
            assert worker is not None and worker.alive
            restore = health.stop_worker_on_sigterm(worker)
            signal.raise_signal(signal.SIGTERM)
            assert seen == [signal.SIGTERM]
            assert worker._shutdown_thread is not None
            worker._shutdown_thread.join(5)
            assert worker.draining
            restore()
            assert signal.getsignal(signal.SIGTERM) is not None
    finally:
        signal.signal(signal.SIGTERM, outer)


def test_the_sigterm_hook_does_nothing_off_the_main_thread():
    before = signal.getsignal(signal.SIGTERM)
    result: list[object] = []
    thread = threading.Thread(
        target=lambda: result.append(health.stop_worker_on_sigterm(object())())
    )
    thread.start()
    thread.join(5)
    assert result == [None] and signal.getsignal(signal.SIGTERM) is before


# ------------------------------------------------------------- a real server and SIGTERM


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_a_served_app_answers_readyz_and_exits_quickly_on_sigterm(tmp_path):
    port = _free_port()
    env = {
        **os.environ,
        "ENV": "dev",
        "FAKE_MODELS": "true",
        "RUN_WORKER": "true",
        "DATABASE_URL": f"sqlite:///{tmp_path}/serve.db",
    }
    for name in ("MODEL_PROVIDER", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DAILY_SPEND_CAP_USD"):
        env.pop(name, None)
    log = Path(tmp_path / "serve.log")
    with log.open("wb") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "a11y_stem.cli.main", "serve", "--port", str(port)],
            cwd=tmp_path,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            body: dict = {}
            while time.monotonic() < deadline:
                try:
                    status, body = _get(f"http://127.0.0.1:{port}/readyz")
                    break
                except OSError:
                    time.sleep(0.2)
            else:
                pytest.fail("server did not start: " + log.read_text()[-500:])
            assert status == 200 and body["checks"]["worker"] == "ok"
            began = time.monotonic()
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
            assert time.monotonic() - began < 8  # Cloud Run kills at 10 s
        finally:
            if proc.poll() is None:
                proc.kill()
    text = log.read_text()
    assert "sigterm_received" in text and "worker_stopped" in text
    assert "Application shutdown complete" in text
