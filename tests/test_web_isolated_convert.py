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
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from fixtures.web.support import ALICE, WebEnv, make_pdf, make_settings
from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.models import make_vision_client
from readablestem.pipeline import run as run_module
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
    "model": offline_settings().main_model,
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
        {"model": "gpt-not-configured"},
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
    assert time.monotonic() - started < 120  # (a hang is an hour: only "not waited out")
    assert spawned.all_gone()


def test_a_hanging_child_is_killed_at_the_time_limit(spawned, monkeypatch):
    monkeypatch.setattr(isolated_convert, "DEADLINE_SLACK_S", 0.0)
    settings = offline_settings()
    client, _ = offline_client(settings)
    started = time.monotonic()
    with pytest.raises(ConversionTimeout) as caught:
        convert_isolated(b"%PDF-1.4", settings=settings, client=client, limit_s=1.5, fault="hang")
    assert error_code_for(caught.value) == "timeout"
    assert 1.4 < time.monotonic() - started < 120
    assert spawned.all_gone()


def test_the_child_cannot_use_more_than_its_memory_cap(spawned):
    """A real allocation past the cap, not a stand-in: `conversion_memory_mb` is enforced."""
    settings = offline_settings(conversion_memory_mb=1536)
    client, _ = offline_client(settings)
    with pytest.raises(ConversionOom):
        convert_isolated(b"%PDF-1.4", settings=settings, client=client, fault="oom")


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
    assert time.monotonic() - started < 120
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
    deadline = time.monotonic() + 120
    while not spawned.processes and time.monotonic() < deadline:
        time.sleep(0.05)
    assert spawned.processes
    started = time.monotonic()
    worker.stop(timeout=1)  # what SIGTERM does
    runner.join(60)
    assert not runner.is_alive()
    assert time.monotonic() - started < 60  # (the child hangs for an hour)
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
    deadline = time.monotonic() + 120
    while not spawned.processes and time.monotonic() < deadline:
        time.sleep(0.05)
    with session_scope(env.ctx.factory) as session:
        session.delete(session.get(Job, job_id))
    runner.join(120)  # the heartbeat finds the job gone, cancels, and the child is killed
    assert not runner.is_alive()
    assert spawned.all_gone()


def test_a_child_flooding_the_parent_with_requests_is_refused(monkeypatch):
    release = threading.Event()
    c = conversion()
    c.client = type("Slow", (), {"run": lambda *a, **k: release.wait(5)})()  # type: ignore[assignment]
    c._reply = lambda *a, **k: None  # type: ignore[method-assign]
    frame = [json.dumps(MODEL_HEADER).encode()]
    try:
        for _ in range(c.max_in_flight):
            c._handle(frame)
        with pytest.raises(_Protocol, match="flood"):
            c._handle(frame)
    finally:
        release.set()


@pytest.mark.parametrize("code", ["conversion_crashed", "conversion_oom"])
def test_try_again_is_not_offered_for_a_file_that_crashed_or_exhausted_its_child(code):
    from readablestem.web import retry

    assert code not in retry.RETRYABLE_FAILURES
    assert retry.offer(status="failed", error_code=code, result_json=None, retries=0) is None


# --------------------------------------------------------------------------- review fixes


def test_an_oversized_done_frame_fails_the_call_as_a_crash_and_is_logged(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(protocol, "MAX_FRAME_BYTES", 50_000)  # a real result is larger
    settings = offline_settings()
    client, _ = offline_client(settings)
    with pytest.raises(ConversionCrashed):
        convert_isolated(LECTURE_NOTES.read_bytes(), settings=settings, client=client)
    assert any(r.getMessage() == "conversion_child_frame_refused" for r in caplog.records)


def test_what_is_held_of_the_childs_output_is_bounded_in_bytes(monkeypatch):
    monkeypatch.setattr(isolated_convert, "HELD_BYTES", 100)
    c = conversion()
    assert c._admit(80)  # nothing held: admitted
    results: list[bool] = []
    waiter = threading.Thread(target=lambda: results.append(c._admit(60)))
    waiter.start()
    waiter.join(0.7)
    assert waiter.is_alive() and not results  # 80 + 60 > 100: waits
    c._release(80)
    waiter.join(5)
    assert results == [True] and c.held == 60
    c.closed.set()
    assert c._admit(1000) is False


def test_a_sigkill_from_outside_is_run_once_more_and_then_called_out_of_memory(monkeypatch):
    runs: list[str | None] = []
    real = isolated_convert._Conversion

    def conversion_killed_each_time(*args, **kwargs):
        runs.append(kwargs["fault"])
        return real(*args, **{**kwargs, "fault": "kill9"})

    monkeypatch.setattr(isolated_convert, "_Conversion", conversion_killed_each_time)
    settings = offline_settings()
    client, _ = offline_client(settings)
    with pytest.raises(ConversionOom) as caught:
        convert_isolated(b"%PDF-1.4", settings=settings, client=client)
    assert isinstance(caught.value, isolated_convert.ConversionExternalKill)
    assert error_code_for(caught.value) == "conversion_oom" and len(runs) == 2


@pytest.mark.parametrize("status", [1, protocol.EXIT_BROKEN])
def test_a_child_that_cannot_start_is_unavailable_not_a_bad_pdf(monkeypatch, caplog, status):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(
        isolated_convert,
        "child_command",
        lambda fd, memory, cpu: [sys.executable, "-c", f"import sys; sys.exit({status})"],
    )
    settings = offline_settings()
    client, _ = offline_client(settings)
    with pytest.raises(isolated_convert.ConversionUnavailable) as caught:
        convert_isolated(b"%PDF-1.4", settings=settings, client=client)
    assert error_code_for(caught.value) == "conversion_unavailable"
    assert any(r.getMessage() == "conversion_child_unavailable" for r in caplog.records)


def test_a_startup_error_frame_is_unavailable_and_logs_only_the_type(caplog):
    caplog.set_level(logging.INFO)
    c = conversion()
    with pytest.raises(isolated_convert.ConversionUnavailable):
        c._handle([b'{"op": "error", "kind": "startup", "type": "ModuleNotFoundError"}'])
    line = next(r for r in caplog.records if r.getMessage() == "conversion_child_unavailable")
    fields = getattr(line, "fields", None) or next(
        v for k, v in vars(line).items() if isinstance(v, dict) and "error_type" in v
    )
    assert line.levelno == logging.ERROR and fields["error_type"] == "ModuleNotFoundError"


def test_the_self_check_starts_a_child_and_reports_when_it_cannot(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    assert isolated_convert.self_check() is True
    monkeypatch.setattr(isolated_convert.sys, "executable", "/bin/false")
    assert isolated_convert.self_check() is False
    assert any(r.getMessage() == "conversion_child_check_failed" for r in caplog.records)


def run_python(code: str) -> str:
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
        env=child_environment(),
    )
    return done.stdout.strip()


def test_the_child_asks_the_kernel_to_kill_it_before_the_web_process():
    code = (
        "from readablestem.web import conversion_child as c\n"
        "c._harden(8 << 30, 0)\n"
        "print(open('/proc/self/oom_score_adj').read().strip())"
    )
    assert run_python(code) == "1000"


def test_a_failed_start_still_cleans_up(monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("no processes")

    monkeypatch.setattr(isolated_convert.subprocess, "Popen", broken)
    settings = offline_settings()
    client, _ = offline_client(settings)
    with pytest.raises(OSError, match="no processes"):
        convert_isolated(b"x", settings=settings, client=client)


class FatalWhileOthersRun:
    """The offline client where the first `n` requests are still being answered when the
    others fail with a configuration error. Those `n` are held until `release`, so the run
    always sees the error first and the answers later, whatever the machine's speed. A
    failing request first waits for `gate` if there is one; `delay` releases the held ones
    that long after the first failure (when the test cannot see the run take in the error)."""

    def __init__(
        self,
        inner,
        n: int,
        *,
        gate: threading.Event | None = None,
        delay: float | None = None,
    ) -> None:
        self.inner, self.n, self.asked, self.gate, self.delay = inner, n, 0, gate, delay
        self.lock = threading.Lock()
        self.release = threading.Event()
        self.timer_started = False

    def run(self, request, schema, **kwargs):
        from readablestem.config import ConfigError

        with self.lock:
            self.asked += 1
            order = self.asked
        if order <= self.n:  # (bounded waits: a bug must not hang the test)
            assert self.release.wait(timeout=30)
        elif not self.release.is_set():
            if self.gate is not None:
                assert self.gate.wait(timeout=30)
            with self.lock:
                start, self.timer_started = not self.timer_started, True
            if start and self.delay is not None:
                threading.Timer(self.delay, self.release.set).start()
            raise ConfigError("the model is not set up")
        return self.inner.run(request, schema, **kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)


@pytest.mark.parametrize("where", ["scheduler", "between-crops", "child"])
def test_steps_that_finished_before_a_configuration_error_are_kept_for_the_next_try(
    where, monkeypatch
):
    """Two steps are in flight when a third fails with a configuration error, and finish
    after the run has learned of it: the run learns of it in the scheduler's wait for a
    finished step, or between two crops, or (the pipeline in a child process) a model
    reply of the parent; in each the two are kept."""
    from readablestem.config import ConfigError

    stored: list[str] = []

    class Cache:
        def lookup(self, element, key):
            return None

        def store(self, element, key, fix):
            stored.append(element.id)

    settings = offline_settings()
    client, _ = offline_client(settings)
    gate = threading.Event() if where == "scheduler" else None
    wrapper = FatalWhileOthersRun(client, 2, gate=gate, delay=0.5 if where == "child" else None)
    if where == "scheduler":
        # The error is raised once the scheduler waits for a step, and the two finish once
        # it has the failed one in hand.
        real_wait = run_module.wait

        def wait(*args, **kwargs):
            if kwargs.get("timeout", 0) is None:
                assert gate is not None
                gate.set()
            done = real_wait(*args, **kwargs)
            if any(not f.cancelled() and f.exception() for f in done[0]):
                wrapper.release.set()
            return done

        monkeypatch.setattr(run_module, "wait", wait)
    elif where == "between-crops":
        # The scheduler is not told: the next crop finds the error, then the two finish.
        real_keep = run_module._Run.keep_finished

        def keep_finished(self):
            wrapper.release.set()
            return real_keep(self)

        monkeypatch.setattr(run_module._Run, "keep_finished", keep_finished)
    try:
        with pytest.raises(ConfigError):
            (convert_isolated if where == "child" else convert_pdf)(
                LECTURE_NOTES.read_bytes(),
                settings=settings,
                client=wrapper,
                step_cache=Cache(),
            )
    finally:
        wrapper.release.set()
    assert wrapper.asked > 2
    assert len(stored) >= 2  # what was answered (and paid for) before the error is kept


# --------------------------------------------------------------------------- a full-size job


def test_a_full_size_document_converts_inside_the_default_memory_cap_time_and_job_caps():
    """100 pages (`MAX_PAGES_PER_JOB`), as dense as the repository's own fixtures, converted in
    the child under the default address-space cap, with the call and dollar caps a job of that
    size really gets (`job_caps`): no budget stop, every page done, well inside the job's time
    limit. Measured 2026-11-03 on one CPU: 31 s, 494 MB resident, 652 calls (docs/web.md)."""
    import time

    import pymupdf

    from readablestem.web.worker import job_caps, job_time_limit_s

    fixtures = LECTURE_NOTES.parent
    sources = [pymupdf.open(fixtures / n) for n in ("lecture_notes.pdf", "worked_example.pdf")]
    doc = pymupdf.open()
    cursor = 0
    while doc.page_count < 100:
        src = sources[cursor % 2]
        page = (cursor // 2) % src.page_count
        doc.insert_pdf(src, from_page=page, to_page=page)
        cursor += 1
    pdf = doc.tobytes()
    settings = offline_settings()
    assert settings.max_pages_per_job == 100 and settings.conversion_memory_mb == 3072
    usd, calls = job_caps(settings, 100)
    budget = JobBudget(guard=InMemorySpendGuard(100.0), max_usd=usd, max_calls=calls)
    client = make_vision_client(settings, budget, fake=True)
    seen: list[tuple[int, int]] = []
    started = time.perf_counter()
    out = convert_isolated(
        pdf,
        settings=settings,
        client=client,
        limit_s=job_time_limit_s(100, settings),
        progress=lambda done, total: seen.append((done, total)),
    )
    elapsed = time.perf_counter() - started
    assert out.result.stopped_by is None
    assert seen and seen[-1] == (100, 100)
    assert out.result.stats.model_calls > 0
    assert elapsed < job_time_limit_s(100, settings) / 10
