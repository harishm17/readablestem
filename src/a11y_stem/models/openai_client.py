"""`VisionClient` backed by the OpenAI Responses API with structured outputs.

One `run()` is one logical request: check the request, reserve its worst-case cost, send it
(the SDK retries transient failures itself, `settings.model_max_retries` times, each attempt
bounded by `settings.model_timeout_s`), read the usage, settle the reservation at the real
cost, and return the parsed object with a content-free `ModelCall`.

Why it is built this way:

- Reserve before sending (architecture rule 3). The reservation is the upper bound from
  `pricing.estimate_max_cost_usd`. If the budget refuses it, `BudgetExceeded` propagates
  and nothing leaves the machine. A missing API key, an unpriced model or a malformed
  request is raised before the reservation for the same reason.
- `responses.with_raw_response.parse` instead of `responses.parse`. It sends the same
  request, but the SDK checks the model's JSON against the schema while parsing and raises on
  truncated or schema-invalid output. Those calls are still billed, so we keep the raw HTTP
  body and record their real usage.
- Outcomes, not exceptions. Provider failures become `ModelCall.outcome` codes so the
  router can fall back and the step can flag the element. Exception messages are never
  logged or stored: an API error can quote the request, and the request carries document
  content. Logs get the exception class, HTTP status and the provider's error code only.
- `store=False`: OpenAI does not keep the response for later retrieval.
- `prompt_cache_key` = step + prompt version. Requests that share the constant
  instructions are routed together, so that prefix is billed at the cached-input rate.
- Cost comes from the response's usage block. With no usage (connection failure, error
  status, timeout) the reservation is released at $0. Caveat: OpenAI may still process and
  bill a request that timed out on our side, and we cannot see that usage. The provider's
  monthly limit (layer 3 in `core.budget`) is the backstop.

Thread safety: an instance keeps no per-call state. The SDK client is created once (lazily,
under a lock), and both the OpenAI client and `JobBudget` are safe to share between threads.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal, cast

import openai
from openai.types.responses import (
    ResponseInputContentParam,
    ResponseInputImageParam,
    ResponseInputTextParam,
)
from pydantic import ValidationError

from a11y_stem.config import Settings
from a11y_stem.core.budget import JobBudget
from a11y_stem.core.types import ModelCall, Outcome
from a11y_stem.models.base import T, VisionRequest, fence_untrusted
from a11y_stem.models.pricing import cost_usd, estimate_max_cost_usd

log = logging.getLogger(__name__)

PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"

# The SDK also accepts "original", but pricing.MAX_IMAGE_TOKENS only bounds the tiled
# "high" detail, so the reservation would stop being an upper bound.
ImageDetail = Literal["low", "high", "auto"]
IMAGE_DETAILS: Final[frozenset[str]] = frozenset({"low", "high", "auto"})

# Provider error codes are short identifiers ("rate_limit_exceeded", "invalid_image").
# Anything else is logged as "other" so a log line can never carry free text.
_SAFE_CODE: Final = re.compile(r"[a-z0-9_.\-]{1,64}")

SAFETY_IDENTIFIER_MAX_CHARS: Final = 64


@dataclass(frozen=True)
class Usage:
    """Token counts from one response (zeros when the provider reported none)."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0  # includes reasoning tokens
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class _Reply:
    """What one exchange with the provider produced. Content-free apart from `parsed`."""

    outcome: Outcome
    parsed: Any = None
    usage: Usage = field(default_factory=Usage)
    error: str | None = None  # exception class name, when the SDK raised
    status: int | None = None  # HTTP status, when there was one
    code: str | None = None  # provider error code, sanitised by _safe_code


class OpenAIVisionClient:
    """`VisionClient` for the OpenAI Responses API.

    Build one per job: it charges every call to that job's `budget`. `client` lets tests and
    callers inject an `openai.OpenAI` (or a duck-typed stand-in); without it the client is
    created on first use from `settings.require_openai_key()`, so constructing this object
    never needs a key. `safety_identifier` is optional and is passed to OpenAI for abuse
    attribution. It must be an opaque hash of the user id, never an email.
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

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        """Send one request. See `models.base.VisionClient.run` for the contract.

        Raises (before anything is reserved or sent): `ValueError` for a malformed request,
        `config.ConfigError` without an API key, `pricing.UnknownModelError` for an unpriced
        model. Raises `BudgetExceeded` when the reservation is refused (nothing is sent).
        """
        content = _input_content(request)
        sdk = self._client()
        estimate = estimate_max_cost_usd(
            request.model,
            n_images=len(request.images),
            text_chars=len(request.context_text or ""),
            max_output_tokens=request.max_output_tokens,
        )
        reservation = self._budget.reserve(estimate)
        started = time.perf_counter()
        try:
            reply = self._exchange(sdk, request, schema, content)
        except BaseException:
            # Not a provider failure (those are mapped to outcomes): a bug or an interrupt.
            # Release the hold so the budget stays right, then let it surface.
            self._budget.settle(reservation, 0.0)
            raise
        latency_ms = round((time.perf_counter() - started) * 1000)
        usage = reply.usage
        cost = cost_usd(
            request.model, usage.input_tokens, usage.cached_input_tokens, usage.output_tokens
        )
        self._budget.settle(reservation, cost)
        if cost > estimate:
            # The reservation is meant to be an upper bound; if it is not, the estimate in
            # models/pricing.py needs widening (e.g. longer instructions than it assumes).
            log.warning(
                "model call cost exceeded its reservation: step=%s model=%s "
                "prompt_version=%s estimate_usd=%.6f actual_usd=%.6f",
                request.step,
                request.model,
                request.prompt_version,
                estimate,
                cost,
            )
        call = ModelCall(
            step=request.step,
            model=request.model,
            prompt_version=request.prompt_version,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            is_fallback=is_fallback,
            outcome=reply.outcome,
        )
        _log_call(call, reply)
        return cast("T | None", reply.parsed), call

    def _client(self) -> openai.OpenAI:
        """The SDK client with this app's timeout and retry policy, created once."""
        with self._sdk_lock:
            if self._sdk is None:
                base = self._given_client
                if base is None:
                    base = openai.OpenAI(api_key=self._settings.require_openai_key())
                self._sdk = base.with_options(
                    timeout=self._settings.model_timeout_s,
                    max_retries=self._settings.model_max_retries,
                )
            return self._sdk

    def _exchange(
        self,
        sdk: openai.OpenAI,
        request: VisionRequest,
        schema: type[T],
        content: list[ResponseInputContentParam],
    ) -> _Reply:
        """Send the request and classify what came back. Never raises for provider errors."""
        raw = None
        try:
            raw = sdk.responses.with_raw_response.parse(
                model=request.model,
                instructions=request.instructions,
                input=[{"role": "user", "content": content}],
                text_format=schema,
                max_output_tokens=request.max_output_tokens,
                store=False,
                prompt_cache_key=prompt_cache_key(request),
                safety_identifier=(
                    openai.omit if self._safety_identifier is None else self._safety_identifier
                ),
            )
            response = raw.parse()
        except (ValidationError, json.JSONDecodeError) as exc:
            # The model's text failed the schema (often cut off by max_output_tokens). The
            # call was billed, so read status and usage from the raw body instead.
            body = _json_body(raw)
            outcome = _response_outcome(body, parsed=None)
            return _Reply(outcome, usage=_usage(body), error=type(exc).__name__)
        except openai.OpenAIError as exc:
            return _Reply(
                exception_outcome(exc),
                error=type(exc).__name__,
                status=getattr(exc, "status_code", None),
                code=_safe_code(getattr(exc, "code", None)),
            )
        parsed = response.output_parsed
        outcome = _response_outcome(response, parsed=parsed)
        return _Reply(outcome, parsed=parsed if outcome == "ok" else None, usage=_usage(response))


def prompt_cache_key(request: VisionRequest) -> str:
    """Content-free routing key: requests with the same instructions share a cache."""
    return f"a11y-stem:{request.step}:{request.prompt_version}"


def png_data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _input_content(request: VisionRequest) -> list[ResponseInputContentParam]:
    """The user message: images first, then the fenced document text when there is any.

    Raises ValueError for requests that could only fail at the provider (no input at all,
    an image that is not PNG, an unsupported detail level).
    """
    if request.image_detail not in IMAGE_DETAILS:
        raise ValueError(f"image_detail must be one of {sorted(IMAGE_DETAILS)}")
    if not request.images and not request.context_text:
        raise ValueError("a vision request needs at least one image or context text")
    detail = cast(ImageDetail, request.image_detail)
    content: list[ResponseInputContentParam] = []
    for index, png in enumerate(request.images):
        if not png.startswith(PNG_SIGNATURE):
            raise ValueError(f"image {index} is not PNG data")
        image: ResponseInputImageParam = {
            "type": "input_image",
            "image_url": png_data_url(png),
            "detail": detail,
        }
        content.append(image)
    if request.context_text:
        text: ResponseInputTextParam = {
            "type": "input_text",
            "text": fence_untrusted(request.context_text),
        }
        content.append(text)
    return content


def exception_outcome(exc: openai.OpenAIError) -> Outcome:
    """Category for an SDK exception. Order matters: APITimeoutError is a connection error."""
    if isinstance(exc, openai.APITimeoutError):
        return "timeout"
    if isinstance(exc, openai.ContentFilterFinishReasonError):
        return "refused"  # the provider declined to produce output, like a refusal
    if isinstance(exc, openai.LengthFinishReasonError):
        return "invalid_output"  # cut off by the output token limit
    # APIConnectionError, RateLimitError and every other APIStatusError (4xx/5xx), plus
    # anything else the SDK raises: the request did not produce a usable response.
    return "api_error"


def _response_outcome(response: object, *, parsed: object | None) -> Outcome:
    """Category for a response the provider returned (typed SDK object or raw JSON body)."""
    status = _read(response, "status")
    if status not in (None, "completed", "incomplete"):
        return "api_error"  # "failed", or a background state we never request
    if _has_refusal(response):
        return "refused"
    if status == "incomplete":
        reason = _read(response, "incomplete_details", "reason")
        return "refused" if reason == "content_filter" else "invalid_output"
    if parsed is None:
        return "invalid_output"  # e.g. no message at all, only reasoning
    return "ok"


def _has_refusal(response: object) -> bool:
    for item in _read(response, "output") or []:
        if _read(item, "type") != "message":
            continue
        for part in _read(item, "content") or []:
            if _read(part, "type") == "refusal":
                return True
    return False


def _usage(response: object) -> Usage:
    usage = _read(response, "usage")
    if usage is None:
        return Usage()
    return Usage(
        input_tokens=_count(_read(usage, "input_tokens")),
        cached_input_tokens=_count(_read(usage, "input_tokens_details", "cached_tokens")),
        output_tokens=_count(_read(usage, "output_tokens")),
        reasoning_tokens=_count(_read(usage, "output_tokens_details", "reasoning_tokens")),
    )


def _read(obj: object, *path: str) -> Any:
    """`obj.a.b` for SDK objects or `obj["a"]["b"]` for raw JSON; None when absent.

    One reader for both shapes lets the raw-body fallback reuse the same classification.
    """
    for name in path:
        if obj is None:
            return None
        obj = obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)
    return obj


def _count(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def _json_body(raw: object) -> object:
    """The decoded HTTP body of a raw SDK response, or None if it cannot be read."""
    http_response = _read(raw, "http_response")
    if http_response is None:
        return None
    try:
        return http_response.json()
    except ValueError:
        return None


def _safe_code(code: object) -> str | None:
    if code is None or code == "":
        return None
    return code if isinstance(code, str) and _SAFE_CODE.fullmatch(code) else "other"


def _log_call(call: ModelCall, reply: _Reply) -> None:
    """One line per call, numbers and category codes only.

    Never the exception message or body: an API error can echo the request, which carries
    document content. Failures log at WARNING so outages are visible at the default level.
    """
    log.log(
        logging.DEBUG if call.outcome == "ok" else logging.WARNING,
        "model call: step=%s model=%s prompt_version=%s outcome=%s fallback=%s "
        "input_tokens=%d cached_input_tokens=%d output_tokens=%d reasoning_tokens=%d "
        "cost_usd=%.6f latency_ms=%d error=%s status=%s code=%s",
        call.step,
        call.model,
        call.prompt_version,
        call.outcome,
        call.is_fallback,
        call.input_tokens,
        call.cached_input_tokens,
        call.output_tokens,
        call.reasoning_tokens,
        call.cost_usd,
        call.latency_ms,
        reply.error,
        reply.status,
        reply.code,
    )


def _check_safety_identifier(value: str) -> None:
    if not value or len(value) > SAFETY_IDENTIFIER_MAX_CHARS:
        raise ValueError(f"safety_identifier must be 1-{SAFETY_IDENTIFIER_MAX_CHARS} characters")
    if "@" in value:
        raise ValueError("safety_identifier must be a hash of the user id, not an email")
