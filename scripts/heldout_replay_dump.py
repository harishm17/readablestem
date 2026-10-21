"""Dump ReadableSTEM's recorded held-out run for `heldout_secondary_scores.py`.

Run it with the interpreter of a checkout of the tag `proof-heldout-2026-10` (the code that
recorded the run; a recording can only be replayed through that code), from any directory:

    git worktree add /tmp/heldout-tag proof-heldout-2026-10
    cd /tmp/heldout-tag && uv sync --all-groups
    uv run python /path/to/scripts/heldout_replay_dump.py /tmp/heldout-tag DUMP_DIR [BUNDLE]

It replays the committed bundle (`make proof`'s replay: no key, no network, no model call,
about ten minutes; the keys are removed from the environment first) and writes
`DUMP_DIR/layout.json` (the layout elements of each section's PDF) and `DUMP_DIR/html.json`
(the output HTML of every run and section). It is a reporting aid for the recorded run;
the held-out set may not be used to develop anything (docs/eval.md).
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path


def main() -> int:
    tag, dump = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    # Optionally another recorded run than the set's own (`--bundle`); a replay of it goes on
    # past a stale axe answer (`--allow-misses --allow-drift`).
    bundle = sys.argv[3:4]
    sys.path.insert(0, str(tag / "src"))
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(key, None)
    package = "readablestem" if (tag / "src" / "readablestem").is_dir() else "a11y_stem"
    dump.mkdir(parents=True, exist_ok=True)

    layout = importlib.import_module(f"{package}.pipeline.layout")
    pdfs = tag / "proof" / "bundles" / "heldout-2026-10" / "media" / "pdfs"
    elements: dict[str, list[dict]] = {}
    for pdf in sorted(pdfs.glob("*.pdf")):
        section = pdf.stem.replace("__", "/", 1)
        result = layout.analyze(pdf.read_bytes())
        found = result.elements if hasattr(result, "elements") else result
        elements[section] = [
            {
                "id": e.id,
                "kind": e.kind.value,
                "page": e.page,
                "order": e.order,
                "text": e.text,
                "display": e.display,
                "parent": e.parent_id,
                "bbox": list(e.bbox),
            }
            for e in found
        ]
    (dump / "layout.json").write_text(json.dumps(elements))

    runner = importlib.import_module(f"{package}.proof.runner")
    original = runner.replay_arm

    def wrapped(*args, **kwargs):
        arm = original(*args, **kwargs)
        (dump / "html.json").write_text(
            json.dumps({f"{r}|{s}": h for (r, s), h in arm.html.items()})
        )
        return arm

    runner.replay_arm = wrapped
    main_module = importlib.import_module(f"{package}.cli.main")
    sys.argv = [
        "proof",
        "proof",
        "--out",
        str(dump / "PROOF-replay.md"),
        "--work",
        str(dump / "work"),
        *(["--bundle", bundle[0], "--allow-misses", "--allow-drift"] if bundle else []),
    ]
    main_module.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
