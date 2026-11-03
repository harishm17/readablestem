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
CPU_FACTOR = float(protocol.MAX_THREADS)
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
# Requests the child may have open at once: twice the steps it runs at once (a step's own
# verifier request is made on its thread, a pass has its own pool), at least this many; more
# is not the pipeline, and each costs a thread here.
MIN_IN_FLIGHT = 4
# What the parent holds of the child's output at once (queued, being read, being handled).
HELD_BYTES = protocol.MAX_FRAME_BYTES
# The modules whose pydantic models are what a step asks a model for.
_SCHEMA_MODULES = frozenset(
    f"readablestem.pipeline.{name}"
    for name in (
        "equation_step",
        "figure_step",
        "table_step",
        "page_math",
        "page_witness",
        "image_page",
        "image_page_check",
    )
)
# How long a child that closed its socket may take to exit before it is called stuck.
EXIT_WAIT_S = 2.0
CHECK_TIMEOUT_S = 90.0
MAX_OUTPUT_TOKENS = 64_000
_STEPS = frozenset(get_args(Step))
_DETAILS = frozenset({"low", "high", "auto"})


class ConversionFailed(Exception):
    """The child could not finish. `code` becomes `Job.error_code`."""

    code = "internal_error"


class ConversionCrashed(ConversionFailed):
    """The child died of a signal or exited without an answer (MuPDF aborted, say)."""

    code = "conversion_crashed"


class ConversionOom(ConversionFailed):
    """The child ran out of memory."""

    code = "conversion_oom"


class ConversionExternalKill(ConversionOom):
    """The child was killed by SIGKILL that this process did not send: the kernel's OOM
    killer (other work on the host may have used the memory), or an operator. `convert_isolated`
    runs the conversion once more before it fails the job as `conversion_oom`."""


class ConversionUnavailable(ConversionFailed):
    """The child could not start (an import failed, a library or the model file is missing):
    the service's fault, not the PDF's. Worth trying again once it is fixed."""

    code = "conversion_unavailable"


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
    # child_env() already caps BLAS/OpenMP threads and glibc arenas (MALLOC_ARENA_MAX=2) for
    # every capped child; see core/childenv.py.
    return env


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _valid[M: BaseModel](model: type[M], data: Any) -> M:
    try:
        return model.model_validate(data)
    except ValueError:  # pydantic's ValidationError
        raise _Protocol(model.__name__) from None


def resolve_schema(name: str) -> type[BaseModel]:
    """The pydantic model a request names ("module:Qualname"): one the steps define
    (`_SCHEMA_MODULES`), nothing else."""
    module_name, _, qualname = name.partition(":")
    if module_name not in _SCHEMA_MODULES or not qualname or "." in qualname:
        raise _Protocol("schema")
    target: Any = getattr(importlib.import_module(module_name), qualname, None)
    if not (
        isinstance(target, type)
        and issubclass(target, BaseModel)
        and target.__module__ == module_name
    ):
        raise _Protocol("schema")
    return target


def allowed_models(settings: Settings) -> frozenset[str]:
    """The model names this deployment is configured with: every `*_model` setting, and the
    models they fall back to. A request for any other is not sent."""
    names = {
        value
        for name in type(settings).model_fields
        if name.endswith("model") and isinstance(value := getattr(settings, name), str) and value
    }
    for first, second in settings.fallback_for.items():
        names.update((first, second))
    return frozenset(names)


_CGROUP_EVENTS = "/sys/fs/cgroup/memory.events"


def cgroup_oom_kills() -> int | None:
    """How many processes of this cgroup the kernel's OOM killer has killed (cgroup v2), or
    None when that cannot be read. Compared before and after a child dies, for the log."""
    try:
        with open(_CGROUP_EVENTS) as handle:
            for line in handle:
                name, _, value = line.partition(" ")
                if name == "oom_kill":
                    return int(value)
    except (OSError, ValueError):
        return None
    return None


def self_check(timeout_s: float = CHECK_TIMEOUT_S) -> bool:
    """Start a child that only imports what a conversion needs: True if it can. Logs the
    failure (the type of what failed is not known here; the exit status is)."""
    try:
        done = subprocess.run(
            [sys.executable, "-m", "readablestem.web.conversion_child", "--check"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_environment(),
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log_event(
            logger, "conversion_child_check_failed", logging.ERROR, error_type=type(exc).__name__
        )
        return False
    if done.returncode != 0:
        log_event(logger, "conversion_child_check_failed", logging.ERROR, exit_code=done.returncode)
        return False
    log_event(logger, "conversion_child_check_ok")
    return True


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
        # What is held of the child's output is bounded in bytes (`_admit`): a child that
        # sends faster than the parent handles waits on its socket instead of filling this
        # process's memory.
        self.frames: queue.Queue[Any] = queue.Queue()
        self.held = 0
        self.held_changed = threading.Condition()
        self.refused = False
        self.closed = threading.Event()
        self.models = allowed_models(settings)
        self.max_in_flight = max(MIN_IN_FLIGHT, 2 * concurrency)
        self.killed_before = cgroup_oom_kills()
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
            "limit_s": self.limit_s,  # the job's time: second readings stay inside what it leaves
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
            while (parts := protocol.read_frame(sock, admit=self._admit)) is not None:
                self.frames.put((parts, sum(len(p) for p in parts)))
        except ValueError:  # a frame over the limit, or one that is not a frame
            self.refused = True
        except OSError:
            pass
        self.frames.put(_EOF)

    def _admit(self, size: int) -> bool:
        """Wait until `size` more bytes of the child's output may be held; False once the
        run is over. A frame is always admitted when nothing else is held."""
        with self.held_changed:
            while self.held and self.held + size > HELD_BYTES and not self.closed.is_set():
                self.held_changed.wait(0.5)
            if self.closed.is_set():
                return False
            self.held += size
        return True

    def _release(self, size: int) -> None:
        with self.held_changed:
            self.held -= size
            self.held_changed.notify_all()

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

    def kill_group(self) -> None:
        if self.proc is not None:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(self.proc.pid, signal.SIGKILL)

    def reap(self) -> int | None:
        """Kill the child's process group, wait for the child, close the socket. Returns
        its exit status (negative: the signal)."""
        proc, sock = self.proc, self.sock
        self.closed.set()
        with self.held_changed:
            self.held_changed.notify_all()
        if proc is not None and not self.reaped:
            self.kill_group()
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
        try:
            self.start()
            deadline = (
                None if self.limit_s is None else time.monotonic() + self.limit_s + DEADLINE_SLACK_S
            )
            while True:
                if self.cancelled is not None and self.cancelled():
                    raise BudgetExceeded("cancelled", "the job was deleted or taken over")
                if deadline is not None and time.monotonic() > deadline:
                    log_event(logger, "conversion_child_timeout", logging.WARNING)
                    raise ConversionTimeout("the conversion ran past its time limit")
                try:
                    item = self.frames.get(timeout=POLL_S)
                except queue.Empty:
                    continue
                if item is _EOF:
                    raise self._ended()
                parts, size = item
                try:
                    output = self._handle(parts)
                except _Protocol as exc:
                    log_event(logger, "conversion_child_protocol", logging.ERROR, what=str(exc))
                    raise ConversionCrashed("the child broke the protocol") from None
                finally:
                    self._release(size)
                if output is not None:
                    return output
        finally:
            self.reap()

    def _ended(self) -> ConversionFailed:
        """The child closed its end without a last frame: say why, by how it died."""
        code: int | None = None
        if self.proc is not None:
            try:
                code = self.proc.wait(timeout=EXIT_WAIT_S)
                self.reaped, self.exit_code = True, code
            except subprocess.TimeoutExpired:
                pass  # closed its socket and did not exit: `reap` kills it
        self.kill_group()  # whatever it started (the render probes) goes with it
        self.reap()
        if self.refused:
            log_event(logger, "conversion_child_frame_refused", logging.ERROR)
            return ConversionCrashed("the child sent more than a result may be")
        if code is not None and code < 0:
            number = -code
            kills = cgroup_oom_kills()
            more = (
                None if kills is None or self.killed_before is None else kills - self.killed_before
            )
            log_event(
                logger,
                "conversion_child_died",
                logging.WARNING,
                signal=number,
                cgroup_oom_kills=more,
            )
            if number == signal.SIGKILL:  # not ours: this process only kills on cancel or timeout
                return ConversionExternalKill("the child was killed from outside")
            if number == signal.SIGXCPU:
                return ConversionTimeout("the child used up its CPU time")
        else:
            log_event(logger, "conversion_child_died", logging.WARNING, exit_code=code)
            if code == protocol.EXIT_OOM:
                return ConversionOom("the child ran out of memory")
            if code in (1, protocol.EXIT_BROKEN):  # the child, not the PDF
                log_event(logger, "conversion_child_unavailable", logging.ERROR, exit_code=code)
                return ConversionUnavailable("the child could not run")
        return ConversionCrashed("the child ended without an answer")

    # ------------------------------------------------------------------ messages

    def _handle(self, parts: list[bytearray]) -> ConversionOutput | None:
        """One frame of the child. Returns the output for the last one; raises what ended
        the conversion. Raises `_Protocol` for anything malformed: only the reading of the
        frame is covered by that, not what the callbacks do with it."""
        try:
            header = json.loads(parts[0])
            action = self._parse(header["op"], header, [bytes(p) for p in parts[1:]])
        except (KeyError, TypeError, IndexError, AttributeError, ValueError) as exc:
            raise _Protocol(type(exc).__name__) from None
        return action()

    def _parse(
        self, op: str, header: dict[str, Any], blobs: list[bytes]
    ) -> Callable[[], ConversionOutput | None]:
        """Check one frame and say what to do about it (a function to call): nothing here
        calls out. Raises `_Protocol`, or what reading a missing or misshapen field raises."""
        if op in ("model", "verify"):
            if not isinstance(header["id"], int):
                raise _Protocol("id")
            target = self._model if op == "model" else self._verify
            return lambda: self._spawn(target, header, blobs)
        if op == "progress":
            done, total, ident = header["done"], header["total"], header["id"]
            ok = all(isinstance(n, int) and not isinstance(n, bool) for n in (done, total, ident))
            if not ok or not 0 <= done <= total <= self.settings.max_pages_per_job:
                raise _Protocol("progress")
            return lambda: self._progressed(done, total, ident)
        if op == "stage":
            event, ident = _valid(ProgressEvent, header["event"]), header["id"]
            return lambda: self._staged(event, ident)
        if op == "cache_lookup":
            element, key, ident = (
                _valid(Element, header["element"]),
                str(header["key"]),
                header["id"],
            )
            return lambda: self._lookup(element, key, ident)
        if op == "cache_store":
            from readablestem.core.types import Fix

            element, fix = _valid(Element, header["element"]), _valid(Fix, header["fix"])
            key, ident = str(header["key"]), header["id"]
            return lambda: self._store(element, key, fix, ident)
        if op == "observe":
            call = _valid(ModelCall, header["call"])
            return lambda: self._observed(call)
        if op == "done":
            output = self._done(header, blobs)
            return lambda: output
        if op == "error":
            failure = self._error(header)
            return lambda: self._fail(failure)
        raise _Protocol("op")

    @staticmethod
    def _fail(failure: Exception) -> None:
        raise failure

    def _spawn(self, target: Callable[..., None], *args: Any) -> None:
        with self.flight_lock:
            if self.in_flight >= self.max_in_flight:
                raise _Protocol("flood")
            self.in_flight += 1

        def run() -> None:
            try:
                target(*args)
            finally:
                with self.flight_lock:
                    self.in_flight -= 1

        threading.Thread(target=run, name="conversion-request", daemon=True).start()

    def _progressed(self, done: int, total: int, ident: int) -> None:
        assert self.progress is not None
        self.progress(done, total)  # raises JobCancelled: the caller kills the child
        self._reply(ident)

    def _staged(self, event: ProgressEvent, ident: int) -> None:
        assert self.stage_progress is not None
        self.stage_progress(event)
        self._reply(ident)

    def _lookup(self, element: Element, key: str, ident: int) -> None:
        assert self.step_cache is not None
        try:
            fix = self.step_cache.lookup(element, key)
        except Exception:  # a cache never fails a run (`StepCache`)
            fix = None
        self._reply(ident, value=fix)

    def _store(self, element: Element, key: str, fix: Any, ident: int) -> None:
        assert self.step_cache is not None
        with suppress(Exception):
            self.step_cache.store(element, key, fix)
        self._reply(ident)

    def _observed(self, call: ModelCall) -> None:
        if self.call_observer is not None:
            with suppress(Exception):
                self.call_observer(call)

    def _request(self, header: dict[str, Any], images: list[bytes]):
        from readablestem.models.base import VisionRequest

        if len(images) > MAX_IMAGES_PER_REQUEST or header["step"] not in _STEPS:
            raise _Protocol("request")
        if header["model"] not in self.models:  # a name this deployment is not set up with
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
        if kind == "source":  # a LaTeX source that could not be converted: a plain code
            code = "".join(c for c in str(header.get("code", "")) if c.isalnum() or c == "_")[:40]
            failure = ConversionFailed("the source could not be converted")
            failure.code = code or "source_failed"
            return failure
        if kind == "oom":
            return ConversionOom("the child ran out of memory")
        if kind == "startup":
            name = "".join(c for c in str(header.get("type", "")) if c.isalnum())[:60]
            log_event(logger, "conversion_child_unavailable", logging.ERROR, error_type=name)
            return ConversionUnavailable("the child could not start")
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

    def once() -> ConversionOutput:
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

    try:
        return once()
    except ConversionExternalKill:
        # Not this process's doing (the kernel's OOM killer, most likely, with other work on
        # the host using the memory): once more, and then it is what it looks like. The
        # finished steps are in the step cache and what was spent stays counted.
        log_event(logger, "conversion_child_retry", logging.WARNING)
        return once()
