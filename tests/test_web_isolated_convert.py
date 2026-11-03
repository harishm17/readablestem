"""A conversion runs in a child process the worker can kill (web/isolated_convert.py).

A crash, an out of memory or a hang in the child fails that job and nothing else; a normal
conversion gives what `convert_pdf` gives, and spends what it spends.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import socket
import subprocess
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from fixtures.web.support import ALICE, WebEnv, make_pdf, make_settings
from readablestem.config import Settings
from readablestem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from readablestem.core.types import ConversionOutput
from readablestem.models import make_vision_client
from readablestem.pipeline.layout import LayoutError
from readablestem.pipeline.run import convert_pdf
from readablestem.web import conversion_child as protocol
from readablestem.web import isolated_convert
from readablestem.web.db import Blob, Job, User, session_scope, utcnow
from readablestem.web.isolated_convert import (
    ConversionCrashed,
    ConversionOom,
    ConversionTimeout,
    _Conversion,
    _Protocol,
    child_environment,
    convert_isolated,
    resolve_schema,
    settings_for_child,
)
from readablestem.web.views import job_error_message
from readablestem.web.worker import Worker, default_converter, error_code_for

LECTURE_NOTES = Path(__file__).parent / "fixtures" / "layout" / "lecture_notes.pdf"


def offline_settings(**overrides) -> Settings:
    return Settings(_env_file=None, env="dev", fake_models=True, **overrides)  # type: ignore[call-arg]


def offline_client(settings: Settings, calls: int = 1000):
    budget = JobBudget(guard=InMemorySpendGuard(100.0), max_usd=50.0, max_calls=calls)
    return make_vision_client(settings, budget, fake=True), budget


class Spawned:
    """Every child `convert_isolated` starts, to look at after the call."""

    def __init__(self, monkeypatch) -> None:
        self.processes: list[subprocess.Popen] = []
        real = subprocess.Popen

        def popen(*args, **kwargs):
            process = real(*args, **kwargs)
            self.processes.append(process)
            return process

        monkeypatch.setattr(isolated_convert.subprocess, "Popen", popen)

    def all_gone(self) -> bool:
        gone = True
        for process in self.processes:
            gone &= process.poll() is not None
            try:
                os.killpg(process.pid, 0)  # its process group, the render probes too
                gone = False
            except ProcessLookupError:
                pass
        return gone


@pytest.fixture
def spawned(monkeypatch) -> Spawned:
    return Spawned(monkeypatch)


def without_timings(value):
    if isinstance(value, dict):
        return {k: without_timings(v) for k, v in value.items() if k != "latency_ms"}
    if isinstance(value, list):
        return [without_timings(v) for v in value]
    return value


# --------------------------------------------------------------------------- the protocol


def test_frames_round_trip_and_a_closed_end_is_the_end():
    a, b = socket.socketpair()
    with a, b:
        protocol.write_frame(a, [b"head", b"", b"x" * 100_000])
        assert protocol.read_frame(b) == [b"head", b"", b"x" * 100_000]
        a.close()
        assert protocol.read_frame(b) is None


def test_a_frame_cut_short_is_the_end_and_an_oversized_one_is_refused():
    a, b = socket.socketpair()
    with a, b:
        a.sendall(protocol._COUNT.pack(1) + protocol._LENGTH.pack(50) + b"short")
        a.shutdown(socket.SHUT_WR)
        assert protocol.read_frame(b) is None
    a, b = socket.socketpair()
    with a, b:
        protocol.write_frame(a, [b"x" * 100])
        with pytest.raises(ValueError, match="too large"):
            protocol.read_frame(b, limit=10)
    a, b = socket.socketpair()
    with a, b:
        a.sendall(protocol._COUNT.pack(protocol.MAX_PARTS + 1))
        with pytest.raises(ValueError, match="too many"):
            protocol.read_frame(b)


def test_the_child_gets_no_secret_and_not_the_database():
    settings = make_settings(
        Path("."),
        openai_api_key="sk-secret",
        anthropic_api_key="sk-ant-secret",
        session_secret="s" * 40,
        cron_token="c" * 40,
        database_url="postgresql://user:password@host/db",
    )
    values = settings_for_child(settings)
    assert "database_url" not in values and "admin_emails" not in values
    text = repr(values)
    for secret in ("sk-secret", "sk-ant-secret", "s" * 40, "c" * 40, "password"):
        assert secret not in text
    assert values["max_pages_per_job"] == settings.max_pages_per_job  # the rest is carried


def test_the_child_environment_has_no_keys_and_does_say_where_the_table_model_is(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@host/db")
    monkeypatch.setenv("TABLE_MODEL_DIR", "/app/models")
    env = child_environment()
    assert "OPENAI_API_KEY" not in env and "DATABASE_URL" not in env
    assert env["TABLE_MODEL_DIR"] == "/app/models"
    assert env["MALLOC_ARENA_MAX"] == "2"


def test_a_request_may_name_only_a_pydantic_model_of_this_package():
    assert resolve_schema("readablestem.pipeline.equation_step:EquationOutput")
    for name in (
        "os:system",
        "builtins:dict",
        "readablestem.core.types:ModelCall.__init__",
        "readablestem.pipeline.run:convert_pdf",
        "readablestem.pipeline.equation_step:<locals>.X",
        "readablestem.pipeline.equation_step",
    ):
        with pytest.raises((_Protocol, AttributeError)):
            resolve_schema(name)


def conversion(**overrides) -> _Conversion:
    settings = offline_settings()
    client, _ = offline_client(settings)
    values = dict(
        settings=settings,
        client=client,
        pages=None,
        progress=None,
        concurrency=1,
        notice=None,
        source_note=None,
        options=None,
        stage_progress=None,
        step_cache=None,
        call_observer=None,
        cancelled=None,
        limit_s=None,
        fault=None,
        fault_at="start",
    )
    values.update(overrides)
    return _Conversion(b"%PDF-", **values)  # type: ignore[arg-type]


MODEL_HEADER = {
    "op": "model",
    "id": 1,
    "step": "equation",
    "model": "m",
    "prompt_version": "v",
    "instructions": "i",
    "context_text": None,
    "max_output_tokens": 100,
    "image_detail": "high",
    "context_limit": None,
    "effort": None,
    "timeout_s": None,
    "schema": "readablestem.pipeline.equation_step:EquationOutput",
    "is_fallback": False,
}


@pytest.mark.parametrize(
    "header",
    [
        b"not json",
        b"[]",
        b'{"op": "format_disk"}',
        b'{"op": "progress", "id": 1, "done": "1", "total": 2}',
        b'{"op": "stage", "id": 1, "event": {"stage": "nonsense"}}',
        b'{"op": "done", "result": {}, "checks": [], "crop_ids": []}',
        b'{"op": "done", "result": {}, "checks": [], "crop_ids": ["a"]}',
    ],
)
def test_a_frame_that_is_not_the_protocol_is_refused(header):
    c = conversion(progress=lambda *_: None, stage_progress=lambda _e: None)
    with pytest.raises(_Protocol):
        c._handle([header])


@pytest.mark.parametrize(
    "changes",
    [
        {"step": "rm -rf"},
        {"image_detail": "ultra"},
        {"max_output_tokens": 10**9},
        {"max_output_tokens": True},
        {"model": 5},
        {"timeout_s": "soon"},
        {"schema": "os:system"},
    ],
)
def test_a_model_request_outside_the_bounds_is_answered_with_an_error_not_sent(changes):
    sent: list = []
    c = conversion()
    c.client = type("Never", (), {"run": lambda *a, **k: sent.append(a)})()  # type: ignore[assignment]
    replies: list = []
    c._reply = lambda ident, **kw: replies.append((ident, kw))  # type: ignore[method-assign]
    c._model({**MODEL_HEADER, **changes}, [])
    assert not sent
    assert replies and replies[0][0] == 1 and "error" in replies[0][1]


def test_a_model_request_with_too_many_images_is_not_sent():
    c = conversion()
    c.client = type("Never", (), {"run": lambda *a, **k: pytest.fail("sent")})()  # type: ignore[assignment]
    replies: list = []
    c._reply = lambda ident, **kw: replies.append(kw)  # type: ignore[method-assign]
    c._model(MODEL_HEADER, [b"x"] * (isolated_convert.MAX_IMAGES_PER_REQUEST + 1))
    assert "error" in replies[0]


# --------------------------------------------------------------------------- normal runs


def test_a_conversion_in_the_child_gives_what_convert_pdf_gives_and_spends_the_same():
    pdf = LECTURE_NOTES.read_bytes()
    settings = offline_settings(feature_tables=True)
    own_client, own_budget = offline_client(settings)
    kid_client, kid_budget = offline_client(settings)
    own = convert_pdf(pdf, settings=settings, client=own_client)
    kid = convert_isolated(pdf, settings=settings, client=kid_client)
    assert without_timings(kid.result.model_dump(mode="json")) == without_timings(
        own.result.model_dump(mode="json")
    )
    assert kid.crops == own.crops and kid.crops
    assert kid.result.html == own.result.html
    assert kid.result.stats.model_calls == own.result.stats.model_calls > 0
    # Spend is reserved and settled in this process, on the budget this process holds.
    assert own_budget.spent_usd > 0
    assert kid_budget.spent_usd == pytest.approx(own_budget.spent_usd)
    assert kid_budget.calls == own_budget.calls


def test_the_callbacks_run_here_and_the_cache_and_observer_are_the_parents(spawned):
    from readablestem.core.types import Element, Fix, ModelCall

    seen = {"progress": [], "stages": [], "lookups": 0, "stored": [], "calls": []}

    class Cache:
        def lookup(self, element: Element, key: str) -> Fix | None:
            seen["lookups"] += 1
            return None

        def store(self, element: Element, key: str, fix: Fix) -> None:
            seen["stored"].append((element.id, key))

    settings = offline_settings()
    client, _ = offline_client(settings)
    out = convert_isolated(
        LECTURE_NOTES.read_bytes(),
        settings=settings,
        client=client,
        progress=lambda done, total: seen["progress"].append((done, total)),
        stage_progress=lambda event: seen["stages"].append(event.stage),
        step_cache=Cache(),
        call_observer=lambda call: seen["calls"].append(call),
    )
    assert seen["progress"] and seen["progress"][-1][0] == seen["progress"][-1][1]
    assert seen["stages"][0] == "layout" and "assemble" in seen["stages"]
    assert seen["lookups"] > 0 and seen["stored"]
    assert seen["calls"] and all(isinstance(c, ModelCall) for c in seen["calls"])
    assert len(seen["calls"]) == out.result.stats.model_calls
    assert spawned.all_gone()  # the child does not outlive the call


def test_an_error_the_pipeline_raises_arrives_as_the_same_error():
    settings = offline_settings()
    client, _ = offline_client(settings)
    with pytest.raises(LayoutError) as caught:
        convert_isolated(b"", settings=settings, client=client)
    assert caught.value.code == "unreadable"
    with pytest.raises(LayoutError) as caught:
        convert_isolated(make_pdf(3), settings=offline_settings(max_pages_per_job=2), client=client)
    assert caught.value.code == "too_many_pages"


def test_a_spent_budget_arrives_in_the_child_as_a_budget_error_and_the_run_still_ends():
    settings = offline_settings()
    client, _ = offline_client(settings, calls=1)  # the second model call is refused
    out = convert_isolated(LECTURE_NOTES.read_bytes(), settings=settings, client=client)
    reasons = {f.reason for f in out.result.fixes if f.reason}
    assert "budget_job_calls" in reasons


# --------------------------------------------------------------------------- a bad child


DEATHS = [
    ("abort", ConversionCrashed),
    ("segv", ConversionCrashed),
    ("exit", ConversionCrashed),
    ("oom", ConversionOom),
    ("kill9", ConversionOom),
]
# (A MemoryError inside a model step is that step's failure, as it is in process: "oom" is
# tried where it ends the run.)
DEATH_CASES = [
    (fault, at, error)
    for fault, error in DEATHS
    for at in ("start", "progress", "model")
    if (fault, at) != ("oom", "model")
]


@pytest.mark.parametrize(("fault", "at", "error"), DEATH_CASES)
def test_a_child_that_dies_fails_the_call_and_not_the_process(spawned, fault, at, error):
    settings = offline_settings()
    client, _ = offline_client(settings)
    started = time.monotonic()
    with pytest.raises(error) as caught:
        convert_isolated(
            LECTURE_NOTES.read_bytes(),
            settings=settings,
            client=client,
            progress=lambda *_: None,
            fault=fault,
            fault_at=at,
        )
    assert caught.value.code == error.code == error_code_for(caught.value)
    assert time.monotonic() - started < 30
    assert spawned.all_gone()


def test_a_hanging_child_is_killed_at_the_time_limit(spawned, monkeypatch):
    monkeypatch.setattr(isolated_convert, "DEADLINE_SLACK_S", 0.0)
    settings = offline_settings()
    client, _ = offline_client(settings)
    started = time.monotonic()
    with pytest.raises(ConversionTimeout) as caught:
        convert_isolated(b"%PDF-1.4", settings=settings, client=client, limit_s=1.5, fault="hang")
    assert error_code_for(caught.value) == "timeout"
    assert 1.4 < time.monotonic() - started < 10
    assert spawned.all_gone()


def test_a_cancelled_attempt_kills_the_child_at_once(spawned):
    settings = offline_settings()
    client, _ = offline_client(settings)
    flag = threading.Event()
    threading.Timer(1.0, flag.set).start()
    started = time.monotonic()
    with pytest.raises(BudgetExceeded) as caught:
        convert_isolated(
            b"%PDF-1.4", settings=settings, client=client, cancelled=flag.is_set, fault="hang"
        )
    assert caught.value.scope == "cancelled"
    assert time.monotonic() - started < 5
    assert spawned.all_gone()


def test_a_progress_callback_that_raises_kills_the_child_and_the_error_is_raised_here(spawned):
    class Gone(BudgetExceeded):
        def __init__(self) -> None:
            super().__init__("cancelled", "gone")

    def progress(done: int, total: int) -> None:
        raise Gone

    settings = offline_settings()
    client, _ = offline_client(settings)
    with pytest.raises(Gone):
        convert_isolated(
            LECTURE_NOTES.read_bytes(), settings=settings, client=client, progress=progress
        )
    assert spawned.all_gone()


def test_the_child_cannot_use_more_than_its_memory_cap(spawned):
    """A real allocation past the cap, not a stand-in: `conversion_memory_mb` is enforced."""
    settings = offline_settings(conversion_memory_mb=1024)
    client, _ = offline_client(settings)
    with pytest.raises(ConversionOom):
        convert_isolated(b"%PDF-1.4", settings=settings, client=client, fault="oom")


def test_an_unknown_fault_is_refused_before_anything_starts():
    settings = offline_settings()
    client, _ = offline_client(settings)
    with pytest.raises(ValueError, match="unknown fault"):
        convert_isolated(b"x", settings=settings, client=client, fault="format_disk")


# --------------------------------------------------------------------------- on the worker


@pytest.fixture
def env(tmp_path):
    env = WebEnv(tmp_path)
    with env.running():
        env.allow(ALICE)
        yield env


def queue_job(env, *, age_s: float = 0.0) -> str:
    with session_scope(env.ctx.factory) as session:
        user = session.scalar(select(User).where(User.email == ALICE))
        assert user is not None
        now = utcnow() - timedelta(seconds=age_s)
        job = Job(
            user_id=user.id,
            filename="notes.pdf",
            page_count=1,
            retain_until=now + timedelta(hours=24),
            created_at=now,
        )
        session.add(job)
        session.flush()
        session.add(
            Blob(
                job_id=job.id,
                kind="upload",
                media_type="application/pdf",
                content=LECTURE_NOTES.read_bytes(),
            )
        )
        return job.id


def offline_factory(settings, budget):
    return make_vision_client(settings, budget, fake=True)


def worker_with(env, **kwargs) -> Worker:
    return Worker(
        env.settings,
        env.ctx.factory,
        converter=kwargs.pop("converter", default_converter()),
        client_factory=offline_factory,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("fault", "code"),
    [("abort", "conversion_crashed"), ("oom", "conversion_oom")],
)
def test_a_poison_pdf_fails_its_job_at_once_and_the_next_job_converts(env, fault, code):
    poisoned = queue_job(env, age_s=10)
    good = queue_job(env)
    converter = functools.partial(convert_isolated, fault=fault, fault_at="progress")
    assert worker_with(env, converter=converter).run_once() == poisoned
    job = env.job(poisoned)
    assert job.status == "failed" and job.error_code == code
    assert job.attempts == 1  # not requeued, not tried three times
    assert job.lease_until is None and job.finished_at is not None
    assert job_error_message(code) != job_error_message("no such code")
    # The web process is still here, and the queue goes on.
    assert worker_with(env).run_once() == good
    assert env.job(good).status == "done"
    assert env.worker().requeue_expired() == (0, 0)


def test_a_hanging_child_fails_the_job_as_a_timeout_without_an_abandoned_thread(
    env, spawned, caplog
):
    caplog.set_level(logging.INFO)
    job_id = queue_job(env)
    converter = functools.partial(convert_isolated, fault="hang", fault_at="progress")
    worker = worker_with(env, converter=converter, max_job_s=2.0, timeout_grace_s=10.0)
    started = time.monotonic()
    assert worker.run_once() == job_id
    assert time.monotonic() - started < 20
    job = env.job(job_id)
    assert job.status == "failed" and job.error_code == "timeout"
    assert spawned.all_gone()
    messages = [r.getMessage() for r in caplog.records]
    assert "job_timeout" in messages and "conversion_abandoned" not in messages


def test_shutdown_kills_the_child_and_leaves_the_job_for_the_next_worker(env, spawned):
    job_id = queue_job(env)
    converter = functools.partial(convert_isolated, fault="hang", fault_at="progress")
    worker = worker_with(env, converter=converter, lease_s=60)
    runner = threading.Thread(target=worker.run_once)
    runner.start()
    deadline = time.monotonic() + 20
    while not spawned.processes and time.monotonic() < deadline:
        time.sleep(0.05)
    assert spawned.processes
    started = time.monotonic()
    worker.stop(timeout=1)  # what SIGTERM does
    runner.join(10)
    assert not runner.is_alive()
    assert time.monotonic() - started < 5  # well inside the seconds a deploy gives
    assert spawned.all_gone()
    job = env.job(job_id)
    lease = job.lease_until
    assert job.status == "running" and lease is not None
    assert env.worker().requeue_expired() == (1, 0)  # requeued at once, as before
    assert env.job(job_id).status == "queued"


def test_deleting_a_running_job_kills_its_child(env, spawned):
    job_id = queue_job(env)
    converter = functools.partial(convert_isolated, fault="hang", fault_at="progress")
    worker = worker_with(env, converter=converter, lease_s=3.0)
    runner = threading.Thread(target=worker.run_once)
    runner.start()
    deadline = time.monotonic() + 20
    while not spawned.processes and time.monotonic() < deadline:
        time.sleep(0.05)
    with session_scope(env.ctx.factory) as session:
        session.delete(session.get(Job, job_id))
    runner.join(30)  # the heartbeat finds the job gone, cancels, and the child is killed
    assert not runner.is_alive()
    assert spawned.all_gone()


def test_the_worker_hands_the_converter_its_cancel_flag_and_time_limit(env):
    seen: dict = {}

    def converter(pdf_bytes, /, *, settings, client, cancelled=None, limit_s=None, **_):
        seen.update(cancelled=cancelled, limit_s=limit_s)
        return env.converter.output.model_copy(deep=True)

    queue_job(env)
    worker_with(env, converter=converter).run_once()
    assert callable(seen["cancelled"]) and seen["cancelled"]() is False
    assert seen["limit_s"] == pytest.approx(worker_with(env).time_limit_s(1))


def test_the_default_converter_is_the_isolated_one_and_the_codes_are_explained():
    assert default_converter() is convert_isolated
    for code in ("conversion_crashed", "conversion_oom"):
        assert job_error_message(code) != job_error_message("no such code")
    assert error_code_for(ConversionCrashed("x")) == "conversion_crashed"
    assert error_code_for(ConversionOom("x")) == "conversion_oom"
    assert error_code_for(ConversionTimeout("x")) == "timeout"


def test_converting_through_the_child_stores_the_same_job_as_in_process(env):
    """The worker's own path, offline: result, crops and the job's spend are what they are
    with the converter in this process."""
    inside = queue_job(env)
    outside = queue_job(env, age_s=10)
    assert worker_with(env, converter=convert_pdf).run_once() == outside
    assert worker_with(env).run_once() == inside
    a, b = env.job(outside), env.job(inside)
    assert a.status == b.status == "done"
    assert a.model_calls == b.model_calls and a.cost_usd == pytest.approx(b.cost_usd)
    assert a.html == b.html

    def crops(job_id: str) -> dict:
        with session_scope(env.ctx.factory) as session:
            rows = session.scalars(select(Blob).where(Blob.job_id == job_id, Blob.kind == "crop"))
            return {r.element_id: r.content for r in rows}

    assert crops(outside) == crops(inside) and crops(inside)


def test_a_conversion_output_from_the_child_is_a_conversion_output():
    settings = offline_settings()
    client, _ = offline_client(settings)
    out = convert_isolated(LECTURE_NOTES.read_bytes(), settings=settings, client=client)
    assert isinstance(out, ConversionOutput) and out.result.html


def test_a_child_flooding_the_parent_with_requests_is_refused(monkeypatch):
    release = threading.Event()
    c = conversion()
    c.client = type("Slow", (), {"run": lambda *a, **k: release.wait(5)})()  # type: ignore[assignment]
    c._reply = lambda *a, **k: None  # type: ignore[method-assign]
    frame = [json.dumps(MODEL_HEADER).encode()]
    try:
        for _ in range(isolated_convert.MAX_IN_FLIGHT):
            c._handle(frame)
        with pytest.raises(_Protocol, match="flood"):
            c._handle(frame)
    finally:
        release.set()


def test_the_settings_the_child_is_built_from_are_the_parents_but_for_the_secrets(tmp_path):
    settings = make_settings(tmp_path, feature_tables=True, max_pages_per_job=7, main_model="x")
    rebuilt = Settings.model_construct(**settings_for_child(settings))
    skipped = {
        "database_url",
        "admin_emails",
        "email_sender",
        "langfuse_public_key",
        "langfuse_host",
    }
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if name in skipped or isinstance(value, SecretStr):
            continue
        assert getattr(rebuilt, name) == value, name


@pytest.mark.parametrize("code", ["conversion_crashed", "conversion_oom"])
def test_try_again_is_not_offered_for_a_file_that_crashed_or_exhausted_its_child(code):
    from readablestem.web import retry

    assert code not in retry.RETRYABLE_FAILURES
    assert retry.offer(status="failed", error_code=code, result_json=None, retries=0) is None
