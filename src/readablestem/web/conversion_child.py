"""The child process a conversion runs in (`web.isolated_convert` starts it).

`python -m readablestem.web.conversion_child FD MEMORY_BYTES CPU_SECONDS` runs
`pipeline.run.convert_pdf` on one PDF, with every call that needs the outside world
answered by the parent over the socket `FD`:

- model requests (`VisionClient.run`, and the job's verifier), so the API keys, the spend
  reservations on the job's row and the daily cap stay in the parent;
- progress, stage progress and the step cache, so every database write stays in the parent;
- nothing else: the child has no database URL, no keys and no tokens (`core.childenv`), and
  writes no file.

The point is that MuPDF, which reads the untrusted PDF, runs where a crash, an out of
memory or a hang costs one job: the parent kills the child and fails that job.

Protocol (`write_frame`, `read_frame`): a frame is a count, the length of each part, then the
parts. Parent to child, part 0 is a pickle (the parent is trusted, and so are its answers).
Child to parent, part 0 is JSON and the rest are raw bytes (images, crops): the child is
the process that reads the hostile file, so the parent never unpickles anything it sends.

Child to parent `op`s: "model" (a `VisionRequest`'s fields; the images are the blobs),
"verify", "progress", "stage", "cache_lookup", "cache_store" (each answered, by `id`),
"observe" (a finished model call; not answered), and the last frame: "done" (the result as
JSON, the crops as blobs) or "error" (a category, never an exception message: MuPDF's
messages quote the PDF).
"""

from __future__ import annotations

import contextlib
import json
import os
import pickle
import signal
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from readablestem.core import sandbox

if TYPE_CHECKING:
    from readablestem.core.types import Element, Fix, ModelCall, ProgressEvent
    from readablestem.models.base import VisionRequest

# The largest frame the parent reads from the child: a result with its crops is tens of
# megabytes (the parent holds at most this much of the child's output at once). The child
# reads the parent's frames, which are trusted and carry one PDF (at most
# `MAX_LARGE_UPLOAD_MB`): a looser cap.
MAX_FRAME_BYTES = 128 * 1024 * 1024
MAX_PARENT_FRAME_BYTES = 512 * 1024 * 1024
MAX_PARTS = 100_000
_COUNT = struct.Struct(">I")
_LENGTH = struct.Struct(">Q")

# What the child may be told to do to itself, for tests of the parent (`fault=`). Never set
# in production: `isolated_convert.convert_isolated(fault=...)` is the only way in.
FAULTS = ("abort", "segv", "hang", "oom", "exit", "kill9")
FAULT_AT = ("start", "progress", "model")
EXIT_PARENT_GONE = 70
EXIT_OOM = 71
# The child itself is broken (an import failed, a library is missing, the table model file is
# not readable): not a bad PDF. The interpreter's own failure to start the module is 1.
EXIT_BROKEN = 72
# The most threads the child's numeric libraries may use, whatever the host's core count: with
# the address-space cap, every thread's stack and arena counts. `isolated_convert.CPU_FACTOR`
# is this number, so the CPU limit allows all of them to be busy for the whole wall clock.
MAX_THREADS = 4


# --------------------------------------------------------------------------- frames


def write_frame(sock: socket.socket, parts: Sequence[bytes]) -> None:
    head = _COUNT.pack(len(parts)) + b"".join(_LENGTH.pack(len(p)) for p in parts)
    sock.sendall(head)
    for part in parts:
        sock.sendall(part)


def _read_exact(sock: socket.socket, size: int) -> bytearray | None:
    """`size` bytes, or None when the other end closed first. A bytearray, filled in place:
    no second copy of a large part."""
    buffer = bytearray(size)
    view = memoryview(buffer)
    got = 0
    while got < size:
        read = sock.recv_into(view[got:], min(size - got, 1 << 20))
        if read == 0:
            return None
        got += read
    return buffer


def read_frame(
    sock: socket.socket,
    *,
    limit: int | None = None,
    admit: Callable[[int], bool] | None = None,
) -> list[bytearray] | None:
    """The parts of the next frame; None at the end of the stream (a frame cut short is
    the end too). Raises ValueError for a frame larger than `limit` or with too many parts.
    `admit(total_size)` is called once the sizes are known, before any part is allocated: it
    may wait (to bound what is held) and returns False to stop reading (the stream ends)."""
    raw = _read_exact(sock, _COUNT.size)
    if raw is None:
        return None
    (count,) = _COUNT.unpack(raw)
    if count > MAX_PARTS:
        raise ValueError("too many parts")
    raw = _read_exact(sock, _LENGTH.size * count)
    if raw is None:
        return None
    sizes = [_LENGTH.unpack_from(raw, i * _LENGTH.size)[0] for i in range(count)]
    if sum(sizes) > (MAX_FRAME_BYTES if limit is None else limit):
        raise ValueError("frame too large")
    if admit is not None and not admit(sum(sizes)):
        return None
    parts = []
    for size in sizes:
        part = _read_exact(sock, size)
        if part is None:
            return None
        parts.append(part)
    return parts


def encode(header: dict[str, Any], blobs: Sequence[bytes] = ()) -> list[bytes]:
    """A child to parent frame: JSON, then the blobs."""
    return [json.dumps(header, separators=(",", ":")).encode("utf-8"), *blobs]


# --------------------------------------------------------------------------- errors

# How an exception crosses the socket: a category and its small parts, never its message.
ErrorWire = tuple[str, ...]


def error_to_wire(exc: BaseException) -> ErrorWire:
    """Parent side: an exception of a callback or a model request, as a tuple the child
    raises again. Messages of the exceptions the pipeline itself defines are plain text."""
    from readablestem.config import ConfigError
    from readablestem.core.budget import BudgetExceeded
    from readablestem.models.pricing import UnknownModelError

    if isinstance(exc, BudgetExceeded):
        return ("budget", str(exc.scope), str(exc))
    if isinstance(exc, ConfigError):
        return ("config", str(exc))
    if isinstance(exc, UnknownModelError):
        return ("unknown_model", str(exc))
    return ("internal", type(exc).__name__)


def error_from_wire(wire: ErrorWire) -> Exception:
    """Child side: the exception to raise for `error_to_wire`'s tuple."""
    from readablestem.config import ConfigError
    from readablestem.core.budget import BudgetExceeded
    from readablestem.models.pricing import UnknownModelError

    kind = wire[0]
    if kind == "budget":
        return BudgetExceeded(wire[1], wire[2])
    if kind == "config":
        return ConfigError(wire[1])
    if kind == "unknown_model":
        return UnknownModelError(wire[1])
    return RuntimeError(f"the parent failed a request ({wire[1] if len(wire) > 1 else kind})")


# --------------------------------------------------------------------------- the link


class Link:
    """The child's end: sends requests, and a reader thread hands each answer to the thread
    that asked. If the parent goes away the child exits at once."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._send_lock = threading.Lock()
        self._lock = threading.Lock()
        self._next = 0
        self._waiting: dict[int, tuple[threading.Event, list[Any]]] = {}
        self.init: dict[str, Any] | None = None
        self._ready = threading.Event()
        threading.Thread(target=self._read, name="link-reader", daemon=True).start()

    def _read(self) -> None:
        try:
            while True:
                parts = read_frame(self._sock, limit=MAX_PARENT_FRAME_BYTES)
                if parts is None:
                    break
                message = pickle.loads(bytes(parts[0]))
                if message.get("op") == "init":
                    message["pdf"] = bytes(parts[1])
                    self.init = message
                    self._ready.set()
                    continue
                with self._lock:
                    waiter = self._waiting.pop(message["id"], None)
                if waiter is not None:
                    waiter[1].append(message)
                    waiter[0].set()
        except Exception:
            pass
        os._exit(EXIT_PARENT_GONE)

    def wait_for_init(self) -> dict[str, Any]:
        self._ready.wait()
        assert self.init is not None
        return self.init

    def send(self, header: dict[str, Any], blobs: Sequence[bytes] = ()) -> None:
        with self._send_lock:
            write_frame(self._sock, encode(header, blobs))

    def call(self, header: dict[str, Any], blobs: Sequence[bytes] = ()) -> Any:
        """Send a request and wait for its answer; raises what the parent failed with."""
        with self._lock:
            self._next += 1
            ident = self._next
            event: threading.Event = threading.Event()
            slot: list[Any] = []
            self._waiting[ident] = (event, slot)
        self.send({**header, "id": ident}, blobs)
        event.wait()
        message = slot[0]
        if "error" in message:
            raise error_from_wire(tuple(message["error"]))
        return message["value"]


# --------------------------------------------------------------------------- proxies


class _Faults:
    """Applies `fault` once, at `at` ("start", "progress" or "model")."""

    def __init__(self, fault: str | None, at: str, memory: int) -> None:
        self.fault, self.at, self.memory = fault, at, memory
        self._done = threading.Lock()

    def at_point(self, point: str) -> None:
        if self.fault is None or point != self.at or not self._done.acquire(blocking=False):
            return
        apply_fault(self.fault, self.memory)


def apply_fault(fault: str, memory: int) -> None:
    if fault == "abort":
        os.abort()
    elif fault == "segv":
        os.kill(os.getpid(), signal.SIGSEGV)
        time.sleep(5)
    elif fault == "kill9":
        os.kill(os.getpid(), signal.SIGKILL)
        time.sleep(5)
    elif fault == "exit":
        os._exit(3)
    elif fault == "oom":
        _ = bytearray(max(memory, 1 << 30) * 4)  # past the cap: MemoryError
    elif fault == "hang":
        while True:
            time.sleep(3600)


class ProxyVerifier:
    """`models.verifier.Verifier` answered by the parent's verifier (fail-open like it)."""

    def __init__(self, link: Link) -> None:
        self._link = link

    def _ask(self, check: str, png: bytes, **fields: Any) -> Any:
        from readablestem.models.verifier import VerifierVerdict

        try:
            return self._link.call({"op": "verify", "check": check, **fields}, [png])
        except Exception:  # the parent's verifier never raises; if the request did, fail open
            return VerifierVerdict(check=check, probabilities={}, reason_code="api_error")

    def figure_alt(self, png: bytes, *, alt: str, caption: str | None) -> Any:
        return self._ask("figure_alt", png, alt=alt, caption=caption)

    def equation_match(self, png: bytes, *, latex: str) -> Any:
        return self._ask("equation_match", png, latex=latex)


class ProxyClient:
    """`VisionClient` whose requests are answered by the parent's real client."""

    def __init__(self, link: Link, faults: _Faults, *, verifier: bool) -> None:
        self._link = link
        self._faults = faults
        if verifier:
            self.verifier = ProxyVerifier(link)

    def run(self, request: VisionRequest, schema: type, *, is_fallback: bool = False) -> Any:
        self._faults.at_point("model")
        header = {
            "op": "model",
            "step": request.step,
            "model": request.model,
            "prompt_version": request.prompt_version,
            "instructions": request.instructions,
            "context_text": request.context_text,
            "max_output_tokens": request.max_output_tokens,
            "image_detail": request.image_detail,
            "context_limit": request.context_limit,
            "effort": request.effort,
            "timeout_s": request.timeout_s,
            "schema": f"{schema.__module__}:{schema.__qualname__}",
            "is_fallback": is_fallback,
        }
        return self._link.call(header, request.images)


class ProxyStepCache:
    def __init__(self, link: Link) -> None:
        self._link = link

    def lookup(self, element: Element, key: str) -> Fix | None:
        try:
            return self._link.call(
                {"op": "cache_lookup", "element": element.model_dump(mode="json"), "key": key}
            )
        except Exception:  # a miss, as the cache's own contract says
            return None

    def store(self, element: Element, key: str, fix: Fix) -> None:
        with contextlib.suppress(Exception):
            self._link.call(
                {
                    "op": "cache_store",
                    "element": element.model_dump(mode="json"),
                    "key": key,
                    "fix": fix.model_dump(mode="json"),
                }
            )


# --------------------------------------------------------------------------- the child


def _harden(memory_bytes: int, cpu_seconds: int) -> None:
    """Cap address space and CPU time, no core file (it would hold the PDF), and die with
    the parent (Linux). Each step is best effort where the platform lacks it."""
    from readablestem.web.pdf_probe import limit_memory

    limit_memory(memory_bytes)
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if cpu_seconds > 0:
            _soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
            soft = cpu_seconds if hard == resource.RLIM_INFINITY else min(cpu_seconds, hard)
            resource.setrlimit(resource.RLIMIT_CPU, (soft, hard))
    except (ImportError, ValueError, OSError):
        pass
    if sys.platform.startswith("linux"):
        # If the host runs short of memory, the kernel's OOM killer should take this process
        # (one job) before the web process (every session). Before dumpable is cleared below:
        # /proc/self is root's after that.
        with contextlib.suppress(OSError), open("/proc/self/oom_score_adj", "w") as handle:
            handle.write("1000")
        try:
            import ctypes

            libc = ctypes.CDLL(None, use_errno=True)
            libc.prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
            # Not dumpable: a crash is not handed to the host's crash reporter (apport,
            # systemd-coredump), which would hold a dead child for seconds while it copies
            # the memory, the PDF's text included, into a report.
            libc.prctl(4, 0)  # PR_SET_DUMPABLE
        except (OSError, AttributeError):
            pass


def _classify(exc: BaseException) -> dict[str, Any]:
    """The `error` frame for an exception that ended the conversion. Codes and numbers
    only: the message of a MuPDF error quotes the PDF."""
    from readablestem.config import ConfigError
    from readablestem.core.budget import BudgetExceeded
    from readablestem.models.pricing import UnknownModelError
    from readablestem.pipeline.layout import LayoutError

    if isinstance(exc, LayoutError):
        return {"kind": "layout", "code": str(exc.code), "message": str(exc)}
    if isinstance(exc, BudgetExceeded):
        return {"kind": "budget", "scope": str(exc.scope), "message": str(exc)}
    if isinstance(exc, ConfigError):
        return {"kind": "config", "message": str(exc)}
    from readablestem.source.convert import SourceFailed

    if isinstance(exc, SourceFailed):  # a category code, never the source's own words
        return {"kind": "source", "code": exc.code}
    if isinstance(exc, UnknownModelError):
        return {"kind": "unknown_model", "message": str(exc)}
    text = str(exc).lower()
    if isinstance(exc, MemoryError) or "alloc" in text or "out of memory" in text:
        return {"kind": "oom"}
    return {"kind": "internal", "type": type(exc).__name__}


def _settings_from(values: dict[str, Any]):
    from readablestem.config import Settings

    return Settings.model_construct(**values)


def main(argv: list[str] | None = None) -> int:
    """Exit status: 0 (an answer was sent, whatever it was), `EXIT_PARENT_GONE`, `EXIT_OOM`
    (out of memory even for the error report) or `EXIT_BROKEN` (this process failed, not
    the PDF). `--check` only imports what a conversion needs: 0 or `EXIT_BROKEN`."""
    args = sys.argv[1:] if argv is None else argv
    sandbox.make_undumpable()  # a sandboxed program of the same user cannot read this process
    try:
        if args and args[0] == "--check":
            _prepare({"settings": {}})
            return 0
        return _main(args)
    except MemoryError:
        return EXIT_OOM
    except BaseException:
        return EXIT_BROKEN


def _prepare(init: dict[str, Any]):
    """Everything a conversion needs before it reads the PDF: the settings, the pipeline's
    imports, the thread cap. Returns `convert_pdf` and the settings. A failure here is the
    child's own (a missing library, an unreadable model file), reported as "startup"."""
    settings = _settings_from(init["settings"])
    # Modules that read `get_settings()` (the assembler) get the parent's settings, not a
    # fresh read of this process's stripped environment. Set before any pipeline import.
    from readablestem import config

    config.get_settings = lambda: settings  # ty: ignore[invalid-assignment]

    from readablestem.pipeline import tables
    from readablestem.pipeline.run import convert_pdf

    # The ONNX runtime sizes its thread pools from this: not from the host's core count.
    host_allowance = tables.cpu_allowance
    tables.cpu_allowance = lambda: min(host_allowance(), MAX_THREADS)  # ty: ignore[invalid-assignment]
    return convert_pdf, settings


def _main(args: list[str]) -> int:
    sock = socket.socket(fileno=int(args[0]))
    _harden(int(args[1]), int(args[2]))
    link = Link(sock)
    init = link.wait_for_init()
    faults = _Faults(init.get("fault"), init.get("fault_at", "start"), int(args[1]))
    faults.at_point("start")
    try:
        try:
            convert_pdf, settings = _prepare(init)
        except MemoryError:
            raise
        except Exception as exc:  # the child's own failure: the type, never the message
            link.send({"op": "error", "kind": "startup", "type": type(exc).__name__})
            return 0
        output = _convert(link, init, faults, convert_pdf, settings)
        header = {
            "op": "done",
            "result": output.result.model_dump(mode="json"),
            "checks": [c.model_dump(mode="json") for c in getattr(output, "checks", [])],
            "crop_ids": list(output.crops),
        }
        blobs = list(output.crops.values())
    except BaseException as exc:
        header, blobs = {"op": "error", **_classify(exc)}, []
    try:
        link.send(header, blobs)
    except OSError:
        return EXIT_PARENT_GONE
    return 0


def _convert(link: Link, init: dict[str, Any], faults: _Faults, convert_pdf, settings):
    """Build the callbacks the parent asked for, then `convert_pdf`."""
    kwargs: dict[str, Any] = {}
    if init["progress"]:

        def progress(pages_done: int, pages_total: int) -> None:
            faults.at_point("progress")
            link.call({"op": "progress", "done": pages_done, "total": pages_total})

        kwargs["progress"] = progress
    if init["stage_progress"]:

        def stage_progress(event: ProgressEvent) -> None:
            faults.at_point("progress")
            link.call({"op": "stage", "event": event.model_dump(mode="json")})

        kwargs["stage_progress"] = stage_progress
    if init["step_cache"]:
        kwargs["step_cache"] = ProxyStepCache(link)
    if init["call_observer"]:

        def observe(call: ModelCall) -> None:
            with contextlib.suppress(OSError):  # the parent is gone: the reader exits us
                link.send({"op": "observe", "call": call.model_dump(mode="json")})

        kwargs["call_observer"] = observe
    return convert_pdf(
        init["pdf"],
        settings=settings,
        client=ProxyClient(link, faults, verifier=init["verifier"]),
        pages=init["pages"],
        concurrency=init["concurrency"],
        notice=init["notice"],
        source_note=init["source_note"],
        options=init["options"],
        time_limit_s=init.get("limit_s"),
        **kwargs,
    )


if __name__ == "__main__":
    # `os._exit`: the answer is sent, and a thread left over (a pool, the model runtime)
    # must not keep a finished or failed child alive.
    os._exit(main())
