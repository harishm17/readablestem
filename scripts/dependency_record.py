"""Write and check docs/dependencies.md: every dependency, its version, license and rule.

Usage:
    uv run python scripts/dependency_record.py --write   # after uv.lock or the Dockerfile changes
    uv run python scripts/dependency_record.py --check   # what the test suite runs

A version comes from uv.lock or the Dockerfile and a Python package's license from the
package's own metadata (the installed copy; for a package this machine has not installed,
the same metadata as the package index serves it, asked for only by `--write`). What people
decide is in data/dependency_facts.toml: why a package is here, the licenses that are
allowed, and for a package that reads a stranger's bytes, the process that limits it and the
tests that feed it hostile input. The check fails for anything the record does not cover.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
import tomllib
import urllib.request
from pathlib import Path

RECORD = Path("docs/dependencies.md")
FACTS = Path("data/dependency_facts.toml")
PROJECT = "readablestem"
INDEX_URL = "https://pypi.org/pypi/{name}/{version}/json"
NOT_STATED = "not stated"
IMAGE, TRACING, DEVELOPMENT, BROWSER = (
    "image",
    "image (tracing)",
    "development",
    "browser checks",
)
HEADER = "| Package | Version | License, as the package states it | Where | Why it is here |"


# ------------------------------------------------------------------------------ the lock


def lock_packages(root: Path) -> dict[str, dict]:
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    return {package["name"]: package for package in lock["package"]}


def _closure(packages: dict[str, dict], names: list[str]) -> set[str]:
    seen: set[str] = set()
    todo = list(names)
    while todo:
        name = todo.pop()
        if name in seen or name not in packages:
            continue
        seen.add(name)
        package = packages[name]
        todo += [d["name"] for d in package.get("dependencies", [])]
        for extra in package.get("optional-dependencies", {}).values():
            todo += [d["name"] for d in extra]
    return seen


def where_installed(packages: dict[str, dict]) -> dict[str, str]:
    """Where each locked package ends up. The image is built with `uv sync --no-dev --extra
    tracing` (Dockerfile), so the project's dependencies and the tracing extra are in it."""
    project = packages[PROJECT]
    groups = project.get("dev-dependencies", {})
    order = [
        (IMAGE, [d["name"] for d in project.get("dependencies", [])]),
        (TRACING, [d["name"] for d in project.get("optional-dependencies", {}).get("tracing", [])]),
        (DEVELOPMENT, [d["name"] for d in groups.get("dev", [])]),
        (BROWSER, [d["name"] for d in groups.get("browser", [])]),
    ]
    for group in sorted(set(groups) - {"dev", "browser"}):
        order.append((group, [d["name"] for d in groups[group]]))
    where: dict[str, str] = {}
    for label, names in order:
        for name in sorted(_closure(packages, names)):
            where.setdefault(name, label)
    return where


def direct_names(packages: dict[str, dict]) -> set[str]:
    project = packages[PROJECT]
    lists = [project.get("dependencies", [])]
    lists += project.get("optional-dependencies", {}).values()
    lists += project.get("dev-dependencies", {}).values()
    return {d["name"] for items in lists for d in items}


def needed_by(packages: dict[str, dict], name: str) -> list[str]:
    found = []
    for other, package in packages.items():
        named = [d["name"] for d in package.get("dependencies", [])]
        for extra in package.get("optional-dependencies", {}).values():
            named += [d["name"] for d in extra]
        if name in named and other != PROJECT:
            found.append(other)
    return sorted(found)


def outside_image(root: Path) -> set[str]:
    """The locked packages the image does not carry (development and browser tools)."""
    where = where_installed(lock_packages(root))
    return {name for name, label in where.items() if label not in (IMAGE, TRACING)}


# ---------------------------------------------------------------------------- licenses


def stated_license(expression: str | None, text: str | None, classifiers: list[str]) -> str:
    """A package's license as its metadata states it: the license expression, else a one-line
    `License` field, else its license classifiers."""
    if expression and expression.strip():
        return expression.strip()
    text = (text or "").strip()
    if text and "\n" not in text and len(text) <= 100:
        return text
    named = [c.rsplit(" :: ", 1)[-1] for c in classifiers if c.startswith("License ::")]
    named = [n for n in named if n != "DFSG approved"]
    return " / ".join(sorted(named)) if named else NOT_STATED


def installed_license(name: str, version: str) -> str | None:
    try:
        found = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    if found.version != version:
        return None
    meta = found.metadata
    return stated_license(
        meta.get("License-Expression"), meta.get("License"), meta.get_all("Classifier") or []
    )


def index_license(name: str, version: str) -> str:
    request = urllib.request.Request(
        INDEX_URL.format(name=name, version=version),
        headers={"User-Agent": "dependency-record/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as reply:
        info = json.load(reply)["info"]
    return stated_license(
        info.get("license_expression"), info.get("license"), info.get("classifiers") or []
    )


def license_allowed(stated: str, facts: dict) -> bool:
    """Permissive under the list of the facts file: of the alternatives an `OR` gives, one
    whose every part is on the list."""
    rules = facts["licenses"]
    allowed = set(rules["allowed"])
    names = rules.get("names", {})

    def one(part: str) -> bool:
        part = part.strip().strip("()").strip()
        return names.get(part, part) in allowed

    return any(
        all(one(part) for part in re.split(r"\s+AND\s+|\s*/\s*", alternative))
        for alternative in re.split(r"\s+OR\s+", stated)
    )


# -------------------------------------------------------------------------- the Dockerfile


def dockerfile_installs(root: Path) -> list[dict]:
    """What the Dockerfile installs besides the lock: base images, apt and pip packages.
    Each has `name`, `version` (None when the line names none), `kind` and `when` (the build
    argument that switches it on, or `always`)."""
    text = (root / "Dockerfile").read_text(encoding="utf-8")
    text = re.sub(r"^\s*#.*\n", "", text, flags=re.MULTILINE)
    instructions = re.sub(r"\\\n", " ", text).splitlines()
    found: dict[tuple[str, str], dict] = {}

    def add(kind: str, name: str, version: str | None, when: str) -> None:
        found.setdefault(
            (kind, name), {"kind": kind, "name": name, "version": version, "when": when}
        )

    for line in instructions:
        line = line.strip()
        base = re.match(
            r"FROM\s+(\S+?)(?::([^@\s]+))?(?:@(sha256:[0-9a-f]{64}))?(?:\s+AS\s+\S+)?$", line
        )
        if base:
            if base.group(1) in {"build", "runtime", "math-reader", "uv"}:
                continue  # an earlier stage of this file
            version = (
                f"{base.group(2) or 'latest'} @ {base.group(3)[:19]}" if base.group(3) else None
            )
            add("base image", base.group(1), version, "always")
            continue
        if not line.startswith("RUN "):
            continue
        switch = re.search(r'"\$(INSTALL_\w+)" = "true"', line)
        when = f"{switch.group(1)}=true" if switch else "always"
        for command in re.split(r"&&|;", line):
            words = command.replace("RUN", " ").split()
            while words and words[0] in {"if", "then", "else", "do"}:
                words = words[1:]
            if words[:2] == ["apt-get", "install"]:
                for word in words[2:]:
                    if not word.startswith("-"):
                        name, _, version = word.partition("=")
                        add("Debian package", name, version or None, when)
            elif words[:2] == ["pip", "install"]:
                for word in words[2:]:
                    if not word.startswith("-"):
                        name, _, version = word.strip("\"'").partition("==")
                        add("Python package (build stage)", name, version or None, when)
    return list(found.values())


# -------------------------------------------------------------------------------- the record


def recorded(root: Path) -> dict[str, tuple[str, str]]:
    """The rows of the record as it stands: name -> (version, license)."""
    path = root / RECORD
    if not path.is_file():
        return {}
    rows: dict[str, tuple[str, str]] = {}
    section = path.read_text(encoding="utf-8").split(HEADER, 1)
    for line in section[1].splitlines()[2:] if len(section) == 2 else []:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split(" | ")]
        rows[cells[0].strip("`")] = (cells[1], cells[2])
    return rows


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def build(root: Path, *, fetch: bool = False) -> tuple[str, list[str]]:
    """The record as it should read, and what is wrong with the dependencies."""
    facts = tomllib.loads((root / FACTS).read_text(encoding="utf-8"))
    packages = lock_packages(root)
    where = where_installed(packages)
    direct = direct_names(packages)
    known = recorded(root)
    python_facts: dict[str, dict] = facts.get("python", {})
    accepted: dict[str, dict] = facts.get("accepted", {})
    problems: list[str] = []
    rows, readers, exceptions = [], [], []

    def reader(label: str, name: str, entry: dict) -> None:
        if "reads" not in entry:
            return
        hostile = entry.get("hostile", [])
        if not entry.get("runs_in"):
            problems.append(f"{name}: reads {entry['reads']} and names no `runs_in`")
        if not hostile:
            problems.append(f"{name}: reads {entry['reads']} and names no `hostile` cases")
        for path in hostile:
            if not (root / path).is_file():
                problems.append(f"{name}: its hostile case {path} does not exist")
        cases = ", ".join(f"`{path}`" for path in hostile)
        readers.append(
            f"| {label} | {_cell(entry['reads'])} | {_cell(entry.get('runs_in', ''))} | {cases} |"
        )

    for name in sorted(n for n in packages if n != PROJECT):
        version = packages[name]["version"]
        stated = installed_license(name, version)
        if stated is None and known.get(name, ("", ""))[0] == version:
            stated = known[name][1]
        if stated is None and fetch:
            stated = index_license(name, version)
        if stated is None:
            problems.append(
                f"{name} {version}: no row in {RECORD} (run scripts/dependency_record.py --write)"
            )
            continue
        entry = python_facts.get(name, {})
        if name in direct and not entry.get("use"):
            problems.append(f"{name}: pyproject.toml names it and {FACTS} has no `use` for it")
        if name in accepted:
            if accepted[name]["license"] != stated:
                problems.append(
                    f"{name} {version}: its license reads {stated!r}, and the accepted "
                    f"license is {accepted[name]['license']!r}"
                )
            elif license_allowed(stated, facts):
                problems.append(f"{name}: its license is permissive, so [accepted.{name}] can go")
            exceptions.append(f"| `{name}` | {_cell(stated)} | {_cell(accepted[name]['why'])} |")
        elif not license_allowed(stated, facts):
            problems.append(
                f"{name} {version}: the license {stated!r} is not on the allowed list and "
                f"{FACTS} does not accept it"
            )
        users = needed_by(packages, name)
        why = entry.get("use") or (
            "needed by "
            + ", ".join(users[:3])
            + (f" and {len(users) - 3} more" if users[3:] else "")
        )
        rows.append(f"| `{name}` | {version} | {_cell(stated)} | {where[name]} | {_cell(why)} |")
        reader(f"`{name}`", name, entry)

    for name in sorted(set(python_facts) - set(packages)):
        problems.append(f"{name}: {FACTS} describes it and uv.lock does not hold it")
    for name in sorted(set(accepted) - set(packages)):
        problems.append(f"{name}: {FACTS} accepts its license and uv.lock does not hold it")

    system_facts: dict[str, dict] = facts.get("system", {})
    installs = dockerfile_installs(root)
    system_rows = []
    for item in sorted(installs, key=lambda i: (i["kind"], i["name"])):
        name = item["name"]
        entry = system_facts.get(name)
        if entry is None:
            problems.append(f"{name}: the Dockerfile installs it and {FACTS} has no [system] entry")
            continue
        if item["version"]:
            version = item["version"]
        elif item["kind"] == "Debian package" and entry.get("follows"):
            version = f"not fixed: {entry['follows']}"
        else:
            version = "not fixed"
            problems.append(
                f"{name}: the Dockerfile names no version"
                + (" or digest" if item["kind"] == "base image" else "")
                + (
                    f" and {FACTS} gives no `follows` rule"
                    if item["kind"] == "Debian package"
                    else ""
                )
            )
        stated = entry.get("license", NOT_STATED)
        if not (entry.get("run_only") or entry.get("license_ok") or license_allowed(stated, facts)):
            problems.append(
                f"{name}: the license {stated!r} is not on the allowed list, and it is neither "
                "`run_only` nor given a `license_ok` reason"
            )
        run_only = "; only ever run as a child process" if entry.get("run_only") else ""
        system_rows.append(
            f"| `{name}` | {item['kind']} | {_cell(version)} | {_cell(stated)}{run_only} | "
            f"{item['when']} | {_cell(entry['use'])} |"
        )
        reader(f"`{name}` ({item['kind']})", name, entry)
    for name in sorted(set(system_facts) - {item["name"] for item in installs}):
        problems.append(f"{name}: {FACTS} describes it and the Dockerfile does not install it")

    allowed = ", ".join(facts["licenses"]["allowed"])
    text = "\n".join(
        [
            "# Dependencies",
            "",
            "Written by `scripts/dependency_record.py --write`; do not edit it by hand. Versions",
            "come from `uv.lock` and the `Dockerfile`, a Python package's license from the",
            "package's own metadata, and the rest from `data/dependency_facts.toml`. The test",
            "suite fails when this file is out of date, when a locked package has no row, when",
            "a license is neither on the allowed list nor accepted below, when a package that",
            "reads a stranger's bytes names no limits or no hostile-input tests, and when the",
            "`Dockerfile` installs something with neither a version nor a stated rule.",
            "",
            f"Allowed licenses: {allowed}. A program that is only ever run as a child process",
            "and never linked may carry another license.",
            "",
            "## What reads a stranger's bytes",
            "",
            "| Dependency | Reads | Runs in | Hostile-input tests |",
            "|---|---|---|---|",
            *readers,
            "",
            "## Licenses outside the allowed list",
            "",
            "| Package | License | Why it is accepted |",
            "|---|---|---|",
            *exceptions,
            "",
            "## Python packages (`uv.lock`)",
            "",
            f'{len(rows)} packages. "Where" is the first of: the image, the image\'s tracing',
            "extra, the development tools, the browser checks.",
            "",
            HEADER,
            "|---|---|---|---|---|",
            *rows,
            "",
            "## What the `Dockerfile` installs",
            "",
            "| Dependency | Kind | Version | License | Installed | Why it is here |",
            "|---|---|---|---|---|---|",
            *system_rows,
            "",
        ]
    )
    return text, problems


def check(root: Path) -> list[str]:
    """Everything wrong with the record and the dependencies; empty when all is in order."""
    text, problems = build(root)
    path = root / RECORD
    if not problems and (not path.is_file() or path.read_text(encoding="utf-8") != text):
        problems.append(f"{RECORD} is out of date (run scripts/dependency_record.py --write)")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write or check docs/dependencies.md.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    if args.check:
        problems = check(args.root)
    else:
        text, problems = build(args.root, fetch=True)
        (args.root / RECORD).write_text(text, encoding="utf-8")
        print(f"wrote {RECORD}")
    for problem in problems:
        print(f"- {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
