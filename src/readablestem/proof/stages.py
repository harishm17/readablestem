"""Stages of a proof: the parts that turn a recorded run into tables.

A stage has two hooks:

- `record(ctx, writer)`: live mode only. Produces what the stage needs and writes it into
  the bundle (for example the outputs of other systems under `systems/`).
- `run(ctx)`: both modes. Reads the bundle and the gold set, returns `Section`s for
  PROOF.md and a status.

The a11y-stem arm is built in (`A11yStage`). Four other parts are made by other work
packages and are stubs here: the invented-content rate, the comparison with the chatbot
recipe, the comparison with the open parsers, and the human labels. A stub returns the
status `not_wired` and a section that says so; it never prints a number. To wire one,
drop a module of the name below into `readablestem/proof/` that calls `register(...)` with a
stage of the same `name`; `load_plugins` imports it, and its stage replaces the stub in
the same place in the report.

| stage | module `load_plugins` imports | what it must produce (preregistration) |
|---|---|---|
| `invented_content` | `readablestem.proof.invented` | section 7.5, all systems |
| `chatbot_comparison` | `readablestem.proof.comparison` | family A (section 8.3), arms C1, C2 |
| `parser_comparison` | `readablestem.proof.parsers` | family B, parser plan section 4 |
| `human_labels` | `readablestem.proof.labels` | sections 7.8 and 7.3, judge agreement |
"""

from __future__ import annotations

import importlib
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from readablestem.config import Settings
from readablestem.proof import a11y_tables
from readablestem.proof.bundle import Bundle, BundleWriter, pdf_name
from readablestem.proof.errors import ProofError
from readablestem.proof.goldinfo import SectionFacts
from readablestem.proof.registry import ProofSet
from readablestem.proof.report import STATUS_WORDS, Section, StageResult, Table, bullets
from readablestem.proof.runner import ArmResults

PARSERS_DIR = Path("proof") / "parsers"
PARSERS_TIMEOUT_S = 6 * 3600


@dataclass
class StageContext:
    pset: ProofSet
    mode: str  # "replay" | "live"
    root: Path
    bundle: Bundle
    gold_dir: Path
    records: list[dict]
    facts: list[SectionFacts]
    arm: ArmResults
    g1: frozenset[str]
    settings: Settings
    work_dir: Path
    n_boot: int
    pdf_paths: dict[str, Path] = field(default_factory=dict)  # section id -> the PDF
    say: Callable[[str], None] = lambda _msg: None
    # Work shared between stages in one run (the other systems are scored once).
    cache: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecordContext:
    """What a stage's `record` hook gets in a live run, before the bundle exists."""

    pset: ProofSet
    root: Path
    gold_dir: Path
    records: list[dict]
    arm: ArmResults
    settings: Settings
    work_dir: Path
    pdf_paths: dict[str, Path]  # section id -> the PDF every system is given
    say: Callable[[str], None] = lambda _msg: None


class Stage(Protocol):
    name: str
    title: str

    def record(self, ctx: RecordContext, writer: BundleWriter) -> None: ...

    def run(self, ctx: StageContext) -> StageResult: ...


STAGES: dict[str, Stage] = {}


def register(stage: Stage) -> None:
    """Add or replace the stage called `stage.name`; a replacement keeps its place."""
    STAGES[stage.name] = stage


# ------------------------------------------------------------------------ a11y-stem


def _settings_text(settings: Settings) -> str:
    """The settings of the run in words: OpenAI's defaults, or the Claude pair."""
    if settings.model_provider == "anthropic":
        fallback = "on" if settings.anthropic_refusal_fallback else "off"
        return (
            f"Production settings with the Anthropic provider (main model {settings.main_model} "
            f"at effort {settings.anthropic_effort_main}, cheap model {settings.cheap_model} "
            f"at effort {settings.anthropic_effort_cheap}, the main model's server-side "
            f"refusal fallback {fallback}, every content flag off, no second automated check: "
            "it is OpenAI's)."
        )
    return (
        "Production settings (`Settings()` defaults: main model gpt-6.1-sol, cheap "
        "model gpt-6-luna, every content flag off, the second automated check on)."
    )


class A11yStage:
    name = "a11y_stem"
    title = "a11y-stem, production defaults"

    def record(self, ctx: RecordContext, writer: BundleWriter) -> None:
        return None  # recorded by the runner

    def run(self, ctx: StageContext) -> StageResult:
        arm, n_boot, g1 = ctx.arm, ctx.n_boot, ctx.g1
        blocks: list[Section] = []
        if arm.runs("document"):
            doc = a11y_tables.document_section(arm, g1, n_boot)
            doc.blocks.append(
                a11y_tables.section_table(arm, ctx.facts, g1, "Per section (runs pooled)")
            )
            doc.blocks.append(a11y_tables.book_table(arm, ctx.facts, g1))
            blocks.append(doc)
        if arm.runs("equations"):
            blocks.append(a11y_tables.equations_section(arm, g1, n_boot))
        if arm.runs("headings"):
            blocks.append(a11y_tables.headings_section(arm, n_boot))
        if arm.runs("figures"):
            blocks.append(a11y_tables.figures_section(arm, n_boot, ctx.settings.main_model))
        blocks.append(a11y_tables.cost_section(arm, ctx.bundle.runs, ctx.settings))
        blocks.append(a11y_tables.failures_section(arm))
        intro = Section(
            "Results: a11y-stem",
            [
                _settings_text(ctx.settings) + " "
                "Tables are off in production, so a detected data table is kept as an image "
                "with a required decision. Intervals: cluster bootstrap by section "
                f"(B = {ctx.n_boot:,}, seed 0), which is approximate with this few sections "
                "and tends to be a little narrow; the Wilson interval is shown beside it for "
                "comparison and treats items as independent."
            ],
        )
        return StageResult("ok", [intro, *blocks])


# ---------------------------------------------------------------------------- stubs


class StubStage:
    """A part another work package makes. It prints no number."""

    def __init__(
        self,
        name: str,
        title: str,
        module: str,
        will_show: list[str],
        reads: list[str],
    ) -> None:
        self.name = name
        self.title = title
        self._module = module
        self._will_show = will_show
        self._reads = reads

    def record(self, ctx: RecordContext, writer: BundleWriter) -> None:
        ctx.say(f"{self.name}: not wired, nothing recorded")

    def run(self, ctx: StageContext) -> StageResult:
        section = Section(
            self.title,
            [
                f"**Not computed in this build.** The stage `{self.name}` is a placeholder; "
                f"its module `{self._module}` is not installed, so this report shows no "
                "number for it.",
                "It will show:\n\n" + bullets(self._will_show),
                "It will read:\n\n" + bullets(self._reads),
            ],
        )
        return StageResult("not_wired", [section], [f"{self.title}: not computed (stub)"])


def _stubs() -> list[StubStage]:
    return [
        StubStage(
            "invented_content",
            "Invented-content rate",
            "readablestem.proof.invented",
            [
                "per system and per type (prose sentence, number, heading, equation, "
                "figure description, table cell): the share of output units the source PDF "
                "does not support, with cluster intervals;",
                "the rule's audited precision and recall, from a blind sample labelled by "
                "the author, next to the raw rate.",
            ],
            [
                "the PDF text layer of every section (`media/pdfs/`);",
                "the HTML or intermediate output of every system (`systems/`).",
            ],
        ),
        StubStage(
            "chatbot_comparison",
            "Comparison with the chatbot recipe",
            "readablestem.proof.comparison",
            [
                "family A of the preregistration: a11y-stem against the Purdue-prompt "
                "recipe on gpt-6.1-sol on end-to-end equation exact match, unflagged wrong "
                "equations, invented prose sentences, invented numbers and heading F1, with "
                "paired cluster intervals at the Bonferroni level;",
                "the expert-prompt arm and the gpt-6-luna arm as descriptive rows.",
            ],
            ["`systems/chatbot-*/` in the intermediate form of `proof.intermediate`."],
        ),
        StubStage(
            "parser_comparison",
            "Comparison with open PDF parsers",
            "readablestem.proof.parsers",
            [
                "family B: a11y-stem against Docling, MinerU and PyMuPDF4LLM on shared "
                "measures only (heading F1, equations for the parsers that emit LaTeX), "
                "with what each tool is not built to do stated next to it;",
                "equation content kept in any form, reading order, images kept, tables, "
                "time and memory, as descriptive rows.",
            ],
            ["`systems/<parser>/` in the intermediate form of `proof.intermediate`."],
        ),
        StubStage(
            "human_labels",
            "Human labels and judge agreement",
            "readablestem.proof.labels",
            [
                "the share of wrong equations that change the meaning, labelled blind by "
                "the author;",
                "Cohen's kappa between the figure judge and the author's labels, with a "
                "bootstrap interval, and the audited precision and recall of the "
                "invented-content rule.",
            ],
            ["label files the author writes after the run (none exist yet)."],
        ),
    ]


class ParserHook:
    """Wraps a stage so that live mode also runs `proof/parsers` when it is installed.

    Contract with whoever installs the parsers: `proof/parsers/Makefile` has a `proof`
    target that takes `PDF_DIR` (the section PDFs, named `<book>__<page>.pdf`) and
    `OUT_DIR`, and writes `OUT_DIR/<arm>/<book>__<page>[.runN].json` in the intermediate
    form. Everything under `OUT_DIR` is copied into the bundle's `systems/`.
    """

    def __init__(self, inner: Stage) -> None:
        self._inner = inner
        self.name = inner.name
        self.title = inner.title

    def record(self, ctx: RecordContext, writer: BundleWriter) -> None:
        folder = ctx.root / PARSERS_DIR
        if not (folder / "Makefile").is_file():
            ctx.say("parsers: proof/parsers is not installed; no parser output recorded")
            return
        pdf_dir = ctx.work_dir / "parser-pdfs"
        out_dir = ctx.work_dir / "parser-out"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        for section_id, path in ctx.pdf_paths.items():
            (pdf_dir / pdf_name(section_id)).write_bytes(path.read_bytes())
        ctx.say("parsers: running proof/parsers (this can take hours)")
        command = ["make", "-C", str(folder), "proof", f"PDF_DIR={pdf_dir}", f"OUT_DIR={out_dir}"]
        try:
            done = subprocess.run(command, timeout=PARSERS_TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProofError(f"proof/parsers did not run to the end: {type(exc).__name__}") from exc
        if done.returncode != 0:
            raise ProofError(f"proof/parsers failed (make exit code {done.returncode})")
        writer.copy_tree(out_dir, "systems")

    def run(self, ctx: StageContext) -> StageResult:
        return self._inner.run(ctx)


# ------------------------------------------------------------------------- registry

PLUGIN_MODULES = (
    "readablestem.proof.invented",
    "readablestem.proof.comparison",
    "readablestem.proof.parsers",
    "readablestem.proof.labels",
)


def default_stages() -> dict[str, Stage]:
    stages: dict[str, Stage] = {"a11y_stem": A11yStage()}
    for stub in _stubs():
        stages[stub.name] = ParserHook(stub) if stub.name == "parser_comparison" else stub
    return stages


def load_plugins() -> list[str]:
    """Import the optional stage modules that exist; returns the ones that did.

    A module that is absent is skipped. A module that exists but fails to import is a
    bug in that module and raises.
    """
    loaded = []
    for name in PLUGIN_MODULES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                raise
        else:
            loaded.append(name)
    return loaded


def active_stages() -> list[Stage]:
    """The default stages with any registered replacements, in the report's order."""
    merged = {**default_stages(), **STAGES}
    return list(merged.values())


def stage_table(results: dict[str, StageResult], stages: list[Stage]) -> Table:
    rows = [
        [
            s.title,
            STATUS_WORDS.get(results[s.name].status, "?") if s.name in results else "not run",
        ]
        for s in stages
    ]
    return Table("Parts of this proof", ["Part", "Status"], rows)
