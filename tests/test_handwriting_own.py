"""The own-notes handwriting harness (scripts/handwriting_own.py and the gold form):
intake and the hash split, the refusal of any folder inside a repository, the answer-key round
trip through the form's server, and the run's guards. Synthetic ink drawn here; no model is called
and no real notes are needed."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "handwriting_own", REPO / "scripts/handwriting_own.py"
)
assert spec and spec.loader
own = importlib.util.module_from_spec(spec)
sys.modules["handwriting_own"] = own
spec.loader.exec_module(own)


def ink_pdf(path: Path, seed: int, pages: int = 2) -> None:
    """A PDF of synthetic pen strokes (zig-zags for handwriting), a picture per page."""
    doc = pymupdf.open()
    for number in range(pages):
        image = Image.new("RGB", (612, 792), "white")
        draw = ImageDraw.Draw(image)
        for line in range(4):
            y = 100 + 120 * line
            points = [(60 + 25 * k, y + ((k * (seed + number + line)) % 5) * 6) for k in range(12)]
            draw.line(points, fill=(20, 20, 160), width=3)
        path_png = path.with_suffix(f".{number}.png")
        image.save(path_png)
        page = doc.new_page(width=612, height=792)
        page.insert_image(page.rect, filename=str(path_png))
        path_png.unlink()
    doc.save(path)
    doc.close()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "private" / "handwriting"
    monkeypatch.setenv("HANDWRITING_OWN_DIR", str(folder))
    (folder / "pdfs").mkdir(parents=True)
    for seed in range(1, 6):
        ink_pdf(folder / "pdfs" / f"notes-{seed}.pdf", seed)
    return folder


# ------------------------------------------------------------------ the repo is never used


def test_intake_refuses_a_source_inside_the_repo(root: Path) -> None:
    inside = REPO / "tests" / "_notes_should_not_exist"
    with pytest.raises(SystemExit, match="inside a git repository"):
        own.intake(root, inside)


def test_private_root_refuses_the_repo_and_any_git_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HANDWRITING_OWN_DIR", str(REPO / "handwriting"))
    with pytest.raises(SystemExit, match="inside a git repository"):
        own.private_root()
    other = tmp_path / "some-other-repo"
    (other / ".git").mkdir(parents=True)
    monkeypatch.setenv("HANDWRITING_OWN_DIR", str(other / "notes"))
    with pytest.raises(SystemExit, match="inside a git repository"):
        own.private_root()


def test_a_symlink_into_the_repo_is_refused(tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(REPO)
    with pytest.raises(SystemExit, match="inside a git repository"):
        own.ensure_outside_repo(link / "docs")


def test_gitignore_guards_private_folders() -> None:
    lines = (REPO / ".gitignore").read_text().splitlines()
    for guard in ("readablestem-private/", "handwriting-own/", "handwriting-private/"):
        assert guard in lines


# ------------------------------------------------------------------------- intake and split


def test_registration_refuses_a_changed_pdf_or_set(root: Path) -> None:
    own.intake(root)
    ink_pdf(root / "pdfs" / "notes-6.pdf", 9)
    with pytest.raises(SystemExit, match="already written"):
        own.intake(root)
    (root / "pdfs" / "notes-6.pdf").unlink()
    ink_pdf(root / "pdfs" / "notes-1.pdf", 99)
    with pytest.raises(SystemExit, match="changed or is missing"):
        own.load_registration(root)


# --------------------------------------------------------------- the form's round trip


def call(url: str, body: object | None = None, *, method: str | None = None) -> tuple[int, bytes]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


@pytest.fixture
def server(root: Path):
    registration = own.intake(root)
    httpd = own.make_server(root, registration, None)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield registration, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def typed(page: int, box: list[float], latex: str, **kw: object) -> dict:
    return {"page": page, "box": box, "latex": latex, "display": "inline", "uncertain": False, **kw}


def test_server_only_knows_registered_units_and_this_host(server, root: Path) -> None:
    _, base = server
    assert call(f"{base}/api/gold/own-deadbeef", {"expressions": []})[0] == 404
    assert call(f"{base}/page/own-deadbeef/1.png")[0] == 404
    req = urllib.request.Request(base + "/api/state", headers={"Host": "evil.example"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req)
    assert err.value.code == 403
    assert not (root / "gold").exists()


# ------------------------------------------------------------------------------- the run


def finish_part(root: Path, registration: dict, part: str, latex: str = "x + y") -> None:
    for entry in registration["units"]:
        if entry["part"] == part:
            gold = {
                "unit": entry["unit"],
                "pdf_sha256": entry["sha256"],
                "part": part,
                "complete": True,
                "expressions": [typed(1, [0.1, 0.1, 0.4, 0.2], latex)],
            }
            own.write_json(root / "gold" / f"{entry['unit']}.json", gold)


def run_args(part: str = "dev", max_usd: float | None = 1.0, offline: bool = False):
    return SimpleNamespace(part=part, max_usd=max_usd, offline=offline)


@pytest.mark.parametrize("cap", [None, 0, -1, 2.01, 50])
def test_run_needs_a_cap_of_at_most_two_dollars(root: Path, cap: float | None) -> None:
    own.intake(root)
    with pytest.raises(SystemExit, match="--max-usd"):
        own.cmd_run(run_args(max_usd=cap))


def test_run_refuses_changed_notes(root: Path) -> None:
    registration = own.intake(root)
    finish_part(root, registration, "dev")
    (root / "pdfs" / registration["units"][0]["file"]).write_bytes(b"%PDF-1.4 changed")
    with pytest.raises(SystemExit, match="changed or is missing"):
        own.cmd_run(run_args())


def test_run_passes_the_cap_and_records_once(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from readablestem.cli import eval_cli

    registration = own.intake(root)
    finish_part(root, registration, "dev")
    finish_part(root, registration, "heldout")
    seen: dict = {}

    def fake(ns, gold_dir, out, **kw):
        seen.update(max_usd=ns.max_usd, gold=Path(gold_dir), live=not ns.answers_offline)
        seen["env"] = {k: os.environ.get(k) for k in own.PIPELINE_ENV}
        return 0

    monkeypatch.setattr(eval_cli, "_run_pipeline", fake)
    monkeypatch.setenv("IMAGE_PAGES_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("IMAGE_PAGES_DPI", "300")
    with pytest.raises(SystemExit, match="run dev first"):
        own.cmd_run(run_args("heldout"))
    assert own.cmd_run(run_args("dev", 1.5)) == 0
    assert seen["max_usd"] == 1.5 and seen["live"] and not own.inside_repo(seen["gold"])
    assert seen["env"] == own.PIPELINE_ENV  # the registered settings applied during the run
    assert os.environ["IMAGE_PAGES_MODEL"] == "gpt-6.1-sol"  # and nothing leaks afterwards
    assert os.environ["IMAGE_PAGES_DPI"] == "300"
    assert "FEATURE_IMAGE_PAGES" not in os.environ or os.environ["FEATURE_IMAGE_PAGES"] != "true"
    assert len(list(seen["gold"].glob("own-*.jsonl"))) == 2
    assert len(list((seen["gold"] / "pdfs").glob("*.pdf"))) == 2
    with pytest.raises(SystemExit, match="already recorded"):
        own.cmd_run(run_args("dev"))
    assert own.cmd_run(run_args("dev", offline=True)) == 0  # replay is allowed
    assert own.cmd_run(run_args("heldout", 0.5)) == 0
    log = [json.loads(x) for x in (root / "runs" / "spend-log.jsonl").read_text().splitlines()]
    assert [e["part"] for e in log] == ["dev", "heldout"]
    assert "latex" not in json.dumps(log)  # counts only


def test_status_prints_counts_only(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registration = own.intake(root)
    finish_part(root, registration, "dev", latex=r"secret \alpha")
    own.cmd_status(SimpleNamespace())
    out = capsys.readouterr().out
    assert "dev: 2 PDFs, 2 finished, 2 expressions typed" in out
    assert "alpha" not in out and "notes-" not in out


def test_max_usd_is_a_total_across_restarts(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from readablestem.cli import eval_cli

    registration = own.intake(root)
    finish_part(root, registration, "dev")
    seen: dict = {}

    def fake(ns, gold_dir, out, **kw):
        seen["max_usd"] = ns.max_usd
        return 1  # a failed start: no marker, so a restart is allowed

    monkeypatch.setattr(eval_cli, "_run_pipeline", fake)
    answers = root / "runs" / "dev" / "answers.jsonl"
    answers.parent.mkdir(parents=True, exist_ok=True)
    answers.write_text(json.dumps({"call": {"cost_usd": 0.75}}) + "\n")
    own.cmd_run(run_args("dev", 1.0))
    assert seen["max_usd"] == 0.25  # 1.00 total minus the 0.75 already spent
    with pytest.raises(SystemExit, match="total across restarts"):
        own.cmd_run(run_args("dev", 0.75))


# --------------------------------------------------------- the form itself, in a real browser
