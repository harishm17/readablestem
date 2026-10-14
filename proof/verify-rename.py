#!/usr/bin/env python3
"""Check that the registered files differ from the tag only by the product's new name.

On 2026-10-14 the product was renamed ReadableSTEM and the old name was replaced in every text
file of `proof/` and in `PROOF.md` (Addendum 8). This script rebuilds that change from the
frozen tag, so a reader does not have to trust it:

    python3 proof/verify-rename.py                  # check (default tag: proof-heldout-2026-10)
    python3 proof/verify-rename.py --tag TAG        # another ref
    python3 proof/verify-rename.py --diff           # also print a unified diff of the name changes
    python3 proof/verify-rename.py --write          # write the rebuilt files into the working tree

What it does, for every file of `proof/` and `PROOF.md` at the tag:

1. A binary file (it does not decode as UTF-8, has a NUL byte, or has a binary extension) is
   taken as it is.
2. In a text file the old name is replaced (`rename_text`): the import name by `readablestem`;
   a command, a path or a package name by `readablestem`; the name in prose by `ReadableSTEM`.
   A few strings are never changed (`PROTECTED`): they are inputs of the registered draw and
   sample seeds, and changing a letter would change which sections or items were drawn.
3. A SHA-256 written in a text file for a file that changed (in full, or abbreviated to 8
   characters or more) is replaced by the new SHA-256 of that file, until nothing changes. This
   is how the hash lists and the manifests are regenerated. Hashes of files that did not change,
   and of files that are not in this repository (version 1 of the registration, the answer key's
   records), are left alone.
4. The result is compared byte for byte with the working tree. The only files the working tree
   may have besides those are `ALLOWED_NEW` (this script, the addendum, and the original hash
   lists), and `PREREG-v2.sha256` may have the extra lines that hash them (`ADDED_TO_LIST`).

Exit status 0 and the line `OK` mean that nothing else differs. The script needs `git` and the
tag in the repository (a clone with tags). It uses only the standard library. It writes nothing
unless `--write` is given.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TAG = "proof-heldout-2026-10"
SCOPE = ("proof/", "PROOF.md")

# The old name is written in pieces so that a search for it finds only what is left on purpose.
OLD = "a11y" + "-stem"
OLD_IMPORT = "a11y" + "_stem"
NEW_IDENT = "readablestem"
NEW_NAME = "ReadableSTEM"

# Never changed: the exact texts hashed into the registered draws and samples.
PROTECTED = [
    OLD + " held-out OpenStax set, pre-registered 2026-10-13",
    OLD + " held-out scorer sample 2026-10-13",
    OLD + " invented-content audit sample 2026-10-13",
    OLD + " held-out labels 2026-10-13",
    OLD + " invented-content figure sample",
]

_COMMANDS = r"(?:gold|eval|proof|serve|demo|ops|convert|db|devset|stemcheck|--version|--help)"
# Places where the old name is a command, a path or a package name (becomes `readablestem`).
_IDENTIFIER = [
    re.compile(r"(?<=[/\\])" + OLD),  # a path segment: ~/.cache/<name>/heldout
    re.compile(OLD + r"(?=-(?:proof|int|site-build)\b)"),  # <name>-proof
    re.compile(OLD + r"(?=\.[A-Za-z])"),  # <name>.db
    re.compile(OLD + r"(?=/)"),  # <name>/data/gold
    re.compile(r"(?<=[`\"])" + OLD + r"(?= " + _COMMANDS + r")"),  # `<name> gold fetch`
    re.compile(r"(?<=run )" + OLD + r"(?= " + _COMMANDS + r")"),
    re.compile(r"(?<=\$ )" + OLD + r"(?= " + _COMMANDS + r")"),
    re.compile(r"(?<=group browser )" + OLD + r"(?= )"),  # uv run --group browser <name> ...
    re.compile(r"(?<=\")" + OLD + r"(?=\", \")"),  # ["<name>", "proof"]
    re.compile(r"(?<=name = \")" + OLD + r"(?=\")"),  # pyproject / uv.lock
    re.compile(r"(?m)^(\s*\")" + OLD + r"(?=\",$)"),  # a dependency line
    re.compile(r"(?m)^" + OLD + r"(?= = )"),  # a source table key
    re.compile(r"(?<=-proof )" + OLD + r"(?= proof)"),  # docker run IMAGE <name> proof
    re.compile(r"(?<=cd )" + OLD + r"(?= &&)"),  # cd <name> && ...
]

# New files that belong to Addendum 8; the working tree may have them and the tag cannot.
ALLOWED_NEW = (
    "proof/PREREGISTRATION-ADDENDUM-8.md",
    "proof/verify-rename.py",
    "proof/original-hashes/",
)
# Lines that hash those files, appended to the list that hashed Addenda 6 and 7.
LIST_FILE = "proof/PREREG-v2.sha256"
ADDED_TO_LIST = ("PREREGISTRATION-ADDENDUM-8.md", "verify-rename.py")
BINARY_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".gz", ".zip", ".docx", ".woff", ".woff2")


def rename_text(text: str) -> str:
    """The text with the old name replaced; see the module docstring, point 2."""
    kept: dict[str, str] = {}
    for index, protected in enumerate(PROTECTED):
        if protected in text:
            token = f"\x00{index}\x00"
            kept[token] = protected
            text = text.replace(protected, token)
    text = text.replace(OLD_IMPORT, NEW_IDENT)
    for pattern in _IDENTIFIER:
        text = pattern.sub(lambda m: m.group(0).replace(OLD, NEW_IDENT), text)
    text = text.replace(OLD, NEW_NAME)
    for token, protected in kept.items():
        text = text.replace(token, protected)
    return text


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def tag_files(tag: str) -> dict[str, bytes]:
    names = _git("ls-tree", "-r", "--name-only", "-z", tag).decode("utf-8").split("\0")
    out = {}
    for name in sorted(n for n in names if n and n.startswith(SCOPE)):
        out[name] = _git("show", f"{tag}:{name}")
    return out


def as_text(name: str, data: bytes) -> str | None:
    if name.endswith(BINARY_SUFFIXES) or b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


_HEX = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{8,64}(?![0-9A-Za-z])")


def replace_hashes(text: str, known: dict[str, str]) -> str:
    """Hashes (full or abbreviated to 8 or more characters) of changed files, renewed."""

    def one(match: re.Match[str]) -> str:
        token = match.group(0)
        hits = {old for old in known if old.startswith(token)}
        if not hits:
            return token
        if len(hits) > 1:
            raise SystemExit(f"{token} is the start of {len(hits)} registered hashes")
        return known[hits.pop()][: len(token)]

    return _HEX.sub(one, text)


def rebuild(tag: str, hash_map: dict[str, str] | None = None) -> dict[str, bytes]:
    """The files of the tag with the name replaced and the hashes of changed files renewed.
    `hash_map`, if given, is filled with the old hash -> new hash of every file that changed."""
    original = tag_files(tag)
    texts = {n: as_text(n, d) for n, d in original.items()}
    named = {n: (rename_text(t) if t is not None else None) for n, t in texts.items()}
    old_hash = {n: hashlib.sha256(d).hexdigest() for n, d in original.items()}
    known: dict[str, str] = {}
    for _ in range(12):
        built = {n: (replace_hashes(t, known) if t is not None else None) for n, t in named.items()}
        new_known = {}
        for n, t in built.items():
            if t is not None:
                new = hashlib.sha256(t.encode("utf-8")).hexdigest()
                if new != old_hash[n]:
                    new_known[old_hash[n]] = new
        if new_known == known:
            break
        known = new_known
    else:
        raise SystemExit(
            "the renewed hashes did not settle (a file cites the hash of another that cites it)"
        )
    if hash_map is not None:
        hash_map.update(known)
    return {n: (t.encode("utf-8") if t is not None else original[n]) for n, t in built.items()}


def list_lines(tree: Path) -> str:
    return "".join(
        f"{hashlib.sha256((tree / 'proof' / name).read_bytes()).hexdigest()}  {name}\n"
        for name in ADDED_TO_LIST
    )


def tracked() -> list[str]:
    names = _git("ls-files", "-z", "--", *SCOPE).decode("utf-8").split("\0")
    return sorted(n for n in names if n)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tag", default=DEFAULT_TAG)
    parser.add_argument("--diff", action="store_true", help="print the name changes as a diff")
    parser.add_argument("--write", action="store_true", help="write the rebuilt files")
    parser.add_argument(
        "--hash-map", type=Path, help="write the old -> new SHA-256 of each changed file (JSON)"
    )
    args = parser.parse_args(argv)

    hashes: dict[str, str] = {}
    expected = rebuild(args.tag, hashes)
    if args.hash_map:
        args.hash_map.write_text(
            json.dumps(hashes, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
    original = tag_files(args.tag)
    if args.write:
        for name, data in expected.items():
            target = ROOT / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        print(f"wrote {len(expected)} files")
        return 0

    problems: list[str] = []
    changed = 0
    for name, data in expected.items():
        path = ROOT / name
        if not path.is_file():
            problems.append(f"missing: {name}")
            continue
        actual = path.read_bytes()
        if name == LIST_FILE:
            data = data + list_lines(ROOT).encode("utf-8")
        if actual != data:
            problems.append(f"differs from the rebuilt file: {name}")
        if data != original[name]:
            changed += 1
            if args.diff and (a := as_text(name, original[name])) is not None:
                b = data.decode("utf-8")
                sys.stdout.writelines(
                    difflib.unified_diff(
                        a.splitlines(keepends=True),
                        b.splitlines(keepends=True),
                        f"{args.tag}:{name}",
                        name,
                        n=0,
                    )
                )
    for name in tracked():
        if name not in expected and not name.startswith(ALLOWED_NEW):
            problems.append(f"not in the tag and not one of the added files: {name}")
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} problem(s)")
        return 1
    print(f"OK: {len(expected)} files of the tag; {changed} changed, only by the name (and the")
    print("hashes of files that changed); every other file is byte for byte as at the tag.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
