"""The seams other work packages fill: stage plugins, the parsers hook, the shared form of
other systems' outputs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from readablestem.proof import stages
from readablestem.proof.bundle import Bundle, BundleWriter
from readablestem.proof.errors import BundleError, ProofError
from readablestem.proof.intermediate import load_systems, parse_system_section

# Contract with proof/parsers: `make proof PDF_DIR=... OUT_DIR=...` writes
# OUT_DIR/<arm>/<book>__<page>.json for each PDF in PDF_DIR. This one writes "{}".
DEMO_MAKEFILE = (
    "proof:\n"
    "\tmkdir -p $(OUT_DIR)/demo\n"
    "\tfor f in $(PDF_DIR)/*.pdf; do b=`basename $$f .pdf`; "
    "echo '{}' > $(OUT_DIR)/demo/$$b.json; done\n"
)


def test_the_repository_wires_every_stage_plugin():
    loaded = stages.load_plugins()
    assert loaded == [
        "readablestem.proof.invented",
        "readablestem.proof.comparison",
        "readablestem.proof.parsers",
        "readablestem.proof.labels",
    ]


def test_a_plugin_that_exists_but_fails_to_import_is_not_hidden(monkeypatch, tmp_path):
    import readablestem.proof as package

    (tmp_path / "broken_plugin.py").write_text("import a_module_that_does_not_exist\n")
    monkeypatch.setattr(package, "__path__", [*package.__path__, str(tmp_path)])
    monkeypatch.setattr(stages, "PLUGIN_MODULES", ("readablestem.proof.broken_plugin",))
    with pytest.raises(ModuleNotFoundError, match="a_module_that_does_not_exist"):
        stages.load_plugins()


def test_a_stub_prints_no_number_and_says_what_it_will_read():
    stub = stages.default_stages()["invented_content"]
    result = stub.run(None)  # type: ignore[arg-type]  # a stub reads nothing
    assert result.status == "not_wired"
    text = "\n".join(s.markdown() for s in result.sections)
    assert "Not computed in this build" in text and "will read" in text
    assert result.notes == ["Invented-content rate: not computed (stub)"]


# ------------------------------------------------------------------ the parsers hook


def record_context(root: Path, work: Path, pdfs: dict[str, Path], said: list[str]):
    return stages.RecordContext(
        pset=None,  # type: ignore[arg-type]
        root=root,
        gold_dir=work,
        records=[],
        arm=None,  # type: ignore[arg-type]
        settings=None,  # type: ignore[arg-type]
        work_dir=work,
        pdf_paths=pdfs,
        say=said.append,
    )


def parser_stage():
    return stages.default_stages()["parser_comparison"]


def test_without_proof_parsers_a_live_run_records_nothing_and_says_so(tmp_path):
    said: list[str] = []
    writer = BundleWriter(tmp_path / "b")
    parser_stage().record(record_context(tmp_path, tmp_path / "w", {}, said), writer)
    assert said == ["parsers: proof/parsers is not installed; no parser output recorded"]
    assert not (writer.partial / "systems").exists()


@pytest.mark.skipif(shutil.which("make") is None, reason="needs make")
def test_proof_parsers_is_run_with_the_pdfs_and_its_output_lands_in_the_bundle(tmp_path):
    root = tmp_path / "root"
    folder = root / "proof" / "parsers"
    folder.mkdir(parents=True)
    (folder / "Makefile").write_text(DEMO_MAKEFILE)
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(b"%PDF-1.4 demo")
    writer = BundleWriter(tmp_path / "bundle")
    parser_stage().record(record_context(root, tmp_path / "work", {"physics/p-1": pdf}, []), writer)
    assert (writer.partial / "systems" / "demo" / "physics__p-1.json").is_file()
    copied = tmp_path / "work" / "parser-pdfs" / "physics__p-1.pdf"
    assert copied.read_bytes() == pdf.read_bytes()


@pytest.mark.skipif(shutil.which("make") is None, reason="needs make")
def test_a_failing_parsers_run_stops_the_proof(tmp_path):
    root = tmp_path / "root"
    (root / "proof" / "parsers").mkdir(parents=True)
    (root / "proof" / "parsers" / "Makefile").write_text("proof:\n\tfalse\n")
    writer = BundleWriter(tmp_path / "bundle")
    with pytest.raises(ProofError, match="proof/parsers failed"):
        parser_stage().record(record_context(root, tmp_path / "w", {}, []), writer)


# ------------------------------------------------------------ the shared output form


def section_json(**extra) -> dict:
    return {
        "arm": "docling-default",
        "section": "physics/p-1",
        "status": "ok",
        "seconds": 1.5,
        "headings": [{"order": 0, "level": 2, "text": "A heading"}],
        **extra,
    }


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"status": "great"}, "status must be one of"),
        ({"arm": None}, "'arm' is missing"),
        ({"equations": {}}, "'equations' must be a list"),
        ({"headings": [{"level": "2", "text": "x"}]}, "integer level"),
        ({"figures": ["x"]}, "'figures' must be a list of objects"),
    ],
)
def test_a_malformed_system_section_is_refused_with_its_place(change, message):
    with pytest.raises(BundleError, match=message):
        parse_system_section(section_json(**change), where="systems/a/b.json")


def test_systems_are_read_from_the_bundle_by_arm_and_section(tmp_path):
    systems = tmp_path / "systems"
    (systems / "docling-default").mkdir(parents=True)
    (systems / "docling-default" / "physics__p-1.json").write_text(json.dumps(section_json()))
    (systems / "docling-default" / "physics__p-1.run2.json").write_text(
        json.dumps(section_json(status="timeout"))
    )
    (systems / "docling-default" / "notes.txt").write_text("ignored")
    loaded = load_systems(Bundle(tmp_path, {}))
    runs = loaded["docling-default"]["physics/p-1"]
    assert sorted((r.run, r.status) for r in runs) == [(1, "ok"), (2, "timeout")]
    assert load_systems(Bundle(tmp_path / "none", {})) == {}
