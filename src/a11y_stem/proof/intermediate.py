"""The shared form every other system's output is converted to before scoring.

The chatbot recipe and the open PDF parsers produce HTML, Markdown or JSON. Each has its own
adapter (written by whoever runs that arm) that reads the tool's most structured output
and writes one JSON file per system and section in this form, so that all systems are
scored by the same code. The form is the one the parser plan fixes (section 4.1):

    {"arm": "docling-default", "versions": {...}, "settings": {...},
     "section": "college-physics/29-3-photon-energies-...", "status": "ok",
     "seconds": 12.3,
     "equations": [{"order": 0, "display": "block", "latex": "...", "raw": "..."}],
     "headings":  [{"order": 0, "level": 2, "text": "..."}],
     "blocks":    [{"order": 0, "kind": "para", "text": "..."}],
     "figures":   [{"order": 0, "caption_ref": "Figure 1.2", "image_kept": true,
                    "text_alternative": null}],
     "tables":    [{"order": 0, "grid": [["a", "b"]]}],
     "html_path": null}

`status` is one of ok, crashed, timeout, empty, truncated. Anything but ok scores the
section as found nothing (preregistration section 9). The files live in the bundle at
`systems/<arm>/<book>__<page>.json`, or `...<page>.run<N>.json` when the arm is repeated.
Logs and reports never hold the text in these files; only counts and codes.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from a11y_stem.proof.bundle import SECTION_SEP, Bundle
from a11y_stem.proof.errors import BundleError

STATUSES = ("ok", "crashed", "timeout", "empty", "truncated")
_FILE = re.compile(r"^(?P<section>.+?)(?:\.run(?P<run>\d+))?\.json$")


@dataclass(frozen=True)
class SystemSection:
    arm: str
    section: str
    status: str
    run: int = 1
    seconds: float | None = None
    versions: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    equations: list[dict[str, Any]] = field(default_factory=list)
    headings: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    figures: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    html_path: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def parse_system_section(data: dict[str, Any], *, where: str, run: int = 1) -> SystemSection:
    for key in ("arm", "section", "status"):
        if not isinstance(data.get(key), str):
            raise BundleError(f"{where}: {key!r} is missing or not a string")
    if data["status"] not in STATUSES:
        raise BundleError(f"{where}: status must be one of {', '.join(STATUSES)}")
    lists: dict[str, list[dict[str, Any]]] = {}
    for key in ("equations", "headings", "blocks", "figures", "tables"):
        value = data.get(key, [])
        if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
            raise BundleError(f"{where}: {key!r} must be a list of objects")
        lists[key] = value
    for h in lists["headings"]:
        if not isinstance(h.get("level"), int) or not isinstance(h.get("text"), str):
            raise BundleError(f"{where}: a heading needs an integer level and a text")
    return SystemSection(
        arm=data["arm"],
        section=data["section"],
        status=data["status"],
        run=run,
        seconds=data.get("seconds"),
        versions=dict(data.get("versions", {})),
        settings=dict(data.get("settings", {})),
        html_path=data.get("html_path"),
        **lists,
    )


def load_systems(bundle: Bundle) -> dict[str, dict[str, list[SystemSection]]]:
    """arm -> section id -> its outputs (one per repeat), read from `systems/`."""
    out: dict[str, dict[str, list[SystemSection]]] = defaultdict(lambda: defaultdict(list))
    root = bundle.systems_dir
    if not root.is_dir():
        return {}
    for arm_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in sorted(arm_dir.glob("*.json")):
            match = _FILE.match(path.name)
            if match is None:
                continue
            where = f"systems/{arm_dir.name}/{path.name}"
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise BundleError(f"{where}: cannot be read: {exc}") from exc
            run = int(match.group("run") or 1)
            parsed = parse_system_section(data, where=where, run=run)
            expected = match.group("section").replace(SECTION_SEP, "/", 1)
            if parsed.section != expected or parsed.arm != arm_dir.name:
                raise BundleError(f"{where}: arm or section does not match the file's place")
            out[arm_dir.name][parsed.section].append(parsed)
    return {arm: dict(sections) for arm, sections in out.items()}
