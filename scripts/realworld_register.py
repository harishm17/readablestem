"""Real-world set: hash the registration and what it names.

    uv run python scripts/realworld_register.py [--check]

Writes `data/realworld/REGISTRATION.sha256`: the SHA-256 of the registration text, of the two
manifests (papers, chosen pages, hashes of every gold file and cut PDF), of the candidate pool, of
the seeded order and of the candidates' outcomes. `realworld_run.py` refuses a held-out run unless
every file listed here still has its recorded hash. `--check` only verifies.

Write the registration, run this, commit, and only then run any system on the held-out part.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "realworld"
FILES = (
    "PREREGISTRATION.md",
    "PREREGISTRATION-ADDENDUM-1.md",
    "manifest-heldout.json",
    "manifest-dev.json",
    "pool.json",
    "order-realworld-2026-10-21.json",
    "candidates.json",
)


def digest(name: str) -> str:
    return hashlib.sha256((DATA / name).read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = DATA / "REGISTRATION.sha256"
    lines = [f"{digest(name)}  {name}" for name in FILES]
    if args.check:
        recorded = target.read_text().splitlines()
        if recorded != lines:
            print("registration does not match the files", file=sys.stderr)
            return 1
        print("OK")
        return 0
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
