"""Fifth fresh real-world split ("fresh5"): the deterministic, seeded selection of its units.

    uv run python scripts/fresh5_select.py arxiv-order
    uv run python scripts/fresh5_select.py arxiv-build      [--pause 3]
    uv run python scripts/fresh5_select.py openstax         [--date 2026-11-02]
    uv run python scripts/fresh5_select.py course-harvest
    uv run python scripts/fresh5_select.py course-draw
    uv run python scripts/fresh5_select.py chemistry          [--date 2026-11-02]

Four strata, each a rule and a seed, never a choice (`PREREGISTRATION-FRESH5.md`):

- A, arXiv LaTeX: the seeded order of the fresh4 split continued from the first candidate fresh4 did
  not visit, with the same gates (`realworld_build.process`), 12 papers.
- B, OpenStax: sections that no earlier set used, from books whose licence is CC BY in both the
  archive and the CMS, ranked by SHA-256 of seed, book and section, at most 2 per book, 6 sections.
- C, course-style LaTeX (`gold/course_corpus.py`): a pool of GitHub repositories found by a fixed
  query set and a licence gate, drawn in the seeded order, 8 documents of which at least 3 are
  Beamer decks.
- D, OpenStax chemistry (reported apart, never pooled): sections of the CC BY first editions only
  (Chemistry, Chemistry: Atoms First; the live licence is read for every book), never used before,
  off the topics earlier chemistry sets cover, with enough chemistry equations, a measured 8-gram
  overlap with every used section of at most 20%, and at most 15 printed pages, ranked by SHA-256.

Gold, downloads and compiled PDFs stay under ROOT (default ~/.cache/readablestem/realworld; outside
the repository). The repository gets manifests, candidate logs, the pool and the selection: ids,
licences, hashes, counts. No command here reads a gold file's content; the gates are automatic.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx

from readablestem.gold import arxiv_corpus as ac
from readablestem.gold import arxiv_gold as ag
from readablestem.gold import arxiv_tex as tex
from readablestem.gold import course_corpus as cc
from readablestem.gold import devset
from readablestem.gold import openstax as ox

REPO = Path(__file__).resolve().parent.parent
ROOT = Path.home() / ".cache" / "readablestem" / "realworld"
REPO_DATA = REPO / "data" / "realworld"
SEED = "realworld-fresh5-2026-11-02"
DATE = "2026-11-02"

FRESH4_ORDER = "order-realworld-fresh4-2026-10-26.json"
FRESH4_CANDIDATES = "candidates-fresh4.json"
# The selection files of the strata that name OpenStax sections: a rerun of one stratum must not
# see its own picks (or a later stratum's) as "used", or it would draw something else.
SELECTION_FILES = frozenset({"selection-fresh5-B.json", "selection-fresh5-D.json"})
ARXIV_TARGET = 12
OPENSTAX_TARGET = 6
OPENSTAX_PER_BOOK = 2
OPENSTAX_MIN_EQUATIONS = 10  # an automatic gate: the section's answer key must hold maths

# Books that share text with another (editions, AP and corequisite variants, a combined book and
# its parts, a book and the one it was adapted from): a section used in one blocks the others, and
# the per-book cap counts the family.
FAMILY_ALIASES = {
    "college-algebra": "precalculus",
    "algebra-and-trigonometry": "precalculus",
    "college-algebra-corequisite-support": "precalculus",
    "college-physics-ap-courses": "college-physics",
    "principles-macroeconomics": "principles-economics",
    "principles-microeconomics": "principles-economics",
    "principles-macroeconomics-ap-courses": "principles-economics",
    "principles-microeconomics-ap-courses": "principles-economics",
    "statistics": "introductory-statistics",
    "introductory-business-statistics": "introductory-statistics",
    "biology-ap-courses": "biology",
}
_EDITION = re.compile(r"-\d+e$")
_NUMBERS = re.compile(r"^\d+-\d+-")
_PATH_REF = re.compile(r"openstax/([a-z0-9-]+)/(\d+-\d+[a-z0-9-]*)")
_DOUBLE_REF = re.compile(r"\b([a-z0-9-]+)__(\d+-\d+[a-z0-9-]*)")
_SCANNED_SUFFIXES = {".json", ".md", ".csv", ".txt", ".sha256", ".log", ".tsv"}
_SCANNED_NAMES = {"SHA256SUMS"}


def load_script(name: str):
    """A sibling script as a module (they are not a package)."""
    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


# ============================================================================== stratum A


def continue_order(order: dict, candidates: dict) -> dict:
    """The order after the last candidate the earlier split visited, nothing re-ordered.

    `order` is the earlier split's order file, `candidates` its candidate log. The earlier split
    walked `order` from the start and stopped when it had its papers, so the visited ones are a
    prefix; the continuation is everything after the last of them.
    """
    ids: list[str] = order["order"]
    visited = [c["id"] for c in candidates["candidates"]]
    positions = [ids.index(v) for v in visited]
    if sorted(positions) != list(range(len(visited))):
        raise ValueError("the visited candidates are not a prefix of the order")
    start = len(visited)
    return {
        "seed": order["seed"],
        "pool_sha256": order["pool_sha256"],
        "continues": f"{FRESH4_ORDER} after its last visited candidate ({visited[-1]})",
        "start_index": order.get("start_index", 0) + start,
        "order": ids[start:],
    }


def cmd_arxiv_order(args: argparse.Namespace) -> int:
    order = json.loads((REPO_DATA / FRESH4_ORDER).read_text(encoding="utf-8"))
    candidates = json.loads((REPO_DATA / FRESH4_CANDIDATES).read_text(encoding="utf-8"))
    out = continue_order(order, candidates)
    path = REPO_DATA / f"order-{SEED}-A.json"
    dump(path, out)
    print(path, len(out["order"]), "candidates; first", out["order"][:3])
    return 0


def cmd_arxiv_build(args: argparse.Namespace) -> int:
    build = load_script("realworld_build")
    order = REPO_DATA / f"order-{SEED}-A.json"
    return build.cmd_build(
        SimpleNamespace(
            order=order,
            root=args.root,
            target=ARXIV_TARGET,
            held_out=0,
            pause=args.pause,
            single_part="fresh5a",
            candidates_out="candidates-fresh5-A.json",
            date=args.date,
        )
    )


# ============================================================================== stratum B


def family(book: str) -> str:
    """The book without its edition suffix, and with sibling editions folded together."""
    base = _EDITION.sub("", book)
    return FAMILY_ALIASES.get(base, base)


def used_from_text(text: str) -> set[tuple[str, str]]:
    """(book, section slug) pairs a registration, manifest or hash list names."""
    found = {(m.group(1), m.group(2)) for m in _PATH_REF.finditer(text)}
    found |= {(m.group(1), m.group(2)) for m in _DOUBLE_REF.finditer(text)}
    return found


def iter_scanned(root: Path, skip: frozenset[str] = frozenset()) -> Iterator[Path]:
    for folder in ("proof", "data"):
        for path in sorted((root / folder).rglob("*")):
            if not path.is_file() or path.stat().st_size > 30_000_000 or path.name in skip:
                continue
            if path.suffix in _SCANNED_SUFFIXES or path.name in _SCANNED_NAMES:
                yield path


def used_openstax(
    root: Path, skip: frozenset[str] = frozenset()
) -> tuple[set[tuple[str, str]], set[str]]:
    """Sections any earlier set used and the book families whose unseen part is sealed.

    Sections: the held-out registration (`gold/heldout.REGISTERED`), and every
    `openstax/<book>/<section>` or `<book>__<section>` named in a manifest, hash list or report
    under `proof/` and `data/` (proof/, dev-v2, chem-heldout, the tables and handwriting sets, the
    earlier real-world splits). Families: the books of dev-v2 (its sealed check part is not listed).
    """
    from readablestem.gold.heldout import REGISTERED

    sections = {(s.book, s.slug) for s in REGISTERED.sections}
    for path in iter_scanned(root, skip):
        sections |= used_from_text(path.read_text(encoding="utf-8", errors="ignore"))
    families: set[str] = set()
    selection = root / "data" / "dev-v2" / "selection.json"
    if selection.is_file():
        books = json.loads(selection.read_text(encoding="utf-8")).get("config", {}).get("books", {})
        families |= {family(b) for b in books}
    return sections, families


def title_key(slug: str) -> str:
    """The section slug without its chapter and section numbers: the same text in another book
    (an economics section shared by sibling books, a statistics chapter reused) has the same key."""
    return _NUMBERS.sub("", slug)


def blocked(
    book: str,
    slug: str,
    used: set[tuple[str, str]],
    families: set[str],
    used_titles: set[str] | None = None,
) -> bool:
    """A section is out if it, or the same section of a sibling edition, was used, if a section
    with the same title in any book was used, or if its book family is one of the sealed ones."""
    fam = family(book)
    if fam in families:
        return True
    titles = used_titles if used_titles is not None else {title_key(s) for _, s in used}
    return title_key(slug) in titles or any(family(b) == fam and s == slug for b, s in used)


def order_sections(sections: Iterable[tuple[str, str]], seed: str) -> list[tuple[str, str]]:
    return sorted(sections, key=lambda bs: ac.stable_key(seed, "openstax", bs[0], bs[1]))


def order_books(books: Iterable[str], seed: str) -> list[str]:
    return sorted(books, key=lambda b: ac.stable_key(seed, "openstax-book", b))


def pick_sections(
    ranked: dict[str, list[str]],
    seed: str,
    passes: Callable[[str, str], bool],
    *,
    target: int = OPENSTAX_TARGET,
    per_book: int = OPENSTAX_PER_BOOK,
    log: list[dict] | None = None,
) -> list[tuple[str, str]]:
    """Books in the seeded order, one section from each in turn (its next-ranked one that passes
    the gate), at most `per_book` from a book, until `target`. A section whose title a taken section
    already has is skipped. A section that fails a gate is logged and skipped, never swapped by
    choice."""
    queues = {book: list(slugs) for book, slugs in ranked.items()}
    taken: list[tuple[str, str]] = []
    count: Counter[str] = Counter()  # per book family
    names = order_books(queues, seed)
    titles: set[str] = set()
    progress = True
    while len(taken) < target and progress:
        progress = False
        for book in names:
            if len(taken) >= target:
                break
            while queues[book] and count[family(book)] < per_book:
                slug = queues[book].pop(0)
                if title_key(slug) in titles:
                    if log is not None:
                        log.append({"book": book, "section": slug, "gate": "duplicate_title"})
                    continue
                ok = passes(book, slug)
                if log is not None:
                    log.append({"book": book, "section": slug, "gate": "pass" if ok else "fail"})
                if ok:
                    titles.add(title_key(slug))
                    taken.append((book, slug))
                    count[family(book)] += 1
                    progress = True
                    break
    return taken


def cmd_openstax(args: argparse.Namespace) -> int:
    fetcher = ox.HttpFetcher(cache_dir=args.root / "archive")
    used, families = used_openstax(REPO, SELECTION_FILES)
    rows = [r for r in ox.license_table(fetcher) if r["cc_by"] and r["in_archive_release"]]
    books: dict[str, ox.Book] = {}
    ranked: dict[str, list[str]] = {}
    refused: list[dict] = []
    used_titles = {title_key(s) for _, s in used}
    for row in sorted(rows, key=lambda r: r["slug"]):
        slug = row["slug"]
        try:
            book = ox.resolve_book(slug, fetcher)
            ox.check_public_license(book)
        except ox.GoldError as exc:
            refused.append({"book": slug, "reason": type(exc).__name__})
            continue
        if family(slug) in families:
            refused.append({"book": slug, "reason": "sealed_family"})
            continue
        pages = [p for p in ox.iter_pages(book.tree) if p.section is not None]
        free = [p.slug for p in pages if not blocked(slug, p.slug, used, families, used_titles)]
        if free:
            books[slug] = book
            ranked[slug] = [s for _, s in order_sections([(slug, s) for s in free], SEED)]
    counts: dict[tuple[str, str], dict[str, int]] = {}

    def passes(book_slug: str, page_slug: str) -> bool:
        book = books[book_slug]
        page = next(p for p in ox.iter_pages(book.tree) if p.slug == page_slug)
        payload = fetcher.get_json(book.content_url(page.uuid))
        records = ox.build_page_records(book, page, payload["content"], args.date)
        counts[(book_slug, page_slug)] = dict(Counter(r["kind"] for r in records))
        return counts[(book_slug, page_slug)].get("equation", 0) >= OPENSTAX_MIN_EQUATIONS

    visits: list[dict] = []
    taken = pick_sections(ranked, SEED, passes, log=visits)
    out_dir = args.root / "gold" / "fresh5b"
    for old in sorted(out_dir.rglob("*")) if out_dir.is_dir() else []:
        if old.is_file():
            old.unlink()  # a rerun writes the gold of this selection only
    selected = []
    for book_slug, page_slug in taken:
        book = books[book_slug]
        ox.fetch_gold(
            book_slug,
            1,
            out_dir,
            fetcher,
            page_slugs=[page_slug],
            retrieved_at=args.date,
            allow_retired=True,
            pin=ox.BookPin(book.archive_version, book.version),
        )
        gold = out_dir / "openstax" / book_slug / f"{page_slug}.jsonl"
        page = next(p for p in ox.iter_pages(book.tree) if p.slug == page_slug)
        selected.append(
            {
                "book": book_slug,
                "book_title": book.title,
                "book_state": book.state,
                "section": page_slug,
                "title": page.title,
                "licence": book.license_name,
                "licence_url": book.license_url,
                "archive_version": book.archive_version,
                "book_version": book.version,
                "records_by_kind": counts[(book_slug, page_slug)],
                "gold_sha256": file_sha256(gold),
            }
        )
    selection = {
        "seed": SEED,
        "rule": {
            "licence": "CC BY in both the archive book JSON and the CMS (plain CC BY, any version)",
            "retired_editions": "allowed (proof/DECISION-retired-editions-2026-10-13.md)",
            "unused": (
                "section not named by any earlier set; sibling editions folded together; "
                "a section whose title (its slug without numbers) an earlier set or pick has is out"
            ),
            "sealed_families": sorted(families),
            "min_equations": OPENSTAX_MIN_EQUATIONS,
            "per_book": OPENSTAX_PER_BOOK,
            "order": "SHA-256 of seed, book and section; books in turn, in the seeded order",
        },
        "n_used_sections": len(used),
        "books_with_free_sections": {b: len(s) for b, s in sorted(ranked.items())},
        "books_refused": refused,
        "visits": visits,
        "sections": selected,
    }
    path = REPO_DATA / "selection-fresh5-B.json"
    dump(path, selection)
    print(path, len(selected), "sections")
    for s in selected:
        print(s["book"], s["section"], s["licence"], s["records_by_kind"].get("equation"))
    return 0


# ============================================================================== stratum C


def github_token() -> str | None:
    try:
        run = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
    except OSError:
        return None
    return run.stdout.strip() or None


def cmd_openstax_render(args: argparse.Namespace) -> int:
    """Print the selected sections to Letter PDFs with the renderer the held-out set uses
    (`eval.render`), and record page counts and SHA-256 values in the selection file. The
    answer key is read by the renderer only to build the page; nothing is shown or scored."""
    from readablestem.eval import render
    from readablestem.eval.pipeline_eval import load_gold

    path = REPO_DATA / "selection-fresh5-B.json"
    selection = json.loads(path.read_text(encoding="utf-8"))
    gold_dir = args.root / "gold" / "fresh5b"
    out_dir = gold_dir / "pdfs"
    out_dir.mkdir(parents=True, exist_ok=True)
    fetcher = ox.HttpFetcher(cache_dir=args.root / "archive")
    images = render.ImageCache(args.root / "render" / "images")
    records = load_gold(gold_dir)
    import pymupdf

    with render.ChromiumRenderer() as browser:
        for section in selection["sections"]:
            mine = [
                r
                for r in records
                if r["book_slug"] == section["book"] and r["page_slug"] == section["section"]
            ]
            authors = render.book_authors(gold_dir, section["book"])
            page = render.page_source_from_records(mine, fetcher, authors=authors)
            pdf = render.render_page_pdf(
                page,
                out_dir / f"{section['book']}-{section['section']}.pdf",
                images=images,
                renderer=browser,
                paper="Letter",
                cache_dir=args.root / "render" / "pages",
            )
            with pymupdf.open(stream=pdf, filetype="pdf") as doc:
                section["pdf_pages"] = doc.page_count
            section["pdf_sha256"] = hashlib.sha256(pdf).hexdigest()
            section["pdf_paper"] = "Letter"
    selection["n_pdf_pages"] = sum(s["pdf_pages"] for s in selection["sections"])
    dump(path, selection)
    for s in selection["sections"]:
        print(s["book"], s["section"], s["pdf_pages"], s["pdf_sha256"][:12])
    print("total", selection["n_pdf_pages"], "pages")
    return 0


def cmd_course_harvest(args: argparse.Namespace) -> int:
    repos, counts = cc.harvest(github_token(), pause=args.pause)
    pool = {
        "seed": SEED,
        "harvested": args.date,
        "queries": [{"label": label, "q": q} for label, q in cc.search_queries()],
        "kept_per_query": counts,
        "accepted_licences": {k: v[0] for k, v in cc.ACCEPTED_LICENCES.items()},
        "n_repos": len(repos),
        "repos": [r.to_json() for r in repos],
    }
    path = REPO_DATA / "pool-fresh5-course.json"
    dump(path, pool)
    print(path, len(repos), "repositories", dict(Counter(r.licence for r in repos)))
    return 0


def prefetch(client: httpx.Client, repos: list[cc.Repo], base: Path, workers: int = 8) -> None:
    """Download and unpack every repository of the pool in parallel (the walk then finds them
    cached). A repository that fails is left for the walk to log; nothing is compiled here."""

    def one(repo: cc.Repo) -> None:
        with contextlib.suppress(cc.Skip, httpx.HTTPError):
            cc.fetch_repo(client, repo, base / repo.full_name.replace("/", "__"))

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, repos))


def cmd_course_draw(args: argparse.Namespace) -> int:
    pool = json.loads((REPO_DATA / "pool-fresh5-course.json").read_text(encoding="utf-8"))
    repos = [cc.Repo(**r) for r in pool["repos"]]
    base = args.root / "course-v2"  # fetched and compiled through safe_tex only
    gold_dir = args.root / "gold" / "fresh5c"
    log: list[dict] = []
    headers = {"User-Agent": cc.USER_AGENT, "Accept": "application/vnd.github+json"}
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(headers=headers, timeout=60) as client:

        def documents(repo: cc.Repo):
            slug = repo.full_name.replace("/", "__")
            src, sha, licence = cc.fetch_repo(client, repo, base / slug)
            sources = tex.read_sources(src)
            docs = cc.discover(sources)
            if not docs:
                raise cc.Skip("no_documents")

            def build(doc: cc.Doc) -> dict:
                uid = cc.unit_id(repo.full_name, doc.path)
                work = base / slug / "build" / ag.paper_slug(uid)
                built = cc.build_document(sources, doc, src, work, SEED, uid)
                return {
                    "repo": repo.full_name,
                    "beamer": doc.beamer,
                    "built": built,
                    "doc": doc,
                    "repo_record": repo,
                    "sha": sha,
                    "licence": licence,
                }

            return docs, build

        prefetch(client, repos, base)  # downloads only: nothing is compiled here
        accepted = cc.draw(repos, SEED, documents, log)
    for old in sorted(gold_dir.rglob("*")) if gold_dir.is_dir() else []:
        if old.is_file():
            old.unlink()  # a rerun writes the gold of this draw only
    (gold_dir / "pdfs").mkdir(parents=True, exist_ok=True)
    papers = []
    for entry in sorted(accepted, key=lambda e: cc.unit_id(e["repo"], e["doc"].path)):
        built, doc, repo, sha, licence = (
            entry["built"],
            entry["doc"],
            entry["repo_record"],
            entry["sha"],
            entry["licence"],
        )
        uid = cc.unit_id(repo.full_name, doc.path)
        slug = ag.paper_slug(uid)
        rows = cc.gold_records(built, repo, doc, licence, sha, retrieved_at=args.date)
        pdf_rel = f"pdfs/{slug}.pdf"
        ag.cut_pdf(built.pdf, built.pages, gold_dir / pdf_rel)
        gold_path = gold_dir / f"{slug}.jsonl"
        gold_path.write_text(
            "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
            encoding="utf-8",
        )
        papers.append(
            {
                "id": uid,
                "title": f"{repo.full_name}: {doc.path}",
                "licence": licence.name,
                "licence_url": licence.url,
                "set": "github",
                "style": "beamer" if doc.beamer else "course",
                "repo": repo.full_name,
                "repo_commit": sha,
                "source_url": f"{repo.html_url}/blob/{sha}/{doc.path}",
                "pages_chosen": [p + 1 for p in built.pages],
                "equations": len(rows),
                "display": sum(1 for r in rows if r["display"] == "block"),
                "pdf_sha256": built.info["pdf_sha256"],
                "tex_engine": built.info["tex_engine"],
                "source_sha256": sha,
                "extract_pdf_sha256": ag.sha256_file(gold_dir / pdf_rel),
                "gold_sha256": ag.sha256_file(gold_path),
            }
        )
    manifest = {
        "part": "fresh5c",
        "seed": SEED,
        "papers": papers,
        "n_papers": len(papers),
        "n_beamer": sum(1 for p in papers if p["style"] == "beamer"),
        "tex_engines": sorted({p["tex_engine"] for p in papers}),
        "n_equations": sum(p["equations"] for p in papers),
        "n_pages": sum(len(p["pages_chosen"]) for p in papers),
    }
    pool_hash = file_sha256(REPO_DATA / "pool-fresh5-course.json")
    dump(
        REPO_DATA / "candidates-fresh5-C.json",
        {"seed": SEED, "pool_sha256": pool_hash, "visits": log},
    )
    dump(gold_dir / "manifest.json", manifest)
    dump(REPO_DATA / "manifest-fresh5c.json", manifest)
    print("accepted", len(papers), "of", cc.TARGET, "; beamer", manifest["n_beamer"])
    for p in papers:
        print(p["id"], p["licence"], p["style"], p["pages_chosen"], p["equations"])
    return 0


# ============================================================================== stratum D

SEED_D = "fresh5-D"
D_TARGET = 4
D_MIN_EQUATIONS = 25  # gold equations in the key, the lone multiplication sign not counted
D_MIN_CHEM_SHARE = 0.40  # of those, the share that is chemistry (`chemistry_equation`)
D_MAX_OVERLAP = 0.20  # 8-gram overlap with the text of every used section
D_MAX_PAGES = 15  # printed Letter pages
D_POOL_WORD = "chemistry"  # every book whose slug has it is looked at; the licence gate decides

# Sections on the topics the development and held-out chemistry sets cover (a stem of the title).
D_TOPIC_STEMS = (
    "balanc",
    "precipitat",
    "galvanic",
    "decay",
    "nernst",
    "rate-law",
    "bronsted",
    "nuclear-equation",
    "measurement",
    "thermodynamic",
    "noble-gas",
    "representative-metal",
    "lewis",
    "titration",
    "coordination",
)

_ELEMENTS = (  # noqa: SIM905
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As "
    "Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu "
    "Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np "
    "Pu Am Cm Bk Cf Es Fm Md No Lr"
).split()
_EL = "|".join(sorted(_ELEMENTS, key=lambda e: (-len(e), e)))
_FORMULA = re.compile(rf"(?<![A-Za-z])(?:(?:{_EL})\d*){{2,}}(?![a-z])")
_ION = re.compile(rf"(?<![A-Za-z])(?:{_EL})\d*\^[−+]|(?<![A-Za-z])(?:{_EL})\^\d[−+]")
_STATE = re.compile(r"\((?:aq|s|l|g)\)")


def chemistry_equation(mathml: str) -> bool:
    """A gold equation that writes chemistry: a reaction arrow, a state symbol, a formula of two
    or more element symbols (H2O, NaCl) or an ion (Cu^2+). A symbol alone (a variable) is not."""
    from readablestem.eval import equations_chem as ec

    stream = ec.chem_of(mathml)
    if stream == ec.LONE_TIMES:
        return False
    text = "".join(stream).replace("<g>", "").replace("</g>", "")
    if "→" in text or "⇌" in text or _STATE.search(text):
        return True
    flat = text.replace("_", "")
    return bool(_FORMULA.search(flat) or _ION.search(flat))


def chemistry_counts(mathmls: Iterable[str]) -> dict[str, int]:
    """Equations that count (the lone multiplication sign is left out, as the metric leaves it
    out) and how many of them are chemistry."""
    from readablestem.eval import equations_chem as ec

    counted = [m for m in mathmls if not ec.is_lone_times(m)]
    return {
        "equations": len(counted),
        "chemistry": sum(1 for m in counted if chemistry_equation(m)),
        "lone_times": sum(1 for m in mathmls if ec.is_lone_times(m)),
    }


def topic_hit(slug: str) -> str | None:
    """The topic stem a section's title has, or None."""
    title = title_key(slug)
    return next((stem for stem in D_TOPIC_STEMS if stem in title), None)


def rank_key_d(book: str, slug: str) -> str:
    """SHA-256 of "fresh5-D|book|slug"; the draw takes the smallest first."""
    return ac.stable_key(SEED_D, book, slug)


def order_d(sections: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    return sorted(sections, key=lambda bs: rank_key_d(*bs))


def overlap_share(words: Sequence[str], reference: set[tuple[str, ...]]) -> float:
    """Share of the section's word 8-grams that occur in the reference text."""
    return devset.containment(words, reference)


def chemistry_gate(counts: dict[str, int], overlap: float, pages: int | None = None) -> str | None:
    """None when a section is eligible, else the name of the first gate it fails."""
    if counts["equations"] < D_MIN_EQUATIONS:
        return "equations"
    if counts["chemistry"] < D_MIN_CHEM_SHARE * counts["equations"]:
        return "chemistry_share"
    if overlap > D_MAX_OVERLAP:
        return "overlap"
    if pages is not None and pages > D_MAX_PAGES:
        return "pages"
    return None


def pick_chemistry(
    order: Sequence[tuple[str, str]],
    judge: Callable[[str, str], str | None],
    *,
    target: int = D_TARGET,
    log: list[dict] | None = None,
) -> list[tuple[str, str]]:
    """Walk the seeded order and take what `judge` passes (None) until `target`. A failing section
    is logged with its gate and skipped, never swapped by choice."""
    taken: list[tuple[str, str]] = []
    for book, slug in order:
        if len(taken) >= target:
            break
        gate = judge(book, slug)
        if log is not None:
            log.append({"book": book, "section": slug, "gate": gate or "pass"})
        if gate is None:
            taken.append((book, slug))
    return taken


def sealed_check_sections(books: dict[str, ox.Book], salt: str) -> set[tuple[str, str]]:
    """The sections of dev-v2's sealed check part. Their names are not recorded (the registry says
    only that a section whose SHA-256 of "salt:book/slug" is below the share can be one), so every
    section of the dev-v2 books that falls below it is treated as sealed: a superset."""
    return {
        (b, p.slug)
        for b, book in books.items()
        for p in ox.iter_pages(book.tree)
        if p.section is not None and devset.part_of(salt, b, p.slug) == "check"
    }


def cmd_chemistry(args: argparse.Namespace) -> int:
    import pymupdf

    from readablestem.eval import render
    from readablestem.eval.pipeline_eval import load_gold

    fetcher = ox.HttpFetcher(cache_dir=args.root / "archive")
    used, _families = used_openstax(REPO, SELECTION_FILES)
    config = json.loads((REPO / "data" / "dev-v2" / "selection.json").read_text(encoding="utf-8"))[
        "config"
    ]
    refused: list[dict] = []
    books: dict[str, ox.Book] = {}
    for row in ox.license_table(fetcher):
        if D_POOL_WORD not in row["slug"]:
            continue
        try:
            book = ox.resolve_book(row["slug"], fetcher)
            ox.check_public_license(book)  # CC BY in the archive and in the CMS, read now
        except ox.GoldError as exc:
            refused.append(
                {
                    "book": row["slug"],
                    "licence": row["license_name"],
                    "reason": type(exc).__name__,
                }
            )
            continue
        books[row["slug"]] = book
    dev_books = {b: bk for b, bk in books.items() if b in config["books"]}
    sealed = sealed_check_sections(dev_books, config["salt"])
    sealed_titles = {title_key(s) for _, s in sealed}
    used_titles = {title_key(s) for _, s in used} | sealed_titles
    pages: dict[tuple[str, str], ox.PageRef] = {}
    ranked: list[tuple[str, str]] = []
    skipped: Counter[str] = Counter()
    for slug, book in sorted(books.items()):
        for page in ox.iter_pages(book.tree):
            if page.section is None:
                continue
            pages[(slug, page.slug)] = page
            reason = (
                "used"
                if blocked(slug, page.slug, used, set(), used_titles)
                else "sealed_check"
                if (slug, page.slug) in sealed
                else "topic"
                if topic_hit(page.slug)
                else None
            )
            if reason:
                skipped[reason] += 1
            else:
                ranked.append((slug, page.slug))
    order = order_d(ranked)

    def words_of(book_slug: str, page_slug: str) -> list[str]:
        book = books[book_slug]
        payload = fetcher.get_json(book.content_url(pages[(book_slug, page_slug)].uuid))
        return devset.words_outside_math(payload["content"])

    reference: set[tuple[str, ...]] = set()
    n_reference = 0
    for book_slug, page_slug in sorted(used | sealed):
        if book_slug in books and (book_slug, page_slug) in pages:
            reference |= devset.ngrams(words_of(book_slug, page_slug))
            n_reference += 1
    out_dir = args.root / "gold" / "fresh5d"
    for old in sorted(out_dir.rglob("*")) if out_dir.is_dir() else []:
        if old.is_file():
            old.unlink()  # a rerun writes the gold of this selection only
    pdf_dir = out_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    images = render.ImageCache(args.root / "render" / "images")
    measured: dict[tuple[str, str], dict] = {}
    printed: dict[tuple[str, str], tuple[int, str]] = {}
    taken_grams: set[tuple[str, ...]] = set()

    with render.ChromiumRenderer() as browser:

        def judge(book_slug: str, page_slug: str) -> str | None:
            book = books[book_slug]
            page = pages[(book_slug, page_slug)]
            payload = fetcher.get_json(book.content_url(page.uuid))
            records = ox.build_page_records(book, page, payload["content"], args.date)
            counts = chemistry_counts([r["mathml"] for r in records if r["kind"] == "equation"])
            grams = devset.ngrams(devset.words_outside_math(payload["content"]))
            overlap = overlap_share(
                devset.words_outside_math(payload["content"]), reference | taken_grams
            )
            measured[(book_slug, page_slug)] = counts | {
                "overlap_8gram": round(overlap, 4),
                "records_by_kind": dict(Counter(r["kind"] for r in records)),
            }
            gate = chemistry_gate(counts, overlap)
            if gate:
                return gate
            ox.fetch_gold(
                book_slug,
                1,
                out_dir,
                fetcher,
                page_slugs=[page_slug],
                retrieved_at=args.date,
                allow_retired=True,
                pin=ox.BookPin(book.archive_version, book.version),
            )
            gold = out_dir / "openstax" / book_slug / f"{page_slug}.jsonl"
            mine = [
                r
                for r in load_gold(out_dir)
                if r["book_slug"] == book_slug and r["page_slug"] == page_slug
            ]
            source = render.page_source_from_records(
                mine, fetcher, authors=render.book_authors(out_dir, book_slug)
            )
            pdf_path = pdf_dir / f"{book_slug}-{page_slug}.pdf"
            pdf = render.render_page_pdf(
                source,
                pdf_path,
                images=images,
                renderer=browser,
                paper="Letter",
                cache_dir=args.root / "render" / "pages",
            )
            with pymupdf.open(stream=pdf, filetype="pdf") as doc:
                n_pages = doc.page_count
            measured[(book_slug, page_slug)]["pdf_pages"] = n_pages
            if chemistry_gate(counts, overlap, n_pages):
                gold.unlink()
                pdf_path.unlink(missing_ok=True)
                return "pages"
            printed[(book_slug, page_slug)] = (n_pages, hashlib.sha256(pdf).hexdigest())
            taken_grams.update(grams)
            return None

        visits: list[dict] = []
        taken = pick_chemistry(order, judge, log=visits)
    for visit in visits:
        visit.update(measured.get((visit["book"], visit["section"]), {}))
    ox.write_manifest(out_dir)
    selected = []
    for book_slug, page_slug in taken:
        book = books[book_slug]
        gold = out_dir / "openstax" / book_slug / f"{page_slug}.jsonl"
        n_pages, pdf_hash = printed[(book_slug, page_slug)]
        selected.append(
            {
                "book": book_slug,
                "book_title": book.title,
                "book_state": book.state,
                "section": page_slug,
                "title": pages[(book_slug, page_slug)].title,
                "licence": book.license_name,
                "licence_url": book.license_url,
                "archive_version": book.archive_version,
                "book_version": book.version,
                **measured[(book_slug, page_slug)],
                "pdf_pages": n_pages,
                "pdf_paper": "Letter",
                "pdf_sha256": pdf_hash,
                "gold_sha256": file_sha256(gold),
            }
        )
    selection = {
        "seed": SEED_D,
        "date": args.date,
        "rule": {
            "licence": (
                "CC BY in both the archive book JSON and the CMS, read live for every book whose "
                "slug has 'chemistry' (the first editions pass; 2e and Organic Chemistry are "
                "NC-SA and are refused)"
            ),
            "retired_editions": "allowed (proof/DECISION-retired-editions-2026-10-13.md)",
            "unused": (
                "section not named by any earlier set; sibling editions folded; a section whose "
                "title (slug without numbers) an earlier set has is out; so is any section of "
                "dev-v2's books that could be in its sealed check part, and any with a title one "
                "of those has"
            ),
            "topics_out": list(D_TOPIC_STEMS),
            "min_equations": D_MIN_EQUATIONS,
            "min_chemistry_share": D_MIN_CHEM_SHARE,
            "max_overlap_8gram": D_MAX_OVERLAP,
            "max_pdf_pages": D_MAX_PAGES,
            "overlap_reference": (
                "word 8-grams outside maths of every used or sealed section of the pool's "
                "books and "
                "of the sections already taken"
            ),
            "order": "SHA-256 of 'fresh5-D|book|section', smallest first; the first 4 that pass",
        },
        "books_refused": refused,
        "n_used_sections": len(used),
        "n_sealed_possible": len(sealed),
        "n_reference_sections": n_reference,
        "skipped_before_gates": dict(sorted(skipped.items())),
        "n_ranked": len(order),
        "visits": visits,
        "sections": selected,
        "n_pdf_pages": sum(s["pdf_pages"] for s in selected),
    }
    path = REPO_DATA / "selection-fresh5-D.json"
    dump(path, selection)
    print(path, len(selected), "sections,", selection["n_pdf_pages"], "pages")
    for s in selected:
        print(s["book"], s["section"], s["licence"], s["equations"], s["chemistry"], s["pdf_pages"])
    return 0


# =================================================================================== main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--date", default=DATE)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("arxiv-order").set_defaults(func=cmd_arxiv_order)
    a = sub.add_parser("arxiv-build")
    a.add_argument("--pause", type=float, default=3.0)
    a.set_defaults(func=cmd_arxiv_build)
    sub.add_parser("openstax").set_defaults(func=cmd_openstax)
    sub.add_parser("openstax-render").set_defaults(func=cmd_openstax_render)
    h = sub.add_parser("course-harvest")
    h.add_argument("--pause", type=float, default=7.0)
    h.set_defaults(func=cmd_course_harvest)
    sub.add_parser("course-draw").set_defaults(func=cmd_course_draw)
    sub.add_parser("chemistry").set_defaults(func=cmd_chemistry)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
