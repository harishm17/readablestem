"""Reproducible before/after measurements for the runtime and web efficiency work.

Run with `uv run python scripts/bench_runtime.py SUBCOMMAND [options]`. Nothing here calls
a real model or needs a key: model calls are the offline client with a simulated latency
(`--latency`, seconds, +/-30% in a fixed pattern), so a run is repeatable and costs nothing.
Numbers depend on the machine; compare runs on the same one. Each subcommand prints a few
lines and, with `--json PATH`, writes them as JSON.

    convert    one conversion at several concurrencies, with the time before the first
               model call and the time after the last one
    first      the first conversion after a start, with the table models loaded while
               idle or not
    spend      database statements and transactions per model call and per idle worker poll
    documents  database work of one rebuild of My documents with N finished jobs
    review     CPU of review clicks, then a download: re-render, figure images, checks
    static     what a first and a repeat page load fetch
    sections   the document eval over several sections, one after the other and together

The PDFs are the repository's own fixtures (`tests/fixtures/layout`), repeated page by page
to `--pages` pages, so the document is the same everywhere.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "layout"
CORPUS = ("lecture_notes.pdf", "worked_example.pdf", "two_column.pdf")


# --------------------------------------------------------------------------- inputs


def corpus_pdf(pages: int) -> bytes:
    """The fixture PDFs, page by page in turn, to `pages` pages."""
    import pymupdf

    sources = [pymupdf.open(FIXTURES / name) for name in CORPUS]
    out = pymupdf.open()
    cursor = [0] * len(sources)
    while out.page_count < pages:
        for i, src in enumerate(sources):
            if out.page_count >= pages:
                break
            out.insert_pdf(
                src, from_page=cursor[i] % src.page_count, to_page=cursor[i] % src.page_count
            )
            cursor[i] += 1
    data = out.tobytes()
    out.close()
    return data


def latency_client(
    latency_s: float,
    budget: Any = None,
    on_start: Callable[[], None] | None = None,
    on_end: Callable[[], None] | None = None,
):
    """The offline client, answering after `latency_s` seconds (+/-30%, a fixed pattern);
    `on_start` and `on_end` are called around every request."""
    from readablestem.models import OFFLINE_DEFAULTS, FakeVisionClient

    pattern = (0.7, 1.3, 1.0, 0.85, 1.15, 1.0, 0.9, 1.1)
    counter = iter(range(10**9))
    lock = threading.Lock()

    class Slow(FakeVisionClient):
        def run(self, request, schema, *, is_fallback=False):
            with lock:
                n = next(counter)
            if on_start:
                on_start()
            time.sleep(latency_s * pattern[n % len(pattern)])
            try:
                return super().run(request, schema, is_fallback=is_fallback)
            finally:
                if on_end:
                    on_end()

    return Slow(defaults=dict(OFFLINE_DEFAULTS), budget=budget)


def settings(**kw: Any):
    from readablestem.config import Settings

    return Settings(_env_file=None, fake_models=True, max_pages_per_job=60, **kw)  # type: ignore[call-arg]


def emit(args: argparse.Namespace, data: dict[str, Any]) -> None:
    for key, value in data.items():
        print(f"{key}: {value}")
    if getattr(args, "json", None):
        Path(args.json).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- convert


class Marks:
    """When the first model call started and the last one ended, and how long layout took."""

    def __init__(self) -> None:
        self.first_call: float | None = None
        self.last_call: float | None = None
        self.layout_s = 0.0
        self.layout_cpu_s = 0.0
        self.calls = 0
        self.lock = threading.Lock()

    def call(self) -> None:
        now = time.perf_counter()
        with self.lock:
            self.calls += 1
            if self.first_call is None:
                self.first_call = now


def run_convert(pdf: bytes, *, latency: float, concurrency: int) -> dict[str, Any]:
    from readablestem.core.budget import InMemorySpendGuard, JobBudget
    from readablestem.pipeline import layout, run

    marks = Marks()
    budget = JobBudget(InMemorySpendGuard(1000.0), max_usd=1000.0, max_calls=100000)

    def ended() -> None:
        marks.last_call = time.perf_counter()

    client = latency_client(latency, budget, on_start=marks.call, on_end=ended)
    original_analyze = layout.analyze

    def timed_analyze(*a, **k):
        t, c = time.perf_counter(), time.process_time()
        try:
            return original_analyze(*a, **k)
        finally:
            marks.layout_s += time.perf_counter() - t
            marks.layout_cpu_s += time.process_time() - c

    layout.analyze = timed_analyze  # ty: ignore[invalid-assignment]
    try:
        started = time.perf_counter()
        out = run.convert_pdf(pdf, settings=settings(), client=client, concurrency=concurrency)
        wall = time.perf_counter() - started
    finally:
        layout.analyze = original_analyze  # ty: ignore[invalid-assignment]
    first = (marks.first_call or started) - started
    last = (marks.last_call or started) - started
    return {
        "concurrency": concurrency,
        "wall_s": round(wall, 2),
        "layout_s": round(marks.layout_s, 2),
        "layout_cpu_s": round(marks.layout_cpu_s, 2),
        "before_first_call_s": round(first, 2),
        "after_last_call_s": round(wall - last, 2),
        "model_phase_s": round(last - first, 2),
        "model_calls": marks.calls,
        "elements": len(out.result.elements),
    }


def cmd_convert(args: argparse.Namespace) -> None:
    from readablestem.pipeline import layout

    pdf = corpus_pdf(args.pages)
    layout.analyze(pdf, max_pages=args.pages)  # warm up: the table model is loaded once
    rows = []
    for concurrency in args.concurrency:
        runs = [
            run_convert(pdf, latency=args.latency, concurrency=concurrency)
            for _ in range(args.repeat)
        ]
        row = min(runs, key=lambda r: r["wall_s"])
        row["wall_s_all"] = [r["wall_s"] for r in runs]
        rows.append(row)
        print(row)
    base = rows[0]["wall_s"]
    summary: dict[str, Any] = {"pages": args.pages, "latency_s": args.latency, "runs": rows}
    for row in rows[1:]:
        summary[f"shorter_at_{row['concurrency']}_vs_{rows[0]['concurrency']}"] = (
            f"{100 * (1 - row['wall_s'] / base):.1f}%"
        )
    emit(args, {k: v for k, v in summary.items() if k != "runs"} | {"runs": len(rows)})


# --------------------------------------------------------------------------- review


def web_site(tmp: Path, **overrides: Any):
    """The web app over the real pipeline with the offline client (as tests/test_web_pipeline.py
    does), signed in as one person. Returns (app context, browser, user id)."""
    sys.path.insert(0, str(ROOT / "tests"))
    from fastapi.testclient import TestClient

    from fixtures.web.support import ALICE, login, make_settings
    from readablestem.models import make_vision_client
    from readablestem.web.app import create_app
    from readablestem.web.auth import set_allowed
    from readablestem.web.db import session_scope

    cfg = make_settings(tmp, max_pages_per_job=60, max_upload_mb=20, per_user_daily_page_quota=500)
    cfg = cfg.model_copy(update=overrides)
    app = create_app(cfg, client_factory=lambda s, b: make_vision_client(s, b, fake=True))
    browser = TestClient(app, follow_redirects=False)
    browser.__enter__()
    with session_scope(app.state.ctx.factory) as session:
        set_allowed(session, ALICE, allowed=True)
    login(browser, ALICE)
    return app.state.ctx, browser


def convert_in_site(ctx: Any, browser: Any, pdf: bytes) -> str:
    sys.path.insert(0, str(ROOT / "tests"))
    from fixtures.web.support import job_id_from, upload
    from readablestem.web.worker import Worker

    job_id = job_id_from(upload(browser, pdf, filename="bench.pdf"))
    worker = Worker(
        ctx.settings, ctx.factory, converter=ctx.converter, client_factory=ctx.client_factory
    )
    assert worker.run_once() == job_id
    return job_id


def cmd_review(args: argparse.Namespace) -> None:
    import tempfile

    sys.path.insert(0, str(ROOT / "tests"))
    from fixtures.web.support import csrf_from
    from readablestem.pipeline import figure_images
    from readablestem.web.db import Job, session_scope

    encodes = [0]
    inner = figure_images._student_image if hasattr(figure_images, "_student_image") else None
    target = "_student_image" if inner else "student_image"
    original = getattr(figure_images, target)

    def counted(*a: Any, **k: Any) -> Any:
        encodes[0] += 1
        return original(*a, **k)

    setattr(figure_images, target, counted)
    with tempfile.TemporaryDirectory() as tmp:
        ctx, browser = web_site(Path(tmp))
        job_id = convert_in_site(ctx, browser, corpus_pdf(args.pages))
        with session_scope(ctx.factory) as session:
            job = session.get(Job, job_id)
            assert job is not None
            fixes = (job.result_json or {})["fixes"]
            html_kb = len(job.html or "") // 1024
            blobs = len(job.blobs)
        pending = [
            f["element_id"]
            for f in fixes
            if f.get("status") in ("needs_review", "abstained", "failed")
        ]
        clicks = [e for e in pending if e.startswith("p")][: args.clicks]
        token = csrf_from(browser.get(f"/jobs/{job_id}/review").text)
        encodes[0] = 0
        cpu: list[float] = []
        wall: list[float] = []
        for element_id in clicks:
            c0, w0 = time.process_time(), time.perf_counter()
            response = browser.post(
                f"/jobs/{job_id}/review/{element_id}",
                data={"csrf_token": token, "action": "reject"},
            )
            cpu.append(time.process_time() - c0)
            wall.append(time.perf_counter() - w0)
            assert response.status_code in (303, 409), response.status_code
        c0 = time.process_time()
        download = browser.get(f"/jobs/{job_id}/download")
        after_cpu = time.process_time() - c0
        assert download.status_code == 200 and "<html" in download.text
        c0 = time.process_time()
        browser.get(f"/jobs/{job_id}/download")
        again_cpu = time.process_time() - c0
        browser.__exit__(None, None, None)
    setattr(figure_images, target, original)
    total_cpu = sum(cpu) + after_cpu
    emit(
        args,
        {
            "pages": args.pages,
            "html_kb": html_kb,
            "crops": blobs - 1,
            "clicks": len(clicks),
            "cpu_ms_per_click": round(1000 * statistics.mean(cpu), 1),
            "wall_ms_per_click": round(1000 * statistics.mean(wall), 1),
            "cpu_ms_first_download": round(1000 * after_cpu, 1),
            "cpu_ms_second_download": round(1000 * again_cpu, 1),
            "cpu_ms_session_total": round(1000 * total_cpu, 1),
            "figure_image_encodes": encodes[0],
        },
    )


# --------------------------------------------------------------------------- static


def cmd_static(args: argparse.Namespace) -> None:
    """What a first visit and a repeat visit to a page fetch, as a browser with a cache
    would: a response with `immutable` or a max-age is not asked for again, any other is
    asked for with its ETag (a 304 has no body). The font is fetched when the equation
    compare is shown, so it is counted separately."""
    import tempfile

    sys.path.insert(0, str(ROOT / "tests"))
    from fastapi.testclient import TestClient

    from fixtures.web.support import make_settings
    from readablestem.web.app import create_app

    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(make_settings(Path(tmp)))
        with TestClient(app, follow_redirects=False) as browser:
            cache: dict[str, tuple[str | None, bool]] = {}  # url -> (etag, fresh)

            def visit(urls: list[str]) -> tuple[int, int, int]:
                requests = bytes_ = not_modified = 0
                for url in urls:
                    entry = cache.get(url)
                    if entry and entry[1]:
                        continue
                    headers = {"If-None-Match": entry[0]} if entry and entry[0] else {}
                    response = browser.get(url, headers=headers)
                    requests += 1
                    bytes_ += len(response.content)
                    not_modified += response.status_code == 304
                    control = response.headers.get("cache-control", "")
                    fresh = "immutable" in control or (
                        "max-age=" in control and "max-age=0" not in control
                    )
                    cache[url] = (
                        response.headers.get("etag") or (entry[0] if entry else None),
                        fresh,
                    )
                return requests, bytes_, not_modified

            page = browser.get("/privacy").text
            urls = re.findall(r'(?:href|src)="(/static/[^"]+)"', page)
            css = next(u for u in urls if u.endswith(".css") or ".css" in u)
            font = re.search(r'url\("(/static/[^"]+\.(?:woff2|otf))"\)', browser.get(css).text)
            assert font, "no font in the stylesheet"
            first = visit(urls)
            first_font = visit([font.group(1)])
            repeat = visit(urls)
            repeat_font = visit([font.group(1)])
    emit(
        args,
        {
            "files": [*urls, font.group(1)],
            "first_visit_requests": first[0],
            "first_visit_bytes": first[1],
            "first_visit_font_bytes": first_font[1],
            "repeat_visit_requests": repeat[0],
            "repeat_visit_304s": repeat[2],
            "repeat_visit_bytes": repeat[1],
            "repeat_visit_font_requests": repeat_font[0],
        },
    )


# --------------------------------------------------------------------------- sections


def cmd_sections(args: argparse.Namespace) -> None:
    """The document task over several sections, one after the other and together."""
    from readablestem.eval import pipeline_eval as pe
    from readablestem.pipeline import layout

    pdfs = [corpus_pdf(args.pages) for _ in range(args.sections)]
    units = [
        [{"book_slug": "bench", "page_slug": f"s{i}", "kind": "paragraph", "id": f"s{i}-0"}]
        for i in range(args.sections)
    ]
    layout.analyze(pdfs[0], max_pages=args.pages)  # warm up the table model
    results: dict[int, tuple[float, list[dict]]] = {}
    for workers in args.workers:
        cfg = pe.EvalConfig(
            task="document",
            settings=settings(),
            client=latency_client(args.latency),
            media=None,  # type: ignore[arg-type]
            date="2026-10-14",
            gold_dir=ROOT,
            concurrency=args.concurrency,
            section_workers=workers,
        )
        started = time.perf_counter()
        items, stopped = pe._eval_document(cfg, units, pdfs)
        results[workers] = (time.perf_counter() - started, items)
        assert stopped is None and len(items) == args.sections

    def without_time(items: list[dict]) -> list[dict]:
        return [{k: v for k, v in item.items() if k != "latency_ms"} for item in items]

    base_workers = args.workers[0]
    out: dict[str, Any] = {"sections": args.sections, "pages_each": args.pages}
    for workers, (wall, items) in results.items():
        out[f"wall_s_workers_{workers}"] = round(wall, 2)
        out[f"items_identical_to_workers_{base_workers}_{workers}"] = without_time(
            items
        ) == without_time(results[base_workers][1])
    for workers in args.workers[1:]:
        out[f"shorter_at_{workers}"] = (
            f"{100 * (1 - results[workers][0] / results[base_workers][0]):.1f}%"
        )
    emit(args, out)


# --------------------------------------------------------------------------- documents


def cmd_documents(args: argparse.Namespace) -> None:
    """One rebuild of My documents (what the poll does whenever a page count changes) with
    N finished documents of `--pages` pages each, on embedded Postgres when pgserver is
    installed (a result is toasted there, and reading one key reads all of it)."""
    import tempfile
    import uuid

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from readablestem.core.budget import InMemorySpendGuard, JobBudget
    from readablestem.pipeline import run
    from readablestem.web import db, documents
    from readablestem.web.db import Job, User, session_scope, utcnow

    budget = JobBudget(InMemorySpendGuard(1000.0), max_usd=1000.0, max_calls=100000)
    output = run.convert_pdf(
        corpus_pdf(args.pages), settings=settings(), client=latency_client(0.0, budget)
    )
    result_json = output.result.model_dump(mode="json", exclude={"html"})
    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite:///{tmp}/documents.db"
        server = None
        try:
            import pgserver  # type: ignore[import-not-found]

            server = pgserver.get_server(Path(tmp) / "pg", cleanup_mode="delete")
            base = make_url(server.get_uri()).set(drivername="postgresql+psycopg")
            name = f"b_{uuid.uuid4().hex[:10]}"
            admin = create_engine(
                base.render_as_string(hide_password=False), isolation_level="AUTOCOMMIT"
            )
            with admin.connect() as conn:
                conn.execute(text(f'CREATE DATABASE "{name}"'))
            url = base.set(database=name).render_as_string(hide_password=False)
        except Exception:  # no pgserver: SQLite
            server = None
        engine = db.make_engine(url)
        db.create_all(engine)
        factory = db.make_sessionmaker(engine)
        with session_scope(factory) as session:
            user = User(email="bench@example.org", is_allowed=True)
            session.add(user)
            session.flush()
            for n in range(args.documents):
                job = Job(
                    user_id=user.id,
                    filename=f"w{n}.pdf",
                    status="done",
                    page_count=args.pages,
                    pages_done=args.pages,
                    retain_until=utcnow().replace(year=2099),
                    html="<p>x</p>",
                )
                job.result_json = result_json  # as the worker stores it
                session.add(job)
            user_id = user.id
        counter = DbCounter(engine)
        walls: list[float] = []
        cpus: list[float] = []
        for _ in range(args.polls):
            c0, w0 = time.process_time(), time.perf_counter()
            with session_scope(factory) as session:
                listing = documents.documents(session, user_id, now=utcnow(), budget_closed=False)
            walls.append(time.perf_counter() - w0)
            cpus.append(time.process_time() - c0)
        statements = counter.summary()
        engine.dispose()
        if server is not None:
            server.cleanup()
    emit(
        args,
        {
            "database": "postgres" if server is not None else "sqlite",
            "documents": args.documents,
            "pages_each": args.pages,
            "result_kb_each": len(json.dumps(result_json)) // 1024,
            "decisions_listed": sum(r.open_decisions for r in listing.rows),
            "ms_per_rebuild_wall": round(1000 * statistics.median(walls), 1),
            "ms_per_rebuild_cpu_of_this_process": round(1000 * statistics.median(cpus), 1),
            "statements_per_rebuild": round(statements["statements"] / args.polls, 1),
        },
    )


# --------------------------------------------------------------------------- spend


class DbCounter:
    """Statements and transactions an engine runs while attached."""

    def __init__(self, engine: Any) -> None:
        from sqlalchemy import event

        self.engine = engine
        self.statements: list[str] = []
        self.transactions = 0
        event.listen(engine, "before_cursor_execute", self._statement)
        event.listen(engine, "commit", self._commit)

    def _statement(self, conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        self.statements.append(statement.strip().split(None, 1)[0].upper())

    def _commit(self, conn: Any) -> None:
        self.transactions += 1

    def reset(self) -> None:
        self.statements.clear()
        self.transactions = 0

    def summary(self) -> dict[str, int]:
        kinds: dict[str, int] = {}
        for kind in self.statements:
            kinds[kind] = kinds.get(kind, 0) + 1
        return {"statements": len(self.statements), "transactions": self.transactions, **kinds}


def cmd_spend(args: argparse.Namespace) -> None:
    """Database work per model call (reserve + settle through a worker's job budget and the
    daily guard) and per idle poll of the worker."""
    import tempfile

    sys.path.insert(0, str(ROOT / "tests"))
    from sqlalchemy import select

    from fixtures.web.support import ALICE, WebEnv, fake_output
    from readablestem.models.base import VisionRequest
    from readablestem.models.fake import FakeVisionClient
    from readablestem.web.db import Blob, Job, User, session_scope, utcnow

    class Out(__import__("pydantic").BaseModel):
        latex: str

    with tempfile.TemporaryDirectory() as tmp:
        env = WebEnv(Path(tmp))
        with env.running():
            env.allow(ALICE)
            counter = DbCounter(env.ctx.factory.kw["bind"])
            worker = env.worker()
            idle: dict[str, Any] = {}
            counter.reset()
            for _ in range(args.polls):
                assert worker.run_once() is None
            idle = counter.summary()
            with session_scope(env.ctx.factory) as session:
                user = session.scalar(select(User).where(User.email == ALICE))
                assert user is not None
                job = Job(
                    user_id=user.id,
                    filename="bench.pdf",
                    page_count=30,
                    retain_until=utcnow().replace(year=2099),
                )
                session.add(job)
                session.flush()
                session.add(
                    Blob(
                        job_id=job.id, kind="upload", media_type="application/pdf", content=b"%PDF"
                    )
                )
            calls = args.calls

            def converter(pdf, /, *, settings, client, progress=None, **extra):
                request = VisionRequest(
                    step="equation", model=settings.main_model, prompt_version="v", instructions="i"
                )
                assert isinstance(client, FakeVisionClient)
                client.defaults["equation"] = {"latex": "x"}
                counter.reset()
                for _ in range(calls):
                    client.run(request, Out)
                per_call.update(counter.summary())
                return fake_output()

            per_call: dict[str, int] = {}
            env.converter = converter  # ty: ignore[invalid-assignment]
            worker = env.worker()
            worker.converter = converter
            assert worker.run_once() is not None
            counter.reset()
    emit(
        args,
        {
            "model_calls": calls,
            **{f"per_call_{k}": round(v / calls, 2) for k, v in per_call.items()},
            "idle_polls": args.polls,
            **{f"per_idle_poll_{k}": round(v / args.polls, 2) for k, v in idle.items()},
        },
    )


# --------------------------------------------------------------------------- first job


def cmd_first(args: argparse.Namespace) -> None:
    """The first conversion after a start, with and without the table models loaded while
    the worker waited (`tables.preload`). Each case is a fresh interpreter."""
    import subprocess

    if args.child:
        from readablestem.pipeline import tables

        pdf = corpus_pdf(args.pages)
        loading = 0.0
        if args.child == "preloaded":
            started = time.perf_counter()
            tables.preload()  # what the worker does at start, while it waits for a job
            loading = time.perf_counter() - started
        row = run_convert(pdf, latency=args.latency, concurrency=8)
        print("ROW " + json.dumps(row | {"preload_s": round(loading, 2)}))
        return
    rows: dict[str, dict[str, Any]] = {}
    for case in ("cold", "preloaded"):
        best = None
        for _ in range(args.repeat):
            out = subprocess.run(
                [sys.executable, __file__, "first", "--child", case, "--pages", str(args.pages),
                 "--latency", str(args.latency)],
                capture_output=True, text=True, check=True,
            )  # fmt: skip
            row = json.loads(next(x for x in out.stdout.splitlines() if x.startswith("ROW "))[4:])
            best = row if best is None or row["wall_s"] < best["wall_s"] else best
        assert best is not None
        rows[case] = best
        print(case, best)
    emit(
        args,
        {
            "first_conversion_s_cold": rows["cold"]["wall_s"],
            "first_conversion_s_preloaded": rows["preloaded"]["wall_s"],
            "layout_cpu_s_cold": rows["cold"]["layout_cpu_s"],
            "layout_cpu_s_preloaded": rows["preloaded"]["layout_cpu_s"],
            "preload_s_while_idle": rows["preloaded"]["preload_s"],
            "shorter": f"{100 * (1 - rows['preloaded']['wall_s'] / rows['cold']['wall_s']):.1f}%",
        },
    )


# --------------------------------------------------------------------------- main

COMMANDS: dict[str, Callable[[argparse.Namespace], None]] = {
    "convert": cmd_convert,
    "review": cmd_review,
    "static": cmd_static,
    "sections": cmd_sections,
    "first": cmd_first,
    "spend": cmd_spend,
    "documents": cmd_documents,
}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("convert", help="conversion wall time at several concurrencies")
    c.add_argument("--pages", type=int, default=12)
    c.add_argument("--latency", type=float, default=1.5, help="seconds per simulated model call")
    c.add_argument("--concurrency", type=int, nargs="+", default=[4, 8])
    c.add_argument("--repeat", type=int, default=1)
    r = sub.add_parser("review", help="CPU of review clicks, then a download")
    r.add_argument("--pages", type=int, default=12)
    r.add_argument("--clicks", type=int, default=12)
    sub.add_parser("static", help="what a first and a repeat page view fetch")
    docs = sub.add_parser("documents", help="one rebuild of My documents with N finished jobs")
    docs.add_argument("--documents", type=int, default=20)
    docs.add_argument("--pages", type=int, default=12)
    docs.add_argument("--polls", type=int, default=10)
    spd = sub.add_parser("spend", help="database statements per model call and per idle poll")
    spd.add_argument("--calls", type=int, default=100)
    spd.add_argument("--polls", type=int, default=50)
    fst = sub.add_parser(
        "first", help="the first conversion after a start, models preloaded or not"
    )
    fst.add_argument("--pages", type=int, default=6)
    fst.add_argument("--latency", type=float, default=1.0)
    fst.add_argument("--repeat", type=int, default=1)
    fst.add_argument("--child", choices=["cold", "preloaded"], help=argparse.SUPPRESS)
    sec = sub.add_parser("sections", help="the document task, sections in series and together")
    sec.add_argument("--sections", type=int, default=6)
    sec.add_argument("--pages", type=int, default=3, help="pages in each section's PDF")
    sec.add_argument("--latency", type=float, default=1.0)
    sec.add_argument("--concurrency", type=int, default=4)
    sec.add_argument("--workers", type=int, nargs="+", default=[1, 4])
    for s in sub.choices.values():
        s.add_argument("--json", help="also write the numbers here")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    COMMANDS[args.command](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
