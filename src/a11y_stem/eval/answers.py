"""Live model answers kept in a file: asked once, replayed on every later run.

`eval pipeline --answers FILE` wraps the run's client in a `CachedClient`. A request whose
answer is already in the file is answered from it, with the recorded `ModelCall` (tokens,
cost, latency, outcome), and costs nothing; any other request goes to the real client and
its answer is appended to the file at once (so a run that stops keeps what it paid for).
`--answers-offline` turns a miss into a refusal instead of a call, which makes a replay
spend nothing whatever the run asks for; the misses are counted and printed.

The key is `proof.replay.request_key` (step, model, prompt version, instructions, the
images by what they show, the fenced text, token limit, image detail) plus what else
changes the answer without being part of the request: the thinking effort the client will
ask for, and whether the untrusted-text notice rides in the system block. So an arm that
changes nothing a model can see (a different routing rule, say) shares every answer with the
baseline, and an arm that does change it (a lower effort, a smaller picture, other context)
asks again and is recorded beside it. Answers and costs are the recording's: a cost read
from the file is the one the recording call paid at list price.

The file is JSON lines, one `{"t": "model", "k": key, ...}` row per answer, the format of
`proof.replay`. It holds model outputs for development-set items only (public OpenStax and
project-authored text); keep it outside the repository.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from a11y_stem.config import Settings
from a11y_stem.core.types import ModelCall
from a11y_stem.models.base import T, VisionRequest
from a11y_stem.proof.bundle import dump_line
from a11y_stem.proof.replay import AnswerSet, request_key


def effective_effort(settings: Settings, request: VisionRequest) -> str | None:
    """The thinking effort a Claude client will send for `request` (None: not Claude)."""
    if settings.model_provider != "anthropic":
        return None
    return request.effort or settings.anthropic_effort_for(request.model)


def answer_key(settings: Settings, request: VisionRequest) -> str:
    """Everything that decides the answer, as one hash."""
    extra = [
        effective_effort(settings, request),
        bool(getattr(settings, "anthropic_notice_in_system", False)),
    ]
    text = request_key(request) + json.dumps(extra, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


class CachedClient:
    """A `VisionClient` that answers from a file and asks `inner` only for what is missing.

    `inner` None (offline): a miss comes back refused, with no call and no cost."""

    def __init__(self, inner: Any, path: Path, settings: Settings) -> None:
        self._inner = inner
        self._path = path
        self._settings = settings
        self._lock = threading.Lock()
        self._answers = AnswerSet(load_rows(path))
        self._local = threading.local()  # per thread: did the last first attempt only stage?
        self.verifier = getattr(inner, "verifier", None)
        self.hits = 0
        self.misses = 0
        self.live_cost_usd = 0.0
        self.replayed_cost_usd = 0.0

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        key = answer_key(self._settings, request)
        if not is_fallback:
            self._local.staged = False
        elif getattr(self._local, "staged", False):
            # The first attempt got a placeholder from a staged run, not a failure: its
            # fallback is not a real request and must not be collected.
            return None, ModelCall(
                step=request.step,
                model=request.model,
                prompt_version=request.prompt_version,
                is_fallback=True,
                outcome="api_error",
            )
        with self._lock:
            row = self._answers.model(key, request.step)
            if row is not None:
                self.hits += 1
            else:
                self.misses += 1
                # AnswerSet counted the miss as a replay miss; a live call is not one.
                self._answers.stats.misses.pop()
        if row is not None:
            call = ModelCall.model_validate(row["call"])
            self.replayed_cost_usd += call.cost_usd
            payload = row["payload"]
            if payload is None:
                return None, call
            try:
                return schema.model_validate(payload), call
            except ValueError:
                return None, call.model_copy(update={"outcome": "invalid_output"})
        if self._inner is None:
            call = ModelCall(
                step=request.step,
                model=request.model,
                prompt_version=request.prompt_version,
                is_fallback=is_fallback,
                outcome="refused",
            )
            return None, call
        parsed, call = self._inner.run(request, schema, is_fallback=is_fallback)
        if getattr(self._inner, "stages", False):
            self._local.staged = True
            return parsed, call
        self.live_cost_usd += call.cost_usd
        # An answer lost to an outage or a budget stop is not worth keeping: ask again later.
        if call.outcome in ("ok", "invalid_output", "refused"):
            self.record(key, request.step, parsed, call)
        return parsed, call

    def record(self, key: str, step: str, parsed: Any, call: ModelCall) -> None:
        row = {
            "t": "model",
            "k": key,
            "step": step,
            "payload": parsed.model_dump(mode="json") if parsed is not None else None,
            "call": call.model_dump(mode="json"),
        }
        self._answers.add(row)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(dump_line(row) + "\n")

    def reset(self) -> None:
        """Zero the counters (a staged run counts its last pass only)."""
        self.hits = self.misses = 0
        self.live_cost_usd = self.replayed_cost_usd = 0.0

    def summary(self) -> Mapping[str, Any]:
        return {
            "answers_file_hits": self.hits,
            "answers_file_misses": self.misses,
            "live_cost_usd": round(self.live_cost_usd, 6),
            "replayed_cost_usd": round(self.replayed_cost_usd, 6),
        }
