"""From a run directory to a score, with hand-made native output (no parser installed)."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from a11y_stem.pipeline.validate import safe_latex_to_mathml

from parsers_bench import intermediate, invented, score
from parsers_bench.adapters import adapt_run
from parsers_bench.arms import ARMS
from parsers_bench.devgold import GoldSubset
from parsers_bench.intermediate import Block, Equation, Figure, Heading, Intermediate


def write_run(
    tmp_path: Path, arm_id: str, native_name: str, native_content: str, status: str = "ok"
) -> Path:
    run_dir = tmp_path / arm_id / "run1"
    (run_dir / "native").mkdir(parents=True)
    if native_content:
        (run_dir / "native" / native_name).write_text(native_content, encoding="utf-8")
    record = {
        "arm": arm_id,
        "status": status,
        "native_output": native_name,
        "wall_seconds": 2.0,
        "wall_seconds_per_page": 1.0,
        "versions": {"tool": "1.0"},
        "settings": {"k": "v"},
        "input": {"pages": 2},
        "measurement": {
            "user_seconds": 1.5,
            "max_rss_kb": 204800,
            "load_before": 0.5,
            "load_after": 0.6,
        },
        "host": {"cpu_model": "x", "logical_cpus": 4, "mem_total_kb": 1},
        "api_cost_usd": 0.0,
        "api_calls": 0,
    }
    (run_dir / "run.json").write_text(json.dumps(record), encoding="utf-8")
    return run_dir


def test_adapt_run_reads_the_native_file_and_records_the_measurement(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path, "pymupdf4llm", "page.md", "# Heading\n\nSome words in a line.\n")
    doc = adapt_run(ARMS["pymupdf4llm"], run_dir, section="sec")
    assert doc.status == "ok"
    assert doc.seconds == 2.0
    assert doc.versions == {"tool": "1.0"}
    assert [h.text for h in doc.headings] == ["Heading"]
    assert intermediate.validate(doc) == []


def test_a_crashed_run_is_a_section_with_nothing_found(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path, "pymupdf4llm", "page.md", "", status="crashed")
    doc = adapt_run(ARMS["pymupdf4llm"], run_dir, section="sec")
    assert doc.status == "crashed"
    assert doc.counts() == {
        "equations": 0,
        "equations_transcribed": 0,
        "headings": 0,
        "blocks": 0,
        "figures": 0,
        "tables": 0,
    }
    assert doc.notes


def test_an_output_with_nothing_in_it_is_empty(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path, "pymupdf4llm", "page.md", "\n\n")
    assert adapt_run(ARMS["pymupdf4llm"], run_dir, section="s").status == "empty"


def test_unreadable_native_output_is_a_crash_not_an_exception(tmp_path: Path) -> None:
    run_dir = write_run(tmp_path, "docling", "page.json", "{ this is not json")
    doc = adapt_run(ARMS["docling"], run_dir, section="s")
    assert doc.status == "crashed"


def test_intermediate_round_trips_through_json(tmp_path: Path) -> None:
    doc = Intermediate(arm="a", section="s")
    doc.equations = [Equation(0, "block", latex="x")]
    doc.headings = [Heading(1, 2, "H")]
    doc.blocks = [Block(2, "para", "text")]
    doc.figures = [Figure(3, "cap", True, None)]
    intermediate.write(doc, tmp_path / "i.json")
    assert intermediate.read(tmp_path / "i.json") == doc


def test_validate_catches_a_bad_kind_and_a_repeated_order() -> None:
    doc = Intermediate(arm="a", section="s", status="weird")
    doc.blocks = [Block(0, "banner", "x")]
    doc.headings = [Heading(0, 9, "H")]
    problems = intermediate.validate(doc)
    assert len(problems) == 4


# ------------------------------------------------------------------ scoring end to end


def gold_equation(gid: str, latex: str, display: str = "block") -> dict:
    return {
        "id": gid,
        "kind": "equation",
        "display": display,
        "index_on_page": int(gid[-1]),
        "mathml": safe_latex_to_mathml(latex, display),
    }


@pytest.fixture
def reference(tmp_path: Path) -> invented.Reference:
    path = tmp_path / "ref.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 100), "Speed and Velocity", fontsize=12)
        page.insert_text(
            (72, 130), "The car travels 150 kilometers in 3.2 hours today.", fontsize=11
        )
        document.save(path)
    return invented.reference_from_pdf(path)


def test_score_arm_puts_every_measure_in_one_summary(
    tmp_path: Path, reference: invented.Reference
) -> None:
    gold = GoldSubset(section="sec", pages=[0])
    gold.equations = [gold_equation("g0", r"v = \frac{d}{t}"), gold_equation("g1", "a = b")]
    gold.headings = [
        {"text": "Speed and Velocity", "level": 2, "context": None, "index_on_page": 0}
    ]
    gold.figures_on_pages = 1
    doc = Intermediate(arm="docling-formula", section="sec")
    doc.equations = [Equation(1, "block", latex=r"v = \frac{d}{t}"), Equation(2, "block")]
    doc.headings = [Heading(0, 2, "Speed and Velocity")]
    doc.blocks = [Block(3, "para", "The car travels 150 kilometers in 3.2 hours today.")]
    doc.figures = [Figure(4, None, True, None)]
    run = json.loads(
        write_run(tmp_path, "docling-formula", "p.json", "{}").joinpath("run.json").read_text()
    )
    result = score.score_arm(doc, run, gold, reference, page_id="p")
    summary = result["summary"]

    assert summary["plumbing_only"] is True
    assert summary["equations"]["primary"]["end_to_end_exact_lenient"] == 0.5
    assert summary["equations"]["primary"]["detection_recall"] == 0.5
    assert summary["equations"]["does_not_transcribe_by_design"] is False
    assert summary["headings"]["f1"] == 1.0
    assert summary["figures"]["no_description"] == 1
    assert summary["invented_content"]["prose_sentence"]["unsupported"] == 0
    assert summary["cost_and_time"]["peak_rss_mb"] == 200.0
    assert summary["cost_and_time"]["api_cost_usd"] == 0.0
    assert set(summary["equations"]) >= {
        "primary",
        "allowlist_free",
        "normalised_plan",
        "normalised_plus",
        "retained_in_any_form",
    }
    # Items sit beside the summary, not in it.
    assert "item" not in summary["equations"]["primary"]
    assert "primary" in result["items"]["equations"]


def test_a_failed_section_scores_as_found_nothing(
    tmp_path: Path, reference: invented.Reference
) -> None:
    gold = GoldSubset(section="sec", pages=[0])
    gold.equations = [gold_equation("g0", "a = b")]
    gold.headings = [
        {"text": "Speed and Velocity", "level": 2, "context": None, "index_on_page": 0}
    ]
    doc = Intermediate(arm="mineru-basic", section="sec", status="timeout")
    run = json.loads(
        write_run(tmp_path, "mineru-basic", "p.zip", "").joinpath("run.json").read_text()
    )
    summary = score.score_arm(doc, run, gold, reference, page_id="p")["summary"]
    assert summary["status"] == "timeout"
    assert summary["equations"]["primary"]["end_to_end_exact_lenient"] == 0.0
    assert summary["headings"]["recall"] == 0.0
    assert summary["equations"]["does_not_transcribe_by_design"] is True


def test_equation_content_kept_as_plain_text_counts_as_retained(
    tmp_path: Path, reference: invented.Reference
) -> None:
    gold = GoldSubset(section="sec", pages=[0])
    gold.equations = [gold_equation("g0", r"v = \frac{d}{t}")]
    doc = Intermediate(arm="pymupdf4llm", section="sec")
    doc.blocks = [Block(0, "para", "so v = d / t holds")]
    run = json.loads(
        write_run(tmp_path, "pymupdf4llm", "p.md", "x").joinpath("run.json").read_text()
    )
    summary = score.score_arm(doc, run, gold, reference, page_id="p")["summary"]
    retained = summary["equations"]["retained_in_any_form"]
    assert (retained["checked"], retained["retained_in_any_form"]) == (1, 1)
    assert summary["equations"]["primary"]["end_to_end_exact_lenient"] == 0.0


def test_markdown_is_exported_for_scorers_that_read_markdown(tmp_path: Path) -> None:
    import zipfile

    # Docling and PyMuPDF4LLM write the file themselves.
    run_dir = write_run(tmp_path, "pymupdf4llm", "page.md", "# H\n\nSome words in a line.\n")
    doc = adapt_run(ARMS["pymupdf4llm"], run_dir, section="s")
    assert doc.markdown_path == "native/page.md"
    # MinerU's is inside the zip and is extracted beside it.
    run_dir = write_run(tmp_path, "mineru-basic", "page.zip", "")
    with zipfile.ZipFile(run_dir / "native" / "page.zip", "w") as archive:
        archive.writestr("structured_content.json", json.dumps({"pages": []}))
        archive.writestr("markdown.md", "## Title\n\nWords here.\n")
    doc = adapt_run(ARMS["mineru-basic"], run_dir, section="s")
    assert doc.status == "empty"  # no blocks in this hand-made zip
    assert doc.markdown_path is None  # only an ok run exports
    with zipfile.ZipFile(run_dir / "native" / "page.zip", "w") as archive:
        archive.writestr(
            "structured_content.json",
            json.dumps({"pages": [{"blocks": [{"type": "text", "content": "Some words."}]}]}),
        )
        archive.writestr("markdown.md", "## Title\n\nWords here.\n")
    doc = adapt_run(ARMS["mineru-basic"], run_dir, section="s")
    assert doc.markdown_path == "native/page.md"
    assert (run_dir / "native" / "page.md").read_text() == "## Title\n\nWords here.\n"
