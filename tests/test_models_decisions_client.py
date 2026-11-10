"""DecisionsClient: request shape, answer parsing, outcomes and spend accounting.

The real `openai.OpenAI` client runs over an `httpx2.MockTransport`, so field names,
the endpoint path and SDK exceptions are the SDK's own and only the network is fake.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

import httpx2
import openai
import pytest

from readablestem.config import ConfigError, Settings
from readablestem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget, Reservation
from readablestem.models.decisions_client import (
    Choice,
    DecisionRequest,
    DecisionsClient,
    ImagePart,
    Question,
    TextPart,
    parse_answers,
)
from readablestem.models.openai_client import PNG_SIGNATURE
from readablestem.models.pricing import (
    UnknownModelError,
    decisions_cost_usd,
)

PNG = PNG_SIGNATURE + b"crop"
SECRET = "SECRET-DOCUMENT-TEXT"
PRED = Question("ok", "True if the alt text matches the image.")
CHOICE = Question(
    "verdict",
    "Compare them.",
    kind="choice",
    choices=(Choice("match", "Same."), Choice("cosmetic"), Choice("meaning_changing")),
)


def settings(**kw: Any) -> Settings:
    kw.setdefault("openai_api_key", "sk-test")
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


class RecordingBudget(JobBudget):
    def __init__(self, cap: float = 1.0, max_calls: int = 100) -> None:
        super().__init__(guard=InMemorySpendGuard(cap_usd=cap), max_usd=cap, max_calls=max_calls)
        self.reserved: list[float] = []
        self.counted: list[bool] = []  # count_call of each reservation granted

    def reserve(self, amount_usd: float, *, count_call: bool = True) -> Reservation:
        reservation = super().reserve(amount_usd, count_call=count_call)
        self.reserved.append(amount_usd)
        self.counted.append(count_call)
        return reservation


def request(*questions: Question, text: str = f"<alt_text>{SECRET}</alt_text>") -> DecisionRequest:
    return DecisionRequest(
        purpose="figure_alt",
        version="figure_alt-test",
        parts=(ImagePart(PNG), TextPart(text)),
        questions=questions or (PRED,),
    )


def body(*answers: dict, input_tokens: int = 600) -> dict:
    return {
        "model": "gpt-6-luna",
        "answers": list(answers),
        "usage": {
            "input_tokens": input_tokens,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 0,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": input_tokens,
        },
    }


def predicate(name: str = "ok", p: float = 0.91) -> dict:
    return {"type": "predicate", "name": name, "probability": p}


def choice(**probabilities: float) -> dict:
    values = [{"value": k, "probability": v} for k, v in probabilities.items()]
    best = max(probabilities, key=lambda k: probabilities[k])
    return {
        "type": "choice",
        "name": "verdict",
        "choice": best,
        "probabilities": values,
        "confidence": 0.5,
    }


Reply = Callable[[httpx2.Request], httpx2.Response]


def ok(payload: dict) -> Reply:
    return lambda request: httpx2.Response(
        200, json=payload, headers={"x-request-id": "req_abc123"}
    )


class HTTP:
    def __init__(self, reply: Reply) -> None:
        self.reply = reply
        self.sent: list[dict] = []
        self.paths: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        with self._lock:
            self.sent.append(json.loads(request.content))
            self.paths.append(request.url.path)
        return self.reply(request)

    def sdk(self) -> openai.OpenAI:
        return openai.OpenAI(
            api_key="sk-test",
            base_url="https://api.test/v1",
            http_client=httpx2.Client(transport=httpx2.MockTransport(self)),
        )


def make(reply: Reply, **kw: Any) -> tuple[DecisionsClient, RecordingBudget, HTTP]:
    http = HTTP(reply)
    budget = kw.pop("budget", None) or RecordingBudget()
    client = DecisionsClient(
        kw.pop("settings", None) or settings(), budget, client=http.sdk(), **kw
    )
    return client, budget, http


# ------------------------------------------------------------------------ requests


def test_safety_identifier_is_sent_when_given_and_must_be_a_hash():
    client, _, http = make(ok(body(predicate())), safety_identifier="u-3f9a")
    client.decide(request())
    assert http.sent[0]["safety_identifier"] == "u-3f9a"
    for bad in ("someone@example.org", "", "x" * 65):
        with pytest.raises(ValueError):
            DecisionsClient(settings(), RecordingBudget(), safety_identifier=bad)


@pytest.mark.parametrize(
    "bad",
    [
        DecisionRequest("p", "v", (), (PRED,)),
        DecisionRequest("p", "v", (ImagePart(b"GIF89a"),), (PRED,)),
        DecisionRequest("p", "v", (TextPart("x"),), ()),
        DecisionRequest("p", "v", (TextPart("x"),), (PRED, PRED)),
        DecisionRequest("p", "v", (TextPart("x"),), (Question("q", "  "),)),
        DecisionRequest(
            "p", "v", (TextPart("x"),), (Question("q", "i", kind="choice", choices=(Choice("a"),)),)
        ),
        DecisionRequest("p", "v", (TextPart("x"),), (Question("q", "i", choices=(Choice("a"),)),)),
    ],
)
def test_malformed_requests_are_refused_before_any_spend(bad):
    client, budget, http = make(ok(body(predicate())))
    with pytest.raises(ValueError):
        client.decide(bad)
    assert budget.reserved == [] and http.sent == []


def test_missing_key_and_unpriced_model_fail_before_any_spend():
    budget = RecordingBudget()
    with pytest.raises(ConfigError):
        DecisionsClient(settings(openai_api_key=None), budget).decide(request())
    client, budget, http = make(ok(body(predicate())), settings=settings(decisions_model="gpt-x"))
    with pytest.raises(UnknownModelError):
        client.decide(request())
    assert budget.reserved == [] and http.sent == []


# --------------------------------------------------------------------- spend


def test_reserves_first_then_settles_at_the_usage_price():
    client, budget, _ = make(ok(body(predicate(), input_tokens=640)))
    result = client.decide(request())
    (reserved,) = budget.reserved
    assert result.cost_usd == pytest.approx(decisions_cost_usd("gpt-6-luna", 640))
    assert result.cost_usd == pytest.approx(640 * 0.10 / 1e6)
    assert result.cost_usd <= reserved
    assert budget.spent_usd == pytest.approx(result.cost_usd)
    assert result.input_tokens == 640 and result.request_id == "req_abc123"


def test_a_refused_reservation_sends_nothing():
    client, _, http = make(ok(body(predicate())), budget=RecordingBudget(cap=1e-9))
    with pytest.raises(BudgetExceeded):
        client.decide(request())
    assert http.sent == []


def test_http_errors_are_outcomes_charged_nothing_and_never_logged_with_content(caplog):
    def reply(request: httpx2.Request) -> httpx2.Response:
        error = {"message": f"bad input near {SECRET}", "type": "x", "code": "invalid_value"}
        return httpx2.Response(400, json={"error": error}, headers={"x-request-id": "req_e1"})

    client, budget, _ = make(reply)
    with caplog.at_level(logging.DEBUG, logger="readablestem"):
        result = client.decide(request())
    assert result.outcome == "api_error" and result.answers == {}
    assert result.cost_usd == 0 and budget.spent_usd == 0 and len(budget.reserved) == 1
    assert result.request_id == "req_e1"
    assert SECRET not in caplog.text
    (line,) = [r for r in caplog.records if r.getMessage() == "decision_call"]
    assert line.levelno == logging.WARNING


def test_a_timeout_is_charged_the_reservation_a_connect_error_nothing():
    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    client, budget, _ = make(timeout)
    result = client.decide(request())
    assert result.outcome == "timeout"
    assert result.cost_usd == pytest.approx(budget.reserved[0])

    def refused(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    client, budget, _ = make(refused)
    result = client.decide(request())
    assert (result.outcome, result.cost_usd) == ("api_error", 0.0)


def test_non_json_and_unexpected_bodies_are_api_errors():
    client, _, _ = make(lambda r: httpx2.Response(200, content=b"<html>", headers={}))
    assert client.decide(request()).outcome == "api_error"
    client, _, _ = make(ok({"error": {"code": "x"}}))
    assert client.decide(request()).outcome == "api_error"


# ------------------------------------------------------------------- answers


@pytest.mark.parametrize(
    "answers",
    [
        [],  # missing
        [predicate("other")],  # wrong name
        [predicate(p=1.5)],  # not a probability
        [{"type": "predicate", "name": "ok", "probability": True}],
        [{"type": "choice", "name": "ok", "probabilities": []}],  # wrong kind
        [predicate(), predicate("ok")],  # too many
    ],
)
def test_answers_that_do_not_fit_the_questions_are_invalid_output(answers):
    assert parse_answers(answers, [PRED]) == ("invalid_output", {})


def test_unknown_or_missing_choice_values_are_invalid_output():
    unknown = choice(match=0.5, cosmetic=0.2, other=0.3)
    missing = choice(match=0.8, cosmetic=0.2)
    assert parse_answers([unknown], [CHOICE])[0] == "invalid_output"
    assert parse_answers([missing], [CHOICE])[0] == "invalid_output"
