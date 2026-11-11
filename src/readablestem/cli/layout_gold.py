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
    listed = layout_gold.registry_of(root, args.set, args.note)
    if not listed["documents"]:
        return _error(f"no gold under {root}")
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps(listed, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {registry}: {len(listed['documents'])} documents")
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
    new.set_defaults(func=_cmd_register)

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
