"""The arms: which systems the harness can run, how, and what each is not built for.

Ids follow PARSERS-PLAN.md (P1 Docling, P1f Docling with formulas, P2 MinerU, P3 PyMuPDF4LLM).
Arms that exist only on paper (Marker, Nougat, olmOCR, Mathpix) are in `NOT_RUN` with the
reason they are not run; there is deliberately no runner for them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parents[2]
ARMS_DIR = HARNESS_DIR / "arms"
RUNNERS_DIR = HARNESS_DIR / "runners"


def envs_root() -> Path:
    """Where the arms' virtual environments live (outside the repository)."""
    return Path(
        os.environ.get("PARSERS_ENVS", Path.home() / ".cache" / "a11y-proof" / "parsers-envs")
    )


def mineru_home() -> Path:
    """MinerU's model directory (about 820 MB for the `basic` tier)."""
    return Path(
        os.environ.get("PARSERS_MINERU_HOME", Path.home() / ".cache" / "a11y-proof" / "mineru-home")
    )


@dataclass(frozen=True)
class Arm:
    id: str
    plan_id: str
    name: str
    project: str  # directory under arms/ holding the pyproject.toml and uv.lock
    packages: tuple[str, ...]  # distributions whose versions are recorded with every run
    settings: dict[str, str] = field(default_factory=dict)
    licence: str = ""
    not_built_for: str = ""
    emits_latex: bool = True

    @property
    def env_dir(self) -> Path:
        return envs_root() / self.project

    @property
    def project_dir(self) -> Path:
        return ARMS_DIR / self.project


ARMS: dict[str, Arm] = {
    "docling": Arm(
        id="docling",
        plan_id="P1",
        name="Docling 2.138.0, CLI defaults",
        project="docling",
        packages=(
            "docling",
            "docling-core",
            "docling-ibm-models",
            "docling-parse",
            "torch",
            "transformers",
            "rapidocr",
        ),
        settings={
            "command": "docling --to json --to html --to md",
            "ocr": "on (RapidOCR)",
            "tables": "TableFormer, accurate",
            "image_export_mode": "embedded (the CLI default)",
            "formula_enrichment": "off (the default)",
        },
        licence="Docling MIT; layout model Apache-2.0; TableFormer CDLA-Permissive-2.0 and "
        "Apache-2.0; RapidOCR Apache-2.0",
        not_built_for="accessibility remediation: no alt text on pictures, every heading at one "
        "level in the HTML export, formulas kept as pictures (with the PDF text as alt) unless "
        "the opt-in enrichment is on",
        emits_latex=False,
    ),
    "docling-formula": Arm(
        id="docling-formula",
        plan_id="P1f",
        name="Docling 2.138.0 with --enrich-formula (non-default arm)",
        project="docling",
        packages=(
            "docling",
            "docling-core",
            "docling-ibm-models",
            "docling-parse",
            "torch",
            "transformers",
            "rapidocr",
        ),
        settings={
            "command": "docling --enrich-formula --to json --to html --to md",
            "ocr": "on (RapidOCR)",
            "tables": "TableFormer, accurate",
            "image_export_mode": "embedded (the CLI default)",
            "formula_enrichment": "on (CodeFormulaV2): the one non-default switch",
        },
        licence="as Docling, plus CodeFormulaV2 under CDLA-Permissive-2.0",
        not_built_for="speed on CPU: about 136 s per page measured on a loaded box, which puts "
        "most sections past the 30-minute failure rule",
    ),
    "mineru-basic": Arm(
        id="mineru-basic",
        plan_id="P2",
        name="MinerU 4.0.12, basic tier (CPU, ONNX)",
        project="mineru",
        packages=("mineru", "docvortex", "onnxruntime", "numpy"),
        settings={
            "command": "mineru-kit parse --tier basic --format zip",
            "tier": "basic (MinerU's own recommendation for a machine without an accelerator)",
            "ocr_mode": "auto (the default)",
        },
        licence="MinerU Open Source License (Apache-2.0 plus extra terms: credit MinerU; a "
        "commercial licence above 100M MAU or $20M monthly revenue; state it when offering an "
        "online service based on MinerU). Models: PaddlePaddle ONNX exports, Apache-2.0 upstream",
        not_built_for="accessible HTML: it writes Markdown and JSON for LLM corpora and RAG; "
        "empty alt text on images, one heading level in Markdown. Higher tiers want a GPU or "
        "a llama.cpp VLM",
    ),
    "pymupdf4llm": Arm(
        id="pymupdf4llm",
        plan_id="P3",
        name="PyMuPDF4LLM 1.28.2 with the layout model",
        project="pymupdf4llm",
        packages=("pymupdf4llm", "pymupdf", "pymupdf-layout", "onnxruntime", "numpy"),
        settings={
            "call": "pymupdf4llm.to_markdown(path)",
            "layout_model": "on (pymupdf-layout installed)",
            "write_images": "off (the default)",
        },
        licence="AGPL-3.0 or Artifex commercial (ADR 0003 already accepts these terms for PyMuPDF)",
        not_built_for="mathematics: no formula recognition and no LaTeX; fast text-layer to "
        "Markdown for LLM and RAG ingestion; images off by default",
        emits_latex=False,
    ),
}

# Considered in the plan and not run. There is no runner for these on purpose.
NOT_RUN: dict[str, dict[str, str]] = {
    "marker": {
        "plan_id": "P4",
        "version": "marker-pdf 2.0.0",
        "reason": "waits for Harish's decision (PARSERS-PLAN.md section 6.1). The weights' licence "
        "counts evaluating as use, and restriction 2(c) bars any use by someone offering a "
        "competing service, with no research exception. Not installed, not run.",
    },
    "nougat": {
        "plan_id": "P5",
        "version": "nougat-ocr 0.1.17",
        "reason": "too slow on CPU (first page of three took 19 minutes); unmaintained since 2023; "
        "trained on arXiv papers, not textbooks.",
    },
    "olmocr": {
        "plan_id": "P6",
        "version": "olmocr 0.4.27",
        "reason": "needs an NVIDIA GPU with at least 12 GB; the box's driver is broken and it has "
        "no GPU to use.",
    },
    "mathpix": {
        "plan_id": "P7",
        "version": "paid API",
        "reason": "no API key on the box; its terms on publishing benchmarks have not been read.",
    },
}


def get(arm_id: str) -> Arm:
    try:
        return ARMS[arm_id]
    except KeyError:
        known = ", ".join(sorted(ARMS))
        if arm_id in NOT_RUN:
            raise SystemExit(f"arm {arm_id!r} is not run: {NOT_RUN[arm_id]['reason']}") from None
        raise SystemExit(f"unknown arm {arm_id!r}; known: {known}") from None
