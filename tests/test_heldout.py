"""The pre-registered held-out set: its registration, the pinned fetch, the check that gold
is the registered gold, and the `--set heldout` selector of the CLI (all offline).

The real registered gold is not in the repository and no test reads it: the checks run on a
small synthetic set built the same way (`fetch_set` on a fake archive), and on the
registration's own numbers.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path
from typing import ClassVar

import pymupdf
import pytest

from readablestem.cli import eval_cli
from readablestem.cli import heldout as heldout_cli
from readablestem.cli.main import build_parser, main
from readablestem.config import Settings
from readablestem.eval import pipeline_eval, render
from readablestem.gold import heldout, openstax
from readablestem.gold.heldout import (
    REGISTERED,
    HeldoutError,
    Registration,
    Section,
    content_sha256,
    fetch_set,
    file_sha256,
    require_gold,
    verify_gold,
)
from readablestem.gold.openstax import BookPin, BookStateError, GoldError, fetch_gold
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import equation_step
from test_eval_support import EQ_A, EQ_V, EQ_X, FakeMedia, equation, fake_transcribe, write_gold
from test_openstax import ARCHIVE as CURRENT_ARCHIVE
from test_openstax import CC_BY, FIXTURE, TREE

REPO = Path(__file__).parent.parent
PAGE = FIXTURE.replace("<body>", '<body><h2 id="h-first">A heading</h2>', 1)
PINNED = "20250101.000000"
PINNED_URL = f"https://openstax.org/apps/archive/{PINNED}/contents/book-uuid@v0"
SLUGS = ("1-1-first-section", "1-2-second-section")
PINS = {"physics": BookPin(PINNED, "v0")}


class PinnedFetcher:
    """A retired book whose pinned archive is not the current release's."""

    made: ClassVar[list[PinnedFetcher]] = []

    def __init__(
        self,
        state: str = "retired",
        missing: tuple[str, ...] = (),
        cache_dir: Path | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        PinnedFetcher.made.append(self)
        cms_book = {
            "meta": {"slug": "physics"},
            "title": "Physics",
            "cnx_id": "book-uuid",
            "book_state": state,
            "license_name": "CMS name",
            "license_url": CC_BY,
            "authors": [{"value": {"name": "Senior Author", "senior_author": True}}],
        }
        self.responses: dict[str, dict] = {
            openstax.CMS_BOOKS_URL: {"items": [cms_book]},
            openstax.RELEASE_URL: {
                "archiveUrl": CURRENT_ARCHIVE,
                "books": {"book-uuid": {"defaultVersion": "v9"}},
            },
            f"{PINNED_URL}.json": {
                "title": "Physics",
                "tree": TREE,
                "license": {"name": "Creative Commons Attribution License", "url": CC_BY},
            },
        }
        for page in ("p12", "p13", "p21"):
            self.responses[f"{PINNED_URL}:{page}.json"] = {"content": PAGE}
        for url in missing:
            del self.responses[url]
        self.calls: list[str] = []

    @property
    def requests_made(self) -> int:
        return len(self.calls)

    def get_json(self, url: str):
        self.calls.append(url)
        if url not in self.responses:
            raise GoldError(f"GET {url} failed: HTTP 404")
        return self.responses[url]


def draft(date: str) -> Registration:
    """A registration of the synthetic set with placeholder hashes (enough to fetch)."""
    sections = tuple(Section("physics", slug, 3, "0" * 64, "0" * 64) for slug in SLUGS)
    return Registration(date, sections, PINS, "0" * 64, "0" * 64)


def registration_of(
    gold_dir: Path, date: str, pins: dict[str, BookPin], pdf_pages: dict[str, int] | None = None
) -> Registration:
    """The registration a gold folder would have been given on `date`: its own hashes."""
    manifest = json.loads((gold_dir / "manifest.json").read_text(encoding="utf-8"))
    sections = []
    for entry in manifest["files"]:
        path = gold_dir / entry["path"]
        pages = (pdf_pages or {}).get(path.stem, 3)
        sections.append(
            Section(path.parent.name, path.stem, pages, file_sha256(path), content_sha256(path))
        )
    return Registration(
        date=date,
        sections=tuple(sections),
        pins=pins,
        manifest_sha256=file_sha256(gold_dir / "manifest.json"),
        attribution_sha256=file_sha256(gold_dir / "ATTRIBUTION.md"),
        preregistration_sha256="ab" * 32,
    )


def fetched(root: Path, date: str = "2026-10-06") -> tuple[Path, Registration]:
    """Fetch the synthetic set into root/gold; return it with its registration."""
    gold = root / "gold"
    fetch_set(
        gold, PinnedFetcher(), retrieved_at=date, allow_retired=True, registration=draft(date)
    )
    return gold, registration_of(gold, date, PINS)


# ------------------------------------------------------------ the registration itself


def test_registration_is_the_pre_registered_set():
    assert len(REGISTERED.sections) == 15
    assert REGISTERED.total_pdf_pages == 242
    assert REGISTERED.date == "2026-10-13"
    assert list(REGISTERED.pages) == [
        "college-physics",
        "chemistry",
        "precalculus",
        "introductory-statistics",
        "intermediate-algebra",
    ]
    assert all(len(slugs) == 3 for slugs in REGISTERED.pages.values())
    assert set(REGISTERED.pins) == set(REGISTERED.pages)
    # One archive release for every book, which is what `gold fetch` read on the day.
    assert {pin.archive_version for pin in REGISTERED.pins.values()} == {"20260604.144757"}
    assert REGISTERED.pins["precalculus"] == BookPin("20260604.144757", "ebc5beb")


def test_registered_slugs_are_numbered_sections_and_unique():
    slugs = [section.slug for section in REGISTERED.sections]
    assert len(set(slugs)) == 15
    assert all(openstax.SECTION_SLUG.match(slug) for slug in slugs)
    for section in REGISTERED.sections:
        assert 1 <= section.pdf_pages <= 30  # the app refuses longer documents
        assert re.fullmatch(r"[0-9a-f]{64}", section.sha256)
        assert re.fullmatch(r"[0-9a-f]{64}", section.content_sha256)
        assert section.path == f"openstax/{section.book}/{section.slug}.jsonl"
    for digest in (
        REGISTERED.manifest_sha256,
        REGISTERED.attribution_sha256,
        REGISTERED.preregistration_sha256,
    ):
        assert digest and re.fullmatch(r"[0-9a-f]{64}", digest)


def test_section_lookup_by_slug_and_book():
    found = REGISTERED.section("3-1-complex-numbers")
    assert found is not None and found.book == "precalculus"
    assert REGISTERED.section("3-1-complex-numbers", "precalculus") == found
    assert REGISTERED.section("3-1-complex-numbers", "chemistry") is None
    assert REGISTERED.section("2-1-relative-motion-distance-and-displacement") is None


def test_no_held_out_gold_is_in_the_repository():
    """Pre-registration section 10: not in data/gold, not in a fixture, not in the demo."""
    for book in REGISTERED.pins:
        assert not list(REPO.glob(f"data/**/openstax/{book}")), book
        assert not list(REPO.glob(f"tests/fixtures/**/openstax/{book}")), book
    development = {"physics", "statistics"}  # the live CC BY books, used whole in development
    assert development.isdisjoint(REGISTERED.pins)
    for section in REGISTERED.sections:
        assert not (REPO / "data" / "gold" / section.path).exists()
        assert not (REPO / "data" / "gold" / "tables" / section.path).exists()


# --------------------------------------------------------------------- pinned fetching


def test_fetch_gold_with_a_pin_reads_the_pinned_archive_not_the_current_one(tmp_path):
    fetcher = PinnedFetcher()
    book, results = fetch_gold(
        "physics",
        2,
        tmp_path,
        fetcher,
        retrieved_at="2026-10-06",
        allow_retired=True,
        pin=PINS["physics"],
    )
    assert (book.version, book.archive_version, book.state) == ("v0", PINNED, "retired")
    assert openstax.RELEASE_URL not in fetcher.calls
    assert all(PINNED in url for url in fetcher.calls if "/contents/" in url)
    record = json.loads(results[0].path.read_text(encoding="utf-8").splitlines()[0])
    assert (record["archive_version"], record["book_version"]) == (PINNED, "v0")
    assert record["content_url"].startswith(f"{PINNED_URL}:")
    meta = json.loads((tmp_path / "openstax/physics/_book.json").read_text(encoding="utf-8"))
    assert (meta["archive_version"], meta["version"]) == (PINNED, "v0")


def test_without_a_pin_the_current_archive_is_read(tmp_path):
    fetcher = PinnedFetcher()
    fetcher.responses[f"https://openstax.org{CURRENT_ARCHIVE}/contents/book-uuid@v9.json"] = (
        fetcher.responses[f"{PINNED_URL}.json"]
    )
    book = openstax.resolve_book("physics", fetcher)
    assert (book.version, book.archive_version) == ("v9", "20990101.000000")


def test_a_pinned_archive_that_cannot_be_read_says_so(tmp_path):
    fetcher = PinnedFetcher(missing=(f"{PINNED_URL}.json",))
    with pytest.raises(GoldError, match=r"pinned archive 20250101\.000000"):
        fetch_gold("physics", 1, tmp_path, fetcher, allow_retired=True, pin=PINS["physics"])
    assert not (tmp_path / "openstax").exists()


def test_fetch_set_writes_every_record_kind_with_attribution(tmp_path):
    gold, registration = fetched(tmp_path)
    manifest = json.loads((gold / "manifest.json").read_text(encoding="utf-8"))
    assert [Path(f["path"]).stem for f in manifest["files"]] == sorted(SLUGS)
    # Equations, figures (alt text and caption), tables and headings, as data/gold has them.
    assert set(manifest["totals"]) == {"equation", "figure", "table", "heading"}
    assert manifest["books"]["physics"]["state"] == "retired"
    attribution = (gold / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "Senior Author" in attribution and CC_BY in attribution
    assert "not affiliated with or endorsed by OpenStax" in attribution
    assert verify_gold(gold, registration) == []


def test_fetch_set_needs_the_retired_opt_in_and_writes_nothing_without_it(tmp_path):
    with pytest.raises(BookStateError, match=r"0001-scope\.md"):
        fetch_set(
            tmp_path / "gold",
            PinnedFetcher(),
            retrieved_at="2026-10-06",
            registration=draft("2026-10-06"),
        )
    assert not (tmp_path / "gold").exists()


def test_fetch_set_still_refuses_a_book_that_is_not_cc_by(tmp_path):
    fetcher = PinnedFetcher()
    fetcher.responses[openstax.CMS_BOOKS_URL]["items"][0]["license_url"] = (
        "https://creativecommons.org/licenses/by-nc-sa/4.0/"
    )
    with pytest.raises(openstax.LicenseError, match="not CC BY"):
        fetch_set(
            tmp_path / "gold",
            fetcher,
            retrieved_at="2026-10-06",
            allow_retired=True,
            registration=draft("2026-10-06"),
        )
    assert not (tmp_path / "gold").exists()


# ----------------------------------------------------------------------- the content hash


def test_content_hash_ignores_retrieved_at_and_key_order_but_not_content(tmp_path):
    a = {"id": "x", "kind": "equation", "mathml": "<math/>", "retrieved_at": "2026-10-06"}
    b = {"mathml": "<math/>", "retrieved_at": "2027-01-01", "kind": "equation", "id": "x"}
    c = a | {"mathml": "<math><mi>y</mi></math>"}
    paths = []
    for name, record in (("a", a), ("b", b), ("c", c)):
        paths.append(tmp_path / f"{name}.jsonl")
        paths[-1].write_text(json.dumps(record) + "\n\n", encoding="utf-8")
    assert content_sha256(paths[0]) == content_sha256(paths[1])
    assert content_sha256(paths[0]) != content_sha256(paths[2])
    assert file_sha256(paths[0]) != file_sha256(paths[1])


# ---------------------------------------------------------------------------- verifying


@pytest.fixture
def registered(tmp_path):
    return fetched(tmp_path)


def edit_first_line(path: Path, **changes) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = json.dumps(json.loads(lines[0]) | changes, ensure_ascii=False, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_the_registered_gold_verifies(registered):
    gold, registration = registered
    assert verify_gold(gold, registration) == []
    require_gold(gold, registration)  # does not raise


def test_a_missing_folder_or_file_is_reported(registered, tmp_path):
    gold, registration = registered
    assert "not found" in verify_gold(tmp_path / "nope", registration)[0]
    (gold / "openstax/physics" / f"{SLUGS[0]}.jsonl").unlink()
    (problem, *_) = verify_gold(gold, registration)
    assert problem.startswith("1 registered file(s) missing") and SLUGS[0] in problem


def test_a_changed_record_is_a_content_difference(registered):
    gold, registration = registered
    edit_first_line(gold / "openstax/physics" / f"{SLUGS[1]}.jsonl", mathml="<math/>")
    problems = verify_gold(gold, registration)
    assert any("1 file(s) differ in content" in p and SLUGS[1] in p for p in problems)
    assert not any("retrieved_at" in p for p in problems)


def test_fetching_on_another_day_is_named_and_the_date_to_use_is_given(tmp_path):
    _, registration = fetched(tmp_path / "day-one", "2026-10-06")
    later, _ = fetched(tmp_path / "day-two", "2026-12-01")
    problems = verify_gold(later, registration)
    assert any(
        "2 file(s) differ from the registered ones only in retrieved_at" in p
        and "--date 2026-10-06" in p
        for p in problems
    )
    assert not any("differ in content" in p for p in problems)
    assert any(p.startswith("manifest.json is not the registered one") for p in problems)
    # A book file that matches its folder's own manifest is not a further problem.
    assert not any("book file" in p for p in problems)


def test_a_gold_file_outside_the_registered_set_is_refused(registered):
    gold, registration = registered
    stray = gold / "openstax" / "physics" / "2-1-third-section.jsonl"
    stray.write_text((gold / "openstax/physics" / f"{SLUGS[0]}.jsonl").read_text("utf-8"))
    (problem, *_) = verify_gold(gold, registration)
    assert problem.startswith("1 file(s) outside the registered set") and "2-1-third" in problem
    # The loaders read every .jsonl below the folder, so a stray one anywhere counts.
    nested = gold / "notes" / "extra.jsonl"
    nested.parent.mkdir()
    nested.write_text(stray.read_text("utf-8"), encoding="utf-8")
    (problem, *_) = verify_gold(gold, registration)
    assert problem.startswith("2 file(s) outside the registered set") and "notes/extra" in problem


def test_manifest_attribution_and_book_files_are_checked(registered):
    gold, registration = registered
    (gold / "ATTRIBUTION.md").write_text("changed\n", encoding="utf-8")
    manifest = json.loads((gold / "manifest.json").read_text(encoding="utf-8"))
    manifest["books"]["physics"]["title"] = "Other"
    (gold / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    problems = verify_gold(gold, registration)
    assert any(p.startswith("manifest.json is not the registered one") for p in problems)
    assert any(p.startswith("ATTRIBUTION.md is not the registered one") for p in problems)
    assert any("1 book file(s) differ from the manifest" in p for p in problems)


def test_missing_manifest_attribution_and_an_unreadable_book_file(registered):
    gold, registration = registered
    (gold / "openstax/physics/_book.json").write_text("not json", encoding="utf-8")
    assert any("1 book file(s) missing or not JSON" in p for p in verify_gold(gold, registration))
    (gold / "manifest.json").unlink()
    (gold / "ATTRIBUTION.md").unlink()
    problems = verify_gold(gold, registration)
    assert "manifest.json is missing" in problems and "ATTRIBUTION.md is missing" in problems


def test_require_gold_raises_with_every_problem(registered):
    gold, registration = registered
    edit_first_line(gold / "openstax/physics" / f"{SLUGS[0]}.jsonl", mathml="<math/>")
    with pytest.raises(HeldoutError, match="not the pre-registered held-out gold") as caught:
        require_gold(gold, registration)
    assert SLUGS[0] in str(caught.value)
    assert isinstance(caught.value, GoldError)


def test_long_lists_of_files_are_shortened(tmp_path):
    names = [f"openstax/physics/{n}.jsonl" for n in range(7)]
    shown = heldout._listed(names)
    assert shown.endswith("and 3 more") and "6.jsonl" not in shown


# ---------------------------------------------------------------- the CLI: gold commands


@pytest.fixture
def synthetic(monkeypatch, tmp_path):
    """The synthetic set standing in for the registered one, and a fake archive."""
    _, registration = fetched(tmp_path / "reference")
    monkeypatch.setattr(heldout, "REGISTERED", registration)
    monkeypatch.setattr(openstax, "HttpFetcher", PinnedFetcher)
    PinnedFetcher.made = []
    monkeypatch.chdir(REPO)
    return registration


def test_parser_offers_the_set_on_gold_and_eval_commands():
    parser = build_parser()
    args = parser.parse_args(["gold", "fetch", "--set", "heldout", "--allow-retired"])
    assert (args.set, args.allow_retired, args.heldout_dir) == ("heldout", True, None)
    args = parser.parse_args(["eval", "pipeline", "--task", "equations", "--set", "heldout"])
    assert (args.gold_set, args.fake) == ("heldout", False)
    for command in (["eval", "equations", "--self-check"], ["eval", "render", "--page", "x"]):
        assert parser.parse_args([*command, "--set", "heldout"]).gold_set == "heldout"
        assert parser.parse_args(command).gold_set is None
    for bad in (["gold", "fetch", "--set", "other"], ["gold", "verify"]):
        with pytest.raises(SystemExit):
            parser.parse_args(bad)


def test_gold_fetch_set_heldout_writes_outside_the_repository_and_checks_the_hashes(
    synthetic, tmp_path, capsys
):
    held = tmp_path / "held"
    argv = ["gold", "fetch", "--set", "heldout", "--allow-retired", "--heldout-dir", str(held)]
    assert main([*argv, "--date", synthetic.date]) == 0
    out = capsys.readouterr().out
    assert (
        f"matches the pre-registered held-out gold (manifest sha256 {synthetic.manifest_sha256}"
        in out
    )
    assert (held / "gold" / "manifest.json").is_file()
    (fetcher,) = PinnedFetcher.made
    assert fetcher.cache_dir == held / "openstax"  # the archive responses, kept apart
    assert verify_gold(held / "gold") == []


def test_gold_fetch_set_heldout_on_another_day_writes_the_files_but_fails_the_check(
    synthetic, tmp_path, capsys
):
    held = tmp_path / "held"
    argv = ["gold", "fetch", "--set", "heldout", "--allow-retired", "--heldout-dir", str(held)]
    assert main([*argv, "--date", "2027-01-01"]) == 1
    out = capsys.readouterr().out
    assert "not the registered held-out gold" in out and f"--date {synthetic.date}" in out
    assert (held / "gold" / "manifest.json").is_file()


def test_gold_fetch_set_heldout_needs_allow_retired(synthetic, tmp_path, capsys):
    argv = ["gold", "fetch", "--set", "heldout", "--heldout-dir", str(tmp_path / "held")]
    assert main(argv) == 2
    assert "--allow-retired" in capsys.readouterr().err
    assert not (tmp_path / "held" / "gold").exists()


def test_gold_fetch_set_heldout_refuses_a_destination_inside_the_repository(
    synthetic, tmp_path, capsys
):
    for argv in (
        ["--out", str(REPO / "data" / "gold" / "heldout")],
        ["--out", "data/gold"],
        ["--heldout-dir", str(REPO / ".cache" / "heldout")],
    ):
        assert main(["gold", "fetch", "--set", "heldout", "--allow-retired", *argv]) == 2
        assert "is inside the repository" in capsys.readouterr().err
    assert not (REPO / "data" / "gold" / "heldout").exists()
    assert main(["gold", "fetch", "--set", "heldout", "--page", "x"]) == 2


def test_gold_verify_reports_the_registered_gold_and_what_differs(synthetic, tmp_path, capsys):
    held = tmp_path / "held"
    fetch = ["gold", "fetch", "--set", "heldout", "--allow-retired", "--heldout-dir", str(held)]
    assert main([*fetch, "--date", synthetic.date]) == 0
    capsys.readouterr()
    verify = ["gold", "verify", "--set", "heldout", "--heldout-dir", str(held)]
    assert main(verify) == 0
    assert "is the pre-registered held-out gold: 2 sections" in capsys.readouterr().out
    edit_first_line(held / "gold/openstax/physics" / f"{SLUGS[0]}.jsonl", mathml="<math/>")
    assert main(verify) == 1
    err = capsys.readouterr().err
    assert "differ in content" in err and "gold fetch --set heldout" in err
    assert main(["gold", "verify", "--set", "heldout", "--gold", str(REPO / "data" / "gold")]) == 1


# --------------------------------------------------------------- the CLI: eval commands


class Media:
    """Stands in for CachedMedia; remembers where the run was told to cache."""

    built: ClassVar[list[tuple[Path, Path, str, Path | None]]] = []

    def __init__(self, gold_dir, cache_dir, fetcher, *, paper="Letter") -> None:
        Media.built.append((gold_dir, cache_dir, paper, fetcher.cache_dir))
        self.media = FakeMedia()

    def __enter__(self):
        return self.media

    def __exit__(self, *exc):
        return None


class StubClient(FakeVisionClient):
    def __init__(self, settings, budget, *, client=None, verifier=None):
        super().__init__(budget=budget)


@pytest.fixture
def eval_set(monkeypatch, tmp_path):
    """A one-book, two-page registered set holding equations, with every model call
    replaced (the stub client refuses all of them at zero cost)."""
    gold = write_gold(
        tmp_path / "held",
        [
            equation("p-1", 0, EQ_V, "block"),
            equation("p-1", 1, EQ_X, "inline"),
            equation("p-2", 0, EQ_A, "block"),
        ],
    )
    registration = registration_of(
        gold,
        "2026-10-13",
        {"physics": BookPin("20990101.000000", "v1")},
        pdf_pages={"p-1": 2, "p-2": 3},
    )
    monkeypatch.setattr(heldout, "REGISTERED", registration)
    monkeypatch.setattr(eval_cli, "_settings", lambda: Settings(_env_file=None, openai_api_key="k"))
    from readablestem.models import openai_client

    monkeypatch.setattr(openai_client, "OpenAIVisionClient", StubClient)
    Media.built = []
    monkeypatch.setattr(pipeline_eval, "CachedMedia", Media)
    monkeypatch.setattr(equation_step, "transcribe", fake_transcribe)
    monkeypatch.chdir(REPO)
    return tmp_path / "held", registration


def pipeline_argv(held: Path, *extra: str) -> list[str]:
    return [
        *("eval", "pipeline", "--task", "equations", "--set", "heldout"),
        *("--heldout-dir", str(held), "--date", "2026-10-14", *extra),
    ]


def test_eval_pipeline_set_heldout_uses_the_registered_gold_and_keeps_files_outside(
    eval_set, tmp_path
):
    held, registration = eval_set
    assert main(pipeline_argv(held, "--max-usd", "1")) == 0
    report = json.loads((held / "reports" / "equations.json").read_text(encoding="utf-8"))
    assert report["set"] == "heldout"
    assert report["preregistration_sha256"] == registration.preregistration_sha256
    assert report["dataset"]["manifest_sha256"] == registration.manifest_sha256
    assert report["dataset"]["gold_dir"] == "heldout (pre-registered 2026-10-13)"
    assert (report["n"], report["complete"], report["client"]) == (3, True, "openai")
    ((gold_dir, cache_dir, paper, openstax_cache),) = Media.built
    assert (gold_dir, cache_dir, paper) == (held / "gold", held / "eval-cache", "Letter")
    assert openstax_cache == held / "openstax"  # not the repository's .cache/openstax


def test_eval_pipeline_set_heldout_accepts_other_places_outside_the_repository(eval_set, tmp_path):
    held, _ = eval_set
    out, items, cache = tmp_path / "r.json", tmp_path / "i.jsonl", tmp_path / "c"
    argv = pipeline_argv(held, "--max-usd", "1", "--out", str(out), "--items", str(items))
    assert main([*argv, "--cache", str(cache)]) == 0
    assert out.is_file() and items.is_file()
    assert Media.built[0][1] == cache


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--fake"], "--fake is not allowed on the held-out set"),
        (["--max-usd", "1", "--notation", "bold d"], "--notation is not part"),
        (["--max-usd", "1", "--cache", "data/cache"], "--cache data/cache is inside"),
        (["--max-usd", "1", "--out", "data/eval/latest-equations.json"], "--out data/eval"),
        (["--max-usd", "1", "--items", "items.jsonl"], "--items items.jsonl is inside"),
        (["--max-usd", "1", "--gold", "data/gold"], "not the pre-registered held-out gold"),
    ],
)
def test_eval_pipeline_set_heldout_refuses_what_the_pre_registration_forbids(
    eval_set, monkeypatch, capsys, extra, message
):
    held, _ = eval_set

    def must_not_run(*args, **kwargs):
        raise AssertionError("the run started")

    monkeypatch.setattr(eval_cli, "_run_pipeline", must_not_run)
    assert main(pipeline_argv(held, *extra)) == 2
    assert message in capsys.readouterr().err
    assert not (held / "reports").exists()


def test_eval_pipeline_set_heldout_refuses_the_tables_task_and_unregistered_gold(eval_set, capsys):
    held, _ = eval_set
    argv = pipeline_argv(held, "--max-usd", "1")
    argv[argv.index("equations")] = "tables"
    assert main(argv) == 2
    assert "tables task is not part" in capsys.readouterr().err
    edit_first_line(held / "gold/openstax/physics/p-1.jsonl", mathml=EQ_A)
    assert main(pipeline_argv(held, "--max-usd", "1")) == 2
    assert "differ in content" in capsys.readouterr().err
    assert Media.built == []


@pytest.mark.parametrize(
    "command",
    [
        ["eval", "pipeline", "--task", "equations", "--max-usd", "1"],
        ["eval", "pipeline", "--task", "headings", "--fake"],
        ["eval", "sample", "--task", "equations", "--fake"],
        ["eval", "render", "--page", "p-1"],
        ["eval", "calibrate", "--probes", "--fake"],
        ["demo", "build", "--page", "p-1", "--fake"],
    ],
)
def test_held_out_gold_given_by_path_needs_the_set(eval_set, monkeypatch, capsys, command):
    """Without --set a command would cache and report inside the repository."""
    held, _ = eval_set

    def must_not_run(*args, **kwargs):
        raise AssertionError("the run started")

    monkeypatch.setattr(eval_cli, "_run_pipeline", must_not_run)
    monkeypatch.setattr(eval_cli, "_make_client", must_not_run)
    assert main([*command, "--gold", str(held / "gold")]) == 2
    assert "Add --set heldout" in capsys.readouterr().err


def test_other_gold_is_not_taken_for_held_out_gold(eval_set, tmp_path):
    assert heldout_cli.unselected(tmp_path) is None
    assert heldout_cli.unselected(tmp_path / "missing") is None
    assert heldout_cli.unselected(eval_set[0] / "gold") is not None


def test_the_default_run_without_the_set_is_unchanged(eval_set, tmp_path):
    dev_gold = write_gold(tmp_path / "dev", [equation("q-1", 0, EQ_V, "block")])
    out = tmp_path / "plain.json"
    argv = ["eval", "pipeline", "--task", "equations", "--gold", str(dev_gold)]
    assert main([*argv, "--out", str(out), "--cache", str(tmp_path / "c"), "--fake"]) == 0
    assert "set" not in json.loads(out.read_text(encoding="utf-8"))
    assert Media.built[0][3] == REPO / ".cache" / "openstax"


def test_eval_equations_set_heldout_scores_against_the_registered_gold(eval_set, capsys):
    held, _ = eval_set
    argv = ["eval", "equations", "--set", "heldout", "--heldout-dir", str(held), "--self-check"]
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)["n"] == 3
    edit_first_line(held / "gold/openstax/physics/p-1.jsonl", mathml=EQ_A)
    assert main(argv) == 2
    assert "not the pre-registered held-out gold" in capsys.readouterr().err
    assert main([*argv, "--report", "report.json"]) == 2
    assert not (REPO / "report.json").exists()


# ----------------------------------------------------------- the CLI: printing the sections


def pdf_of(pages: int) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    return doc.tobytes()


class Browser:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


@pytest.fixture
def printed(eval_set, monkeypatch):
    """`render` with the browser replaced: each page prints to a PDF of a chosen length."""
    held, registration = eval_set
    lengths = {s.slug: s.pdf_pages for s in registration.sections}
    calls: list[tuple] = []

    def page_source(book, slug, gold_dir, fetcher):
        calls.append((book, slug, gold_dir, fetcher.cache_dir))
        return slug

    def render_page_pdf(page, out_path=None, *, paper="Letter", cache_dir=None, **kwargs):
        pdf = pdf_of(lengths[page])
        calls.append(("print", page, paper, cache_dir))
        if out_path is not None:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(pdf)
        return pdf

    monkeypatch.setattr(eval_cli, "_page_source", page_source)
    monkeypatch.setattr(render, "ChromiumRenderer", Browser)
    monkeypatch.setattr(render, "render_page_pdf", render_page_pdf)
    return held, registration, lengths, calls


def test_render_all_prints_every_section_and_checks_the_page_counts(printed, capsys):
    held, _, _, calls = printed
    assert main(["eval", "render", "--set", "heldout", "--all", "--heldout-dir", str(held)]) == 0
    out = capsys.readouterr().out
    assert "5 PDF pages in 2 sections (registered: 5)" in out
    summary = json.loads((held / "pdfs" / "pages.json").read_text(encoding="utf-8"))
    assert (summary["total_pdf_pages"], summary["pages_match_registration"]) == (5, True)
    rows = {row["page"]: row for row in summary["sections"]}
    assert rows["p-1"]["pdf_pages"] == 2 and rows["p-2"]["pdf_pages"] == 3
    pdf = (held / "pdfs" / "physics-p-1.pdf").read_bytes()
    assert rows["p-1"]["sha256"] == hashlib.sha256(pdf).hexdigest()
    # The prints are cached where the document task looks for them, and read from the
    # held-out folder's own archive cache.
    assert ("print", "p-1", "Letter", held / "eval-cache" / "pages") in calls
    assert ("physics", "p-1", held / "gold", held / "openstax") in calls


def test_render_all_fails_when_a_page_count_differs_from_the_registered_one(printed, capsys):
    held, _, lengths, _ = printed
    lengths["p-2"] = 4
    assert main(["eval", "render", "--set", "heldout", "--all", "--heldout-dir", str(held)]) == 1
    out = capsys.readouterr().out
    assert "(registered 3)" in out and "another page count than registered" in out
    summary = json.loads((held / "pdfs" / "pages.json").read_text(encoding="utf-8"))
    assert summary["pages_match_registration"] is False


def test_render_all_on_another_paper_does_not_compare_page_counts(printed):
    held, _, lengths, _ = printed
    lengths["p-2"] = 4
    argv = ["eval", "render", "--set", "heldout", "--all", "--paper", "A4"]
    assert main([*argv, "--heldout-dir", str(held)]) == 0
    summary = json.loads((held / "pdfs" / "pages.json").read_text(encoding="utf-8"))
    assert summary["pages_match_registration"] is None


def test_render_page_finds_the_book_of_a_held_out_section(printed):
    held, _, _, calls = printed
    argv = ["eval", "render", "--set", "heldout", "--heldout-dir", str(held)]
    assert main([*argv, "--page", "p-2"]) == 0
    assert calls[0] == ("physics", "p-2", held / "gold", held / "openstax")
    assert (held / "eval-cache" / "pages" / "physics-p-2.pdf").is_file()
    assert main([*argv, "--page", "p-2", "--book", "chemistry"]) == 2
    assert main([*argv, "--page", "2-1-relative-motion-distance-and-displacement"]) == 2


def test_render_all_and_out_dir_need_the_set(printed, capsys):
    assert main(["eval", "render", "--all"]) == 2
    assert main(["eval", "render", "--page", "x", "--out-dir", "somewhere"]) == 2
    assert "belong to --set heldout" in capsys.readouterr().err


# ------------------------------------------------------------------------ small helpers


def test_default_folder_follows_xdg_cache_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert heldout_cli.default_root() == tmp_path / "readablestem" / "heldout"
    loc = heldout_cli.locations(None)
    assert loc.gold == tmp_path / "readablestem" / "heldout" / "gold"
    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert heldout_cli.default_root() == tmp_path / "home" / ".cache" / "readablestem" / "heldout"


def test_a_cache_folder_under_the_old_name_is_still_used_while_it_is_the_only_one(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    old = tmp_path / "a11y-stem" / "heldout"
    old.mkdir(parents=True)
    assert heldout_cli.default_root() == old
    (tmp_path / "readablestem" / "heldout").mkdir(parents=True)
    assert heldout_cli.default_root() == tmp_path / "readablestem" / "heldout"


def test_inside_the_repository_means_the_checkout_this_runs_in(monkeypatch, tmp_path):
    monkeypatch.chdir(REPO / "tests")
    assert heldout_cli.outside_project(REPO / "data" / "eval", "--out") is not None
    assert heldout_cli.outside_project(REPO, "--out") is not None
    assert heldout_cli.outside_project(tmp_path, "--out") is None
    monkeypatch.chdir(tmp_path)  # not a checkout: nothing is inside it
    assert heldout_cli.checkout_root() is None
    assert heldout_cli.outside_project(tmp_path / "x", "--out") is None


def test_the_dataclasses_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        REGISTERED.date = "2030-01-01"  # type: ignore[misc]


# ----------------------------------------------------------------------------- live


@pytest.mark.network
def test_live_refetch_reproduces_the_registered_gold_byte_for_byte(tmp_path):
    """Rebuilds the 15 files from OpenStax's pinned archive (about 25 requests) and
    compares them with the hashes registered on 2026-10-13."""
    fetcher = openstax.HttpFetcher(cache_dir=tmp_path / "cache")
    fetch_set(tmp_path / "gold", fetcher, retrieved_at=REGISTERED.date, allow_retired=True)
    assert verify_gold(tmp_path / "gold") == []
