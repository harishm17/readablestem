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
FRESH4_FILES = (
    "PREREGISTRATION-FRESH4.md",
    "manifest-fresh4.json",
    "candidates-fresh4.json",
    "order-realworld-fresh4-2026-10-26.json",
    "order-realworld-fresh3-2026-10-22.json",
    "pool.json",
    "../../scripts/rw_notation_neutral.py",
    "../../src/readablestem/models/prompts/equation_line.md",  # the arm under test
)
FRESH3_FILES = (
    "PREREGISTRATION-FRESH3.md",
    "manifest-fresh3.json",
    "candidates-fresh3.json",
    "order-realworld-fresh3-2026-10-22.json",
    "order-realworld-fresh2-2026-10-21.json",
    "order-realworld-fresh-2026-10-21.json",
    "pool.json",
    "../../scripts/rw_notation_neutral.py",  # the notation-neutral reading, defined by it
)
FRESH2_FILES = (
    "PREREGISTRATION-FRESH2.md",
    "manifest-fresh2.json",
    "candidates-fresh2.json",
    "order-realworld-fresh2-2026-10-21.json",
    "order-realworld-fresh-2026-10-21.json",
    "pool.json",
)
FRESH_FILES = (
    "PREREGISTRATION-FRESH.md",
    "manifest-fresh.json",
    "candidates-fresh.json",
    "order-realworld-fresh-2026-10-21.json",
    "pool.json",
)
FILES = (
    "PREREGISTRATION.md",
    "PREREGISTRATION-ADDENDUM-1.md",
    "PREREGISTRATION-ADDENDUM-2.md",
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
    parser.add_argument(
        "--set", choices=["heldout", "fresh", "fresh2", "fresh3", "fresh4"], default="heldout"
    )
    args = parser.parse_args()
    targets = {
        "heldout": ("REGISTRATION.sha256", FILES),
        "fresh": ("REGISTRATION-fresh.sha256", FRESH_FILES),
        "fresh2": ("REGISTRATION-fresh2.sha256", FRESH2_FILES),
        "fresh3": ("REGISTRATION-fresh3.sha256", FRESH3_FILES),
        "fresh4": ("REGISTRATION-fresh4.sha256", FRESH4_FILES),
    }
    name, files = targets[args.set]
    target = DATA / name
    lines = [f"{digest(name)}  {name}" for name in files]
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
