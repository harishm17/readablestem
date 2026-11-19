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
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from fixtures.web.support import (
    ADMIN,
    WebEnv,
    fake_client_factory,
    fake_output,
    job_id_from,
    login,
    make_pdf,
    upload,
)
from readablestem.ops import heartbeat
from readablestem.web import health, worker_rows, worker_service
from readablestem.web.database import as_utc
from readablestem.web.db import OpsHeartbeat, session_scope, utcnow

SECRET_KEY = "sk-never-in-the-output-0123456789"


def running(tmp_path, **overrides):
    env = WebEnv(tmp_path, **overrides)
    return env, env.running()


def test_readyz_is_ready_with_a_key_and_shows_no_secret(tmp_path):
    _env, client = running(tmp_path, openai_api_key=SECRET_KEY)
    with client as web:
        response = web.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "database": "ok",
            "database_write": "ok",
            "worker": "disabled",
            "model_provider": "ok",
        },
    }
    assert SECRET_KEY not in response.text and "gpt" not in response.text


def test_the_health_routes_with_stale_tasks_and_under_a_held_write_lock(tmp_path):
    """WF51. Fresh tasks, then a backup and a cron run made stale by the clock, then the same
    routes while another connection holds the write lock (F-343): every page that only reads
    keeps answering, `/readyz` says not ready in about a second and ready again at once when the
    lock goes (a real second connection, measured, nothing mocked)."""
    import sqlite3

    env, client = running(tmp_path, openai_api_key=SECRET_KEY)
    anonymous = env.client()

    def beat(backup_age: timedelta, cron_age: timedelta) -> None:
        now = utcnow()
        with session_scope(env.ctx.factory) as session:
            heartbeat.beat(session, heartbeat.BACKUP, now=now - backup_age)
            heartbeat.beat(session, heartbeat.CRON, now=now - cron_age)

    def seen(web) -> dict[str, tuple[int, float]]:
        out = {}
        for path in ("/healthz", "/readyz", "/healthz/ops", "/status"):
            started = time.monotonic()
            out[path] = (web.get(path).status_code, time.monotonic() - started)
        return out

    with client as admin, anonymous:
        env.allow(ADMIN, admin=True)
        login(admin, ADMIN)
        beat(timedelta(hours=1), timedelta(minutes=10))
        public = anonymous.get("/healthz/ops")
        assert (public.status_code, public.json()) == (200, {"status": "ok"})
        assert admin.get("/admin/health").json()["stale"] == []

        # The backup is past its 26 hours and the cron past its 2: both named for the admin,
        # and the public body is still the status word and nothing else.
        beat(timedelta(hours=27), timedelta(hours=3))
        public = anonymous.get("/healthz/ops")
        assert (public.status_code, public.json()) == (503, {"status": "stale"})
        detail = admin.get("/admin/health")
        assert detail.status_code == 503 and detail.json()["stale"] == ["backup", "cron"]
        row = admin.get("/admin").text.split('id="ops-heading"')[1].split("</section>")[0]
        assert "backup" in row and "cron" in row

        holder = sqlite3.connect(tmp_path / "web.db", isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        try:
            for web in (anonymous, admin):
                locked = seen(web)
                assert locked["/healthz"][0] == 200
                assert locked["/healthz/ops"][0] == 503 and locked["/status"][0] == 200
                assert locked["/readyz"][0] == 503
                assert locked["/readyz"][1] < 1.5  # the 1 s write limit, and the request around it
            assert admin.get("/admin/health").json()["stale"] == ["backup", "cron"]
            assert admin.get("/admin").status_code == 200
            assert anonymous.get("/healthz/ops").json() == {"status": "stale"}
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        started = time.monotonic()
        released = anonymous.get("/readyz")
        assert released.status_code == 200 and time.monotonic() - started < 1
        assert seen(anonymous)["/healthz"][0] == 200


def test_readyz_without_any_key_is_not_ready_unless_the_models_are_fake(tmp_path):
    _env, client = running(tmp_path)
    with client as web:
        assert web.get("/readyz").status_code == 503
    _env, client = running(tmp_path / "f", fake_models=True)
    with client as web:
        assert web.get("/readyz").json()["checks"]["model_provider"] == "fake"


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


def test_with_the_worker_as_its_own_service_the_web_process_reads_its_rows(tmp_path):
    """WS9.4. `WORKER_MODE=service`: the web process starts no worker and a queued job waits;
    `worker_service.run` (the `readablestem worker` command) converts it; `/readyz` and `/status`
    answer from the worker's `ops_heartbeat` rows, and `/readyz` is not ready once they are old."""
    env = WebEnv(tmp_path, openai_api_key=SECRET_KEY, worker_mode="service")
    with env.running() as web:
        env.allow("alice@example.org")
        login(web, "alice@example.org")
        job_id = job_id_from(upload(web, make_pdf(1)))
        assert env.ctx.worker is None
        assert web.get("/readyz").json()["checks"]["worker"] == "not_started"
        assert env.job(job_id).status == "queued"

        stop = threading.Event()
        service = threading.Thread(
            target=worker_service.run,
            args=(env.settings,),
            kwargs={
                "stop": stop,
                "converter": env.converter,
                "client_factory": fake_client_factory,
            },
        )
        service.start()
        try:
            deadline = time.monotonic() + 30
            while env.job(job_id).status != "done" and time.monotonic() < deadline:
                time.sleep(0.2)
            assert env.job(job_id).status == "done"
            ready = web.get("/readyz")
            assert ready.status_code == 200 and ready.json()["checks"]["worker"] == "ok"
            worker_rows.write(
                env.ctx.factory,
                worker_rows.PROVIDER,
                {"calls": [[time.time(), 0], [time.time(), 1]]},
            )
            assert "1 of 2 requests" in web.get("/status").text
        finally:
            stop.set()
            service.join(30)
        assert not service.is_alive()
        assert web.get("/readyz").json()["checks"]["worker"] == "stopping"
        with session_scope(env.ctx.factory) as session:  # nothing has beaten for a few minutes
            session.execute(
                update(OpsHeartbeat)
                .where(OpsHeartbeat.name.like("worker.%"))
                .values(last_ok_at=utcnow() - timedelta(minutes=5))
            )
        gone = web.get("/readyz")
        assert gone.status_code == 503 and gone.json()["checks"]["worker"] == "stopped"


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
            [sys.executable, "-m", "readablestem.cli.main", "serve", "--port", str(port)],
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
