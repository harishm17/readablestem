"""Live model answers kept in a file: asked once, replayed on every later run.

`eval pipeline --answers FILE` wraps the run's client in a `CachedClient`. A request whose
answer is already in the file is answered from it, with the recorded `ModelCall` (tokens,
cost, latency, outcome), and costs nothing; any other request goes to the real client and
its answer is appended to the file at once (so a run that stops keeps what it paid for).
`--answers-offline` turns a miss into a refusal instead of a call, which makes a replay
spend nothing whatever the run asks for; the misses are counted and printed.

The key is `models.question.question_key`: everything the model is sent (step, model,
prompt version, the system text, the images by what they show, the fenced text, token limit,
image detail, the thinking effort, the answer format). Nothing about the release is in it.
So an arm that changes nothing a model can see (a different routing rule, a new pipeline
version) shares every answer with the baseline, and an arm that does change it (a lower
effort, a smaller picture, an edited prompt or schema) asks again and is recorded beside it.
Answers and costs are the recording's: a cost read from the file is the one the recording
call paid at list price.

The file is JSON lines, one `{"t": "model", "k": key, "q": what was asked, ...}` row per
answer, the format of `proof.replay`. "q" is the content-free list the key is the hash of
(`models.question.asked`); with it a request the file cannot answer is counted by reason
("prompt equation-1a2b3c4d recorded, equation-9f8e7d6c asked"). It holds model outputs for
development-set items only (public OpenStax and project-authored text); keep it outside the
repository.

A file recorded under the older key (before the answer format and the fence were part of
it) is read with `rekey`: a request found only under its old key is answered from that row,
and the row is appended again under the new key, so one replay of the runs a file was made
for moves it to the new keys without a call. Rows no run asks for stay as they were.

`live_limit_usd` bounds what a process spends on new answers for one file: once the calls
it paid for add up to the limit, a miss is refused as it is offline. Replayed answers cost
nothing and do not count. The limit is checked before a call, so the overshoot is at most
the calls already on their way.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from readablestem.config import Settings
from readablestem.core.types import ModelCall
from readablestem.models.base import T, VisionRequest
from readablestem.models.question import asked, effective_effort, key_of, legacy_key
from readablestem.models.question import question_key as answer_key
from readablestem.proof.bundle import dump_line
from readablestem.proof.replay import AnswerSet

__all__ = [
    "CachedClient",
    "answer_key",
    "effective_effort",
    "keep_second_check_answers",
    "load_rows",
]

_FILE_LOCKS: dict[Path, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()
# What this process has paid for new answers, by answers file (`live_limit_usd`).
_LIVE_SPENT: dict[Path, float] = {}


def _file_lock(path: Path) -> threading.Lock:
    """The process-wide lock for appending to `path`."""
    key = path.resolve()
    with _FILE_LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(key, threading.Lock())


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

    `inner` None (offline): a miss comes back refused, with no call and no cost.
    `rekey`: a request the file holds only under its older key is answered from that row and
    the row is written again under the new key (module docstring).
    `live_limit_usd`: stop asking `inner` once this process has paid that much for new
    answers in this file."""

    def __init__(
        self,
        inner: Any,
        path: Path,
        settings: Settings,
        *,
        rekey: bool = False,
        live_limit_usd: float | None = None,
    ) -> None:
        self._inner = inner
        self._path = path
        self._settings = settings
        self._rekey = rekey
        self._live_limit = live_limit_usd
        self._lock = threading.Lock()
        self._answers = AnswerSet(load_rows(path))
        self._local = threading.local()  # per thread: did the last first attempt only stage?
        self.verifier = getattr(inner, "verifier", None)
        # The second check's own `CachedClient`, when the run keeps its answers too.
        self.second_check: CachedClient | None = None
        self.hits = 0
        self.misses = 0
        # Of the misses, those this client then answered live and wrote down (`unanswered`).
        self.answered_live = 0
        self.rekeyed = 0
        self.missed_because: Counter[str] = Counter()
        # The keys this client answered from the file or wrote to it: what a refresh keeps.
        self.used: set[str] = set()
        self.live_cost_usd = 0.0
        self.replayed_cost_usd = 0.0
        self._steps: dict[str, dict[str, float]] = {}

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        parts = asked(self._settings, request, schema)
        key = key_of(parts)
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
        moved = None
        with self._lock:
            row = self._answers.model(key, request.step)
            if row is None:
                # AnswerSet counted the miss as a replay miss; a live call is not one.
                self._answers.stats.misses.pop()
                if self._rekey:
                    row = moved = self._answers.taken(legacy_key(self._settings, request))
            if row is not None:
                self.hits += 1
                self.rekeyed += moved is not None
                self.used.add(key)
            else:
                self.misses += 1
                self.missed_because[self._answers.why_missing(parts)] += 1
        if moved is not None:
            self._write({**moved, "k": key, "q": parts})
        if row is not None:
            call = ModelCall.model_validate(row["call"])
            self.replayed_cost_usd += call.cost_usd
            self._tally(call)
            payload = row["payload"]
            if payload is None:
                return None, call
            try:
                return schema.model_validate(payload), call
            except ValueError:
                return None, call.model_copy(update={"outcome": "invalid_output"})
        if self._inner is None or self._limit_spent():
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
        with _file_lock(self._path):
            spent = _LIVE_SPENT.get(self._path.resolve(), 0.0)
            _LIVE_SPENT[self._path.resolve()] = spent + call.cost_usd
        self._tally(call)
        # An answer lost to an outage or a budget stop is not worth keeping: ask again later.
        if call.outcome in ("ok", "invalid_output", "refused"):
            self.record(key, request.step, parsed, call, parts)
            with self._lock:
                self.answered_live += 1
        return parsed, call

    def _limit_spent(self) -> bool:
        if self._live_limit is None:
            return False
        with _file_lock(self._path):
            return _LIVE_SPENT.get(self._path.resolve(), 0.0) >= self._live_limit

    def record(
        self,
        key: str,
        step: str,
        parsed: Any,
        call: ModelCall,
        asked: dict[str, Any] | None = None,
    ) -> None:
        """Keep one answer under `key`; `asked` is the list the key is the hash of."""
        row = {
            "t": "model",
            "k": key,
            "step": step,
            "payload": parsed.model_dump(mode="json") if parsed is not None else None,
            "call": call.model_dump(mode="json"),
        }
        if asked is not None:
            row["q"] = asked
        self._write(row)

    def _write(self, row: dict[str, Any]) -> None:
        self._answers.add(row)
        self.used.add(row["k"])
        # One lock per file for the process: the web app builds a client per job and per
        # re-read, and two of them may append to the same file at once.
        with _file_lock(self._path):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(dump_line(row) + "\n")

    @property
    def unanswered(self) -> int:
        """Requests that ended with no answer: not in the file and not answered live."""
        return self.misses - self.answered_live

    def reset(self) -> None:
        """Zero the counters (a staged run counts its last pass only)."""
        self.hits = self.misses = self.answered_live = self.rekeyed = 0
        self.missed_because = Counter()
        self.live_cost_usd = self.replayed_cost_usd = 0.0
        self._steps = {}

    def _tally(self, call: ModelCall) -> None:
        """Count a call's tokens and its cost twice: as billed, and as it would be billed
        with the instructions always read from the prompt cache. Whether a call found the
        cache warm depends on what ran before it, not on what it asked; the second figure
        is the one to compare two runs by."""
        from readablestem.models.pricing import cost_usd

        steady = cost_usd(
            call.model,
            call.input_tokens,
            call.cached_input_tokens + call.cache_creation_input_tokens,
            call.output_tokens,
        )
        with self._lock:
            row = self._steps.setdefault(
                f"{call.step}/{call.model}",
                dict.fromkeys(
                    ("calls", "input", "cached", "written", "output", "usd", "steady_usd"), 0.0
                ),
            )
            row["calls"] += 1
            row["input"] += call.input_tokens
            row["cached"] += call.cached_input_tokens
            row["written"] += call.cache_creation_input_tokens
            row["output"] += call.output_tokens
            row["usd"] += call.cost_usd
            row["steady_usd"] += steady

    def summary(self) -> Mapping[str, Any]:
        out = self._summary()
        if self.second_check is not None:
            out["second_check"] = dict(self.second_check._summary())
        return out

    def _summary(self) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        if self.rekeyed:
            extra["answers_file_rekeyed"] = self.rekeyed
        if self.missed_because:
            extra["answers_file_missed_because"] = dict(self.missed_because.most_common())
        return {
            "by_step": {
                k: {n: round(v, 6) for n, v in row.items()} for k, row in self._steps.items()
            },
            "steady_cost_usd": round(sum(r["steady_usd"] for r in self._steps.values()), 6),
            "answers_file_hits": self.hits,
            "answers_file_misses": self.misses,
            "live_cost_usd": round(self.live_cost_usd, 6),
            "replayed_cost_usd": round(self.replayed_cost_usd, 6),
            **extra,
        }


def keep_second_check_answers(client: Any, cached: CachedClient, *, offline: bool) -> None:
    """The Claude second check asks the model too: its answers go into the same file under
    their own step ("check"), so a replay spends nothing on it either. `client` is the real
    client the check hangs on, `cached` the one the pipeline sees. Offline, the check is
    handed to `cached` (whose inner client is None, so it has none of its own) behind a
    `CachedClient` that refuses a missing verdict: the check runs as it did when recorded,
    and a verdict the file lacks comes back as an unanswered check, with no call."""
    from readablestem.models.claude_verifier import ClaudeVerifier

    verifier = getattr(client, "verifier", None)
    if not isinstance(verifier, ClaudeVerifier):
        return

    def wrap(inner: Any) -> Any:
        check = CachedClient(
            None if offline else inner, cached._path, inner.settings, rekey=cached._rekey
        )
        cached.second_check = check
        return check

    verifier.wrap_client(wrap)
    if offline:
        cached.verifier = verifier
