"""The chatbot recipe: upload a PDF to a general model, type one prompt, take the answer.

This repeats `niche/chatbot-runs/scripts/run_chat.py` (mode "pages") of the research that
preceded the held-out run, and preregistration section 6.2:

- one user message: the PDF as an `input_file`, a 150 dpi PNG of every page as an
  `input_image` (detail high), then the prompt text last;
- no system or developer instructions, no tools, `store=False`, the API's default
  reasoning effort, `max_output_tokens` 64,000;
- the HTML in the reply is taken as it comes (the largest fenced code block, or what
  follows an unterminated fence, or the text from `<!DOCTYPE`/`<html`). A reply that is
  cut off or refused is scored as it is and counted as a failure of the section (status
  `truncated`/`empty`); it is not asked again;
- an infrastructure error (connection, 5xx, 429, timeout) is tried again at most twice,
  every attempt logged (codes only). Any other error is a crash of the section.

The prompts are in `chat_prompts.json`. The Purdue sentence is Purdue Innovative Learning's
("Accessibility in Mathematics"), quoted verbatim as the input it is.

One spend guard covers all calls: before each call the exact input tokens are counted (the
free `/responses/input_tokens` endpoint), the worst case (input plus `max_output_tokens`
at list price) is held against the cap, and the call is refused when it does not fit. The
actual cost of a call comes from its usage and `models.pricing`. Nothing but codes, counts,
costs and ids is logged; the HTML is written to files.

Claude arms (Addendum 6) send the same things through the Messages API: the PDF as a
`document` block, a 150 dpi PNG of every page as `image` blocks, the prompt last; no system
prompt, no tools, no `thinking` or `output_config` (the model's default effort), no refusal
fallback, `max_tokens` 64,000 (thinking counts toward it), streamed. The input is counted
with the free `count_tokens` endpoint, the status is `truncated` when the reply stopped at
`max_tokens`, and a refusal with no text is `empty`. The cost of a call is its usage at
list price; a call that failed after the request went out is charged its input tokens.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from readablestem.core.budget import BudgetExceeded, InMemorySpendGuard
from readablestem.models import pricing
from readablestem.proof.bundle import dumps, pdf_name

MAX_OUTPUT_TOKENS = 64_000
RENDER_DPI = 150
RETRIES = 2  # infrastructure errors only
RETRY_WAIT_S = (60, 180)  # a rate limit needs a minute, not seconds
TIMEOUT_S = 1800
PROMPTS: dict[str, str] = json.loads(
    (Path(__file__).with_name("chat_prompts.json")).read_text(encoding="utf-8")
)
CONTINUED = "CONTINUED IN NEXT REPLY"


@dataclass(frozen=True)
class ChatArm:
    id: str
    model: str
    prompt: str  # key of PROMPTS
    runs: int
    role: str  # "primary" | "secondary" | "descriptive"
    provider: str = "openai"  # "openai" | "anthropic"

    @property
    def prompt_text(self) -> str:
        return PROMPTS[self.prompt]


# The order is the order of spending: the primary arm first, the cheap arm next, the
# expensive secondary arm last (it runs only if the cap still holds it).
ARMS: tuple[ChatArm, ...] = (
    ChatArm("chatbot-sol-purdue", "gpt-6.1-sol", "purdue", 2, "primary"),
    ChatArm("chatbot-luna-purdue", "gpt-6-luna", "purdue", 2, "descriptive"),
    ChatArm("chatbot-sol-expert", "gpt-6.1-sol", "expert", 1, "secondary"),
)
# The arms of a run on Claude (Addendum 6): the Purdue prompt on the main and on the cheap
# model of the ReadableSTEM run. They replace the arms above; the expert prompt is not run.
CLAUDE_ARMS: tuple[ChatArm, ...] = (
    ChatArm("chatbot-opus-purdue", "claude-opus-5-5", "purdue", 2, "primary", "anthropic"),
    ChatArm("chatbot-haiku-purdue", "claude-haiku-5-5", "purdue", 2, "descriptive", "anthropic"),
)
ARM_BY_ID = {a.id: a for a in (*ARMS, *CLAUDE_ARMS)}
PRIMARY_IDS = tuple(a.id for a in (*ARMS, *CLAUDE_ARMS) if a.role == "primary")


def arms_for(provider: str) -> tuple[ChatArm, ...]:
    """The arms of a provider, in the order of spending."""
    return CLAUDE_ARMS if provider == "anthropic" else ARMS


def html_from(text: str) -> str:
    """The HTML of a reply, as `run_chat.py` took it."""
    fenced = re.findall(r"```(?:html)?[ \t]*\n(.*?)```", text, flags=re.S | re.I)
    if fenced:
        return max(fenced, key=len)
    unterminated = re.search(r"```(?:html)?[ \t]*\n(.*)$", text, flags=re.S | re.I)
    if unterminated:
        return unterminated.group(1)
    start = text.find("<!DOCTYPE")
    if start < 0:
        start = text.find("<html")
    return text[start:] if start >= 0 else text


def page_pngs(pdf: Path) -> list[bytes]:
    """A 150 dpi PNG of every page (poppler's pdftoppm, as the research used; PyMuPDF when
    poppler is not installed)."""
    if shutil.which("pdftoppm"):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                ["pdftoppm", "-r", str(RENDER_DPI), "-png", str(pdf), f"{tmp}/p"], check=True
            )
            return [f.read_bytes() for f in sorted(Path(tmp).glob("p*.png"))]
    import pymupdf

    with pymupdf.open(pdf) as doc:
        return [page.get_pixmap(dpi=RENDER_DPI).tobytes("png") for page in doc]


def pdf_page_count(pdf: Path) -> int:
    import pymupdf

    with pymupdf.open(pdf) as doc:
        return doc.page_count


def build_input(pdf: Path, prompt: str, pngs: Sequence[bytes]) -> list[dict[str, Any]]:
    data = base64.b64encode(pdf.read_bytes()).decode()
    content: list[dict[str, Any]] = [
        {
            "type": "input_file",
            "filename": pdf.name,
            "file_data": f"data:application/pdf;base64,{data}",
        }
    ]
    for png in pngs:
        url = "data:image/png;base64," + base64.b64encode(png).decode()
        content.append({"type": "input_image", "image_url": url, "detail": "high"})
    content.append({"type": "input_text", "text": prompt})
    return [{"role": "user", "content": content}]


def run_stem(section_id: str, run: int, runs: int) -> str:
    base = pdf_name(section_id)[: -len(".pdf")]
    return f"{base}.run{run}" if runs > 1 else base


@dataclass
class Job:
    arm: ChatArm
    section: str
    run: int
    pdf: Path

    @property
    def stem(self) -> str:
        return run_stem(self.section, self.run, self.arm.runs)


def jobs_for(
    arms: Sequence[ChatArm], pdfs: dict[str, Path], *, runs: int | None = None
) -> list[Job]:
    """Every (arm, run, section). `runs` fewer than an arm's own makes fewer runs of it; the
    files are still named for the arm's own number of runs."""
    jobs = []
    for arm in arms:
        for run in range(1, (min(runs, arm.runs) if runs else arm.runs) + 1):
            for section, path in pdfs.items():
                jobs.append(Job(arm, section, run, path))
    return jobs


_INFRASTRUCTURE = (
    "APIConnectionError",
    "APITimeoutError",
    "InternalServerError",
    "OverloadedError",
    "RateLimitError",
    "ServiceUnavailableError",
)


# Errors after which the provider may have processed (and billed) the input.
_MAY_BE_BILLED = ("APIConnectionError", "APITimeoutError", "InternalServerError")


def _input_cost(model: str, input_tokens: int) -> float:
    return input_tokens * pricing.price_for(model).for_prompt(input_tokens).input / 1e6


def _price_cost(model: str, usage: Any) -> float:
    cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
    return pricing.cost_usd(model, usage.input_tokens, cached, usage.output_tokens)


# ------------------------------------------------------------------- the Claude request


def build_claude_input(pdf: Path, prompt: str, pngs: Sequence[bytes]) -> list[dict[str, Any]]:
    """The same message for the Messages API: the PDF, a PNG of every page, the prompt last."""
    data = base64.b64encode(pdf.read_bytes()).decode()
    content: list[dict[str, Any]] = [
        {
            "type": "document",
            "title": pdf.name,
            "source": {"type": "base64", "media_type": "application/pdf", "data": data},
        }
    ]
    for png in pngs:
        source = {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(png).decode(),
        }
        content.append({"type": "image", "source": source})
    content.append({"type": "text", "text": prompt})
    return [{"role": "user", "content": content}]


@dataclass
class Reply:
    """One model reply, whichever provider wrote it."""

    text: str
    complete: bool  # the model finished; False: cut off, refused or incomplete
    served_model: str
    usage: dict[str, int]
    cost: float
    stop: str | None  # the provider's own word for why it stopped
    reason: str | None  # why it is incomplete, when the provider says


def _claude_text(message: Any) -> str:
    parts = []
    for block in getattr(message, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts)


def _claude_reply(message: Any, model: str) -> Reply:
    from readablestem.models.anthropic_client import _usage_and_cost

    usage, cost = _usage_and_cost(message, model)
    stop = getattr(message, "stop_reason", None)
    return Reply(
        text=_claude_text(message),
        complete=stop in ("end_turn", "stop_sequence"),
        served_model=getattr(message, "model", model) or model,
        usage={
            "input_tokens": usage.input_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
            "output_tokens": usage.output_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
        },
        cost=cost,
        stop=stop,
        reason=None if stop in ("end_turn", "stop_sequence") else stop,
    )


def _openai_reply(resp: Any, model: str) -> Reply:
    details = resp.incomplete_details
    usage = resp.usage
    return Reply(
        text=resp.output_text or "",
        complete=resp.status == "completed",
        served_model=resp.model,
        usage={
            "input_tokens": usage.input_tokens,
            "cached_input_tokens": getattr(usage.input_tokens_details, "cached_tokens", 0) or 0,
            "output_tokens": usage.output_tokens,
            "reasoning_tokens": getattr(usage.output_tokens_details, "reasoning_tokens", 0) or 0,
        },
        cost=_price_cost(model, usage),
        stop=resp.status,
        reason=getattr(details, "reason", None) if details else None,
    )


def _prepare(job: Job, pngs: Sequence[bytes], client: Any) -> tuple[Any, int, float]:
    """The provider's input, the exact count of its input tokens, and the worst-case cost
    (input plus `MAX_OUTPUT_TOKENS` at list price) that is held against the cap."""
    model = job.arm.model
    if job.arm.provider == "anthropic":
        inp = build_claude_input(job.pdf, job.arm.prompt_text, pngs)
        counted = client.messages.count_tokens(model=model, messages=inp).input_tokens
    else:
        inp = build_input(job.pdf, job.arm.prompt_text, pngs)
        counted = client.responses.input_tokens.count(model=model, input=inp).input_tokens
    price = pricing.price_for(model).for_prompt(counted)
    return inp, counted, (counted * price.input + MAX_OUTPUT_TOKENS * price.output) / 1e6


def _send(job: Job, client: Any, inp: Any) -> Reply:
    model = job.arm.model
    if job.arm.provider == "anthropic":
        # Streamed: a reply of this length is refused on a plain request. Nothing but the
        # model, the budget of tokens and the message is set, so the model's own defaults
        # (thinking effort included) apply.
        with client.messages.stream(
            model=model, max_tokens=MAX_OUTPUT_TOKENS, messages=inp
        ) as stream:
            return _claude_reply(stream.get_final_message(), model)
    resp = client.responses.create(
        model=model, input=inp, max_output_tokens=MAX_OUTPUT_TOKENS, store=False
    )
    return _openai_reply(resp, model)


def write_meta(
    job: Job,
    out_dir: Path,
    *,
    reply: Reply | None,
    error: str | None,
    attempts: list[str],
    cost: float,
    cost_estimated: bool,
    seconds: float,
    pdf_pages: int,
    counted: int,
    say: Callable[[str], None],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the record of one job (its JSON and, when the model wrote HTML, the HTML) and
    say what happened. `extra` is added to the record as it is (a batch run's price)."""
    arm_dir = out_dir / job.arm.id
    arm_dir.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {
        "arm": job.arm.id,
        "section": job.section,
        "status": "crashed",
        "seconds": round(seconds, 1),
        "pdf_pages": pdf_pages,
        "cost_usd": round(cost, 6),
        "versions": {"model_requested": job.arm.model},
        "settings": {
            "prompt": job.arm.prompt,
            "mode": "pages",
            "dpi": RENDER_DPI,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            **({} if job.arm.provider == "anthropic" else {"store": False}),
        },
        "attempts": attempts,
        "error": error,
        "counted_input_tokens": counted,
        "run": job.run,
    }
    if cost_estimated:
        meta["cost_estimated"] = True
    meta.update(extra or {})
    if reply is not None:
        text = reply.text
        html = html_from(text)
        if not text.strip():
            status = "empty"
        elif not reply.complete or CONTINUED in text:
            status = "truncated"
        else:
            status = "ok"
        meta.update(
            status=status,
            versions={"model_requested": job.arm.model, "model_served": reply.served_model},
            usage=reply.usage,
            response_status=reply.stop,
            incomplete_reason=reply.reason,
            output_chars=len(text),
        )
        if text.strip():
            (arm_dir / f"{job.stem}.html").write_text(html, encoding="utf-8")
            meta["output"] = {"file": f"{job.stem}.html", "format": "html"}
    (arm_dir / f"{job.stem}.json").write_text(dumps(meta), encoding="utf-8")
    say(
        f"{job.arm.id} {job.section} run{job.run}: {meta['status']} "
        f"${meta['cost_usd']:.4f} {meta['seconds']}s"
    )
    return meta


def run_job(
    client: Any,
    guard: InMemorySpendGuard,
    job: Job,
    out_dir: Path,
    *,
    say: Callable[[str], None],
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Run one job. Returns "done", "skipped" (already recorded) or "budget" (not run)."""
    arm_dir = out_dir / job.arm.id
    arm_dir.mkdir(parents=True, exist_ok=True)
    meta_path = arm_dir / f"{job.stem}.json"
    if meta_path.is_file():
        return "skipped"
    pngs = page_pngs(job.pdf)
    inp, counted, reserve = _prepare(job, pngs, client)
    while True:
        try:
            held = guard.reserve(reserve)
            break
        except BudgetExceeded:
            if not getattr(guard, "_held", None):
                return "budget"
            sleep(5)  # running calls settle their holds; then this one may fit
    attempts: list[str] = []
    reply: Reply | None = None
    error = None
    cost = 0.0
    cost_estimated = False
    started = time.monotonic()
    try:
        for attempt in range(RETRIES + 1):
            try:
                reply = _send(job, client, inp)
                break
            except Exception as exc:
                name = type(exc).__name__
                attempts.append(name)
                if name in _MAY_BE_BILLED and job.arm.provider == "anthropic":
                    # The request may have been processed: its input is charged, so the
                    # cap never under-counts. The output, if any, is not known.
                    cost += _input_cost(job.arm.model, counted)
                    cost_estimated = True
                if name not in _INFRASTRUCTURE or attempt == RETRIES:
                    error = name
                    break
                sleep(RETRY_WAIT_S[attempt])
    finally:
        latency = time.monotonic() - started
        if reply is not None:
            cost += reply.cost
        guard.settle(held, cost)
    write_meta(
        job,
        out_dir,
        reply=reply,
        error=error,
        attempts=attempts,
        cost=cost,
        cost_estimated=cost_estimated,
        seconds=latency,
        pdf_pages=len(pngs),
        counted=counted,
        say=say,
    )
    return "done"


def record_arms(
    pdfs: dict[str, Path],
    out_dir: Path,
    *,
    arms: Sequence[ChatArm] = ARMS,
    max_usd: float,
    client: Any,
    workers: int = 4,
    runs: int | None = None,
    say: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run every job of `arms` within one cap. A job the cap refuses is not run, and the
    summary lists it; a half-finished arm is the caller's to report as incomplete.
    `runs` makes fewer runs of each arm than the arm's own number (see `jobs_for`)."""
    if len({a.provider for a in arms}) > 1:
        raise ValueError("one client serves one provider: record the arms of each separately")
    guard = InMemorySpendGuard(cap_usd=max_usd)
    refused: list[str] = []
    lock = threading.Lock()

    def one(job: Job) -> None:
        if run_job(client, guard, job, out_dir, say=say, sleep=sleep) == "budget":
            with lock:
                refused.append(f"{job.arm.id}/{job.stem}")

    # Arms are run one after another so the primary arm is paid for first.
    for arm in arms:
        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(one, jobs_for([arm], pdfs, runs=runs)))
    return {
        "spent_usd": round(guard.spent_usd(), 6),
        "cap_usd": max_usd,
        "refused_by_cap": sorted(refused),
    }


def recorded_cost(out_dir: Path, arm_ids: Sequence[str]) -> float:
    """What the recorded jobs of these arms cost, from their files (jobs made by an earlier
    start that was resumed included, which one start's `spent_usd` is not)."""
    total = 0.0
    for arm_id in arm_ids:
        for path in (out_dir / arm_id).glob("*.json"):
            total += float(json.loads(path.read_text(encoding="utf-8")).get("cost_usd") or 0.0)
    return round(total, 6)


def make_client(provider: str = "openai") -> Any:
    """The real client of a provider (the key comes from the environment; it is never
    printed). Retries are made by `run_job`, each logged, so the SDK makes none."""
    if provider == "anthropic":
        import anthropic

        from readablestem.models.anthropic_client import build_sdk
        from readablestem.proof.runner import proof_settings

        sdk = build_sdk(proof_settings("anthropic"))
        return sdk.with_options(timeout=anthropic.Timeout(TIMEOUT_S, connect=30), max_retries=0)
    import openai

    return openai.OpenAI(timeout=TIMEOUT_S, max_retries=1)
