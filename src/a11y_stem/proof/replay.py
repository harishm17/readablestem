"""Record model answers in a live run; replay them, exactly, in any later run.

The pipeline talks to a model only through `VisionClient.run(request, schema)` (and the
second automated check through a `Verifier`). A live proof run wraps the real client in a
`RecordingClient`: every request's answer is kept under a key that is a hash of everything
the request carried (step, model, prompt version, instructions, the images, the fenced
text, token cap, image detail). A replay run gives the pipeline a `ReplayClient` instead,
which looks the same request up and hands back the recorded answer and the recorded
`ModelCall` (tokens, cost, latency, outcome). Everything else, the layout analysis, the
checks, the triage, the assembly and the scoring, runs for real on the committed PDFs.

Images are keyed by what they show, not by how a PNG encoder wrote them: the SHA-256 of
the decoded RGBA pixels ("px:..."). For the equations and figures tasks the replay does
not carry the pictures at all: `ReplayMedia` hands out a blank PNG of the recorded size
that declares the recorded identity in a text chunk, and `image_identity` reads it. A
PNG that is not decodable is keyed by its bytes ("raw:...").

A request the bundle has no answer for is a miss. The replay never invents an answer and
never raises from inside the pipeline (a step could turn the exception into a failed fix
and hide it): it counts the miss, answers "refused", and the runner stops the proof when
the run ends with any miss. Repeated identical requests inside one run are answered from
the recorded list in order, then cycle; the runner replays on one thread so the order is
fixed.

The same module holds the media stand-ins (`RecordingMedia`, `ReplayMedia`) and the axe
recorder, because an axe-core run needs a browser and its output is a function of the HTML.
"""

from __future__ import annotations

import hashlib
import io
import json
import struct
import threading
from collections import defaultdict
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest import mock

from PIL import Image, PngImagePlugin

from a11y_stem.core.types import Check, ModelCall
from a11y_stem.models.base import T, VisionClient, VisionRequest
from a11y_stem.models.verifier import (
    EQUATION_CHECK,
    EQUATION_VERSION,
    FIGURE_CHECK,
    FIGURE_VERSION,
    Verifier,
    VerifierVerdict,
    verifier_of,
)
from a11y_stem.pipeline import html_checks
from a11y_stem.proof.bundle import Bundle, dump_line
from a11y_stem.proof.errors import BundleError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
IDENTITY_KEYWORD = "a11y-proof-identity"
MAX_STANDIN_SIDE = 8192


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- images


def declared_identity(png: bytes) -> str | None:
    """The identity a stand-in PNG declares in its text chunk, if it has one."""
    if png[:8] != PNG_SIGNATURE:
        return None
    pos = 8
    while pos + 8 <= len(png):
        (length,) = struct.unpack(">I", png[pos : pos + 4])
        kind = png[pos + 4 : pos + 8]
        if kind == b"tEXt":
            key, _, value = png[pos + 8 : pos + 8 + length].partition(b"\x00")
            if key == IDENTITY_KEYWORD.encode("ascii"):
                return value.decode("ascii", "replace")
        if kind in (b"IDAT", b"IEND"):
            return None
        pos += 12 + length
    return None


def pixel_identity(png: bytes) -> str:
    """SHA-256 of the decoded pixels ("px:..."), or of the bytes ("raw:...") when the
    data is not an image PIL can read."""
    try:
        with Image.open(io.BytesIO(png)) as image:
            rgba = image.convert("RGBA")
    except (OSError, ValueError, SyntaxError):
        return "raw:" + hashlib.sha256(png).hexdigest()
    digest = hashlib.sha256(f"{rgba.width}x{rgba.height}".encode())
    digest.update(rgba.tobytes())
    return "px:" + digest.hexdigest()


def image_identity(png: bytes) -> str:
    return declared_identity(png) or pixel_identity(png)


def png_size(png: bytes) -> tuple[int, int] | None:
    if len(png) >= 24 and png[:8] == PNG_SIGNATURE and png[12:16] == b"IHDR":
        width, height = struct.unpack(">II", png[16:24])
        return width, height
    return None


def standin_png(identity: str, width: int, height: int) -> bytes:
    """A blank 1-bit PNG of the recorded size that declares `identity`."""
    width = min(max(width, 1), MAX_STANDIN_SIDE)
    height = min(max(height, 1), MAX_STANDIN_SIDE)
    info = PngImagePlugin.PngInfo()
    info.add_text(IDENTITY_KEYWORD, identity)
    out = io.BytesIO()
    Image.new("1", (width, height), 1).save(out, format="PNG", pnginfo=info)
    return out.getvalue()


# ---------------------------------------------------------------------------- keys


def request_key(request: VisionRequest) -> str:
    parts = [
        request.step,
        request.model,
        request.prompt_version,
        _sha(request.instructions),
        [image_identity(png) for png in request.images],
        request.context_text,
        request.max_output_tokens,
        request.image_detail,
    ]
    return _sha(json.dumps(parts, ensure_ascii=False, separators=(",", ":")))


def verify_key(check: str, version: str, png: bytes, *texts: str | None) -> str:
    parts = [check, version, image_identity(png), *texts]
    return _sha(json.dumps(parts, ensure_ascii=False, separators=(",", ":")))


# ------------------------------------------------------------------ answer storage


class AnswerStore:
    """What one live run answered: model calls, second-check verdicts, axe results."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[dict[str, Any]] = []

    def add(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._rows.append(row)

    def rows(self) -> list[dict[str, Any]]:
        """In a fixed order (type, key, content): live order depends on thread timing."""
        with self._lock:
            return sorted(self._rows, key=lambda r: (r["t"], r["k"], dump_line(r)))

    def requests(self) -> int:
        with self._lock:
            return sum(r["t"] == "model" for r in self._rows)


@dataclass
class ReplayStats:
    requests: int = 0
    reused: int = 0  # answered from the list again after it ran out
    verify_requests: int = 0
    axe_requests: int = 0
    misses: list[str] = field(default_factory=list)  # content-free: "model equation 3fa9c2d1e0b4"

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "reused": self.reused,
            "verify_requests": self.verify_requests,
            "axe_requests": self.axe_requests,
            "misses": len(self.misses),
        }


class AnswerSet:
    """The recorded answers of one run, loaded for replay."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._lock = threading.Lock()
        self._model: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._verify: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._axe: dict[str, list[dict[str, Any]]] = {}
        self._cursor: dict[tuple[str, str], int] = defaultdict(int)
        self.stats = ReplayStats()
        for row in rows:
            if row["t"] == "model":
                self._model[row["k"]].append(row)
            elif row["t"] == "verify":
                self._verify[row["k"]].append(row)
            elif row["t"] == "axe":
                self._axe[row["k"]] = row["checks"]
            else:
                raise BundleError(f"an answers file holds a row of unknown type {row['t']!r}")

    @classmethod
    def load(cls, bundle: Bundle, task: str, repeat: int) -> AnswerSet:
        return cls(bundle.jsonl(f"answers/{task}.run{repeat}.jsonl"))

    def _take(self, table: dict[str, list[dict[str, Any]]], name: str, key: str) -> dict | None:
        with self._lock:
            rows = table.get(key)
            if not rows:
                return None
            index = self._cursor[(name, key)]
            self._cursor[(name, key)] = index + 1
            if index >= len(rows):
                self.stats.reused += 1
            return rows[index % len(rows)]

    def model(self, key: str, step: str) -> dict[str, Any] | None:
        with self._lock:
            self.stats.requests += 1
        row = self._take(self._model, "model", key)
        if row is None:
            with self._lock:
                self.stats.misses.append(f"model {step} {key[:12]}")
        return row

    def verdict(self, key: str, check: str) -> dict[str, Any] | None:
        with self._lock:
            self.stats.verify_requests += 1
        row = self._take(self._verify, "verify", key)
        if row is None:
            with self._lock:
                self.stats.misses.append(f"verify {check} {key[:12]}")
        return row

    def axe(self, key: str) -> list[dict[str, Any]] | None:
        with self._lock:
            self.stats.axe_requests += 1
            checks = self._axe.get(key)
            if checks is None:
                self.stats.misses.append(f"axe {key[:12]}")
            return checks


# --------------------------------------------------------------------------- clients


class RecordingVerifier:
    """Passes the second check through and records each verdict."""

    def __init__(self, inner: Verifier, store: AnswerStore) -> None:
        self._inner = inner
        self._store = store

    def figure_alt(self, png: bytes, *, alt: str, caption: str | None) -> VerifierVerdict:
        verdict = self._inner.figure_alt(png, alt=alt, caption=caption)
        key = verify_key(FIGURE_CHECK, FIGURE_VERSION, png, alt, caption)
        self._record(key, verdict)
        return verdict

    def equation_match(self, png: bytes, *, latex: str) -> VerifierVerdict:
        verdict = self._inner.equation_match(png, latex=latex)
        self._record(verify_key(EQUATION_CHECK, EQUATION_VERSION, png, latex), verdict)
        return verdict

    def _record(self, key: str, verdict: VerifierVerdict) -> None:
        self._store.add(
            {
                "t": "verify",
                "k": key,
                "verdict": {
                    "check": verdict.check,
                    "probabilities": dict(verdict.probabilities),
                    "reason_code": verdict.reason_code,
                    "model": verdict.model,
                    "version": verdict.version,
                    "input_tokens": verdict.input_tokens,
                    "cost_usd": verdict.cost_usd,
                    "latency_ms": verdict.latency_ms,
                },
            }
        )


class ReplayVerifier:
    """Answers the second check from the recorded verdicts. A request with no recorded
    verdict comes back "skipped", as the real check does when it cannot answer; the miss
    is counted and fails the proof."""

    def __init__(self, answers: AnswerSet) -> None:
        self._answers = answers

    def figure_alt(self, png: bytes, *, alt: str, caption: str | None) -> VerifierVerdict:
        key = verify_key(FIGURE_CHECK, FIGURE_VERSION, png, alt, caption)
        return self._lookup(key, FIGURE_CHECK, FIGURE_VERSION)

    def equation_match(self, png: bytes, *, latex: str) -> VerifierVerdict:
        key = verify_key(EQUATION_CHECK, EQUATION_VERSION, png, latex)
        return self._lookup(key, EQUATION_CHECK, EQUATION_VERSION)

    def _lookup(self, key: str, check: str, version: str) -> VerifierVerdict:
        row = self._answers.verdict(key, check)
        if row is None:
            return VerifierVerdict(check, {}, "skipped", "", version)
        v = row["verdict"]
        return VerifierVerdict(
            check=v["check"],
            probabilities=dict(v["probabilities"]),
            reason_code=v["reason_code"],
            model=v["model"],
            version=v["version"],
            input_tokens=int(v["input_tokens"]),
            cost_usd=float(v["cost_usd"]),
            latency_ms=int(v["latency_ms"]),
        )


class RecordingClient:
    """Wraps the real `VisionClient` of a live run and records every answer."""

    def __init__(self, inner: VisionClient, store: AnswerStore) -> None:
        self._inner = inner
        self.store = store
        inner_verifier = verifier_of(inner)
        self.verifier: Verifier | None = (
            RecordingVerifier(inner_verifier, store) if inner_verifier is not None else None
        )

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        parsed, call = self._inner.run(request, schema, is_fallback=is_fallback)
        self.store.add(
            {
                "t": "model",
                "k": request_key(request),
                "step": request.step,
                "payload": parsed.model_dump(mode="json") if parsed is not None else None,
                "call": call.model_dump(mode="json"),
            }
        )
        return parsed, call


class ReplayClient:
    """A `VisionClient` that answers from a recorded run. No key, no network, no cost."""

    def __init__(self, answers: AnswerSet, *, second_check: bool) -> None:
        self.answers = answers
        self.verifier: Verifier | None = ReplayVerifier(answers) if second_check else None

    @property
    def stats(self) -> ReplayStats:
        return self.answers.stats

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        row = self.answers.model(request_key(request), request.step)
        if row is None:
            call = ModelCall(
                step=request.step,
                model=request.model,
                prompt_version=request.prompt_version,
                is_fallback=is_fallback,
                outcome="refused",
            )
            return None, call
        call = ModelCall.model_validate(row["call"])
        payload = row["payload"]
        if payload is None:
            return None, call
        try:
            return schema.model_validate(payload), call
        except ValueError:  # the schema changed since the recording: count it as a miss
            self.answers.stats.misses.append(f"schema {request.step} {row['k'][:12]}")
            return None, call.model_copy(update={"outcome": "invalid_output"})


# ----------------------------------------------------------------------------- axe


def axe_key(html: str) -> str:
    return _sha(html)


class AxeRecorder:
    """Runs axe-core as usual and keeps the result, keyed by the HTML it ran on."""

    def __init__(self, store: AnswerStore, real: Callable[[str], list[Check]]) -> None:
        self._store = store
        self._real = real

    def __call__(self, html: str) -> list[Check]:
        checks = self._real(html)
        self._store.add(
            {"t": "axe", "k": axe_key(html), "checks": [c.model_dump(mode="json") for c in checks]}
        )
        return checks


class AxeReplay:
    """Returns the recorded axe-core result for the HTML; no browser needed."""

    def __init__(self, answers: AnswerSet) -> None:
        self._answers = answers

    def __call__(self, html: str) -> list[Check]:
        rows = self._answers.axe(axe_key(html))
        if rows is None:
            return [html_checks.AXE_SKIPPED]
        return [Check.model_validate(r) for r in rows]


@contextmanager
def patched_axe(replacement: Callable[[str], list[Check]]) -> Generator[None]:
    """Use `replacement` wherever the eval calls `html_checks.axe_checks`."""
    with mock.patch.object(html_checks, "axe_checks", replacement):
        yield


# ---------------------------------------------------------------------------- media


class MediaRecorder:
    """What the inputs of a live run were, enough to rebuild them as stand-ins."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.equations: dict[str, dict[str, Any]] = {}
        self.figures: dict[str, dict[str, Any]] = {}
        self.pdfs: dict[str, bytes] = {}

    def equation(self, record_id: str, **fields: Any) -> None:
        with self._lock:
            self.equations.setdefault(record_id, {"id": record_id}).update(fields)

    def figure(self, url: str, png: bytes) -> None:
        width, height = png_size(png) or (0, 0)
        row = {"url": url, "identity": image_identity(png), "width": width, "height": height}
        with self._lock:
            self.figures[url] = row

    def pdf(self, section_id: str, data: bytes) -> None:
        with self._lock:
            known = self.pdfs.setdefault(section_id, data)
        if known != data:
            raise BundleError(f"two different PDFs were printed for {section_id}")

    def equation_rows(self) -> list[dict[str, Any]]:
        return [self.equations[k] for k in sorted(self.equations)]

    def figure_rows(self) -> list[dict[str, Any]]:
        return [self.figures[k] for k in sorted(self.figures)]


def section_of(page_records: list[dict]) -> str:
    return f"{page_records[0]['book_slug']}/{page_records[0]['page_slug']}"


class RecordingMedia:
    """Wraps the real media of a live run (`eval.pipeline_eval.CachedMedia`)."""

    def __init__(self, inner: Any, recorder: MediaRecorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def equation_png(self, record: dict) -> bytes:
        png = self._inner.equation_png(record)
        width, height = png_size(png) or (0, 0)
        self._recorder.equation(
            record["id"], identity=image_identity(png), width=width, height=height
        )
        return png

    def equation_text(self, record: dict) -> str | None:
        text = self._inner.equation_text(record)
        self._recorder.equation(record["id"], text=text)
        return text

    def equation_context(self, record: dict) -> str | None:
        context = self._inner.equation_context(record)
        self._recorder.equation(record["id"], context=context)
        return context

    def figure_png(self, url: str) -> bytes:
        png = self._inner.figure_png(url)
        self._recorder.figure(url, png)
        return png

    def page_pdf(self, page_records: list[dict]) -> bytes:
        data = self._inner.page_pdf(page_records)
        self._recorder.pdf(section_of(page_records), data)
        return data

    def close(self) -> None:
        self._inner.close()


class ReplayMedia:
    """Inputs for a replay: the committed PDFs and stand-ins for the pictures."""

    def __init__(self, bundle: Bundle) -> None:
        self._bundle = bundle
        self._equations = {r["id"]: r for r in bundle.jsonl("media/equations.jsonl")}
        self._figures = {r["url"]: r for r in bundle.jsonl("media/figures.jsonl")}

    def _equation(self, record: dict) -> dict[str, Any]:
        row = self._equations.get(record["id"])
        if row is None:
            raise BundleError(f"the bundle holds no input for the equation {record['id']}")
        return row

    def equation_png(self, record: dict) -> bytes:
        row = self._equation(record)
        return standin_png(row["identity"], int(row["width"]), int(row["height"]))

    def equation_text(self, record: dict) -> str | None:
        return self._equation(record).get("text")

    def equation_context(self, record: dict) -> str | None:
        return self._equation(record).get("context")

    def figure_png(self, url: str) -> bytes:
        row = self._figures.get(url)
        if row is None:
            raise BundleError("the bundle holds no input for a figure image of the gold set")
        return standin_png(row["identity"], int(row["width"]), int(row["height"]))

    def page_pdf(self, page_records: list[dict]) -> bytes:
        return self._bundle.pdf_bytes(section_of(page_records))

    def close(self) -> None:
        return None
