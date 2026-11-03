"""Selection of the fifth fresh real-world split: seeded determinism and the licence gate
(`scripts/fresh5_select.py`, `readablestem.gold.course_corpus`). Offline: no network, no model, no
TeX engine."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from readablestem.gold import course_corpus as cc

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fresh5_select.py"


@pytest.fixture(scope="module")
def sel():
    spec = importlib.util.spec_from_file_location("fresh5_select_for_tests", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------------------- licence


@pytest.mark.parametrize(
    ("spdx", "name"),
    [
        ("CC-BY-4.0", "CC BY 4.0"),
        ("CC-BY-3.0", "CC BY 3.0"),
        ("CC-BY-SA-4.0", "CC BY-SA 4.0"),
        ("CC-BY-SA-3.0", "CC BY-SA 3.0"),
        ("MIT", "MIT"),
    ],
)
def test_licence_gate_accepts_the_registered_licences(spdx, name):
    found = cc.licence_gate(spdx)
    assert found is not None and found.name == name


@pytest.mark.parametrize(
    "spdx",
    [
        None,
        "",
        "NOASSERTION",
        "CC-BY-NC-4.0",
        "CC-BY-NC-SA-4.0",
        "CC-BY-ND-4.0",
        "GPL-3.0",
        "other",
    ],
)
def test_licence_gate_refuses_everything_else(spdx):
    assert cc.licence_gate(spdx) is None
    with pytest.raises(cc.Skip) as info:
        cc.require_licence(spdx)
    assert info.value.code == "licence_refused"


def item(name: str, spdx: str | None, **extra) -> dict:
    base = {
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "default_branch": "main",
        "size": 500,
        "stargazers_count": 3,
        "pushed_at": "2025-01-01T00:00:00Z",
        "license": {"spdx_id": spdx} if spdx else None,
    }
    return base | extra


def test_search_result_is_kept_only_with_an_accepted_licence():
    assert cc.parse_repo(item("a/b", "MIT"), "q") is not None
    assert cc.parse_repo(item("a/b", "CC-BY-NC-4.0"), "q") is None
    assert cc.parse_repo(item("a/b", None), "q") is None
    assert cc.parse_repo(item("a/b", "NOASSERTION"), "q") is None


def test_forks_archived_and_huge_repositories_are_out():
    assert cc.parse_repo(item("a/b", "MIT", fork=True), "q") is None
    assert cc.parse_repo(item("a/b", "MIT", archived=True), "q") is None
    assert cc.parse_repo(item("a/b", "MIT", size=cc.MAX_REPO_KB + 1), "q") is None


def test_query_set_is_fixed_and_names_only_supported_licences():
    queries = cc.search_queries()
    assert len(queries) == (len(cc.TOPIC_PHRASES) + len(cc.TOPICS)) * len(cc.LICENCE_QUALIFIERS)
    assert queries == cc.search_queries()
    assert any("beamer slides" in q for _, q in queries)
    assert all("language:TeX" in q and "fork:false" in q for _, q in queries)
    assert not any("-nc" in q for _, q in queries)


def test_merge_joins_queries_of_the_same_repository():
    a = cc.parse_repo(item("O/R", "MIT"), "one")
    b = cc.parse_repo(item("o/r", "MIT"), "two")
    c = cc.parse_repo(item("x/y", "MIT"), "one")
    merged = cc.merge_repos([a, b, c])
    assert [r.full_name for r in merged] == ["O/R", "x/y"]
    assert merged[0].queries == ["one", "two"]


# ------------------------------------------------------------------- course documents


NOTES = "\\documentclass{article}\n\\begin{document}\n$x$\n\\end{document}\n"
DECK = "\\documentclass[10pt]{beamer}\n\\begin{document}\n\\end{document}\n"
DECK2 = "\\documentclass{article}\n\\usepackage{beamer}\n\\begin{document}\n\\end{document}\n"
PART = "\\section{x} $y$\n"


def test_discover_finds_documents_and_beamer_decks():
    sources = {"notes.tex": NOTES, "slides/deck.tex": DECK, "d2.tex": DECK2, "chap.tex": PART}
    docs = {d.path: d.beamer for d in cc.discover(sources)}
    assert docs == {"notes.tex": False, "slides/deck.tex": True, "d2.tex": True}


def test_sources_for_drops_the_other_documents():
    sources = {"notes.tex": NOTES, "deck.tex": DECK, "chap.tex": PART}
    assert set(cc.sources_for(sources, "notes.tex")) == {"notes.tex", "chap.tex"}


def test_document_order_is_seeded_and_stable():
    paths = [f"lec{i}.tex" for i in range(12)]
    first = cc.order_documents(paths, "seed-a", "o/r")
    assert first == cc.order_documents(reversed(paths), "seed-a", "o/r")
    assert first != cc.order_documents(paths, "seed-b", "o/r")
    assert sorted(first) == sorted(paths)


def test_repo_order_ignores_input_order_and_case_of_the_name():
    repos = [cc.parse_repo(item(f"o/r{i}", "MIT"), "q") for i in range(20)]
    a = [r.full_name for r in cc.order_repos(repos, "s")]
    b = [r.full_name for r in cc.order_repos(list(reversed(repos)), "s")]
    assert a == b


def fake_documents(plan: dict[str, list[cc.Doc]], failing: set[str] = frozenset()):
    def documents(repo: cc.Repo):
        def build(doc: cc.Doc) -> dict:
            if doc.path in failing:
                raise cc.Skip("compile_failed")
            return {"repo": repo.full_name, "path": doc.path, "beamer": doc.beamer}

        return plan[repo.full_name], build

    return documents


def pool_and_plan(n_repos: int, beamer_every: int):
    repos, plan = [], {}
    for i in range(n_repos):
        repo = cc.parse_repo(item(f"o/r{i:02d}", "MIT"), "q")
        repos.append(repo)
        plan[repo.full_name] = [
            cc.Doc(f"d{j}.tex", beamer=(i + j) % beamer_every == 0) for j in range(3)
        ]
    return repos, plan


def test_draw_is_deterministic_and_respects_caps():
    repos, plan = pool_and_plan(30, beamer_every=3)
    runs = []
    for _ in range(2):
        log: list[dict] = []
        got = cc.draw(repos, "seed", fake_documents(plan), log)
        runs.append(([(e["repo"], e["path"]) for e in got], log))
    assert runs[0] == runs[1]
    accepted = runs[0][0]
    assert len(accepted) == cc.TARGET
    per_repo = {}
    for repo, _ in accepted:
        per_repo[repo] = per_repo.get(repo, 0) + 1
    assert max(per_repo.values()) <= cc.PER_REPO


def test_draw_reaches_the_beamer_minimum_when_decks_are_scarce():
    repos, plan = pool_and_plan(40, beamer_every=7)
    got = cc.draw(repos, "seed", fake_documents(plan), [])
    assert len(got) == cc.TARGET
    assert sum(1 for e in got if e["beamer"]) >= cc.MIN_BEAMER


def test_draw_logs_a_failed_document_and_never_swaps_it_by_choice():
    repos, plan = pool_and_plan(10, beamer_every=2)
    log: list[dict] = []
    first = cc.draw(repos, "seed", fake_documents(plan), [])
    dropped = first[0]["path"]
    cc.draw(repos, "seed", fake_documents(plan, failing={dropped}), log)
    skipped = [v for v in log if v["outcome"] == "skipped" and v.get("reason") == "compile_failed"]
    assert skipped
    assert {v["path"] for v in skipped} >= {dropped}


def test_draw_skips_a_repository_that_cannot_be_fetched():
    repos, plan = pool_and_plan(6, beamer_every=2)
    first = cc.order_repos(repos, "seed")[0].full_name

    def documents(repo: cc.Repo):
        if repo.full_name == first:
            raise cc.Skip("licence_refused", "NOASSERTION")
        return fake_documents(plan)(repo)

    log: list[dict] = []
    got = cc.draw(repos, "seed", documents, log)
    assert all(e["repo"] != first for e in got)
    assert {
        "repo": first,
        "outcome": "skipped",
        "reason": "licence_refused",
        "detail": "NOASSERTION",
    } in log


def test_draw_tries_at_most_visits_per_repo_documents_in_one_repository():
    repo = cc.parse_repo(item("o/big", "MIT"), "q")
    docs = [cc.Doc(f"d{j:03d}.tex", beamer=False) for j in range(50)]
    plan = {"o/big": docs}
    log: list[dict] = []
    got = cc.draw([repo], "seed", fake_documents(plan, failing={d.path for d in docs}), log)
    assert got == []
    assert len(log) == cc.VISITS_PER_REPO


def test_compile_falls_back_to_the_next_engine_and_records_it(tmp_path, monkeypatch):
    """Fallback order and the cache only; the sandbox itself is tested in test_safe_tex.py."""
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(cc, "engines", lambda: [("pdflatex", "pdfTeX 1"), ("xelatex", "XeTeX 2")])
    tried: list[str] = []

    def fake_compile(src, main, out, engine, *, timeout):
        tried.append(engine)
        ok = engine == "xelatex"
        if ok:
            (out / "a.pdf").write_bytes(b"%PDF")
        return cc.safe_tex.Result(0 if ok else 1, False)

    monkeypatch.setattr(cc.safe_tex, "compile_tex", fake_compile)
    pdf, label = cc.compile_document(src, "a.tex", tmp_path / "out")
    assert tried == ["pdflatex", "xelatex"]
    assert label == "xelatex: XeTeX 2" and pdf.is_file()
    # a second call is answered from the cache, with the same engine
    assert cc.compile_document(src, "a.tex", tmp_path / "out")[1] == label
    assert tried == ["pdflatex", "xelatex"]


def test_compile_reports_failure_and_timeout_by_category(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(cc, "engines", lambda: [("pdflatex", "v")])
    result = {"value": cc.safe_tex.Result(1, False)}
    monkeypatch.setattr(cc.safe_tex, "compile_tex", lambda *a, **k: result["value"])
    with pytest.raises(cc.Skip) as info:
        cc.compile_document(src, "a.tex", tmp_path / "out")
    assert info.value.code == "compile_failed"
    result["value"] = cc.safe_tex.Result(-9, True)
    with pytest.raises(cc.Skip) as info:
        cc.compile_document(src, "a.tex", tmp_path / "out2")
    assert info.value.code == "compile_timeout"


def test_a_missing_sandbox_stops_the_run_instead_of_skipping(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "engines", lambda: [("pdflatex", "v")])

    def refuse(*args, **kwargs):
        raise cc.safe_tex.SandboxUnavailable("bubblewrap (bwrap) is required")

    monkeypatch.setattr(cc.safe_tex, "compile_tex", refuse)
    with pytest.raises(cc.CourseError):
        cc.compile_document(tmp_path, "a.tex", tmp_path / "out")


def test_unit_id_has_no_extension():
    assert cc.unit_id("o/r", "notes/week1.tex") == "course/o/r/notes/week1"


# ----------------------------------------------------------------- stratum A: arXiv


def test_arxiv_order_continues_after_the_last_visited_candidate(sel):
    order = {"seed": "s", "pool_sha256": "h", "start_index": 175, "order": list("abcdefgh")}
    log = {"candidates": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    out = sel.continue_order(order, log)
    assert out["order"] == list("defgh")
    assert out["start_index"] == 178
    assert out["seed"] == "s" and out["pool_sha256"] == "h"


def test_arxiv_order_refuses_a_log_that_is_not_a_prefix(sel):
    order = {"seed": "s", "pool_sha256": "h", "order": list("abcdef")}
    with pytest.raises(ValueError):
        sel.continue_order(order, {"candidates": [{"id": "a"}, {"id": "c"}]})


def test_the_recorded_fresh4_log_is_a_prefix_of_its_order(sel):
    data = Path(__file__).resolve().parent.parent / "data" / "realworld"
    import json

    order = json.loads((data / sel.FRESH4_ORDER).read_text(encoding="utf-8"))
    log = json.loads((data / sel.FRESH4_CANDIDATES).read_text(encoding="utf-8"))
    out = sel.continue_order(order, log)
    assert out["order"][0] not in {c["id"] for c in log["candidates"]}
    assert out["order"] == order["order"][len(log["candidates"]) :]


# --------------------------------------------------------------- stratum B: OpenStax


def test_family_folds_editions_and_siblings(sel):
    assert sel.family("college-algebra-2e") == sel.family("college-algebra-corequisite-support")
    assert sel.family("principles-microeconomics-2e") == sel.family("principles-economics-2e")
    assert sel.family("statistics") == sel.family("introductory-statistics")
    assert sel.family("college-physics-ap-courses") == "college-physics"
    assert sel.family("astronomy") == "astronomy"


def test_used_sections_are_read_from_paths_and_proof_names(sel):
    text = (
        "./openstax/chemistry/15-1-precipitation-and-dissolution.jsonl\n"
        "proof/x/physics__2-3-position-vs-time-graphs.md\n"
    )
    assert sel.used_from_text(text) == {
        ("chemistry", "15-1-precipitation-and-dissolution"),
        ("physics", "2-3-position-vs-time-graphs"),
    }


def test_blocked_by_section_sibling_edition_or_sealed_family(sel):
    used = {("college-algebra", "3-2-linear-functions")}
    assert sel.blocked("college-algebra", "3-2-linear-functions", used, set())
    assert sel.blocked("college-algebra-2e", "3-2-linear-functions", used, set())
    assert sel.blocked("college-algebra-corequisite-support", "3-2-linear-functions", used, set())
    assert not sel.blocked("college-algebra", "3-3-other", used, set())
    assert sel.blocked("elementary-algebra", "1-1-x", used, {"elementary-algebra"})
    # the same title in another book is the same text
    assert sel.blocked("astronomy", "9-9-linear-functions", used, set())
    assert not sel.blocked("astronomy", "9-9-orbits", used, set())


def test_the_registered_heldout_sections_count_as_used(sel):
    root = Path(__file__).resolve().parent.parent
    used, families = sel.used_openstax(root)
    assert ("college-physics", "5-3-elasticity-stress-and-strain") in used
    assert "elementary-algebra" in families


def ranked_books():
    return {
        "astronomy": [f"{i}-1-a{i}" for i in range(1, 8)],
        "biology": [f"{i}-1-b{i}" for i in range(1, 8)],
        "physics": [f"{i}-1-p{i}" for i in range(1, 8)],
        "statistics": [f"{i}-1-s{i}" for i in range(1, 8)],
    }


def test_section_pick_is_deterministic_and_capped_per_book(sel):
    always = lambda book, slug: True  # noqa: E731
    first = sel.pick_sections(ranked_books(), "seed", always)
    again = sel.pick_sections(
        {k: list(v) for k, v in reversed(ranked_books().items())}, "seed", always
    )
    assert first == again
    assert len(first) == sel.OPENSTAX_TARGET
    per_book = {}
    for book, _ in first:
        per_book[book] = per_book.get(book, 0) + 1
    assert max(per_book.values()) <= sel.OPENSTAX_PER_BOOK


def test_section_pick_logs_a_failed_gate_and_moves_to_the_next_ranked(sel):
    ranked = ranked_books()
    failing = {("astronomy", ranked["astronomy"][0])}
    log: list[dict] = []
    got = sel.pick_sections(ranked, "seed", lambda b, s: (b, s) not in failing, log=log)
    assert ("astronomy", ranked["astronomy"][0]) not in got
    assert {"book": "astronomy", "section": ranked["astronomy"][0], "gate": "fail"} in log


def test_section_rank_depends_on_the_seed_only(sel):
    sections = [("physics", f"{i}-1-x") for i in range(10)]
    assert sel.order_sections(sections, "a") == sel.order_sections(reversed(sections), "a")
    assert sel.order_sections(sections, "a") != sel.order_sections(sections, "b")


# ------------------------------------------------------------- stratum D: chemistry


class FakeFetcher:
    """Serves a CMS list and one archive book JSON per slug; no network."""

    def __init__(self, books: dict[str, tuple[str, str]]):
        self.books = books  # slug -> (licence name, licence url)

    def get_json(self, url: str):
        from readablestem.gold import openstax as ox

        if url == ox.CMS_BOOKS_URL:
            return {
                "items": [
                    {
                        "title": slug,
                        "meta": {"slug": slug},
                        "cnx_id": f"uuid-{slug}",
                        "book_state": "retired",
                        "license_name": name,
                        "license_url": link,
                    }
                    for slug, (name, link) in self.books.items()
                ]
            }
        if url == ox.RELEASE_URL:
            return {
                "archiveUrl": "/apps/archive/20260604.144757",
                "books": {f"uuid-{s}": {"defaultVersion": "v1"} for s in self.books},
            }
        slug = url.rsplit("uuid-", 1)[1].split("@", 1)[0]
        name, link = self.books[slug]
        return {"title": slug, "license": {"name": name, "url": link}, "tree": {}}


CC_BY = ("Creative Commons Attribution License", "https://creativecommons.org/licenses/by/4.0/")
CC_BY_NC_SA = (
    "Creative Commons Attribution-NonCommercial-ShareAlike License",
    "https://creativecommons.org/licenses/by-nc-sa/4.0/",
)


def test_chemistry_licence_gate_takes_the_cc_by_editions_and_refuses_nc_sa():
    from readablestem.gold import openstax as ox

    fetcher = FakeFetcher(
        {
            "chemistry": CC_BY,
            "chemistry-atoms-first": CC_BY,
            "chemistry-2e": CC_BY_NC_SA,
            "chemistry-atoms-first-2e": CC_BY_NC_SA,
            "organic-chemistry": CC_BY_NC_SA,
        }
    )
    passed, refused = [], []
    for slug in fetcher.books:
        book = ox.resolve_book(slug, fetcher)
        try:
            ox.check_public_license(book)
            passed.append(slug)
        except ox.LicenseError:
            refused.append(slug)
    assert passed == ["chemistry", "chemistry-atoms-first"]
    assert refused == ["chemistry-2e", "chemistry-atoms-first-2e", "organic-chemistry"]


def test_chemistry_licence_gate_refuses_a_cms_that_disagrees_with_the_archive():
    from readablestem.gold import openstax as ox

    fetcher = FakeFetcher({"chemistry": CC_BY})
    book = ox.resolve_book("chemistry", fetcher)
    book.cms_license_url = CC_BY_NC_SA[1]  # the CMS changed its mind after the archive was cut
    with pytest.raises(ox.LicenseError):
        ox.check_public_license(book)


def test_the_recorded_chemistry_selection_has_only_cc_by_sections():
    import json

    data = Path(__file__).resolve().parent.parent / "data" / "realworld"
    selection = json.loads((data / "selection-fresh5-D.json").read_text(encoding="utf-8"))
    assert selection["sections"], "no section selected"
    for section in selection["sections"]:
        assert section["licence_url"].split("://")[1] == "creativecommons.org/licenses/by/4.0/"
        assert section["book"] in {"chemistry", "chemistry-atoms-first"}
        assert section["pdf_pages"] <= 15
        assert section["overlap_8gram"] <= 0.2
        assert section["equations"] >= 25 and section["chemistry"] >= 0.4 * section["equations"]
    assert {b["book"] for b in selection["books_refused"]} >= {"chemistry-2e", "organic-chemistry"}


def test_chemistry_order_is_seeded_and_ignores_input_order(sel):
    import hashlib

    sections = [("chemistry", f"{i}-1-a{i}") for i in range(1, 12)]
    sections += [("chemistry-atoms-first", f"{i}-1-a{i}") for i in range(1, 12)]
    first = sel.order_d(sections)
    assert first == sel.order_d(list(reversed(sections)))
    assert sel.rank_key_d("chemistry", "1-1-a1") != sel.rank_key_d(
        "chemistry-atoms-first", "1-1-a1"
    )
    assert (
        sel.rank_key_d("chemistry", "2-6-x")
        == hashlib.sha256(b"fresh5-D|chemistry|2-6-x").hexdigest()
    )
    ranks = [sel.rank_key_d(*s) for s in first]
    assert ranks == sorted(ranks)


def test_chemistry_pick_walks_the_order_and_logs_failures_without_swapping(sel):
    order = [("chemistry", f"{i}-1-s") for i in range(1, 9)]
    failing = {"2-1-s": "overlap", "3-1-s": "equations"}
    log: list[dict] = []
    got = sel.pick_chemistry(order, lambda b, s: failing.get(s), target=3, log=log)
    assert got == [("chemistry", "1-1-s"), ("chemistry", "4-1-s"), ("chemistry", "5-1-s")]
    assert [v["gate"] for v in log] == ["pass", "overlap", "equations", "pass", "pass"]
    # fewer eligible than the target: take what passes, do not fill up by choice
    assert sel.pick_chemistry(order, lambda b, s: "pages", target=3) == []


def test_chemistry_gate_names_the_first_failed_rule(sel):
    ok = {"equations": 30, "chemistry": 20, "lone_times": 3}
    assert sel.chemistry_gate(ok, 0.05, 10) is None
    assert sel.chemistry_gate({**ok, "equations": 24}, 0.0) == "equations"
    assert sel.chemistry_gate({**ok, "chemistry": 11}, 0.0) == "chemistry_share"
    assert sel.chemistry_gate({**ok, "chemistry": 12}, 0.0) is None  # 40% of 30
    assert sel.chemistry_gate(ok, 0.2001) == "overlap"
    assert sel.chemistry_gate(ok, 0.2) is None
    assert sel.chemistry_gate(ok, 0.0, 16) == "pages"
    assert sel.chemistry_gate(ok, 0.0, 15) is None


def test_chemistry_topics_and_overlap(sel):
    assert sel.topic_hit("17-2-galvanic-cells") == "galvanic"
    assert sel.topic_hit("4-1-writing-and-balancing-chemical-equations") == "balanc"
    assert sel.topic_hit("7-3-lewis-symbols-and-structures") == "lewis"
    assert sel.topic_hit("2-6-molecular-and-ionic-compounds") is None
    words = list("abcdefghij")
    reference = {tuple(words[i : i + 8]) for i in range(len(words) - 7)}
    assert sel.overlap_share(words, reference) == 1.0
    assert sel.overlap_share(list("qrstuvwxyz"), reference) == 0.0
    assert sel.overlap_share(list("abcdefghxy"), reference) == pytest.approx(1 / 3)


def test_the_sealed_check_part_is_a_superset_by_the_registered_hash_rule(sel):
    from readablestem.gold import devset
    from readablestem.gold import openstax as ox

    tree = {
        "contents": [
            {"id": f"u{i}@1", "slug": f"{i}-1-s{i}", "title": f"<span>{i}.1</span> S{i}"}
            for i in range(1, 61)
        ]
    }
    book = ox.Book(
        slug="chemistry-atoms-first",
        title="t",
        uuid="u",
        version="v",
        archive_path="/apps/archive/x",
        state="retired",
        license_name=None,
        license_url=None,
        cms_license_name=None,
        cms_license_url=None,
        tree=tree,
    )
    pages = [p for p in ox.iter_pages(book.tree) if p.section is not None]
    sealed = sel.sealed_check_sections({"chemistry-atoms-first": book}, "salt")
    assert sealed == {
        ("chemistry-atoms-first", p.slug)
        for p in pages
        if devset.hash_fraction("salt", f"chemistry-atoms-first/{p.slug}") < devset.CHECK_SHARE
    }


# the chemistry test: arrows, state symbols, formulas and ions, not a lone variable or sign
M = '<math xmlns="http://www.w3.org/1998/Math/MathML">{}</math>'


@pytest.mark.parametrize(
    ("body", "chem"),
    [
        ("<mi>x</mi><mo>=</mo><mn>2</mn>", False),
        ("<mi>K</mi><mo>=</mo><mn>3</mn>", False),
        ("<msub><mtext>H</mtext><mn>2</mn></msub><mtext>O</mtext>", True),
        ("<mtext>NaCl</mtext>", True),
        ("<msup><mtext>Cu</mtext><mrow><mn>2</mn><mo>+</mo></mrow></msup>", True),
        ("<mtext>C</mtext><mo>→</mo><mtext>D</mtext>", True),
    ],
)
def test_chemistry_equation_tells_chemistry_from_plain_maths(sel, body, chem):
    assert sel.chemistry_equation(M.format(body)) is chem


def test_the_lone_multiplication_sign_is_not_counted(sel):
    times = M.format("<mo>\u00d7</mo>")
    salt = M.format("<mtext>NaCl</mtext>")
    assert sel.chemistry_counts([times, salt, times]) == {
        "equations": 1,
        "chemistry": 1,
        "lone_times": 2,
    }


def test_selection_scan_skips_the_named_selection_files(sel, tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "proof").mkdir()
    (tmp_path / "data" / "selection-fresh5-D.json").write_text('"openstax/chemistry/2-6-x"')
    (tmp_path / "data" / "other.json").write_text('"openstax/chemistry/3-1-y"')
    used: set[tuple[str, str]] = set()
    for path in sel.iter_scanned(tmp_path, sel.SELECTION_FILES):
        used |= sel.used_from_text(path.read_text())
    assert used == {("chemistry", "3-1-y")}
