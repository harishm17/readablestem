"""`VisionClient` backed by the OpenAI Responses API with structured outputs.

One `run()` is one logical request: check the request, reserve its worst-case cost, send it,
read the usage, settle the reservation, and return the parsed object with a content-free
`ModelCall`. Transient failures are resent here, up to `settings.model_max_retries` times,
each send bounded by `settings.model_timeout_s`.

Why it is built this way:

- Reserve before every send (architecture rule 3). The reservation is the upper bound from
  `pricing.estimate_max_cost_usd`, priced on the text that is actually sent (the fenced,
  truncated context with its notice), not on the raw context length. If the budget refuses
  the first reservation, `BudgetExceeded` propagates and nothing leaves the machine. A
  missing API key, an unpriced model or a malformed request is raised before the
  reservation for the same reason.
- Retries live here, not in the SDK (its client is built with `max_retries=0`). An SDK retry
  resends the whole billable request under the one reservation, so a slow provider could
  take three requests and the budget would record one call at $0. Here every resend makes
  its own reservation (so it also counts against the job's call cap) and settles on its own.
  What is retried follows the SDK: connection errors, timeouts, HTTP 408/409/429/5xx and the
  `x-should-retry` header, waiting as `retry-after` asks up to `MAX_RETRY_AFTER_S`. If the
  budget refuses a resend, the failed outcome is returned instead of raised, so the sends
  already made stay on record and the router can decide what to do next.
- What a send is charged. With a usage block: its real cost. With an HTTP error status, or a
  connection that failed before reaching the provider: $0. With a timeout or a connection
  that broke after the request went out: the full reservation, because OpenAI may still
  process and bill that request and we cannot see its usage. Charging the upper bound keeps
  the job and daily caps meaningful during a provider slowdown. `ModelCall.cost_usd` is the
  sum of what its sends were charged, so it always equals what the budget recorded.
- `responses.with_raw_response.parse` instead of `responses.parse`. It sends the same
  request, but the SDK checks the model's JSON against the schema while parsing and raises
  on truncated or schema-invalid output. Those calls are still billed, so we keep the raw
  HTTP body and record their real usage. The body is checked before parsing too: a gateway
  can answer 200 with HTML, `null`, an array or an error object, which the SDK's parser
  fails on with AttributeError rather than an API error. Those are `api_error`.
- Outcomes, not exceptions. Provider failures become `ModelCall.outcome` codes so the
  router can fall back and the step can flag the element. Exception messages are never
  logged or stored: an API error can quote the request, and the request carries document
  content. Logs get the exception class, HTTP status and the provider's error code only.
- Document text goes through `defang_context` before `base.fence_untrusted`. The fence alone
  deletes the exact tags in one pass, so a nested, re-cased or spaced tag survives it and
  can close the fence early, putting the rest of the text outside it.
- `store=False`: OpenAI does not keep the response for later retrieval.
- `prompt_cache_key` = step + prompt version. Requests that share the constant
  instructions are routed together, so that prefix is billed at the cached-input rate.
- The provider's monthly limit (layer 3 in `core.budget`) stays the backstop for anything
  this accounting cannot see.

Thread safety: an instance keeps no per-call state. The SDK client is created once (lazily,
under a lock), and both the OpenAI client and `JobBudget` are safe to share between threads.
"""

from __future__ import annotations

import base64
import json
import logging
import random
import re
import threading
import time
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal, cast

import openai
from openai.types.responses import (
    ResponseInputContentParam,
    ResponseInputImageParam,
    ResponseInputTextParam,
)
from pydantic import ValidationError

from readablestem.config import Settings
from readablestem.core.budget import BudgetExceeded, JobBudget, Reservation
from readablestem.core.types import ModelCall, Outcome
from readablestem.models.base import T, VisionRequest, fence_untrusted, rate_gate
from readablestem.models.pricing import cost_usd, estimate_max_cost_usd
from readablestem.observability.logging import log_fields

if TYPE_CHECKING:
    from readablestem.models.verifier import Verifier

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

# Backoff between sends, as in the OpenAI SDK: 0.5 s doubling up to 8 s, minus 0-25% jitter.
INITIAL_RETRY_DELAY_S: Final = 0.5
MAX_RETRY_DELAY_S: Final = 8.0
# A server asking for a longer wait is not retried on the same model: the router's fallback
# to the other model is a better use of that time than a step sitting idle.
MAX_RETRY_AFTER_S: Final = 60.0
# Transient HTTP statuses besides 5xx (request timeout, lock contention, rate limit). An error
# status carries no usage and is not billed, so resending costs only the reservation.
RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset({408, 409, 429})

# Transport errors raised before the request reached the provider (the same class names in
# httpx and httpx2). Any other broken connection may have happened after OpenAI received
# the request, so it is charged as if billed.
_NOT_SENT_ERRORS: Final[frozenset[str]] = frozenset(
    {"ConnectError", "ConnectTimeout", "PoolTimeout"}
)

# Our fence tag's name, in any case, with whitespace, "_" or "-" (or nothing) between words.
_FENCE_NAME: Final = re.compile(r"document[\s_\-]*text", re.IGNORECASE)
# `fence_untrusted` keeps 2,000 characters. Defanging a few times that much bounds the work on
# a huge context while leaving enough text once tag names are removed.
_CONTEXT_SCAN_CHARS: Final = 8000


@dataclass(frozen=True)
class Usage:
    """Token counts from one response (zeros when the provider reported none)."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0  # includes reasoning tokens
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class _Reply:
    """What one send to the provider produced. Content-free apart from `parsed`."""

    outcome: Outcome
    parsed: Any = None
    usage: Usage = field(default_factory=Usage)
    error: str | None = None  # exception class name, or a body category, when it failed
    status: int | None = None  # HTTP status, when there was one
    code: str | None = None  # provider error code, sanitised by _safe_code
    retryable: bool = False  # transient: worth sending again to the same model
    retry_after_s: float | None = None  # the wait the server asked for, if it said
    billed_unseen: bool = False  # the provider may have billed it, but no usage came back


class OpenAIVisionClient:
    """`VisionClient` for the OpenAI Responses API.

    Build one per job: it charges every send to that job's `budget`. `client` lets tests and
    callers inject an `openai.OpenAI` (or a duck-typed stand-in); without it the client is
    created on first use from `settings.require_openai_key()`, so constructing this object
    never needs a key. `safety_identifier` is optional and is passed to OpenAI for abuse
    attribution. It must be an opaque hash of the user id, never an email. `verifier` is
    the job's second automated check (`models.verifier`, charging the same budget), which
    the steps find with `verifier.verifier_of(client)`; None when it is off.
    """

    def __init__(
        self,
        settings: Settings,
        budget: JobBudget,
        *,
        client: openai.OpenAI | None = None,
        safety_identifier: str | None = None,
        verifier: Verifier | None = None,
    ) -> None:
        if safety_identifier is not None:
            _check_safety_identifier(safety_identifier)
        self.verifier = verifier
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
        model. Raises `BudgetExceeded` when the first reservation is refused (nothing is
        sent). The returned `ModelCall` covers every send this call made: tokens and cost are
        summed, latency includes backoff, and the outcome is the last send's.
        """
        context = fenced_context(request.context_text)
        content = _input_content(request, context)
        sdk = self._client()
        estimate = estimate_max_cost_usd(
            request.model,
            n_images=len(request.images),
            text_chars=len(context or ""),
            max_output_tokens=request.max_output_tokens,
        )
        started = time.perf_counter()
        gate = rate_gate("openai")
        gate.wait()  # after a 429 anywhere in this process, wait out its retry-after
        reservation = self._budget.reserve(estimate)  # BudgetExceeded: nothing was sent
        replies: list[_Reply] = []
        charged = 0.0
        while True:
            reply, charge = self._send(sdk, request, schema, content, reservation, estimate)
            replies.append(reply)
            charged += charge
            if not reply.retryable or len(replies) > self._settings.model_max_retries:
                break
            try:
                reservation = self._budget.reserve(estimate)
            except BudgetExceeded as exc:
                # No room for another send. Return the failure we have rather than raise, so
                # the sends already made (and charged) are not lost to the caller.
                log_fields(
                    log,
                    logging.INFO,
                    "model_call_retry_skipped",
                    step=request.step,
                    model=request.model,
                    budget_scope=exc.scope,
                )
                break
            delay = _retry_delay_s(len(replies), reply.retry_after_s)
            if reply.status == 429:
                gate.pause(
                    delay
                )  # the other steps in flight wait too, instead of each being refused
            _log_retry(request, reply, sends=len(replies), delay_s=delay)
            try:
                _sleep(delay)
            except BaseException:
                self._budget.settle(reservation, 0.0)
                raise
        last = replies[-1]
        usage = _total(r.usage for r in replies)
        call = ModelCall(
            step=request.step,
            model=request.model,
            prompt_version=request.prompt_version,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cost_usd=charged,
            latency_ms=round((time.perf_counter() - started) * 1000),
            is_fallback=is_fallback,
            outcome=last.outcome,
        )
        _log_call(call, last, sends=len(replies))
        return cast("T | None", last.parsed), call

    def _client(self) -> openai.OpenAI:
        """The SDK client with this app's timeout, created once. Retries are done in run()."""
        with self._sdk_lock:
            if self._sdk is None:
                base = self._given_client
                if base is None:
                    base = openai.OpenAI(api_key=self._settings.require_openai_key())
                self._sdk = base.with_options(
                    timeout=self._settings.model_timeout_s,
                    max_retries=0,
                )
            return self._sdk

    def _send(
        self,
        sdk: openai.OpenAI,
        request: VisionRequest,
        schema: type[T],
        content: list[ResponseInputContentParam],
        reservation: Reservation,
        estimate: float,
    ) -> tuple[_Reply, float]:
        """One HTTP exchange under `reservation`, settled before returning (with the charge)."""
        try:
            reply = self._exchange(sdk, request, schema, content)
        except BaseException:
            # Not a provider failure (those are mapped to outcomes): a bug or an interrupt.
            # Release the hold so the budget stays right, then let it surface.
            self._budget.settle(reservation, 0.0)
            raise
        charge = _charge(request.model, reply, estimate)
        self._budget.settle(reservation, charge)
        if charge > estimate:
            # The reservation is meant to be an upper bound; if it is not, the estimate in
            # models/pricing.py needs widening (e.g. longer instructions than it assumes).
            log_fields(
                log,
                logging.WARNING,
                "model_call_over_reservation",
                step=request.step,
                model=request.model,
                prompt_version=request.prompt_version,
                estimate_usd=round(estimate, 6),
                actual_usd=round(charge, 6),
            )
        return reply, charge

    def _exchange(
        self,
        sdk: openai.OpenAI,
        request: VisionRequest,
        schema: type[T],
        content: list[ResponseInputContentParam],
    ) -> _Reply:
        """Send the request and classify what came back. Never raises for provider errors."""
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
        except openai.OpenAIError as exc:
            return _error_reply(exc)

        body: object = None
        http_response = _read(raw, "http_response")
        status = _read(http_response, "status_code")
        if http_response is not None:  # None only for a duck-typed SDK with no HTTP layer
            try:
                body = http_response.json()
            except ValueError:
                return _Reply("api_error", error="NonJSONBody", status=status)
            if not _is_response_body(body):
                code = _read(body, "error", "code") if isinstance(body, Mapping) else None
                return _Reply(
                    "api_error", error="UnexpectedBody", status=status, code=_safe_code(code)
                )

        try:
            response = raw.parse()
        except (ValidationError, json.JSONDecodeError) as exc:
            # The model's text failed the schema (often cut off by max_output_tokens). The
            # call was billed, so read status and usage from the raw body instead.
            outcome = _response_outcome(body, parsed=None)
            return _Reply(outcome, usage=_usage(body), error=type(exc).__name__)
        except openai.OpenAIError as exc:
            return _error_reply(exc)
        except (AttributeError, TypeError) as exc:
            # A JSON object whose items the SDK's parser cannot walk (an output item or a
            # content part that is not an object). Malformed provider output, not our bug.
            return _Reply("api_error", usage=_usage(body), error=type(exc).__name__, status=status)
        parsed = response.output_parsed
        outcome = _response_outcome(response, parsed=parsed)
        return _Reply(outcome, parsed=parsed if outcome == "ok" else None, usage=_usage(response))


def prompt_cache_key(request: VisionRequest) -> str:
    """Content-free routing key: requests with the same instructions share a cache."""
    return f"readablestem:{request.step}:{request.prompt_version}"


def png_data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def defang_context(text: str) -> str:
    """Document text with every spelling of the fence tag's name removed.

    `base.fence_untrusted` deletes the exact tags once, so '</document_te</document_text>xt>'
    rebuilds a closing tag, and '</DOCUMENT_TEXT>' or '</document_text >' pass untouched. A
    model can read any of them as the end of the fence and the text after it as
    instructions. Removing the tag *name* in any case and spacing, after dropping invisible
    format characters (zero-width spaces, Unicode tag characters) that could hide inside it,
    and repeating until nothing changes, leaves no way to spell either tag whatever brackets
    or entities surround it. Only the start of a long text is scanned: the fence keeps 2,000
    characters anyway.
    """
    text = text[:_CONTEXT_SCAN_CHARS]
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    while True:
        cleaned = _FENCE_NAME.sub("", text)
        if cleaned == text:
            return cleaned
        text = cleaned


def fenced_context(text: str | None) -> str | None:
    """The exact text sent to the model for `text` (notice + fence), or None if empty."""
    return fence_untrusted(defang_context(text)) if text else None


def _input_content(request: VisionRequest, context: str | None) -> list[ResponseInputContentParam]:
    """The user message: images first, then the fenced document text when there is any.

    Raises ValueError for requests that could only fail at the provider (no input at all,
    an image that is not PNG, an unsupported detail level).
    """
    if request.image_detail not in IMAGE_DETAILS:
        raise ValueError(f"image_detail must be one of {sorted(IMAGE_DETAILS)}")
    if not request.images and not context:
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
    if context:
        text: ResponseInputTextParam = {"type": "input_text", "text": context}
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


def _error_reply(exc: openai.OpenAIError) -> _Reply:
    """Classify an SDK exception: outcome, whether to resend, whether it may be billed."""
    retryable = False
    retry_after: float | None = None
    if isinstance(exc, openai.APIConnectionError):  # includes APITimeoutError
        retryable = True
    elif isinstance(exc, openai.APIStatusError):
        headers = exc.response.headers
        retry_after = _retry_after_s(headers)
        retryable = _status_is_retryable(exc.status_code, headers, retry_after)
    return _Reply(
        exception_outcome(exc),
        error=type(exc).__name__,
        status=getattr(exc, "status_code", None),
        code=_safe_code(getattr(exc, "code", None)),
        retryable=retryable,
        retry_after_s=retry_after,
        billed_unseen=_may_have_reached_provider(exc),
    )


def _may_have_reached_provider(exc: openai.OpenAIError) -> bool:
    """True for a connection failure or timeout that can happen after the request was sent."""
    if not isinstance(exc, openai.APIConnectionError):
        return False
    return type(exc.__cause__).__name__ not in _NOT_SENT_ERRORS


def _status_is_retryable(
    status: int, headers: Mapping[str, str], retry_after: float | None
) -> bool:
    """The SDK's rule: obey x-should-retry, else retry 408/409/429/5xx."""
    if retry_after is not None and retry_after > MAX_RETRY_AFTER_S:
        return False
    should_retry = headers.get("x-should-retry")
    if should_retry in ("true", "false"):
        return should_retry == "true"
    return status in RETRYABLE_STATUSES or status >= 500


def _retry_after_s(headers: Mapping[str, str]) -> float | None:
    """Seconds from `retry-after-ms` or a numeric `retry-after`; None if absent or a date."""
    for name, divisor in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        value = headers.get(name)
        if value is None:
            continue
        try:
            return float(value) / divisor
        except ValueError:
            continue
    return None


def _retry_delay_s(sends: int, retry_after: float | None) -> float:
    """Wait before resend number `sends` (1 for the first retry)."""
    if retry_after is not None and 0 < retry_after <= MAX_RETRY_AFTER_S:
        return retry_after
    backoff = min(INITIAL_RETRY_DELAY_S * 2 ** (sends - 1), MAX_RETRY_DELAY_S)
    return backoff * (1 - 0.25 * random.random())


def _sleep(seconds: float) -> None:
    """The backoff wait. A module function so tests can skip the waiting."""
    time.sleep(seconds)


def _charge(model: str, reply: _Reply, estimate: float) -> float:
    """What one send costs the budget: its usage, or the reservation if billed unseen."""
    if reply.billed_unseen:
        return estimate
    usage = reply.usage
    return cost_usd(model, usage.input_tokens, usage.cached_input_tokens, usage.output_tokens)


def _total(usages: Iterable[Usage]) -> Usage:
    total = Usage()
    for usage in usages:
        total = Usage(
            input_tokens=total.input_tokens + usage.input_tokens,
            cached_input_tokens=total.cached_input_tokens + usage.cached_input_tokens,
            output_tokens=total.output_tokens + usage.output_tokens,
            reasoning_tokens=total.reasoning_tokens + usage.reasoning_tokens,
        )
    return total


def _is_response_body(body: object) -> bool:
    """A JSON object shaped like a Responses API response (it has an `output` list)."""
    return (
        isinstance(body, Mapping)
        and "output" in body
        and (body["output"] is None or isinstance(body["output"], list))
    )


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


def _safe_code(code: object) -> str | None:
    if code is None or code == "":
        return None
    return code if isinstance(code, str) and _SAFE_CODE.fullmatch(code) else "other"


def _log_retry(request: VisionRequest, reply: _Reply, *, sends: int, delay_s: float) -> None:
    """One line per resend, category codes only (see `_log_call`)."""
    log_fields(
        log,
        logging.INFO,
        "model_call_retry",
        step=request.step,
        model=request.model,
        prompt_version=request.prompt_version,
        sends=sends,
        outcome=reply.outcome,
        error=reply.error,
        status=reply.status,
        code=reply.code,
        delay_s=round(delay_s, 3),
    )


def _log_call(call: ModelCall, reply: _Reply, *, sends: int) -> None:
    """One line per call, numbers and category codes only.

    Never the exception message or body: an API error can echo the request, which carries
    document content. Failures log at WARNING so outages are visible at the default level.
    Every value is its own JSON key (`jsonPayload.code="project_spend_limit_exceeded"`).
    """
    log_fields(
        log,
        logging.DEBUG if call.outcome == "ok" else logging.WARNING,
        "model_call",
        step=call.step,
        model=call.model,
        prompt_version=call.prompt_version,
        outcome=call.outcome,
        fallback=call.is_fallback,
        sends=sends,
        input_tokens=call.input_tokens,
        cached_input_tokens=call.cached_input_tokens,
        output_tokens=call.output_tokens,
        reasoning_tokens=call.reasoning_tokens,
        cost_usd=round(call.cost_usd, 6),
        latency_ms=call.latency_ms,
        error=reply.error,
        status=reply.status,
        code=reply.code,
    )


def _check_safety_identifier(value: str) -> None:
    if not value or len(value) > SAFETY_IDENTIFIER_MAX_CHARS:
        raise ValueError(f"safety_identifier must be 1-{SAFETY_IDENTIFIER_MAX_CHARS} characters")
    if "@" in value:
        raise ValueError("safety_identifier must be a hash of the user id, not an email")
