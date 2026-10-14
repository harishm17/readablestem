"""Figure descriptions of other systems, judged when the system's output is recorded.

Preregistration section 7.3: for every gold figure with an OpenStax alt text, the text the
system wrote for it is found by rule (`eval.invented.figures.bind`: the description's text
or caption holds the figure's number, or at least half of the gold caption's word
4-grams), and the figure judge (`eval.judge`, prompt `judge_figure`, gpt-6.1-sol, no
fallback) sees the candidate, the OpenStax alt, the caption and the image, never which
system wrote the candidate. No description found: no judge call, and a fail.

The verdicts are written into the system's JSON in the bundle (`figure_judgements`), so a
replay needs no model. Judge cost is recorded apart (`judge_cost_usd`) and is never part of
a system's cost.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from readablestem.config import Settings
from readablestem.eval import judge as judge_mod
from readablestem.eval.invented import figures as inv_figures
from readablestem.proof.bundle import dumps
from readablestem.proof.errors import ProofError
from readablestem.proof.systems import SectionGold, SystemRun, figure_references

JUDGED_ARMS_FILE = "judge.json"


def judge_run(
    run: SystemRun,
    gold: SectionGold,
    *,
    client: Any,
    settings: Settings,
    image_for: Callable[[str], bytes],
) -> dict[str, Any]:
    """The `figure_judgements` and `judge_cost_usd` entries for one output."""
    refs = [r for r in figure_references(gold, image_for) if r.alt]
    output = run.read()
    descriptions = output.descriptions if output else []
    bindings = inv_figures.bind(descriptions, refs) if refs else []
    best: dict[str, inv_figures.Binding] = {}
    for binding in bindings:
        if binding.reference is None:
            continue
        current = best.get(binding.reference)
        if current is None or binding.share > current.share:
            best[binding.reference] = binding
    rows: list[dict[str, Any]] = []
    cost = 0.0
    for ref in refs:
        binding = best.get(ref.id)
        if binding is None:
            rows.append({"figure_id": ref.id, "outcome": "no_description"})
            continue
        description = descriptions[binding.description]
        png = ref.image
        if png is not None and not png.startswith(inv_figures.PNG_SIGNATURE):
            png = inv_figures.to_png(png)
        judgement = judge_mod.judge_alt(
            description.text,
            ref.alt,
            description.caption or ref.caption,
            client=client,
            settings=settings,
            image=png,
        )
        verdict = judgement.verdict
        cost += judgement.call.cost_usd
        rows.append(
            {
                "figure_id": ref.id,
                "outcome": "judged" if verdict is not None else "judge_failed",
                "passed": None if verdict is None else verdict.passed,
                "factual_error": None if verdict is None else verdict.factual_error,
                "conveys": None if verdict is None else verdict.conveys_key_information,
                "score": None if verdict is None else verdict.score,
                "source": description.source,
                "bound_by": binding.by,
                "cost_usd": judgement.call.cost_usd,
                "prompt_version": judgement.call.prompt_version,
            }
        )
    return {"figure_judgements": rows, "judge_cost_usd": round(cost, 6)}


def judge_arms(
    arms: dict[str, dict[str, list[SystemRun]]],
    golds: dict[str, SectionGold],
    *,
    client: Any,
    settings: Settings,
    image_for: Callable[[str], bytes],
    say: Callable[[str], None] = print,
    write: bool = True,
) -> float:
    """Judge every ok output that has not been judged; rewrite its JSON. Returns the
    judge cost. `BudgetExceeded` from the client propagates (nothing is half-written: a
    JSON is rewritten only when all its figures were judged). `write` False judges
    and writes nothing (a pass of a staged run that is only collecting requests)."""
    total = 0.0
    for arm, sections in arms.items():
        for section, runs in sections.items():
            for run in runs:
                if not run.ok or "figure_judgements" in run.meta:
                    continue
                path = _meta_path(run)
                done = judge_run(
                    run, golds[section], client=client, settings=settings, image_for=image_for
                )
                total += done["judge_cost_usd"]
                if not write:
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                data.update(done)
                path.write_text(dumps(data), encoding="utf-8")
                say(
                    f"judged {arm} {section} run{run.run}: {len(done['figure_judgements'])} figures"
                )
    return total


def judge_arms_batched(
    arms: dict[str, dict[str, list[SystemRun]]],
    golds: dict[str, SectionGold],
    *,
    client: Any,
    settings: Settings,
    budget: Any,
    answers: Path,
    image_for: Callable[[str], bytes],
    say: Callable[[str], None] = print,
) -> float:
    """`judge_arms` with the judge's requests sent as Message Batches (Claude judge only).

    The same requests, answered when the batches end: passes that only collect what the
    answers file cannot yet answer (`eval.staged`), then one pass that writes the verdicts.
    `answers` keeps the judge's answers, so an interrupted step does not pay for them again."""
    from readablestem.eval import staged
    from readablestem.eval.answers import CachedClient
    from readablestem.models.anthropic_batch import BatchRunner, PendingPool, StagingClient

    pool = PendingPool()
    staging = StagingClient(client, pool)
    cached = CachedClient(staging, answers, settings)
    staging.owner = cached
    runner = BatchRunner(budget, say=say)

    def collect() -> float:
        return judge_arms(
            arms, golds, client=cached, settings=settings, image_for=image_for, say=say, write=False
        )

    staged.drive(collect, pool, runner, say=say)
    return judge_arms(arms, golds, client=cached, settings=settings, image_for=image_for, say=say)


def _meta_path(run: SystemRun) -> Path:
    if run.output_path is None:
        raise ProofError("an output with no file cannot be judged")
    return run.output_path.with_suffix(".json")
