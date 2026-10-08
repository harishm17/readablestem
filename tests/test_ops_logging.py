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
)

SENTINEL = "quokka"


@pytest.fixture(autouse=True)
def restore_logging():
    """configure_logging changes process-wide state; put it back after each test."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    access = logging.getLogger("uvicorn.access").disabled
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = access
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
    "key", ["job_id", "prompt_version", "status", "latency_ms", "error_type", "textbook_id"]
)
def test_metadata_keys_are_kept(key):
    assert not is_denied_key(key)


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
