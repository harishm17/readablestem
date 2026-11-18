"""The production profile: what production runs, as a file in the repository (WS0.9, F-224).

The code defaults in `config.py` are one configuration and production is another; until this
file the difference lived only as values in the hosting dashboard, so every eval and test
measured a product nobody used. `production.profile.toml`, beside this module and shipped
inside the package, lists every behaviour setting with its production value and where that
value came from; every other setting of `config.py` is named in its `operations` list (secrets,
addresses, limits, timeouts, accounts), never valued. Its `[image_facts]` table says what the
production image carries; the image build fills it (`readablestem ops image-facts --write`,
Dockerfile), and in the repository every fact is "unknown".

Who reads it:
- a production start (`Settings.check_production`) compares the running settings with it and
  names each setting that differs, never a value. While the file says `confirmed = false` (its
  values were taken from the repository's own prose, not read from the dashboard) a difference
  is a warning and the service starts: a wrong guess here must not take production down on the
  next deploy. Once confirmed, a difference refuses the start;
- `/admin/health` returns the profile's hash in its report, and /admin its name, hash and the
  names of the settings that differ (the public `/healthz/ops` says the status word only);
- `readablestem ops profile-check` prints the same comparison;
- the eval reports stamp it (`stamp`): a run whose settings are not the profile's says
  "development settings" (`readablestem eval ... --profile production` runs under it).

The hash is a sha256 over the sorted behaviour settings and image facts, so reordering the file
or editing a comment does not change it, and a changed value or image fact does.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from readablestem.config import Settings

PROFILE_PATH = Path(__file__).with_name("production.profile.toml")
# A setting whose value is None is written as this (TOML has no null).
UNSET = "unset"
# An image fact nobody measured. Every reader treats it as "not present".
UNKNOWN = "unknown"
ABSENT = "none"
IMAGE_FACTS = ("tex", "install_tex_witness", "latexml", "pandoc", "libreoffice", "lxml")
# A switch that needs a program counts as on only when the image facts carry that program
# (decision A16, points 8 and 20). The equation line check joins this when it lands.
NEEDS_PROGRAM: dict[str, str] = {
    "feature_latex_source": "latexml",
    "office_input_enabled": "libreoffice",
}
DEVELOPMENT = "development settings"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Profile:
    name: str
    confirmed: bool
    settings: dict[str, Any]  # config.py field name -> production value
    sources: dict[str, str]  # field name -> where the value came from
    operations: frozenset[str]  # field names that are named only
    image_facts: dict[str, str] = field(default_factory=dict)

    @property
    def sha256(self) -> str:
        body = {"settings": self.settings, "image_facts": self.image_facts}
        text = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(text.encode()).hexdigest()

    @property
    def short_hash(self) -> str:
        """The first 12 characters: with `PIPELINE_VERSION`, the name of a release."""
        return self.sha256[:12]

    def has(self, fact: str) -> bool:
        """The image carries this program. Unknown counts as not present."""
        value = str(self.image_facts.get(fact, UNKNOWN)).strip().lower()
        return value not in {UNKNOWN, ABSENT, ""}

    def differences(self, settings: Settings) -> list[str]:
        """The environment names of the behaviour settings whose value differs from the file."""
        return [
            name.upper()
            for name, wanted in sorted(self.settings.items())
            if not _same(_plain(getattr(settings, name)), wanted)
        ]

    def counted_off(self, settings: Settings) -> list[str]:
        """Switches that are on in `settings` but need a program these image facts lack."""
        return [
            name.upper()
            for name, program in sorted(NEEDS_PROGRAM.items())
            if getattr(settings, name) and not self.has(program)
        ]

    def apply(self, settings: Settings) -> Settings:
        """`settings` with every behaviour setting set to the profile's value; the rest
        (secrets, addresses, limits) kept."""
        from readablestem.config import PROVIDER_LIMIT_DEFAULTS, Settings

        # A provider default cap is left out, so the profile's provider brings its own.
        unset = {n for n in PROVIDER_LIMIT_DEFAULTS["openai"] if n not in settings.model_fields_set}
        values = settings.model_dump(exclude=unset)
        values |= {name: (None if v == UNSET else v) for name, v in self.settings.items()}
        return Settings(**values)


def _plain(value: Any) -> Any:
    """A setting's value in the file's terms: None as "unset", a path as text."""
    if value is None:
        return UNSET
    if isinstance(value, Path):
        return str(value)
    return value


def _same(actual: Any, wanted: Any) -> bool:
    numbers = (int, float)
    if (
        isinstance(actual, numbers)
        and isinstance(wanted, numbers)
        and not isinstance(actual, bool)
        and not isinstance(wanted, bool)
    ):
        return float(actual) == float(wanted)
    return actual == wanted


def parse(text: str) -> Profile:
    data = tomllib.loads(text)
    rows = data.get("settings", {})
    return Profile(
        name=str(data["name"]),
        confirmed=data["confirmed"] is True,
        settings={name.lower(): row["value"] for name, row in rows.items()},
        sources={name.lower(): str(row["source"]) for name, row in rows.items()},
        operations=frozenset(name.lower() for name in data.get("operations", [])),
        image_facts={k: str(v) for k, v in data.get("image_facts", {}).items()},
    )


def load(path: Path | None = None) -> Profile:
    """The profile at `path`; the shipped one when None (read once per process)."""
    return _shipped() if path is None else parse(path.read_text(encoding="utf-8"))


@cache
def _shipped() -> Profile:
    return parse(PROFILE_PATH.read_text(encoding="utf-8"))


def check_start(settings: Settings, profile: Profile | None = None) -> list[str]:
    """Compare a production start's settings with the profile (called by
    `Settings.check_production`). Logs each differing setting by name, never its value, and
    returns the names. Refuses the start only when the profile is confirmed."""
    from readablestem.config import ConfigError
    from readablestem.observability.logging import log_fields

    profile = profile or load()
    differing = profile.differences(settings)
    for name in differing:
        log_fields(
            logger,
            logging.ERROR if profile.confirmed else logging.WARNING,
            "profile_mismatch",
            setting=name,
            profile=profile.name,
            confirmed=profile.confirmed,
        )
    if differing and profile.confirmed:
        raise ConfigError(
            "production start refused; settings differ from the production profile: "
            + ", ".join(differing)
        )
    return differing


def stamp(settings: Settings, profile: Profile | None = None) -> dict[str, Any]:
    """What an eval report records about its settings: the profile's name and hash when the
    run's behaviour settings are the profile's, else "development settings"; the names that
    differ; the switches counted off because the image facts lack their program."""
    profile = profile or load()
    differing = profile.differences(settings)
    return {
        "name": DEVELOPMENT if differing else profile.name,
        "sha256": profile.sha256,
        "confirmed": profile.confirmed,
        "differs": differing,
        "counted_off": profile.counted_off(settings),
    }


def stamp_line(stamp_: dict[str, Any] | None) -> str:
    """One line for a report's header."""
    if not stamp_:
        return "Settings: development settings (no profile recorded)."
    short = stamp_["sha256"][:12]
    if stamp_["name"] == DEVELOPMENT:
        line = (
            f"Settings: development settings ({len(stamp_['differs'])} differ from the "
            f"production profile {short})."
        )
    else:
        unconfirmed = "" if stamp_["confirmed"] else ", not yet confirmed against the dashboard"
        line = f"Settings: {stamp_['name']} profile {short}{unconfirmed}."
    if stamp_.get("counted_off"):
        line += " Counted off, the image lacks their program: " + ", ".join(stamp_["counted_off"])
        line += "."
    return line


# ------------------------------------------------------------------ image facts (the build)


def _first_line(*command: str) -> str:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    # LaTeXML prints its version on stderr, pandoc on stdout.
    lines = ((done.stdout or "") + (done.stderr or "")).strip().splitlines()
    return lines[0].strip() if done.returncode == 0 and lines else UNKNOWN


def measure_image_facts() -> dict[str, str]:
    """What this machine carries, for the image build. A program that is missing is "none";
    a fact this function cannot measure stays "unknown"."""
    tex = UNKNOWN
    if shutil.which("dpkg-query"):
        try:
            done = subprocess.run(
                ["dpkg-query", "-W", "-f", "${Package}=${Version}\\n", "texlive-*"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            installed = [line for line in done.stdout.splitlines() if line.split("=")[-1]]
            tex = " ".join(sorted(installed)) or ABSENT
        except (OSError, subprocess.SubprocessError):
            tex = UNKNOWN
    try:
        lxml = importlib.metadata.version("lxml")
    except importlib.metadata.PackageNotFoundError:
        lxml = ABSENT
    return {
        "tex": tex,
        # Not a build argument on main: nothing can measure it yet.
        "install_tex_witness": UNKNOWN,
        "latexml": _first_line("latexml", "--VERSION") if shutil.which("latexml") else ABSENT,
        "pandoc": _first_line("pandoc", "--version") if shutil.which("pandoc") else ABSENT,
        "libreoffice": "present" if shutil.which("soffice") else ABSENT,
        "lxml": lxml,
    }


_FACTS_TABLE = re.compile(r"^\[image_facts\][^\n]*\n.*\Z", re.MULTILINE | re.DOTALL)


def write_image_facts(path: Path, facts: dict[str, str]) -> None:
    """Replace the file's `[image_facts]` table (the last table) with `facts`."""
    text = path.read_text(encoding="utf-8")
    if not _FACTS_TABLE.search(text):
        raise ValueError(f"{path} has no [image_facts] table")
    body = "".join(f"{key} = {json.dumps(str(facts[key]))}\n" for key in IMAGE_FACTS)
    path.write_text(_FACTS_TABLE.sub(lambda _: "[image_facts]\n" + body, text), encoding="utf-8")
    _shipped.cache_clear()
