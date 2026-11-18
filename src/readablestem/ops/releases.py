"""The release record: which pipeline versions were released, in order (WS10.15).

`PIPELINE_VERSION` goes up for any change, so a stored job cannot say "a release has come
since this was made". `releases.toml`, beside this module and shipped inside the package, lists
the releases, oldest first: the release (`<pipeline version>+<first 12 hex digits of the
production profile's hash>`, the way the quality ledger and the outcome events spell it), the
pipeline version, the profile and the date.

It is written by `readablestem eval ledger --for-release --record-release`, when the ledger
has no release failing, and never by hand: a row says that the release's figures were held
against their bars on that day. A release already in the record is not added again.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

PATH = Path(__file__).with_name("releases.toml")


@dataclass(frozen=True)
class Release:
    release: str
    pipeline_version: str
    profile: str
    date: str


def read(path: Path | None = None) -> list[Release]:
    """The recorded releases, oldest first; none when there is no record yet."""
    path = path or PATH
    if not path.is_file():
        return []
    rows = tomllib.loads(path.read_text(encoding="utf-8")).get("release", [])
    return [Release(**row) for row in rows]


def record(release: Release, path: Path | None = None) -> bool:
    """Add `release` to the record when it is not there; whether it was added."""
    path = path or PATH
    if any(known.release == release.release for known in read(path)):
        return False
    row = (
        "[[release]]\n"
        f'release = "{release.release}"\n'
        f'pipeline_version = "{release.pipeline_version}"\n'
        f'profile = "{release.profile}"\n'
        f'date = "{release.date}"\n'
    )
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(before + ("\n" if before else "") + row, encoding="utf-8")
    return True
