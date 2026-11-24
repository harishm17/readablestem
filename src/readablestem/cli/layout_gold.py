"""`gold layout ...`: reading-order gold built from sources (`readablestem.gold.layout_gold`).

A set is a folder outside the repository (`files/<id>.pdf`, `gold/<id>.json`); the repository
keeps a registry of its hashes under `data/gold/layout/`. `verify` checks a folder against
its registry, `register` writes the registry of a new set, `sample` draws the pages a person
checks and `agreement` reads the filled sheet.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from readablestem.core.paths import cache_dir
from readablestem.gold import layout_gold

REGISTRIES = Path("data/gold/layout")


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def set_root(name: str, root: Path | None = None) -> Path:
    """Where a set lies: `--root`, else `<cache home>/readablestem/layout-gold/<set>`."""
    return root or cache_dir("layout-gold", name)


def registry_path(name: str, path: Path | None = None) -> Path:
    from readablestem.cli.main import project_root

    return path or project_root() / REGISTRIES / f"{name}.json"


def held_out(registry: dict[str, Any], root: Path | None = None) -> bool:
    """Whether a set's documents and pages are not to be named, and its score is for a release
    only. A set is held out unless its registry says development (any other word, or none,
    fails closed). With `root`, a development registry speaks only for the folder it registers:
    a held-out folder under a development set's name, or given its registry, stays held out."""
    if registry.get("side") != "development":
        return True
    return root is not None and bool(layout_gold.verify_set(root, registry))


def _outside_project(path: Path) -> bool:
    from readablestem.cli.main import project_root

    return not path.resolve().is_relative_to(project_root())


def _cmd_verify(args: argparse.Namespace) -> int:
    registry = registry_path(args.set, args.registry)
    root = set_root(args.set, args.root)
    if not registry.is_file():
        return _error(f"no registry at {registry}")
    if not (root / "gold").is_dir():
        return _error(f"no set at {root} (a folder with files/ and gold/)")
    listed = json.loads(registry.read_text(encoding="utf-8"))
    problems = layout_gold.verify_set(root, listed)
    for problem in problems:
        print(f"- {problem}", file=sys.stderr)
    if problems:
        return 1
    pages = sum(row["pages"] for row in listed["documents"])
    print(f"{args.set}: {len(listed['documents'])} documents, {pages} pages, as registered")
    return 0


def _cmd_register(args: argparse.Namespace) -> int:
    registry = registry_path(args.set, args.registry)
    root = set_root(args.set, args.root)
    if registry.exists() and not args.replace:
        return _error(f"{registry} exists; a registered set changes only with --replace")
    if not _outside_project(root):
        return _error(f"{root} is inside the repository; a set's files stay outside it")
    listed = layout_gold.registry_of(root, args.set, args.note, args.side)
    if not listed["documents"]:
        return _error(f"no gold under {root}")
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps(listed, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {registry}: {len(listed['documents'])} documents")
    return 0


def _work(args: argparse.Namespace) -> Path:
    return args.work or cache_dir("layout-gold-work", args.set)


def _cmd_draw_papers(args: argparse.Namespace) -> int:
    from readablestem.cli.main import project_root
    from readablestem.gold import layout_sources

    root = set_root(args.set, args.root)
    if not _outside_project(root):
        return _error(f"{root} is inside the repository; a set's files stay outside it")
    if not args.pool.is_file():
        return _error(f"no pool at {args.pool} (the candidates of `gold/arxiv_corpus.py`)")
    work = _work(args)
    candidates = json.loads(args.pool.read_text(encoding="utf-8"))["candidates"]
    used = layout_sources.used_papers(project_root(), cache_dir()) - {
        path.name for path in (work / "papers").glob("*")
    }
    print(f"{len(candidates)} candidates, {len(used)} papers used before", flush=True)
    walk = layout_sources.draw_papers(
        candidates,
        args.seed,
        root,
        work,
        used=used,
        per_family=args.per_family,
        visits=args.visits,
        log=lambda line: print(line, flush=True),
    )
    pages = sum(row["pages"] for row in walk["taken"])
    print(f"{len(walk['taken'])} papers taken, {pages} pages; {len(walk['passed'])} passed over")
    return 0


def _cmd_draw_books(args: argparse.Namespace) -> int:
    from readablestem.gold import layout_sources

    root = set_root(args.set, args.root)
    if not _outside_project(root):
        return _error(f"{root} is inside the repository; a set's files stay outside it")
    walk = layout_sources.draw_books(
        args.seed, root, _work(args), count=args.count, log=lambda line: print(line, flush=True)
    )
    pages = sum(row["pages"] for row in walk["taken"])
    print(f"{len(walk['taken'])} books taken, {pages} pages; {len(walk['passed'])} passed over")
    return 0


def _cmd_draw_course(args: argparse.Namespace) -> int:
    from readablestem.cli.main import project_root
    from readablestem.gold import course_corpus, layout_sources

    root = set_root(args.set, args.root)
    if not _outside_project(root):
        return _error(f"{root} is inside the repository; a set's files stay outside it")
    if not args.pool.is_file():
        return _error(f"no pool at {args.pool} (the repositories of `gold/course_corpus.py`)")
    if not args.sources.is_dir():
        return _error(f"no downloaded repositories at {args.sources}")
    pool = json.loads(args.pool.read_text(encoding="utf-8"))["repos"]
    used = layout_sources.used_repos(project_root())
    print(f"{len(pool)} repositories, {len(used)} used before", flush=True)
    walk = layout_sources.draw_course(
        [course_corpus.Repo(**repo) for repo in pool],
        args.seed,
        root,
        _work(args),
        args.sources,
        used=used,
        count=args.count,
        visits=args.visits,
        log=lambda line: print(line, flush=True),
    )
    pages = sum(row["pages"] for row in walk["taken"])
    print(f"{len(walk['taken'])} documents taken, {pages} pages; {len(walk['passed'])} passed over")
    return 0


def _cmd_sample(args: argparse.Namespace) -> int:
    root = set_root(args.set, args.root)
    if not _outside_project(args.out):
        return _error(f"{args.out} is inside the repository; page pictures stay outside it")
    rows = layout_gold.write_sample(root, args.seed, args.out)
    pages = {(row["id"], row["page"]) for row in rows}
    print(f"{len(pages)} pages, {len(rows)} blocks: {args.out / 'sheet.jsonl'}")
    return 0


def _cmd_agreement(args: argparse.Namespace) -> int:
    if not args.sheet.is_file():
        return _error(f"sheet not found: {args.sheet}")
    rows = [json.loads(line) for line in args.sheet.read_text(encoding="utf-8").splitlines()]
    result = layout_gold.agreement(rows)
    for kind, count in result.items():
        verdict = "may be used as gold" if count["usable"] else "not usable as gold"
        print(
            f"{kind}: {count['right']} of {count['checked']} checked blocks right "
            f"({count['blocks']} sampled): {verdict}"
        )
    return 0 if all(count["usable"] for count in result.values()) else 1


def register(gold: argparse._SubParsersAction) -> None:
    parser = gold.add_parser(
        "layout", help="reading-order gold built from sources: verify, register, sample"
    ).add_subparsers(dest="layout_command", required=True)

    def add_set(p: argparse.ArgumentParser) -> None:
        p.add_argument("--set", required=True, help="the set's name, e.g. layout-dev-1")
        p.add_argument("--root", type=Path, help="the set's folder (default: the cache)")

    verify = parser.add_parser("verify", help="check a set's folder against its registry")
    add_set(verify)
    verify.add_argument("--registry", type=Path, help="default: data/gold/layout/<set>.json")
    verify.set_defaults(func=_cmd_verify)

    new = parser.add_parser("register", help="write the registry (hashes, no text) of a set")
    add_set(new)
    new.add_argument("--registry", type=Path, help="default: data/gold/layout/<set>.json")
    new.add_argument("--note", required=True, help="how the set was made and what it is for")
    new.add_argument("--replace", action="store_true")
    new.add_argument(
        "--side",
        choices=layout_gold.SIDES,
        default="development",
        help="held-out: no page of the set is opened while the reader is being worked on",
    )
    new.set_defaults(func=_cmd_register)

    def add_draw(p: argparse.ArgumentParser) -> None:
        add_set(p)
        p.add_argument("--seed", required=True, help="fixes the order the candidates are tried")
        p.add_argument("--work", type=Path, help="downloads and the walk's record (the cache)")

    papers = parser.add_parser(
        "draw-papers",
        help="add arXiv papers to a set: a seeded walk, LaTeXML in the sandbox, no model",
    )
    add_draw(papers)
    papers.add_argument("--pool", type=Path, required=True, help="pool.json of licensed papers")
    papers.add_argument("--per-family", type=int, default=4, help="papers per class family")
    papers.add_argument("--visits", type=int, default=200, help="papers tried at most")
    papers.set_defaults(func=_cmd_draw_papers)

    books = parser.add_parser(
        "draw-books", help="add CC BY OpenStax books to a set: a seeded walk, no model"
    )
    add_draw(books)
    books.add_argument("--count", type=int, default=8, help="books taken at most")
    books.set_defaults(func=_cmd_draw_books)

    course = parser.add_parser(
        "draw-course",
        help="add course handouts written in LaTeX to a set: a seeded walk, no model",
    )
    add_draw(course)
    course.add_argument("--pool", type=Path, required=True, help="the pool of repositories")
    course.add_argument(
        "--sources", type=Path, required=True, help="the repositories as downloaded"
    )
    course.add_argument("--count", type=int, default=15, help="documents taken at most")
    course.add_argument("--visits", type=int, default=120, help="repositories tried at most")
    course.set_defaults(func=_cmd_draw_course)

    sample = parser.add_parser(
        "sample", help="draw 1 page in 10 per kind and write its picture and a sheet to fill"
    )
    add_set(sample)
    sample.add_argument("--seed", required=True)
    sample.add_argument("--out", type=Path, required=True, help="a folder outside the repository")
    sample.set_defaults(func=_cmd_sample)

    agreement = parser.add_parser(
        "agreement", help="read a filled sheet: a kind under 95 of 100 blocks is not usable"
    )
    agreement.add_argument("sheet", type=Path)
    agreement.set_defaults(func=_cmd_agreement)
