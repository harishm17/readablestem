"""observability.logging: content keys dropped, strings truncated, Cloud Run JSON shape,
exceptions without messages, and the request-logging middleware."""

from __future__ import annotations

import json
import logging

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from a11y_stem.config import Settings
from a11y_stem.observability.logging import (
    DENYLIST,
    MAX_STRING,
    TRUNCATION_MARK,
    configure_logging,
    exception_codes,
    get_logger,
    is_denied_key,
    privacy_filter,
    request_logging_middleware,
    uvicorn_log_options,
)

SENTINEL = "quokka"


UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi")


@pytest.fixture(autouse=True)
def restore_logging():
    """configure_logging (and uvicorn's dictConfig) change process-wide state; put it back
    after each test."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    saved = {
        name: (list(lg.handlers), list(lg.filters), lg.propagate, lg.disabled, lg.level)
        for name in UVICORN_LOGGERS
        if (lg := logging.getLogger(name))
    }
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    for name, (hs, fs, propagate, disabled, lvl) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers[:], lg.filters[:] = hs, fs
        lg.propagate, lg.disabled = propagate, disabled
        lg.setLevel(lvl)
    structlog.reset_defaults()


def parse(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def lines(capsys) -> list[dict]:
    return parse(capsys.readouterr().out)


@pytest.mark.parametrize("key", sorted(DENYLIST))
def test_every_denylisted_key_is_dropped(key):
    assert privacy_filter(None, "info", {"event": "x", key: SENTINEL}) == {"event": "x"}


@pytest.mark.parametrize(
    "key", ["alt_text", "user_email", "source_filename", "contextText", "raw-html", "page.text"]
)
def test_compound_content_keys_are_dropped(key):
    assert is_denied_key(key)


@pytest.mark.parametrize(
    "key",
    [
        # spellings that split into other words on case changes
        "MathML",
        "mathML",
        "LaTeX",
        "latexSource",
        # plurals
        "texts",
        "emails",
        "admin_emails",
        "filenames",
        "contents",
        "captions",
        "prompts",
        "alts",
        "speeches",
        "queries",
        # separators inside a denied word, or none between words
        "file_name",
        "fileName",
        "FILE-NAME",
        "alttext",
        "useremail",
        "doctitle",
        # document title and personal or path-like data
        "title",
        "page_title",
        "user_name",
        "body",
        "source_path",
        "callback_url",
        # underscore keys are not exempt
        "_text",
        "_email",
    ],
)
def test_other_spellings_of_content_keys_are_dropped(key):
    assert is_denied_key(key)
    assert privacy_filter(None, "info", {"event": "x", key: SENTINEL}) == {"event": "x"}


@pytest.mark.parametrize(
    "key",
    [
        "job_id",
        "prompt_version",
        "status",
        "latency_ms",
        "error_type",
        "textbook_id",
        "jobs_deleted",
        "unaggregated_deleted",
        "route",
        "method",
        "reason",
        "stage",
        "pages",
    ],
)
def test_metadata_keys_are_kept(key):
    assert not is_denied_key(key)


def test_underscore_keys_are_scrubbed_like_any_other():
    """Only ProcessorFormatter's own `_record`/`_from_structlog` skip the filter."""

    class Doc:
        def __repr__(self) -> str:
            return f"Doc(text={SENTINEL!r})"

    record = object()
    out = privacy_filter(
        None,
        "info",
        {"event": "x", "_doc": Doc(), "_note": "z" * 300, "_record": record},
    )
    assert out["_doc"] == "<Doc>"
    assert out["_note"] == "z" * MAX_STRING + TRUNCATION_MARK
    assert out["_record"] is record
    assert SENTINEL not in json.dumps({k: v for k, v in out.items() if k != "_record"})


def test_configured_output_drops_other_spellings(capsys):
    configure_logging()

    class Doc:
        def __repr__(self) -> str:
            return f"Doc(text={SENTINEL!r})"

    get_logger("a11y_stem.test").info(
        "e1",
        MathML=SENTINEL,
        LaTeX=SENTINEL,
        file_name=SENTINEL,
        emails=[SENTINEL],
        title=SENTINEL,
        _text=SENTINEL,
        _doc=Doc(),
        job_id="j1",
    )
    (record,) = lines(capsys)
    assert record["job_id"] == "j1" and record["_doc"] == "<Doc>"
    assert SENTINEL not in json.dumps(record)


def test_values_are_plain_data_truncated_and_scrubbed_when_nested():
    class Element(BaseModel):
        text: str

    out = privacy_filter(
        None,
        "info",
        {
            "event": "y" * 500,
            "job_id": "j1",
            "long": "z" * 300,
            "element": Element(text=SENTINEL),  # its repr would contain the text
            "nested": {"latex": SENTINEL, "pages": 3, "deeper": [{"email": SENTINEL}]},
            "_record": "internal",
        },
    )
    assert out["job_id"] == "j1"
    assert out["long"] == "z" * MAX_STRING + TRUNCATION_MARK
    assert len(out["event"]) == MAX_STRING + len(TRUNCATION_MARK)
    assert out["element"] == "<Element>"
    assert out["nested"] == {"pages": 3, "deeper": [{}]}
    assert out["_record"] == "internal"
    assert SENTINEL not in json.dumps(out)


def test_exceptions_are_logged_by_type_and_location_only():
    try:
        raise ValueError(f"input_value='{SENTINEL}'")
    except ValueError:
        out = exception_codes(None, "error", {"event": "failed", "exc_info": True})
    assert out["error_type"] == "builtins.ValueError"
    assert out["stack"][-1].endswith("test_exceptions_are_logged_by_type_and_location_only")
    assert SENTINEL not in json.dumps(out)
    assert "exc_info" not in out


def test_configured_output_is_cloud_run_json(capsys):
    configure_logging(Settings(_env_file=None, log_level="INFO"))  # type: ignore[call-arg]
    log = get_logger("a11y_stem.test")
    log.info("job_done", job_id="j1", alt=SENTINEL, latex=SENTINEL)
    log.debug("hidden")
    try:
        raise RuntimeError(SENTINEL)
    except RuntimeError:
        log.exception("job_failed", job_id="j1")
    logging.getLogger("sqlalchemy.pool").warning("pool %s", "exhausted")

    records = lines(capsys)
    assert [r["message"] for r in records] == ["job_done", "job_failed", "pool exhausted"]
    first = records[0]
    assert first["severity"] == "INFO" and first["logger"] == "a11y_stem.test"
    assert first["time"].endswith("Z")
    assert first["job_id"] == "j1" and "alt" not in first and "latex" not in first
    assert records[1]["severity"] == "ERROR"
    assert records[1]["error_type"] == "builtins.RuntimeError"
    assert records[2]["severity"] == "WARNING"
    assert SENTINEL not in json.dumps(records)


def test_debug_level_never_enables_chatty_libraries(capsys):
    configure_logging(Settings(_env_file=None, log_level="DEBUG"))  # type: ignore[call-arg]
    get_logger("a11y_stem.test").debug("ours")
    logging.getLogger("openai").debug("request body would be here")
    logging.getLogger("httpx2").info(f"HTTP Request: GET /x?email={SENTINEL}")
    logging.getLogger("some.new.library").info(f"GET /x?q={SENTINEL}")
    logging.getLogger("some.new.library").warning("disk almost full")
    assert [r["message"] for r in lines(capsys)] == ["ours", "disk almost full"]
    assert logging.getLogger("uvicorn.access").disabled


def _uvicorn_lines() -> None:
    """What uvicorn logs for a request and for an unhandled exception."""
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d',
        "203.0.113.9",
        "GET",
        f"/auth/callback?code={SENTINEL}&state=s",
        "1.1",
        200,
    )
    try:
        raise RuntimeError(f"bad LaTeX {SENTINEL}")
    except RuntimeError:
        logging.getLogger("uvicorn.error").exception("Exception in ASGI application")
    logging.getLogger("uvicorn.error").info("Application startup complete.")


@pytest.mark.parametrize("ours_first", [True, False], ids=["ours-first", "uvicorn-first"])
def test_uvicorn_default_log_config_cannot_bypass_the_chain(capsys, ours_first):
    """`uvicorn.run` without `log_config=None` runs dictConfig with its own plain-text
    handlers (full path and query string, tracebacks with messages). Whichever runs
    last, uvicorn's records must still come out through the privacy chain."""
    uvicorn = pytest.importorskip("uvicorn")

    async def app(scope, receive, send) -> None:  # never called
        return None

    if ours_first:
        configure_logging()
        uvicorn.Config(app)  # default log_config, as `a11y-stem serve` uses today
    else:
        uvicorn.Config(app)
        configure_logging()
    _uvicorn_lines()

    out, err = capsys.readouterr()
    assert SENTINEL not in out + err
    assert "callback" not in out + err  # the access log is dropped entirely
    records = parse(out)
    assert [r["message"] for r in records] == [
        "Exception in ASGI application",
        "Application startup complete.",
    ]
    assert records[0]["error_type"] == "builtins.RuntimeError"
    assert all(r["logger"] == "uvicorn.error" for r in records)


def test_uvicorn_log_options_leave_logging_to_us(capsys):
    uvicorn = pytest.importorskip("uvicorn")

    async def app(scope, receive, send) -> None:
        return None

    configure_logging()
    config = uvicorn.Config(app, **uvicorn_log_options())
    assert config.log_config is None and config.access_log is False
    _uvicorn_lines()
    out, err = capsys.readouterr()
    assert SENTINEL not in out + err
    assert len(parse(out)) == 2


def test_configure_logging_is_idempotent(capsys):
    configure_logging()
    configure_logging()
    get_logger("a11y_stem.test").info("once")
    assert len(lines(capsys)) == 1


def make_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(request_logging_middleware)

    @app.get("/jobs/{job_id}")
    def job(job_id: str) -> dict:
        return {"ok": True}

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/boom")
    def boom() -> dict:
        raise RuntimeError(SENTINEL)

    return app


def test_middleware_logs_route_template_not_path_or_query(capsys):
    configure_logging()
    client = TestClient(make_app(), raise_server_exceptions=False)
    client.get(
        f"/jobs/{SENTINEL}-notes.pdf",
        params={"email": f"{SENTINEL}@example.org"},
        headers={"X-Filename": SENTINEL},
    )
    client.get("/no/such/page")
    client.get("/boom")
    client.get("/healthz")  # debug level: not shown at INFO

    out = capsys.readouterr().out
    records = [r for r in parse(out) if r["message"] == "request"]
    assert [(r["method"], r["route"], r["status"]) for r in records] == [
        ("GET", "/jobs/{job_id}", 200),
        ("GET", "unmatched", 404),
        ("GET", "/boom", 500),
    ]
    assert all(isinstance(r["latency_ms"], int) and r["latency_ms"] >= 0 for r in records)
    assert SENTINEL not in out
