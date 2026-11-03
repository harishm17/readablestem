"""Run one conversion in a child process the worker can kill.

`convert_isolated` has the signature of `pipeline.run.convert_pdf` (plus `cancelled`,
`limit_s` and, for tests, `fault`), so it is the worker's converter (`web.worker.
default_converter`). It starts `web.conversion_child`, which runs the whole conversion, and
answers what the child asks of the outside world on the calling thread and a thread per
model request:

- Model requests, the job's verifier, the spend reservations behind them: the parent's real
  client, with the job's `JobBudget` on the job's row. The child holds no key, so spend
  accounting is exactly what it was; a model call that is still running when the child is
  killed finishes and settles in the parent as an abandoned conversion's did.
- Progress, stage progress and the step cache: the worker's callbacks, so every database
  write is the parent's. A callback that raises (the job is no longer ours) kills the
  child and the exception propagates, as it aborted `convert_pdf` before.

Why the whole conversion and not only the MuPDF calls: MuPDF is used on every page by
layout analysis, page typing, the table detector, the text pass, tagged structure, source
facts and every crop, and those calls are interleaved with the model steps (a crop is cut,
its step is submitted, the next crop is cut). A boundary at the PDF calls would mean
sending page objects, which cannot cross a process. The boundary at the model client
carries only plain data (requests, answers, results as JSON).

Outcomes, mapped to `Job.error_code` by `web.worker.error_code_for`:

- the child answers: the result, or the error it ended with (`LayoutError`, `BudgetExceeded`,
  `ConfigError`, `UnknownModelError` raised again here; any other error "internal_error");
- the child dies of a signal or exits without an answer (MuPDF aborting, a segfault):
  `ConversionCrashed`, "conversion_crashed";
- it runs out of memory (`MemoryError` under the address-space cap, or SIGKILL, which is what
  the kernel's OOM killer sends): `ConversionOom`, "conversion_oom";
- it runs past `limit_s` (the worker's own limit cancels it first; this is for other callers)
  or the CPU limit: `ConversionTimeout`, "timeout";
- the worker cancels the attempt (deleted, taken over, shutting down): the child is killed
  within `POLL_S` and `BudgetExceeded("cancelled")` is raised.

None of these is retried here: a PDF that crashes MuPDF will crash it again, and the job is
failed at once instead of waiting for three lease expiries. A person may use "Try again"
(at most 3 tries a job, `web.retry`), which is a conscious choice.

The child's limits: address space `Settings.conversion_memory_mb` plus twice the PDF's
size, CPU time `CPU_FACTOR` times `limit_s` (a backstop; the wall-clock limit is the
parent's), no core file, death with the parent. Its environment has no keys or tokens
(`core.childenv`). Tracing (off unless configured) does not reach into the child.
"""

from __future__ import annotations

import importlib
import json
import logging
import math
import os
import pickle
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, get_args

from pydantic import BaseModel, SecretStr

from readablestem.config import ConfigError, Settings
from readablestem.core.budget import BudgetExceeded
from readablestem.core.childenv import child_env
from readablestem.core.types import (
    Check,
    ConversionOptions,
    ConversionOutput,
    DocumentResult,
    Element,
    ModelCall,
    ProgressEvent,
    Step,
)
from readablestem.web import conversion_child as protocol
from readablestem.web.logsafe import log_event

if TYPE_CHECKING:
    from readablestem.models.base import VisionClient
    from readablestem.observability.tracing import Tracer
    from readablestem.pipeline.run import ProgressFn, StageProgressFn, StepCache

logger = logging.getLogger(__name__)

# How often the parent looks at the cancel flag and the clock while the child works.
POLL_S = 0.2
# CPU seconds the child may use per second of the job's wall-clock limit. A conversion is
# about half idle (it waits on the API) but the table model uses several threads.
CPU_FACTOR = 4.0
# The converter's own wall-clock limit is this much past `limit_s`, so the worker's limit,
# which records the failure as "timeout", always fires first.
DEADLINE_SLACK_S = 5.0
REAP_WAIT_S = 5.0
# Environment the child needs besides `core.childenv`: where the table model file is.
_EXTRA_ENV = ("TABLE_MODEL_DIR", "XDG_CACHE_HOME", "HOME")
# Settings the child does not get: where the database is, and who the people are.
_NOT_FOR_CHILD = frozenset(
    {"database_url", "admin_emails", "email_sender", "langfuse_public_key", "langfuse_host"}
)
# Bounds on what a model request from the child may ask the parent to send.
MAX_IMAGES_PER_REQUEST = 32
# Requests the child may have open at once (its steps run `MODEL_CONCURRENCY` at a time, a few
# pools at most); more is not the pipeline, and each costs a thread here.
MAX_IN_FLIGHT = 64
QUEUED_FRAMES = 4
MAX_OUTPUT_TOKENS = 64_000
_STEPS = frozenset(get_args(Step))
_DETAILS = frozenset({"low", "high", "auto"})
_MODULE_PREFIX = "readablestem."


class ConversionFailed(Exception):
    """The child could not finish. `code` becomes `Job.error_code`."""

    code = "internal_error"


class ConversionCrashed(ConversionFailed):
    """The child died of a signal or exited without an answer (MuPDF aborted, say)."""

    code = "conversion_crashed"


class ConversionOom(ConversionFailed):
    """The child ran out of memory."""

    code = "conversion_oom"


class ConversionTimeout(ConversionFailed):
    """The child ran past its time (wall clock or CPU)."""

    code = "timeout"


class _Protocol(Exception):
    """The child sent something that is not the protocol."""


_EOF = object()


def settings_for_child(settings: Settings) -> dict[str, Any]:
    """The settings the child is built from: every field but the secrets (`SecretStr`) and
    the ones it has no use for. The child's `Settings` is made without validation or
    environment, so a missing secret is simply unset."""
    values: dict[str, Any] = {}
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr) or name in _NOT_FOR_CHILD:
            continue
        values[name] = value
    return values


def child_command(fd: int, memory_bytes: int, cpu_seconds: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "readablestem.web.conversion_child",
        str(fd),
        str(memory_bytes),
        str(cpu_seconds),
    ]


def child_environment() -> dict[str, str]:
    env = child_env()
    env.update({name: os.environ[name] for name in _EXTRA_ENV if name in os.environ})
    # glibc gives every thread its own 64 MB arena of address space; with a dozen threads
    # that is most of what the address-space cap counts. Two arenas cost nothing measurable.
    env["MALLOC_ARENA_MAX"] = "2"
    return env


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _valid[M: BaseModel](model: type[M], data: Any) -> M:
    try:
        return model.model_validate(data)
    except ValueError:  # pydantic's ValidationError
        raise _Protocol(model.__name__) from None


def resolve_schema(name: str) -> type[BaseModel]:
    """The pydantic model a request names ("module:Qualname"), only from this package."""
    module_name, _, qualname = name.partition(":")
    if not module_name.startswith(_MODULE_PREFIX) or not qualname or "<" in qualname:
        raise _Protocol("schema")
    target: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        target = getattr(target, part)
    if not (isinstance(target, type) and issubclass(target, BaseModel)):
        raise _Protocol("schema")
    return target


class _Conversion:
    """One run of the child: its process, its socket and what it asks."""

    def __init__(
        self,
        pdf_bytes: bytes,
        *,
        settings: Settings,
        client: VisionClient,
        pages: list[int] | None,
        progress: ProgressFn | None,
        concurrency: int,
        notice: str | None,
        source_note: str | None,
        options: ConversionOptions | None,
        stage_progress: StageProgressFn | None,
        step_cache: StepCache | None,
        call_observer: Callable[[ModelCall], None] | None,
        cancelled: Callable[[], bool] | None,
        limit_s: float | None,
        fault: str | None,
        fault_at: str,
    ) -> None:
        self.pdf = pdf_bytes
        self.settings = settings
        self.client = client
        self.pages = pages
        self.progress = progress
        self.concurrency = concurrency
        self.notice = notice
        self.source_note = source_note
        self.options = options
        self.stage_progress = stage_progress
        self.step_cache = step_cache
        self.call_observer = call_observer
        self.cancelled = cancelled
        self.limit_s = limit_s
        self.fault = fault
        self.fault_at = fault_at
        # Bounded: a child that sends faster than the parent handles waits on its socket
        # instead of filling this process's memory.
        self.frames: queue.Queue[Any] = queue.Queue(maxsize=QUEUED_FRAMES)
        self.closed = threading.Event()
        self.send_lock = threading.Lock()
        self.proc: subprocess.Popen[bytes] | None = None
        self.sock: socket.socket | None = None
        self.reaped = False
        self.in_flight = 0
        self.flight_lock = threading.Lock()
        self.exit_code: int | None = None

    # ------------------------------------------------------------------ the child

    def start(self) -> None:
        from readablestem.models.verifier import verifier_of

        memory = self.settings.conversion_memory_mb * 1024 * 1024 + 2 * len(self.pdf)
        cpu = 0 if self.limit_s is None else max(1, math.ceil(self.limit_s * CPU_FACTOR))
        mine, theirs = socket.socketpair()
        try:
            self.proc = subprocess.Popen(
                child_command(theirs.fileno(), memory, cpu),
                pass_fds=(theirs.fileno(),),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,  # MuPDF's own messages are never logged
                stderr=subprocess.DEVNULL,
                env=child_environment(),  # no keys or secrets for the process that reads the PDF
                start_new_session=True,  # its own process group, so the render probes go too
                close_fds=True,
            )
        except BaseException:
            mine.close()
            raise
        finally:
            theirs.close()
        self.sock = mine
        init = {
            "op": "init",
            "settings": settings_for_child(self.settings),
            "pages": self.pages,
            "concurrency": self.concurrency,
            "notice": self.notice,
            "source_note": self.source_note,
            "options": self.options,
            "progress": self.progress is not None,
            "stage_progress": self.stage_progress is not None,
            "step_cache": self.step_cache is not None,
            "call_observer": self.call_observer is not None,
            "verifier": verifier_of(self.client) is not None,
            "fault": self.fault,
            "fault_at": self.fault_at,
        }
        threading.Thread(
            target=self._send_init, args=(init,), name="conversion-init", daemon=True
        ).start()
        threading.Thread(target=self._read, name="conversion-reader", daemon=True).start()

    def _send_init(self, init: dict[str, Any]) -> None:
        # On its own thread: a 20 MB PDF does not fit the socket's buffer, and the child
        # reads it only once it has started; the cancel flag is looked at meanwhile.
        with suppress(OSError):  # the child is gone: the reader sees the end
            self._send([pickle.dumps(init), self.pdf])

    def _read(self) -> None:
        sock = self.sock
        assert sock is not None
        try:
            while (parts := protocol.read_frame(sock)) is not None:
                if not self._queue(parts):
                    return
        except (OSError, ValueError):
            pass
        self._queue(_EOF)

    def _queue(self, item: Any) -> bool:
        """Hand a frame to the loop, waiting while it is busy; False once the run is over."""
        while not self.closed.is_set():
            try:
                self.frames.put(item, timeout=0.5)
                return True
            except queue.Full:
                continue
        return False

    def _send(self, parts: list[bytes]) -> None:
        sock = self.sock
        if sock is None:
            raise OSError("closed")
        with self.send_lock:
            protocol.write_frame(sock, parts)

    def _reply(self, ident: int, *, value: Any = None, error: protocol.ErrorWire | None = None):
        try:
            message = (
                {"id": ident, "value": value} if error is None else {"id": ident, "error": error}
            )
            try:
                data = pickle.dumps(message)
            except Exception as exc:  # an answer that cannot be sent is a failed request
                data = pickle.dumps({"id": ident, "error": protocol.error_to_wire(exc)})
            self._send([data])
        except OSError:
            pass  # the child was killed while the request ran

    def reap(self) -> int | None:
        """Kill the child's process group, wait for the child, close the socket. Returns
        its exit status (negative: the signal)."""
        proc, sock = self.proc, self.sock
        self.closed.set()
        if proc is not None and not self.reaped:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            try:
                self.exit_code = proc.wait(timeout=REAP_WAIT_S)
                self.reaped = True
            except subprocess.TimeoutExpired:
                log_event(logger, "conversion_child_unreaped", logging.ERROR)
        if sock is not None:
            with suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()
        return self.exit_code

    # ------------------------------------------------------------------ the loop

    def run(self) -> ConversionOutput:
        self.start()
        deadline = (
            None if self.limit_s is None else time.monotonic() + self.limit_s + DEADLINE_SLACK_S
        )
        try:
            while True:
                if self.cancelled is not None and self.cancelled():
                    raise BudgetExceeded("cancelled", "the job was deleted or taken over")
                if deadline is not None and time.monotonic() > deadline:
                    log_event(logger, "conversion_child_timeout", logging.WARNING)
                    raise ConversionTimeout("the conversion ran past its time limit")
                try:
                    parts = self.frames.get(timeout=POLL_S)
                except queue.Empty:
                    continue
                if parts is _EOF:
                    raise self._ended()
                try:
                    output = self._handle(parts)
                except _Protocol as exc:
                    log_event(logger, "conversion_child_protocol", logging.ERROR, what=str(exc))
                    raise ConversionCrashed("the child broke the protocol") from None
                if output is not None:
                    return output
        finally:
            self.reap()

    def _ended(self) -> ConversionFailed:
        """The child closed its end without a last frame: say why, by how it died."""
        code = self.reap()
        if code is not None and code < 0:
            number = -code
            log_event(logger, "conversion_child_died", logging.WARNING, signal=number)
            if number == signal.SIGKILL:  # the kernel's OOM killer, since we did not
                return ConversionOom("the child was killed")
            if number == signal.SIGXCPU:
                return ConversionTimeout("the child used up its CPU time")
        else:
            log_event(logger, "conversion_child_died", logging.WARNING, exit_code=code)
            if code == protocol.EXIT_OOM:
                return ConversionOom("the child ran out of memory")
        return ConversionCrashed("the child ended without an answer")

    # ------------------------------------------------------------------ messages

    def _handle(self, parts: list[bytes]) -> ConversionOutput | None:
        """One frame of the child. Returns the output for the last one; raises what ended
        the conversion. Raises `_Protocol` for anything malformed."""
        try:
            header = json.loads(parts[0])
            op = header["op"]
            if op == "model":
                self._spawn(self._model, header, parts[1:])
            elif op == "verify":
                self._spawn(self._verify, header, parts[1:])
            elif op == "progress":
                self._progressed(header)
            elif op == "stage":
                self._staged(header)
            elif op == "cache_lookup":
                self._lookup(header)
            elif op == "cache_store":
                self._store(header)
            elif op == "observe":
                self._observed(header)
            elif op == "done":
                return self._done(header, parts[1:])
            elif op == "error":
                failure = self._error(header)
            else:
                raise _Protocol("op")
        except (KeyError, TypeError, IndexError, AttributeError, json.JSONDecodeError) as exc:
            raise _Protocol(type(exc).__name__) from None
        if op == "error":
            raise failure
        return None

    def _spawn(self, target: Callable[..., None], *args: Any) -> None:
        with self.flight_lock:
            if self.in_flight >= MAX_IN_FLIGHT:
                raise _Protocol("flood")
            self.in_flight += 1

        def run() -> None:
            try:
                target(*args)
            finally:
                with self.flight_lock:
                    self.in_flight -= 1

        threading.Thread(target=run, name="conversion-request", daemon=True).start()

    def _progressed(self, header: dict[str, Any]) -> None:
        done, total = header["done"], header["total"]
        if not (isinstance(done, int) and isinstance(total, int)):
            raise _Protocol("progress")
        assert self.progress is not None
        self.progress(done, total)  # raises JobCancelled: the caller kills the child
        self._reply(header["id"])

    def _staged(self, header: dict[str, Any]) -> None:
        assert self.stage_progress is not None
        self.stage_progress(_valid(ProgressEvent, header["event"]))
        self._reply(header["id"])

    def _lookup(self, header: dict[str, Any]) -> None:
        assert self.step_cache is not None
        element = _valid(Element, header["element"])
        try:
            fix = self.step_cache.lookup(element, str(header["key"]))
        except Exception:  # a cache never fails a run (`StepCache`)
            fix = None
        self._reply(header["id"], value=fix)

    def _store(self, header: dict[str, Any]) -> None:
        from readablestem.core.types import Fix

        assert self.step_cache is not None
        element = _valid(Element, header["element"])
        fix = _valid(Fix, header["fix"])
        with suppress(Exception):
            self.step_cache.store(element, str(header["key"]), fix)
        self._reply(header["id"])

    def _observed(self, header: dict[str, Any]) -> None:
        call = _valid(ModelCall, header["call"])
        if self.call_observer is not None:
            with suppress(Exception):
                self.call_observer(call)

    def _request(self, header: dict[str, Any], images: list[bytes]):
        from readablestem.models.base import VisionRequest

        if len(images) > MAX_IMAGES_PER_REQUEST or header["step"] not in _STEPS:
            raise _Protocol("request")
        if header["image_detail"] not in _DETAILS:
            raise _Protocol("request")
        tokens = header["max_output_tokens"]
        if (
            not isinstance(tokens, int)
            or isinstance(tokens, bool)
            or not 0 < tokens <= MAX_OUTPUT_TOKENS
        ):
            raise _Protocol("request")
        for name in ("model", "prompt_version", "instructions"):
            if not isinstance(header[name], str):
                raise _Protocol("request")
        for name in ("context_text", "effort"):
            if header[name] is not None and not isinstance(header[name], str):
                raise _Protocol("request")
        for name in ("context_limit", "timeout_s"):
            if header[name] is not None and not _number(header[name]):
                raise _Protocol("request")
        return VisionRequest(
            step=header["step"],
            model=header["model"],
            prompt_version=header["prompt_version"],
            instructions=header["instructions"],
            images=images,
            context_text=header["context_text"],
            max_output_tokens=tokens,
            image_detail=header["image_detail"],
            context_limit=header["context_limit"],
            effort=header["effort"],
            timeout_s=header["timeout_s"],
        )

    def _model(self, header: dict[str, Any], images: list[bytes]) -> None:
        """One model request, on its own thread: the real client, the job's budget."""
        ident = header.get("id")
        if not isinstance(ident, int):
            return
        try:
            request = self._request(header, images)
            schema = resolve_schema(header["schema"])
            value = self.client.run(request, schema, is_fallback=bool(header["is_fallback"]))
        except Exception as exc:  # BudgetExceeded and ConfigError reach the child as such
            self._reply(ident, error=protocol.error_to_wire(exc))
            return
        self._reply(ident, value=value)

    def _verify(self, header: dict[str, Any], blobs: list[bytes]) -> None:
        from readablestem.models.verifier import verifier_of

        ident = header.get("id")
        if not isinstance(ident, int):
            return
        try:
            verifier = verifier_of(self.client)
            if verifier is None or len(blobs) != 1:
                raise _Protocol("verify")
            if header["check"] == "figure_alt":
                caption = header["caption"]
                value = verifier.figure_alt(
                    blobs[0],
                    alt=str(header["alt"]),
                    caption=caption if isinstance(caption, str) else None,
                )
            elif header["check"] == "equation_match":
                value = verifier.equation_match(blobs[0], latex=str(header["latex"]))
            else:
                raise _Protocol("verify")
        except Exception as exc:
            self._reply(ident, error=protocol.error_to_wire(exc))
            return
        self._reply(ident, value=value)

    def _done(self, header: dict[str, Any], crops: list[bytes]) -> ConversionOutput:
        from readablestem.pipeline.run import PipelineOutput

        ids = header["crop_ids"]
        if not (
            isinstance(ids, list)
            and len(ids) == len(crops)
            and all(isinstance(i, str) for i in ids)
        ):
            raise _Protocol("crops")
        try:
            return PipelineOutput(
                result=DocumentResult.model_validate(header["result"]),
                crops=dict(zip(ids, crops, strict=True)),
                checks=[Check.model_validate(c) for c in header["checks"]],
            )
        except ValueError:  # pydantic's ValidationError
            raise _Protocol("result") from None

    def _error(self, header: dict[str, Any]) -> Exception:
        """The exception for the error the child ended with."""
        from readablestem.models.pricing import UnknownModelError
        from readablestem.pipeline.layout import LayoutError

        kind = header["kind"]
        message = str(header.get("message", ""))
        if kind == "layout":
            return LayoutError(str(header["code"]), message)
        if kind == "budget":
            return BudgetExceeded(str(header["scope"]), message)
        if kind == "config":
            return ConfigError(message)
        if kind == "unknown_model":
            return UnknownModelError(message)
        if kind == "oom":
            return ConversionOom("the child ran out of memory")
        return RuntimeError(f"the conversion failed in its child ({header.get('type')})")


def convert_isolated(
    pdf_bytes: bytes,
    *,
    settings: Settings,
    client: VisionClient,
    pages: list[int] | None = None,
    progress: ProgressFn | None = None,
    tracer: Tracer | None = None,
    concurrency: int = 4,
    notice: str | None = None,
    source_note: str | None = None,
    options: ConversionOptions | None = None,
    stage_progress: StageProgressFn | None = None,
    step_cache: StepCache | None = None,
    call_observer: Callable[[ModelCall], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    limit_s: float | None = None,
    fault: str | None = None,
    fault_at: str = "start",
) -> ConversionOutput:
    """`pipeline.run.convert_pdf` in a child process (module docstring).

    `cancelled` is polled every `POLL_S`: once true the child is killed and
    `BudgetExceeded("cancelled")` is raised. `limit_s` is the job's time limit: the child's
    CPU limit and a wall-clock backstop are derived from it. `tracer` is accepted and not
    used (spans do not cross the process). `fault` ("abort", "segv", "hang", "oom", "exit",
    "kill9") makes the child do that to itself at `fault_at` ("start", "progress", "model"):
    a test hook, never set by the worker.
    """
    if fault is not None and (fault not in protocol.FAULTS or fault_at not in protocol.FAULT_AT):
        raise ValueError("unknown fault")
    return _Conversion(
        pdf_bytes,
        settings=settings,
        client=client,
        pages=pages,
        progress=progress,
        concurrency=concurrency,
        notice=notice,
        source_note=source_note,
        options=options,
        stage_progress=stage_progress,
        step_cache=step_cache,
        call_observer=call_observer,
        cancelled=cancelled,
        limit_s=limit_s,
        fault=fault,
        fault_at=fault_at,
    ).run()
