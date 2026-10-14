"""Do the spend limits fit the model provider? One place that answers it.

Every model call reserves its worst-case cost before it is sent (`core.budget`), and the
reservations count against the job's and the day's caps like spend does. The worst case is
not the same on every provider: a call on claude-opus-5-5 holds about $0.30 (a thinking
allowance on top of the answer, and a large image), against about $0.03 on gpt-6.1-sol.
Caps sized for one provider therefore break the other: with OpenAI's $0.30 per page, a
one-page document on Claude could not hold two calls at once and its elements would come
back "not converted" although the real cost was a cent.

`spend_findings(settings)` compares the configured limits with the worst-case reservation
of the models in use and with the measured cost of a page (`PLANNING_USD_PER_PAGE`):

- an *error* is a setting under which a document cannot be converted as designed; the
  web app refuses to start with one in production (`Settings.check_production`);
- a *warning* is a setting that works but is probably not what was meant (a normal
  document close to a cap, a runaway job allowed far past a normal one); `a11y-stem ops
  launch-check` lists them.

Measurements behind the numbers: docs/deploy.md, "Spend limits".
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a11y_stem.config import Settings

# What a dense page costs, rounded up from the measurements (docs/deploy.md): on Claude
# (claude-opus-5-5 main, claude-haiku-5-5 cheap, effort medium and low) a development
# page costs $0.0030-0.0047 and a dense held-out page about $0.014; on OpenAI $0.0037.
PLANNING_USD_PER_PAGE = 0.015

# A job cap this many times a full-size normal document is no longer "stops a runaway".
RUNAWAY_FACTOR = 25


@dataclass(frozen=True)
class Finding:
    severity: str  # "error" or "warning"
    text: str

    def __str__(self) -> str:
        return f"{self.severity}: {self.text}"


def largest_reservation_usd(settings: Settings, *, optional_steps: bool = True) -> float:
    """Worst-case cost of the dearest single call the enabled steps can make: equations
    and figures always; long descriptions and tables when their flags are on and
    `optional_steps` is True. The images are priced at their largest, a 2048 px crop (what
    the pipeline's crops are cut to), and the neighbouring text at its largest."""
    from a11y_stem.models.base import fence_untrusted
    from a11y_stem.models.pricing import UnknownModelError, estimate_max_cost_usd
    from a11y_stem.pipeline import equation_step, figure_step, run, table_step

    context = fence_untrusted("x" * (run.CONTEXT_CHARS + 64))
    largest = 0.0
    for model in (settings.main_model, settings.cheap_model):
        try:
            limits = [equation_step.output_limit(model), figure_step.MAX_OUTPUT_TOKENS]
            if optional_steps and settings.feature_long_descriptions:
                limits.append(figure_step.LONG_MAX_OUTPUT_TOKENS)
            if optional_steps and settings.feature_tables:
                limits.append(table_step.MAX_OUTPUT_TOKENS)
            for tokens in limits:
                cost = estimate_max_cost_usd(
                    model, n_images=1, text_chars=len(context), max_output_tokens=tokens
                )
                largest = max(largest, cost)
        except UnknownModelError:  # refused at the first call anyway
            continue
    # The Claude second check (T2-20) holds its own reservation, after the step's and never
    # beside it, on a model of its own: it counts when it is the dearest.
    if settings.model_provider == "anthropic" and settings.claude_second_check != "off":
        from a11y_stem.models import claude_verifier

        text_chars = claude_verifier.CAPTION_LIMIT + claude_verifier.ALT_LIMIT + 400
        with contextlib.suppress(UnknownModelError):  # refused at the first call anyway
            largest = max(
                largest,
                estimate_max_cost_usd(
                    settings.claude_check_model,
                    n_images=1,
                    text_chars=text_chars,
                    max_output_tokens=claude_verifier.MAX_OUTPUT_TOKENS,
                ),
            )
    return largest


def in_flight_usd(settings: Settings) -> float:
    """Most that can be held at once by one conversion: every concurrent step
    (`Settings.model_concurrency`) at the dearest reservation."""
    return settings.model_concurrency * largest_reservation_usd(settings)


def affordable_concurrency(
    settings: Settings,
    *,
    budget_usd: float | None,
    daily_left_usd: float | None = None,
    largest_usd: float | None = None,
) -> int:
    """How many steps of one conversion may run at once: `Settings.model_concurrency`, or
    fewer when the money that is left could not hold that many worst-case reservations.

    Every step holds its reservation while its request is out, and a refused reservation
    stops all further model work of the job (`pipeline.run`: a budget stop). A one-page
    job on Claude has $1.00 and a call holds up to $0.14, so eight at once would be
    refused on the way to a job that costs a few cents; seven fit. `budget_usd` is what the
    job may still hold or spend (its cap minus what earlier attempts spent), `daily_left_usd`
    what is left under the day's cap (None: no cap). At least 1, at most the setting.
    """
    largest = largest_reservation_usd(settings) if largest_usd is None else largest_usd
    wanted = settings.model_concurrency
    if largest <= 0:
        return wanted
    room = [x for x in (budget_usd, daily_left_usd) if x is not None]
    if not room:
        return wanted
    return max(1, min(wanted, int(min(room) // largest)))


def spend_findings(settings: Settings) -> list[Finding]:
    """Errors and warnings about the spend limits for the provider in use."""
    out: list[Finding] = []
    largest = largest_reservation_usd(settings)
    steps = settings.model_concurrency
    in_flight = steps * largest
    who = f"MODEL_PROVIDER={settings.model_provider} (worst case ${largest:.2f} per call)"

    def error(text: str) -> None:
        out.append(Finding("error", f"{text}; {who}"))

    def warning(text: str) -> None:
        out.append(Finding("warning", f"{text}; {who}"))

    normal_job = settings.max_pages_per_job * PLANNING_USD_PER_PAGE
    # What a full-size job is really allowed (`web.worker.job_caps`): the job cap, lowered by
    # the page cap times the page count.
    full_job_cap = min(
        settings.max_usd_per_job, settings.max_pages_per_job * settings.max_usd_per_page
    )
    if settings.max_usd_per_page < largest:
        error(
            f"MAX_USD_PER_PAGE ${settings.max_usd_per_page:.2f} is below one call's "
            f"reservation, so no page could be converted"
        )
    # A page cap below `steps` calls held at once is no finding: a job whose cap could not
    # hold that many runs fewer steps at once (`affordable_concurrency`, used by the worker
    # and the convert command), so a short document is converted either way.
    if settings.max_usd_per_job < largest:
        error(f"MAX_USD_PER_JOB ${settings.max_usd_per_job:.2f} is below one call's reservation")
    elif full_job_cap < normal_job + in_flight:
        warning(
            f"MAX_USD_PER_JOB ${settings.max_usd_per_job:.2f} is below what a "
            f"{settings.max_pages_per_job}-page dense document needs "
            f"(${normal_job:.2f} spent plus ${in_flight:.2f} held at once)"
        )
    elif full_job_cap > RUNAWAY_FACTOR * normal_job:
        warning(
            f"MAX_USD_PER_JOB ${settings.max_usd_per_job:.2f} is more than {RUNAWAY_FACTOR}x "
            f"a {settings.max_pages_per_job}-page dense document (${normal_job:.2f}): a "
            f"runaway job would run on far past a normal one"
        )
    daily = settings.daily_spend_cap_usd
    if daily is not None:
        if daily < in_flight:
            error(
                f"DAILY_SPEND_CAP_USD ${daily:.2f} is below {steps} calls held "
                f"at once (${in_flight:.2f}), so a document could not be converted"
            )
        elif daily < settings.max_usd_per_job:
            warning(
                f"DAILY_SPEND_CAP_USD ${daily:.2f} is below one job's cap "
                f"(${settings.max_usd_per_job:.2f})"
            )
        quota = settings.per_user_daily_page_quota
        if quota is not None and daily < quota * PLANNING_USD_PER_PAGE + in_flight:
            warning(
                f"DAILY_SPEND_CAP_USD ${daily:.2f} is below what one user at the page quota "
                f"needs ({quota} pages x ${PLANNING_USD_PER_PAGE} plus ${in_flight:.2f} held)"
            )
    quota = settings.per_user_daily_page_quota
    if quota is not None and quota < settings.max_pages_per_job:
        warning(
            f"PER_USER_DAILY_PAGE_QUOTA {quota} is below MAX_PAGES_PER_JOB "
            f"{settings.max_pages_per_job}: a full-size document is refused"
        )
    return out


def main() -> int:
    """`python -m a11y_stem.ops.limits`: check the spend limits in the process environment
    (deploy/deploy.sh runs it before it deploys anything). Exit 3 when an error was found,
    0 otherwise; warnings are printed. Reads no `.env` file."""
    from a11y_stem.config import Settings

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    findings = spend_findings(settings)
    for finding in findings:
        print(finding)
    print(
        f"provider={settings.model_provider} errors={sum(f.severity == 'error' for f in findings)} "
        f"warnings={sum(f.severity == 'warning' for f in findings)}"
    )
    return 3 if any(f.severity == "error" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
