"""`gold devset ...` and `eval ... --set dev-v2` (offline; Chromium and the model are replaced)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from a11y_stem.cli import eval_cli
from a11y_stem.cli.main import main
from a11y_stem.eval import devset_media
from a11y_stem.gold import devset as ds
from a11y_stem.pipeline import equation_step
from devset_support import tiny_set
from test_eval_support import FakeMedia, fake_transcribe, settings

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(eval_cli, "_settings", lambda: settings())
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))  # where the check part goes


class Media:
    """Stands in for DevSetMedia (same constructor), handing out FakeMedia."""

    built: list[tuple] = []  # noqa: RUF012 - reset by the fixture

    def __init__(self, gold_dir, cache_dir, fetcher, *, paper="Letter") -> None:
        Media.built.append((gold_dir, cache_dir, fetcher.cache_dir))
        self.media = FakeMedia()

    def __enter__(self):
        return self.media

    def __exit__(self, *exc):
        return None


@pytest.fixture
def media(monkeypatch):
    Media.built = []
    monkeypatch.setattr(devset_media, "DevSetMedia", Media)
    monkeypatch.setattr(equation_step, "transcribe", fake_transcribe)


def run(root: Path, *extra: str, part: str = "dev") -> int:
    argv = ["eval", "pipeline", "--task", "equations", "--set", "dev-v2", "--part", part]
    return main([*argv, "--devset-dir", str(root), "--fake", "--date", "2026-10-14", *extra])


def test_verify_summary_and_a_damaged_set(tmp_path, capsys) -> None:
    root = tiny_set(tmp_path)
    assert main(["gold", "devset", "verify", "--root", str(root)]) == 0
    assert "check part is the sealed archive" in capsys.readouterr().out
    assert main(["gold", "devset", "summary", "--root", str(root), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["id"] for r in rows} >= {"inline_fraction", "hyphen_break"}
    assert next(r for r in rows if r["id"] == "heading_sentence")["dev"] == 1
    (root / "dev" / "manifest.json").unlink()
    assert main(["gold", "devset", "verify", "--root", str(root)]) == 1
    assert "manifest.json is missing" in capsys.readouterr().err


def test_a_dev_run_uses_the_dev_folder_and_the_repository_cache(tmp_path, media) -> None:
    root = tiny_set(tmp_path)
    out = tmp_path / "r.json"
    assert run(root, "--out", str(out), "--cache", str(tmp_path / "c")) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert (report["set"], report["part"], report["client"]) == ("dev-v2", "dev", "fake")
    assert report["dataset"]["gold_dir"] == "dev-v2 (dev)"
    assert report["n"] == 2  # the two equations of the synthetic page
    ((gold_dir, cache_dir, archive_cache),) = Media.built
    assert gold_dir == root / "dev" and cache_dir == tmp_path / "c"
    assert archive_cache == REPO / ".cache" / "openstax"


def test_a_dev_run_without_out_reports_under_local_eval(tmp_path, media, monkeypatch) -> None:
    from a11y_stem.cli import main as cli_main

    monkeypatch.setattr(cli_main, "project_root", lambda start=None: tmp_path / "project")
    root = tiny_set(tmp_path)
    assert run(root, "--cache", str(tmp_path / "c")) == 0
    assert (tmp_path / "project" / ".local" / "eval" / "dev-v2" / "dev-equations.json").is_file()


def test_the_check_part_runs_from_the_sealed_archive_and_stays_outside_the_repository(
    tmp_path, media
) -> None:
    root = tiny_set(tmp_path)
    assert run(root, part="check") == 0
    ((gold_dir, cache_dir, archive_cache),) = Media.built
    xdg = tmp_path / "xdg" / "a11y-stem" / "dev-v2"
    assert xdg in gold_dir.parents
    assert cache_dir == xdg / "eval-cache" and archive_cache == xdg / "openstax"
    report = json.loads((xdg / "reports" / "check-equations.json").read_text(encoding="utf-8"))
    seal = json.loads((root / ds.SEAL_FILE).read_text(encoding="utf-8"))
    assert report["part"] == "check" and report["seal_archive_sha256"] == seal["archive_sha256"]
    assert "authored/" not in json.dumps(report)


def test_the_check_part_refuses_item_rows_and_places_inside_the_repository(
    tmp_path, media, capsys
) -> None:
    root = tiny_set(tmp_path)
    assert run(root, "--items", str(tmp_path / "i.jsonl"), part="check") == 2
    assert "item by item" in capsys.readouterr().err
    assert run(root, "--out", str(REPO / "data" / "eval" / "x.json"), part="check") == 2
    assert "is inside the repository" in capsys.readouterr().err
    assert run(root, "--cache", str(REPO / ".cache" / "x"), part="check") == 2
    assert Media.built == []


def test_a_set_that_is_not_intact_is_not_run(tmp_path, media, capsys) -> None:
    root = tiny_set(tmp_path)
    archive = root / ds.CHECK_ARCHIVE
    archive.write_bytes(archive.read_bytes() + b"x")
    assert run(root, part="check") == 2
    assert "not intact" in capsys.readouterr().err
    gold = root / "dev" / "authored" / "doc-a" / "gold.jsonl"
    gold.write_bytes(gold.read_bytes() + b"\n")
    assert run(root) == 2
    assert "does not match its manifest hash" in capsys.readouterr().err
    assert Media.built == []


def test_other_places_and_options_do_not_apply(tmp_path, media, capsys) -> None:
    root = tiny_set(tmp_path)
    assert run(root, "--gold", str(tmp_path)) == 2
    assert "--gold does not apply" in capsys.readouterr().err
    assert run(root, "--notation", "bold d") == 2
    assert "--notation" in capsys.readouterr().err


def test_equations_self_check_reads_the_dev_part_only(tmp_path, capsys) -> None:
    root = tiny_set(tmp_path)
    argv = ["eval", "equations", "--set", "dev-v2", "--devset-dir", str(root), "--self-check"]
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)["n"] == 2
    assert main([*argv, "--part", "check"]) == 2
    assert "only for eval pipeline" in capsys.readouterr().err


def test_the_devset_media_prints_openstax_pages_and_reads_authored_pdfs(tmp_path) -> None:
    root = tiny_set(tmp_path)
    media = devset_media.DevSetMedia.__new__(devset_media.DevSetMedia)
    media.gold_dir = root / "dev"
    record = {"source": "authored", "pdf": "authored/doc-a/doc-a.pdf", "context": "near"}
    assert media.page_pdf([record]) == b"%PDF-1.4 fake"
    assert media.equation_context(record) == "near"
    with pytest.raises(devset_media.RenderError, match="missing"):
        media.page_pdf([{**record, "pdf": "authored/none/none.pdf"}])


def test_classes_self_check_scores_the_gold_against_itself(tmp_path, capsys) -> None:
    root = tiny_set(tmp_path)
    argv = ["eval", "classes", "--set", "dev-v2", "--devset-dir", str(root), "--self-check"]
    assert main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["scripts"]["scripted_rate"] == 1.0
    assert out["code"]["exact_rate"] == 1.0 and out["glossary"]["structured_rate"] == 1.0


def test_classes_scores_saved_pages_and_a_missing_page_is_all_lost(tmp_path, capsys) -> None:
    root = tiny_set(tmp_path)
    argv = ["eval", "classes", "--set", "dev-v2", "--devset-dir", str(root)]
    pages = tmp_path / "html"
    pages.mkdir()
    assert main([*argv, "--html-dir", str(pages)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["scripts"]["scripted"] == 0 and out["code"]["missing"] == out["code"]["n"]
    assert main([*argv, "--html-dir", str(tmp_path / "none")]) == 2
    assert "html dir not found" in capsys.readouterr().err


def test_classes_needs_the_dev_part_of_dev_v2(tmp_path, capsys) -> None:
    root = tiny_set(tmp_path)
    assert main(["eval", "classes", "--self-check"]) == 2
    assert "--set dev-v2" in capsys.readouterr().err
    argv = ["eval", "classes", "--set", "dev-v2", "--devset-dir", str(root), "--self-check"]
    assert main([*argv, "--part", "check"]) == 2
    assert "--part check" in capsys.readouterr().err
