"""The bundle: everything a live run recorded, so a replay needs no key and no network.

Layout of a bundle directory (every file except `bundle.json` is listed there with its
SHA-256, and `Bundle.verify` checks them):

    bundle.json                    what ran: set, date, code identity, runs, PDFs, spend
    answers/<task>.run<N>.jsonl    model answers of one run (see proof.replay)
    reports/<task>.run<N>.json     the live run's report, without its per-item rows
    media/pdfs/<book>__<page>.pdf  the exact PDF every system was given
    media/equations.jsonl          per gold equation: image identity, size, text layer, context
    media/figures.jsonl            per figure image URL: identity and size
    systems/<system>/...           outputs of the other systems, in the intermediate form
                                   of proof.intermediate (chatbot recipe, parsers)
    gold/                          optional copy of the answer key (CC BY, with ATTRIBUTION.md)

The bundle holds content derived from CC BY OpenStax sections (PDFs, LaTeX, alt texts) and
nothing else: no keys, no host names, no user data. Dates come from the caller.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from readablestem.proof.errors import BundleError
from readablestem.proof.registry import sha256_file

BUNDLE_SCHEMA = 1
MANIFEST_NAME = "bundle.json"
SECTION_SEP = "__"


def pdf_name(section_id: str) -> str:
    """`physics/2-1-foo` -> `physics__2-1-foo.pdf`."""
    return section_id.replace("/", SECTION_SEP) + ".pdf"


def run_stem(task: str, repeat: int) -> str:
    return f"{task}.run{repeat}"


def dumps(obj: Any) -> str:
    """Deterministic JSON text: sorted keys, two-space indent, trailing newline."""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def dump_line(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Bundle:
    """A recorded run on disk. Opening one reads `bundle.json` only; call `verify`."""

    def __init__(self, root: Path, manifest: dict[str, Any]) -> None:
        self.root = root
        self.manifest = manifest

    @classmethod
    def open(cls, root: Path) -> Bundle:
        path = root / MANIFEST_NAME
        if not path.is_file():
            raise BundleError(
                f"no recorded run at {root.name}/ (no {MANIFEST_NAME}). Record one with "
                "`a11y-stem proof --live --max-usd N`, or point --bundle at an existing one"
            )
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BundleError(f"{root.name}/{MANIFEST_NAME} cannot be read: {exc}") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != BUNDLE_SCHEMA:
            raise BundleError(
                f"{root.name}/{MANIFEST_NAME}: schema_version must be {BUNDLE_SCHEMA}"
            )
        return cls(root, manifest)

    # ------------------------------------------------------------------ identity

    @property
    def set_id(self) -> str:
        return str(self.manifest.get("set_id", ""))

    @property
    def recorded_on(self) -> str:
        return str(self.manifest.get("recorded_on", ""))

    @property
    def code(self) -> dict[str, Any]:
        return dict(self.manifest.get("code", {}))

    @property
    def runs(self) -> list[dict[str, Any]]:
        return list(self.manifest.get("runs", []))

    @property
    def pdfs(self) -> list[dict[str, Any]]:
        return list(self.manifest.get("pdfs", []))

    @property
    def files(self) -> dict[str, str]:
        return dict(self.manifest.get("files", {}))

    # --------------------------------------------------------------------- paths

    def path(self, rel: str) -> Path:
        return self.root / rel

    def answers_path(self, task: str, repeat: int) -> Path:
        return self.path(f"answers/{run_stem(task, repeat)}.jsonl")

    def report_path(self, task: str, repeat: int) -> Path:
        return self.path(f"reports/{run_stem(task, repeat)}.json")

    @property
    def gold_dir(self) -> Path:
        return self.path("gold")

    @property
    def systems_dir(self) -> Path:
        return self.path("systems")

    # ------------------------------------------------------------------ contents

    def report(self, task: str, repeat: int) -> dict[str, Any]:
        try:
            return json.loads(self.report_path(task, repeat).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BundleError(f"report {run_stem(task, repeat)} cannot be read: {exc}") from exc

    def pdf_bytes(self, section_id: str) -> bytes:
        for entry in self.pdfs:
            if entry["section"] == section_id:
                return self.path(entry["file"]).read_bytes()
        raise BundleError(f"the bundle holds no PDF for {section_id}")

    def jsonl(self, rel: str) -> list[dict[str, Any]]:
        path = self.path(rel)
        if not path.is_file():
            return []
        rows = []
        with path.open(encoding="utf-8") as fh:
            for number, line in enumerate(fh, 1):
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except ValueError as exc:
                        raise BundleError(f"{rel}:{number}: not valid JSON") from exc
        return rows

    # ----------------------------------------------------------------- integrity

    def verify(self) -> list[str]:
        """Problems found, empty when every listed file is present and unchanged and no
        other file sits in the bundle. Reads every file once."""
        problems = []
        listed = self.files
        for rel, sha in sorted(listed.items()):
            path = self.path(rel)
            if not path.is_file():
                problems.append(f"missing: {rel}")
            elif sha256_file(path) != sha:
                problems.append(f"changed: {rel}")
        on_disk = {
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file() and p.name != MANIFEST_NAME
        }
        problems.extend(f"not listed: {rel}" for rel in sorted(on_disk - set(listed)))
        return problems

    def extend(
        self, source: Path, rel: str = "systems", extra: dict[str, Any] | None = None
    ) -> Bundle:
        """Add the files under `source` to this recorded run (below `rel`), hash them with
        the rest and rewrite the manifest. Existing files are never overwritten: a system
        is recorded once. `extra` is merged into the manifest (for example the spend)."""
        self.require_intact()
        added = []
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            target = self.root / rel / path.relative_to(source)
            if target.exists():
                raise BundleError(f"{target.relative_to(self.root)} is already in the recorded run")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            added.append(target)
        manifest = {k: v for k, v in self.manifest.items() if k != "files"}
        manifest.update(extra or {})
        files = {
            p.relative_to(self.root).as_posix(): sha256_file(p)
            for p in sorted(self.root.rglob("*"))
            if p.is_file() and p.name != MANIFEST_NAME
        }
        full = {**manifest, "files": files}
        (self.root / MANIFEST_NAME).write_text(dumps(full), encoding="utf-8")
        return Bundle(self.root, full)

    def require_intact(self) -> None:
        problems = self.verify()
        if problems:
            shown = "; ".join(problems[:3]) + (
                f" (and {len(problems) - 3} more)" if problems[3:] else ""
            )
            raise BundleError(f"the bundle {self.root.name}/ does not match its manifest: {shown}")


class BundleWriter:
    """Builds a bundle in `<root>.partial` and moves it to `root` only when `finish` runs,
    so an interrupted or incomplete run never looks like a recorded one."""

    def __init__(self, root: Path, *, resume: bool = False) -> None:
        self.root = root
        self.partial = root.with_name(root.name + ".partial")
        if root.exists() and any(root.iterdir()):
            raise BundleError(f"{root} already holds a recorded run; choose another --record dir")
        if self.partial.exists() and not resume:
            shutil.rmtree(self.partial)
        self.partial.mkdir(parents=True, exist_ok=True)

    def has(self, rel: str) -> bool:
        return (self.partial / rel).is_file()

    def read_json(self, rel: str) -> Any:
        return json.loads((self.partial / rel).read_text(encoding="utf-8"))

    def write_bytes(self, rel: str, data: bytes) -> None:
        path = self.partial / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def write_text(self, rel: str, text: str) -> None:
        self.write_bytes(rel, text.encode("utf-8"))

    def write_json(self, rel: str, obj: Any) -> None:
        self.write_text(rel, dumps(obj))

    def write_jsonl(self, rel: str, rows: Iterable[Any]) -> None:
        self.write_text(rel, "".join(dump_line(r) + "\n" for r in rows))

    def copy_tree(self, source: Path, rel: str) -> None:
        shutil.copytree(source, self.partial / rel, dirs_exist_ok=True)

    def finish(self, manifest: dict[str, Any]) -> Bundle:
        """Hash every file, write `bundle.json`, and move the directory into place."""
        files = {
            p.relative_to(self.partial).as_posix(): sha256_file(p)
            for p in sorted(self.partial.rglob("*"))
            if p.is_file()
        }
        full = {"schema_version": BUNDLE_SCHEMA, **manifest, "files": files}
        (self.partial / MANIFEST_NAME).write_text(dumps(full), encoding="utf-8")
        if self.root.exists():
            self.root.rmdir()  # empty (checked in __init__)
        os.replace(self.partial, self.root)
        return Bundle(self.root, full)
