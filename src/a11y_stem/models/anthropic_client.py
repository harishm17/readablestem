"""`VisionClient` backed by the Anthropic Messages API with structured outputs.

The second provider next to `openai_client`, behind the same `VisionClient` contract
(`models.base`): one `run()` is one logical request that checks the request, reserves its
worst-case cost, sends it, reads the usage, settles the reservation and returns the parsed
object with a content-free `ModelCall`. The accounting rules are the OpenAI client's (read
its module docstring): reserve before every send, retries here and not in the SDK (the SDK
client is built with `max_retries=0`, every resend reserves and settles on its own), a
timeout or a connection that broke after the request went out is charged its full
reservation, provider failures become `ModelCall.outcome` codes, and neither an exception
message nor a body is ever logged. The retry rules are shared with that module.

What is specific to Claude:

- Official `anthropic` SDK only. Images are base64 `image` content blocks (PNG). The
  step's instructions go in `system` with `cache_control: ephemeral`, the stable prefix the
  prompt cache bills at the cache-read rate from the second request on; the images and the
  fenced document text come after the breakpoint. `base.fence_untrusted` and
  `defang_context` apply exactly as for OpenAI.
- Structured output: `output_config.format` carries the step's Pydantic schema (through
  `anthropic.transform_schema`, the transformation `messages.parse` applies), and the reply
  is checked against the original schema here. That is `messages.parse` split in two on
  purpose: `parse` raises on a cut-off or schema-invalid reply and drops the response, but
  such a call is still billed and its usage must be recorded.
- Model rules, from the claude-api reference: no `temperature`, `top_p` or `top_k` (the
  newest models reject non-default values), no assistant prefill, no `tool_choice` (forced
  tool use is rejected on the 5.5 models, which is why structured outputs carry the
  schema), no `thinking` field. claude-opus-5-5 cannot turn thinking off and
  claude-sonnet-5-5 / claude-haiku-5-5 think by default, so `output_config.effort`
  (`Settings.anthropic_effort_main` / `_cheap`) is the control. Thinking counts toward
  `max_tokens`, so the client sends the step's output limit plus
  `pricing.THINKING_ALLOWANCE_TOKENS`, and the reservation prices that same limit.
- Refusal fallback: for claude-opus-5-5 and claude-sonnet-5-5 the request goes to the beta
  Messages endpoint with `fallbacks="default"` and the beta header
  `server-side-fallback-2026-07-01`, so a request a safety classifier declines is re-run by
  Anthropic on the model it recommends, inside the same call
  (`Settings.anthropic_refusal_fallback`; claude-haiku-5-5 has no server-side fallback).
  When a fallback ran, the cost is the sum over `usage.iterations`, each priced at the
  model that ran it (unknown models at the reservation's ceiling rates).
- Usage: `input_tokens` excludes cached tokens in Anthropic's numbers; `ModelCall.input_tokens`
  is the whole prompt (uncached + cache read + cache write), `cached_input_tokens` the cache
  read, `cache_creation_input_tokens` the write, `output_tokens` everything generated
  (thinking included, as billed) and `reasoning_tokens` the thinking part when reported.
- Outcomes: `stop_reason == "refusal"` is `refused` (`stop_details` is read only then, for
  its category code in the log); `max_tokens` (or a full context window) is
  `invalid_output`; `RateLimitError`, a status of 500 or more (529 overloaded included),
  `APIConnectionError` and other API errors are `api_error`, `APITimeoutError` is
  `timeout`; a 400 (`BadRequestError`) is `api_error` and not retried.
- `VisionRequest.image_detail` has no Claude counterpart and is ignored (it is still
  checked, so a bad value fails the same way on both providers).
- `ANTHROPIC_WORKSPACE_ID`, when set, is sent as the `anthropic-workspace-id` header on
  every request (an organisation-level key is refused without it).
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, cast

import anthropic
import httpx2
from pydantic import ValidationError

from a11y_stem.config import Settings
from a11y_stem.core.budget import BudgetExceeded, JobBudget, Reservation
from a11y_stem.core.types import ModelCall, Outcome
from a11y_stem.models.base import T, VisionRequest, rate_gate
from a11y_stem.models.openai_client import (
    _NOT_SENT_ERRORS,
    IMAGE_DETAILS,
    PNG_SIGNATURE,
    _count,
    _read,
    _retry_after_s,
    _retry_delay_s,
    _safe_code,
    _status_is_retryable,
    fenced_context,
)
from a11y_stem.models.pricing import (
    PRICES,
    ceiling_cost_usd,
    cost_usd,
    estimate_max_cost_usd,
    output_token_limit,
)
from a11y_stem.observability.logging import log_fields

log = logging.getLogger(__name__)

WORKSPACE_HEADER: Final = "anthropic-workspace-id"
# The models that accept the server-side refusal fallback. claude-haiku-5-5 has none (a
# `fallbacks` list is a 400 there).
FALLBACK_MODELS: Final[frozenset[str]] = frozenset({"claude-opus-5-5", "claude-sonnet-5-5"})
FALLBACK_BETA: Final = "server-side-fallback-2026-07-01"
IMAGE_MEDIA_TYPE: Final = "image/png"

# stop_reason values that mean the reply is a complete answer.
_FINISHED: Final = frozenset({"end_turn", "stop_sequence"})
# Cut off: by `max_tokens` (thinking included) or by the context window.
_CUT_OFF: Final = frozenset({"max_tokens", "model_context_window_exceeded"})


@dataclass(frozen=True)
class Usage:
    """Token counts from one response (zeros when the provider reported none)."""

    input_tokens: int = 0  # the whole prompt: uncached + cache read + cache write
    cached_input_tokens: int = 0  # cache read
    cache_creation_input_tokens: int = 0  # cache write
    output_tokens: int = 0  # thinking included
    reasoning_tokens: int = 0  # the thinking part of output_tokens, when reported


@dataclass(frozen=True)
class _Reply:
    """What one send to the provider produced. Content-free apart from `parsed`."""

    outcome: Outcome
    parsed: Any = None
    usage: Usage = field(default_factory=Usage)
    charge: float | None = None  # the cost of the usage; None: priced from `usage` alone
    error: str | None = None  # exception class name, or a body category, when it failed
    status: int | None = None  # HTTP status, when there was one
    code: str | None = None  # provider error type / refusal category, sanitised
    retryable: bool = False  # transient: worth sending again to the same model
    retry_after_s: float | None = None
    billed_unseen: bool = False  # the provider may have billed it, but no usage came back
    served_by: str | None = None  # the model that answered, when it is not the one asked


class AnthropicVisionClient:
    """`VisionClient` for the Anthropic Messages API.

    Build one per job: it charges every send to that job's `budget`. `client` lets tests
    inject an `anthropic.Anthropic`; without it the SDK client is created on first use from
    `settings.require_anthropic_key()` (so constructing this object never needs a key), with
    the workspace header when `settings.anthropic_workspace_id` is set. `http_client` is
    passed to that SDK client (tests give it a scripted transport). `verifier` is the job's
    second automated check, if any (`models.claude_verifier`, T2-20); `count_calls=False`
    makes the client reserve dollars only, which is how the check's own client is built.
    """

    def __init__(
        self,
        settings: Settings,
        budget: JobBudget,
        *,
        client: anthropic.Anthropic | None = None,
        http_client: httpx2.Client | None = None,
        verifier: Any = None,
        count_calls: bool = True,
    ) -> None:
        self.verifier = verifier
        # False: every send holds dollars only, not one of the job's model calls (the
        # second check's client, `models.claude_verifier`).
        self._count_calls = count_calls
        self._settings = settings
        self._budget = budget
        self._given_client = client
        self._http_client = http_client
        self._sdk: anthropic.Anthropic | None = None
        self._sdk_lock = threading.Lock()

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        """Send one request. See `models.base.VisionClient.run` for the contract.

        Raises (before anything is reserved or sent): `ValueError` for a malformed request,
        `config.ConfigError` without an API key, `pricing.UnknownModelError` for an unpriced
        model. Raises `BudgetExceeded` when the first reservation is refused (nothing is
        sent). The returned `ModelCall` covers every send this call made.
        """
        context = fenced_context(request.context_text)
        content = _user_content(request, context)
        sdk = self._client()
        estimate = estimate_max_cost_usd(
            request.model,
            n_images=len(request.images),
            text_chars=len(context or ""),
            max_output_tokens=request.max_output_tokens,
            images=request.images,
        )
        started = time.perf_counter()
        gate = rate_gate("anthropic")
        gate.wait()  # after a 429 anywhere in this process, wait out its retry-after
        reservation = self._budget.reserve(
            estimate, count_call=self._count_calls
        )  # BudgetExceeded: nothing was sent
        replies: list[_Reply] = []
        charged = 0.0
        while True:
            reply, charge = self._send(sdk, request, schema, content, reservation, estimate)
            replies.append(reply)
            charged += charge
            if not reply.retryable or len(replies) > self._settings.model_max_retries:
                break
            try:
                reservation = self._budget.reserve(estimate, count_call=self._count_calls)
            except BudgetExceeded as exc:
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
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cost_usd=charged,
            latency_ms=round((time.perf_counter() - started) * 1000),
            is_fallback=is_fallback,
            outcome=last.outcome,
        )
        _log_call(call, last, sends=len(replies))
        return cast("T | None", last.parsed), call

    def _client(self) -> anthropic.Anthropic:
        """The SDK client with this app's timeout, created once. Retries are done in run()."""
        with self._sdk_lock:
            if self._sdk is None:
                base = self._given_client
                if base is None:
                    base = build_sdk(self._settings, http_client=self._http_client)
                self._sdk = base.with_options(
                    timeout=self._settings.model_timeout_s,
                    max_retries=0,
                )
            return self._sdk

    def _send(
        self,
        sdk: anthropic.Anthropic,
        request: VisionRequest,
        schema: type[T],
        content: list[dict[str, Any]],
        reservation: Reservation,
        estimate: float,
    ) -> tuple[_Reply, float]:
        """One HTTP exchange under `reservation`, settled before returning (with the charge)."""
        try:
            reply = self._exchange(sdk, request, schema, content)
        except BaseException:
            # Not a provider failure (those are mapped to outcomes): a bug or an interrupt.
            self._budget.settle(reservation, 0.0)
            raise
        charge = _charge(request.model, reply, estimate)
        self._budget.settle(reservation, charge)
        if charge > estimate:
            # The reservation is meant to be an upper bound; if it is not, the estimate in
            # models/pricing.py needs widening.
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
        sdk: anthropic.Anthropic,
        request: VisionRequest,
        schema: type[T],
        content: list[dict[str, Any]],
    ) -> _Reply:
        """Send the request and classify what came back. Never raises for provider errors."""
        params = self._params(request, schema, content)
        try:
            if self._uses_fallback(request.model):
                raw = sdk.beta.messages.with_raw_response.create(
                    **params, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                raw = sdk.messages.with_raw_response.create(**params)
        except anthropic.AnthropicError as exc:
            return _error_reply(exc)

        http_response = _read(raw, "http_response")
        status = _read(http_response, "status_code")
        body: object = None
        if http_response is not None:  # None only for a duck-typed SDK with no HTTP layer
            try:
                body = http_response.json()
            except ValueError:
                return _Reply("api_error", error="NonJSONBody", status=status)
            if not _is_message_body(body):
                kind = _read(body, "error", "type") if isinstance(body, Mapping) else None
                return _Reply(
                    "api_error", error="UnexpectedBody", status=status, code=_safe_code(kind)
                )
        try:
            message = raw.parse()
        except anthropic.AnthropicError as exc:
            return _error_reply(exc)
        except (ValueError, AttributeError, TypeError) as exc:
            # A JSON object whose blocks the SDK cannot walk: malformed provider output.
            return _Reply("api_error", error=type(exc).__name__, status=status)
        return self._read_message(request, schema, message)

    def _params(
        self, request: VisionRequest, schema: type[T], content: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """The Messages request for `request`, with only what every Claude model accepts."""
        return {
            "model": request.model,
            "max_tokens": output_token_limit(request.model, request.max_output_tokens),
            "system": [
                {
                    "type": "text",
                    "text": request.instructions,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": content}],
            "output_config": {
                "effort": self._settings.anthropic_effort_for(request.model),
                "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)},
            },
        }

    def _uses_fallback(self, model: str) -> bool:
        return self._settings.anthropic_refusal_fallback and model in FALLBACK_MODELS

    def _read_message(self, request: VisionRequest, schema: type[T], message: Any) -> _Reply:
        usage, charge = _usage_and_cost(message, request.model)
        served = _read(message, "model")
        served_by = served if isinstance(served, str) and served != request.model else None
        stop = _read(message, "stop_reason")
        if stop == "refusal":
            # stop_details is meaningful only here; the category is a short code.
            category = _read(message, "stop_details", "category")
            return _Reply(
                "refused",
                usage=usage,
                charge=charge,
                code=_safe_code(category),
                served_by=served_by,
            )
        if stop in _CUT_OFF:
            return _Reply(
                "invalid_output", usage=usage, charge=charge, code=stop, served_by=served_by
            )
        if stop not in _FINISHED:
            # pause_turn, tool_use or nothing: not a finished answer to this request.
            return _Reply(
                "api_error",
                usage=usage,
                charge=charge,
                error="UnexpectedStopReason",
                code=_safe_code(stop),
                served_by=served_by,
            )
        text = _answer_text(message)
        if not text:
            return _Reply("invalid_output", usage=usage, charge=charge, served_by=served_by)
        try:
            parsed = schema.model_validate_json(text)
        except ValidationError as exc:
            return _Reply(
                "invalid_output",
                usage=usage,
                charge=charge,
                error=type(exc).__name__,
                served_by=served_by,
            )
        return _Reply("ok", parsed=parsed, usage=usage, charge=charge, served_by=served_by)


def build_sdk(
    settings: Settings, *, http_client: httpx2.Client | None = None
) -> anthropic.Anthropic:
    """The SDK client: the key from settings (never from the environment), and the
    workspace header when one is configured. Retries are off (the client retries itself)."""
    headers = (
        {WORKSPACE_HEADER: settings.anthropic_workspace_id}
        if settings.anthropic_workspace_id
        else None
    )
    return anthropic.Anthropic(
        api_key=settings.require_anthropic_key(),
        default_headers=headers,
        max_retries=0,
        http_client=http_client,
    )


def _sleep(seconds: float) -> None:
    """The backoff wait. A module function so tests can skip the waiting."""
    time.sleep(seconds)


def _user_content(request: VisionRequest, context: str | None) -> list[dict[str, Any]]:
    """The user message: images first, then the fenced document text when there is any.

    Raises ValueError for requests that could only fail at the provider (no input at all,
    an image that is not PNG, an unsupported detail level).
    """
    if request.image_detail not in IMAGE_DETAILS:
        raise ValueError(f"image_detail must be one of {sorted(IMAGE_DETAILS)}")
    if not request.images and not context:
        raise ValueError("a vision request needs at least one image or context text")
    content: list[dict[str, Any]] = []
    for index, png in enumerate(request.images):
        if not png.startswith(PNG_SIGNATURE):
            raise ValueError(f"image {index} is not PNG data")
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": IMAGE_MEDIA_TYPE,
                    "data": base64.standard_b64encode(png).decode("ascii"),
                },
            }
        )
    if context:
        content.append({"type": "text", "text": context})
    return content


def _answer_text(message: Any) -> str:
    """The text of the answer: every text block after the last `fallback` marker (a block
    from a declined model before it is not the answer), thinking blocks skipped."""
    parts: list[str] = []
    for block in _read(message, "content") or []:
        kind = _read(block, "type")
        if kind == "fallback":
            parts.clear()
        elif kind == "text":
            text = _read(block, "text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts).strip()


def _is_message_body(body: object) -> bool:
    """A JSON object shaped like a Messages API response (it has a `content` list)."""
    return isinstance(body, Mapping) and isinstance(body.get("content"), list)


def _usage_and_cost(message: Any, model: str) -> tuple[Usage, float]:
    """Usage and its cost. With a refusal fallback that ran, the sum over the attempts."""
    usage = _read(message, "usage")
    if usage is None:
        return Usage(), 0.0
    iterations = _read(usage, "iterations")
    entries = [e for e in iterations or [] if _read(e, "type") in ("message", "fallback_message")]
    if any(_read(e, "type") == "fallback_message" for e in entries):
        total = Usage()
        cost = 0.0
        for entry in entries:
            part = _one_usage(entry)
            total = _add(total, part)
            cost += _entry_cost(_read(entry, "model"), model, part)
        return total, cost
    one = _one_usage(usage)
    return one, cost_usd(
        model,
        one.input_tokens,
        one.cached_input_tokens,
        one.output_tokens,
        one.cache_creation_input_tokens,
    )


def _one_usage(usage: object) -> Usage:
    uncached = _count(_read(usage, "input_tokens"))
    read = _count(_read(usage, "cache_read_input_tokens"))
    write = _count(_read(usage, "cache_creation_input_tokens"))
    return Usage(
        input_tokens=uncached + read + write,
        cached_input_tokens=read,
        cache_creation_input_tokens=write,
        output_tokens=_count(_read(usage, "output_tokens")),
        reasoning_tokens=_count(_read(usage, "output_tokens_details", "thinking_tokens")),
    )


def _entry_cost(ran: object, asked: str, usage: Usage) -> float:
    """One attempt's cost: at the rates of the model that ran it when that model is priced,
    else at the dearer of the asked model's rates and its fallback ceiling."""
    model = ran if isinstance(ran, str) and ran in PRICES else None
    args = (
        usage.input_tokens,
        usage.cached_input_tokens,
        usage.output_tokens,
        usage.cache_creation_input_tokens,
    )
    if model is not None:
        return cost_usd(model, *args)
    return ceiling_cost_usd(asked, *args)


def _add(a: Usage, b: Usage) -> Usage:
    return Usage(
        a.input_tokens + b.input_tokens,
        a.cached_input_tokens + b.cached_input_tokens,
        a.cache_creation_input_tokens + b.cache_creation_input_tokens,
        a.output_tokens + b.output_tokens,
        a.reasoning_tokens + b.reasoning_tokens,
    )


def _total(usages: Iterable[Usage]) -> Usage:
    total = Usage()
    for usage in usages:
        total = _add(total, usage)
    return total


def _charge(model: str, reply: _Reply, estimate: float) -> float:
    """What one send costs the budget: its usage, or the reservation if billed unseen."""
    if reply.billed_unseen:
        return estimate
    if reply.charge is not None:
        return reply.charge
    usage = reply.usage
    return cost_usd(
        model,
        usage.input_tokens,
        usage.cached_input_tokens,
        usage.output_tokens,
        usage.cache_creation_input_tokens,
    )


def exception_outcome(exc: anthropic.AnthropicError) -> Outcome:
    """Category for an SDK exception. Order matters: APITimeoutError is a connection error."""
    if isinstance(exc, anthropic.APITimeoutError):
        return "timeout"
    # APIConnectionError, RateLimitError, InternalServerError (500, 529), BadRequestError and
    # every other APIStatusError: the request did not produce a usable response.
    return "api_error"


def _error_reply(exc: anthropic.AnthropicError) -> _Reply:
    """Classify an SDK exception: outcome, whether to resend, whether it may be billed."""
    retryable = False
    retry_after: float | None = None
    if isinstance(exc, anthropic.APIConnectionError):  # includes APITimeoutError
        retryable = True
    elif isinstance(exc, anthropic.APIStatusError):
        headers = exc.response.headers
        retry_after = _retry_after_s(headers)
        retryable = _status_is_retryable(exc.status_code, headers, retry_after)
    return _Reply(
        exception_outcome(exc),
        error=type(exc).__name__,
        status=getattr(exc, "status_code", None),
        code=_safe_code(getattr(exc, "type", None)),
        retryable=retryable,
        retry_after_s=retry_after,
        billed_unseen=_may_have_reached_provider(exc),
    )


def _may_have_reached_provider(exc: anthropic.AnthropicError) -> bool:
    """True for a connection failure or timeout that can happen after the request was sent."""
    if not isinstance(exc, anthropic.APIConnectionError):
        return False
    return type(exc.__cause__).__name__ not in _NOT_SENT_ERRORS


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
    """One line per call, numbers and category codes only; same keys as the OpenAI client's
    (plus the cache write and the model that answered when a fallback served it)."""
    log_fields(
        log,
        logging.DEBUG if call.outcome == "ok" else logging.WARNING,
        "model_call",
        step=call.step,
        model=call.model,
        provider="anthropic",
        prompt_version=call.prompt_version,
        outcome=call.outcome,
        fallback=call.is_fallback,
        sends=sends,
        input_tokens=call.input_tokens,
        cached_input_tokens=call.cached_input_tokens,
        cache_creation_input_tokens=call.cache_creation_input_tokens,
        output_tokens=call.output_tokens,
        reasoning_tokens=call.reasoning_tokens,
        cost_usd=round(call.cost_usd, 6),
        latency_ms=call.latency_ms,
        error=reply.error,
        status=reply.status,
        code=reply.code,
        served_by=reply.served_by,
    )
