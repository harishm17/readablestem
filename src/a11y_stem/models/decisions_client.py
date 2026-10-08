"""Client for the OpenAI Decisions API (`POST /v1/decisions`), the second automated check.

The Decisions API answers fixed questions about a shared input (text and images) with
probabilities instead of text: a predicate gives P(true), a choice a probability per
value. It serves gpt-6-luna only, answers in about 0.15 s, bills input tokens only and
is deterministic (the same request gets the same answers). The probabilities rank errors
well but are not calibrated, so callers compare them with thresholds set on labelled
data (ADR 0005; the research is summarised in docs/steps.md "Second automated check").

One `decide()` is one request: check it, reserve its worst case, send it once, read the
usage, settle, and return the answers with a content-free outcome. Why it is built so:

- Reserve before every send, through the same `JobBudget` as the vision client
  (architecture rule 3). The bound is `pricing.estimate_decisions_max_cost_usd`: the
  shared input once plus, per question, the hidden per-question prompt and the
  question's own length. If the reservation is refused, `BudgetExceeded` propagates and
  nothing leaves the machine; a malformed request, a missing key or an unpriced model
  are raised before the reservation for the same reason.
- `detail="high"` on every image. The endpoint's default, "auto", behaves like
  "original" on large images: about 4.8x the tokens of "high" (and it refuses images
  over 30,000 patches that "high" accepts).
- No `store`, `prompt_cache_key`, `temperature`, `seed`, `reasoning` or `service_tier`:
  the endpoint refuses every one of them (400 `unknown_parameter`). There is no system
  prompt either; the framing is in the question instructions and the input text.
- One send, no retries (the SDK's are off too). The check is optional and fail-open: a
  failed send costs the caller only the note it would have added, and a retry would
  add latency to a step for that. Rate limits are separate from the Responses API's.
- What a send is charged: its usage when the provider answered; $0 for an HTTP error
  status (a 4xx is never billed) or a connection that failed before reaching the
  provider; the full reservation for a timeout or a connection broken after the request
  went out, because it may have been billed unseen (as in `openai_client`).
- Outcomes, not exceptions: provider failures come back as `ModelCall`-style outcome
  codes ("api_error", "timeout", "refused" when every question was refused,
  "invalid_output" for answers that do not fit the questions). Exception messages and
  bodies are never logged: an error can echo the request, which carries document
  content. The log line has the purpose, version, outcome, token count, cost, latency,
  HTTP status, the provider's error code and the `x-request-id` header (the response
  body has no id of its own).

Thread safety: no per-call state; the SDK client is created once under a lock.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, cast

import openai

from a11y_stem.config import Settings
from a11y_stem.core.budget import JobBudget
from a11y_stem.core.types import Outcome
from a11y_stem.models.openai_client import PNG_SIGNATURE, exception_outcome, png_data_url
from a11y_stem.models.pricing import (
    decisions_cost_usd,
    decisions_price_for,
    estimate_decisions_max_cost_usd,
)
from a11y_stem.observability.logging import log_fields

log = logging.getLogger(__name__)

# Limits the endpoint enforces (200 questions and 10 score levels are undocumented; the
# others are in the reference). Checked here so a request that could only fail is not sent.
MAX_QUESTIONS: Final = 200
MAX_IMAGES: Final = 128
MAX_CHOICES: Final = 255
IMAGE_DETAIL: Final = "high"
SAFETY_IDENTIFIER_MAX_CHARS: Final = 64

_SAFE_CODE: Final = re.compile(r"[a-z0-9_.\-]{1,64}")
_REQUEST_ID: Final = re.compile(r"[A-Za-z0-9_.\-]{1,128}")
# Transport errors raised before the request reached the provider (httpx and httpx2 names).
_NOT_SENT_ERRORS: Final[frozenset[str]] = frozenset(
    {"ConnectError", "ConnectTimeout", "PoolTimeout"}
)

QuestionKind = Literal["predicate", "choice"]


@dataclass(frozen=True)
class Choice:
    value: str  # a code of ours ("match"), never document text
    description: str = ""


@dataclass(frozen=True)
class Question:
    """One question. Names, instructions and choices are constants of this codebase:
    document text only ever travels in the request's text parts."""

    name: str
    instructions: str
    kind: QuestionKind = "predicate"
    choices: tuple[Choice, ...] = ()

    @property
    def chars(self) -> int:
        """Its length as sent, for the spend reservation."""
        return (
            len(self.name)
            + len(self.instructions)
            + sum(len(c.value) + len(c.description) for c in self.choices)
        )

    def param(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": self.kind,
            "name": self.name,
            "instructions": self.instructions,
        }
        if self.kind == "choice":
            out["choices"] = [
                {"value": c.value, **({"description": c.description} if c.description else {})}
                for c in self.choices
            ]
        return out


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    png: bytes


Part = TextPart | ImagePart


@dataclass(frozen=True)
class DecisionRequest:
    """One request. `purpose` and `version` are content-free codes for the log line."""

    purpose: str  # "figure_alt", "equation_match", "canary"
    version: str  # "<purpose>-<sha8>" of the question wording
    parts: tuple[Part, ...]
    questions: tuple[Question, ...]


@dataclass(frozen=True)
class Answer:
    """One answer. `probability` for a predicate (P(true)); `probabilities` for a choice
    (value -> P, in the order the choices were asked); neither for a refusal. Score
    questions are not used here, so their answers are not read."""

    kind: str  # "predicate" | "choice" | "refusal"
    probability: float | None = None
    probabilities: Mapping[str, float] = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return self.kind == "refusal"


@dataclass(frozen=True)
class DecisionResult:
    outcome: Outcome  # "ok" when at least one question was answered
    answers: Mapping[str, Answer]  # question name -> answer (empty unless "ok")
    model: str
    input_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    request_id: str | None = None


@dataclass(frozen=True)
class _Reply:
    outcome: Outcome
    answers: Mapping[str, Answer] = field(default_factory=dict)
    input_tokens: int = 0
    status: int | None = None
    code: str | None = None
    error: str | None = None  # exception class name or body category
    request_id: str | None = None
    billed_unseen: bool = False


class DecisionsClient:
    """Sends `DecisionRequest`s, charging every send to `budget` (the job's budget).

    `client` lets tests inject an `openai.OpenAI`; without it the SDK client is created on
    the first request from `settings.require_openai_key()`, so building this object never
    needs a key. `safety_identifier` must be an opaque hash of the user id, never an email.
    """

    def __init__(
        self,
        settings: Settings,
        budget: JobBudget,
        *,
        client: openai.OpenAI | None = None,
        safety_identifier: str | None = None,
    ) -> None:
        if safety_identifier is not None:
            _check_safety_identifier(safety_identifier)
        self._settings = settings
        self._budget = budget
        self._given_client = client
        self._safety_identifier = safety_identifier
        self._sdk: openai.OpenAI | None = None
        self._sdk_lock = threading.Lock()

    @property
    def model(self) -> str:
        return self._settings.decisions_model

    def decide(self, request: DecisionRequest) -> DecisionResult:
        """Send one request and return its answers.

        Raises before anything is reserved or sent: `ValueError` for a malformed request,
        `config.ConfigError` without an API key, `pricing.UnknownModelError` for a model
        with no Decisions price. Raises `BudgetExceeded` when the reservation is refused
        (nothing is sent). Never raises for provider errors, timeouts or refusals.
        """
        content = request_content(request)
        questions = check_questions(request.questions)
        model = self.model
        decisions_price_for(model)  # unpriced model: refuse before reserving
        sdk = self._client()
        estimate = reservation_usd(model, request)
        started = time.perf_counter()
        reservation = self._budget.reserve(estimate)  # BudgetExceeded: nothing was sent
        try:
            reply = self._exchange(sdk, model, content, questions, request.questions)
        except BaseException:
            # Not a provider failure (those are outcomes): a bug or an interrupt.
            self._budget.settle(reservation, 0.0)
            raise
        charge = estimate if reply.billed_unseen else decisions_cost_usd(model, reply.input_tokens)
        self._budget.settle(reservation, charge)
        if charge > estimate:
            log_fields(
                log,
                logging.WARNING,
                "decision_over_reservation",
                purpose=request.purpose,
                version=request.version,
                estimate_usd=round(estimate, 8),
                actual_usd=round(charge, 8),
            )
        result = DecisionResult(
            outcome=reply.outcome,
            answers=reply.answers if reply.outcome == "ok" else {},
            model=model,
            input_tokens=reply.input_tokens,
            cost_usd=charge,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_id=reply.request_id,
        )
        _log_call(request, result, reply)
        return result

    def _client(self) -> openai.OpenAI:
        with self._sdk_lock:
            if self._sdk is None:
                base = self._given_client
                if base is None:
                    base = openai.OpenAI(api_key=self._settings.require_openai_key())
                self._sdk = base.with_options(
                    timeout=self._settings.decisions_timeout_s, max_retries=0
                )
            return self._sdk

    def _exchange(
        self,
        sdk: openai.OpenAI,
        model: str,
        content: list[dict[str, Any]],
        questions: list[dict[str, Any]],
        asked: Sequence[Question],
    ) -> _Reply:
        """Send and classify what came back. Never raises for provider errors."""
        try:
            raw = sdk.decisions.with_raw_response.create(
                model=model,
                # Plain dicts in the SDK's TypedDict shapes (checked by `request_content`
                # and `check_questions`); the SDK sends them as they are.
                input=cast(Any, [{"role": "user", "content": content}]),
                questions=cast(Any, questions),
                safety_identifier=(
                    openai.omit if self._safety_identifier is None else self._safety_identifier
                ),
            )
        except openai.OpenAIError as exc:
            return _error_reply(exc)
        http_response = raw.http_response
        request_id = _request_id(http_response.headers.get("x-request-id"))
        status = http_response.status_code
        try:
            body = http_response.json()
        except ValueError:
            return _Reply("api_error", status=status, error="NonJSONBody", request_id=request_id)
        tokens = _input_tokens(body)
        if not isinstance(body, Mapping) or not isinstance(body.get("answers"), list):
            return _Reply(
                "api_error",
                input_tokens=tokens,
                status=status,
                error="UnexpectedBody",
                request_id=request_id,
            )
        outcome, answers = parse_answers(body["answers"], asked)
        return _Reply(outcome, answers, input_tokens=tokens, status=status, request_id=request_id)


# ------------------------------------------------------------------------- request


def reservation_usd(model: str, request: DecisionRequest) -> float:
    """What `decide` reserves before sending `request`: its worst-case cost (images
    once, the text parts once, every question by its own length)."""
    return estimate_decisions_max_cost_usd(
        model,
        n_images=sum(isinstance(p, ImagePart) for p in request.parts),
        text_chars=sum(len(p.text) for p in request.parts if isinstance(p, TextPart)),
        question_chars=[q.chars for q in request.questions],
    )


def request_content(request: DecisionRequest) -> list[dict[str, Any]]:
    """The user message's parts, in order. Raises ValueError for a request that could
    only fail at the provider (no parts, an image that is not PNG, too many images)."""
    if not request.parts:
        raise ValueError("a decision request needs at least one text or image part")
    images = sum(isinstance(p, ImagePart) for p in request.parts)
    if images > MAX_IMAGES:
        raise ValueError(f"at most {MAX_IMAGES} images per decision request")
    content: list[dict[str, Any]] = []
    for index, part in enumerate(request.parts):
        if isinstance(part, ImagePart):
            if not part.png.startswith(PNG_SIGNATURE):
                raise ValueError(f"part {index} is not PNG data")
            content.append(
                {"type": "input_image", "image_url": png_data_url(part.png), "detail": IMAGE_DETAIL}
            )
        else:
            content.append({"type": "input_text", "text": part.text})
    return content


def check_questions(questions: Sequence[Question]) -> list[dict[str, Any]]:
    """Wire form of the questions; ValueError for anything the endpoint would refuse."""
    if not 1 <= len(questions) <= MAX_QUESTIONS:
        raise ValueError(f"a decision request takes 1 to {MAX_QUESTIONS} questions")
    names = [q.name for q in questions]
    if any(not n for n in names) or len(set(names)) != len(names):
        raise ValueError("question names must be non-empty and unique")
    for question in questions:
        if not question.instructions.strip():
            raise ValueError("a question needs instructions (empty ones are always refused)")
        if question.kind == "choice":
            values = [c.value for c in question.choices]
            if not 2 <= len(values) <= MAX_CHOICES or len(set(values)) != len(values):
                raise ValueError(f"a choice needs 2 to {MAX_CHOICES} distinct values")
        elif question.choices:
            raise ValueError("only a choice question has choices")
    return [q.param() for q in questions]


# ------------------------------------------------------------------------ response


def parse_answers(
    raw: Sequence[object], asked: Sequence[Question]
) -> tuple[Outcome, dict[str, Answer]]:
    """Answers by question name, checked against what was asked.

    The endpoint returns one answer per question, in question order, each with the
    question's name. Anything else (a missing answer, an unknown choice value, a
    probability outside [0, 1]) is "invalid_output"; every question refused is
    "refused"; otherwise "ok" (a refused question is an answer of kind "refusal").
    """
    if len(raw) != len(asked):
        return "invalid_output", {}
    answers: dict[str, Answer] = {}
    for item, question in zip(raw, asked, strict=True):
        if not isinstance(item, Mapping) or item.get("name") != question.name:
            return "invalid_output", {}
        answer = _answer(item, question)
        if answer is None:
            return "invalid_output", {}
        answers[question.name] = answer
    if all(a.refused for a in answers.values()):
        return "refused", answers
    return "ok", answers


def _answer(item: Mapping[str, object], question: Question) -> Answer | None:
    kind = item.get("type")
    if kind == "refusal":
        return Answer("refusal")
    if kind == "predicate" and question.kind == "predicate":
        p = _probability(item.get("probability"))
        return None if p is None else Answer("predicate", probability=p)
    if kind == "choice" and question.kind == "choice":
        allowed = {c.value for c in question.choices}
        found = _distribution(item.get("probabilities"), "value", allowed)
        return None if found is None else Answer("choice", probabilities=found)
    return None


def _distribution(raw: object, key: str, allowed: set[str]) -> dict[str, float] | None:
    if not isinstance(raw, list):
        return None
    out: dict[str, float] = {}
    for entry in raw:
        if not isinstance(entry, Mapping):
            return None
        value, p = entry.get(key), _probability(entry.get("probability"))
        if not isinstance(value, str) or value not in allowed or p is None:
            return None
        out[value] = p
    return out if set(out) == allowed else None


def _probability(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if 0.0 <= number <= 1.0 else None


def _input_tokens(body: object) -> int:
    usage = body.get("usage") if isinstance(body, Mapping) else None
    tokens = usage.get("input_tokens") if isinstance(usage, Mapping) else None
    return tokens if isinstance(tokens, int) and tokens > 0 else 0


def _error_reply(exc: openai.OpenAIError) -> _Reply:
    status = getattr(exc, "status_code", None)
    request_id = None
    response = getattr(exc, "response", None)
    if response is not None:
        request_id = _request_id(response.headers.get("x-request-id"))
    return _Reply(
        exception_outcome(exc),
        status=status if isinstance(status, int) else None,
        code=_safe_code(getattr(exc, "code", None)),
        error=type(exc).__name__,
        request_id=request_id,
        billed_unseen=_may_have_reached_provider(exc),
    )


def _may_have_reached_provider(exc: openai.OpenAIError) -> bool:
    """A timeout or broken connection that can happen after the request was sent."""
    if not isinstance(exc, openai.APIConnectionError):
        return False
    return type(exc.__cause__).__name__ not in _NOT_SENT_ERRORS


def _safe_code(code: object) -> str | None:
    if code is None or code == "":
        return None
    return code if isinstance(code, str) and _SAFE_CODE.fullmatch(code) else "other"


def _request_id(value: object) -> str | None:
    return value if isinstance(value, str) and _REQUEST_ID.fullmatch(value) else None


def _log_call(request: DecisionRequest, result: DecisionResult, reply: _Reply) -> None:
    """One line per request: codes and numbers only (never the questions' input)."""
    refused = sum(a.refused for a in reply.answers.values())
    log_fields(
        log,
        logging.DEBUG if result.outcome == "ok" else logging.WARNING,
        "decision_call",
        purpose=request.purpose,
        version=request.version,
        model=result.model,
        outcome=result.outcome,
        questions=len(request.questions),
        refused=refused,
        input_tokens=result.input_tokens,
        cost_usd=round(result.cost_usd, 8),
        latency_ms=result.latency_ms,
        status=reply.status,
        code=reply.code,
        error=reply.error,
        request_id=result.request_id,
    )


def _check_safety_identifier(value: str) -> None:
    if not value or len(value) > SAFETY_IDENTIFIER_MAX_CHARS:
        raise ValueError(f"safety_identifier must be 1-{SAFETY_IDENTIFIER_MAX_CHARS} characters")
    if "@" in value:
        raise ValueError("safety_identifier must be a hash of the user id, not an email")
