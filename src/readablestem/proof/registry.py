"""Proof sets: which OpenStax sections a proof covers, and the pins that make it checkable.

A proof set is one JSON file in `proof/sets/`. It names the sections (book slug and page
slug), how to fetch their answer key from OpenStax (the archive release is fixed by the
`retrieved_at` date written into every gold record), and the SHA-256 of the gold set's
`manifest.json`, which in turn lists the SHA-256 of every gold file. It also names where
the recorded run (the "bundle") lives, how often each task is repeated, and, for a
held-out set, the preregistration file and its SHA-256.

Pins are checked before anything runs. A pin that does not match stops the run
(`PinMismatch`); a file that is simply not in this checkout is reported as "absent" and
the report says so, so a missing preregistration copy is visible and not silently
skipped.

Nothing here reads the clock or the network.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from readablestem.proof.errors import PinMismatch, ProofError

SCHEMA_VERSION = 1
SETS_DIR = Path("proof") / "sets"
INDEX_FILE = "index.json"
TASKS = ("document", "equations", "headings", "figures")
# Tasks that call a model; "headings" runs the layout and the heading rules only.
MODEL_TASKS = ("document", "equations", "figures")
SHA256_LEN = 64


@dataclass(frozen=True)
class SectionRef:
    book: str
    page: str

    @property
    def id(self) -> str:
        return f"{self.book}/{self.page}"


@dataclass(frozen=True)
class FetchSpec:
    """How to rebuild the gold set from OpenStax (`gold fetch`, one call per book)."""

    date: str  # recorded as `retrieved_at` in every record, so it is part of the hash
    allow_retired: bool
    sections: tuple[SectionRef, ...]
    # book slug -> (archive release, book version): read the book from that release and not
    # the current one. Needed when OpenStax may have published a newer release since.
    pins: dict[str, tuple[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Prereg:
    file: str  # path in this checkout where a public copy would live
    sha256: str
    note: str = ""


@dataclass(frozen=True)
class ProofSet:
    id: str
    title: str
    held_out: bool
    description: str
    gold_dir: str | None  # relative to the project root; None: the bundle's own gold/
    gold_manifest_sha256: str
    fetch: FetchSpec
    prereg: Prereg | None
    repeats: dict[str, int]
    tasks: tuple[str, ...]
    concurrency: int
    bundle: str
    signoff: tuple[str, ...] = ()
    suggested_max_usd: float | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    limits: tuple[str, ...] = field(default_factory=tuple)

    @property
    def sections(self) -> tuple[SectionRef, ...]:
        return self.fetch.sections

    def runs(self) -> list[tuple[str, int]]:
        """(task, repeat) in run order; repeats count from 1."""
        return [(task, n) for task in self.tasks for n in range(1, self.repeats[task] + 1)]


# ------------------------------------------------------------------------- loading


def _need(data: dict[str, Any], key: str, kind: type | tuple[type, ...], where: str) -> Any:
    if key not in data:
        raise ProofError(f"{where}: missing {key!r}")
    value = data[key]
    if not isinstance(value, kind) or (isinstance(value, bool) and kind is not bool):
        raise ProofError(f"{where}: {key!r} has the wrong type ({type(value).__name__})")
    return value


def _sha(value: Any, where: str) -> str:
    text = str(value)
    if len(text) != SHA256_LEN or any(c not in "0123456789abcdef" for c in text):
        raise ProofError(f"{where}: not a SHA-256 hex digest")
    return text


def parse_set(data: dict[str, Any], *, where: str = "proof set") -> ProofSet:
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ProofError(f"{where}: schema_version must be {SCHEMA_VERSION}")
    gold = _need(data, "gold", dict, where)
    fetch = _need(gold, "fetch", dict, f"{where} gold")
    sections = tuple(
        SectionRef(
            book=str(_need(s, "book", str, f"{where} section")),
            page=str(_need(s, "page", str, f"{where} section")),
        )
        for s in _need(fetch, "sections", list, f"{where} gold.fetch")
    )
    if not sections:
        raise ProofError(f"{where}: no sections")
    pins = {}
    for book, raw_pin in dict(fetch.get("pins", {})).items():
        if not isinstance(raw_pin, dict):
            raise ProofError(f"{where}: pin for {book!r} must be an object")
        pins[str(book)] = (
            str(_need(raw_pin, "archive_version", str, f"{where} pin {book}")),
            str(_need(raw_pin, "version", str, f"{where} pin {book}")),
        )
    if len({s.id for s in sections}) != len(sections):
        raise ProofError(f"{where}: a section is listed twice")
    tasks = tuple(data.get("tasks", TASKS))
    unknown = [t for t in tasks if t not in TASKS]
    if unknown or not tasks:
        raise ProofError(f"{where}: tasks must come from {', '.join(TASKS)}; got {list(tasks)}")
    repeats = {t: int(data.get("repeats", {}).get(t, 1)) for t in tasks}
    if any(n < 1 for n in repeats.values()):
        raise ProofError(f"{where}: repeats must be 1 or more")
    prereg = None
    if data.get("preregistration"):
        raw = data["preregistration"]
        prereg = Prereg(
            file=str(_need(raw, "file", str, f"{where} preregistration")),
            sha256=_sha(_need(raw, "sha256", str, f"{where} preregistration"), where),
            note=str(raw.get("note", "")),
        )
    held_out = bool(_need(data, "held_out", bool, where))
    if held_out and prereg is None:
        raise ProofError(f"{where}: a held-out set must pin its preregistration")
    cap = data.get("suggested_max_usd")
    return ProofSet(
        id=str(_need(data, "id", str, where)),
        title=str(_need(data, "title", str, where)),
        held_out=held_out,
        description=str(data.get("description", "")),
        gold_dir=gold.get("dir"),
        gold_manifest_sha256=_sha(_need(gold, "manifest_sha256", str, f"{where} gold"), where),
        fetch=FetchSpec(
            date=str(_need(fetch, "date", str, f"{where} gold.fetch")),
            allow_retired=bool(fetch.get("allow_retired", False)),
            sections=sections,
            pins=pins,
        ),
        prereg=prereg,
        repeats=repeats,
        tasks=tasks,
        concurrency=int(data.get("concurrency", 4)),
        bundle=str(_need(data, "bundle", str, where)),
        signoff=tuple(str(s) for s in data.get("signoff", ())),
        suggested_max_usd=float(cap) if cap is not None else None,
        notes=tuple(str(n) for n in data.get("notes", ())),
        limits=tuple(str(n) for n in data.get("limits", ())),
    )


def load_set(path: Path) -> ProofSet:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProofError(f"cannot read proof set {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProofError(f"{path.name}: expected a JSON object")
    return parse_set(data, where=path.name)


def list_sets(root: Path) -> list[ProofSet]:
    folder = root / SETS_DIR
    if not folder.is_dir():
        return []
    return [load_set(p) for p in sorted(folder.glob("*.json")) if p.name != INDEX_FILE]


def default_set_id(root: Path) -> str | None:
    index = root / SETS_DIR / INDEX_FILE
    if not index.is_file():
        return None
    try:
        value = json.loads(index.read_text(encoding="utf-8")).get("default")
    except (OSError, ValueError):
        return None
    return value if isinstance(value, str) else None


def resolve_set(root: Path, set_id: str | None) -> ProofSet:
    """The set named `set_id`, or the repository's default one."""
    wanted = set_id or default_set_id(root)
    sets = {s.id: s for s in list_sets(root)}
    if wanted is None:
        raise ProofError(f"no proof set: add one to {SETS_DIR.as_posix()}/ or pass --set")
    if wanted not in sets:
        known = ", ".join(sorted(sets)) or "none"
        raise ProofError(f"no proof set {wanted!r} (known: {known})")
    return sets[wanted]


# ---------------------------------------------------------------------------- pins


@dataclass(frozen=True)
class PinCheck:
    name: str
    status: Literal["ok", "absent", "mismatch"]
    detail: str = ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_gold(gold_dir: Path, pset: ProofSet) -> list[PinCheck]:
    """The gold directory against the set's pin and against its own manifest.

    The manifest's hash is the pin; the manifest lists each JSONL file's hash, so a
    changed or extra file shows up even when someone leaves manifest.json alone.
    """
    manifest_path = gold_dir / "manifest.json"
    if not manifest_path.is_file():
        return [PinCheck("gold manifest", "absent", f"{gold_dir.name}/manifest.json not found")]
    checks = []
    actual = sha256_file(manifest_path)
    if actual == pset.gold_manifest_sha256:
        checks.append(PinCheck("gold manifest", "ok", f"sha256 {actual[:12]}"))
    else:
        checks.append(
            PinCheck(
                "gold manifest",
                "mismatch",
                f"sha256 {actual[:12]}, the proof set pins {pset.gold_manifest_sha256[:12]}",
            )
        )
        return checks
    try:
        listed = {f["path"]: f["sha256"] for f in json.loads(manifest_path.read_text())["files"]}
    except (OSError, ValueError, KeyError, TypeError):
        return [*checks, PinCheck("gold files", "mismatch", "manifest.json cannot be read")]
    bad = [p for p, sha in sorted(listed.items()) if not _file_is(gold_dir / p, sha)]
    on_disk = {p.relative_to(gold_dir).as_posix() for p in gold_dir.glob("openstax/*/*.jsonl")}
    extra = sorted(on_disk - set(listed))
    if bad or extra:
        detail = []
        if bad:
            detail.append(f"{len(bad)} listed file(s) changed or missing (first: {bad[0]})")
        if extra:
            detail.append(f"{len(extra)} file(s) not in the manifest (first: {extra[0]})")
        checks.append(PinCheck("gold files", "mismatch", "; ".join(detail)))
    else:
        checks.append(PinCheck("gold files", "ok", f"{len(listed)} files match the manifest"))
    wanted = {s.id for s in pset.sections}
    have = {f"{p.split('/')[1]}/{p.split('/')[2].removesuffix('.jsonl')}" for p in listed}
    if wanted != have:
        checks.append(
            PinCheck("gold sections", "mismatch", "the gold files are not the set's sections")
        )
    else:
        checks.append(PinCheck("gold sections", "ok", f"{len(wanted)} sections"))
    return checks


def _file_is(path: Path, sha: str) -> bool:
    return path.is_file() and sha256_file(path) == sha


def check_prereg(pset: ProofSet, root: Path) -> PinCheck | None:
    """The preregistration file, when this checkout has a copy of it."""
    if pset.prereg is None:
        return None
    path = root / pset.prereg.file
    if not path.is_file():
        return PinCheck(
            "preregistration",
            "absent",
            f"no copy at {pset.prereg.file}; the set pins sha256 {pset.prereg.sha256[:12]}",
        )
    actual = sha256_file(path)
    if actual == pset.prereg.sha256:
        return PinCheck("preregistration", "ok", f"sha256 {actual[:12]}")
    return PinCheck(
        "preregistration",
        "mismatch",
        f"{pset.prereg.file} has sha256 {actual[:12]}, the set pins {pset.prereg.sha256[:12]}",
    )


def require_ok(checks: list[PinCheck]) -> None:
    """Stop on any mismatch ("absent" is reported, not fatal: see the module docstring)."""
    bad = [c for c in checks if c.status == "mismatch"]
    if bad:
        raise PinMismatch("; ".join(f"{c.name}: {c.detail}" for c in bad))
