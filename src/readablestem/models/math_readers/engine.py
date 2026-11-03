"""The shared send loop of the HTTP readers: reserve, send, map, settle, retry.

Same rules as `models.openai_client`, in the same order:

- Reserve the worst case before every send (`JobBudget.reserve`, `count_call=False`: a
  reader is an optional second reading, not one of the steps' own calls, so it must not use
  up the job's call cap; every dollar cap still holds it). A malformed request raises before
  the reservation. A refused reservation raises `BudgetExceeded` and nothing is sent; a
  refused resend returns the failure already had.
- Retries live here, each resend reserving and settling on its own. Connection errors,
  timeouts and HTTP 408/409/429/5xx are resent up to `Settings.model_max_retries` times,
  each send bounded by `Settings.model_timeout_s`, with the same backoff and `retry-after`
  rule as the OpenAI client; a 429 pauses the process's gate for that provider.
- Charge per send: a reply that was processed, the price it states (or the reservation
  when it states none); an HTTP error or a request that never left: $0; a timeout or a
  connection that broke after sending: the full reservation (the provider may bill it).
- Outcomes, not exceptions, and nothing from the request or the reply is logged or kept:
  logs carry the reader's name, status, outcome and counts only.
"""

from __future__ import annotations

import json
import logging
import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from readablestem.config import Settings
from readablestem.core.budget import BudgetExceeded, JobBudget
from readablestem.core.types import Outcome
from readablestem.models.base import rate_gate
from readablestem.models.math_readers.base import (
    Candidate,
    ReaderCall,
    ReadRequest,
    ReadResult,
)
from readablestem.models.math_readers.transport import (
    HttpReply,
    HttpxTransport,
    Transport,
    TransportError,
)
from readablestem.models.openai_client import (
    INITIAL_RETRY_DELAY_S,
    MAX_RETRY_AFTER_S,
    MAX_RETRY_DELAY_S,
    RETRYABLE_STATUSES,
)
from readablestem.observability.logging import log_fields

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Prepared:
    """One HTTP request, built once and resent as it is."""

    url: str
    headers: Mapping[str, str]
    content: bytes


@dataclass(frozen=True)
class Parsed:
    candidates: list[Candidate]
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None  # None: the reservation (nothing finer is known)


class Rejected(Exception):
    """A 2xx reply that is not a usable reading (a refusal, an error object, bad output).
    `cost_usd` None: the reply was processed and is charged at the reservation."""

    def __init__(
        self,
        outcome: Outcome,
        code: str,
        cost_usd: float | None = None,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        super().__init__(code)
        self.outcome = outcome
        self.code = code
        self.cost_usd = cost_usd
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


@dataclass
class _Send:
    outcome: Outcome
    charge: float
    status: int | None = None
    code: str | None = None
    retryable: bool = False
    retry_after_s: float | None = None
    candidates: list[Candidate] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


def retry_delay_s(sends: int, retry_after: float | None, jitter: float = 0.0) -> float:
    """Wait before resend number `sends` (1 for the first). `jitter` in [0, 1) takes up to
    25% off the backoff, as the OpenAI client does."""
    if retry_after is not None and 0 < retry_after <= MAX_RETRY_AFTER_S:
        return retry_after
    return min(INITIAL_RETRY_DELAY_S * 2 ** (sends - 1), MAX_RETRY_DELAY_S) * (1 - 0.25 * jitter)


def _sleep(seconds: float) -> None:
    """The backoff wait. A module function so tests can skip the waiting."""
    time.sleep(seconds)


def json_object(body: bytes) -> dict[str, object]:
    """A reply body that must be a JSON object, else `Rejected("api_error")`."""
    try:
        data = json.loads(body)
    except ValueError:
        raise Rejected("api_error", "not_json") from None
    if not isinstance(data, dict):
        raise Rejected("api_error", "not_object")
    return data


def as_count(value: object) -> int:
    """A token count from a reply: a non-negative int, else 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _retry_after(headers: Mapping[str, str]) -> float | None:
    for name, value in headers.items():
        if name.lower() == "retry-after":
            try:
                return float(value)
            except ValueError:
                return None
    return None


class HttpReader(ABC):
    """Base of the readers that call a web API. A subclass says what to send and how to
    read the reply; this class does the budget, the retries and the outcome mapping."""

    name: str
    model: str = ""

    def __init__(
        self,
        settings: Settings,
        budget: JobBudget,
        *,
        transport: Transport | None = None,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._settings = settings
        self._budget = budget
        self._transport: Transport = transport or HttpxTransport()
        self._jitter = jitter

    @abstractmethod
    def _prepare(self, request: ReadRequest) -> Prepared: ...

    @abstractmethod
    def _worst_case_usd(self, request: ReadRequest) -> float:
        """The most one send can cost. Reserved before every send."""

    @abstractmethod
    def _parse(self, reply: HttpReply) -> Parsed:
        """Read a 2xx reply. Raise `Rejected` when it is not a usable reading."""

    def read(self, request: ReadRequest) -> ReadResult:
        prepared = self._prepare(request)  # raises before any reservation
        estimate = self._worst_case_usd(request)
        started = time.perf_counter()
        gate = rate_gate(self.name)
        gate.wait()
        reservation = self._budget.reserve(estimate, count_call=False)
        sends: list[_Send] = []
        while True:
            try:
                send = self._send(prepared, estimate)
            except BaseException:
                self._budget.settle(reservation, 0.0)
                raise
            self._budget.settle(reservation, send.charge)
            sends.append(send)
            if not send.retryable or len(sends) > self._settings.model_max_retries:
                break
            try:
                reservation = self._budget.reserve(estimate, count_call=False)
            except BudgetExceeded as exc:
                log_fields(
                    log,
                    logging.INFO,
                    "math_reader_retry_skipped",
                    reader=self.name,
                    budget_scope=exc.scope,
                )
                break
            delay = retry_delay_s(len(sends), send.retry_after_s, self._jitter())
            if send.status == 429:
                gate.pause(delay)
            log_fields(
                log,
                logging.INFO,
                "math_reader_retry",
                reader=self.name,
                outcome=send.outcome,
                status=send.status,
                code=send.code,
                sends=len(sends),
                delay_s=round(delay, 3),
            )
            try:
                _sleep(delay)
            except BaseException:
                self._budget.settle(reservation, 0.0)
                raise
        last = sends[-1]
        call = ReaderCall(
            reader=self.name,
            model=self.model,
            outcome=last.outcome,
            cost_usd=sum(s.charge for s in sends),
            latency_ms=round((time.perf_counter() - started) * 1000),
            sends=len(sends),
            input_tokens=sum(s.input_tokens for s in sends),
            output_tokens=sum(s.output_tokens for s in sends),
        )
        log_fields(
            log,
            logging.INFO,
            "math_reader_call",
            reader=self.name,
            outcome=call.outcome,
            status=last.status,
            code=last.code,
            sends=call.sends,
            cost_usd=round(call.cost_usd, 6),
            latency_ms=call.latency_ms,
            candidates=len(last.candidates),
        )
        return ReadResult(last.candidates if last.outcome == "ok" else [], call)

    def _send(self, prepared: Prepared, estimate: float) -> _Send:
        try:
            reply = self._transport.post(
                prepared.url,
                headers=prepared.headers,
                content=prepared.content,
                timeout_s=self._settings.model_timeout_s,
            )
        except TransportError as exc:
            if exc.kind == "not_sent":
                return _Send("api_error", 0.0, code="not_sent", retryable=True)
            outcome: Outcome = "timeout" if exc.kind == "timeout" else "api_error"
            return _Send(outcome, estimate, code=exc.kind, retryable=True)
        status = reply.status
        if status in RETRYABLE_STATUSES or status >= 500:
            return _Send(
                "api_error",
                0.0,
                status=status,
                retryable=True,
                retry_after_s=_retry_after(reply.headers),
            )
        if not 200 <= status < 300:
            return _Send("api_error", 0.0, status=status)
        try:
            parsed = self._parse(reply)
        except Rejected as exc:
            charge = estimate if exc.cost_usd is None else exc.cost_usd
            return _Send(
                exc.outcome,
                charge,
                status=status,
                code=exc.code,
                input_tokens=exc.input_tokens,
                output_tokens=exc.output_tokens,
            )
        charge = estimate if parsed.cost_usd is None else parsed.cost_usd
        if not parsed.candidates:
            return _Send(
                "invalid_output",
                charge,
                status=status,
                code="no_candidates",
                input_tokens=parsed.input_tokens,
                output_tokens=parsed.output_tokens,
            )
        return _Send(
            "ok",
            charge,
            status=status,
            candidates=parsed.candidates,
            input_tokens=parsed.input_tokens,
            output_tokens=parsed.output_tokens,
        )
