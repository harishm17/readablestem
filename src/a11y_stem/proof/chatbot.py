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

from a11y_stem.core.budget import BudgetExceeded, InMemorySpendGuard
from a11y_stem.models import pricing
from a11y_stem.proof.bundle import dumps, pdf_name

MAX_OUTPUT_TOKENS = 64_000
RENDER_DPI = 150
RETRIES = 2  # infrastructure errors only
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
ARM_BY_ID = {a.id: a for a in ARMS}


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


def jobs_for(arms: Sequence[ChatArm], pdfs: dict[str, Path]) -> list[Job]:
    jobs = []
    for arm in arms:
        for run in range(1, arm.runs + 1):
            for section, path in pdfs.items():
                jobs.append(Job(arm, section, run, path))
    return jobs


_INFRASTRUCTURE = (
    "APIConnectionError",
    "APITimeoutError",
    "InternalServerError",
    "RateLimitError",
    "ServiceUnavailableError",
)


def _price_cost(model: str, usage: Any) -> float:
    cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
    return pricing.cost_usd(model, usage.input_tokens, cached, usage.output_tokens)


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
    inp = build_input(job.pdf, job.arm.prompt_text, pngs)
    counted = client.responses.input_tokens.count(model=job.arm.model, input=inp).input_tokens
    price = pricing.price_for(job.arm.model)
    reserve = (counted * price.input + MAX_OUTPUT_TOKENS * price.output) / 1e6
    while True:
        try:
            held = guard.reserve(reserve)
            break
        except BudgetExceeded:
            if not getattr(guard, "_held", None):
                return "budget"
            sleep(5)  # running calls settle their holds; then this one may fit
    attempts: list[str] = []
    resp = None
    error = None
    started = time.monotonic()
    try:
        for attempt in range(RETRIES + 1):
            try:
                resp = client.responses.create(
                    model=job.arm.model,
                    input=inp,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    store=False,
                )
                break
            except Exception as exc:
                name = type(exc).__name__
                attempts.append(name)
                if name not in _INFRASTRUCTURE or attempt == RETRIES:
                    error = name
                    break
                sleep(10 * (attempt + 1))
    finally:
        latency = time.monotonic() - started
        cost = _price_cost(job.arm.model, resp.usage) if resp is not None else 0.0
        guard.settle(held, cost)
    meta: dict[str, Any] = {
        "arm": job.arm.id,
        "section": job.section,
        "status": "crashed",
        "seconds": round(latency, 1),
        "pdf_pages": len(pngs),
        "cost_usd": round(cost, 6),
        "versions": {"model_requested": job.arm.model},
        "settings": {
            "prompt": job.arm.prompt,
            "mode": "pages",
            "dpi": RENDER_DPI,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        },
        "attempts": attempts,
        "error": error,
        "counted_input_tokens": counted,
        "run": job.run,
    }
    if resp is not None:
        text = resp.output_text or ""
        html = html_from(text)
        incomplete = resp.status != "completed"
        if not text.strip():
            status = "empty"
        elif incomplete or CONTINUED in text:
            status = "truncated"
        else:
            status = "ok"
        details = resp.incomplete_details
        usage = resp.usage
        meta.update(
            status=status,
            versions={"model_requested": job.arm.model, "model_served": resp.model},
            usage={
                "input_tokens": usage.input_tokens,
                "cached_input_tokens": getattr(usage.input_tokens_details, "cached_tokens", 0) or 0,
                "output_tokens": usage.output_tokens,
                "reasoning_tokens": getattr(usage.output_tokens_details, "reasoning_tokens", 0)
                or 0,
            },
            response_status=resp.status,
            incomplete_reason=getattr(details, "reason", None) if details else None,
            output_chars=len(text),
        )
        if text.strip():
            (arm_dir / f"{job.stem}.html").write_text(html, encoding="utf-8")
            meta["output"] = {"file": f"{job.stem}.html", "format": "html"}
    meta_path.write_text(dumps(meta), encoding="utf-8")
    say(
        f"{job.arm.id} {job.section} run{job.run}: {meta['status']} "
        f"${meta['cost_usd']:.4f} {meta['seconds']}s"
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
    say: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run every job of `arms` within one cap. A job the cap refuses is not run, and the
    summary lists it; a half-finished arm is the caller's to report as incomplete."""
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
            list(pool.map(one, jobs_for([arm], pdfs)))
    return {
        "spent_usd": round(guard.spent_usd(), 6),
        "cap_usd": max_usd,
        "refused_by_cap": sorted(refused),
    }


def make_client() -> Any:
    """The real OpenAI client (the key comes from the environment; it is never printed)."""
    import openai

    return openai.OpenAI(timeout=TIMEOUT_S, max_retries=1)
