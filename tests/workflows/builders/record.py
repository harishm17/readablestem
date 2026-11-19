"""Build the fixtures from the command line.

    uv run python tests/workflows/builders/record.py list
    uv run python tests/workflows/builders/record.py hashes [--slow]     print name, hash, size
    uv run python tests/workflows/builders/record.py record              rewrite recorded.json
    uv run python tests/workflows/builders/record.py write NAME DIR      save one file into DIR

`record` builds every byte-stable file, the slow ones too, and writes their hashes. Run it
only when a builder changed on purpose or PyMuPDF was upgraded, and read the diff.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # tests/: the fixtures package

import builders


def hashes(*, slow: bool) -> dict[str, dict]:
    out = {}
    for name, item in builders.load().items():
        if item.unstable or (item.slow and not slow):
            continue
        data = item.make()
        out[name] = {"sha256": builders.sha256(data), "bytes": len(data)}
    return out


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "list"
    if command == "list":
        for name, item in builders.load().items():
            note = " (not byte-stable)" if item.unstable else " (slow)" if item.slow else ""
            print(f"{name}{note}: {item.shows}. {item.origin}")
    elif command == "hashes":
        print(json.dumps(hashes(slow="--slow" in argv), indent=1))
    elif command == "record":
        builders.RECORD.write_text(json.dumps(hashes(slow=True), indent=1) + "\n", encoding="utf-8")
        print(f"wrote {builders.RECORD}")
    elif command == "write":
        target = Path(argv[2]) / argv[1]
        target.write_bytes(builders.build(argv[1]))
        print(f"wrote {target}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
