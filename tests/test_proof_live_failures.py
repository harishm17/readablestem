"""A live proof run in which the model API fails is not a recording.

A call that fails (no credit, a bad key, a rate limit, a timeout) is not the system's answer.
Before this check a run whose every call failed exited 0, reported all runs as complete and
scored every equation as a failure of the system.
"""

from __future__ import annotations

import httpx
import openai
import pytest

from proof_support import SET_ID, PdfMedia, fake_client, make_project
from readablestem.core.types import Check
from readablestem.pipeline import html_checks
from readablestem.proof import engine, runner
from readablestem.proof.errors import Incomplete, ProofError

DATE = "2026-10-14"


def _status_error(status: int, code: str | None) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.invalid/v1/responses")
    response = httpx.Response(status, request=request)
    body = {"code": code, "message": "do not print this"}  # what the SDK hands over
    return openai.APIStatusError("do not print this", response=response, body=body)


def _live(root, work, max_usd=5.0, **overrides):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(html_checks, "axe_checks", lambda html: [Check(name="axe", passed=True)])
        return engine.run_proof(
            engine.ProofOptions(
                root=root,
                live=True,
                max_usd=max_usd,
                date=DATE,
                work_dir=work,
                media_factory=PdfMedia,
                n_boot=100,
                **overrides,
            ),
            say=lambda _m: None,
        )


def _client_that_fails(outcome: str):
    def factory(settings, budget):
        client = fake_client(settings, budget)
        client.fail_models = {settings.main_model: outcome, settings.cheap_model: outcome}
        return client

    return factory


@pytest.mark.parametrize("outcome", ["api_error", "timeout"])
def test_a_run_where_every_call_fails_is_not_recorded(tmp_path, outcome):
    root = make_project(tmp_path)
    with pytest.raises(Incomplete, match="model calls failed") as caught:
        _live(root, tmp_path / "w", client_factory=_client_that_fails(outcome))
    assert outcome in str(caught.value)
    assert "not the system's answer" in str(caught.value)
    assert not (tmp_path / "w" / "bundles" / f"{SET_ID}-{DATE}").exists()


def test_a_refusal_is_the_systems_answer_and_is_not_a_failed_call():
    rows = [
        {"t": "model", "call": {"outcome": "refused"}},
        {"t": "model", "call": {"outcome": "invalid_output"}},
        {"t": "model", "call": {"outcome": "ok"}},
        {"t": "verify", "verdict": {"reason_code": None}},
    ]
    assert runner.failed_calls(rows)[0] == 0
    runner.require_answers("equations.run1", rows, None)


def test_the_second_check_failing_counts_as_a_failed_call():
    rows = [
        {"t": "model", "call": {"outcome": "ok"}},
        {"t": "verify", "verdict": {"reason_code": "api_error"}},
    ]
    assert runner.failed_calls(rows) == (1, 2, {"second check api_error": 1})
    with pytest.raises(Incomplete, match="1 of 2"):
        runner.require_answers("figures.run1", rows, None)


def test_the_probe_reports_a_connection_failure_by_type_only():
    def send():
        raise openai.APIConnectionError(request=httpx.Request("POST", "https://api.invalid"))

    with pytest.raises(ProofError, match=r"could not be reached.*APIConnectionError"):
        runner.probe_api(runner.proof_settings(), send=send)


def test_a_live_run_stops_at_the_probe_before_any_work(tmp_path):
    root = make_project(tmp_path)
    media_built = []

    def media():
        media_built.append(1)
        return PdfMedia()

    def send():
        raise _status_error(429, "credit_balance_exhausted")

    with pytest.raises(ProofError, match="HTTP 429, credit_balance_exhausted"):
        engine.run_proof(
            engine.ProofOptions(
                root=root,
                live=True,
                max_usd=5.0,
                date=DATE,
                work_dir=tmp_path / "w",
                media_factory=media,
                probe=send,
            ),
            say=lambda _m: None,
        )
    assert not media_built
    assert not (tmp_path / "w" / "bundles").exists()
