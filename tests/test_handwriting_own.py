"""The own-notes handwriting harness (scripts/handwriting_own.py and the gold form):
intake and the hash split, the refusal of any folder inside a repository, the answer-key round
trip through the form's server, and the run's guards. Synthetic ink drawn here; no model is called
and no real notes are needed."""

from __future__ import annotations

import importlib.util
import json
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


def test_default_folder_is_outside_the_repo() -> None:
    assert not own.inside_repo(own.DEFAULT_ROOT)


def test_gitignore_guards_private_folders() -> None:
    lines = (REPO / ".gitignore").read_text().splitlines()
    for guard in ("readablestem-private/", "handwriting-own/", "handwriting-private/"):
        assert guard in lines


# ------------------------------------------------------------------------- intake and split


def test_intake_registers_a_hash_split_once(root: Path) -> None:
    data = own.intake(root)
    parts = [e["part"] for e in data["units"]]
    assert parts.count("dev") == 2 and parts.count("heldout") == 3  # 30% of 5, rounded
    assert all(e["pages"] == 2 for e in data["units"])
    assert (root / "registration.json").is_file() and (root / "registration.sha256").is_file()
    assert len(list((root / "pages").glob("own-*/p0?.png"))) == 10
    own.load_registration(root)  # hashes hold
    again = own.intake(root)  # idempotent
    assert [e["part"] for e in again["units"]] == parts


def test_split_depends_only_on_the_file_hashes() -> None:
    hashes = [f"{i:064x}" for i in range(10)]
    first = own.split_units(hashes)
    assert own.split_units(list(reversed(hashes))) == first
    assert sum(p == "dev" for p in first.values()) == 3


def test_registration_refuses_a_changed_pdf_or_set(root: Path) -> None:
    own.intake(root)
    ink_pdf(root / "pdfs" / "notes-6.pdf", 9)
    with pytest.raises(SystemExit, match="already written"):
        own.intake(root)
    (root / "pdfs" / "notes-6.pdf").unlink()
    ink_pdf(root / "pdfs" / "notes-1.pdf", 99)
    with pytest.raises(SystemExit, match="changed or is missing"):
        own.load_registration(root)


def test_intake_needs_two_pdfs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "p"
    monkeypatch.setenv("HANDWRITING_OWN_DIR", str(folder))
    (folder / "pdfs").mkdir(parents=True)
    ink_pdf(folder / "pdfs" / "a.pdf", 1)
    with pytest.raises(SystemExit, match="at least 2"):
        own.intake(folder)


def test_intake_copies_from_a_source_outside_the_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, src = tmp_path / "private", tmp_path / "exports"
    src.mkdir()
    monkeypatch.setenv("HANDWRITING_OWN_DIR", str(folder))
    for seed in (1, 2, 3):
        ink_pdf(src / f"n{seed}.pdf", seed, pages=1)
    data = own.intake(folder, src)
    assert len(data["units"]) == 3
    assert len(list((folder / "pdfs").glob("*.pdf"))) == 3


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


def test_form_round_trip_writes_gold_outside_the_repo(root: Path, server) -> None:
    registration, base = server
    status, html = call(base + "/")
    assert status == 200 and b"LaTeX, typed by you" in html
    _, state = call(base + "/api/state")
    units = json.loads(state)["units"]
    assert len(units) == 5 and all(u["gold"] is None and u["pages"] == 2 for u in units)
    unit = units[0]["unit"]
    status, png = call(f"{base}/page/{unit}/1.png")
    assert status == 200 and png.startswith(b"\x89PNG")
    status, ml = call(base + "/api/mathml?latex=x%5E2%2By&display=inline")
    assert status == 200 and "<msup>" in json.loads(ml)["mathml"]

    body = {
        "complete": True,
        "expressions": [
            typed(2, [0.1, 0.5, 0.5, 0.6], r"\frac{a}{b}", display="display"),
            typed(1, [0.1, 0.1, 0.4, 0.2], r"x^2 + y"),
            typed(1, [0.1, 0.3, 0.4, 0.4], "z = ?", uncertain=True),
            typed(1, [0.5, 0.3, 0.7, 0.4], ""),
        ],
    }
    status, saved = call(f"{base}/api/gold/{unit}", body)
    assert status == 200 and json.loads(saved) == {"saved": 4}
    path = root / "gold" / f"{unit}.json"
    assert path.is_file() and not own.inside_repo(path)
    on_disk = json.loads(path.read_text())
    assert on_disk["complete"] is True and on_disk["pdf_sha256"] in {
        e["sha256"] for e in registration["units"]
    }
    _, again = call(base + "/api/state")
    reloaded = next(u for u in json.loads(again)["units"] if u["unit"] == unit)
    assert reloaded["gold"]["expressions"][1]["latex"] == "x^2 + y"

    entry = next(e for e in registration["units"] if e["unit"] == unit)
    records = own.gold_records(root, entry, on_disk)
    assert [r["latex"] for r in records] == [r"x^2 + y", r"\frac{a}{b}"]  # reading order
    assert [r["display"] for r in records] == ["inline", "block"]  # `?` and blank left out
    assert records[0]["mathml"].startswith("<math") and records[0]["tokens"] >= 3
    assert records[1]["pdf_page"] == 2


@pytest.mark.parametrize(
    "body",
    [
        {"expressions": [typed(3, [0.1, 0.1, 0.4, 0.2], "x")]},  # page out of range
        {"expressions": [typed(1, [0.4, 0.1, 0.1, 0.2], "x")]},  # inverted box
        {"expressions": [typed(1, [0.1, 0.1, 0.4, 0.2], "x", display="huge")]},
        {"expressions": [typed(1, [0.1, 0.1, 0.4, 0.2], "x" * 3000)]},
        {"expressions": "no"},
    ],
)
def test_server_rejects_bad_answer_keys(server, body: dict) -> None:
    registration, base = server
    unit = registration["units"][0]["unit"]
    status, _ = call(f"{base}/api/gold/{unit}", body)
    assert status == 400


def test_server_only_knows_registered_units_and_this_host(server, root: Path) -> None:
    _, base = server
    assert call(f"{base}/api/gold/own-deadbeef", {"expressions": []})[0] == 404
    assert call(f"{base}/page/own-deadbeef/1.png")[0] == 404
    req = urllib.request.Request(base + "/api/state", headers={"Host": "evil.example"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req)
    assert err.value.code == 403
    assert not (root / "gold").exists()


def test_form_part_filter(root: Path) -> None:
    registration = own.intake(root)
    httpd = own.make_server(root, registration, "dev")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        _, state = call(f"http://127.0.0.1:{httpd.server_address[1]}/api/state")
        assert {u["part"] for u in json.loads(state)["units"]} == {"dev"}
    finally:
        httpd.shutdown()
        httpd.server_close()


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


def test_run_refuses_an_unfinished_answer_key(root: Path) -> None:
    own.intake(root)
    with pytest.raises(SystemExit, match="not finished"):
        own.cmd_run(run_args())


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
        return 0

    monkeypatch.setattr(eval_cli, "_run_pipeline", fake)
    with pytest.raises(SystemExit, match="run dev first"):
        own.cmd_run(run_args("heldout"))
    assert own.cmd_run(run_args("dev", 1.5)) == 0
    assert seen["max_usd"] == 1.5 and seen["live"] and not own.inside_repo(seen["gold"])
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


# --------------------------------------------------------- the form itself, in a real browser


@pytest.mark.browser
def test_form_in_a_browser_box_type_save(root: Path) -> None:
    """Drag a box on the page, type LaTeX with no draft shown, see the preview, find the JSON."""
    sync_api = pytest.importorskip("playwright.sync_api")
    registration = own.intake(root)
    httpd = own.make_server(root, registration, None)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/"
    first = sorted(registration["units"], key=lambda e: e["unit"])[0]["unit"]
    try:
        with sync_api.sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 1000}, bypass_csp=True)
            page.goto(base)
            page.wait_for_function("document.getElementById('page').naturalWidth > 0")
            assert page.locator("textarea").count() == 0  # blank form, no draft
            box = page.locator("#page").bounding_box()
            page.mouse.move(box["x"] + box["width"] * 0.1, box["y"] + box["height"] * 0.1)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.2)
            page.mouse.up()
            area = page.locator("textarea")
            assert area.count() == 1 and area.input_value() == ""
            area.fill(r"\frac{a}{b}")
            page.wait_for_selector(".preview math")
            page.check("#complete")
            page.wait_for_function("document.getElementById('status').textContent === 'Saved'")
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
    saved = json.loads((root / "gold" / f"{first}.json").read_text())
    assert saved["complete"] is True
    (item,) = saved["expressions"]
    assert item["latex"] == r"\frac{a}{b}" and item["page"] == 1
    assert 0.08 < item["box"][0] < 0.12 and 0.45 < item["box"][2] < 0.55
